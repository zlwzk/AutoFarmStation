"""跨启动的窗口/配置快照.

保存上次的窗口列表(用 exe basename + title 稳定标识)+ 每窗口的连点器/键盘宏配置。
下次启动时按 (exe, title 稳定前缀) 在当前可见窗口里匹配,找到的窗口自动添加并
套用配置 —— 「是否恢复上次的窗口」。

匹配策略(从强到弱):
    1. exe_basename 相同 + saved.title 是当前标题的子串            → 100 分
    2. exe_basename 相同 + saved.title 的稳定前缀在当前标题里     →  70 分
    3. exe_basename 相同(兜底,游戏启动后标题/角色名变了仍能命中)  →  40 分
    4. process_name 相同 + title 完全子串                          →  30 分

贪心分配:高分优先;同分按 saved 顺序(先添加的优先);每个当前窗口最多匹配一次。
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING

from ..utils.paths import atomic_write, session_path
from . import window_finder as wf

if TYPE_CHECKING:
    from .process_manager import ProcessManager
    from .automation_hub import AutomationHub


_log = logging.getLogger("autofarmstation.session")


# === 标题稳定前缀提取 ===
# 启发式:绝大多数挂机游戏的标题形如 "游戏名 - 角色/服务器/关卡",
# "游戏名" 部分是稳定的,我们用第一个分隔符之前的内容;否则取前 8 字符。
def _stable_prefix(title: str) -> str:
    if not title:
        return ""
    for sep in (" - ", " — ", " | ", " · ", " — ", "- "):
        idx = title.find(sep)
        if 0 < idx:
            return title[:idx].strip()
    return title[: min(8, len(title))].strip()


# === 进程信息补全(给 WindowInfo 加 exe_basename / process_name) ===
def _enrich_visible(visible: list[wf.WindowInfo]) -> list[tuple[wf.WindowInfo, str, str]]:
    """对每个窗口用 psutil 取进程名 + exe 名.失败留空串."""
    out: list[tuple[wf.WindowInfo, str, str]] = []
    try:
        import psutil  # type: ignore
    except Exception:  # noqa: BLE001
        psutil = None  # type: ignore
    for w in visible:
        exe_b = ""
        pname = ""
        if psutil and w.pid:
            try:
                p = psutil.Process(w.pid)
                pname = p.name()
                exe = p.exe() if hasattr(p, "exe") else ""
                if exe:
                    exe_b = Path(exe).name
            except Exception:  # noqa: BLE001
                pass
        out.append((w, exe_b, pname))
    return out


class Session:
    """单文件快照:写入 / 读取 / 匹配."""

    SCHEMA_VERSION = 1

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or session_path()
        self._lock = threading.RLock()

    # ---------- 写入 ----------
    def save(self, pm: "ProcessManager", hub: "AutomationHub") -> None:
        from .process_manager import TrackedProcess  # 避免循环引用
        items: list[dict] = []
        cfg_map = hub.export_configs()
        for tp in pm.all():
            items.append(
                {
                    "id": f"{tp.exe}|{tp.title}",
                    "exe": tp.exe or "",
                    "exe_basename": (Path(tp.exe).name if tp.exe else tp.name or ""),
                    "name": tp.name or "",
                    "title": tp.title or "",
                    "config": cfg_map.get(str(tp.hwnd), {}) or {},
                    "added_at": float(tp.added_at or 0.0),
                }
            )
        payload = {
            "version": self.SCHEMA_VERSION,
            "saved_at": time.time(),
            "items": items,
        }
        with self._lock:
            try:
                atomic_write(
                    self._path,
                    json.dumps(payload, indent=2, ensure_ascii=False),
                )
            except Exception as e:  # noqa: BLE001
                _log.warning("保存 session 失败: %s", e)

    # ---------- 读取 ----------
    def load(self) -> dict | None:
        with self._lock:
            if not self._path.exists():
                return None
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception as e:  # noqa: BLE001
                _log.warning("读取 session 失败,当作空: %s", e)
                return None
            if not isinstance(data, dict) or data.get("version") != self.SCHEMA_VERSION:
                return None
            items = data.get("items") or []
            if not isinstance(items, list) or not items:
                return None
            return {"saved_at": data.get("saved_at", 0.0), "items": items}

    # ---------- 清理 ----------
    def clear(self) -> None:
        with self._lock:
            try:
                self._path.unlink()
            except FileNotFoundError:
                pass
            except Exception as e:  # noqa: BLE001
                _log.warning("清理 session 失败: %s", e)

    # ---------- 匹配 ----------
    @staticmethod
    def match(
        saved_items: list[dict],
        visible: list[wf.WindowInfo],
        *,
        enricher=None,
    ) -> list[tuple[dict, wf.WindowInfo]]:
        """saved → current 候选匹配.一个 current 窗口最多命中一次.

        enricher: 可选,传入 (visible) → list[tuple[WindowInfo, exe_basename, process_name]]
        的函数,默认用 psutil 实现。测试时可注入假数据。
        """
        if not saved_items or not visible:
            return []
        enriched = (enricher or _enrich_visible)(visible)
        candidates: list[tuple[int, int, dict, wf.WindowInfo]] = []
        for idx, s in enumerate(saved_items):
            sb = (s.get("exe_basename") or "").lower()
            sn = (s.get("name") or "").lower()
            st = s.get("title") or ""
            sp = _stable_prefix(st)
            for w, wb_raw, wn_raw in enriched:
                wb = (wb_raw or "").lower()
                wn = (wn_raw or "").lower()
                score = -1
                if sb and wb and sb == wb:
                    score = 40
                    if st and st in w.title:
                        score = 100
                    elif sp and len(sp) >= 2 and sp in w.title:
                        score = 70
                elif sn and wn and sn == wn and st and st in w.title:
                    score = 30
                if score > 0:
                    candidates.append((score, idx, s, w))

        # 高分优先,同分则按 saved 顺序(先添加的优先)
        candidates.sort(key=lambda x: (-x[0], x[1]))
        used: set[int] = set()
        out: list[tuple[dict, wf.WindowInfo]] = []
        for score, _, s, w in candidates:
            if int(w.hwnd) in used:
                continue
            out.append((s, w))
            used.add(int(w.hwnd))
        return out