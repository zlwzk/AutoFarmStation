"""Bat 脚本库对话框."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QSplitter, QTreeWidget, QTreeWidgetItem,
    QLabel, QPushButton, QLineEdit, QInputDialog, QMessageBox, QGroupBox,
    QFormLayout, QTextEdit, QDialogButtonBox, QWidget,
)

from ..core import BatLibrary, BatEntry


class BatLibraryDialog(QDialog):
    """非模态工具窗口:左侧分类树,右侧详情 + 操作按钮."""

    def __init__(self, lib: BatLibrary, parent=None) -> None:
        super().__init__(parent)
        self._lib = lib
        self.setWindowTitle("Bat 脚本库")
        self.resize(900, 560)
        # 不强制模态:让用户能在游戏和本窗口间来回切
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)

        v = QVBoxLayout(self)

        # 顶部:搜索 + 操作
        top = QHBoxLayout()
        self._search = QLineEdit()
        self._search.setPlaceholderText("搜索标题 / 标签 / 描述...")
        self._search.textChanged.connect(self._refresh)
        top.addWidget(self._search, stretch=1)

        btn_user_dir = QPushButton("📂 用户脚本目录")
        btn_user_dir.setToolTip(
            f"你的脚本(包括内置副本 + 自己加的)全部保存在:\n"
            f"{self._lib.user_dir()}\n\n"
            f"升级软件时不会被覆盖。",
        )
        btn_user_dir.clicked.connect(self._lib.open_user_dir)
        top.addWidget(btn_user_dir)

        btn_data_dir = QPushButton("🗂 数据目录")
        btn_data_dir.setToolTip(
            f"打开整个用户数据目录(配置 / 预设 / 宏 / 日志 / 统计 ...):\n"
            f"{self._lib.user_data_dir()}",
        )
        btn_data_dir.clicked.connect(self._on_open_data_dir)
        top.addWidget(btn_data_dir)

        btn_new = QPushButton("➕ 新建脚本")
        btn_new.clicked.connect(self._on_new)
        top.addWidget(btn_new)

        btn_del = QPushButton("🗑 删除")
        btn_del.clicked.connect(self._on_delete)
        top.addWidget(btn_del)

        v.addLayout(top)

        # 主体:左树 + 右详情
        split = QSplitter(Qt.Orientation.Horizontal)
        self._tree = QTreeWidget()
        self._tree.setHeaderLabels(["脚本"])
        self._tree.setColumnWidth(0, 280)
        self._tree.itemSelectionChanged.connect(self._on_select)
        split.addWidget(self._tree)

        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(8, 0, 0, 0)
        self._title = QLabel("(未选中)")
        self._title.setStyleSheet("font-size:14pt; font-weight:bold;")
        rv.addWidget(self._title)

        meta_box = QGroupBox("信息")
        mf = QFormLayout(meta_box)
        self._meta_path = QLabel("")
        self._meta_tags = QLabel("")
        self._meta_builtin = QLabel("")
        for w in (self._meta_path, self._meta_tags, self._meta_builtin):
            w.setStyleSheet("color:#aaa;")
            w.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        mf.addRow("脚本路径:", self._meta_path)
        mf.addRow("标签:", self._meta_tags)
        mf.addRow("来源:", self._meta_builtin)
        rv.addWidget(meta_box)

        desc_box = QGroupBox("描述")
        dv = QVBoxLayout(desc_box)
        self._desc = QTextEdit()
        self._desc.setReadOnly(True)
        dv.addWidget(self._desc)
        rv.addWidget(desc_box, stretch=1)

        # 操作按钮
        ops = QHBoxLayout()
        btn_run = QPushButton("▶ 运行")
        btn_run.clicked.connect(self._on_run)
        ops.addWidget(btn_run)
        btn_reveal = QPushButton("🔍 在文件管理器中定位")
        btn_reveal.clicked.connect(self._on_reveal)
        ops.addWidget(btn_reveal)
        btn_edit = QPushButton("✏️ 编辑脚本")
        btn_edit.clicked.connect(self._on_edit)
        ops.addWidget(btn_edit)
        rv.addLayout(ops)

        split.addWidget(right)
        split.setStretchFactor(0, 2)
        split.setStretchFactor(1, 3)
        v.addWidget(split, stretch=1)

        # 底部
        self._status = QLabel("")
        self._status.setStyleSheet("color:#888;")
        v.addWidget(self._status)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

        # 初次启动把内置拷到用户目录 + 新版本合并内置新增条目
        n = self._lib.ensure_seeded()
        if n:
            self._status.setText(
                f"已把 {n} 个内置脚本(首次 / 新版本)同步到用户目录,可自由修改",
            )

        self._refresh()
        self._center_on_parent()

    def _center_on_parent(self) -> None:
        if self.parent() is None:
            return
        QTimer.singleShot(0, lambda: (
            self.move(
                self.parent().x() + 40,
                self.parent().y() + 40,
            )
        ))

    def _on_open_data_dir(self) -> None:
        """打开整个 %APPDATA%\\AutoFarmStation 文件夹(包含 bat / 配置 / 日志 ...)."""
        d = self._lib.user_data_dir()
        try:
            import os as _os
            import subprocess as _sp
            import sys as _sys
            d.mkdir(parents=True, exist_ok=True)
            if _sys.platform == "win32":
                _os.startfile(str(d))  # type: ignore[attr-defined]
            else:
                _sp.Popen(["xdg-open", str(d)])
        except OSError as ex:
            QMessageBox.warning(self, "打开失败", str(ex))

    # --- 列表 / 选中 ---
    def _refresh(self) -> None:
        keyword = self._search.text().strip().lower()
        self._tree.clear()
        groups: dict[str, QTreeWidgetItem] = {}
        for e in self._lib.list():
            if keyword and not _match(e, keyword):
                continue
            grp = groups.get(e.category)
            if grp is None:
                grp = QTreeWidgetItem([f"📁 {e.category}"])
                grp.setData(0, Qt.ItemDataRole.UserRole, None)
                grp.setExpanded(True)
                self._tree.addTopLevelItem(grp)
                groups[e.category] = grp
            label = f"{'🔒' if e.builtin else '🟢'} {e.title}"
            item = QTreeWidgetItem([label])
            item.setData(0, Qt.ItemDataRole.UserRole, e.id)
            grp.addChild(item)
        if not self._tree.topLevelItemCount():
            self._title.setText("(没有脚本)")
            self._desc.setPlainText("点击右上「新建脚本」开始。")
            self._meta_path.setText("")
            self._meta_tags.setText("")
            self._meta_builtin.setText("")

    def _on_select(self) -> None:
        items = self._tree.selectedItems()
        if not items:
            return
        eid = items[0].data(0, Qt.ItemDataRole.UserRole)
        if not eid:
            return
        e = self._lib.get(eid)
        if not e:
            return
        self._title.setText(e.title)
        self._desc.setPlainText(e.desc)
        self._meta_path.setText(str(e.full_path) if e.full_path else "(文件缺失)")
        self._meta_tags.setText(", ".join(e.tags) or "(无)")
        self._meta_builtin.setText("🔒 内置(只读)" if e.builtin else "🟢 用户脚本(可改/可删)")

    def selected_entry(self) -> BatEntry | None:
        items = self._tree.selectedItems()
        if not items:
            return None
        eid = items[0].data(0, Qt.ItemDataRole.UserRole)
        return self._lib.get(eid) if eid else None

    # --- 操作 ---
    def _on_new(self) -> None:
        title, ok = QInputDialog.getText(self, "新建脚本", "给脚本起个名字:")
        if not ok or not title.strip():
            return
        try:
            e = self._lib.create(title.strip())
        except OSError as ex:
            QMessageBox.warning(self, "创建失败", str(ex))
            return
        self._refresh()
        # 选中新创建的
        for i in range(self._tree.topLevelItemCount()):
            grp = self._tree.topLevelItem(i)
            for j in range(grp.childCount()):
                ch = grp.child(j)
                if ch.data(0, Qt.ItemDataRole.UserRole) == e.id:
                    self._tree.setCurrentItem(ch)
                    break
        self._status.setText(f"已新建:{e.title} —— 在右侧「编辑脚本」里加你的命令")

    def _on_delete(self) -> None:
        e = self.selected_entry()
        if e is None:
            return
        if e.builtin:
            QMessageBox.information(self, "内置脚本", "这是内置脚本,不允许删除(可复制到用户目录后修改)。")
            return
        r = QMessageBox.question(
            self, "删除脚本",
            f"确认删除「{e.title}」?\n文件:{e.full_path}\n\n(只删用户脚本,内置项不受影响)",
        )
        if r != QMessageBox.StandardButton.Yes:
            return
        if self._lib.delete(e.id):
            self._status.setText(f"已删除:{e.title}")
            self._refresh()
        else:
            QMessageBox.warning(self, "删除失败", "文件可能被占用或已不存在")

    def _on_run(self) -> None:
        e = self.selected_entry()
        if e is None:
            return
        if e.confirm:
            r = QMessageBox.question(
                self, "运行脚本",
                f"即将运行:\n  {e.full_path}\n\n确认?(若脚本里要管理员权限,系统会再次弹 UAC)",
            )
            if r != QMessageBox.StandardButton.Yes:
                return
        args = None
        if e.args:
            args = {}
            for a in e.args:
                v, ok = QInputDialog.getText(
                    self, f"参数:{a.label}",
                    f"{a.label}  (参数名 --{a.name})",
                    text=a.default,
                )
                if not ok:
                    return
                args[a.name] = v
        proc = self._lib.run(e.id, args)
        if proc is None:
            QMessageBox.warning(self, "运行失败", "脚本文件丢失或被占用")
            return
        self._status.setText(f"已启动:{e.title} (PID={proc.pid}) —— 不会弹黑色 cmd 窗口")

    def _on_reveal(self) -> None:
        e = self.selected_entry()
        if e is None:
            return
        if not self._lib.reveal(e.id):
            QMessageBox.warning(self, "打开失败", "找不到文件")

    def _on_edit(self) -> None:
        e = self.selected_entry()
        if e is None:
            return
        if e.builtin:
            r = QMessageBox.question(
                self, "内置脚本",
                f"「{e.title}」是内置脚本,不能直接修改。\n"
                "是否复制一份到用户目录再编辑?",
            )
            if r != QMessageBox.StandardButton.Yes:
                return
            # 复制 .bat 到用户目录
            try:
                new_e = self._lib.create(
                    f"{e.title} (副本)",
                    file_name=f"{e.id}.bat",
                    desc=e.desc,
                )
            except OSError as ex:
                QMessageBox.warning(self, "复制失败", str(ex))
                return
            self._refresh()
            self._status.setText(f"已复制为:{new_e.title}")
            return
        if not self._lib.edit(e.id):
            QMessageBox.warning(self, "打开失败", "找不到文件")


def _match(e: BatEntry, keyword: str) -> bool:
    if keyword in e.title.lower():
        return True
    if keyword in e.desc.lower():
        return True
    if keyword in e.category.lower():
        return True
    return any(keyword in t.lower() for t in e.tags)