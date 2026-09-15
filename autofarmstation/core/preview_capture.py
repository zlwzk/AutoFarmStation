"""窗口截图与预览捕获.

优先用 GDI 直接「打印」窗口客户区(不要求窗口在最前、不被遮挡):
    PrintWindow(PW_RENDERFULLCONTENT) → 支持 DWM/GPU 渲染的游戏窗口(Win8.1+)
    BitBlt(windowDC)                  → 退路,窗口可见即可
    mss 屏幕区域(客户区坐标)          → 最后手段

为什么不用 mss 当主力:它截的是屏幕区域,窗口被遮挡/最小化时拿不到内容,
多开挂机时窗口经常互相覆盖 —— 这正是旧版预览「无图像」的根因之一。

多开优化:
- 相位错峰(phase):各窗口的截图时刻错开,避免同一瞬间 N 个线程同时抢 GDI
- 失败退避:连续失败自动拉长间隔(最长 4s),成功后立即恢复,防止空转烧 CPU
"""

from __future__ import annotations

import ctypes
import threading
import time
from ctypes import wintypes
from dataclasses import dataclass
from typing import Callable

from . import window_finder as wf

# === GDI 常量 ===
PW_RENDERFULLCONTENT = 0x00000002  # Win8.1+,包含 DWM 合成的 GPU 内容
SRCCOPY = 0x00CC0020
CAPTUREBLT = 0x40000000
BI_RGB = 0
DIB_RGB_COLORS = 0

# === ctypes 原型 ===
_user32 = wf._user32
_gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)  # type: ignore[attr-defined]

_user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
_user32.PrintWindow.restype = wintypes.BOOL
_user32.GetWindowDC.argtypes = [wintypes.HWND]
_user32.GetWindowDC.restype = wintypes.HDC
_user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
_user32.ReleaseDC.restype = ctypes.c_int
_gdi32.BitBlt.argtypes = [
    wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.DWORD,
]
_gdi32.BitBlt.restype = wintypes.BOOL

_gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
_gdi32.CreateCompatibleDC.restype = wintypes.HDC
_gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
_gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
_gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
_gdi32.SelectObject.restype = wintypes.HGDIOBJ
_gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
_gdi32.DeleteObject.restype = wintypes.BOOL
_gdi32.DeleteDC.argtypes = [wintypes.HDC]
_gdi32.DeleteDC.restype = wintypes.BOOL
_gdi32.GetDIBits.argtypes = [
    wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT,
    ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT,
]
_gdi32.GetDIBits.restype = ctypes.c_int


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [
        ("bmiHeader", BITMAPINFOHEADER),
        ("bmiColors", wintypes.DWORD * 3),
    ]


def capture_gdi(hwnd: int) -> tuple[int, int, bytes] | None:
    """GDI 截取窗口**客户区**,返回 (w, h, BGRA bytes) 或 None.

    - PrintWindow 不要求窗口在屏幕最前,被遮挡也能截到
    - 窗口最小化时 GDI 不渲染,返回 None
    - 32bpp 的第 4 字节(alpha)不可靠,显示时用 Format_BGRX8888 忽略它
    """
    if not wf._is_window(hwnd):
        return None
    info = wf.get_window_info(hwnd)
    if info is None or info.is_minimized:
        return None

    rect = wintypes.RECT(0, 0, 0, 0)
    if not _user32.GetClientRect(hwnd, ctypes.byref(rect)):
        return None
    w = rect.right - rect.left
    h = rect.bottom - rect.top
    if w <= 4 or h <= 4:
        return None

    hdc_window = _user32.GetWindowDC(hwnd)
    if not hdc_window:
        return None
    try:
        mem = _gdi32.CreateCompatibleDC(hdc_window)
        if not mem:
            return None
        try:
            bmp = _gdi32.CreateCompatibleBitmap(hdc_window, w, h)
            if not bmp:
                return None
            old = _gdi32.SelectObject(mem, bmp)
            try:
                ok = bool(_user32.PrintWindow(hwnd, mem, PW_RENDERFULLCONTENT))
                if not ok:
                    ok = bool(_user32.PrintWindow(hwnd, mem, 0))
                if not ok:
                    ok = bool(_gdi32.BitBlt(
                        mem, 0, 0, w, h, hdc_window, 0, 0, SRCCOPY | CAPTUREBLT))
                if not ok:
                    return None

                bi = BITMAPINFO()
                bi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
                bi.bmiHeader.biWidth = w
                bi.bmiHeader.biHeight = -h  # 负值 = 自上而下行序
                bi.bmiHeader.biPlanes = 1
                bi.bmiHeader.biBitCount = 32
                bi.bmiHeader.biCompression = BI_RGB

                buf = ctypes.create_string_buffer(w * h * 4)
                got = _gdi32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bi), DIB_RGB_COLORS)
                if got != h:
                    return None
                return (w, h, buf.raw)
            finally:
                _gdi32.SelectObject(mem, old)
                _gdi32.DeleteObject(bmp)
        finally:
            _gdi32.DeleteDC(mem)
    finally:
        _user32.ReleaseDC(hwnd, hdc_window)


