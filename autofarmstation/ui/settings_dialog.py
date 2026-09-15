"""设置对话框."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QGroupBox,
    QCheckBox, QComboBox, QLineEdit, QDialogButtonBox, QPushButton, QLabel,
)

from ..utils.config import Config


class SettingsDialog(QDialog):
    def __init__(self, cfg: Config, parent=None) -> None:
        super().__init__(parent)
        self._cfg = cfg
        self.setWindowTitle("设置")
        self.resize(520, 480)

        v = QVBoxLayout(self)

        # UI
        ui_box = QGroupBox("界面")
        uf = QFormLayout(ui_box)
        self._theme = QComboBox()
        self._theme.addItems(["dark 深色", "light 浅色"])
        idx = 0 if cfg.get("ui.theme", "dark") == "dark" else 1
        self._theme.setCurrentIndex(idx)
        uf.addRow("主题:", self._theme)

        self._confirm_exit = QCheckBox("退出前确认")
        self._confirm_exit.setChecked(cfg.get("ui.confirm_exit", True))
        uf.addRow("", self._confirm_exit)

        self._minimize_tray = QCheckBox("最小化到托盘(占位)")
        self._minimize_tray.setChecked(cfg.get("ui.minimize_to_tray", True))
        self._minimize_tray.setEnabled(False)  # 简化:未实现托盘
        uf.addRow("", self._minimize_tray)

        v.addWidget(ui_box)

        # 全局热键(只展示,实际硬编码)
        hk_box = QGroupBox("全局热键(不可改)")
        hf = QFormLayout(hk_box)
        hf.addRow("启动全部:", QLabel("F9"))
        hf.addRow("停止全部:", QLabel("F10"))
        hf.addRow("一键急停:", QLabel("Ctrl + Alt + P"))
        v.addWidget(hk_box)

        # 隐私
        pr_box = QGroupBox("隐私与更新")
        pf = QFormLayout(pr_box)
        self._check_updates = QCheckBox("启动时自动检查更新(每小时)")
        self._check_updates.setChecked(cfg.get("settings.check_updates", True))
        pf.addRow("", self._check_updates)

        self._log_level = QComboBox()
        self._log_level.addItems(["DEBUG", "INFO", "WARNING", "ERROR"])
        idx = {"DEBUG": 0, "INFO": 1, "WARNING": 2, "ERROR": 3}.get(
            cfg.get("settings.log_level", "INFO"), 1)
        self._log_level.setCurrentIndex(idx)
        pf.addRow("日志级别:", self._log_level)

        info = QLabel("所有路径/用户名/游戏目录在日志与反馈中自动脱敏。")
        info.setStyleSheet("color:#888;")
        pf.addRow(info)
        v.addWidget(pr_box)

        # 数据目录(只读展示)
        path_box = QGroupBox("数据位置(不可改)")
        pbf = QFormLayout(path_box)
        from ..utils.paths import user_data_dir, user_log_dir, user_macro_dir, user_preset_dir
        from ..utils.sanitize import user_data_dir_display
        pbf.addRow("配置:", QLabel(user_data_dir_display() + "\\config.json"))
        pbf.addRow("日志:", QLabel(user_data_dir_display() + "\\logs\\"))
        pbf.addRow("宏:", QLabel(user_data_dir_display() + "\\macros\\"))
        pbf.addRow("预设:", QLabel(user_data_dir_display() + "\\presets\\"))
        v.addWidget(path_box)

        # 按钮
        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        bb.accepted.connect(self._on_accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

    def _on_accept(self) -> None:
        self._cfg.set("ui.theme", "dark" if self._theme.currentIndex() == 0 else "light")
        self._cfg.set("ui.confirm_exit", self._confirm_exit.isChecked())
        self._cfg.set("settings.check_updates", self._check_updates.isChecked())
        self._cfg.set("settings.log_level", self._log_level.currentText())
        self._cfg.save()
        self.accept()