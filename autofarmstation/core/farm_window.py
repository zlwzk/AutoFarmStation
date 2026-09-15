"""v1.6.2 挂机时段守护:时段外不允许启动 / 自动停;回到时段可自动恢复.

不依赖 Qt;UI 在 main_window 里轮询它(每 30 秒一次 + 进入 / 退出页面时各一次)。
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass


@dataclass(frozen=True)
class _TimePair:
    start: datetime.time
    end: datetime.time


def _parse_hhmm(text: str, default: _TimePair) -> _TimePair:
    try:
        s = str(text).strip()
        parts = s.replace("：", ":").split(":")
        h1 = int(parts[0]) % 24
        m1 = int(parts[1]) % 60 if len(parts) > 1 else 0
        return _TimePair(datetime.time(h1, m1), default.end)
    except (ValueError, IndexError, AttributeError):
        return default


def _resolve_pair(start_raw: str, end_raw: str) -> _TimePair:
    """把 (start, end) 字符串解成 _TimePair;单边失败回退到全时段."""
    try:
        s = str(start_raw).strip()
        e = str(end_raw).strip()
        sh, sm = (int(x) for x in s.replace("：", ":").split(":")[:2])
        eh, em = (int(x) for x in e.replace("：", ":").split(":")[:2])
        return _TimePair(
            datetime.time(sh % 24, sm % 60),
            datetime.time(eh % 24, em % 60),
        )
    except (ValueError, IndexError, AttributeError):
        # 回退值 = 全时段(0:0 ~ 23:59:59.999999),等同于「时段不限制」
        return _TimePair(datetime.time(0, 0), datetime.time(23, 59, 59, 999999))


class FarmWindowGuard:
    """封装「挂机时段」的所有运行时判定."""

    def __init__(self, cfg) -> None:
        self._cfg = cfg

    @property
    def enabled(self) -> bool:
        try:
            return bool(self._cfg.get("farm_window.enabled", False))
        except Exception:
            return False

    def action(self) -> str:
        """时段外动作: 'pause' (回到时段自动恢复) / 'stop' (回到时段不自动恢复)."""
        try:
            v = str(self._cfg.get("farm_window.action", "pause"))
            return v if v in ("pause", "stop") else "pause"
        except Exception:
            return "pause"

    def in_window(self, now: datetime.datetime | None = None) -> bool:
        """现在是否在挂机时段内. 未启用或解析失败都视为「在时段内」,即不限制。"""
        if not self.enabled:
            return True
        pair = _resolve_pair(
            self._cfg.get("farm_window.start", "22:00"),
            self._cfg.get("farm_window.end", "08:00"),
        )
        if pair.start == pair.end:
            return True  # 起止相同 = 全天 = 不限制
        cur = (now or datetime.datetime.now()).time()
        if pair.start < pair.end:
            # 当天内: [start, end)
            return pair.start <= cur < pair.end
        # 跨天: [start, 24:00) ∪ [00:00, end)
        return cur >= pair.start or cur < pair.end

    def should_block_start(self) -> bool:
        """是否应该拒绝启动(启用且当前不在时段内)."""
        return self.enabled and not self.in_window()