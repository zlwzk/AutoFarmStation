"""隐私脱敏.

所有日志/反馈/导出文本都需经 Sanitize 处理,确保不含:
- Windows 用户名
- 游戏安装绝对路径
- AppData、TEMP 路径
- 机器名
"""

from __future__ import annotations

import getpass
import os
import re
import socket
from typing import Final

# === 通用占位符 ===
PLACEHOLDER_USERNAME: Final[str] = "%USERPROFILE%"
PLACEHOLDER_APPDATA: Final[str] = "%APPDATA%"
PLACEHOLDER_LOCALAPPDATA: Final[str] = "%LOCALAPPDATA%"
PLACEHOLDER_TEMP: Final[str] = "%TEMP%"
PLACEHOLDER_HOSTNAME: Final[str] = "<主机名>"

# === 正则缓存 ===
_username: str = ""
_appdata: str = ""
_localappdata: str = ""
_temp: str = ""
_hostname: str = ""


def _init() -> None:
    """首次使用时初始化本机敏感字段."""
    global _username, _appdata, _localappdata, _temp, _hostname
    if _username:
        return
    try:
        _username = getpass.getuser()
    except Exception:
        _username = ""
    try:
        _hostname = socket.gethostname()
    except Exception:
        _hostname = ""
    _appdata = os.environ.get("APPDATA", "") or ""
    _localappdata = os.environ.get("LOCALAPPDATA", "") or ""
    _temp = os.environ.get("TEMP", "") or os.environ.get("TMP", "") or ""


def _replace_known(text: str) -> str:
    """替换已知的本机敏感字段."""
    _init()
    if _username:
        text = text.replace(_username, PLACEHOLDER_USERNAME)
    if _hostname:
        text = text.replace(_hostname, PLACEHOLDER_HOSTNAME)
    if _appdata:
        text = text.replace(_appdata, PLACEHOLDER_APPDATA)
    if _localappdata:
        text = text.replace(_localappdata, PLACEHOLDER_LOCALAPPDATA)
    if _temp:
        text = text.replace(_temp, PLACEHOLDER_TEMP)
    return text


# 兜底:盘符 \Users\xxx\ 这种形式不管用户名是什么都替换为占位符
_USER_PATH_RE = re.compile(r"[A-Za-z]:\\Users\\[^\\/\s\"'<>]+", re.IGNORECASE)


def sanitize(text: str) -> str:
    """把字符串中的本机隐私字段全部替换为占位符."""
    if not text:
        return text
    out = _replace_known(text)
    # 兜底正则
    out = _USER_PATH_RE.sub(PLACEHOLDER_USERNAME, out)
    return out


def sanitize_path(path: str) -> str:
    """脱敏路径,优先做精确替换,再走兜底正则."""
    return sanitize(path)


def user_data_dir_display() -> str:
    """UI 显示用:返回 %APPDATA%\\AutoFarmStation(永远不展示真实用户名)."""
    return f"{PLACEHOLDER_APPDATA}\\{'AutoFarmStation'}"


def get_username() -> str:
    """获取当前用户名(返回占位符以避免日志/UI 暴露)."""
    _init()
    return PLACEHOLDER_USERNAME if _username else PLACEHOLDER_USERNAME