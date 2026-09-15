"""开发探针:扫描任务栏托盘区域,靠「弹出菜单的归属进程」定位某个托盘图标.

Win11 的托盘是 XAML 岛,没有 ToolbarWindow32 可以枚举,所以改用:
    在 TrayNotifyWnd 范围内逐个候选点右键 -> 读 #32768 菜单的归属进程/菜单项文本
    -> 命中目标(exe 名或菜单关键词)就停 -> Esc 关闭

用法:

    python scripts/probe_tray_hit.py [exe名] [步长]
"""

from __future__ import annotations

import ctypes
import sys
import time
from ctypes import wintypes
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_user32 = ctypes.WinDLL("user32", use_last_error=True)

_user32.FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
_user32.FindWindowW.restype = wintypes.HWND
_user32.FindWindowExW.argtypes = [
    wintypes.HWND, wintypes.HWND, ctypes.c_wchar_p, ctypes.c_wchar_p
]
_user32.FindWindowExW.restype = wintypes.HWND
_user32.mouse_event.argtypes = [
    wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p
]
_user32.keybd_event.argtypes = [
    ctypes.c_ubyte, ctypes.c_ubyte, wintypes.DWORD, ctypes.c_void_p
]
_user32.GetClassNameW.argtypes = [wintypes.HWND, ctypes.c_wchar_p, ctypes.c_int]
_user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
_user32.IsWindowVisible.argtypes = [wintypes.HWND]
_user32.IsWindowVisible.restype = wintypes.BOOL
_user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
_user32.GetWindowRect.restype = wintypes.BOOL
_user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
_user32.SetCursorPos.restype = wintypes.BOOL
_user32.SendMessageW.argtypes = [wintypes.HWND, ctypes.c_uint, wintypes.WPARAM, wintypes.LPARAM]
_user32.SendMessageW.restype = wintypes.LPARAM
_user32.GetMenuItemCount.argtypes = [wintypes.HMENU]
_user32.GetMenuItemCount.restype = ctypes.c_int
_user32.GetMenuStringW.argtypes = [
    wintypes.HMENU, ctypes.c_uint, ctypes.c_wchar_p, ctypes.c_int, ctypes.c_uint
]
_user32.GetSubMenu.argtypes = [wintypes.HMENU, ctypes.c_int]
_user32.GetSubMenu.restype = wintypes.HMENU

WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
_user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
_user32.EnumWindows.restype = wintypes.BOOL

MN_GETHMENU = 0x01E1
MF_BYPOSITION = 0x00000400

MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
VK_ESCAPE = 0x1B

try:
    import psutil  # type: ignore
except Exception:  # noqa: BLE001
    psutil = None


def _mouse(flags: int) -> None:
    _user32.mouse_event(flags, 0, 0, 0, 0)


def right_click(x: int, y: int) -> None:
    _user32.SetCursorPos(int(x), int(y))
    time.sleep(0.08)
    _mouse(MOUSEEVENTF_RIGHTDOWN)
    time.sleep(0.03)
    _mouse(MOUSEEVENTF_RIGHTUP)


def left_click(x: int, y: int) -> None:
    _user32.SetCursorPos(int(x), int(y))
    time.sleep(0.08)
    _mouse(MOUSEEVENTF_LEFTDOWN)
    time.sleep(0.03)
    _mouse(MOUSEEVENTF_LEFTUP)


def press_esc(times: int = 1) -> None:
    _user32.keybd_event(VK_ESCAPE, 0, 0, 0)
    _user32.keybd_event(VK_ESCAPE, 0, 2, 0)
    time.sleep(0.12)


def list_popups() -> list[int]:
    out: list[int] = []

    def cb(h: int, _lp: int) -> bool:
        buf = ctypes.create_unicode_buffer(64)
        _user32.GetClassNameW(h, buf, 63)
        if buf.value == "#32768" and _user32.IsWindowVisible(h):
            out.append(h)
        return True

    _user32.EnumWindows(WNDENUMPROC(cb), 0)
    return out


