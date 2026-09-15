"""开发探针:用 PrintWindow 抓指定窗口的内容(不受遮挡/置顶影响).

用法:

    python scripts/probe_printwindow.py [类名] [窗口标题] [输出文件] [底条高度]

默认抓 SDL_app 'Steam' 的底部 90px 状态条。
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

_user32.FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
_user32.FindWindowW.restype = wintypes.HWND
_user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
_user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
_user32.GetWindowDC.argtypes = [wintypes.HWND]
_user32.GetWindowDC.restype = wintypes.HDC
_user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
_user32.ReleaseDC.restype = ctypes.c_int
_user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, ctypes.c_uint]
_user32.PrintWindow.restype = wintypes.BOOL
_gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
_gdi32.CreateCompatibleDC.restype = wintypes.HDC
_gdi32.CreateDIBSection.argtypes = [
    wintypes.HDC, ctypes.c_void_p, ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p),
    wintypes.HANDLE, ctypes.c_uint,
]
_gdi32.CreateDIBSection.restype = wintypes.HBITMAP
_gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
_gdi32.SelectObject.restype = wintypes.HGDIOBJ
_gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
_gdi32.DeleteObject.restype = wintypes.BOOL
_gdi32.DeleteDC.argtypes = [wintypes.HDC]
_gdi32.DeleteDC.restype = wintypes.BOOL


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", ctypes.c_long),
        ("biHeight", ctypes.c_long),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", ctypes.c_long),
        ("biYPelsPerMeter", ctypes.c_long),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER)]


DIB_RGB_COLORS = 0
BI_RGB = 0
PW_RENDERFULLCONTENT = 0x00000002


def grab(hwnd: int) -> bytes | None:
    """PrintWindow 整窗,返回 BGRA 原始位(底朝上)."""
    rc = wintypes.RECT()
    _user32.GetClientRect(hwnd, ctypes.byref(rc))
    w, h = rc.right - rc.left, rc.bottom - rc.top
    if w <= 0 or h <= 0:
        return None
    hdc_win = _user32.GetWindowDC(hwnd)
    hdc_mem = _gdi32.CreateCompatibleDC(hdc_win)
    bmi = BITMAPINFO()
    bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.bmiHeader.biWidth = w
    bmi.bmiHeader.biHeight = -h  # top-down
    bmi.bmiHeader.biPlanes = 1
    bmi.bmiHeader.biBitCount = 32
    bmi.bmiHeader.biCompression = BI_RGB
    bits = ctypes.c_void_p()
    hbmp = _gdi32.CreateDIBSection(hdc_mem, ctypes.byref(bmi), DIB_RGB_COLORS,
                                   ctypes.byref(bits), None, 0)
    data: bytes | None = None
    if hbmp and bits.value:
        old = _gdi32.SelectObject(hdc_mem, wintypes.HGDIOBJ(hbmp))
        ok = _user32.PrintWindow(hwnd, hdc_mem, PW_RENDERFULLCONTENT)
        if ok:
            data = ctypes.string_at(bits.value, w * h * 4)
        _gdi32.SelectObject(hdc_mem, old)
    if hbmp:
        _gdi32.DeleteObject(wintypes.HGDIOBJ(hbmp))
    _gdi32.DeleteDC(hdc_mem)
    _user32.ReleaseDC(hwnd, hdc_win)
    return data, w, h  # type: ignore[return-value]


def main() -> int:
    cls = sys.argv[1] if len(sys.argv) > 1 else "SDL_app"
    title = sys.argv[2] if len(sys.argv) > 2 else "Steam"
    out = sys.argv[3] if len(sys.argv) > 3 else r"C:\Users\zlwzk\AppData\Local\Temp\afs_pw.png"
    bottom = int(sys.argv[4]) if len(sys.argv) > 4 else 90

    hwnd = int(_user32.FindWindowW(cls, title) or 0)
    if not hwnd:
        print("window not found")
        return 1
    got = grab(hwnd)
    if not got or not got[0]:
        print("PrintWindow failed")
        return 1
    data, w, h = got
    # 取底部 bottom 像素条,转成 top-down 索引
    row = w * 4
    band = data[row * (h - bottom):]
    from PySide6.QtGui import QImage
    img = QImage(band, w, bottom, row, QImage.Format.Format_ARGB32)
    if not img.save(out, "PNG"):
        print("save failed")
        return 1
    print(f"saved {out}  client={w}x{h} band={bottom}px")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
