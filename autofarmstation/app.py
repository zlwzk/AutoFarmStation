"""应用入口."""

from __future__ import annotations

import os
import sys

from .__init__ import __version__, __app_name__, __app_name_cn__, __author_handle__
from .utils.logger import setup, get


def _apply_palette(app, theme: str = "dark") -> None:
    """按主题应用调色板. v1.6.2 起读 cfg.get("ui.theme") 而不是写死 dark.

    注意:Qt 的 stylesheet + 调色板在「窗口已 show」之后切换只能改一部分颜色,
    想要所有页面 100% 跟随,得重启软件;这里会立刻刷新当前页面并提示用户重启。
    """
    from PySide6.QtGui import QPalette, QColor
    app.setStyle("Fusion")
    palette = QPalette()
    if theme == "light":
        palette.setColor(QPalette.ColorRole.Window, QColor(245, 246, 248))
        palette.setColor(QPalette.ColorRole.WindowText, QColor(28, 32, 36))
        palette.setColor(QPalette.ColorRole.Base, QColor(255, 255, 255))
        palette.setColor(QPalette.ColorRole.AlternateBase, QColor(240, 242, 245))
        palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(255, 255, 255))
        palette.setColor(QPalette.ColorRole.ToolTipText, QColor(28, 32, 36))
        palette.setColor(QPalette.ColorRole.Text, QColor(28, 32, 36))
        palette.setColor(QPalette.ColorRole.Button, QColor(232, 234, 238))
        palette.setColor(QPalette.ColorRole.ButtonText, QColor(28, 32, 36))
        palette.setColor(QPalette.ColorRole.BrightText, QColor(220, 60, 60))
        palette.setColor(QPalette.ColorRole.Link, QColor(36, 112, 220))
        palette.setColor(QPalette.ColorRole.Highlight, QColor(36, 112, 220))
        palette.setColor(QPalette.ColorRole.HighlightedText, QColor(255, 255, 255))
    else:  # dark
        palette.setColor(QPalette.ColorRole.Window, QColor(35, 35, 40))
        palette.setColor(QPalette.ColorRole.WindowText, QColor(220, 220, 220))
        palette.setColor(QPalette.ColorRole.Base, QColor(45, 45, 50))
        palette.setColor(QPalette.ColorRole.AlternateBase, QColor(55, 55, 60))
        palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(255, 255, 255))
        palette.setColor(QPalette.ColorRole.ToolTipText, QColor(220, 220, 220))
        palette.setColor(QPalette.ColorRole.Text, QColor(220, 220, 220))
        palette.setColor(QPalette.ColorRole.Button, QColor(55, 55, 60))
        palette.setColor(QPalette.ColorRole.ButtonText, QColor(220, 220, 220))
        palette.setColor(QPalette.ColorRole.BrightText, QColor(255, 80, 80))
        palette.setColor(QPalette.ColorRole.Link, QColor(74, 158, 255))
        palette.setColor(QPalette.ColorRole.Highlight, QColor(74, 158, 255))
        palette.setColor(QPalette.ColorRole.HighlightedText, QColor(0, 0, 0))
    app.setPalette(palette)


def apply_theme(app, theme: str) -> None:
    """设置面板切换主题后立刻调这个. 想要所有页面 100% 跟随需要重启。"""
    _apply_palette(app, theme)


def run(argv: list[str], version: str) -> int:
    """主入口."""
    # 解析参数
    verbose = "--verbose" in argv or "-v" in argv
    # self-test 模式
    if "--selftest" in argv:
        from scripts.selftest import run_all
        return run_all()

    # 启动 GUI
    setup()
    log = get()
    log.info("=== %s v%s 启动 ===", __app_name_cn__, version)
    # v1.6.2:启动时把「用户数据目录」+「bat 脚本库」位置打到日志,
    # 排查「升级后数据丢了」一类问题时第一时间能找到文件。
    try:
        from .utils.paths import user_data_dir, user_bat_dir
        log.info("用户数据目录:%s", user_data_dir())
        log.info("Bat 脚本库:%s", user_bat_dir())
    except Exception as ex:  # noqa: BLE001
        log.warning("路径模块加载失败:%s", ex)

    # 必须在 QApplication 创建后引用 QWidget
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import Qt
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_EnableHighDpiScaling, True)

    app = QApplication(argv)
    app.setApplicationName(__app_name__)
    app.setApplicationDisplayName(__app_name_cn__)
    app.setApplicationVersion(version)

    # 主题:读 cfg.ui.theme,没有则默认 dark(保证老用户行为不变)
    from .utils.config import instance as _cfg_instance
    _theme = "dark"
    try:
        _theme = str(_cfg_instance().get("ui.theme", "dark") or "dark")
    except Exception:
        _theme = "dark"
    _apply_palette(app, _theme)
    app._afs_theme = _theme  # 给设置对话框切换主题用

    # 主窗口
    from .ui.main_window import MainWindow
    win = MainWindow(verbose=verbose)
    win.show()

    return app.exec()