def menu_items(menu_hwnd: int, depth: int = 0, limit: int = 14) -> list[str]:
    hmenu = int(_user32.SendMessageW(menu_hwnd, MN_GETHMENU, 0, 0) or 0)
    if not hmenu:
        return []
    out: list[str] = []
    n = _user32.GetMenuItemCount(wintypes.HMENU(hmenu))
    for i in range(min(n, limit)):
        buf = ctypes.create_unicode_buffer(256)
        _user32.GetMenuStringW(wintypes.HMENU(hmenu), i, buf, 255, MF_BYPOSITION)
        out.append(buf.value or "-")
        if depth < 1:
            sub = int(_user32.GetSubMenu(wintypes.HMENU(hmenu), i) or 0)
            if sub:
                out.extend("  > " + s for s in menu_items(int(sub), depth + 1, limit))
    return out


def pid_of(hwnd: int) -> int:
    p = wintypes.DWORD(0)
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
    return int(p.value)


def exe_of(pid: int) -> str:
    if psutil is None:
        return f"pid{pid}"
    try:
        return psutil.Process(pid).name()
    except Exception:  # noqa: BLE001
        return f"pid{pid}"


def main() -> int:
    target = (sys.argv[1] if len(sys.argv) > 1 else "steam.exe").lower()
    step = int(sys.argv[2]) if len(sys.argv) > 2 else 12

    # --- 环境自检:DPI 感知与坐标是否一致 ---
    try:
        _user32.GetSystemMetrics.argtypes = [ctypes.c_int]
        _user32.GetSystemMetrics.restype = ctypes.c_int
        sw, sh = _user32.GetSystemMetrics(0), _user32.GetSystemMetrics(1)
        _user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
        pt = wintypes.POINT()
        _user32.SetCursorPos(700, 400)
        _user32.GetCursorPos(ctypes.byref(pt))
        print(f"[env] screen={sw}x{sh}  cursor after SetCursorPos(700,400) = ({pt.x},{pt.y})")
    except Exception as e:  # noqa: BLE001
        print("[env] 自检失败:", e)

    tray = int(_user32.FindWindowW("Shell_TrayWnd", None) or 0)
    notify = int(_user32.FindWindowExW(tray, 0, "TrayNotifyWnd", None) or 0)
    if not notify:
        print("没找到 TrayNotifyWnd")
        return 1
    r = wintypes.RECT()
    _user32.GetWindowRect(notify, ctypes.byref(r))
    print(f"TrayNotifyWnd = ({r.left},{r.top})-({r.right},{r.bottom})")

    cyber_x = r.right - 4
    cyber_y = r.top + 4

    hits: list[tuple[int, int, str]] = []
    for x in range(r.left + 8, r.right - 8, step):
        y = (r.top + r.bottom) // 2
        right_click(x, y)
        time.sleep(0.45)
        popups = list_popups()
        if popups:
            for m in popups:
                p = pid_of(m)
                name = exe_of(p)
                items = menu_items(m)
                mark = "  <<< HIT" if name.lower() == target else ""
                print(f"x={x:5d} pid={p} {name} items={items[:8]}{mark}")
                if name.lower() == target:
                    hits.append((x, y, name))
        press_esc()
        time.sleep(0.15)
        press_esc()
        time.sleep(0.1)

    print("\n=== hits:", hits)

    # 顺便看看溢出面板里的图标
    print("\n=== 点 ^ 展开隐藏图标 ===")
    left_click(cyber_x, cyber_y)
    time.sleep(0.6)
    ov = int(_user32.FindWindowW("TopLevelWindowForOverflowXamlIsland", None) or 0)
    if ov:
        rr = wintypes.RECT()
        _user32.GetWindowRect(ov, ctypes.byref(rr))
        print(f"overflow flyout = ({rr.left},{rr.top})-({rr.right},{rr.bottom})")
        for x in range(rr.left + 10, rr.right - 10, 24):
            for y in range(rr.top + 10, rr.bottom - 10, 24):
                right_click(x, y)
                time.sleep(0.4)
                for m in list_popups():
                    p = pid_of(m)
                    name = exe_of(p)
                    if name.lower() == target:
                        print(f"  OVERFLOW HIT x={x} y={y} {name} {menu_items(m)[:6]}")
                    else:
                        print(f"  x={x} y={y} -> {name} {menu_items(m)[:4]}")
                press_esc()
                time.sleep(0.15)
    press_esc()
    press_esc()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
