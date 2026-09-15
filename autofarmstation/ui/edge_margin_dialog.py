"""边距 / 缩放对话框(v1.6.1).

每窗口独立设置聚焦时的上 / 下 / 左 / 右像素边距,以及画面缩放 50%~150%。
默认全部 0 + 100%,与 v1.6.0 完全一致(不破坏老设置)。
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)


@dataclass
class EdgeMargin:
    """边距 + 缩放数值对象(便于配置序列化和 dialog 之间传值)."""

    left: int = 0
    top: int = 0
    right: int = 0
    bottom: int = 0
    scale: float = 1.0


class EdgeMarginDialog(QDialog):
    """聚焦时的边距 / 缩放对话框."""

    def __init__(
        self,
        parent: QWidget | None,
        *,
        title: str,
        initial: EdgeMargin,
        max_edge_px: int = 2000,
        info_text: str = "",
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(360)

        v = QVBoxLayout(self)
        if info_text:
            v.addWidget(QLabel(info_text))

        form = QFormLayout()
        # 4 个边距
        self._left = self._mk_spin(0, max_edge_px, initial.left)
        self._top = self._mk_spin(0, max_edge_px, initial.top)
        self._right = self._mk_spin(0, max_edge_px, initial.right)
        self._bottom = self._mk_spin(0, max_edge_px, initial.bottom)

        form.addRow("左边距(px):", self._left)
        form.addRow("上边距(px):", self._top)
        form.addRow("右边距(px):", self._right)
        form.addRow("下边距(px):", self._bottom)

        # 缩放
        self._scale = QDoubleSpinBox()
        self._scale.setRange(0.5, 1.5)
        self._scale.setSingleStep(0.05)
        self._scale.setDecimals(2)
        self._scale.setValue(initial.scale)
        form.addRow("画面缩放:", self._scale)

        v.addLayout(form)

        # 一键还原
        from PySide6.QtWidgets import QPushButton

        reset_row = QHBoxLayout()
        reset_btn = QPushButton("全部归零")
        reset_btn.setToolTip("把 4 个边距都设成 0、缩放设回 100%,与未设置时完全一致")
        reset_btn.clicked.connect(self._on_reset)
        reset_row.addWidget(reset_btn)
        reset_row.addStretch(1)
        v.addLayout(reset_row)

        # 按钮
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

    def _mk_spin(self, lo: int, hi: int, value: int) -> QSpinBox:
        s = QSpinBox()
        s.setRange(lo, hi)
        s.setSingleStep(5)
        s.setSuffix(" px")
        s.setValue(int(value))
        return s

    def _on_reset(self) -> None:
        self._left.setValue(0)
        self._top.setValue(0)
        self._right.setValue(0)
        self._bottom.setValue(0)
        self._scale.setValue(1.0)

    def result_value(self) -> EdgeMargin:
        return EdgeMargin(
            left=self._left.value(),
            top=self._top.value(),
            right=self._right.value(),
            bottom=self._bottom.value(),
            scale=round(self._scale.value(), 2),
        )