"""检查更新.

读 GitHub releases/latest 与本地 __version__ 比对,返回是否有新版本.
纯标准库,无需联网库.
"""

from __future__ import annotations

import json
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Final

from ..__init__ import __app_name__, __version__, __author_handle__

# === 常量(可在自检里覆盖) ===
DEFAULT_REPO: Final[str] = f"{__author_handle__}/AutoFarmStation"
LATEST_URL: Final[str] = "https://api.github.com/repos/{repo}/releases/latest"
API_TIMEOUT: Final[float] = 5.0
USER_AGENT: Final[str] = f"{__app_name__}/{__version__} (Windows)"


@dataclass
class UpdateInfo:
    """更新检查结果."""

    has_update: bool
    latest_version: str
    current_version: str
    release_url: str
    release_notes: str
    error: str = ""


def _parse_version(v: str) -> tuple[int, ...]:
    """解析版本号,容忍 '1.2.3' / 'v1.2.3' / '1.2.3-beta1'."""
    s = v.strip()
    if s.lower().startswith("v"):
        s = s[1:]
    m = re.match(r"^(\d+(?:\.\d+)*)", s)
    if not m:
        return ()
    return tuple(int(x) for x in m.group(1).split("."))


def is_newer(latest: str, current: str) -> bool:
    """latest > current ?."""
    a, b = _parse_version(latest), _parse_version(current)
    # 长度对齐(短的补 0)
    n = max(len(a), len(b))
    a = a + (0,) * (n - len(a))
    b = b + (0,) * (n - len(b))
    return a > b


def check(repo: str = DEFAULT_REPO, current: str = __version__) -> UpdateInfo:
    """联网检查更新.失败时 has_update=False,error 填错误描述."""
    info = UpdateInfo(
        has_update=False,
        latest_version=current,
        current_version=current,
        release_url=f"https://github.com/{repo}/releases/latest",
        release_notes="",
    )
    try:
        req = urllib.request.Request(
            LATEST_URL.format(repo=repo),
            headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"},
        )
        with urllib.request.urlopen(req, timeout=API_TIMEOUT) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as e:
        info.error = f"网络错误:{e.reason}"
        return info
    except Exception as e:  # noqa: BLE001
        info.error = f"检查失败:{type(e).__name__}: {e}"
        return info

    tag = str(payload.get("tag_name") or "").strip()
    if not tag:
        info.error = "远端未发布版本"
        return info
    info.latest_version = tag
    info.release_url = str(payload.get("html_url") or info.release_url)
    info.release_notes = str(payload.get("body") or "")
    info.has_update = is_newer(tag, current)
    return info


# 避免 import 时意外触发网络
_check_on_import = False  # noqa: F841


if __name__ == "__main__":  # pragma: no cover
    print(json.dumps(check().__dict__, indent=2, ensure_ascii=False))
    sys.exit(0)