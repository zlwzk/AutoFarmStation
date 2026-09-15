"""选择要追踪的进程对话框."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QPushButton, QLineEdit, QDialogButtonBox,
)

from ..core import window_finder as wf
from ..core.window_finder import WindowInfo


class ProcessSelectorDialog(QDialog):
    """列出所有可见顶层窗口,让用户选择要添加的."""

    def __init__(self, parent: QWidget | None = None, *, title_filter: str = "") -> None:
        super().__init__(parent)
        self.setWindowTitle("选择要追踪的窗口")
        self.resize(640, 480)
        self.selected: WindowInfo | None = None

        v = QVBoxLayout(self)
        v.addWidget(QLabel("勾选需要挂机管理的窗口(可多选,点击「追踪选中」)"))
        self._filter = QLineEdit()
        self._filter.setPlaceholderText("按窗口标题筛选...")
        self._filter.textChanged.connect(self._refresh)
        v.addWidget(self._filter)

        self._list = QListWidget()
        self._list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        v.addWidget(self._list)

        bb = QDialogButtonBox()
        self._btn_ok = bb.addButton("追踪选中", QDialogButtonBox.ButtonRole.AcceptRole)
        cancel = bb.addButton("取消", QDialogButtonBox.ButtonRole.RejectRole)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

        self._refresh(title_filter)

    def _refresh(self, title_filter: str = "") -> None:
        self._list.clear()
        wins = wf.list_visible_windows(title_filter=self._filter.text() or title_filter)
        for w in wins:
            label = f"[{w.pid}] {w.title}  ({w.class_name})"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, w)
            self._list.addItem(item)

    def accept(self):
        items = self._list.selectedItems()
        if not items:
            # 允许单选(用户没多选就用当前行)
            if self._list.currentItem():
                items = [self._list.currentItem()]
            else:
                return
        # 取第一个,实际支持多选可以在 main 里循环
        self.selected = items[0].data(Qt.ItemDataRole.UserRole)
        super().accept()

    def selected_all(self) -> list[WindowInfo]:
        """获取所有选中行(支持多选)。"""
        out = []
        for it in self._list.selectedItems():
            w = it.data(Qt.ItemDataRole.UserRole)
            if isinstance(w, WindowInfo):
                out.append(w)
        return out