"""通用 UI 组件."""

from __future__ import annotations

import logging
from typing import Callable

from PySide6.QtCore import Qt, QSize, Signal
from PySide6.QtGui import QAction, QImage, QPainter, QPixmap, QColor, QIcon, QFont
from PySide6.QtWidgets import (
    QWidget, QLabel, QVBoxLayout, QHBoxLayout, QPushButton,
    QFrame, QSizePolicy, QToolButton, QLineEdit, QSpinBox, QDoubleSpinBox,
    QComboBox, QCheckBox, QGroupBox, QFormLayout, QScrollArea,
)


def make_label(text: str = "", *, bold: bool = False, color: str = "") -> QLabel:
    lab = QLabel(text)
    if bold or color:
        f = lab.font()
        if bold:
            f.setBold(True)
        lab.setFont(f)
        if color:
            lab.setStyleSheet(f"color:{color};")
    return lab


def make_button(text: str, *, on_click: Callable | None = None, primary: bool = False) -> QPushButton:
    btn = QPushButton(text)
    if on_click:
        btn.clicked.connect(on_click)
    if primary:
        btn.setProperty("primary", True)
        btn.setStyleSheet("QPushButton[primary=\"true\"] { font-weight: bold; padding: 6px 12px; }")
    return btn


def hline() -> QFrame:
    f = QFrame()
    f.setFrameShape(QFrame.Shape.HLine)
    f.setFrameShadow(QFrame.Shadow.Sunken)
    return f


def bytes_to_qimage(bgra: bytes, width: int, height: int, bpl: int) -> QImage:
    """把 BGRA 原始字节包装为 QImage."""
    from PySide6.QtGui import QImage as _QI
    img = _QI(bgra, width, height, bpl, _QI.Format.Format_BGR888)
    return img


def bgra_to_pixmap(bgra: bytes, width: int, height: int, bpl: int) -> QPixmap:
    img = bytes_to_qimage(bgra, width, height, bpl)
    return QPixmap.fromImage(img)


class ClickableFrame(QFrame):
    """可点击的容器,带 hover 效果."""

    clicked = Signal()
    double_clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self._hover = False

    def enterEvent(self, ev):
        self._hover = True
        self.setStyleSheet("QFrame { border: 2px solid #4a9eff; }")
        super().enterEvent(ev)

    def leaveEvent(self, ev):
        self._hover = False
        self.setStyleSheet("QFrame { border: 1px solid #555; }")
        super().leaveEvent(ev)

    def mousePressEvent(self, ev):
        if ev.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(ev)

    def mouseDoubleClickEvent(self, ev):
        if ev.button() == Qt.MouseButton.LeftButton:
            self.double_clicked.emit()
        super().mouseDoubleClickEvent(ev)


class NumberField(QWidget):
    """数字输入 + 微调按钮."""

    valueChanged = Signal(object)  # float,但用 object 避免 int/float 类型不匹配

    def __init__(
        self,
        *,
        minimum: float = 0,
        maximum: float = 99999,
        step: float = 1,
        suffix: str = "",
        decimals: int = 0,
        value: float = 0,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._spin = QDoubleSpinBox() if decimals > 0 else QSpinBox()
        self._spin.setRange(minimum, maximum)
        if decimals > 0:
            self._spin.setDecimals(decimals)
            self._spin.setSingleStep(step)
        else:
            self._spin.setSingleStep(int(step))
        if suffix:
            self._spin.setSuffix(suffix)
        self._spin.setValue(value)
        # 中转:不管 spin 发 int 还是 double,统一转 float 再 emit
        self._spin.valueChanged.connect(self._on_value_changed)
        layout.addWidget(self._spin)

    def _on_value_changed(self, v) -> None:
        self.valueChanged.emit(float(v))

    def value(self) -> float:
        return float(self._spin.value())

    def setValue(self, v: float) -> None:
        self._spin.setValue(v)


class ScrollSection(QScrollArea):
    """带标题的滚动区域."""

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self._title = title
        self._inner = QWidget()
        self._layout = QVBoxLayout(self._inner)
        self._layout.setContentsMargins(8, 8, 8, 8)
        self._layout.setSpacing(8)
        self.setWidget(self._inner)

    def addWidget(self, w: QWidget) -> None:
        self._layout.addWidget(w)

    def addLayout(self, l) -> None:
        self._layout.addLayout(l)

    def addStretch(self) -> None:
        self._layout.addStretch()


def styled_message(text: str, *, level: str = "info") -> str:
    """生成带颜色的 HTML 文本(用于 QLabel.setText)."""
    color = {
        "info": "#a0cfff",
        "warn": "#ffcb6b",
        "error": "#ff7575",
        "ok": "#9bde7e",
        "muted": "#888",
    }.get(level, "#fff")
    return f'<span style="color:{color};">{text}</span>'