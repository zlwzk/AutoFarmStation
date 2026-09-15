"""主窗口."""

from __future__ import annotations

import logging
import threading
import time
import webbrowser

from PySide6.QtCore import Qt, QTimer, QSize
from PySide6.QtGui import QAction, QKeySequence, QShortcut, QIcon
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QSplitter, QMenuBar,
    QToolBar, QStatusBar, QMessageBox, QFileDialog, QLabel, QComboBox,
    QInputDialog, QApplication,
)

from .. import __version__, __app_name__, __app_name_cn__, __author_handle__
from ..core import (
    ProcessManager, ProcessMonitor, MonitorEvent, MonitorStatus,
    Scheduler, ScheduledTask, TaskFreq, Statistics,
    PresetLibrary, Preset, AutomationHub, SteamStatusController,
    Session,
)
from ..core import window_finder as wf
from ..utils import config as cfg_mod
from ..utils.logger import get
from ..utils.update_checker import check as check_update, UpdateInfo
from .preview_grid import PreviewGrid
from .action_panel import ActionPanel
from .process_selector_dialog import ProcessSelectorDialog
from .widgets import styled_message
from .bat_library_dialog import BatLibraryDialog
from ..core.bat_library import BatLibrary


# === 全局热键监听(基于 keyboard hook) ===
class GlobalHotkeyWatcher:
    """监听全局快捷键.用 pynput 的 keyboard 钩子(后台线程)."""

    def __init__(self, logger: logging.Logger | None = None) -> None:
        self._log = logger or logging.getLogger("autofarmstation.hotkey")
        self._listener = None
        self._callbacks: dict[str, callable] = {}
        self._lock = threading.Lock()

    def register(self, name: str, cb) -> None:
        with self._lock:
            self._callbacks[name] = cb

    def start(self) -> bool:
        try:
            from pynput import keyboard  # type: ignore
        except Exception as e:
            self._log.warning("pynput 不可用,全局热键将禁用: %s", e)
            return False
        try:
            self._listener = keyboard.GlobalHotKeys({
                "<f9>": lambda: self._fire("start_all"),
                "<f10>": lambda: self._fire("stop_all"),
                "<ctrl>+<alt>+p": lambda: self._fire("panic"),
            })
            self._listener.start()
            return True
        except Exception as e:
            self._log.warning("启动全局热键失败: %s", e)
            return False

    def stop(self) -> None:
        try:
            if self._listener:
                self._listener.stop()
        except Exception:
            pass
        self._listener = None

    def _fire(self, name: str) -> None:
        cb = self._callbacks.get(name)
        if cb:
            try:
                cb()
            except Exception as e:
                self._log.warning("热键回调出错: %s", e)


