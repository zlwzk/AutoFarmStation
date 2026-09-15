"""进程监控.

定时检测追踪窗口的存活状态;失效时:
- 通知回调
- 若启用自动重启:调用回调启动游戏(用户预配置的启动路径)
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
        logger: logging.Logger | None = None,
    ) -> None:
        self._pm = pm
        self._interval = max(0.5, interval_sec)
        self._auto_restart = auto_restart
        self._restart_exe = restart_exe
        self._restart_args = restart_args
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

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
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