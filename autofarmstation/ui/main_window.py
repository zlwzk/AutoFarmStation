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
from ..core import audio
from ..core import window_finder as wf
from ..core.game_launcher import (
    LaunchInfo, detect_launch_info, is_process_alive, kill_process, launch_async,
)
from ..utils import config as cfg_mod
from ..utils.logger import get
from ..utils.update_checker import check as check_update, UpdateInfo
from .preview_grid import PreviewGrid
from .action_panel import ActionPanel
from .process_selector_dialog import ProcessSelectorDialog
from .widgets import styled_message
from .bat_library_dialog import BatLibraryDialog
from .schedule_dialog import ScheduleDialog
from .volume_dialog import MasterVolumeDialog, SessionVolumeDialog
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
        self._sched.register_handler("launch_games", lambda t: self._launch_games_scheduled(t))
        self._sched.register_handler("kill_games", lambda t: self._kill_games_scheduled(t))
        self._sched.register_handler("start_clicker", lambda t: self._clicker_scheduled(t, True))
        self._sched.register_handler("stop_clicker", lambda t: self._clicker_scheduled(t, False))
        self._sched.register_handler("play_macro", lambda t: self._play_macro_scheduled(t))
        self._sched.register_handler("volume_low", lambda t: self._master_volume_set(0.2))
        self._sched.register_handler("volume_mute", lambda t: audio.set_master_mute(True))
        self._sched.register_handler("volume_restore", lambda t: self._master_volume_set(1.0))
        self._sched.register_handler("shutdown_app", lambda t: self.close())
        self._sched.start()
        self._monitor.start()

        # 唤醒缓存:hwnd → 启动信息(exe / Steam appid),用于「恢复进程」
        self._launch_cache: dict[int, LaunchInfo] = {}

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

        # 启动时套用保存的整体音量(可选)
        if self._cfg.get("audio.apply_master_on_start", False) and audio.available():
            try:
                mv = float(self._cfg.get("audio.master_volume", -1))
                if mv >= 0:
                    audio.set_master_volume(mv)
                    audio.set_master_mute(bool(self._cfg.get("audio.master_mute", False)))
            except (TypeError, ValueError):
                pass

        # 退出时保存
        self._saved = False

    # --- 关闭 ---
    def closeEvent(self, ev) -> None:
        close_games = bool(self._cfg.get("launch.close_games_on_exit", True))
        pids = self._tracked_pids()
        if self._cfg.get("ui.confirm_exit", True):
            msg = "确认退出 多开挂机大师?所有运行中的连点器/键盘宏会停止。"
            if close_games and pids:
                msg += (
                    f"\n\n⚠ 按当前设置(设置 → 游戏进程),还会一并结束 "
                    f"{len(pids)} 个被追踪的游戏进程。"
                )
            r = QMessageBox.question(self, "退出", msg)
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
        # 关闭被追踪的游戏进程(可在设置里关掉)
        if close_games and pids:
            n = self._kill_pids(pids, reason="退出软件")
            self._log.info("退出时结束了 %d/%d 个游戏进程", n, len(pids))
        super().closeEvent(ev)

    # --- 进程结束 / 唤醒 ---
    def _tracked_pids(self) -> list[int]:
        out: list[int] = []
        for tp in self._pm.all():
            pid = int(getattr(tp, "pid", 0) or 0)
            if pid and is_process_alive(pid):
                out.append(pid)
        return out

    def _kill_pids(self, pids, *, reason: str = "") -> int:
        tree = bool(self._cfg.get("launch.kill_tree", True))
        n = 0
        for pid in list(pids):
            try:
                ok, _msg = kill_process(int(pid), tree=tree)
            except Exception as e:  # noqa: BLE001
                self._log.warning("结束进程 %s 失败: %s", pid, e)
                continue
            if ok:
                n += 1
        if reason:
            self._log.info("已结束 %d/%d 个进程(%s)", n, len(list(pids)), reason)
        return n

    def _remember_launch(self, hwnd: int, pid: int = 0, title: str = "") -> None:
        """记下「怎么把该窗口的游戏重新拉起来」(供恢复进程用)."""
        if not pid:
            info = wf.get_window_info(hwnd)
            if info is None:
                return
            pid, title = int(info.pid or 0), title or info.title or ""
        if not pid:
            return
        try:
            li = detect_launch_info(pid, title=title)
        except Exception as e:  # noqa: BLE001
            self._log.debug("探测启动信息失败(%s): %s", pid, e)
            return
        if li is not None and li.usable:
            self._launch_cache[int(hwnd)] = li

    def _on_card_stop_process(self, hwnd: int) -> None:
        """卡片「■ 停止进程」."""
        info = wf.get_window_info(hwnd)
        pid = int(info.pid) if info else 0
        if not pid:
            QMessageBox.information(self, "停止进程", "窗口已失效,拿不到进程号。")
            return
        title = info.title if info else str(hwnd)
        r = QMessageBox.question(
            self, "停止进程",
            f"确认结束该游戏进程?\n\n{title}\nPID {pid}\n\n"
            "未保存的游戏进度会丢失。",
        )
        if r != QMessageBox.StandardButton.Yes:
            return
        self._remember_launch(hwnd, pid, title)
        tree = bool(self._cfg.get("launch.kill_tree", True))
        ok, msg = kill_process(pid, tree=tree)
        self._statusBar().showMessage(  # type: ignore[union-attr]
            f"[{pid}] {'已结束进程' if ok else msg}", 5000,
        )
        if not ok:
            QMessageBox.warning(self, "停止进程", msg)

    def _on_card_resume_process(self, hwnd: int, *, silent: bool = False) -> None:
        """卡片「▶ 恢复进程」:游戏没开 → 自动唤醒(Steam 游戏先开 Steam)."""
        li = self._launch_cache.get(int(hwnd))
        if li is None or not li.usable:
            # 退而求其次:从上次会话快照里找
            try:
                data = self._session.load() or {}
                for item in (data.get("items") or []):
                    info = Session.launch_info_of(item)
                    if info is not None:
                        li = info
                        break
            except Exception:  # noqa: BLE001
                pass
        if li is None or not li.usable:
            if silent:
                return
            QMessageBox.information(
                self, "恢复进程",
                "没有该窗口的启动信息,无法自动唤醒。\n\n"
                "下次在游戏运行时用「添加窗口」加一次,软件就会记住怎么启动它。",
            )
            return

        timeout = float(self._cfg.get("launch.steam_timeout_sec", 90))
        self._statusBar().showMessage(  # type: ignore[union-attr]
            f"正在唤醒 {li.describe()} ..."
            + ("(Steam 游戏,会先启动 Steam 客户端)" if li.steam_appid else ""),
            0,
        )

        def _done(ok: bool, msg: str) -> None:
            QTimer.singleShot(0, lambda: self._after_resume(hwnd, li, ok, msg, silent))

        launch_async(li, steam_timeout=timeout, on_done=_done)

    def _after_resume(
        self, hwnd: int, li: LaunchInfo, ok: bool, msg: str, silent: bool = False,
    ) -> None:
        self._statusBar().showMessage(
            f"[{hwnd}] {msg}", 6000,  # type: ignore[union-attr]
        )
        if not ok:
            if not silent:
                QMessageBox.warning(self, "唤醒失败", msg)
            return
        # 游戏起来后窗口 hwnd 会变,轮询等它出现再自动接回追踪列表
        self._watch_for_launched(int(hwnd), li, 0)

    def _pids_by_name(self, li: LaunchInfo) -> set[int]:
        """按 exe 名找出所有同名的运行中进程(判断游戏是否已经起来了)."""
        names = {str(li.name).lower()} if li.name else set()
        if not names:
            return set()
        try:
            import psutil  # type: ignore
        except Exception:  # noqa: BLE001
            return set()
        pids: set[int] = set()
        for proc in psutil.process_iter(["name"]):
            try:
                if str((proc.info or {}).get("name") or "").lower() in names:
                    pids.add(int(proc.pid))
            except Exception:  # noqa: BLE001
                continue
        return pids

    def _find_launched_window(self, li: LaunchInfo):
        pids = self._pids_by_name(li)
        if not pids:
            return None
        try:
            for win in wf.list_visible_windows():
                if win.pid in pids and __app_name_cn__ not in (win.title or ""):
                    return win
        except Exception:  # noqa: BLE001
            return None
        return None

    def _watch_for_launched(self, old_hwnd: int, li: LaunchInfo, tries: int) -> None:
        if tries >= 90:  # 最多等 ~3 分钟
            self._statusBar().showMessage(  # type: ignore[union-attr]
                "游戏启动较慢,窗口出现后请用「添加窗口」加回来(会自动套用原配置)", 8000,
            )
            return
        win = self._find_launched_window(li)
        if win is not None:
            self._rebind_window(old_hwnd, win)
            return
        QTimer.singleShot(2000, lambda: self._watch_for_launched(old_hwnd, li, tries + 1))

    def _rebind_window(self, old_hwnd: int, win) -> None:
        """游戏重启后窗口句柄变了:把配置搬到新句柄上."""
        try:
            all_cfg = self._hub.export_configs() or {}
        except Exception:  # noqa: BLE001
            all_cfg = {}
        old_cfg = all_cfg.get(str(int(old_hwnd))) or {}
        try:
            self._hub.forget(int(old_hwnd))
            self._pm.remove(int(old_hwnd))
        except Exception:  # noqa: BLE001
            pass
        tp = self._pm.add_by_hwnd(win.hwnd)
        if tp is None:
            return
        if old_cfg:
            try:
                self._hub.import_configs({str(int(win.hwnd)): old_cfg})
            except Exception as e:  # noqa: BLE001
                self._log.warning("迁移配置失败: %s", e)
        self._launch_cache.pop(int(old_hwnd), None)
        self._remember_launch(win.hwnd, tp.pid, tp.title)
        self._save_state()
        self._refresh_status()
        self._statusBar().showMessage(  # type: ignore[union-attr]
            f"已唤醒并接回:{win.title[:40]}", 6000,
        )

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
        a_sched = QAction("定时任务...", self)
        a_sched.setShortcut("Ctrl+T")
        a_sched.triggered.connect(self._on_open_schedule)
        menu_tool.addAction(a_sched)
        a_vol = QAction("整体音量...", self)
        a_vol.triggered.connect(self._on_open_master_volume)
        menu_tool.addAction(a_vol)
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
            card_size=(
                int(self._cfg.get("ui.card_min_width", 240)),
                int(self._cfg.get("ui.card_min_height", 190)),
            ),
            show_volume=bool(self._cfg.get("audio.show_volume_on_card", True)),
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
        # 卡片上的 停止进程 / 恢复进程 / 音量
        left.stop_process_requested.connect(self._on_card_stop_process)
        left.resume_process_requested.connect(self._on_card_resume_process)
        left.volume_requested.connect(self._on_card_volume)

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
                    # 记住怎么重新启动它 + 套用「新窗口默认音量」
                    self._remember_launch(int(tp.hwnd), int(tp.pid), tp.title)
                    self._apply_default_session_volume(int(tp.hwnd))
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

    # --- 音量 ---
    def _on_card_volume(self, hwnd: int) -> None:
        """卡片「♪」:调该窗口(游戏进程)的会话音量."""
        card = self._left.card(hwnd)
        pid = card.pid() if card is not None else 0
        if not pid:
            info = wf.get_window_info(hwnd)
            pid = int(info.pid) if info and info.pid else 0
        if not pid:
            QMessageBox.information(self, "窗口音量", "窗口已失效,拿不到进程号。")
            return
        info = wf.get_window_info(hwnd)
        title = (info.title if info else "") or str(hwnd)
        SessionVolumeDialog(int(pid), title, self).exec()

    def _on_open_master_volume(self) -> None:
        if not audio.available():
            QMessageBox.information(
                self, "整体音量",
                "音量功能不可用:当前环境缺少 pycaw 组件。\n"
                "源码运行请执行:pip install pycaw",
            )
            return
        dlg = MasterVolumeDialog(self)
        dlg.set_pids_provider(self._tracked_pids)
        dlg.exec()

    def _master_volume_set(self, v: float) -> None:
        if not audio.available():
            return
        audio.set_master_volume(float(v))
        self._statusBar().showMessage(  # type: ignore[union-attr]
            f"整体音量已设为 {int(round(float(v) * 100))}%", 3000,
        )

    def _apply_default_session_volume(self, hwnd: int) -> None:
        """新窗口加入时套用「新窗口默认音量」(默认关闭)."""
        if not audio.available():
            return
        if not self._cfg.get("audio.apply_session_on_add", False):
            return
        try:
            v = float(self._cfg.get("audio.default_session_volume", -1))
        except (TypeError, ValueError):
            return
        if v < 0:
            return
        info = wf.get_window_info(hwnd)
        if info is not None and info.pid:
            if audio.set_session_volume(int(info.pid), v):
                self._log.debug("已对 [%s] 套用默认音量 %d%%", hwnd, int(v * 100))

    # --- 定时任务 ---
    def _on_open_schedule(self) -> None:
        targets = [
            (int(tp.hwnd), tp.title or tp.name or str(tp.hwnd))
            for tp in self._pm.all()
        ]
        ScheduleDialog(self._sched, targets, self).exec()

    def _launch_games_scheduled(self, task) -> None:
        hwnd = int(getattr(task, "target_hwnd", 0) or 0)
        hwnds = [hwnd] if hwnd else [int(tp.hwnd) for tp in self._pm.all()]
        n = 0
        for h in hwnds:
            li = self._launch_cache.get(h)
            if li is None:
                info = wf.get_window_info(h)
                if info is not None:
                    self._remember_launch(h, info.pid, info.title)
                    li = self._launch_cache.get(h)
            if li is None or not li.usable:
                continue
            if self._find_launched_window(li) is not None:
                continue  # 已经在跑了
            self._on_card_resume_process(h, silent=True)  # 复用唤醒流程(异步)
            n += 1
        self._statusBar().showMessage(  # type: ignore[union-attr]
            f"定时任务:正在唤醒 {n} 个游戏", 4000,
        )

    def _kill_games_scheduled(self, task) -> None:
        hwnd = int(getattr(task, "target_hwnd", 0) or 0)
        if hwnd:
            info = wf.get_window_info(hwnd)
            pids = [int(info.pid)] if info is not None and info.pid else []
        else:
            pids = self._tracked_pids()
        if not pids:
            self._statusBar().showMessage("定时任务:没有需要结束的游戏进程", 4000)  # type: ignore[union-attr]
            return
        n = self._kill_pids(pids, reason="定时任务")
        self._statusBar().showMessage(  # type: ignore[union-attr]
            f"定时任务:已结束 {n}/{len(pids)} 个游戏进程", 4000,
        )

    def _clicker_scheduled(self, task, on: bool) -> None:
        hwnd = int(getattr(task, "target_hwnd", 0) or 0)
        if hwnd:
            hwnds = [hwnd]
        else:
            hwnds = [int(h) for h in (self._left.selected_hwnds() or self._left.all_hwnds())]
        if not hwnds:
            self._statusBar().showMessage("定时任务:没有可操作的窗口", 4000)  # type: ignore[union-attr]
            return
        if on:
            res = self._hub.start_many(hwnds, clicker=True, key_macro=False)
            self._statusBar().showMessage(f"定时任务:{res.message}", 4000)  # type: ignore[union-attr]
        else:
            try:
                res = self._hub.stop_many(hwnds)
                msg = res.message
            except Exception:  # noqa: BLE001
                for h in hwnds:
                    self._hub.stop(h)
                msg = f"已停止 {len(hwnds)} 个窗口"
            self._statusBar().showMessage(f"定时任务:{msg}", 4000)  # type: ignore[union-attr]

    def _play_macro_scheduled(self, task) -> None:
        hwnd = int(getattr(task, "target_hwnd", 0) or 0)
        if not hwnd:
            hwnds = self._left.selected_hwnds() or self._left.all_hwnds()
            if not hwnds:
                self._statusBar().showMessage("定时任务:没有可操作的窗口", 4000)  # type: ignore[union-attr]
                return
            hwnd = int(hwnds[0])
        if self._hub.start_key_macro(hwnd):
            self._statusBar().showMessage(f"定时任务:已启动键盘宏 [{hwnd}]", 4000)  # type: ignore[union-attr]
        else:
            self._statusBar().showMessage(  # type: ignore[union-attr]
                f"定时任务:键盘宏启动失败 [{hwnd}](该窗口可能还没配置按键)", 5000,
            )

    # --- 设置变更即时生效 ---
    def apply_settings_changes(self) -> None:
        """设置对话框点确定后,把卡片尺寸 / 音量按钮 / 刷新率立刻应用."""
        try:
            self._left.set_card_size(
                int(self._cfg.get("ui.card_min_width", 240)),
                int(self._cfg.get("ui.card_min_height", 190)),
            )
            self._left.set_volume_visible(
                bool(self._cfg.get("audio.show_volume_on_card", True))
            )
            if hasattr(self._left, "set_fps"):
                self._left.set_fps(float(self._cfg.get("ui.preview_fps", 1)))  # type: ignore[attr-defined]
            self._left.set_columns(int(self._cfg.get("ui.preview_columns", 0)))
            self._left.refresh_cards()
        except Exception as e:  # noqa: BLE001
            self._log.debug("应用卡片设置失败: %s", e)
        self._statusBar().showMessage("设置已生效", 2500)  # type: ignore[union-attr]

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
        # 刷新「怎么把游戏重新拉起来」的缓存(供退出关闭 / 下次唤醒使用)
        for tp in self._pm.all():
            if tp.pid and int(tp.hwnd) not in self._launch_cache:
                try:
                    self._remember_launch(int(tp.hwnd), int(tp.pid), tp.title)
                except Exception:  # noqa: BLE001
                    continue
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
            if missed and self._cfg.get("launch.auto_wake_on_restore", True):
                self._wake_missed(saved_items, pairs)
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
            self._remember_launch(int(w.hwnd), int(w.pid), w.title)
            self._apply_default_session_volume(int(w.hwnd))
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

    def _wake_missed(
        self,
        saved_items: list[dict],
        pairs: list[tuple[dict, wf.WindowInfo]],
    ) -> None:
        """上次有、现在没起来的游戏:按记录的启动信息自动唤醒.

        Steam 游戏会先唤起 Steam 客户端,再走 steam://rungameid。
        """
        missed = Session.missed_items(saved_items, pairs)
        if not missed:
            return
        infos = [
            li for li in (Session.launch_info_of(s) for s in missed)
            if li is not None and li.usable
        ]
        # 已经在跑的(可能窗口还没建好)不要重复启动
        todo = [li for li in infos if not self._pids_by_name(li)]
        if not todo:
            return
        steam_n = sum(1 for li in todo if li.steam_appid)
        self._log.info(
            "恢复会话:唤醒 %d 个未启动的游戏(其中 Steam 游戏 %d 个)", len(todo), steam_n,
        )
        self._statusBar().showMessage(  # type: ignore[union-attr]
            f"正在唤醒 {len(todo)} 个未启动的游戏"
            + (f"(其中 {steam_n} 个 Steam 游戏会先启动 Steam 客户端)" if steam_n else ""),
            8000,
        )
        timeout = float(self._cfg.get("launch.steam_timeout_sec", 90))
        for li in todo:
            launch_async(
                li,
                steam_timeout=timeout,
                on_done=lambda ok, msg, _li=li: QTimer.singleShot(
                    0, lambda: self._after_wake(_li, ok, msg)
                ),
            )

    def _after_wake(self, li: LaunchInfo, ok: bool, msg: str) -> None:
        self._statusBar().showMessage(  # type: ignore[union-attr]
            f"[{li.describe()}] {msg}", 6000,
        )
        if ok:
            self._log.info("已唤醒 %s:%s", li.describe(), msg)
        else:
            self._log.warning("唤醒 %s 失败:%s", li.describe(), msg)

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