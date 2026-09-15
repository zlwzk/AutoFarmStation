"""AutoFarmStation 主入口."""

from __future__ import annotations

import sys

from .__init__ import __version__


def main() -> int:
    """程序主入口."""
    # 延迟导入:加快模块导入速度,也避免在某些无 GUI 场景(如自检)下报错
    from .app import run
    return run(sys.argv, __version__)


if __name__ == "__main__":
    raise SystemExit(main())