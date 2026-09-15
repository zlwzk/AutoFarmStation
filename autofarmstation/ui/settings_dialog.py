"""设置对话框.

分组:
  界面 · 预览与窗口尺寸 · 默认连点参数 · 音量 ·
  游戏进程 · Steam 状态联动与叠加层 · 更新与日志 · 数据位置
"""

from __future__ import annotations

import datetime
import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QGroupBox,
    QCheckBox, QComboBox, QLineEdit, QDialogButtonBox, QPushButton, QLabel,
    QMessageBox, QDoubleSpinBox, QSpinBox, QSlider, QScrollArea, QWidget,
)

from ..core import audio
from ..utils.config import Config
from ..utils.paths import steam_config_path
from ..core.steam_overlay import (
    describe as steam_overlay_describe,
    set_overlay as steam_overlay_set,
    restore_backup as steam_overlay_restore,
)
from ..core.steam_status import (
    SteamStatusController, SUPPORTED_STATES, STATE_ONLINE, state_label,
)

_log = logging.getLogger("autofarmstation.settings")


class SettingsDialog(QDialog):
    def __init__(self, cfg: Config, parent=None) -> None:
        super().__init__(parent)
        self._cfg = cfg
        self.setWindowTitle("设置")
        self.resize(700, 820)
        self.setMinimumWidth(620)

        outer = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        inner = QWidget()
        v = QVBoxLayout(inner)
        v.setContentsMargins(4, 4, 4, 4)

        self._build_ui_group(v)
        self._build_preview_group(v)
        self._build_defaults_group(v)
        self._build_audio_group(v)
        self._build_game_group(v)
        self._build_steam_group(v)
        self._build_update_group(v)
        self._build_paths_group(v)
        v.addStretch()

        scroll.setWidget(inner)
        outer.addWidget(scroll, stretch=1)

        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        bb.accepted.connect(self._on_accept)
        bb.rejected.connect(self.reject)
        outer.addWidget(bb)

        self._steam = SteamStatusController(cfg)
        self._refresh_steam_info()
        self._refresh_overlay_status()

    # ===== 各组 =====
    def _build_ui_group(self, parent_layout: QVBoxLayout) -> None:
        box = QGroupBox("界面")
        uf = QFormLayout(box)
        self._theme = QComboBox()
        self._theme.addItems(["dark 深色", "light 浅色"])
        idx = 0 if self._cfg.get("ui.theme", "dark") == "dark" else 1
        self._theme.setCurrentIndex(idx)
        uf.addRow("主题:", self._theme)

        self._language = QComboBox()
        self._language.addItems(["zh-CN 简体中文", "en-US English(占位)"])
        idx = 0 if self._cfg.get("ui.language", "zh-CN") == "zh-CN" else 1
        self._language.setCurrentIndex(idx)
        uf.addRow("语言:", self._language)

        self._confirm_exit = QCheckBox("退出前确认")
        self._confirm_exit.setChecked(self._cfg.get("ui.confirm_exit", True))
        uf.addRow("", self._confirm_exit)

        self._start_minimized = QCheckBox("启动时最小化窗口(到任务栏,不退出)")
        self._start_minimized.setChecked(self._cfg.get("ui.start_minimized", False))
        uf.addRow("", self._start_minimized)

        self._restore_ask = QCheckBox("启动时弹框询问「是否恢复上次的窗口」")
        self._restore_ask.setChecked(self._cfg.get("ui.restore_session_ask", True))
        uf.addRow("", self._restore_ask)

        parent_layout.addWidget(box)

    def _build_preview_group(self, parent_layout: QVBoxLayout) -> None:
        box = QGroupBox("预览")
        uf = QFormLayout(box)
        self._preview_fps = QComboBox()
        self._preview_fps.addItems(["0.5", "1", "2", "3", "5", "10"])
        cur = str(self._cfg.get("ui.preview_fps", 1))
        if cur not in [self._preview_fps.itemText(i) for i in range(self._preview_fps.count())]:
            cur = "1"
        self._preview_fps.setCurrentText(cur)
        uf.addRow("预览刷新频率(每秒):", self._preview_fps)

        self._preview_cols = QComboBox()
        self._preview_cols.addItems(["自适应", "1", "2", "3", "4", "6"])
        cols = int(self._cfg.get("ui.preview_columns", 0))
        labels = [self._preview_cols.itemText(i) for i in range(self._preview_cols.count())]
        if str(cols) in labels:
            self._preview_cols.setCurrentText(str(cols))
        else:
            self._preview_cols.setCurrentText("自适应")
        uf.addRow("默认布局列数:", self._preview_cols)

        # --- 窗口(卡片)尺寸:用户自行调节 ---
        size_row = QHBoxLayout()
        self._card_w = QSpinBox()
        self._card_w.setRange(160, 800)
        self._card_w.setSingleStep(10)
        self._card_w.setSuffix(" px")
        self._card_w.setValue(int(self._cfg.get("ui.card_min_width", 240)))
        size_row.addWidget(self._card_w)
        size_row.addWidget(QLabel("×"))
        self._card_h = QSpinBox()
        self._card_h.setRange(120, 600)
        self._card_h.setSingleStep(10)
        self._card_h.setSuffix(" px")
        self._card_h.setValue(int(self._cfg.get("ui.card_min_height", 190)))
        size_row.addWidget(self._card_h)
        size_row.addStretch()
        uf.addRow("卡片大小(宽 × 高):", size_row)

        size_note = QLabel(
            "每张游戏窗口预览卡片的最小尺寸。数值越大,一行能放的窗口越少,"
            "但画面和按钮都更清楚。点确定后立刻生效。"
        )
        size_note.setStyleSheet("color:#888;")
        size_note.setWordWrap(True)
        uf.addRow(size_note)

        self._focus_fit = QCheckBox("点「聚焦」时把游戏窗口铺满所在显示器工作区")
        self._focus_fit.setChecked(self._cfg.get("ui.focus_fit_screen", True))
        uf.addRow("", self._focus_fit)
        fit_note = QLabel(
            "开启后聚焦(或双击预览画面)会让游戏窗口完整铺满屏幕可用区域,"
            "确保整个界面都看得见 —— 不会被任务栏、屏幕边缘切掉一部分。\n"
            "关闭则只把窗口提到最前面,不改尺寸。"
        )
        fit_note.setStyleSheet("color:#888;")
        fit_note.setWordWrap(True)
        uf.addRow(fit_note)
        parent_layout.addWidget(box)

    def _build_audio_group(self, parent_layout: QVBoxLayout) -> None:
        """音量:整体音量 + 单窗口音量."""
        box = QGroupBox("音量")
        af = QFormLayout(box)

        if not audio.available():
            warn = QLabel(
                "音量功能不可用:当前环境缺少 pycaw 组件(打包版已内置)。\n"
                "源码运行时执行:pip install pycaw"
            )
            warn.setStyleSheet("color:#e08080;")
            warn.setWordWrap(True)
            af.addRow(warn)
            parent_layout.addWidget(box)
            return

        # 整体音量
        row = QHBoxLayout()
        self._master_vol = QSlider(Qt.Orientation.Horizontal)
        self._master_vol.setRange(0, 100)
        mv = audio.get_master_volume()
        self._master_vol.setValue(int(round((mv if mv is not None else 1.0) * 100)))
        self._master_vol.valueChanged.connect(self._on_master_volume_changed)
        row.addWidget(self._master_vol, stretch=1)
        self._master_lab = QLabel(f"{self._master_vol.value()}%")
        self._master_lab.setFixedWidth(48)
        row.addWidget(self._master_lab)
        af.addRow("整体音量:", row)

        self._master_mute = QCheckBox("静音")
        self._master_mute.setChecked(bool(audio.get_master_mute()))
        self._master_mute.toggled.connect(lambda on: audio.set_master_mute(on))
        af.addRow("", self._master_mute)

        self._apply_master_on_start = QCheckBox("启动本软件时把整体音量设成上面的值")
        self._apply_master_on_start.setChecked(self._cfg.get("audio.apply_master_on_start", False))
        af.addRow("", self._apply_master_on_start)

        row_all = QHBoxLayout()
        btn_apply_all = QPushButton("把该音量应用到所有追踪窗口")
        btn_apply_all.setToolTip("按窗口设置,不动系统整体音量")
        btn_apply_all.clicked.connect(self._on_apply_volume_to_all)
        row_all.addWidget(btn_apply_all)
        row_all.addStretch()
        af.addRow("", row_all)

        sep = QLabel("— — — — — — — — — — — — — — — — — — —")
        sep.setStyleSheet("color:#666;")
        af.addRow(sep)

        self._show_volume_on_card = QCheckBox("在每张窗口卡片上显示 ♪ 音量按钮")
        self._show_volume_on_card.setChecked(self._cfg.get("audio.show_volume_on_card", True))
        af.addRow("", self._show_volume_on_card)

        row_def = QHBoxLayout()
        self._def_session_vol = QSlider(Qt.Orientation.Horizontal)
        self._def_session_vol.setRange(0, 100)
        dv = int(self._cfg.get("audio.default_session_volume", -1))
        self._def_session_vol.setValue(dv if dv >= 0 else 100)
        self._def_session_vol.valueChanged.connect(
            lambda v: self._def_session_lab.setText(f"{v}%")
        )
        row_def.addWidget(self._def_session_vol, stretch=1)
        self._def_session_lab = QLabel(f"{self._def_session_vol.value()}%")
        self._def_session_lab.setFixedWidth(48)
        row_def.addWidget(self._def_session_lab)
        af.addRow("新窗口默认音量:", row_def)

        self._apply_session_on_add = QCheckBox("新添加窗口时自动套用上面的音量")
        self._apply_session_on_add.setChecked(self._cfg.get("audio.apply_session_on_add", False))
        af.addRow("", self._apply_session_on_add)

        note = QLabel(
            "说明:整体音量 = 任务栏音量条(影响所有程序);\n"
            "单窗口音量 = Windows「音量合成器」里那一栏(只影响该游戏)。\n"
            "进程还没发出声音时读不到它的会话音量,此时拖动滑块会在它出声后仍生效。"
        )
        note.setStyleSheet("color:#888;")
        note.setWordWrap(True)
        af.addRow(note)
        parent_layout.addWidget(box)

    def _build_game_group(self, parent_layout: QVBoxLayout) -> None:
        """游戏进程的唤醒 / 结束策略."""
        box = QGroupBox("游戏进程")
        gf = QFormLayout(box)

        self._auto_wake = QCheckBox("恢复上次会话时,游戏没打开就自动唤醒")
        self._auto_wake.setChecked(self._cfg.get("launch.auto_wake_on_restore", True))
        gf.addRow("", self._auto_wake)

        wake_note = QLabel(
            "Steam 游戏会先唤起 Steam 客户端,等它启动完成后再通过 "
            "steam://rungameid/<AppID> 拉起游戏;非 Steam 程序直接运行原来的 exe。"
        )
        wake_note.setStyleSheet("color:#888;")
        wake_note.setWordWrap(True)
        gf.addRow(wake_note)

        self._close_games = QCheckBox("关闭本软件时,同时结束被追踪的游戏进程")
        self._close_games.setChecked(self._cfg.get("launch.close_games_on_exit", True))
        gf.addRow("", self._close_games)

        warn = QLabel(
            "⚠ 打开后,退出软件会一并结束这些游戏(含未保存进度),请谨慎使用。\n"
            "只对「本软件当前追踪中的窗口」生效,不会去动其它程序。"
        )
        warn.setStyleSheet("color:#e0a080;")
        warn.setWordWrap(True)
        gf.addRow(warn)

        self._kill_tree = QCheckBox("结束进程时连同子进程")
        self._kill_tree.setChecked(self._cfg.get("launch.kill_tree", True))
        gf.addRow("", self._kill_tree)

        self._steam_timeout = QSpinBox()
        self._steam_timeout.setRange(10, 600)
        self._steam_timeout.setSuffix(" 秒")
        self._steam_timeout.setValue(int(self._cfg.get("launch.steam_timeout_sec", 90)))
        gf.addRow("等待 Steam 启动超时:", self._steam_timeout)

        parent_layout.addWidget(box)

    def _build_defaults_group(self, parent_layout: QVBoxLayout) -> None:
        """新建追踪窗口时,自动给连点器填充的默认参数."""
        box = QGroupBox("默认连点参数(新建窗口时使用)")
        uf = QFormLayout(box)
        self._def_interval = QSpinBox()
        self._def_interval.setRange(10, 60000)
        self._def_interval.setSuffix(" ms")
        self._def_interval.setValue(int(self._cfg.get("defaults.clicker_interval_ms", 200)))
        uf.addRow("点击间隔:", self._def_interval)

        self._def_jitter = QSpinBox()
        self._def_jitter.setRange(0, 5000)
        self._def_jitter.setSuffix(" ms")
        self._def_jitter.setValue(int(self._cfg.get("defaults.clicker_jitter_ms", 30)))
        uf.addRow("± 抖动:", self._def_jitter)

        self._def_button = QComboBox()
        self._def_button.addItems(["left 左键", "right 右键", "middle 中键"])
        cur = str(self._cfg.get("defaults.clicker_button", "left"))
        labels = [self._def_button.itemText(i) for i in range(self._def_button.count())]
        if cur == "right":
            self._def_button.setCurrentIndex(1)
        elif cur == "middle":
            self._def_button.setCurrentIndex(2)
        else:
            self._def_button.setCurrentIndex(0)
        uf.addRow("按键:", self._def_button)

        self._def_mode = QComboBox()
        self._def_mode.addItems(["fixed 固定间隔", "random 随机范围", "hold 按住连点"])
        cur = str(self._cfg.get("defaults.clicker_mode", "fixed"))
        if cur == "random":
            self._def_mode.setCurrentIndex(1)
        elif cur == "hold":
            self._def_mode.setCurrentIndex(2)
        else:
            self._def_mode.setCurrentIndex(0)
        uf.addRow("模式:", self._def_mode)

        note = QLabel(
            "提示:这些只是新建窗口时的初始值,已添加窗口不受影响 —— "
            "想改的话去对应窗口的连点器面板里调。"
        )
        note.setStyleSheet("color:#888;")
        note.setWordWrap(True)
        uf.addRow(note)
        parent_layout.addWidget(box)

    def _build_steam_group(self, parent_layout: QVBoxLayout) -> None:
        box = QGroupBox("Steam 状态联动 & 叠加层")
        sf = QFormLayout(box)

        self._steam_enabled = QCheckBox("挂机时自动切换 Steam 在线状态")
        self._steam_enabled.setChecked(self._cfg.get("steam.enabled", False))
        sf.addRow("", self._steam_enabled)

        self._steam_state = QComboBox()
        for st in SUPPORTED_STATES:
            self._steam_state.addItem(state_label(st), st)
        cur = str(self._cfg.get("steam.farm_state", STATE_ONLINE) or STATE_ONLINE)
        if cur not in SUPPORTED_STATES:
            cur = STATE_ONLINE
        self._steam_state.setCurrentIndex(SUPPORTED_STATES.index(cur))
        sf.addRow("挂机时切到:", self._steam_state)

        self._steam_restore = QCheckBox("挂机结束后还原成挂机前的状态")
        self._steam_restore.setChecked(self._cfg.get("steam.restore_previous", True))
        sf.addRow("", self._steam_restore)

        self._steam_verify = QCheckBox("切换后回读本地配置确认是否生效")
        self._steam_verify.setChecked(self._cfg.get("steam.verify", True))
        sf.addRow("", self._steam_verify)

        rowh = QHBoxLayout()
        self._steam_info = QLabel("")
        self._steam_info.setStyleSheet("color:#888;")
        rowh.addWidget(self._steam_info, stretch=1)
        btn_refresh = QPushButton("刷新")
        btn_refresh.clicked.connect(self._refresh_steam_info)
        rowh.addWidget(btn_refresh)
        sf.addRow("当前 Steam 状态:", rowh)

        sep = QLabel("— — — — — — — — — — — — — — — — — — —")
        sep.setStyleSheet("color:#666;")
        sf.addRow(sep)

        self._disable_overlay = QCheckBox(
            "禁用 Steam 叠加层(Shift+Tab 不再唤起)\n"
            "实现:写 %APPDATA%\\Steam\\steam.cfg → SteamOverlay=0;"
            "运行中游戏不受影响,需重启 Steam 客户端后对新启动的游戏生效。"
        )
        self._disable_overlay.setChecked(self._cfg.get("ui.disable_steam_overlay", False))
        self._disable_overlay.toggled.connect(self._refresh_overlay_status)
        sf.addRow("", self._disable_overlay)

        row_overlay = QHBoxLayout()
        self._overlay_info = QLabel("")
        self._overlay_info.setStyleSheet("color:#aaa;")
        self._overlay_info.setWordWrap(True)
        row_overlay.addWidget(self._overlay_info, stretch=1)
        btn_apply_overlay = QPushButton("立即写入 steam.cfg")
        btn_apply_overlay.clicked.connect(self._on_apply_overlay)
        row_overlay.addWidget(btn_apply_overlay)
        btn_restore_overlay = QPushButton("还原")
        btn_restore_overlay.setToolTip("把上次写入前的 steam.cfg 内容恢复")
        btn_restore_overlay.clicked.connect(self._on_restore_overlay)
        row_overlay.addWidget(btn_restore_overlay)
        sf.addRow("当前 steam.cfg:", row_overlay)

        note_overlay = QLabel(
            "注意:SteamOverlay=0 会一并关闭叠加层里的截图、好友消息、网页浏览器等所有功能。\n"
            "想恢复时:关闭上方复选框 + 点「立即写入」,或直接运行 Bat 脚本库里的「启用 Steam 叠加层」。"
        )
        note_overlay.setStyleSheet("color:#888;")
        note_overlay.setWordWrap(True)
        sf.addRow(note_overlay)

        parent_layout.addWidget(box)

    def _build_update_group(self, parent_layout: QVBoxLayout) -> None:
        box = QGroupBox("更新与日志")
        pf = QFormLayout(box)

        self._check_updates = QCheckBox("启动时自动检查更新(后台静默,不打扰)")
        self._check_updates.setChecked(self._cfg.get("settings.check_updates", True))
        self._check_updates.toggled.connect(self._refresh_update_status)
        pf.addRow("", self._check_updates)

        self._check_interval = QComboBox()
        self._check_interval.addItems([
            "1 每小时", "6 每 6 小时", "12 每 12 小时", "24 每天", "168 每周",
        ])
        cur = int(self._cfg.get("settings.update_check_interval_hours", 1))
        labels = [self._check_interval.itemData(i) or 1 for i in range(self._check_interval.count())]
        # itemData 没设过 → 落到文本里的数字
        wanted = None
        for i in range(self._check_interval.count()):
            txt = self._check_interval.itemText(i)
            if txt.startswith(str(cur)):
                wanted = i
                break
        self._check_interval.setCurrentIndex(wanted if wanted is not None else 0)
        self._check_interval.currentIndexChanged.connect(self._refresh_update_status)
        pf.addRow("检查频率:", self._check_interval)

        rowh = QHBoxLayout()
        self._last_check_info = QLabel("")
        self._last_check_info.setStyleSheet("color:#888;")
        rowh.addWidget(self._last_check_info, stretch=1)
        btn_check_now = QPushButton("立即检查")
        btn_check_now.clicked.connect(self._on_check_now)
        rowh.addWidget(btn_check_now)
        pf.addRow("上次检查:", rowh)

        self._skipped_label = QLabel("")
        self._skipped_label.setStyleSheet("color:#888;")
        self._skipped_label.setWordWrap(True)
        pf.addRow("", self._skipped_label)

        self._log_level = QComboBox()
        self._log_level.addItems(["DEBUG", "INFO", "WARNING", "ERROR"])
        idx = {"DEBUG": 0, "INFO": 1, "WARNING": 2, "ERROR": 3}.get(
            self._cfg.get("settings.log_level", "INFO"), 1)
        self._log_level.setCurrentIndex(idx)
        pf.addRow("日志级别:", self._log_level)

        info = QLabel("所有路径/用户名/游戏目录在日志与反馈中自动脱敏。")
        info.setStyleSheet("color:#888;")
        pf.addRow(info)
        parent_layout.addWidget(box)
        self._refresh_update_status()

    def _refresh_update_status(self) -> None:
        last = self._cfg.get("settings.last_update_check_at", "")
        found = self._cfg.get("settings.last_update_found", "")
        skipped = self._cfg.get("settings.skipped_version", "")
        if not last:
            self._last_check_info.setText("尚未检查过(启动 3 秒后会自动检查一次)")
        else:
            ago = self._ago_text(last)
            txt = f"{last[:19].replace('T', ' ')} · {ago}前"
            if found:
                txt += f" · 发现 v{found}"
            self._last_check_info.setText(txt)
        if skipped:
            self._skipped_label.setText(
                f"已跳过 v{skipped}(启动时不再提示)。想再看到它,点「立即检查」后选「下载」。"
            )
        else:
            self._skipped_label.setText("")

    @staticmethod
    def _ago_text(iso: str) -> str:
        try:
            t = datetime.datetime.fromisoformat(iso)
        except ValueError:
            return ""
        delta = datetime.datetime.now() - t
        s = int(delta.total_seconds())
        if s < 60:
            return f"{s} 秒"
        if s < 3600:
            return f"{s // 60} 分钟"
        if s < 86400:
            return f"{s // 3600} 小时"
        return f"{s // 86400} 天"

    def _on_check_now(self) -> None:
        from ..utils.update_checker import check as check_update
        info = check_update()
        now = datetime.datetime.now().isoformat(timespec="seconds")
        if info.error:
            self._cfg.set("settings.last_update_check_at", now)
            self._cfg.set("settings.last_update_found", "")
            self._cfg.save()
            self._refresh_update_status()
            QMessageBox.warning(
                self, "检查失败",
                f"无法连接到 GitHub 检查更新:\n{info.error}\n\n"
                "下次按设置的频率自动重试。",
            )
            return
        self._cfg.set("settings.last_update_check_at", now)
        if info.has_update:
            self._cfg.set("settings.last_update_found", info.latest_version)
            r = QMessageBox.question(
                self, "发现新版本",
                f"发现新版本 v{info.latest_version}(当前 v{info.current_version})。\n\n"
                f"更新说明:\n{info.release_notes[:600]}\n\n"
                "是否前往下载?",
                QMessageBox.StandardButton.Yes
                | QMessageBox.StandardButton.No
                | QMessageBox.StandardButton.Ignore,
            )
            if r == QMessageBox.StandardButton.Yes:
                import webbrowser
                webbrowser.open(info.release_url)
            elif r == QMessageBox.StandardButton.Ignore:
                self._cfg.set("settings.skipped_version", info.latest_version)
            # 「稍后」/「No」:不记 skipped,下次自动检查还会再问
        else:
            self._cfg.set("settings.last_update_found", "")
            self._cfg.set("settings.skipped_version", "")
            QMessageBox.information(
                self, "已是最新", f"当前 v{info.current_version} 已是最新版本。",
            )
        self._cfg.save()
        self._refresh_update_status()

    def _build_paths_group(self, parent_layout: QVBoxLayout) -> None:
        box = QGroupBox("数据位置(只读)")
        pbf = QFormLayout(box)
        from ..utils.paths import (
            user_data_dir, user_log_dir, user_macro_dir, user_preset_dir,
            user_bat_dir,
        )
        from ..utils.sanitize import user_data_dir_display
        base = user_data_dir_display()
        for label, sub in (
            ("配置:", "\\config.json"),
            ("日志:", "\\logs\\"),
            ("宏:", "\\macros\\"),
            ("预设:", "\\presets\\"),
            ("Bat 脚本库:", "\\bats\\"),
        ):
            pbf.addRow(label, QLabel(base + sub))
        parent_layout.addWidget(box)

    # ===== 行为 =====
    def _tracked_pids(self) -> list[int]:
        """从主窗口拿「当前追踪的所有窗口」的 pid(用于批量设音量)."""
        parent = self.parent()
        for attr in ("_pm", "pm", "_process_manager"):
            pm = getattr(parent, attr, None)
            if pm is not None and hasattr(pm, "all"):
                try:
                    return [int(tp.pid) for tp in pm.all() if getattr(tp, "pid", 0)]
                except Exception:  # noqa: BLE001
                    return []
        return []

    def _on_master_volume_changed(self, val: int) -> None:
        self._master_lab.setText(f"{val}%")
        audio.set_master_volume(val / 100.0)

    def _on_apply_volume_to_all(self) -> None:
        pids = self._tracked_pids()
        if not pids:
            QMessageBox.information(self, "音量", "当前没有追踪中的窗口。")
            return
        n = audio.set_volume_for_pids(pids, self._master_vol.value() / 100.0)
        QMessageBox.information(
            self, "音量",
            f"已对 {n}/{len(pids)} 个窗口设置音量 {self._master_vol.value()}%。\n"
            "没生效的窗口是因为它此刻还没有音频会话(没在发声)。",
        )

    def _refresh_steam_info(self) -> None:
        try:
            self._steam_info.setText(self._steam.describe())
        except Exception:  # noqa: BLE001
            self._steam_info.setText("状态读取失败")

    def _refresh_overlay_status(self) -> None:
        info = steam_overlay_describe()
        cur = info.get("overlay")
        if not info["exists"]:
            cur_txt = "文件不存在"
        elif cur is None:
            cur_txt = "未设置(默认开启)"
        else:
            cur_txt = f"SteamOverlay={cur} ({'禁用' if cur == 0 else '启用'})"
        target = "禁用" if self._disable_overlay.isChecked() else "启用"
        self._overlay_info.setText(
            f"当前:{cur_txt}\n"
            f"目标:{target}\n"
            f"路径:{info['path']}"
        )

    def _on_apply_overlay(self) -> None:
        """把复选框状态写入 steam.cfg,失败时弹错误."""
        want_disable = self._disable_overlay.isChecked()
        backup = self._cfg.get("ui.steam_overlay_backup", "")
        if not backup:
            info = steam_overlay_describe()
            if info["exists"]:
                try:
                    backup = steam_config_path().read_text(encoding="utf-8", errors="replace")
                except OSError:
                    backup = ""
        ok, original = steam_overlay_set(not want_disable, backup=backup)
        if not ok:
            QMessageBox.warning(
                self, "写入失败",
                "无法写入 steam.cfg。常见原因:\n"
                " · Steam 客户端正在运行并独占该文件\n"
                " · 权限不足(右键本软件以管理员身份运行)\n"
                " · 路径不存在(没装 Steam)\n"
                f"路径:{steam_config_path()}",
            )
            return
        self._cfg.set("ui.disable_steam_overlay", want_disable)
        self._cfg.set("ui.steam_overlay_backup", backup)
        self._cfg.set(
            "ui.steam_overlay_applied_at",
            datetime.datetime.now().isoformat(timespec="seconds"),
        )
        self._cfg.save()
        QMessageBox.information(
            self, "已写入",
            f"已写入 steam.cfg:SteamOverlay={'0' if want_disable else '1'}\n"
            "请重启 Steam 客户端后生效。\n"
            "运行中的游戏不会受影响。",
        )
        self._refresh_overlay_status()

    def _on_restore_overlay(self) -> None:
        backup = self._cfg.get("ui.steam_overlay_backup", "")
        if not backup:
            r = QMessageBox.question(
                self, "没有备份",
                "当前没有保存的备份,无法回滚。\n是否要清空复选框状态(并不动 steam.cfg)?",
            )
            if r == QMessageBox.StandardButton.Yes:
                self._disable_overlay.setChecked(False)
                self._cfg.set("ui.disable_steam_overlay", False)
                self._cfg.save()
            return
        ok, _ = steam_overlay_restore(backup)
        if not ok:
            QMessageBox.warning(self, "还原失败", "无法写入 steam.cfg,请检查权限")
            return
        self._cfg.set("ui.disable_steam_overlay", False)
        self._cfg.set("ui.steam_overlay_backup", "")
        self._cfg.set("ui.steam_overlay_applied_at", "")
        self._cfg.save()
        self._disable_overlay.setChecked(False)
        self._refresh_overlay_status()
        QMessageBox.information(self, "已还原", "steam.cfg 已恢复成写入前的状态。\n请重启 Steam 后生效。")

    def _on_accept(self) -> None:
        self._cfg.set("ui.theme", "dark" if self._theme.currentIndex() == 0 else "light")
        self._cfg.set("ui.language", "zh-CN" if self._language.currentIndex() == 0 else "en-US")
        self._cfg.set("ui.confirm_exit", self._confirm_exit.isChecked())
        self._cfg.set("ui.start_minimized", self._start_minimized.isChecked())
        self._cfg.set("ui.restore_session_ask", self._restore_ask.isChecked())
        try:
            self._cfg.set("ui.preview_fps", float(self._preview_fps.currentText()))
        except ValueError:
            pass
        cols_text = self._preview_cols.currentText()
        self._cfg.set("ui.preview_columns", 0 if cols_text == "自适应" else int(cols_text))
        self._cfg.set("defaults.clicker_interval_ms", self._def_interval.value())
        self._cfg.set("defaults.clicker_jitter_ms", self._def_jitter.value())
        self._cfg.set(
            "defaults.clicker_button",
            ["left", "right", "middle"][self._def_button.currentIndex()],
        )
        self._cfg.set(
            "defaults.clicker_mode",
            ["fixed", "random", "hold"][self._def_mode.currentIndex()],
        )
        self._cfg.set("steam.enabled", self._steam_enabled.isChecked())
        self._cfg.set("steam.farm_state", self._steam_state.currentData() or STATE_ONLINE)
        self._cfg.set("steam.restore_previous", self._steam_restore.isChecked())
        self._cfg.set("steam.verify", self._steam_verify.isChecked())
        # 卡片尺寸 + 聚焦行为
        self._cfg.set("ui.card_min_width", self._card_w.value())
        self._cfg.set("ui.card_min_height", self._card_h.value())
        self._cfg.set("ui.focus_fit_screen", self._focus_fit.isChecked())
        # 游戏进程策略
        self._cfg.set("launch.auto_wake_on_restore", self._auto_wake.isChecked())
        self._cfg.set("launch.close_games_on_exit", self._close_games.isChecked())
        self._cfg.set("launch.kill_tree", self._kill_tree.isChecked())
        self._cfg.set("launch.steam_timeout_sec", self._steam_timeout.value())
        # 音量
        self._cfg.set("audio.apply_master_on_start", self._apply_master_on_start.isChecked())
        if audio.available():
            self._cfg.set("audio.master_volume", self._master_vol.value() / 100.0)
            self._cfg.set("audio.master_mute", self._master_mute.isChecked())
            self._cfg.set("audio.show_volume_on_card", self._show_volume_on_card.isChecked())
            self._cfg.set("audio.default_session_volume", self._def_session_vol.value() / 100.0)
            self._cfg.set("audio.apply_session_on_add", self._apply_session_on_add.isChecked())
        self._cfg.set("settings.check_updates", self._check_updates.isChecked())
        self._cfg.set(
            "settings.update_check_interval_hours",
            int(self._check_interval.currentText().split(" ")[0]),
        )
        self._cfg.set("settings.log_level", self._log_level.currentText())
        self._cfg.save()
        # 让 main_window 立即按新频率重建定时器
        if self.parent() is not None and hasattr(self.parent(), "_update_timer"):
            parent = self.parent()
            interval_ms = int(self._cfg.get("settings.update_check_interval_hours", 1)) * 3600 * 1000
            parent._update_timer.stop()
            parent._update_timer.start(interval_ms)
            parent._update_check_interval_hours = int(
                self._cfg.get("settings.update_check_interval_hours", 1)
            )
        # 让主窗口把「卡片尺寸 / 音量按钮 / 聚焦行为」等立即应用到现有卡片
        parent = self.parent()
        if parent is not None and hasattr(parent, "apply_settings_changes"):
            try:
                parent.apply_settings_changes()
            except Exception:  # noqa: BLE001
                _log.warning("应用设置变更失败", exc_info=True)
        self.accept()