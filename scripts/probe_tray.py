"""开发探针:摸清系统托盘(通知区域)的窗口结构.

不同 Windows 版本里托盘图标的宿主控件不一样(Win10 是 ToolbarWindow32,
Win11 走 XAML 岛),这个脚本把相关窗口树打印出来,方便确认自动化该怎么定位图标。

用法:

    python scripts/probe_tray.py
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_user32 = ctypes.WinDLL("user32", use_last_error=True)

_user32.FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
_user32.FindWindowW.restype = wintypes.HWND
_user32.FindWindowExW.argtypes = [
    wintypes.HWND, wintypes.HWND, ctypes.c_wchar_p, ctypes.c_wchar_p
]
_user32.FindWindowExW.restype = wintypes.HWND
_user32.GetClassNameW.argtypes = [wintypes.HWND, ctypes.c_wchar_p, ctypes.c_int]
_user32.GetClassNameW.restype = ctypes.c_int
_user32.GetWindowTextW.argtypes = [wintypes.HWND, ctypes.c_wchar_p, ctypes.c_int]
_user32.GetWindowTextW.restype = ctypes.c_int
_user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
_user32.GetWindowRect.restype = wintypes.BOOL
_user32.IsWindowVisible.argtypes = [wintypes.HWND]
_user32.IsWindowVisible.restype = wintypes.BOOL
_user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
_user32.GetWindowThreadProcessId.restype = wintypes.DWORD

WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
_user32.EnumChildWindows.argtypes = [wintypes.HWND, WNDENUMPROC, wintypes.LPARAM]
_user32.EnumChildWindows.restype = wintypes.BOOL


def cls_of(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    _user32.GetClassNameW(hwnd, buf, 255)
    return buf.value


def title_of(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(512)
    _user32.GetWindowTextW(hwnd, buf, 511)
    return buf.value


def rect_of(hwnd: int) -> tuple[int, int, int, int]:
    r = wintypes.RECT()
    _user32.GetWindowRect(hwnd, ctypes.byref(r))
    return (r.left, r.top, r.right, r.bottom)


def pid_of(hwnd: int) -> int:
    p = wintypes.DWORD(0)
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
    return int(p.value)


def dump_tree(root: int, max_depth: int = 5) -> None:
    def walk(hwnd: int, depth: int) -> None:
        kids: list[int] = []

        def cb(h: int, _lp: int) -> bool:
            kids.append(h)
            return True

        _user32.EnumChildWindows(hwnd, WNDENUMPROC(cb), 0)
        for h in kids:
            if depth > max_depth:
                return
            vis = "V" if _user32.IsWindowVisible(h) else " "
            print(
                f"{'  ' * depth}{vis} {cls_of(h)!r} pid={pid_of(h)} "
                f"rect={rect_of(h)} title={title_of(h)!r}"
            )
            walk(h, depth + 1)

    print(f"--- tree of {cls_of(root)!r} hwnd={root} rect={rect_of(root)} pid={pid_of(root)}")
    walk(root, 1)


def main() -> int:
    print("=== top-level candidates ===")
    for name in (
        "Shell_TrayWnd",
        "Shell_SecondaryTrayWnd",
        "NotifyIconOverflowWindow",
        "TopLevelWindowForOverflowXamlIsland",
    ):
        h = int(_user32.FindWindowW(name, None) or 0)
        print(f"  {name!r:46s} -> {h}  rect={rect_of(h) if h else None}")

    tray = int(_user32.FindWindowW("Shell_TrayWnd", None) or 0)
    if tray:
        dump_tree(tray, max_depth=5)

    overflow = int(_user32.FindWindowW("NotifyIconOverflowWindow", None) or 0)
    if overflow:
        dump_tree(overflow, max_depth=5)

    print("=== every ToolbarWindow32 in the system ===")

    def cb(h: int, _lp: int) -> bool:
        if cls_of(h) == "ToolbarWindow32":
            print(f"  hwnd={h} pid={pid_of(h)} rect={rect_of(h)} vis={_user32.IsWindowVisible(h)}")
        return True

    _user32.EnumWindows(WNDENUMPROC(cb), 0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
