"""定时调度.

提供一次性和重复性任务。任务 = 回调 + 时间规则.
用于:每日定时重启游戏、定时启停某个动作、倒计时关游戏.
"""

from __future__ import annotations

import enum
import logging
import threading
from dataclasses import dataclass, field
from typing import Callable

from ..utils.sanitize import sanitize


class TaskFreq(str, enum.Enum):
    ONCE = "once"
    INTERVAL = "interval"
    DAILY = "daily"
    WEEKLY = "weekly"


@dataclass
class ScheduledTask:
    """一个调度任务."""

    name: str = ""
    freq: TaskFreq = TaskFreq.ONCE
    interval_sec: int = 60  # INTERVAL 模式
    hour: int = 9  # DAILY/WEEKLY 模式
    minute: int = 0
    weekday: int = -1  # 0=周一..6=周日;-1=不限(WEEKLY)
    action: str = ""  # 'start_all' / 'stop_all' / 'restart_game' / 'shutdown_app'
    target_hwnd: int = 0
    enabled: bool = True
    last_run: float = 0.0
    next_run: float = 0.0
    run_count: int = 0
    note: str = ""


class Scheduler:
    """简易调度器."""

    def __init__(self, logger: logging.Logger | None = None) -> None:
        self._log = logger or logging.getLogger("autofarmstation.scheduler")
        self._lock = threading.RLock()
        self._tasks: dict[str, ScheduledTask] = {}
        self._handlers: dict[str, Callable[[ScheduledTask], None]] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # --- API ---
    def register_handler(self, action: str, cb: Callable[[ScheduledTask], None]) -> None:
        self._handlers[action] = cb

    def add(self, task: ScheduledTask) -> None:
        with self._lock:
            self._tasks[task.name or f"task-{len(self._tasks)}"] = task

    def remove(self, name: str) -> bool:
        with self._lock:
            return self._tasks.pop(name, None) is not None

    def list(self) -> list[ScheduledTask]:
        with self._lock:
            return list(self._tasks.values())

    def get(self, name: str) -> ScheduledTask | None:
        with self._lock:
            return self._tasks.get(name)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="Scheduler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)
        self._thread = None

    # --- 内部 ---
    def _loop(self) -> None:
        import time as _t
        while not self._stop.is_set():
            now = _t.time()
            local = _t.localtime()
            with self._lock:
                tasks = list(self._tasks.values())
            for t in tasks:
                if not t.enabled:
                    continue
                if self._should_run(t, now, local):
                    self._run_task(t)
            self._stop.wait(timeout=1.0)

    def _should_run(self, t: ScheduledTask, now: float, local) -> bool:
        # 简化:每秒检查一次是否到达预定时间
        if t.freq == TaskFreq.ONCE:
            # 用 hour:minute 决定时刻
            target_sec = local.tm_hour * 3600 + local.tm_min * 60 + local.tm_sec
            now_sec = target_sec
            return local.tm_hour == t.hour and local.tm_min == t.minute and t.last_run < now - 60
        if t.freq == TaskFreq.INTERVAL:
            return now - t.last_run >= max(1, t.interval_sec)
        if t.freq == TaskFreq.DAILY:
            return (local.tm_hour == t.hour and local.tm_min == t.minute
                    and t.last_run < now - 60)
        if t.freq == TaskFreq.WEEKLY:
            if t.weekday >= 0 and local.tm_wday != t.weekday:
                return False
            return (local.tm_hour == t.hour and local.tm_min == t.minute
                    and t.last_run < now - 60)
        return False

    def _run_task(self, t: ScheduledTask) -> None:
        import time as _t
        t.last_run = _t.time()
        t.run_count += 1
        cb = self._handlers.get(t.action)
        if not cb:
            self._log.warning("未注册 action handler: %s", t.action)
            return
        try:
            cb(t)
            self._log.info("调度任务执行: %s (%s)", sanitize(t.name), t.action)
        except Exception as e:  # noqa: BLE001
            self._log.exception("任务执行失败: %s", e)
        if t.freq == TaskFreq.ONCE:
            t.enabled = False