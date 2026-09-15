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
from .volume_dialog import GroupVolumeDialog, SessionVolumeDialog
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
        self._monitor = ProcessMonitor(
            self._pm,
            logger=self._log,
            resource_alert=bool(self._cfg.get("monitor.resource_alert_enabled", True)),
            cpu_alert_pct=float(self._cfg.get("monitor.cpu_alert_pct", 90)),
            mem_alert_mb=float(self._cfg.get("monitor.mem_alert_mb", 4096)),
        )
        # 监控事件(掉线/占用超阈值)必须有监听者才有意义 ——
        # 这里挂上去,顺便让「自动重启」真正生效。
        self._monitor.on_event(self._on_monitor_event)
        # 自动重启节流:hwnd → 最近几次重启的时间戳
        self._restart_hist: dict[int, list[float]] = {}
        # v1.6.1:「选择窗口」对话框是否列出隐藏 / 无标题窗口
        self._include_hidden: bool = False
        # v1.6.2:挂机时段守护(时段外禁止启动;定时轮询进出)
        from ..core.farm_window import FarmWindowGuard
        self._farm_guard = FarmWindowGuard(self._cfg)
        self._farm_was_in_window: bool = self._farm_guard.in_window()
        self._farm_paused: set[int] = set()  # 退出时段时被停的 hwnd(action=pause 才用)
        self._farm_timer = QTimer(self)
        self._farm_timer.setInterval(30 * 1000)
        self._farm_timer.timeout.connect(self._check_farm_window)
        self._farm_timer.start()
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
        # 音量动作只作用于「已加入本软件的窗口」,绝不碰系统总音量 ——
        # 定时「静音」不该顺手把用户的 QQ / 视频也静掉。
        self._sched.register_handler("volume_low", lambda t: self._group_volume_set(0.2))
        self._sched.register_handler("volume_mute", lambda t: self._group_mute_set(True))
        self._sched.register_handler("volume_restore", lambda t: self._group_volume_restore())
        self._sched.register_handler("shutdown_app", lambda t: self.close())
        self._sched.start()
        self._monitor.start()

        # 唤醒缓存:hwnd → 启动信息(exe / Steam appid),用于「恢复进程」
        self._launch_cache: dict[int, LaunchInfo] = {}
        # 音量快照:pid → (音量, 静音)。定时任务「静音/降音量」前先记一份,
        # 「恢复音量」时还原成各自操作前的值,而不是一刀切 100%。
        self._volume_backup: dict[int, tuple[float, bool]] = {}

        # UI
        self.setWindowTitle(f"{__app_name_cn__} v{__version__}")
        self.resize(1400, 880)
        # v1.6.2:启动时最小化设置要被真正读一次。
        # resize 之后再 show + setWindowState 才能稳定生效,所以延迟到 showEvent。
        self._start_minimized_requested = bool(
            self._cfg.get("ui.start_minimized", False),
        )
        self._start_minimized_applied = False
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

        # 启动时套用保存的音量设置(可选)
        if audio.available():
            # 1) 已加入窗口的音量 —— 安全,默认就对
            if self._cfg.get("audio.apply_group_on_start", False):
                try:
                    gv = float(self._cfg.get("audio.group_volume", -1))
                except (TypeError, ValueError):
                    gv = -1.0
                pids = self._tracked_pids()
                if gv >= 0 and pids:
                    n = audio.set_volume_for_pids(pids, gv)
                    audio.set_mute_for_pids(pids, bool(self._cfg.get("audio.group_mute", False)))
                    self._log.info("启动套用窗口音量 %d%%:%d/%d 个", int(gv * 100), n, len(pids))
            # 2) 系统总音量 —— 必须显式打开才动,免得影响其它程序
            if self._cfg.get("audio.control_system_master", False):
                try:
                    mv = float(self._cfg.get("audio.system_master_volume", -1))
                    if mv >= 0:
                        audio.set_master_volume(mv)
                    audio.set_master_mute(bool(self._cfg.get("audio.system_master_mute", False)))
                except (TypeError, ValueError):
                    pass

        # 退出时保存
        self._saved = False

    # --- 显示:启动时最小化(只在第一次 show 触发一次) ---
    def showEvent(self, ev) -> None:  # noqa: D401
        super().showEvent(ev)
        if self._start_minimized_requested and not self._start_minimized_applied:
            self._start_minimized_applied = True
            try:
                self.setWindowState(self.windowState() | Qt.WindowState.WindowMinimized)
                self.showMinimized()
            except Exception:
                pass

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
        # 别名 / 强调色也跟着搬过去,不然自动重启后辛苦起的名就没了
        old_item = self._pm.get(int(old_hwnd))
        old_alias = (old_item.alias if old_item else "") or ""
        old_color = (old_item.color if old_item else "") or ""
        old_kinds = self._running_kinds(int(old_hwnd))
        try:
            self._hub.forget(int(old_hwnd))
            self._pm.remove(int(old_hwnd))
        except Exception:  # noqa: BLE001
            pass
        self._restart_hist.pop(int(old_hwnd), None)
        tp = self._pm.add_by_hwnd(win.hwnd)
        if tp is None:
            return
        if old_alias or old_color:
            self._pm.set_alias(int(win.hwnd), old_alias, old_color)
        if old_cfg:
            try:
                self._hub.import_configs({str(int(win.hwnd)): old_cfg})
            except Exception as e:  # noqa: BLE001
                self._log.warning("迁移配置失败: %s", e)
        self._launch_cache.pop(int(old_hwnd), None)
        self._remember_launch(win.hwnd, tp.pid, tp.title)
        # 掉线前正在跑的连点器 / 键盘宏接着跑,不用手动再开一遍
        restarted = self._restore_running_kinds(int(win.hwnd), old_kinds)
        self._save_state()
        self._refresh_status()
        note = f"(已自动接回{'、'.join(restarted)})" if restarted else ""
        self._statusBar().showMessage(  # type: ignore[union-attr]
            f"已唤醒并接回:{win.title[:40]}{note}", 6000,
        )
        self._notify(f"已自动接回游戏窗口:{win.title[:40]}")

    # --- 监控事件 ---
    def _running_kinds(self, hwnd: int) -> tuple[bool, bool]:
        """该窗口此刻是否在跑 (连点器, 键盘宏)."""
        try:
            return (
                bool(self._hub.is_clicker_running(int(hwnd))),
                bool(self._hub.is_key_macro_running(int(hwnd))),
            )
        except Exception:  # noqa: BLE001
            return (False, False)

    def _restore_running_kinds(self, hwnd: int, kinds: tuple[bool, bool]) -> list[str]:
        """把之前在这个窗口上跑着的自动化重新拉起来,返回恢复了的名字."""
        clicker, key_macro = kinds
        done: list[str] = []
        if clicker:
            try:
                self._hub.start_clicker(int(hwnd))
                done.append("连点")
            except Exception as e:  # noqa: BLE001
                self._log.warning("自动恢复连点失败: %s", e)
                done.append("连点失败")
        if key_macro:
            try:
                self._hub.start_key_macro(int(hwnd))
                done.append("键盘宏")
            except Exception as e:  # noqa: BLE001
                self._log.warning("自动恢复键盘宏失败: %s", e)
                done.append("键盘宏失败")
        return done

    def _on_monitor_event(self, ev: MonitorEvent) -> None:
        """监控线程发来的事件(可能在非 UI 线程,统一切回主线程处理)."""
        try:
            QTimer.singleShot(0, lambda: self._handle_monitor_event(ev))
        except Exception:  # noqa: BLE001
            pass

    def _handle_monitor_event(self, ev: MonitorEvent) -> None:
        item = self._pm.get(int(ev.hwnd))
        who = item.display_name() if item is not None else f"PID {ev.pid}"

        if ev.status is MonitorStatus.RESOURCE:
            self._statusBar().showMessage(f"⚠ {ev.message}", 8000)  # type: ignore[union-attr]
            self._notify(ev.message)
            self._log.warning("%s", ev.message)
            return

        if ev.status is MonitorStatus.MISSING:
            self._log.info("窗口失效:%s(%s)", who, ev.message)
            if self._cfg.get("launch.auto_restart", False):
                self._maybe_auto_restart(int(ev.hwnd), who)
            else:
                self._statusBar().showMessage(  # type: ignore[union-attr]
                    f"{who} 已退出(设置 → 游戏进程里可开启自动重启)", 6000,
                )
            return

        if ev.status is MonitorStatus.ERROR:
            self._log.warning("监控异常 %s:%s", who, ev.message)
            self._statusBar().showMessage(f"⚠ {ev.message}", 6000)  # type: ignore[union-attr]
            return

        if ev.status is MonitorStatus.MINIMIZED:
            self._log.debug("%s 被最小化:%s", who, ev.message)
            return

        self._log.debug("监控事件 %s:%s(%s)", ev.status, who, ev.message)

    def _maybe_auto_restart(self, hwnd: int, who: str) -> None:
        """窗口掉了 → 按记下的启动信息自动拉起来(有次数上限,防死循环)."""
        limit = max(1, int(self._cfg.get("launch.auto_restart_max", 3)))
        window = max(60.0, float(self._cfg.get("launch.auto_restart_window_sec", 600)))
        now = time.time()
        hist = [t for t in self._restart_hist.get(hwnd, []) if now - t < window]
        if len(hist) >= limit:
            self._log.warning("%s 在 %d 秒内已自动重启 %d 次,放弃", who, int(window), len(hist))
            self._statusBar().showMessage(  # type: ignore[union-attr]
                f"{who} 频繁掉线,已自动重启 {len(hist)} 次,停止重试(请看日志排查)", 10000,
            )
            self._notify(f"{who} 频繁掉线,已停止自动重启")
            return
        li = self._launch_cache.get(int(hwnd))
        if li is None or not li.usable:
            self._statusBar().showMessage(  # type: ignore[union-attr]
                f"{who} 已退出,但没有它的启动信息,无法自动重启", 6000,
            )
            return
        hist.append(now)
        self._restart_hist[hwnd] = hist
        self._log.info("自动重启 %s(第 %d/%d 次):%s", who, len(hist), limit, li.describe())
        self._statusBar().showMessage(  # type: ignore[union-attr]
            f"{who} 掉线,正在自动重启(第 {len(hist)}/{limit} 次)"
            + ("(Steam 游戏会先启动 Steam)" if li.steam_appid else ""),
            8000,
        )
        self._notify(f"{who} 掉线,正在自动重启")
        self._on_card_resume_process(int(hwnd), silent=True)

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
        a_add_by_pid = QAction("按 PID 添加...", self)
        a_add_by_pid.setToolTip("输入一个 PID(任务管理器能看到),强制把它的所有窗口加入追踪,适合主窗口被隐藏或无标题的游戏")
        a_add_by_pid.triggered.connect(self._on_add_by_pid)
        menu_proc.addAction(a_add_by_pid)
        a_add_by_name = QAction("按进程名添加...", self)
        a_add_by_name.setToolTip("输入 .exe 名(如 melvoridle.exe),把所有同名进程的窗口都加入追踪")
        a_add_by_name.triggered.connect(self._on_add_by_name)
        menu_proc.addAction(a_add_by_name)
        a_include_hidden = QAction("包含隐藏窗口", self)
        a_include_hidden.setCheckable(True)
        a_include_hidden.setChecked(self._include_hidden)
        a_include_hidden.setToolTip("勾选后,「添加窗口」对话框会列出隐藏 / 无标题的窗口")
        a_include_hidden.triggered.connect(self._on_toggle_include_hidden)
        menu_proc.addAction(a_include_hidden)
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
        a_vol = QAction("全部窗口音量...", self)
        a_vol.setToolTip("只调已加入本软件的窗口,不影响其它程序")
        a_vol.triggered.connect(self._on_open_group_volume)
        menu_tool.addAction(a_vol)
        menu_tool.addSeparator()
        a_steam = QAction("还原 Steam 在线状态", self)
        a_steam.triggered.connect(self._on_restore_steam)
        menu_tool.addAction(a_steam)
        a_update = QAction("检查更新", self)
        a_update.triggered.connect(lambda: self._do_update_check(show_dialog=True))
        menu_tool.addAction(a_update)
        a_log = QAction("查看日志...", self)
        a_log.setToolTip("在界面里直接看日志,可过滤 / 复制 / 导出")
        a_log.triggered.connect(self._on_view_log)
        menu_tool.addAction(a_log)
        a_log_dir = QAction("打开日志目录", self)
        a_log_dir.triggered.connect(self._on_open_log_dir)
        menu_tool.addAction(a_log_dir)
        menu_tool.addSeparator()
        a_backup = QAction("导出配置备份...", self)
        a_backup.setToolTip("设置 / 窗口列表 / 预设 / 宏 / 定时任务打包成 zip")
        a_backup.triggered.connect(self._on_export_backup)
        menu_tool.addAction(a_backup)
        a_restore = QAction("导入配置备份...", self)
        a_restore.triggered.connect(self._on_import_backup)
        menu_tool.addAction(a_restore)

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
            self._pm, self._sched, self._stats, self._presets, hub=self._hub, cfg=self._cfg,
        )

        # v1.6.2:挂机时段守护 — 把「是否允许启动」检查注入到连点器
        right._clicker_panel._start_blocked_cb = self._farm_block_reason  # type: ignore[attr-defined]

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

    def _statusBar(self) -> QStatusBar:
        """返回状态栏(不存在则创建).

        历史遗留:主窗口里有几十处 ``self._statusBar().showMessage(...)``
        把状态栏当快捷方法用,但 ``QMainWindow`` 只有 ``statusBar()``,
        这个方法从未被定义过 —— 于是每一条状态栏提示都在抛
        ``AttributeError``,表现为「点了按钮什么都没提示」。这里补上实现,
        顺便兜底状态栏尚未建立的情况。
        """
        sb = self.statusBar()
        if sb is None:
            sb = QStatusBar()
            self.setStatusBar(sb)
        return sb

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
        dlg = ProcessSelectorDialog(self, include_hidden=self._include_hidden)
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

    def _on_add_by_pid(self) -> None:
        """按 PID 强制添加(支持主窗口被隐藏 / 无标题的游戏)。"""
        from PySide6.QtWidgets import QInputDialog
        text, ok = QInputDialog.getText(
            self,
            "按 PID 添加",
            "输入要添加的进程 PID(整数):",
        )
        if not ok:
            return
        s = str(text).strip()
        if not s.isdigit():
            self._statusBar().showMessage(
                "请输入整数 PID", 3000,
            )
            return
        pid = int(s)
        added = self._pm.add_by_pid_force(pid)
        if not added:
            self._statusBar().showMessage(
                f"PID {pid} 下没有可用的顶层窗口", 3000,
            )
            return
        self._after_bulk_add(added)

    def _on_add_by_name(self) -> None:
        """按进程名添加(把同名进程的所有窗口都加入追踪)。"""
        from PySide6.QtWidgets import QInputDialog
        text, ok = QInputDialog.getText(
            self,
            "按进程名添加",
            "输入进程名(.exe),如 melvoridle.exe:",
        )
        if not ok:
            return
        name = str(text).strip()
        if not name:
            self._statusBar().showMessage("请输入进程名", 3000)
            return
        added = self._pm.add_by_process_name(name)
        if not added:
            self._statusBar().showMessage(
                f"没找到名为 {name} 的进程,或它们都无可用窗口", 3000,
            )
            return
        self._after_bulk_add(added)

    def _on_toggle_include_hidden(self, checked: bool) -> None:
        self._include_hidden = bool(checked)
        self._statusBar().showMessage(
            ("「选择窗口」对话框将包含隐藏 / 无标题窗口" if checked
             else "「选择窗口」对话框只列可见且有标题的窗口"),
            3000,
        )

    def _after_bulk_add(self, added: list) -> None:
        """按 PID / 按名添加的统一收尾:匹配预设 + 保存 + 状态提示。"""
        for tp in added:
            auto_preset = self._presets.find_match(
                process_name=tp.name, title=tp.title,
            )
            if auto_preset:
                self._log.info(
                    "为 %s 自动匹配预设:%s", tp.title, auto_preset.name,
                )
            self._remember_launch(int(tp.hwnd), int(tp.pid), tp.title)
            self._apply_default_session_volume(int(tp.hwnd))
        self._save_state()
        self._statusBar().showMessage(
            f"已添加 {len(added)} 个窗口", 3000,
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

    def _on_open_group_volume(self) -> None:
        if not audio.available():
            QMessageBox.information(
                self, "全部窗口音量",
                "音量功能不可用:当前环境缺少 pycaw 组件。\n"
                "源码运行请执行:pip install pycaw",
            )
            return
        dlg = GroupVolumeDialog(self._tracked_pids, self._cfg, self)
        dlg.exec()
        try:
            self._cfg.save()
        except Exception:  # noqa: BLE001
            pass
        self._left.refresh_cards()

    def _group_volume_set(self, v: float) -> int:
        """把所有「已加入窗口」的会话音量设为 v(不动系统总音量)."""
        if not audio.available():
            return 0
        pids = self._tracked_pids()
        n = audio.set_volume_for_pids(pids, float(v))
        self._statusBar().showMessage(  # type: ignore[union-attr]
            f"已加入窗口音量 → {int(round(float(v) * 100))}%({n}/{len(pids)} 个窗口)", 4000,
        )
        return n

    def _group_mute_set(self, on: bool, *, snapshot: bool = True) -> int:
        """把所有「已加入窗口」静音 / 取消静音(不影响其它程序)."""
        if not audio.available():
            return 0
        pids = self._tracked_pids()
        if on and snapshot and not self._volume_backup:
            self._volume_backup = audio.snapshot_for_pids(pids)
        n = audio.set_mute_for_pids(pids, bool(on))
        tail = "(没生效的是此刻没在发声的窗口)" if n < len(pids) else ""
        self._statusBar().showMessage(  # type: ignore[union-attr]
            f"已{'静音' if on else '取消静音'} {n}/{len(pids)} 个窗口{tail}", 4000,
        )
        return n

    def _group_volume_restore(self) -> None:
        """还原「静音 / 降音量」之前的音量;没有快照就退回取消静音 + 100%."""
        if not audio.available():
            return
        if self._volume_backup:
            n = audio.restore_for_pids(self._volume_backup)
            self._volume_backup = {}
            self._statusBar().showMessage(  # type: ignore[union-attr]
                f"已还原 {n} 个窗口到操作前的音量", 4000,
            )
            return
        pids = self._tracked_pids()
        n = audio.set_mute_for_pids(pids, False)
        audio.set_volume_for_pids(pids, 1.0)
        self._statusBar().showMessage(  # type: ignore[union-attr]
            f"已取消静音并恢复 100%({n}/{len(pids)} 个窗口)", 4000,
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
    # --- 挂机时段守护 ---
    def _farm_block_reason(self) -> str | None:
        """时段外:返回提示文案;时段内或未启用:返回 None."""
        if not self._farm_guard.enabled:
            return None
        if self._farm_guard.in_window():
            return None
        s = self._cfg.get("farm_window.start", "22:00")
        e = self._cfg.get("farm_window.end", "08:00")
        act = self._farm_guard.action()
        msg = f"当前不在挂机时段({s} ~ {e}),不允许启动。"
        if act == "pause":
            msg += " 回到时段后可手动或自动恢复。"
        return msg

    def _check_farm_window(self) -> None:
        """每 30 秒查一次:在时段 → 出时段 → 在时段 的状态翻转."""
        cur = self._farm_guard.in_window()
        if cur == self._farm_was_in_window:
            return
        self._farm_was_in_window = cur
        if not cur:
            # 退出时段:停下当前所有正在跑的(并记下来供进入时恢复)
            try:
                running = self._hub.running_hwnds() if hasattr(self._hub, "running_hwnds") else []
            except Exception:
                running = []
            if running:
                self._farm_paused.update(int(h) for h in running)
                try:
                    self._hub.stop_all() if hasattr(self._hub, "stop_all") else self._hub.stop_many(running)
                except Exception:
                    pass
                if self._farm_guard.action() == "pause":
                    self._log.info(
                        "挂机时段已退出:暂停 %d 个窗口的自动化(%s → %s)",
                        len(running),
                        self._cfg.get("farm_window.start", "22:00"),
                        self._cfg.get("farm_window.end", "08:00"),
                    )
                    self._statusBar().showMessage(  # type: ignore[union-attr]
                        f"挂机时段外:已暂停 {len(running)} 个窗口", 4000,
                    )
                else:
                    self._log.info(
                        "挂机时段已退出:停止 %d 个窗口的自动化(action=stop)", len(running),
                    )
                    self._statusBar().showMessage(  # type: ignore[union-attr]
                        f"挂机时段外:已停止 {len(running)} 个窗口", 4000,
                    )
        else:
            # 回到时段:如果之前是 pause,清空暂停列表(让用户决定要不要重启,
            # 不自动重启避免「不明窗口突然开始动」的惊吓)
            if self._farm_paused:
                self._log.info(
                    "回到挂机时段:之前暂停的 %d 个窗口可手动重新启动",
                    len(self._farm_paused),
                )
                self._statusBar().showMessage(  # type: ignore[union-attr]
                    f"回到挂机时段:{len(self._farm_paused)} 个之前暂停的窗口待启动", 4000,
                )
            self._farm_paused.clear()

    def _on_hotkey_start_all(self) -> None:
        """启动全部:优先启动「有勾选则勾选,否则全部追踪窗口」的已配置项."""
        # v1.6.2:挂机时段守护 — 时段外拒绝启动
        reason = self._farm_block_reason()
        if reason:
            self._statusBar().showMessage(reason, 3500)  # type: ignore[union-attr]
            return
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

    def _on_view_log(self) -> None:
        """在界面里看日志(不用再去翻目录)."""
        from .log_viewer_dialog import LogViewerDialog
        dlg = LogViewerDialog(self)
        dlg.exec()

    def _on_open_log_dir(self) -> None:
        from ..utils.paths import user_log_dir
        from ..utils.sanitize import user_log_dir_display
        path = user_log_dir()
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        self._statusBar().showMessage(  # type: ignore[union-attr]
            f"日志目录:{user_log_dir_display()}", 4000,
        )

    def _on_export_backup(self) -> None:
        from ..utils import backup
        from ..utils.paths import user_data_dir
        dest, _sel = QFileDialog.getSaveFileName(
            self, "导出配置备份", str(user_data_dir() / backup.default_name()),
            "备份文件 (*.zip)",
        )
        if not dest:
            return
        self._save_state()
        ok, msg = backup.export_to(dest)
        (QMessageBox.information if ok else QMessageBox.warning)(self, "导出配置备份", msg)

    def _on_import_backup(self) -> None:
        from ..utils import backup
        src, _sel = QFileDialog.getOpenFileName(
            self, "导入配置备份", "", "备份文件 (*.zip)",
        )
        if not src:
            return
        info = backup.describe(src)
        r = QMessageBox.question(
            self, "导入配置备份",
            f"{info}\n\n导入会覆盖当前设置(含窗口列表、预设、宏、定时任务)。\n"
            "当前数据会自动备份到 backups\\ 目录,可随时退回。\n\n是否继续?",
        )
        if r != QMessageBox.StandardButton.Yes:
            return
        self._save_state()
        ok, msg = backup.import_from(src)
        if not ok:
            QMessageBox.warning(self, "导入配置备份", msg)
            return
        QMessageBox.information(
            self, "导入配置备份",
            msg + "\n\n建议现在重开软件,确保所有页面都按新配置加载。",
        )

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