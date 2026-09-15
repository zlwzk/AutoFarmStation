"""开发探针:验证 steam://friends/status/<state> 是否真的能切换 Steam 在线状态.

做法:触发 URL 前后对比 Steam 日志/配置文件的 mtime+大小,并扫描日志里是否出现
状态相关的关键字。

用法:

    python scripts/probe_steam_url.py invisible
"""

from __future__ import annotations

import os
import re
import sys
import time

STEAM_DIR = r"d:\steam"
WATCH_DIRS = [os.path.join(STEAM_DIR, "logs"), os.path.join(STEAM_DIR, "config")]


def snapshot() -> dict[str, tuple[float, int]]:
    snap: dict[str, tuple[float, int]] = {}
    for d in WATCH_DIRS:
        if not os.path.isdir(d):
            continue
        for name in os.listdir(d):
            p = os.path.join(d, name)
            try:
                st = os.stat(p)
                snap[p] = (st.st_mtime, st.st_size)
            except OSError:
                pass
    return snap


def main() -> int:
    state = sys.argv[1] if len(sys.argv) > 1 else "invisible"
    url = f"steam://friends/status/{state}"

    before = snapshot()
    print(f"[probe] ShellExecute {url!r}")
    os.startfile(url)  # noqa: S606
    time.sleep(6.0)
    after = snapshot()

    print("\n=== 发生变化的文件 ===")
    changed = [p for p in after if p not in before or after[p] != before.get(p)]
    for p in sorted(changed):
        print(f"  {os.path.basename(p)}  {before.get(p)} -> {after[p]}")
    if not changed:
        print("  (无)")

    print("\n=== 日志里出现状态关键字 ===")
    pat = re.compile(r"invisible|persona|PersonaState|friend.?status|friends/status", re.I)
    for dirpath in WATCH_DIRS:
        if not os.path.isdir(dirpath):
            continue
        for name in sorted(os.listdir(dirpath)):
            if not name.endswith((".txt", ".log", ".vdf")):
                continue
            p = os.path.join(dirpath, name)
            if p not in changed:
                continue
            try:
                with open(p, encoding="utf-8", errors="replace") as fh:
                    lines = fh.readlines()
            except OSError:
                continue
            for ln in lines[-80:]:
                if pat.search(ln):
                    print(f"  [{name}] {ln.strip()[:200]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
