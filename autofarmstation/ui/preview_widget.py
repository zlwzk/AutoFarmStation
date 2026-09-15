"""单个窗口预览卡片."""

from __future__ import annotations

import time

from PySide6.QtCore import Qt, QSize, Signal, QTimer
from PySide6.QtGui import QImage, QPainter, QPixmap, QColor, QFont
from PySide6.QtWidgets import (
    QWidget, QLabel, QVBoxLayout, QHBoxLayout, QPushButton,
    QFrame, QSizePolicy, QCheckBox,
)

from ..core.preview_capture import PreviewCapture, PreviewFrame
from ..core import window_finder as wf
from .widgets import styled_message


class PreviewWidget(QFrame):
    """单个窗口预览卡片.

    支持勾选(用于「多窗口同步执行」):勾上的窗口会被一起施加同一套操作。
    """

    focused = Signal(int)  # hwnd
    closed = Signal(int)  # hwnd(请求从追踪列表移除)
    autoclick_toggle = Signal(int)  # hwnd
    selection_changed = Signal(int, bool)  # hwnd, 是否选中

    def __init__(self, hwnd: int, *, fps: float = 1.0, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.hwnd = int(hwnd)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setObjectName("PreviewCard")
        self.setStyleSheet(
            "#PreviewCard { border: 2px solid #3c3c3c; background:#222; }"
            "#PreviewCard:hover { border: 2px solid #4a9eff; }"
        )
        self.setMinimumSize(220, 180)
        self._capture = PreviewCapture(self.hwnd, fps=fps)
        self._capture.on_frame = self._on_frame
        self._autoclicker_running = False
        self._fps = fps
        self._status_extra = ""

        v = QVBoxLayout(self)
        v.setContentsMargins(4, 4, 4, 4)
        v.setSpacing(4)

        # 标题栏
        title_bar = QHBoxLayout()
        title_bar.setSpacing(4)
        self._chk = QCheckBox()
        self._chk.setToolTip("勾选后可与其它勾选窗口「同步执行」同一套操作")
        self._chk.toggled.connect(self._on_check_toggled)
        title_bar.addWidget(self._chk)

        self._title_lab = QLabel("加载中...")
        self._title_lab.setStyleSheet("color:white;")
        f = self._title_lab.font()
        f.setPointSize(9)
        f.setBold(True)
        self._title_lab.setFont(f)
        self._title_lab.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        title_bar.addWidget(self._title_lab)

        self._btn_focus = QPushButton("聚焦")
        self._btn_focus.setFixedWidth(48)
        self._btn_focus.clicked.connect(self._on_focus_clicked)
        title_bar.addWidget(self._btn_focus)

        self._btn_close = QPushButton("✕")
        self._btn_close.setFixedWidth(28)
        self._btn_close.setStyleSheet("QPushButton { color:#ff8888; }")
        self._btn_close.clicked.connect(lambda: self.closed.emit(self.hwnd))
        title_bar.addWidget(self._btn_close)
        v.addLayout(title_bar)

        # 预览图
        self._image_lab = QLabel()
        self._image_lab.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._image_lab.setStyleSheet("background:#000; color:#888;")
        self._image_lab.setMinimumHeight(120)
        self._image_lab.setText("(无图像)")
        self._image_lab.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        v.addWidget(self._image_lab, stretch=1)

        # 底部状态栏
        status_bar = QHBoxLayout()
        self._status_lab = QLabel("未运行")
        self._status_lab.setStyleSheet("color:#aaa; font-size:11px;")
        status_bar.addWidget(self._status_lab, stretch=1)

        self._btn_autoclick = QPushButton("连点")
        self._btn_autoclick.setCheckable(True)
        self._btn_autoclick.setFixedWidth(56)
        self._btn_autoclick.toggled.connect(self._on_autoclick_toggle)
        status_bar.addWidget(self._btn_autoclick)
        v.addLayout(status_bar)

        # 点击预览 → 窗口聚焦
        self._image_lab.mousePressEvent = self._on_image_click  # type: ignore[assignment]
        self._image_lab.mouseDoubleClickEvent = self._on_image_dblclick  # type: ignore[assignment]

        # 状态刷新定时器
        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self._refresh_status)
        self._refresh_timer.start(1000)
        self._added_at = time.time()

        self._capture.start()
        self._refresh_status()

    # --- 公共 ---
    def set_fps(self, fps: float) -> None:
        self._capture.set_fps(fps)
        self._fps = fps

    def is_selected(self) -> bool:
        return self._chk.isChecked()

    def set_selected(self, on: bool) -> None:
        self._chk.setChecked(bool(on))

    def set_autoclicker_state(self, running: bool) -> None:
        """由 main_window 在连点器状态变化时同步."""
        self._autoclicker_running = running
        if self._btn_autoclick.isChecked() != running:
            self._btn_autoclick.blockSignals(True)
            self._btn_autoclick.setChecked(running)
            self._btn_autoclick.blockSignals(False)
        self._btn_autoclick.setText("停止" if running else "连点")
        self._refresh_status()

    def set_status_extra(self, text: str) -> None:
        """预览网格统一刷新运行状态(来自 AutomationHub)."""
        self._status_extra = text or ""
        self._refresh_status()

    def stop(self) -> None:
        self._capture.stop()
        self._refresh_timer.stop()

    # --- 内部 ---
    def _on_check_toggled(self, on: bool) -> None:
        # 选中时高亮边框
        if on:
            self.setStyleSheet(
                "#PreviewCard { border: 2px solid #4a9eff; background:#222; }"
            )
        else:
            self.setStyleSheet(
                "#PreviewCard { border: 2px solid #3c3c3c; background:#222; }"
                "#PreviewCard:hover { border: 2px solid #4a9eff; }"
            )
        self.selection_changed.emit(self.hwnd, bool(on))

    def _on_frame(self, frame: PreviewFrame) -> None:
        """截图回调(子线程).用 QTimer.singleShot 切到主线程."""
        QTimer.singleShot(0, lambda: self._render_frame(frame))

    def _render_frame(self, frame: PreviewFrame) -> None:
        try:
            img = QImage(frame.pixels, frame.width, frame.height, frame.bytes_per_line,
                         QImage.Format.Format_BGR888)
            pix = QPixmap.fromImage(img)
            target = self._image_lab.size()
            scaled = pix.scaled(target, Qt.AspectRatioMode.KeepAspectRatio,
                                Qt.TransformationMode.SmoothTransformation)
            self._image_lab.setPixmap(scaled)
        except Exception:
            pass

    def _refresh_status(self) -> None:
        info = wf.get_window_info(self.hwnd)
        if info is None or not info.visible:
            self._title_lab.setText("[已失效]")
            self._title_lab.setStyleSheet("color:#888;")
            self._status_lab.setText(styled_message("窗口已失效", level="warn"))
            self._image_lab.setText("(窗口已失效)")
            self._image_lab.setPixmap(QPixmap())
            return
        title = info.title
        if len(title) > 36:
            title = title[:33] + "..."
        self._title_lab.setText(f"[{info.pid}] {title}")
        self._title_lab.setStyleSheet("color:white;")
        runtime = time.time() - self._added_at
        h = int(runtime // 3600)
        m = int((runtime % 3600) // 60)
        s = int(runtime % 60)
        msg = f"已追踪 {h:02d}:{m:02d}:{s:02d} · {self._fps:.1f}fps"
        if self._status_extra:
            msg = f"● {self._status_extra} · " + msg
        self._status_lab.setText(msg)

    def _on_image_click(self, ev) -> None:
        if ev.button() == Qt.MouseButton.LeftButton:
            self._do_focus()

    def _on_image_dblclick(self, ev) -> None:
        if ev.button() == Qt.MouseButton.LeftButton:
            info = wf.get_window_info(self.hwnd)
            if info is None:
                return
            if info.is_minimized:
                wf.show_window(self.hwnd, wf.SW_RESTORE)
            else:
                wf.show_window(self.hwnd, wf.SW_SHOWMAXIMIZED)

    def _on_focus_clicked(self) -> None:
        self._do_focus()

    def _do_focus(self) -> None:
        wf.set_foreground(self.hwnd)
        self.focused.emit(self.hwnd)

    def _on_autoclick_toggle(self, checked: bool) -> None:
        self.autoclick_toggle.emit(self.hwnd)