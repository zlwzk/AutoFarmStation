"""AutoFarmStation 打包入口.

PyInstaller 会把入口脚本当成顶层模块(``__name__ == "__main__"``,没有包上下文),
所以不能在包内的 ``__main__.py`` 里用相对导入。这里从仓库根目录做绝对导入,
既能被 PyInstaller 正确分析,也能直接 ``python main.py`` 运行。
"""

from __future__ import annotations

import sys

from autofarmstation import __version__
from autofarmstation.app import run


def main() -> int:
    """程序主入口."""
    return run(sys.argv, __version__)


if __name__ == "__main__":
    raise SystemExit(main())
