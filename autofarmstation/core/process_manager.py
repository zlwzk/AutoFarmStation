"""进程/窗口追踪管理.

- 跟踪用户选择的 hwnd / pid
- 自动检测进程是否还在
- 提供统一的 dict 持久化结构
- 与 config.tracked_processes 兼容
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field, asdict
from typing import Callable

from . import window_finder as wf


@dataclass
class TrackedProcess:
    """一个被追踪的窗口/进程."""

    hwnd: int
    pid: int
    name: str  # 进程名(尽力获取)
    title: str
    exe: str  # exe 路径(用于脱敏展示)
    added_at: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)
    alive: bool = True
    tags: list[str] = field(default_factory=list)
    alias: str = ""  # 用户给这个窗口起的别名(多开时靠它分辨)
    color: str = ""  # 卡片强调色(十六进制,空 = 不强调)
    # --- 运行时字段:由监控线程周期性刷新,不写进配置 ---
    cpu: float = 0.0
    mem_mb: float = 0.0

    _RUNTIME_FIELDS = ("cpu", "mem_mb")

    def display_name(self) -> str:
        """卡片标题:别名优先,其次窗口标题,最后兜底进程名/PID."""
        return (self.alias or self.title or self.name or f"PID {self.pid}").strip()

    def to_dict(self) -> dict:
        d = asdict(self)
        for key in self._RUNTIME_FIELDS:
            d.pop(key, None)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "TrackedProcess":
        return cls(
            hwnd=int(d.get("hwnd", 0)),
            pid=int(d.get("pid", 0)),
            name=str(d.get("name", "")),
            title=str(d.get("title", "")),
            exe=str(d.get("exe", "")),
            added_at=float(d.get("added_at", time.time())),
            last_seen=float(d.get("last_seen", time.time())),
            alive=bool(d.get("alive", True)),
            tags=list(d.get("tags", []) or []),
            alias=str(d.get("alias", "") or ""),
            color=str(d.get("color", "") or ""),
        )


class ProcessManager:
    """追踪窗口集合."""

    def __init__(self, logger: logging.Logger | None = None) -> None:
        self._log = logger or logging.getLogger("autofarmstation.processmgr")
        self._lock = threading.RLock()
        self._items: dict[int, TrackedProcess] = {}
        self._on_change: list[Callable[[], None]] = []

    # --- 列表 ---
    def all(self) -> list[TrackedProcess]:
        with self._lock:
            return list(self._items.values())

    def get(self, hwnd: int) -> TrackedProcess | None:
        with self._lock:
            return self._items.get(int(hwnd))

    def hwnds(self) -> list[int]:
        with self._lock:
            return list(self._items.keys())

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)

    # --- 增删 ---
    def add_by_hwnd(self, hwnd: int, *, tags: list[str] | None = None) -> TrackedProcess | None:
        info = wf.get_window_info(hwnd)
        if info is None:
            return None
        name, exe = self._lookup_process(info.pid)
        item = TrackedProcess(
            hwnd=hwnd,
            pid=info.pid,
            name=name,
            title=info.title,
            exe=exe,
            tags=list(tags or []),
        )
        with self._lock:
            self._items[hwnd] = item
        self._notify()
        return item

    def add_by_pid(self, pid: int, *, tags: list[str] | None = None) -> list[TrackedProcess]:
        from . import window_finder as wf
        grouped = wf.find_windows_for_pids([pid])
        out: list[TrackedProcess] = []
        name, exe = self._lookup_process(pid)
        for info in grouped.get(pid, []):
            item = TrackedProcess(
                hwnd=info.hwnd, pid=pid, name=name, title=info.title, exe=exe,
                tags=list(tags or []),
            )
            with self._lock:
                self._items[info.hwnd] = item
            out.append(item)
        if out:
            self._notify()
        return out

    def remove(self, hwnd: int) -> bool:
        with self._lock:
            if int(hwnd) in self._items:
                del self._items[int(hwnd)]
                self._notify()
                return True
        return False

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
        self._notify()

    # --- 心跳 / 同步 ---
    def refresh(self) -> list[int]:
        """根据当前窗口状态刷新 alive / title,返回本次失效的 hwnd 列表."""
        gone: list[int] = []
        changed_any = False
        with self._lock:
            items = list(self._items.values())
        for item in items:
            info = wf.get_window_info(item.hwnd)
            if info is None or not info.visible:
                if item.alive:
                    changed_any = True
                item.alive = False
                gone.append(item.hwnd)
            else:
                if (not item.alive) or info.title != item.title or info.pid != item.pid:
                    changed_any = True
                item.alive = True
                item.pid = info.pid
                item.title = info.title
                item.last_seen = time.time()
        if changed_any:
            self._notify()
        return gone

    # --- 资源占用(监控线程写入) ---
    def set_resource(self, hwnd: int, cpu: float, mem_mb: float) -> None:
        """更新某个窗口的 CPU / 内存占用.不需要触发 on_change(卡片自己会刷)."""
        with self._lock:
            item = self._items.get(int(hwnd))
        if item is not None:
            item.cpu = float(cpu)
            item.mem_mb = float(mem_mb)

    def set_alias(self, hwnd: int, alias: str, color: str | None = None) -> bool:
        """改别名 / 强调色(别名留空则回退成窗口标题)."""
        with self._lock:
            item = self._items.get(int(hwnd))
        if item is None:
            return False
        item.alias = str(alias or "").strip()
        if color is not None:
            item.color = str(color or "")
        self._notify()
        return True

    # --- 回调 ---
    def on_change(self, cb: Callable[[], None]) -> None:
        self._on_change.append(cb)

    def _notify(self) -> None:
        for cb in list(self._on_change):
            try:
                cb()
            except Exception as e:  # noqa: BLE001
                self._log.warning("on_change 回调出错: %s", e)

    # --- 内部 ---
    @staticmethod
    def _lookup_process(pid: int) -> tuple[str, str]:
        """用 psutil 拿进程名 + 路径(失败时返回空字符串)."""
        if not pid:
            return ("", "")
        try:
            import psutil  # type: ignore
            p = psutil.Process(pid)
            name = p.name()
            exe = p.exe() if hasattr(p, "exe") else ""
        except Exception:
            name, exe = "", ""
        return (name, exe)

    # --- 序列化 ---
    def export_list(self) -> list[dict]:
        with self._lock:
            return [it.to_dict() for it in self._items.values()]

    def import_list(self, lst: list[dict]) -> None:
        with self._lock:
            self._items.clear()
            for d in lst or []:
                try:
                    it = TrackedProcess.from_dict(d)
                    self._items[it.hwnd] = it
                except Exception as e:  # noqa: BLE001
                    self._log.warning("导入条目失败: %s", e)
        self._notify()