@dataclass
class PreviewFrame:
    """一帧截图."""

    hwnd: int
    width: int
    height: int
    bytes_per_line: int
    pixels: bytes  # BGRA / BGRX 原始字节
    captured_at: float

    @property
    def age_ms(self) -> float:
        return (time.time() - self.captured_at) * 1000.0


class PreviewCapture:
    """针对单个 hwnd 的截图封装.

    - 内部线程按 fps 截图(GDI 优先 / mss 兜底)
    - phase:初始相位(秒),多开时错峰
    - 连续失败自动退避降频,成功后恢复
    """

    def __init__(
        self,
        hwnd: int,
        *,
        fps: float = 1.0,
        phase: float = 0.0,
        logger=None,
    ) -> None:
        self.hwnd = int(hwnd)
        self._fps = max(0.1, min(30.0, fps))
        self._phase = max(0.0, min(5.0, phase))
        self._log = logger
        self._lock = threading.Lock()
        self._latest: PreviewFrame | None = None
        self._stop_ev = threading.Event()
        self._thread: threading.Thread | None = None
        self._mss = None  # 延迟初始化(兜底路径)
        self.on_frame: Callable[[PreviewFrame], None] | None = None

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def latest(self) -> PreviewFrame | None:
        with self._lock:
            return self._latest

    def set_fps(self, fps: float) -> None:
        self._fps = max(0.1, min(30.0, fps))

    def start(self) -> None:
        if self.is_running:
            return
        self._stop_ev.clear()
        self._thread = threading.Thread(
            target=self._loop, name=f"PreviewCapture-{self.hwnd}", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_ev.set()
        if self._thread:
            self._thread.join(timeout=1.5)
        self._thread = None

    def capture_once(self) -> PreviewFrame | None:
        """主动截一帧(忽略 FPS 节流)."""
        return self._capture()

    # --- 内部 ---
    def _loop(self) -> None:
        if self._phase > 0:
            self._stop_ev.wait(self._phase)
        fails = 0
        while not self._stop_ev.is_set():
            t0 = time.time()
            frame = None
            try:
                frame = self._capture()
            except Exception as e:  # noqa: BLE001
                if self._log:
                    self._log.warning("截图异常 (hwnd=%s): %s", self.hwnd, e)
            if frame is not None:
                fails = 0
                with self._lock:
                    self._latest = frame
                if self.on_frame:
                    try:
                        self.on_frame(frame)
                    except Exception:
                        pass
            else:
                fails += 1
            # 目标周期;失败时指数退避(最长 4s),成功即恢复
            period = 1.0 / self._fps
            if fails:
                period = min(period * (1.0 + min(fails, 6)), 4.0)
            elapsed = time.time() - t0
            self._stop_ev.wait(max(0.03, period - elapsed))

    def _capture_gdi_frame(self) -> PreviewFrame | None:
        res = capture_gdi(self.hwnd)
        if res is None:
            return None
        w, h, pixels = res
        return PreviewFrame(
            hwnd=self.hwnd, width=w, height=h,
            bytes_per_line=w * 4, pixels=pixels, captured_at=time.time(),
        )

    def _ensure_mss(self) -> bool:
        if self._mss is not None:
            return True
        try:
            import mss  # type: ignore
            self._mss = mss.mss()
            return True
        except Exception:
            self._mss = None
            return False

    def _capture_mss_frame(self) -> PreviewFrame | None:
        """兜底:mss 截客户区屏幕坐标(窗口被遮挡时会截到遮挡物)."""
        if not self._ensure_mss():
            return None
        origin = wf.client_origin(self.hwnd)
        cw, ch = wf.client_size(self.hwnd)
        if cw <= 4 or ch <= 4:
            return None
        try:
            raw = self._mss.grab(
                {"left": origin[0], "top": origin[1], "width": cw, "height": ch})
        except Exception:
            return None
        pixels = bytes(raw.bgra) if hasattr(raw, "bgra") else bytes(raw.rgb)
        width, height = raw.size
        return PreviewFrame(
            hwnd=self.hwnd, width=width, height=height,
            bytes_per_line=width * 4, pixels=pixels, captured_at=time.time(),
        )

    def _capture(self) -> PreviewFrame | None:
        info = wf.get_window_info(self.hwnd)
        if info is None or not info.visible or info.is_minimized:
            return None
        if info.width < 8 or info.height < 8:
            return None
        frame = self._capture_gdi_frame()
        if frame is not None:
            return frame
        return self._capture_mss_frame()
