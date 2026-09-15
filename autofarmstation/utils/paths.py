"""路径与用户目录处理.

全部使用 %APPDATA% / %USERPROFILE% / %LOCALAPPDATA% / %TEMP% 占位符,
绝不写死任何盘符或用户名。
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Final

# === 应用元数据 ===
APP_DIR_NAME: Final[str] = "AutoFarmStation"


def _env(name: str, default: str) -> str:
    """读取环境变量,空值用 fallback."""
    val = os.environ.get(name)
    if val:
        return val
    return default


def user_data_dir() -> Path:
    """返回 %APPDATA%\\AutoFarmStation,不存在则创建."""
    base = _env("APPDATA", str(Path.home() / "AppData" / "Roaming"))
    p = Path(base) / APP_DIR_NAME
    p.mkdir(parents=True, exist_ok=True)
    return p


def user_log_dir() -> Path:
    """返回 %APPDATA%\\AutoFarmStation\\logs."""
    p = user_data_dir() / "logs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def user_cache_dir() -> Path:
    """返回 %APPDATA%\\AutoFarmStation\\cache."""
    p = user_data_dir() / "cache"
    p.mkdir(parents=True, exist_ok=True)
    return p


def user_macro_dir() -> Path:
    """返回 %APPDATA%\\AutoFarmStation\\macros(用户录制的宏)."""
    p = user_data_dir() / "macros"
    p.mkdir(parents=True, exist_ok=True)
    return p


def user_preset_dir() -> Path:
    """返回 %APPDATA%\\AutoFarmStation\\presets(用户自定义预设)."""
    p = user_data_dir() / "presets"
    p.mkdir(parents=True, exist_ok=True)
    return p


def temp_dir() -> Path:
    """返回系统临时目录,确保存在."""
    p = Path(tempfile.gettempdir())
    p.mkdir(parents=True, exist_ok=True)
    return p


def resource_dir() -> Path:
    """返回资源目录(PyInstaller 打包后指向 _MEIPASS,源码时指向仓库根)."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    # 源码运行时,utils/paths.py 在 autofarmstation/utils/ 下,父级是 autofarmstation/,再父级是仓库根
    return Path(__file__).resolve().parents[2]


def config_path() -> Path:
    """主配置文件路径."""
    return user_data_dir() / "config.json"


def stats_path() -> Path:
    """统计数据文件路径."""
    return user_data_dir() / "stats.json"


def session_path() -> Path:
    """上次会话快照路径(用于「是否恢复上次的窗口」)."""
    return user_data_dir() / "session.json"


def user_bat_dir() -> Path:
    """%APPDATA%\\AutoFarmStation\\bats —— 用户自加的 bat 脚本."""
    p = user_data_dir() / "bats"
    p.mkdir(parents=True, exist_ok=True)
    return p


def bundled_bat_dir() -> Path:
    """内置 bat 库:PyInstaller 时指向 _MEIPASS/bat,源码运行时指向仓库根/bat."""
    return resource_dir() / "bat"


def steam_config_path() -> Path:
    """Steam 的 steam.cfg 路径(平台 store 进程读取)."""
    base = _env("APPDATA", str(Path.home() / "AppData" / "Roaming"))
    return Path(base) / "Steam" / "steam.cfg"


def steam_userdata_dir() -> Path:
    """Steam 用户数据根目录(localconfig.vdf 等)."""
    base = _env("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))
    return Path(base) / "Steam" / "htmlcache"  # 占位,实际路径见具体子模块调用方


# === 工具函数 ===
def safe_filename(name: str) -> str:
    """把任意字符串规整为合法文件名."""
    bad = '<>:"/\\|?*\x00'
    out = "".join("_" if ch in bad else ch for ch in name)
    out = out.strip(" .")
    return out or "unnamed"


def atomic_write(path: Path, content: bytes | str) -> None:
    """原子写入:先写 .tmp,再 rename 覆盖(避免半截文件)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    mode = "wb" if isinstance(content, bytes) else "w"
    enc = None if isinstance(content, bytes) else "utf-8"
    with open(tmp, mode, encoding=enc) as f:
        f.write(content)  # type: ignore[arg-type]
        if isinstance(content, str):
            f.flush()
    # Windows 下 rename 覆盖现有文件
    if sys.platform == "win32":
        if path.exists():
            path.unlink()
    shutil.move(str(tmp), str(path))