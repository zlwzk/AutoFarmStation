"""开发探针:验证「键盘驱动托盘菜单」是否可行(Win11 托盘没有 ToolbarWindow32).

思路:
    Win+B 聚焦系统托盘 -> ←/→ 切换图标 -> Shift+F10 打开该图标右键菜单
    -> 读出菜单窗口(#32768)的菜单项文本 -> Esc 关闭

用法:

    python scripts/probe_steam_menu.py [步数]
"""

from __future__ import annotations

import ctypes
import sys
import time
from ctypes import wintypes
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_user32 = ctypes.WinDLL("user32", use_last_error=True)

# --- 原型 ---
_user32.FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
_user32.FindWindowW.restype = wintypes.HWND
_user32.GetClassNameW.argtypes = [wintypes.HWND, ctypes.c_wchar_p, ctypes.c_int]
_user32.GetWindowTextW.argtypes = [wintypes.HWND, ctypes.c_wchar_p, ctypes.c_int]
_user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
_user32.IsWindowVisible.argtypes = [wintypes.HWND]
_user32.IsWindowVisible.restype = wintypes.BOOL
_user32.GetForegroundWindow.restype = wintypes.HWND
_user32.SendMessageW.argtypes = [wintypes.HWND, ctypes.c_uint, wintypes.WPARAM, wintypes.LPARAM]
_user32.SendMessageW.restype = wintypes.LPARAM
_user32.GetMenuItemCount.argtypes = [wintypes.HMENU]
_user32.GetMenuItemCount.restype = ctypes.c_int
_user32.GetMenuStringW.argtypes = [
    wintypes.HMENU, ctypes.c_uint, ctypes.c_wchar_p, ctypes.c_int, ctypes.c_uint
]
_user32.GetMenuStringW.restype = ctypes.c_int
_user32.GetSubMenu.argtypes = [wintypes.HMENU, ctypes.c_int]
_user32.GetSubMenu.restype = wintypes.HMENU
_user32.GetMenuItemID.argtypes = [wintypes.HMENU, ctypes.c_int]
_user32.GetMenuItemID.restype = ctypes.c_uint
_user32.GetMenuItemInfoW.argtypes = [wintypes.HMENU, ctypes.c_uint, wintypes.BOOL, ctypes.c_void_p]
_user32.GetMenuItemInfoW.restype = wintypes.BOOL
_user32.GetMenuItemRect.argtypes = [
    wintypes.HWND, wintypes.HMENU, ctypes.c_uint, ctypes.POINTER(wintypes.RECT)
]
_user32.GetMenuItemRect.restype = wintypes.BOOL

WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
_user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
_user32.EnumWindows.restype = wintypes.BOOL

MN_GETHMENU = 0x01E1

# --- 键盘输入(SendInput) ---
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_EXTENDEDKEY = 0x0001
VK_LWIN = 0x5B
VK_B = 0x42
VK_LEFT = 0x25
VK_RIGHT = 0x27
VK_APPS = 0x5D
VK_F10 = 0x79
VK_SHIFT = 0x10
VK_ESCAPE = 0x1B


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class INPUTUNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", INPUTUNION)]


def _key(vk: int, up: bool = False) -> None:
    inp = INPUT()
    inp.type = INPUT_KEYBOARD
    inp.u.ki.wVk = vk
    inp.u.ki.wScan = 0
    inp.u.ki.dwFlags = KEYEVENTF_KEYUP if up else 0
    inp.u.ki.time = 0
    inp.u.ki.dwExtraInfo = None
    _user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))


def press(*vks: int) -> None:
    for vk in vks:
        _key(vk, False)
    for vk in reversed(vks):
        _key(vk, True)
    time.sleep(0.12)


def find_menu_windows() -> list[int]:
    out: list[int] = []

    def cb(h: int, _lp: int) -> bool:
        buf = ctypes.create_unicode_buffer(64)
        _user32.GetClassNameW(h, buf, 63)
        if buf.value == "#32768" and _user32.IsWindowVisible(h):
            out.append(h)
        return True

    _user32.EnumWindows(WNDENUMPROC(cb), 0)
    return out


def menu_hmenu(menu_hwnd: int) -> int:
    return int(_user32.SendMessageW(menu_hwnd, MN_GETHMENU, 0, 0) or 0)


def dump_menu(hmenu: int, depth: int = 0, limit: int = 30) -> None:
    n = _user32.GetMenuItemCount(wintypes.HMENU(hmenu))
    for i in range(min(n, limit)):
        buf = ctypes.create_unicode_buffer(256)
        _user32.GetMenuStringW(wintypes.HMENU(hmenu), i, buf, 255, 0x00000400)  # MF_BYPOSITION
        item_id = int(_user32.GetMenuItemID(wintypes.HMENU(hmenu), i))
        sub = int(_user32.GetSubMenu(wintypes.HMENU(hmenu), i) or 0)
        r = wintypes.RECT()
        ok = _user32.GetMenuItemRect(0, wintypes.HMENU(hmenu), i, ctypes.byref(r))
        print(
            f"{'  ' * depth}  [{i}] id={item_id:<6d} sub={'Y' if sub else '-'} "
            f"rect=({r.left},{r.top},{r.right},{r.bottom}) ok={ok} text={buf.value!r}"
        )
        if sub:
            dump_menu(sub, depth + 1, limit)


def pid_of(hwnd: int) -> int:
    p = wintypes.DWORD(0)
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
    return int(p.value)


def main() -> int:
    steps = int(sys.argv[1]) if len(sys.argv) > 1 else 6

    print("[probe] Win+B 聚焦托盘 ...")
    press(VK_LWIN, VK_B)
    time.sleep(0.5)

    for step in range(steps):
        print(f"\n=== step {step}: Shift+F10 ===")
        press(VK_SHIFT, VK_F10)
        time.sleep(0.6)
        menus = find_menu_windows()
        if not menus:
            print("  (没有菜单弹出,改为试 Menu 键)")
            press(VK_APPS)
            time.sleep(0.6)
            menus = find_menu_windows()
        if not menus:
            print("  仍然没有菜单 -> 该位置可能不是图标或键盘菜单不生效")
        for m in menus:
            print(f"  menu hwnd={m} pid={pid_of(m)}")
            hm = menu_hmenu(m)
            print(f"    hmenu={hm}")
            if hm:
                dump_menu(hm)
        press(VK_ESCAPE)
        time.sleep(0.3)
        press(VK_ESCAPE)
        time.sleep(0.2)
        press(VK_RIGHT)
        time.sleep(0.2)

    press(VK_ESCAPE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