class MainWindow(QMainWindow):
    """主窗口."""

    def __init__(self, *, verbose: bool = False) -> None:
        super().__init__()
        self._log = get()
        self._cfg = cfg_mod.instance()
        self._log.setLevel(self._cfg.get("settings.log_level", "INFO") if not verbose else "DEBUG")

        # 核心
        self._pm = ProcessManager(logger=self._log)
        self._stats = Statistics()
        self._stats.on_session_start()
        self._presets = PresetLibrary(logger=self._log)
        self._sched = Scheduler(logger=self._log)
        self._monitor = ProcessMonitor(self._pm, logger=self._log)
        self._bat_lib = BatLibrary()
        # 多窗口自动化中枢:每窗口独立配置 + 多窗口同步执行
        self._hub = AutomationHub(stats=self._stats, logger=self._log)
        # Steam 状态联动:挂机时自动切状态,停完再还原
        self._steam = SteamStatusController(self._cfg, self._log)
        self._pm.on_change(self._on_pm_change)

        # 全局热键
        self._hotkeys = GlobalHotkeyWatcher(logger=self._log)
        self._hotkeys.register("start_all", self._on_hotkey_start_all)
        self._hotkeys.register("stop_all", self._on_hotkey_stop_all)
        self._hotkeys.register("panic", self._on_hotkey_panic)
        self._hotkeys.start()

        # 调度 handlers
        self._sched.register_handler("start_all", lambda t: self._on_hotkey_start_all())
        self._sched.register_handler("stop_all", lambda t: self._on_hotkey_stop_all())
        self._sched.start()
        self._monitor.start()

        # UI
        self.setWindowTitle(f"{__app_name_cn__} v{__version__}")
        self.resize(1400, 880)
        self._build_menu()
        self._build_toolbar()
        self._build_body()
        self._build_statusbar()

        # 加载持久化
        self._load_state()
        # 跨启动的窗口快照(独立于 config):启动后弹框问是否恢复上次窗口
        self._session = Session()
        self._restore_asked = False
        QTimer.singleShot(800, self._maybe_prompt_restore_session)
        # 检查更新 — 用 cfg 里的频率(默认每小时)
        self._update_timer = QTimer(self)
        self._update_timer.timeout.connect(self._periodic_update_check)
        interval_h = max(1, int(self._cfg.get("settings.update_check_interval_hours", 1)))
        self._update_timer.start(interval_h * 3600 * 1000)
        QTimer.singleShot(3000, self._periodic_update_check)

        # 退出时保存
        self._saved = False

    # --- 关闭 ---
    def closeEvent(self, ev) -> None:
        if self._cfg.get("ui.confirm_exit", True):
            r = QMessageBox.question(self, "退出", "确认退出 多开挂机大师?所有运行中的连点器/键盘宏会停止。")
            if r != QMessageBox.StandardButton.Yes:
                ev.ignore()
                return
        self._save_state()
        try:
            self._steam.shutdown()
            self._hub.stop_all()
            self._monitor.stop()
            self._sched.stop()
            self._hotkeys.stop()
            self._stats.on_session_end()
            self._stats.save()
        except Exception:
            pass
        # 还原所有嵌入窗口,避免退出后游戏窗口跟着消失
        try:
            from ..core import window_host
            window_host.release_all()
        except Exception:
            pass
        super().closeEvent(ev)

    # --- 菜单 / 工具栏 / 主体 / 状态栏 ---
    def _build_menu(self) -> None:
        mb = self.menuBar()
        # 进程
        menu_proc = mb.addMenu("进程(&P)")
        a_add = QAction("添加窗口...", self)
        a_add.setShortcut("Ctrl+N")
        a_add.triggered.connect(self._on_add_process)
        menu_proc.addAction(a_add)
        a_refresh = QAction("刷新窗口列表", self)
        a_refresh.setShortcut("F5")
        a_refresh.triggered.connect(self._on_refresh_windows)
        menu_proc.addAction(a_refresh)
        a_clear = QAction("清空追踪", self)
        a_clear.triggered.connect(self._on_clear_all)
        menu_proc.addAction(a_clear)
        menu_proc.addSeparator()

        # 操作
        menu_op = mb.addMenu("操作(&O)")
        a_start = QAction("启动全部", self)
        a_start.setShortcut("F9")
        a_start.triggered.connect(self._on_hotkey_start_all)
        menu_op.addAction(a_start)
        a_stop = QAction("停止全部", self)
        a_stop.setShortcut("F10")
        a_stop.triggered.connect(self._on_hotkey_stop_all)
        menu_op.addAction(a_stop)
        a_panic = QAction("一键急停", self)
        a_panic.setShortcut("Ctrl+Alt+P")
        a_panic.triggered.connect(self._on_hotkey_panic)
        menu_op.addAction(a_panic)
        menu_op.addSeparator()

        # 工具
        menu_tool = mb.addMenu("工具(&T)")
        a_settings = QAction("设置...", self)
        a_settings.setShortcut("Ctrl+,")
        a_settings.triggered.connect(self._on_open_settings)
        menu_tool.addAction(a_settings)
        a_bat = QAction("Bat 脚本库...", self)
        a_bat.setShortcut("Ctrl+B")
        a_bat.triggered.connect(self._on_open_bat_library)
        menu_tool.addAction(a_bat)
        menu_tool.addSeparator()
        a_steam = QAction("还原 Steam 在线状态", self)
        a_steam.triggered.connect(self._on_restore_steam)
        menu_tool.addAction(a_steam)
        a_update = QAction("检查更新", self)
        a_update.triggered.connect(lambda: self._do_update_check(show_dialog=True))
        menu_tool.addAction(a_update)
        a_log = QAction("打开日志目录", self)
        a_log.triggered.connect(self._on_open_log_dir)
        menu_tool.addAction(a_log)
        a_log_file = QAction("查看日志文件", self)
        a_log_file.triggered.connect(self._on_open_log_file)
        menu_tool.addAction(a_log_file)

        # 帮助
        menu_help = mb.addMenu("帮助(&H)")
        a_about = QAction(f"关于 {__app_name_cn__}", self)
        a_about.triggered.connect(self._on_about)
        menu_help.addAction(a_about)
        a_feedback = QAction("反馈建议(去 GitHub)", self)
        a_feedback.triggered.connect(self._on_feedback)
        menu_help.addAction(a_feedback)

    def _build_toolbar(self) -> None:
        tb = QToolBar("主工具栏")
        tb.setIconSize(QSize(16, 16))
        tb.setMovable(False)
        self.addToolBar(tb)

        a_add = QAction("➕ 添加窗口", self)
        a_add.triggered.connect(self._on_add_process)
        tb.addAction(a_add)

        a_refresh = QAction("🔄 刷新", self)
        a_refresh.triggered.connect(self._on_refresh_windows)
        tb.addAction(a_refresh)

        tb.addSeparator()
        tb.addWidget(QLabel("  布局列数: "))
        self._cols_combo = QComboBox()
        self._cols_combo.addItems(["自适应", "1", "2", "3", "4", "6"])
        self._cols_combo.currentIndexChanged.connect(self._on_cols_changed)
        tb.addWidget(self._cols_combo)

        tb.addWidget(QLabel("  预览帧率: "))
        self._fps_combo = QComboBox()
        self._fps_combo.addItems(["0.5", "1", "2", "3", "5"])
        self._fps_combo.setCurrentText(str(self._cfg.get("ui.preview_fps", 1)))
        self._fps_combo.currentTextChanged.connect(self._on_fps_changed)
        tb.addWidget(self._fps_combo)

        tb.addSeparator()
        a_start = QAction("▶ 启动全部", self)
        a_start.triggered.connect(self._on_hotkey_start_all)
        tb.addAction(a_start)
        a_stop = QAction("■ 停止全部", self)
        a_stop.triggered.connect(self._on_hotkey_stop_all)
        tb.addAction(a_stop)

    def _build_body(self) -> None:
        splitter = QSplitter()
        splitter.setOrientation(Qt.Orientation.Horizontal)
        left = PreviewGrid(
            self._pm,
            fps=float(self._cfg.get("ui.preview_fps", 1)),
            columns=int(self._cfg.get("ui.preview_columns", 0)),
            hub=self._hub,
        )
        right = ActionPanel(
            self._pm, self._sched, self._stats, self._presets, hub=self._hub,
        )

        # 预览网格信号 → 主窗口
        left.selection_changed.connect(self._on_sync_selection_changed)
        left.sync_start_requested.connect(right.sync_start)
        left.sync_stop_requested.connect(right.sync_stop)
        left.sync_broadcast_requested.connect(right.broadcast_current_config)

        # 每张卡片的信号(卡片会随进程增删动态创建,所以在 _sync 后补连)
        def wire():
            for w in left._items.values():
                for sig, slot in (
                    (w.focused, self._on_focus_from_preview),
                    (w.closed, self._on_close_from_preview),
                    (w.autoclick_toggle, self._on_autoclick_toggle_from_preview),
                ):
                    try:
                        sig.disconnect(slot)
                    except (RuntimeError, TypeError):
                        pass
                    sig.connect(slot)

        old_sync = left._sync

        def _sync_and_wire():
            old_sync()
            wire()

        left._sync = _sync_and_wire  # type: ignore[assignment]
        QTimer.singleShot(200, wire)

        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([900, 500])
        self._left = left
        self._right = right
        self.setCentralWidget(splitter)

    def _on_sync_selection_changed(self, hwnds: list) -> None:
        """左侧勾选变化 → 同步到右侧面板."""
        self._right.set_sync_targets([int(h) for h in hwnds])
        n = len(hwnds)
        if n:
            self._statusBar().showMessage(  # type: ignore[union-attr]
                f"已选择 {n} 个窗口 · 可用「同步启动」让它们执行同一套操作", 3000)
        self._refresh_status()

    def _build_statusbar(self) -> None:
        sb = QStatusBar()
        self._status_label = QLabel(f"{__app_name_cn__} v{__version__} · 就绪")
        self._status_label.setStyleSheet("color:#888;")
        sb.addWidget(self._status_label, stretch=1)
        self._process_count_label = QLabel("追踪:0")
        sb.addPermanentWidget(self._process_count_label)
        self.setStatusBar(sb)
        self._status_timer = QTimer(self)
        self._status_timer.timeout.connect(self._refresh_status)
        self._status_timer.start(2000)

    # --- 进程操作 ---
    def _on_add_process(self) -> None:
        dlg = ProcessSelectorDialog(self)
        if dlg.exec():
            # 支持多选:这里取所有选中
            sels = dlg.selected_all()
            if not sels and dlg.selected:
                sels = [dlg.selected]
            for w in sels:
                tp = self._pm.add_by_hwnd(w.hwnd)
                if tp:
                    # 自动匹配预设
                    auto_preset = self._presets.find_match(
                        process_name=tp.name, title=tp.title,
                    )
                    if auto_preset:
                        self._log.info("为 %s 自动匹配预设:%s", tp.title, auto_preset.name)
            self._save_state()
            self._statusBar().showMessage(
                f"已添加 {len(sels)} 个窗口", 3000,  # type: ignore[union-attr]
            )

    def _on_refresh_windows(self) -> None:
        gone = self._pm.refresh()
        if gone:
            self._statusBar().showMessage(
                f"{len(gone)} 个窗口已失效", 3000,  # type: ignore[union-attr]
            )
        else:
            self._statusBar().showMessage("刷新完成", 2000)  # type: ignore[union-attr]

    def _on_clear_all(self) -> None:
        r = QMessageBox.question(self, "清空追踪", "确认清空所有追踪窗口?")
        if r == QMessageBox.StandardButton.Yes:
            self._hub.stop_all()
            self._pm.clear()
            self._save_state()

    def _on_pm_change(self) -> None:
        self._save_state()
        self._refresh_status()

    def _on_focus_from_preview(self, hwnd: int) -> None:
        info = wf.get_window_info(hwnd)
        if info:
            self._right.set_target(hwnd, info.title)

    def _on_close_from_preview(self, hwnd: int) -> None:
        self._hub.forget(hwnd)
        self._pm.remove(hwnd)
        self._save_state()

    def _on_autoclick_toggle_from_preview(self, hwnd: int) -> None:
        """卡片上的「连点」按钮:直接对该窗口启停(该窗口独立配置)."""
        ap = self._right
        try:
            ap._tabs.setCurrentWidget(ap._clicker_panel)  # type: ignore[attr-defined]
            info = wf.get_window_info(hwnd)
            ap.set_target(hwnd, info.title if info else str(hwnd))
        except Exception:
            pass

        if self._hub.is_clicker_running(hwnd):
            self._hub.stop_clicker(hwnd)
            self._statusBar().showMessage(f"[{hwnd}] 连点器已停止", 2500)  # type: ignore[union-attr]
            return

        # 该窗口没有配置时,用右侧面板当前的配置复制一份
        if self._hub.clicker_config(hwnd) is None:
            cfg = self._right._clicker_panel.build_config()  # type: ignore[attr-defined]
            if cfg is None:
                self._statusBar().showMessage(  # type: ignore[union-attr]
                    f"[{hwnd}] 还没有点位,请在「连点器」面板添加后再启动", 4000)
                return
            self._hub.set_clicker_config(hwnd, cfg)

        if self._hub.start_clicker(hwnd):
            self._statusBar().showMessage(f"[{hwnd}] 连点器已启动", 2500)  # type: ignore[union-attr]
        else:
            self._statusBar().showMessage(f"[{hwnd}] 连点器启动失败", 3000)  # type: ignore[union-attr]

    # --- 热键 ---
    def _on_hotkey_start_all(self) -> None:
        """启动全部:优先启动「有勾选则勾选,否则全部追踪窗口」的已配置项."""
        try:
            grid_targets = self._left.selected_hwnds() or self._left.all_hwnds()
            if not grid_targets:
                self._statusBar().showMessage("没有可启动的窗口", 2500)  # type: ignore[union-attr]
                return
            # 1) 先保证每个目标至少有一份配置(没有的话,用右侧面板当前配置广播)
            ccfg = self._right._clicker_panel.build_config()  # type: ignore[attr-defined]
            kcfg = self._right._key_panel.build_config()  # type: ignore[attr-defined]
            if ccfg is not None or kcfg is not None:
                for h in grid_targets:
                    if ccfg is not None and self._hub.clicker_config(h) is None:
                        self._hub.set_clicker_config(h, ccfg)
                    if kcfg is not None and self._hub.key_macro_config(h) is None:
                        self._hub.set_key_macro_config(h, kcfg)
            # 2) 启动
            res = self._hub.start_many(grid_targets, clicker=True, key_macro=True)
            self._statusBar().showMessage(f"启动全部:{res.message}", 3000)  # type: ignore[union-attr]
        except Exception as e:
            self._log.warning("启动全部失败: %s", e)

    def _on_hotkey_stop_all(self) -> None:
        try:
            self._hub.stop_all()
            self._right.stop_all()
            self._statusBar().showMessage("已停止全部", 2000)  # type: ignore[union-attr]
        except Exception as e:
            self._log.warning("停止全部失败: %s", e)

    def _on_hotkey_panic(self) -> None:
        try:
            self._hub.stop_all()
            self._right.stop_all()
            self._sched.stop()
            self._monitor.stop()
            self._statusBar().showMessage("急停:全部停止", 3000)  # type: ignore[union-attr]
        except Exception:
            pass

    # --- 工具栏 ---
    def _on_cols_changed(self, idx: int) -> None:
        cols = 0 if idx == 0 else int(self._cols_combo.currentText())
        self._cfg.set("ui.preview_columns", cols)
        self._cfg.save()
        self._left.set_columns(cols)

    def _on_fps_changed(self, text: str) -> None:
        try:
            fps = float(text)
        except ValueError:
            fps = 1.0
        self._cfg.set("ui.preview_fps", fps)
        self._cfg.save()
        self._left.set_fps(fps)

    # --- 状态栏 ---
    def _refresh_status(self) -> None:
        n = len(self._pm)
        running = self._hub.running_hwnds()
        sel = len(self._left.selected_hwnds()) if hasattr(self, "_left") else 0
        txt = f"追踪:{n}"
        if running:
            txt += f" · 运行:{len(running)}"
        if sel:
            txt += f" · 已选:{sel}"
        if self._steam.applied:
            txt += " · Steam已切"
        self._process_count_label.setText(txt)
        # Steam 状态联动:边沿触发(运行数 0↔N 时切一次)
        try:
            self._steam.on_running_changed(len(running))
            for msg in self._steam.poll_messages():
                self._statusBar().showMessage(f"[Steam] {msg}", 6000)  # type: ignore[union-attr]
        except Exception as e:  # noqa: BLE001
            self._log.debug("Steam 状态联动出错: %s", e)

    # --- 持久化 ---
    def _save_state(self) -> None:
        # 保存 tracked_processes 到 config
        self._cfg.set("tracked_processes", self._pm.export_list())
        # 保存每窗口的自动化配置(点位/按键序列),下次启动可恢复
        try:
            self._cfg.set("automation_configs", self._hub.export_configs())
        except Exception as e:
            self._log.debug("保存自动化配置失败: %s", e)
        self._cfg.save()
        # 跨启动窗口快照(独立:启动时按 exe+title 匹配当前可见窗口)
        try:
            self._session.save(self._pm, self._hub)
        except Exception as e:
            self._log.debug("保存 session 快照失败: %s", e)

    def _load_state(self) -> None:
        # 不再从 config 恢复 tracked_processes/automation_configs:
        # hwnd 跨启动失效,这些字段在程序运行期内由 _save_state 自然维护。
        # 跨启动恢复改走 Session(由 _maybe_prompt_restore_session 处理)。
        pass

    # --- 启动恢复上次窗口 ---
    def _maybe_prompt_restore_session(self) -> None:
        """首次启动后异步检查 session.json,匹配当前可见窗口,弹框询问是否恢复."""
        if self._restore_asked:
            return
        self._restore_asked = True
        try:
            data = self._session.load()
        except Exception as e:
            self._log.warning("加载 session 快照失败: %s", e)
            return
        if not data:
            return
        saved_items = data.get("items") or []
        if not saved_items:
            return
        # 用户在设置里关掉了「每次询问恢复」
        if not self._cfg.get("ui.restore_session_ask", True):
            return
        # 取当前可见窗口(过滤掉本程序自身)
        try:
            visible = [
                w for w in wf.list_visible_windows()
                if __app_name_cn__ not in (w.title or "")
            ]
        except Exception as e:
            self._log.warning("枚举可见窗口失败: %s", e)
            return
        pairs = Session.match(saved_items, visible)
        if not pairs:
            # 上次有窗口但现在一个都没找到 → 自动清掉,不再打扰
            self._session.clear()
            self._statusBar().showMessage(  # type: ignore[union-attr]
                "上次的窗口已不在桌面上,无需恢复", 4000,
            )
            return
        # 弹框询问
        matched = len(pairs)
        total = len(saved_items)
        missed = total - matched
        title = "恢复上次的窗口"
        msg = (
            f"上次关闭时共追踪 {total} 个窗口。\n"
            f"在当前桌面上匹配到 {matched} 个"
            + (f",还有 {missed} 个未找到(可能未启动)。" if missed else "。")
            + "\n\n是否把它们自动加回来并套用上次的连点/宏配置?"
        )
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle(title)
        box.setText(msg)
        yes = box.addButton("恢复", QMessageBox.ButtonRole.YesRole)
        no = box.addButton("不恢复", QMessageBox.ButtonRole.NoRole)
        never = box.addButton("不再提示", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(yes)
        box.exec()
        clicked = box.clickedButton()
        if clicked is yes:
            self._do_restore_session(pairs)
        elif clicked is never:
            self._cfg.set("ui.restore_session_ask", False)
            self._cfg.save()
            self._session.clear()
        else:
            # 「不恢复」保留文件,下次启动仍会问(用户可改主意)
            pass

    def _do_restore_session(self, pairs: list[tuple[dict, wf.WindowInfo]]) -> None:
        """按匹配结果把窗口加回来 + 套用保存的连点/宏配置."""
        added = 0
        new_configs: dict[str, dict] = {}
        for s, w in pairs:
            tp = self._pm.add_by_hwnd(w.hwnd)
            if tp is None:
                continue
            added += 1
            cfg = (s.get("config") or {}) if isinstance(s, dict) else {}
            if cfg:
                new_configs[str(int(w.hwnd))] = cfg
        if new_configs:
            try:
                self._hub.import_configs(new_configs)
            except Exception as e:
                self._log.warning("恢复配置失败: %s", e)
        self._save_state()
        self._refresh_status()
        self._statusBar().showMessage(  # type: ignore[union-attr]
            f"已恢复 {added} 个窗口(套用上次配置)", 4000,
        )

    # --- Steam 状态 ---
    def _on_restore_steam(self) -> None:
        """手动把 Steam 状态还原成挂机前的值."""
        if self._steam.restore_if_needed(reason="手动还原"):
            self._statusBar().showMessage("已请求还原 Steam 状态", 3000)  # type: ignore[union-attr]
        else:
            self._statusBar().showMessage(  # type: ignore[union-attr]
                f"[Steam] {self._steam.describe()}", 5000)
        self._refresh_status()

    # --- 设置 ---
    def _on_open_settings(self) -> None:
        from .settings_dialog import SettingsDialog
        dlg = SettingsDialog(self._cfg, self)
        dlg.exec()

    def _on_open_bat_library(self) -> None:
        dlg = BatLibraryDialog(self._bat_lib, self)
        dlg.show()

    def _on_about(self) -> None:
        from ..__init__ import PROJECT_DESCRIPTION, PROJECT_TAGLINE
        QMessageBox.about(
            self,
            f"关于 {__app_name_cn__}",
            f"<h2>{__app_name_cn__} v{__version__}</h2>"
            f"<p><i>{PROJECT_TAGLINE}</i></p>"
            f"<p>{PROJECT_DESCRIPTION}</p>"
            f"<p>仓库:<a href='https://github.com/{__author_handle__}/{__app_name__}'>"
            f"github.com/{__author_handle__}/{__app_name__}</a></p>"
            f"<p>数据目录:%APPDATA%\\{__app_name__}<br>"
            f"日志目录:%APPDATA%\\{__app_name__}\\logs</p>"
        )

    def _on_feedback(self) -> None:
        url = f"https://github.com/{__author_handle__}/{__app_name__}/issues"
        QMessageBox.information(
            self, "反馈建议",
            f"请前往 GitHub Issues 反馈:\n{url}\n\n"
            f"反馈前会自动脱敏:用户名、游戏目录、机器名均不会上传。",
        )

    def _on_open_log_dir(self) -> None:
        import os
        path = os.environ.get("APPDATA", "") + "\\" + __app_name__ + "\\logs"
        if not path.strip("\\"):
            path = "%APPDATA%\\" + __app_name__ + "\\logs"
        try:
            os.startfile(path)  # type: ignore[attr-defined]
        except Exception:
            QMessageBox.information(self, "日志目录", path)

    def _on_open_log_file(self) -> None:
        import os
        path = os.environ.get("APPDATA", "") + "\\" + __app_name__ + "\\logs\\autofarmstation.log"
        if not path.strip("\\"):
            path = "%APPDATA%\\" + __app_name__ + "\\logs\\autofarmstation.log"
        try:
            os.startfile(path)  # type: ignore[attr-defined]
        except Exception:
            QMessageBox.information(self, "日志文件", path)

    # --- 更新检查 ---
    def _periodic_update_check(self) -> None:
        if self._cfg.get("settings.check_updates", True):
            self._do_update_check(show_dialog=False)

    def _do_update_check(self, *, show_dialog: bool) -> None:
        def worker():
            info: UpdateInfo = check_update()
            # 切回主线程
            QTimer.singleShot(0, lambda: self._on_update_done(info, show_dialog))

        threading.Thread(target=worker, daemon=True, name="UpdateCheck").start()

    def _on_update_done(self, info: UpdateInfo, show_dialog: bool) -> None:
        import datetime as _dt
        ts = _dt.datetime.now().isoformat(timespec="seconds")
        self._cfg.set("settings.last_update_check_at", ts)
        if info.error:
            self._cfg.save()
            if show_dialog:
                QMessageBox.warning(
                    self, "检查更新",
                    f"检查失败:{info.error}\n\n下次按设置频率自动重试。",
                )
            return
        skipped = self._cfg.get("settings.skipped_version", "")
        if info.has_update:
            self._cfg.set("settings.last_update_found", info.latest_version)
            self._cfg.save()
            # 跳过被「稍后」忽略的版本
            if not show_dialog and skipped == info.latest_version:
                return
            # 静默检查 → 用顶部横幅,不打断用户
            if show_dialog:
                r = QMessageBox.question(
                    self, "发现新版本",
                    f"发现新版本 v{info.latest_version}(当前 v{info.current_version})。\n\n"
                    f"更新说明(摘要):\n{info.release_notes[:400]}\n\n"
                    f"是否前往下载?\n{info.release_url}",
                    QMessageBox.StandardButton.Yes
                    | QMessageBox.StandardButton.No
                    | QMessageBox.StandardButton.Ignore,
                )
                if r == QMessageBox.StandardButton.Yes:
                    import webbrowser
                    webbrowser.open(info.release_url)
                elif r == QMessageBox.StandardButton.Ignore:
                    self._cfg.set("settings.skipped_version", info.latest_version)
                    self._cfg.save()
            else:
                self._show_update_banner(info)
        else:
            self._cfg.set("settings.last_update_found", "")
            self._cfg.set("settings.skipped_version", "")
            self._cfg.save()
            if show_dialog:
                QMessageBox.information(
                    self, "检查更新",
                    f"当前 v{info.current_version} 已是最新版本。",
                )

    def _show_update_banner(self, info: UpdateInfo) -> None:
        """在状态栏上方贴一个非模态横幅,不打扰."""
        bar = getattr(self, "_update_banner", None)
        if bar is not None:
            bar.close()
            bar.deleteLater()
        from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton
        bar = QFrame(self)
        bar.setObjectName("UpdateBanner")
        bar.setStyleSheet(
            "QFrame#UpdateBanner { background:#3a3a00; border:1px solid #888800;"
            " border-radius:6px; padding:4px; }"
            "QLabel { color:#ffe; }"
            "QPushButton { color:#ffe; background:#666600; border:none;"
            " padding:4px 10px; border-radius:3px; }"
            "QPushButton:hover { background:#888800; }"
        )
        h = QHBoxLayout(bar)
        h.setContentsMargins(8, 4, 8, 4)
        h.setSpacing(8)
        lbl = QLabel(
            f"发现新版本 v{info.latest_version}"
            f"(当前 v{info.current_version})"
        )
        h.addWidget(lbl, stretch=1)
        btn_dl = QPushButton("去下载")
        btn_dl.clicked.connect(lambda: (webbrowser.open(info.release_url), bar.close()))
        h.addWidget(btn_dl)
        btn_close = QPushButton("✕")
        btn_close.setFixedWidth(28)
        btn_close.clicked.connect(bar.close)
        h.addWidget(btn_close)
        # 插到主布局顶部
        layout = self.layout()
        if layout is not None:
            layout.insertWidget(0, bar)
        self._update_banner = bar
        bar.show()