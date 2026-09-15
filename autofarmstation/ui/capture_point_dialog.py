"""可视化选点对话框.

弹出一个全屏透明覆盖层,用户点击目标窗口内的某个位置即可捕获坐标.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QPoint
from PySide6.QtGui import QPainter, QColor, QPen, QCursor
from PySide6.QtWidgets import (
    QDialog, QWidget, QLabel, QVBoxLayout, QPushButton, QHBoxLayout,
)

from ..core import window_finder as wf


class _OverlayWidget(QWidget):
    """覆盖在目标窗口上的透明 widget,捕获点击坐标."""

    def __init__(self, target_hwnd: int) -> None:
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool)
        self._target_hwnd = int(target_hwnd)
        self.captured: tuple[int, int] | None = None
        info = wf.get_window_info(target_hwnd)
        if info is None:
            self.close()
            return
        left, top, right, bottom = info.rect
        w, h = right - left, bottom - top
        self.setGeometry(left, top, w, h)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setWindowOpacity(0.20)
        self.activateWindow()
        self.raise_()
        self.setCursor(Qt.CursorShape.CrossCursor)

    def paintEvent(self, ev):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(0, 0, 0, 80))
        pen = QPen(QColor(74, 158, 255))
        pen.setWidth(3)
        p.setPen(pen)
        p.drawRect(self.rect().adjusted(2, 2, -3, -3))

    def mousePressEvent(self, ev):
        if ev.button() == Qt.MouseButton.LeftButton:
            # 客户区坐标 = 屏幕坐标 - 客户区原点
            ox, oy = wf.client_origin(self._target_hwnd)
            x = ev.globalPosition().toPoint().x() - ox
            y = ev.globalPosition().toPoint().y() - oy
            self.captured = (max(0, int(x)), max(0, int(y)))
            self.close()
        elif ev.button() == Qt.MouseButton.RightButton:
            self.close()

    def keyPressEvent(self, ev):
        if ev.key() == Qt.Key.Key_Escape:
            self.close()


class CapturePointDialog(QDialog):
    """选点对话框封装."""

    def __init__(self, target_hwnd: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("选择点击位置")
        self.setModal(True)
        self.captured: tuple[int, int] | None = None
        self._target_hwnd = int(target_hwnd)

        v = QVBoxLayout(self)
        lab = QLabel("游戏窗口已置顶,点击目标位置 / 右键取消 / ESC 取消")
        lab.setStyleSheet("color:#a0cfff;")
        v.addWidget(lab)
        b = QHBoxLayout()
        btn_cancel = QPushButton("取消")
        btn_cancel.clicked.connect(self.reject)
        b.addStretch(); b.addWidget(btn_cancel)
        v.addLayout(b)

    def exec(self):
        # 先把目标窗口置顶
        wf.set_foreground(self._target_hwnd)
        # 弹出覆盖层
        self._overlay = _OverlayWidget(self._target_hwnd)
        self._overlay.show()
        # 模态循环
        from PySide6.QtCore import QEventLoop
        loop = QEventLoop()
        self._overlay.destroyed.connect(loop.quit)
        loop.exec()
        self.captured = self._overlay.captured
        return self.captured is not None