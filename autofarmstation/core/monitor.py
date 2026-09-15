"""进程监控.

定时检测追踪窗口的存活状态;失效时:
- 通知回调
- 若启用自动重启:调用回调启动游戏(用户预配置的启动路径)

顺带每个周期采样一次各窗口的 **CPU / 内存占用**,写回 ProcessManager
供卡片显示;超过阈值时发一个 RESOURCE 事件(只提醒,不替用户处置)。
内存按「主进程 + 子进程」累加(游戏常把内存放在子进程里),
CPU 只取主进程 —— 子进程要各自预热才有准确读数,不值得那点开销。
"""

from __future__ import annotations

import enum
import logging
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Callable

from . import window_finder as wf
from .process_manager import ProcessManager, TrackedProcess


class MonitorStatus(str, enum.Enum):
    OK = "ok"
    MISSING = "missing"
    MINIMIZED = "minimized"
    RESTARTING = "restarting"
    ERROR = "error"
    RESOURCE = "resource"  # CPU / 内存占用超过阈值(只提醒,不处置)


@dataclass
class MonitorEvent:
    """一次监控事件."""

    hwnd: int
    pid: int
    status: MonitorStatus
    message: str = ""
    ts: float = 0.0


class ProcessMonitor:
    """进程存活监控 + 自动重启."""

    def __init__(
        self,
        pm: ProcessManager,
        *,
        interval_sec: float = 3.0,
        auto_restart: bool = False,
        restart_exe: str = "",
        restart_args: str = "",
        resource_alert: bool = True,
        cpu_alert_pct: float = 90.0,
        mem_alert_mb: float = 4096.0,
        logger: logging.Logger | None = None,
    ) -> None:
        self._pm = pm
        self._interval = max(0.5, interval_sec)
        self._auto_restart = auto_restart
        self._restart_exe = restart_exe
        self._restart_args = restart_args
        self._resource_alert = bool(resource_alert)
        self._cpu_alert_pct = float(cpu_alert_pct)
        self._mem_alert_mb = float(mem_alert_mb)
        self._alert_cooldown = 300.0  # 同一个窗口 5 分钟内只提醒一次
        self._proc_cache: dict[int, object] = {}
        self._alert_ts: dict[int, float] = {}
        self._log = logger or logging.getLogger("autofarmstation.monitor")
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._on_event: list[Callable[[MonitorEvent], None]] = []

    def on_event(self, cb: Callable[[MonitorEvent], None]) -> None:
        self._on_event.append(cb)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="ProcessMonitor", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)
        self._thread = None

    def set_auto_restart(self, on: bool, *, exe: str = "", args: str = "") -> None:
        self._auto_restart = on
        self._restart_exe = exe
        self._restart_args = args

    def set_resource_alert(self, on: bool, *, cpu_pct: float = 90.0, mem_mb: float = 4096.0) -> None:
        self._resource_alert = bool(on)
        self._cpu_alert_pct = float(cpu_pct)
        self._mem_alert_mb = float(mem_mb)

    # --- 资源占用 ---
    def _sample_resources(self) -> None:
        """采样每个追踪窗口的 CPU / 内存,写回 ProcessManager 供卡片显示."""
        try:
            import psutil  # type: ignore
        except ImportError:
            return
        alive_pids = {it.pid for it in self._pm.all() if it.alive and it.pid}
        for stale in [p for p in self._proc_cache if p not in alive_pids]:
            self._proc_cache.pop(stale, None)

        for item in self._pm.all():
            if not item.alive or not item.pid:
                continue
            proc = self._proc_cache.get(item.pid)
            if proc is None:
                try:
                    proc = psutil.Process(item.pid)
                except Exception:  # noqa: BLE001
                    continue
                self._proc_cache[item.pid] = proc
            try:
                cpu = float(proc.cpu_percent(interval=None))  # type: ignore[attr-defined]
                mem = float(proc.memory_info().rss)  # type: ignore[attr-defined]
                try:
                    for child in proc.children(recursive=True):  # type: ignore[attr-defined]
                        mem += float(child.memory_info().rss)
                except Exception:  # noqa: BLE001
                    pass
            except Exception:  # noqa: BLE001
                self._proc_cache.pop(item.pid, None)
                continue
            mem_mb = mem / (1024 * 1024)
            self._pm.set_resource(item.hwnd, cpu, mem_mb)
            self._check_resource_alert(item, cpu, mem_mb)

    def _check_resource_alert(self, item: TrackedProcess, cpu: float, mem_mb: float) -> None:
        """超阈值只提醒,不替用户做处置(停哪个窗口是人的决定)."""
        if not self._resource_alert:
            return
        over = []
        if self._cpu_alert_pct > 0 and cpu >= self._cpu_alert_pct:
            over.append(f"CPU {cpu:.0f}%")
        if self._mem_alert_mb > 0 and mem_mb >= self._mem_alert_mb:
            gb = mem_mb / 1024.0
            over.append(f"内存 {gb:.1f}GB" if gb >= 1 else f"内存 {mem_mb:.0f}MB")
        if not over:
            self._alert_ts.pop(item.hwnd, None)
            return
        now = time.time()
        if now - float(self._alert_ts.get(item.hwnd, 0.0)) < self._alert_cooldown:
            return
        self._alert_ts[item.hwnd] = now
        self._emit(MonitorEvent(
            hwnd=item.hwnd, pid=item.pid, status=MonitorStatus.RESOURCE,
            message=f"{item.display_name()} 占用偏高:" + "、".join(over),
            ts=now,
        ))

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._sample_resources()
                gone = self._pm.refresh()
                for hwnd in gone:
                    item = self._pm.get(hwnd)
                    if item is None:
                        continue
                    self._emit(MonitorEvent(
                        hwnd=hwnd, pid=item.pid,
                        status=MonitorStatus.MISSING,
                        message=f"窗口已失效:{item.title}",
                        ts=time.time(),
                    ))
                    if self._auto_restart and self._restart_exe:
                        self._emit(MonitorEvent(
                            hwnd=hwnd, pid=item.pid,
                            status=MonitorStatus.RESTARTING,
                            message=f"自动重启:{item.title}",
                            ts=time.time(),
                        ))
                        self._try_restart()
            except Exception as e:  # noqa: BLE001
                self._log.warning("监控循环出错: %s", e)
            self._stop.wait(timeout=self._interval)

    def _try_restart(self) -> None:
        if not self._restart_exe:
            return
        try:
            subprocess.Popen(
                [self._restart_exe] + (self._restart_args.split() if self._restart_args else []),
                close_fds=False,
            )
        except Exception as e:  # noqa: BLE001
            self._emit(MonitorEvent(
                hwnd=0, pid=0, status=MonitorStatus.ERROR,
                message=f"重启失败:{e}", ts=time.time(),
            ))

    def _emit(self, ev: MonitorEvent) -> None:
        for cb in list(self._on_event):
            try:
                cb(ev)
            except Exception as e:  # noqa: BLE001
                self._log.warning("on_event 回调出错: %s", e)