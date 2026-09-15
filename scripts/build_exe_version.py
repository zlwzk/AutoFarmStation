"""生成 PyInstaller 的 Windows 版本资源文件(exe 属性 / FileVersion).

为什么需要它:
    单文件 exe 里的 Python 模块被塞进压缩的 ``PYZ`` 归档,版本号字符串在 exe 里
    并**不是**明文,所以「在 exe 二进制里 grep 版本号」这种校验一定会误报。
    改用 Windows 原生版本资源后:
        (Get-Item dist\\AutoFarmStation.exe).VersionInfo.FileVersion  →  1.2.0.0
    既能被构建脚本可靠校验,也让用户在「右键 → 属性 → 详细信息」里看得到版本。

用法(由 scripts\\build.ps1 调用):
    python scripts\\build_exe_version.py --out build\\version_info.txt
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from autofarmstation import (  # noqa: E402
    __app_name__,
    __app_name_cn__,
    __author_handle__,
    __version__,
)


def version_quad(ver: str) -> tuple[int, int, int, int]:
    """'1.2.0' → (1, 2, 0, 0);非法段按 0 处理,永远返回 4 段."""
    parts: list[int] = []
    for seg in str(ver).split("."):
        digits = "".join(ch for ch in seg if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    while len(parts) < 4:
        parts.append(0)
    return parts[0], parts[1], parts[2], parts[3]


# PyInstaller 会用 eval() 读取这个文件(utf-8),所以下面就是一个 Python 表达式
_TEMPLATE = """\
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={quad},
    prodvers={quad},
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(
        '080404b0',
        [
          StringStruct('CompanyName', '{author}'),
          StringStruct('FileDescription', '{desc}'),
          StringStruct('FileVersion', '{version}'),
          StringStruct('InternalName', '{name}'),
          StringStruct('LegalCopyright', 'MIT License'),
          StringStruct('OriginalFilename', '{name}.exe'),
          StringStruct('ProductName', '{name}'),
          StringStruct('ProductVersion', '{version}')
        ]
      )
    ]),
    VarFileInfo([VarStruct('Translation', [2052, 1200])])
  ]
)
"""


def build_text() -> str:
    quad = version_quad(__version__)
    return _TEMPLATE.format(
        quad=quad,
        version=__version__,
        name=__app_name__,
        author=__author_handle__,
        desc=__app_name_cn__,
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="生成 exe 版本资源文件")
    ap.add_argument("--out", required=True, help="输出路径,如 build\\version_info.txt")
    args = ap.parse_args(argv)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(build_text(), encoding="utf-8")
    print(f"[version] {out} ← {__app_name__} {__version__} (quad={version_quad(__version__)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
