"""应用入口."""

from __future__ import annotations

import os
import sys

from .__init__ import __version__, __app_name__, __app_name_cn__, __author_handle__
from .utils.logger import setup, get


def _install_crash_guards(log) -> None:
    """全局兜底:任何没接住的异常(含工作线程)都写日志,不让进程直接崩死.

    - ``sys.excepthook``:PySide6 里 slot / QTimer 回调抛出的异常最终走到这里,
      默认是打印到 stderr,打包成 exe 后等于「静默崩」;改成写日志并在状态栏提示。
    - ``threading.excepthook``:截图 / 连点 / 宏等工作线程的未捕获异常,
      默认只打印线程名,同样写进日志方便排查。
    """
    import threading
    import traceback

    def _format(exc_type, exc, tb) -> str:
        return "".join(traceback.format_exception(exc_type, exc, tb))

    def _notify_gui(text: str) -> None:
        try:
            from PySide6.QtWidgets import QApplication
            app = QApplication.instance()
            if app is None:
                return
            win = app.activeWindow()
            if win is not None and hasattr(win, "_statusBar"):
                win._statusBar().showMessage(text, 6000)
        except Exception:
            pass

    def _sys_hook(exc_type, exc, tb) -> None:
        # KeyboardInterrupt / SystemExit 走原逻辑
        if issubclass(exc_type, (KeyboardInterrupt, SystemExit)):
            sys.__excepthook__(exc_type, exc, tb)
            return
        log.error("未捕获异常(已拦截,程序继续运行):\n%s", _format(exc_type, exc, tb))
        _notify_gui(f"⚠ 内部错误已拦截:{exc_type.__name__}: {exc}")

    def _thread_hook(args) -> None:
        log.error(
            "工作线程 %s 未捕获异常(已拦截):\n%s",
            getattr(args.thread, "name", "?"),
            _format(args.exc_type, args.exc_value, args.exc_traceback),
        )

    sys.excepthook = _sys_hook
    threading.excepthook = _thread_hook


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
    _install_crash_guards(log)
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