"""窗口截图与预览捕获.

用 mss 截取指定 hwnd 的屏幕区域(走屏幕坐标),返回 QImage 友好的 bytes.
带 FPS 限流:同一窗口两次截图间隔 < 1/fps 时直接返回上次结果.
"""

from __future__ import annotations

import ctypes
import threading
import time
from dataclasses import dataclass
from typing import Callable

from . import window_finder as wf


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

    - 内部线程负责按 fps 限流截图
    - get_latest() 拿最新帧,UI 线程可直接显示
    """

    def __init__(
        self,
        hwnd: int,
        *,
        fps: float = 1.0,
        target_w: int = 360,
        target_h: int = 0,  # 0 = 按比例
        logger=None,
    ) -> None:
        self.hwnd = int(hwnd)
        self._fps = max(0.1, min(30.0, fps))
        self._target_w = max(32, target_w)
        self._target_h = max(0, target_h)
        self._log = logger
        self._lock = threading.Lock()
        self._latest: PreviewFrame | None = None
        self._last_capture_ts = 0.0
        self._running = False
        self._thread: threading.Thread | None = None
        self._mss = None  # 延迟初始化
        self.on_frame: Callable[[PreviewFrame], None] | None = None

    @property
    def is_running(self) -> bool:
        return self._running and self._thread is not None and self._thread.is_alive()

    @property
    def latest(self) -> PreviewFrame | None:
        with self._lock:
            return self._latest

    def set_fps(self, fps: float) -> None:
        self._fps = max(0.1, min(30.0, fps))

    def start(self) -> None:
        if self.is_running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._loop, name=f"PreviewCapture-{self.hwnd}", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)
        self._thread = None

    def capture_once(self) -> PreviewFrame | None:
        """主动截一帧(忽略 FPS 节流)."""
        return self._capture()

    # --- 内部 ---
    def _ensure_mss(self):
        if self._mss is not None:
            return
        try:
            import mss  # type: ignore
            self._mss = mss.mss()
        except Exception:
            self._mss = None
            raise

    def _loop(self) -> None:
        while self._running:
            try:
                frame = self._capture()
                if frame is not None:
                    with self._lock:
                        self._latest = frame
                    if self.on_frame:
                        try:
                            self.on_frame(frame)
                        except Exception:
                            pass
            except Exception as e:
                if self._log:
                    self._log.warning("截图失败 (hwnd=%s): %s", self.hwnd, e)
            time.sleep(max(0.05, 1.0 / self._fps))

    def _capture(self) -> PreviewFrame | None:
        info = wf.get_window_info(self.hwnd)
        if info is None or not info.visible or info.is_minimized:
            return None
        if info.width < 8 or info.height < 8:
            return None

        try:
            self._ensure_mss()
        except Exception:
            return None
        if self._mss is None:
            return None

        left, top, right, bottom = info.rect
        if right <= left or bottom <= top:
            return None

        try:
            raw = self._mss.grab({"left": left, "top": top, "width": right - left, "height": bottom - top})
        except Exception:
            return None
        # raw 是 BGRA 4 字节/像素
        pixels = bytes(raw.bgra) if hasattr(raw, "bgra") else bytes(raw.rgb)  # 兜底
        width, height = raw.size
        bpl = width * 4
        frame = PreviewFrame(
            hwnd=self.hwnd,
            width=width,
            height=height,
            bytes_per_line=bpl,
            pixels=pixels,
            captured_at=time.time(),
        )
        return frame