"""定时调度.

任务 = 回调(action) + 时间规则。支持四种频率:

- ``once``     一次性,指定日期时间(``run_at``),跑完自动禁用
- ``interval`` 每隔 N 秒/分钟/小时
- ``daily``    每天 HH:MM
- ``weekly``   每周几的 HH:MM

任务列表持久化到 ``%APPDATA%\\AutoFarmStation\\schedules.json``,
关掉软件再打开依然在。
"""

from __future__ import annotations

import datetime as _dt
import enum
import json
import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..utils.paths import atomic_write, schedule_path
from ..utils.sanitize import sanitize

WEEKDAY_NAMES = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


class TaskFreq(str, enum.Enum):
    ONCE = "once"
    INTERVAL = "interval"
    DAILY = "daily"
    WEEKLY = "weekly"


# 动作 → 中文说明(UI 与日志共用)
ACTION_LABELS: dict[str, str] = {
    "start_all": "启动全部(所有已勾选窗口)",
    "stop_all": "停止全部",
    "launch_games": "启动游戏(自动唤醒,Steam 游戏先开 Steam)",
    "kill_games": "结束游戏进程",
    "start_clicker": "开始连点(默认全部勾选窗口)",
    "stop_clicker": "停止连点",
    "play_macro": "启动键盘宏(指定窗口)",
    "volume_low": "音量降到 20%",
    "volume_mute": "静音整体音量",
    "volume_restore": "音量恢复到 100%",
    "shutdown_app": "退出本软件",
}


def action_label(action: str) -> str:
    return ACTION_LABELS.get(action, action or "(未设置)")


@dataclass
class ScheduledTask:
    """一个调度任务."""

    name: str = ""
    freq: TaskFreq = TaskFreq.ONCE
    run_at: str = ""  # ONCE 专用:"2026-09-16T14:30"
    interval_sec: int = 3600  # INTERVAL 模式
    hour: int = 9  # DAILY/WEEKLY 模式
    minute: int = 0
    weekday: int = -1  # 0=周一..6=周日;-1=不限(WEEKLY)
    action: str = "start_all"
    target_hwnd: int = 0  # 0 = 全部
    macro_name: str = ""  # play_macro 用
    enabled: bool = True
    last_run: float = 0.0
    next_run: float = 0.0
    run_count: int = 0
    note: str = ""

    # --- 序列化 ---
    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "freq": self.freq.value if isinstance(self.freq, TaskFreq) else str(self.freq),
            "run_at": self.run_at,
            "interval_sec": int(self.interval_sec),
            "hour": int(self.hour),
            "minute": int(self.minute),
            "weekday": int(self.weekday),
            "action": self.action,
            "target_hwnd": int(self.target_hwnd),
            "macro_name": self.macro_name,
            "enabled": bool(self.enabled),
            "last_run": float(self.last_run),
            "next_run": float(self.next_run),
            "run_count": int(self.run_count),
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ScheduledTask":
        d = d or {}
        try:
            freq = TaskFreq(str(d.get("freq") or "once"))
        except ValueError:
            freq = TaskFreq.ONCE
        return cls(
            name=str(d.get("name") or ""),
            freq=freq,
            run_at=str(d.get("run_at") or ""),
            interval_sec=int(d.get("interval_sec") or 3600),
            hour=int(d.get("hour") or 0),
            minute=int(d.get("minute") or 0),
            weekday=int(d.get("weekday") if d.get("weekday") is not None else -1),
            action=str(d.get("action") or ""),
            target_hwnd=int(d.get("target_hwnd") or 0),
            macro_name=str(d.get("macro_name") or ""),
            enabled=bool(d.get("enabled", True)),
            last_run=float(d.get("last_run") or 0.0),
            next_run=float(d.get("next_run") or 0.0),
            run_count=int(d.get("run_count") or 0),
            note=str(d.get("note") or ""),
        )

    # --- 展示 ---
    def schedule_text(self) -> str:
        if self.freq is TaskFreq.ONCE:
            raw = self.run_at or ""
            if "T" in raw:
                d, t = raw.split("T", 1)
                return f"一次性 {d} {t[:5]}"
            return f"一次性 {raw}"
        if self.freq is TaskFreq.INTERVAL:
            return f"每 {format_interval(self.interval_sec)}"
        wd = WEEKDAY_NAMES[self.weekday] if 0 <= self.weekday <= 6 else ""
        if self.freq is TaskFreq.WEEKLY:
            return f"{wd or '每周'} {self.hour:02d}:{self.minute:02d}"
        return f"每天 {self.hour:02d}:{self.minute:02d}"


