"""多窗口自动化中枢.

核心思想:
    * **每个窗口各自持有独立的自动化配置**(连点器点位、键盘宏步骤),
      互不干扰 —— 满足「单个窗口也可以分开执行不同的操作」。
    * **同一份配置可以一次性广播到多个窗口并同时启动** —— 满足
      「同步执行某一个或者某一些连贯操作」。

数据结构:
    self._clicker_cfg[hwnd]  -> AutoClickerConfig
    self._key_cfg[hwnd]      -> KeyMacroConfig
    self._clickers[hwnd]     -> 运行中的 AutoClicker 实例
    self._macros[hwnd]       -> 运行中的 KeyMacro 实例

线程安全:所有公开方法都加锁,可被 UI 线程 / 定时器 / 热键回调调用。
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass

from .autoclicker import AutoClicker, AutoClickerConfig
from .key_macro import KeyMacro, KeyMacroConfig
from .input_sender import InputSender
from .statistics import Statistics


@dataclass
class BundleResult:
    """一次批量操作的结果."""

    started: int = 0
    failed: int = 0
    message: str = ""


class AutomationHub:
    """多窗口自动化中枢:统一管理「每窗口独立配置」与「多窗口同步执行」."""

    def __init__(
        self,
        stats: Statistics | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._log = logger or logging.getLogger("autofarmstation.hub")
        self._stats = stats
        self._lock = threading.RLock()

        self._clicker_cfg: dict[int, AutoClickerConfig] = {}
        self._key_cfg: dict[int, KeyMacroConfig] = {}
        self._clickers: dict[int, AutoClicker] = {}
        self._macros: dict[int, KeyMacro] = {}

        # 前进:每窗口使用独立的 sender,避免长按状态互相打架
        self._senders: dict[int, InputSender] = {}

    # ---------- 工具 ----------
    def _sender_for(self, hwnd: int) -> InputSender:
        s = self._senders.get(hwnd)
        if s is None:
            s = InputSender(default_mode="post")
            self._senders[hwnd] = s
        return s

    def forget(self, hwnd: int) -> None:
        """窗口被移除时清理它的配置与实例."""
        with self._lock:
            self.stop(hwnd)
            self._clicker_cfg.pop(hwnd, None)
            self._key_cfg.pop(hwnd, None)
            self._senders.pop(hwnd, None)

    # ---------- 配置读写(单窗口独立) ----------
    def set_clicker_config(self, hwnd: int, cfg: AutoClickerConfig) -> None:
        with self._lock:
            cfg.hwnd = int(hwnd)
            self._clicker_cfg[int(hwnd)] = cfg

    def clicker_config(self, hwnd: int) -> AutoClickerConfig | None:
        with self._lock:
            return self._clicker_cfg.get(int(hwnd))

    def set_key_macro_config(self, hwnd: int, cfg: KeyMacroConfig) -> None:
        with self._lock:
            cfg.hwnd = int(hwnd)
            self._key_cfg[int(hwnd)] = cfg

    def key_macro_config(self, hwnd: int) -> KeyMacroConfig | None:
        with self._lock:
            return self._key_cfg.get(int(hwnd))

    # ---------- 同步:一份配置 → 多个窗口 ----------
    def broadcast_clicker_config(
        self, hwnds: list[int], cfg: AutoClickerConfig
    ) -> None:
        """把连点器配置复制到多个窗口(每个窗口各自一份,互相独立)."""
        with self._lock:
            for h in hwnds:
                import copy
                c = copy.deepcopy(cfg)
                c.hwnd = int(h)
                self._clicker_cfg[int(h)] = c

    def broadcast_key_macro_config(
        self, hwnds: list[int], cfg: KeyMacroConfig
    ) -> None:
        """把键盘宏配置复制到多个窗口."""
        with self._lock:
            for h in hwnds:
                import copy
                c = copy.deepcopy(cfg)
                c.hwnd = int(h)
                self._key_cfg[int(h)] = c

    # ---------- 启停 ----------
    def start_clicker(self, hwnd: int) -> bool:
        """(重新)启动指定窗口的连点器."""
        hwnd = int(hwnd)
        with self._lock:
            cfg = self._clicker_cfg.get(hwnd)
            if cfg is None:  # 没有配置时用「中心点单击」兜底
                from .autoclicker import ClickPoint
                from .window_finder import client_size
                w, h = client_size(hwnd)
                if w <= 0 or h <= 0:
                    return False
                cfg = AutoClickerConfig(hwnd=hwnd, points=[ClickPoint(w // 2, h // 2)])
                self._clicker_cfg[hwnd] = cfg
            self.stop_clicker(hwnd)
            clicker = AutoClicker(cfg, sender=self._sender_for(hwnd))

            def _on_click(_count, _pt, _hwnd=hwnd) -> None:
                if self._stats:
                    self._stats.add_click(1, _hwnd)

            clicker.on_click = _on_click
            ok = clicker.start()
            if ok:
                self._clickers[hwnd] = clicker
            return ok

    def start_key_macro(self, hwnd: int) -> bool:
        """(重新)启动指定窗口的键盘宏."""
        hwnd = int(hwnd)
        with self._lock:
            cfg = self._key_cfg.get(hwnd)
            if cfg is None or not cfg.steps:
                return False
            self.stop_key_macro(hwnd)
            macro = KeyMacro(cfg, sender=self._sender_for(hwnd))

            def _on_step(_round, _step, _hwnd=hwnd) -> None:
                if self._stats:
                    self._stats.add_key(1, _hwnd)

            macro.on_step = _on_step
            ok = macro.start()
            if ok:
                self._macros[hwnd] = macro
            return ok

    def stop_clicker(self, hwnd: int) -> None:
        with self._lock:
            c = self._clickers.pop(int(hwnd), None)
        if c is not None:
            try:
                c.stop()
            except Exception:  # noqa: BLE001
                pass

    def stop_key_macro(self, hwnd: int) -> None:
        with self._lock:
            m = self._macros.pop(int(hwnd), None)
        if m is not None:
            try:
                m.stop()
            except Exception:  # noqa: BLE001
                pass

    def stop(self, hwnd: int) -> None:
        self.stop_clicker(hwnd)
        self.stop_key_macro(hwnd)

    def stop_all(self) -> None:
        with self._lock:
            hwnds = list(set(self._clickers) | set(self._macros))
        for h in hwnds:
            self.stop(h)

    # ---------- 批量启停(同步执行) ----------
    def start_many(self, hwnds: list[int], *, clicker: bool = True,
                   key_macro: bool = True) -> BundleResult:
        res = BundleResult()
        for h in hwnds:
            ok = False
            if clicker:
                ok = self.start_clicker(h) or ok
            if key_macro:
                ok = self.start_key_macro(h) or ok
            if ok:
                res.started += 1
            else:
                res.failed += 1
        res.message = f"已启动 {res.started} 个窗口" + (
            f",{res.failed} 个未启动(缺配置或窗口无效)" if res.failed else ""
        )
        return res

    def stop_many(self, hwnds: list[int]) -> BundleResult:
        res = BundleResult()
        for h in hwnds:
            self.stop(h)
            res.started += 1
        res.message = f"已停止 {res.started} 个窗口"
        return res

    # ---------- 状态查询 ----------
    def is_clicker_running(self, hwnd: int) -> bool:
        with self._lock:
            c = self._clickers.get(int(hwnd))
        return bool(c and c.is_running)

    def is_key_macro_running(self, hwnd: int) -> bool:
        with self._lock:
            m = self._macros.get(int(hwnd))
        return bool(m and m.is_running)

    def is_running(self, hwnd: int) -> bool:
        return self.is_clicker_running(hwnd) or self.is_key_macro_running(hwnd)

    def running_hwnds(self) -> list[int]:
        with self._lock:
            return sorted(set(self._clickers) | set(self._macros))

    def click_count(self, hwnd: int) -> int:
        with self._lock:
            c = self._clickers.get(int(hwnd))
        return c.click_count if c else 0

    def status_text(self, hwnd: int) -> str:
        """给预览卡片用的简短状态."""
        with self._lock:
            c = self._clickers.get(int(hwnd))
            m = self._macros.get(int(hwnd))
        parts = []
        if c and c.is_running:
            tag = "暂停" if c.is_paused else "连点"
            sec = int(c.runtime_seconds)
            parts.append(f"{tag} {sec // 60:02d}:{sec % 60:02d} ({c.click_count})")
        if m and m.is_running:
            parts.append(f"宏 {m.rounds} 轮")
        return " · ".join(parts)

    def has_config(self, hwnd: int) -> bool:
        with self._lock:
            return int(hwnd) in self._clicker_cfg or int(hwnd) in self._key_cfg

    def configured_hwnds(self) -> list[int]:
        with self._lock:
            return sorted(set(self._clicker_cfg) | set(self._key_cfg))

    # ---------- 导出 / 导入 ----------
    def export_configs(self) -> dict[str, dict]:
        """导出所有窗口的配置(用于持久化)."""
        out: dict[str, dict] = {}
        with self._lock:
            for h, cfg in self._clicker_cfg.items():
                out.setdefault(str(h), {})["clicker"] = cfg.to_dict()
            for h, cfg in self._key_cfg.items():
                out.setdefault(str(h), {})["key_macro"] = cfg.to_dict()
        return out

    def import_configs(self, data: dict[str, dict]) -> None:
        """导入窗口配置(持久化恢复)."""
        if not data:
            return
        for h_str, blob in data.items():
            try:
                h = int(h_str)
            except (TypeError, ValueError):
                continue
            if "clicker" in blob:
                try:
                    self._clicker_cfg[h] = AutoClickerConfig.from_dict(blob["clicker"])
                except Exception:  # noqa: BLE001
                    pass
            if "key_macro" in blob:
                try:
                    self._key_cfg[h] = KeyMacroConfig.from_dict(blob["key_macro"])
                except Exception:  # noqa: BLE001
                    pass