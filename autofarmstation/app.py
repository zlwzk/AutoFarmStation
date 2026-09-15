"""应用入口."""

from __future__ import annotations

import os
import sys

from .__init__ import __version__, __app_name__, __app_name_cn__, __author_handle__
from .utils.logger import setup, get


def _apply_dark_palette(app) -> None:
    """应用一套暗色调,提升观感."""
    from PySide6.QtGui import QPalette, QColor
    palette = QPalette()
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
    app.setStyle("Fusion")


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

    # 必须在 QApplication 创建后引用 QWidget
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import Qt
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_EnableHighDpiScaling, True)

    app = QApplication(argv)
    app.setApplicationName(__app_name__)
    app.setApplicationDisplayName(__app_name_cn__)
    app.setApplicationVersion(version)

    _apply_dark_palette(app)

    # 主窗口
    from .ui.main_window import MainWindow
    win = MainWindow(verbose=verbose)
    win.show()

    return app.exec()