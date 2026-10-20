"""标签与分类设置对话框(v1.8.7).

一个窗口的「身份信息」拆成两级:
    * **分类**(单选)—— 对应卡片上的分类徽标 / 顶部筛选条,用来把多开窗口分组
      (如「需点击」「纯挂时长」),点筛选条可以只看某一类;
    * **标签**(多选)—— 自由命名的小标记(如「主力号」「每日」「活动」),
      卡片上以 ``#标签`` 的形式显示,同样可以按标签筛选。

之前 ``TrackedProcess.tags`` 只有数据结构、没有任何设置入口,分类也只能靠
右键标题里一个不显眼的二级菜单 —— 这里补上统一的可视化入口。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QComboBox, QLineEdit,
    QLabel, QPushButton, QDialogButtonBox, QGroupBox, QWidget, QGridLayout,
)

# 分类预设(可自由输入新分类)
DEFAULT_CATEGORIES: tuple[str, ...] = ("需点击", "纯挂时长")

# 标签快捷建议(可自由输入新标签)
DEFAULT_TAGS: tuple[str, ...] = ("主力号", "小号", "每日", "活动", "限时", "挂机")


def split_tags(text: str) -> list[str]:
    """把用户输入拆成标签列表:支持中英文逗号 / 分号 / 空格 / 顿号分隔."""
    out: list[str] = []
    buf = ""
    for ch in text or "":
        if ch in ",，;；、\n\t ":
            if buf.strip():
                out.append(buf.strip())
            buf = ""
        else:
            buf += ch
    if buf.strip():
        out.append(buf.strip())
    # 去重保序,并去掉 # 前缀
    seen: set[str] = set()
    res: list[str] = []
    for t in out:
        s = t.lstrip("#").strip()
        if s and s not in seen:
            seen.add(s)
            res.append(s)
    return res[:12]


class TagGroupDialog(QDialog):
    """设置单个窗口的分类 + 标签."""

    def __init__(
        self,
        *,
        group: str = "",
        tags: list[str] | None = None,
        known_groups: list[str] | None = None,
        known_tags: list[str] | None = None,
        window_label: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("标签与分类")
        self.setMinimumWidth(430)
        self._tags: list[str] = list(tags or [])

        v = QVBoxLayout(self)

        if window_label:
            lab = QLabel(window_label)
            lab.setWordWrap(True)
            lab.setStyleSheet("color:#9fc7ff; font-weight:bold;")
            v.addWidget(lab)

        hint = QLabel(
            "分类:单选,显示在卡片上并可点顶部筛选条过滤;\n"
            "标签:可多个,卡片上显示为 #标签,同样可筛选。"
        )
        hint.setStyleSheet("color:#999; font-size:11px;")
        hint.setWordWrap(True)
        v.addWidget(hint)

        # --- 分类 ---
        box_g = QGroupBox("分类(单选)")
        fg = QFormLayout(box_g)
        self._combo = QComboBox()
        self._combo.setEditable(True)  # 允许直接输入新分类
        self._combo.addItem("(未分类)", "")
        names: list[str] = []
        for n in list(DEFAULT_CATEGORIES) + list(known_groups or []):
            if n and n not in names:
                names.append(n)
        for n in names:
            self._combo.addItem(n, n)
        # 回填当前分类
        idx = self._combo.findData(str(group or ""))
        if idx >= 0:
            self._combo.setCurrentIndex(idx)
        elif group:
            self._combo.setEditText(str(group))
        fg.addRow("分类:", self._combo)
        v.addWidget(box_g)

        # --- 标签 ---
        box_t = QGroupBox("标签(可多选)")
        vt = QVBoxLayout(box_t)
        self._edit = QLineEdit("，".join(self._tags))
        self._edit.setPlaceholderText("用逗号分隔,例如:主力号, 每日, 活动")
        vt.addWidget(self._edit)

        self._chip_grid = QGridLayout()
        self._chip_grid.setSpacing(6)
        suggestions: list[str] = []
        for t in list(DEFAULT_TAGS) + list(known_tags or []) + self._tags:
            if t and t not in suggestions:
                suggestions.append(t)
        self._chips: dict[str, QPushButton] = {}
        for i, t in enumerate(suggestions[:12]):
            btn = QPushButton(f"#{t}")
            btn.setCheckable(True)
            btn.setChecked(t in self._tags)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet(
                "QPushButton { border:1px solid #555; border-radius:9px;"
                "padding:1px 8px; font-size:11px; color:#bbb; }"
                "QPushButton:checked { border-color:#4a9eff; color:#d8ecff;"
                "background:rgba(74,158,255,0.18); }"
            )
            btn.clicked.connect(lambda _=False, name=t: self._toggle_chip(name))
            self._chips[t] = btn
            self._chip_grid.addWidget(btn, i // 4, i % 4)
        vt.addLayout(self._chip_grid)

        tip = QLabel("提示:快捷标签点一下加入 / 再点移除;也可以在上面输入自定义标签。")
        tip.setStyleSheet("color:#888; font-size:11px;")
        tip.setWordWrap(True)
        vt.addWidget(tip)
        v.addWidget(box_t)

        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        bb.button(QDialogButtonBox.StandardButton.Ok).setText("确定")
        bb.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

    # --- 交互 ---
    def _current_tags(self) -> list[str]:
        return split_tags(self._edit.text())

    def _toggle_chip(self, name: str) -> None:
        cur = self._current_tags()
        if name in cur:
            cur = [t for t in cur if t != name]
        else:
            cur.append(name)
        self._edit.setText("，".join(cur))
        btn = self._chips.get(name)
        if btn is not None:
            btn.setChecked(name in cur)

    # --- 结果 ---
    def group(self) -> str:
        """选中的分类;输入框自填的名字优先."""
        text = self._combo.currentText().strip()
        if self._combo.currentIndex() == 0 and text in ("(未分类)", ""):
            return ""
        if text in ("(未分类)", "(无)"):
            return ""
        return text

    def tags(self) -> list[str]:
        return self._current_tags()