def format_interval(sec: int) -> str:
    sec = max(1, int(sec))
    if sec % 3600 == 0:
        return f"{sec // 3600} 小时"
    if sec % 60 == 0:
        return f"{sec // 60} 分钟"
    return f"{sec} 秒"


class Scheduler:
    """简易调度器(带持久化)."""

    def __init__(
        self,
        logger: logging.Logger | None = None,
        path: Path | None = None,
        *,
        autosave: bool = True,
    ) -> None:
        self._log = logger or logging.getLogger("autofarmstation.scheduler")
        self._lock = threading.RLock()
        self._tasks: dict[str, ScheduledTask] = {}
        self._order: list[str] = []
        self._handlers: dict[str, Callable[[ScheduledTask], None]] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._path = path or schedule_path()
        self._autosave = autosave
        self.load()

    # --- 持久化 ---
    def load(self) -> None:
        with self._lock:
            self._tasks.clear()
            self._order.clear()
            if not self._path.exists():
                return
            try:
                raw = json.loads(self._path.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                self._log.warning("定时任务文件损坏,已忽略:%s", self._path.name)
                return
            items = raw.get("tasks") if isinstance(raw, dict) else raw
            if not isinstance(items, list):
                return
            for item in items:
                if not isinstance(item, dict):
                    continue
                task = ScheduledTask.from_dict(item)
                key = task.name or f"task-{len(self._tasks)}"
                task.name = key
                self._tasks[key] = task
                self._order.append(key)

    def save(self) -> None:
        with self._lock:
            payload = {
                "version": 1,
                "tasks": [self._tasks[k].to_dict() for k in self._order if k in self._tasks],
            }
            try:
                atomic_write(self._path, json.dumps(payload, indent=2, ensure_ascii=False))
            except OSError as e:
                self._log.warning("保存定时任务失败:%s", e)

    # --- API ---
    def register_handler(self, action: str, cb: Callable[[ScheduledTask], None]) -> None:
        self._handlers[action] = cb

    def add(self, task: ScheduledTask, *, persist: bool = True) -> str:
        """新增任务,返回最终的任务名(重名会自动加后缀)."""
        with self._lock:
            name = task.name or f"任务 {len(self._tasks) + 1}"
            base = name
            n = 2
            while name in self._tasks:
                name = f"{base} ({n})"
                n += 1
            task.name = name
            task.next_run = self.compute_next(task)
            self._tasks[name] = task
            self._order.append(name)
        if persist and self._autosave:
            self.save()
        return name

    def update(self, name: str, task: ScheduledTask) -> bool:
        with self._lock:
            if name not in self._tasks:
                return False
            old = self._tasks[name]
            task.last_run = old.last_run
            task.run_count = old.run_count
            task.name = name
            task.next_run = self.compute_next(task)
            self._tasks[name] = task
        if self._autosave:
            self.save()
        return True

    def remove(self, name: str) -> bool:
        with self._lock:
            ok = self._tasks.pop(name, None) is not None
            if ok and name in self._order:
                self._order.remove(name)
        if ok and self._autosave:
            self.save()
        return ok

    def set_enabled(self, name: str, enabled: bool) -> bool:
        with self._lock:
            task = self._tasks.get(name)
            if task is None:
                return False
            task.enabled = bool(enabled)
            if enabled:
                task.next_run = self.compute_next(task)
                if task.freq is TaskFreq.ONCE and task.next_run <= time.time():
                    task.next_run = time.time() + 1
        if self._autosave:
            self.save()
        return True

    def list(self) -> list[ScheduledTask]:
        with self._lock:
            return [self._tasks[k] for k in self._order if k in self._tasks]

    def get(self, name: str) -> ScheduledTask | None:
        with self._lock:
            return self._tasks.get(name)

    def clear(self) -> None:
        with self._lock:
            self._tasks.clear()
            self._order.clear()
        if self._autosave:
            self.save()

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

    def run_now(self, name: str) -> bool:
        """立刻执行一次(不影响下次排期,ONCE 也会保留)."""
        task = self.get(name)
        if task is None:
            return False
        self._invoke(task)
        return True

    # --- 下次触发时刻 ---
    def compute_next(self, task: ScheduledTask, now: float | None = None) -> float:
        """计算下次触发的时间戳(0 = 不再触发)."""
        now = time.time() if now is None else float(now)
        if task.freq is TaskFreq.INTERVAL:
            return now + max(1, int(task.interval_sec))
        if task.freq is TaskFreq.ONCE:
            ts = _parse_run_at(task.run_at)
            if ts is None:
                # 退化为「最近一次 hour:minute」
                ts = _next_daily(now, 0, task.hour, task.minute)
            return ts if ts > now else 0.0
        if task.freq is TaskFreq.DAILY:
            return _next_daily(now, 0, task.hour, task.minute)
        if task.freq is TaskFreq.WEEKLY:
            base = _next_daily(now, 0, task.hour, task.minute)
            if not (0 <= task.weekday <= 6):
                return base
            cur = _dt.datetime.fromtimestamp(base)
            delta = (task.weekday - cur.weekday()) % 7
            if delta == 0 and cur.weekday() != task.weekday:
                delta = 7
            return base + delta * 86400
        return 0.0

    def next_run_text(self, task: ScheduledTask) -> str:
        if not task.enabled:
            return "已暂停"
        ts = task.next_run or self.compute_next(task)
        if not ts:
            return "不会再触发"
        left = ts - time.time()
        if left <= 0:
            return "即将触发"
        if left < 90:
            return f"{int(left)} 秒后"
        if left < 5400:
            return f"{int(left // 60)} 分钟后"
        if left < 86400 * 2:
            return f"{left / 3600:.1f} 小时后"
        return f"{left / 86400:.1f} 天后"

    # --- 内部 ---
    def _loop(self) -> None:
        while not self._stop.is_set():
            now = time.time()
            with self._lock:
                tasks = [self._tasks[k] for k in self._order if k in self._tasks]
            dirty = False
            for t in tasks:
                if not t.enabled:
                    continue
                if not t.next_run:
                    t.next_run = self.compute_next(t, now)
                    dirty = True
                    continue
                if now >= t.next_run:
                    self._invoke(t)
                    if t.freq is TaskFreq.ONCE:
                        t.enabled = False
                        t.next_run = 0.0
                    else:
                        t.next_run = self.compute_next(t, now)
                    dirty = True
            if dirty and self._autosave:
                self.save()
            self._stop.wait(timeout=1.0)

    def _invoke(self, t: ScheduledTask) -> None:
        cb = self._handlers.get(t.action)
        t.last_run = time.time()
        t.run_count += 1
        if not cb:
            self._log.warning("未注册 action handler: %s", t.action)
            return
        try:
            cb(t)
            self._log.info("定时任务执行: %s → %s", sanitize(t.name), action_label(t.action))
        except Exception as e:  # noqa: BLE001
            self._log.exception("定时任务执行失败(%s): %s", sanitize(t.name), e)


# === 时间辅助 ===
def _parse_run_at(raw: str) -> float | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return _dt.datetime.strptime(raw, fmt).timestamp()
        except ValueError:
            continue
    return None


def _next_daily(now: float, day_offset: int, hour: int, minute: int) -> float:
    base = _dt.datetime.fromtimestamp(now) + _dt.timedelta(days=day_offset)
    target = base.replace(
        hour=max(0, min(23, int(hour))),
        minute=max(0, min(59, int(minute))),
        second=0,
        microsecond=0,
    )
    if target.timestamp() <= now:
        target += _dt.timedelta(days=1)
    return target.timestamp()
