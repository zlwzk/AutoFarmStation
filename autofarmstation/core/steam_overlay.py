"""Steam 叠加层快捷键的开关.

Steam 默认 Shift+Tab 唤起游戏内叠加层。关闭有两种实现:

1. **进程内拦截**(在游戏窗口内 hook Shift+Tab,不让 Steam 客户端收到) —
   复杂、不通用,本工具不实现。
2. **配置层关闭**(本工具走的方案) — 写 ``%APPDATA%\\Steam\\steam.cfg``
   的 ``[Install] SteamOverlay=0`` / ``=1``。Steam store 进程会在下次启动
   读取这个文件决定是否给游戏注入 GameOverlayRenderer.dll。

注意:写入后必须 **重启 Steam 客户端** 才会生效;运行中的游戏不会受影响。
本工具负责写入/还原,UI 明确提示用户重启 Steam。
"""

from __future__ import annotations

import datetime
import logging
import os
import re
from pathlib import Path

from ..utils.paths import steam_config_path


_log = logging.getLogger("autofarmstation.steam_overlay")

# steam.cfg 是 ini 格式。这里用正则定点替换 [Install] SteamOverlay 一行,
# 其它行原样保留;如果节不存在则追加。
_RE_INSTALL = re.compile(
    r"\[Install\][ \t]*\r?\n(?P<body>.*?)(?=\n\[|\Z)",
    re.DOTALL | re.IGNORECASE,
)
_RE_KEYVALUE = re.compile(
    r"^(\s*)SteamOverlay\s*=\s*(\d+)\s*$",
    re.IGNORECASE | re.MULTILINE,
)


def describe(path: Path | None = None) -> dict:
    """读取 steam.cfg 当前状态:是否存在、当前 SteamOverlay 值、文件大小."""
    p = path or steam_config_path()
    out = {
        "exists": p.exists(),
        "path": str(p),
        "overlay": None,  # 0 / 1 / None(未设置)
        "size": p.stat().st_size if p.exists() else 0,
    }
    if p.exists():
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            out["error"] = str(e)
            return out
        m = _RE_INSTALL.search(text)
        if m:
            kv = _RE_KEYVALUE.search(m.group("body") or "")
            if kv:
                out["overlay"] = int(kv.group(2))
    return out


def set_overlay(enabled: bool, *, backup: str = "",
               path: Path | None = None) -> tuple[bool, str]:
    """把 steam.cfg 的 SteamOverlay 写成 ``0``(禁用)/``1``(启用).

    返回 (成功?, 写入后的文件内容);失败时不修改文件,内容回原状。
    backup 用于在「禁用」时先备份原文件,「启用」时用同字段作为还原。
    path: 测试时传入临时文件路径,默认用真实 steam.cfg。
    """
    p = path or steam_config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    target = "1" if enabled else "0"
    original = ""
    if p.exists():
        try:
            original = p.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            _log.warning("读取 steam.cfg 失败: %s", e)
            return False, original
    new_text = _patch_overlay(original, target)
    try:
        p.write_text(new_text, encoding="utf-8")
    except OSError as e:
        _log.warning("写入 steam.cfg 失败: %s", e)
        return False, original
    _log.info("steam.cfg: SteamOverlay=%s (path=%s)", target, p)
    return True, backup or original


def restore_backup(backup_text: str, *, path: Path | None = None) -> tuple[bool, str]:
    """把备份文本回写到 steam.cfg.用于「撤销/恢复」."""
    if not backup_text:
        return False, ""
    p = path or steam_config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        p.write_text(backup_text, encoding="utf-8")
        return True, backup_text
    except OSError as e:
        _log.warning("还原 steam.cfg 失败: %s", e)
        return False, backup_text


# ---------- 内部 ----------
def _patch_overlay(text: str, value: str) -> str:
    """把 [Install] 节内的 SteamOverlay 一行改成指定值;缺失则追加."""
    m = _RE_INSTALL.search(text)
    if not m:
        # 没有 [Install] 节 → 末尾加一个
        suffix = "" if text.endswith("\n") or not text else "\n"
        return f"{text}{suffix}[Install]\nSteamOverlay={value}\n"
    section_full = m.group(0)
    body = m.group("body") or ""
    # body 已是「节头换行之后到结尾」的内容,这里避免重复换行
    body_end_nl = "" if body.endswith("\n") else "\n"
    if _RE_KEYVALUE.search(body):
        new_body = _RE_KEYVALUE.sub(
            lambda mm: f"{mm.group(1)}SteamOverlay={value}", body,
        )
    else:
        sep = "" if not body.strip() or body.endswith("\n") else "\n"
        new_body = f"{body}{sep}SteamOverlay={value}\n"
    new_section = f"[Install]\n{new_body}{body_end_nl}"
    if not section_full.endswith("\n"):
        new_section = new_section.rstrip("\n")
    return text[:m.start()] + new_section + text[m.end():]