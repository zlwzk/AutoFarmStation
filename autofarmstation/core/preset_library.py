"""Steam 挂机游戏预设库.

每个 Preset 包含:
- 游戏名 / Steam AppID(可选)
- 进程名 / 窗口标题匹配模式
- 推荐:连点器点位、键盘宏、屏幕选项
- 描述

用户可以在 UI 中应用预设,也可把自定义配置保存为 Preset.
"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable

from ..utils.paths import atomic_write, user_preset_dir
from .input_sender import MouseButton
from .autoclicker import ClickPoint


@dataclass
class Preset:
    """一个游戏预设."""

    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    name: str = ""
    description: str = ""
    steam_appid: str = ""  # 可选,仅作显示
    match_process: str = ""  # 进程名匹配(小写,子串)
    match_title: str = ""  # 窗口标题匹配(小写,子串)
    # 推荐配置
    autoclicker: dict = field(default_factory=dict)  # {interval_ms, points: [{x,y,button,double}], jitter_pct}
    key_macro: dict = field(default_factory=dict)  # {steps: [{key, hold_ms, delay_before_ms}], loop}
    preview: dict = field(default_factory=dict)  # {refresh_fps, columns, scale}
    builtin: bool = False  # 是否内置(不可删除)
    tags: list[str] = field(default_factory=list)


# === 内置预设 ===
BUILTIN_PRESETS: list[Preset] = [
    Preset(
        id="melvor-idle",
        name="Melvor Idle",
        description=(
            "经典放置 RPG,Steam AppID 1887720。"
            "挂机时只需偶尔按键或点击,推荐慢速点击(200ms)+ 自动攻击键。"
        ),
        steam_appid="1887720",
        match_process="melvoridle",
        match_title="melvor",
        autoclicker={
            "interval_ms": 250,
            "jitter_pct": 10,
            "points": [{"x": 0, "y": 0, "button": "left", "double": False}],
            "loop": True,
        },
        key_macro={
            "steps": [{"key": "space", "hold_ms": 50, "delay_before_ms": 1500}],
            "loop": True,
        },
        preview={"refresh_fps": 1.0, "columns": 2, "scale": 0.4},
        builtin=True,
        tags=["steam", "idle-rpg"],
    ),
    Preset(
        id="universal-paperclips",
        name="Universal Paperclips",
        description=(
            "网页小游戏,窗口模式运行即可。"
            "无需自动点击,推荐把窗口置顶后偶尔点击主交互区。"
        ),
        match_process="chrome",
        match_title="universal paperclips",
        autoclicker={
            "interval_ms": 300,
            "jitter_pct": 15,
            "points": [{"x": 0, "y": 0, "button": "left"}],
            "loop": True,
        },
        preview={"refresh_fps": 0.5, "columns": 1, "scale": 0.5},
        builtin=True,
        tags=["web", "narrative"],
    ),
    Preset(
        id="clicker-heroes",
        name="Clicker Heroes",
        description=(
            "Steam 放置点击游戏(360820)。挂机英雄自动战斗,"
            "通常只需手动点击伤害怪/送礼。"
        ),
        steam_appid="360820",
        match_process="clickerheroes",
        match_title="clicker heroes",
        autoclicker={
            "interval_ms": 120,
            "jitter_pct": 20,
            "points": [{"x": 0, "y": 0, "button": "left"}],
            "loop": True,
        },
        preview={"refresh_fps": 1.0, "columns": 1, "scale": 0.4},
        builtin=True,
        tags=["steam", "clicker"],
    ),
    Preset(
        id="idle-slayer",
        name="Idle Slayer",
        description=(
            "Steam 放置动作游戏(1610410)。挂机自动打怪,"
            "建议搭配键盘 Q/W/E/R 循环技能。"
        ),
        steam_appid="1610410",
        match_process="idle slayer",
        match_title="idle slayer",
        autoclicker={
            "interval_ms": 300,
            "jitter_pct": 10,
            "points": [{"x": 0, "y": 0, "button": "left"}],
            "loop": True,
        },
        key_macro={
            "steps": [
                {"key": "q", "hold_ms": 30},
                {"key": "w", "hold_ms": 30},
                {"key": "e", "hold_ms": 30},
                {"key": "r", "hold_ms": 30},
            ],
            "loop": True,
        },
        preview={"refresh_fps": 1.0, "columns": 2, "scale": 0.4},
        builtin=True,
        tags=["steam", "idle-action"],
    ),
    Preset(
        id="mr-mine",
        name="Mr. Mine",
        description=(
            "Steam 放置挖矿游戏(1399160)。点击挖矿、推进深度,"
            "慢速点击 200ms 较合适。"
        ),
        steam_appid="1399160",
        match_process="mrmine",
        match_title="mr.mine",
        autoclicker={
            "interval_ms": 200,
            "jitter_pct": 10,
            "points": [{"x": 0, "y": 0, "button": "left"}],
            "loop": True,
        },
        preview={"refresh_fps": 1.0, "columns": 2, "scale": 0.4},
        builtin=True,
        tags=["steam", "idle-mining"],
    ),
    Preset(
        id="ngu-idle",
        name="NGU IDLE",
        description=(
            "Steam 恶搞放置游戏(1147800)。点击+快捷键组合使用,"
            "推荐间隔长一些(500ms)避免崩游戏。"
        ),
        steam_appid="1147800",
        match_process="ngu idle",
        match_title="ngu idle",
        autoclicker={
            "interval_ms": 500,
            "jitter_pct": 10,
            "points": [{"x": 0, "y": 0, "button": "left"}],
            "loop": True,
        },
        key_macro={
            "steps": [{"key": "f2", "hold_ms": 50}, {"key": "f3", "hold_ms": 50}],
            "loop": True,
        },
        preview={"refresh_fps": 0.5, "columns": 1, "scale": 0.5},
        builtin=True,
        tags=["steam", "idle-rpg"],
    ),
    Preset(
        id="cell-to-singularity",
        name="Cell to Singularity",
        description=(
            "Steam 进化放置游戏(723840)。点击进化点 + 偶尔按键。"
        ),
        steam_appid="723840",
        match_process="cell to singularity",
        match_title="cell to singularity",
        autoclicker={
            "interval_ms": 250,
            "jitter_pct": 10,
            "points": [{"x": 0, "y": 0, "button": "left"}],
            "loop": True,
        },
        preview={"refresh_fps": 1.0, "columns": 2, "scale": 0.4},
        builtin=True,
        tags=["steam", "idle-evolution"],
    ),
    Preset(
        id="adventure-capitalist",
        name="AdVenture Capitalist",
        description=(
            "Steam 放置大亨游戏(346900)。游戏自带挂机,"
            "本工具主要用于离线挂机管理/批量按键。"
        ),
        steam_appid="346900",
        match_process="adventurecapitalist",
        match_title="adventure capitalist",
        key_macro={
            "steps": [{"key": "space", "hold_ms": 50}],
            "loop": True,
        },
        preview={"refresh_fps": 0.5, "columns": 1, "scale": 0.5},
        builtin=True,
        tags=["steam", "tycoon"],
    ),
    Preset(
        id="egg-inc",
        name="Egg, Inc.",
        description=(
            "手机模拟器挂机游戏。窗口化运行后,"
            "使用慢速点击配合 R 键升级。"
        ),
        match_process="noxplayer",
        match_title="egg",
        autoclicker={
            "interval_ms": 350,
            "jitter_pct": 10,
            "points": [{"x": 0, "y": 0, "button": "left"}],
            "loop": True,
        },
        key_macro={
            "steps": [{"key": "r", "hold_ms": 50}],
            "loop": True,
        },
        preview={"refresh_fps": 0.5, "columns": 2, "scale": 0.4},
        builtin=True,
        tags=["emulator", "casual"],
    ),
    Preset(
        id="generic-idle",
        name="通用挂机游戏",
        description=(
            "不知道选什么?用它就对了:慢速点击 + 周期性按键,"
            "对大部分挂机游戏安全。"
        ),
        autoclicker={
            "interval_ms": 300,
            "jitter_pct": 15,
            "points": [{"x": 0, "y": 0, "button": "left"}],
            "loop": True,
        },
        preview={"refresh_fps": 0.5, "columns": 2, "scale": 0.4},
        builtin=True,
        tags=["generic"],
    ),
]


class PresetLibrary:
    """预设库管理器."""

    def __init__(self, logger: logging.Logger | None = None) -> None:
        self._log = logger or logging.getLogger("autofarmstation.presets")
        self._lock = threading.RLock()
        self._items: dict[str, Preset] = {p.id: p for p in BUILTIN_PRESETS}
        self._load_user_presets()

    # --- 查询 ---
    def all(self) -> list[Preset]:
        with self._lock:
            return list(self._items.values())

    def builtin(self) -> list[Preset]:
        with self._lock:
            return [p for p in self._items.values() if p.builtin]

    def user_presets(self) -> list[Preset]:
        with self._lock:
            return [p for p in self._items.values() if not p.builtin]

    def get(self, pid: str) -> Preset | None:
        with self._lock:
            return self._items.get(pid)

    def find_match(
        self, *, process_name: str = "", title: str = ""
    ) -> Preset | None:
        """根据进程名/窗口标题匹配最佳预设.无匹配时返回 'generic-idle' 兜底."""
        pn = (process_name or "").lower()
        tt = (title or "").lower()
        best: Preset | None = None
        best_score = 0
        with self._lock:
            for p in self._items.values():
                if not p.match_process and not p.match_title:
                    # 通用兜底预设(match_process/title 都为空)
                    if p.id == "generic-idle":
                        # 单独保留,稍后用
                        continue
                    continue
                score = 0
                if p.match_process and p.match_process in pn:
                    score += 2
                if p.match_title and p.match_title in tt:
                    score += 3
                if score > best_score:
                    best_score = score
                    best = p
        if best is not None:
            return best
        # 无匹配时返回通用兜底
        with self._lock:
            return self._items.get("generic-idle")

    # --- 增删 ---
    def add(self, preset: Preset) -> None:
        preset.builtin = False
        with self._lock:
            self._items[preset.id] = preset
        self._save_user_presets()

    def remove(self, pid: str) -> bool:
        with self._lock:
            p = self._items.get(pid)
            if p is None or p.builtin:
                return False
            del self._items[pid]
        self._save_user_presets()
        return True

    # --- 持久化 ---
    def _load_user_presets(self) -> None:
        d = user_preset_dir()
        for f in sorted(d.glob("*.json")):
            try:
                raw = json.loads(f.read_text(encoding="utf-8"))
                p = Preset(
                    id=str(raw.get("id") or f.stem),
                    name=str(raw.get("name") or ""),
                    description=str(raw.get("description") or ""),
                    steam_appid=str(raw.get("steam_appid") or ""),
                    match_process=str(raw.get("match_process") or ""),
                    match_title=str(raw.get("match_title") or ""),
                    autoclicker=dict(raw.get("autoclicker") or {}),
                    key_macro=dict(raw.get("key_macro") or {}),
                    preview=dict(raw.get("preview") or {}),
                    builtin=False,
                    tags=list(raw.get("tags") or []),
                )
                with self._lock:
                    if p.id not in self._items:
                        self._items[p.id] = p
            except Exception as e:  # noqa: BLE001
                self._log.warning("加载预设失败 %s: %s", f, e)

    def _save_user_presets(self) -> None:
        d = user_preset_dir()
        with self._lock:
            for p in self._items.values():
                if p.builtin:
                    continue
                f = d / f"{p.id}.json"
                try:
                    atomic_write(f, json.dumps(asdict(p), indent=2, ensure_ascii=False))
                except Exception as e:  # noqa: BLE001
                    self._log.warning("保存预设失败 %s: %s", p.id, e)


# === 工具 ===
def autoclicker_dict_to_points(d: dict) -> list[ClickPoint]:
    """把预设 dict 转换为 ClickPoint 列表."""
    pts: list[ClickPoint] = []
    for p in d.get("points", []) or []:
        try:
            pts.append(ClickPoint(
                x=int(p.get("x", 0)),
                y=int(p.get("y", 0)),
                button=MouseButton(p.get("button", "left")),
                double=bool(p.get("double", False)),
            ))
        except Exception:
            continue
    return pts


def key_macro_dict_to_steps(d: dict) -> list:
    """把预设 dict 转换为 KeyStep 列表."""
    from .key_macro import KeyStep
    out: list[KeyStep] = []
    for s in d.get("steps", []) or []:
        try:
            out.append(KeyStep(
                key=str(s.get("key", "")),
                hold_ms=int(s.get("hold_ms", 50)),
                delay_before_ms=int(s.get("delay_before_ms", 0)),
            ))
        except Exception:
            continue
    return out