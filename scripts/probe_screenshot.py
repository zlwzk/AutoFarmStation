"""开发探针:截屏到临时目录(用于人工/开发期核对 UI 状态).

用法:

    python scripts/probe_screenshot.py [输出文件]

默认输出到 %TEMP%\\afs_probe_shot.png(绝不写进仓库)。
"""

from __future__ import annotations

import os
import sys
import tempfile

import mss
import mss.tools


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    out = args[0] if args else os.path.join(tempfile.gettempdir(), "afs_probe_shot.png")
    region = None
    if len(args) >= 5:
        l, t, w, h = (int(v) for v in args[1:5])
        region = {"left": l, "top": t, "width": w, "height": h}
    with mss.mss() as sct:
        if region is None:
            region = sct.monitors[0]
            if "--br" in sys.argv:
                # 屏幕右下角一小条(Steam 好友栏)
                region = {
                    "left": region["left"] + region["width"] - 420,
                    "top": region["top"] + region["height"] - 150,
                    "width": 420,
                    "height": 150,
                }
        shot = sct.grab(region)
        mss.tools.to_png(shot.rgb, shot.size, output=out)
    print(out, region)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
