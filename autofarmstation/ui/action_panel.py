"""右侧操作面板."""

from __future__ import annotations

import logging
import time

from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QFormLayout,
    QTabWidget, QGroupBox, QPushButton, QLabel, QListWidget, QListWidgetItem,
    QSpinBox, QDoubleSpinBox, QComboBox, QCheckBox, QLineEdit, QPlainTextEdit,
    QFileDialog, QMessageBox, QSizePolicy, QInputDialog,
)

from ..core import (
    AutoClicker, AutoClickerConfig, ClickPoint,
    KeyMacro, KeyMacroConfig, KeyStep,
    MacroRecorder, MacroPlayer, MacroScript, MacroEvent, MacroEventType,
    InputSender, MouseButton, InputEventType,
    Scheduler, ScheduledTask, TaskFreq,
    Statistics,
    PresetLibrary, Preset,
    AutomationHub,
    autoclicker_dict_to_points, key_macro_dict_to_steps,
)
from ..core import window_finder as wf
from ..core.process_manager import TrackedProcess, ProcessManager
from .widgets import styled_message, NumberField


# === 连点器面板 ===
class ClickerPanel(QWidget):
    """单个进程的连点器配置 + 启停.

    两种执行方式:
        * 「启动」        —— 只对当前目标窗口生效(单窗口独立)
        * 「📡 同步启动」 —— 把当前这套点位复制到所有勾选的窗口并同时启动
    """

    sync_start_requested = Signal()

    def __init__(self, hub: AutomationHub | None = None,
                 parent: QWidget | None = None,
                 *,
                 defaults: dict[str, object] | None = None) -> None:
        super().__init__(parent)
        self._hub = hub
        self._clicker: AutoClicker | None = None
        self._target_hwnd = 0
        self._stats = None  # 由 main 注入
        # v1.6.2:启动前回调(挂机时段守护会用到);返回 None=放行,返回 str=拒绝并显示
        self._start_blocked_cb = None  # type: ignore[assignment]
        # v1.6.2:从设置 → 默认连点参数 取值,未传就 fallback 到硬编码默认
        d = defaults or {}
        try:
            init_interval = max(10, min(60000, int(d.get("interval_ms", 200))))
        except Exception:
            init_interval = 200
        try:
            init_jitter = max(0, min(100, int(d.get("jitter_pct", 10))))
        except Exception:
            init_jitter = 10
        init_mode_idx = 0 if str(d.get("mode", "post")).lower().startswith("post") else 1

        v = QVBoxLayout(self)
        v.setContentsMargins(8, 8, 8, 8)

        # 目标窗口
        target_box = QGroupBox("目标窗口")
        tf = QFormLayout(target_box)
        self._target_lab = QLabel("(未选择)")
        self._target_lab.setWordWrap(True)
        tf.addRow("窗口:", self._target_lab)
        v.addWidget(target_box)

        # 间隔设置
        interval_box = QGroupBox("点击间隔")
        f = QFormLayout(interval_box)
        self._interval = NumberField(minimum=10, maximum=60000, step=10, suffix=" ms", value=init_interval)
        f.addRow("基础间隔:", self._interval)
        self._jitter = NumberField(minimum=0, maximum=100, step=5, suffix=" %", value=init_jitter)
        f.addRow("随机抖动:", self._jitter)
        self._total = NumberField(minimum=0, maximum=999999, step=10, value=0)
        f.addRow("总次数(0=无限):", self._total)
        self._loop = QCheckBox("点位循环(否则按列表点完即停)")
        self._loop.setChecked(True)
        f.addRow("", self._loop)
        self._mode = QComboBox()
        self._mode.addItems(["post 消息(安全)", "send 真实输入(需前台)"])
        self._mode.setCurrentIndex(init_mode_idx)
        f.addRow("发送方式:", self._mode)
        v.addWidget(interval_box)

        # 点位
        points_box = QGroupBox("点击点位")
        pv = QVBoxLayout(points_box)
        self._points_list = QListWidget()
        self._points_list.setMaximumHeight(140)
        pv.addWidget(self._points_list)

        pb = QHBoxLayout()
        self._btn_pick = QPushButton("从屏幕选点")
        self._btn_pick.clicked.connect(self._on_pick_clicked)
        self._btn_pick.setToolTip("点击后,在游戏窗口上点击要连点的位置")
        self._btn_del = QPushButton("删除选中")
        self._btn_del.clicked.connect(self._on_del_point)
        self._btn_clear = QPushButton("清空")
        self._btn_clear.clicked.connect(self._on_clear_points)
        pb.addWidget(self._btn_pick)
        pb.addWidget(self._btn_del)
        pb.addWidget(self._btn_clear)
        pv.addLayout(pb)

        addb = QHBoxLayout()
        self._btn_add_center = QPushButton("+ 添加中心点")
        self._btn_add_center.clicked.connect(self._on_add_center)
        addb.addWidget(self._btn_add_center)
        pv.addLayout(addb)
        v.addWidget(points_box)

        # 控制按钮
        ctl = QHBoxLayout()
        self._btn_start = QPushButton("启动")
        self._btn_start.setProperty("primary", True)
        self._btn_start.setStyleSheet("QPushButton { font-weight:bold; padding:6px 18px; }")
        self._btn_start.setToolTip("只对当前目标窗口生效(单窗口独立执行)")
        self._btn_start.clicked.connect(self._on_start)
        self._btn_stop = QPushButton("停止")
        self._btn_stop.clicked.connect(self._on_stop)
        self._btn_pause = QPushButton("暂停/继续")
        self._btn_pause.clicked.connect(self._on_pause)
        ctl.addWidget(self._btn_start)
        ctl.addWidget(self._btn_stop)
        ctl.addWidget(self._btn_pause)
        v.addLayout(ctl)

        # 同步到勾选窗口
        sync_ctl = QHBoxLayout()
        self._btn_sync_start = QPushButton("📡 同步启动(勾选窗口)")
        self._btn_sync_start.setToolTip(
            "把当前这套点位复制到左侧所有勾选的窗口,并同时启动。\n"
            "每个窗口各存一份配置,之后可分别微调。"
        )
        self._btn_sync_start.setStyleSheet("QPushButton { font-weight:bold; }")
        self._btn_sync_start.clicked.connect(self._on_sync_start)
        self._btn_sync_stop = QPushButton("■ 同步停止")
        self._btn_sync_stop.clicked.connect(self._on_sync_stop)
        sync_ctl.addWidget(self._btn_sync_start)
        sync_ctl.addWidget(self._btn_sync_stop)
        v.addLayout(sync_ctl)

        self._status = QLabel("就绪")
        self._status.setWordWrap(True)
        self._status.setStyleSheet("color:#aaa; font-size:11px;")
        v.addWidget(self._status)
        v.addStretch()

        # 启停计数回写主窗口
        self.running_changed = Signal(int, bool)  # hwnd, running
        # 启停定时刷新
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh_status)
        self._timer.start(300)

    def set_target(self, hwnd: int, title: str) -> None:
        self._target_hwnd = int(hwnd)
        self._target_lab.setText(f"[{hwnd}] {title}")
        self._status.setText(styled_message(f"已选择目标: {title}", level="info"))

    def set_stats(self, stats) -> None:
        self._stats = stats

    def set_hub(self, hub: AutomationHub) -> None:
        self._hub = hub

    def stop_all(self) -> None:
        self._on_stop()

    # --- 配置构建(供单窗口 / 同步两用) ---
    def build_config(self) -> AutoClickerConfig | None:
        """从界面读取当前配置;无有效点位时返回 None."""
        pts = self._points()
        if not pts:
            return None
        cw, ch = wf.client_size(self._target_hwnd) if self._target_hwnd else (0, 0)
        return AutoClickerConfig(
            hwnd=self._target_hwnd,
            points=pts,
            interval_ms=int(self._interval.value()),
            jitter_pct=int(self._jitter.value()),
            total_clicks=int(self._total.value()),
            loop=self._loop.isChecked(),
            mode="send" if self._mode.currentIndex() == 1 else "post",
            client_w=int(cw) if cw > 0 else 0,
            client_h=int(ch) if ch > 0 else 0,
        )

    # --- 启停 ---
    def _on_start(self) -> None:
        if not self._target_hwnd:
            QMessageBox.warning(self, "连点器", "请先在左侧选择目标窗口。")
            return
        # v1.6.2:挂机时段守护 — 时段外不允许启动,给明确提示
        if self._start_blocked_cb is not None:
            try:
                reason = self._start_blocked_cb()
            except Exception:
                reason = None
            if reason:
                QMessageBox.warning(self, "连点器", reason)
                return
        cfg = self.build_config()
        if cfg is None:
            QMessageBox.warning(self, "连点器", "请至少添加一个点击点位。")
            return

        if self._hub is not None:
            self._hub.set_clicker_config(self._target_hwnd, cfg)
            ok = self._hub.start_clicker(self._target_hwnd)
            if ok:
                self.running_changed.emit(self._target_hwnd, True)
                self._status.setText(styled_message("连点器已启动(单窗口)", level="ok"))
            else:
                self._status.setText(styled_message("连点器启动失败", level="error"))
            return

        # 兜底:不使用 hub 时退化为本地实例
        self._clicker = AutoClicker(cfg, sender=InputSender(default_mode="post"))

        def _on_click(count, point):
            if self._stats:
                self._stats.add_click(1, self._target_hwnd)

        self._clicker.on_click = _on_click
        if self._clicker.start():
            self.running_changed.emit(self._target_hwnd, True)
            self._status.setText(styled_message("连点器已启动", level="ok"))
        else:
            self._status.setText(styled_message("连点器启动失败", level="error"))

    def _on_sync_start(self) -> None:
        self.sync_start_requested.emit()

    def _on_sync_stop(self) -> None:
        if self._hub is None:
            return
        n = len(self._hub.running_hwnds())
        self._hub.stop_many(self._hub.running_hwnds())
        self.running_changed.emit(self._target_hwnd, False)
        self._status.setText(styled_message(f"已同步停止 {n} 个窗口的连点器", level="info"))

    def _on_stop(self) -> None:
        if self._hub is not None:
            if self._target_hwnd:
                self._hub.stop_clicker(self._target_hwnd)
                self.running_changed.emit(self._target_hwnd, False)
                self._status.setText(styled_message("连点器已停止", level="info"))
            return
        if self._clicker and self._clicker.is_running:
            self._clicker.stop()
            self.running_changed.emit(self._target_hwnd, False)
            self._status.setText(styled_message("连点器已停止", level="info"))

    def _on_pause(self) -> None:
        if self._clicker and self._clicker.is_running:
            paused = self._clicker.toggle_pause()
            self._status.setText(styled_message(f"连点器{'暂停' if paused else '继续'}", level="info"))

    def _refresh_status(self) -> None:
        if self._hub is not None:
            running = [h for h in self._hub.configured_hwnds()
                       if self._hub.is_clicker_running(h)]
            if running:
                n = len(running)
                total = sum(self._hub.click_count(h) for h in running)
                if self._target_hwnd in running:
                    self._status.setText(styled_message(
                        f"运行中(本窗口)· 总点击 {total} 次 · 共 {n} 个窗口在跑", level="ok"))
                else:
                    self._status.setText(styled_message(
                        f"{n} 个勾选窗口正在连点 · 总点击 {total} 次", level="ok"))
            return
        if self._clicker and self._clicker.is_running:
            n = self._clicker.click_count
            paused = self._clicker.is_paused
            t = int(self._clicker.runtime_seconds)
            tag = "暂停中" if paused else "运行中"
            self._status.setText(
                styled_message(f"{tag} · 已点击 {n} 次 · {t // 60} 分 {t % 60} 秒",
                               level="ok" if not paused else "warn")
            )

    # --- 点位 ---
    def _on_pick_clicked(self) -> None:
        if not self._target_hwnd:
            QMessageBox.warning(self, "选点", "请先选择目标窗口。")
            return
        # 弹出一个透明全屏窗口,捕获鼠标点击坐标
        from .capture_point_dialog import CapturePointDialog
        dlg = CapturePointDialog(self._target_hwnd, self)
        if dlg.exec() and dlg.captured:
            cp = ClickPoint(x=dlg.captured[0], y=dlg.captured[1], button=MouseButton.LEFT)
            cw, ch = wf.client_size(self._target_hwnd)
            if not cp.set_ratio(cw, ch):
                self._status.setText(styled_message(
                    "读不到窗口客户区尺寸,该点位只能按像素记录"
                    "(窗口缩放后可能打偏)", level="warn"))
            self._add_point(cp)

    def _on_add_center(self) -> None:
        if not self._target_hwnd:
            QMessageBox.warning(self, "中心点", "请先选择目标窗口。")
            return
        cw, ch = wf.client_size(self._target_hwnd)
        if cw > 0 and ch > 0:
            cp = ClickPoint(x=cw // 2, y=ch // 2)
            cp.set_ratio(cw, ch)
            self._add_point(cp)

    def _points(self) -> list[ClickPoint]:
        out: list[ClickPoint] = []
        for i in range(self._points_list.count()):
            data = self._points_list.item(i).data(Qt.ItemDataRole.UserRole)
            if isinstance(data, ClickPoint):
                out.append(data)
        return out

    def _on_rebase_points(self) -> None:
        """把「当前窗口尺寸下的像素坐标」记成比例坐标.

        老版本存下的点位只有像素(没有比例),窗口一被聚焦铺满就会打偏。
        操作方式:先把窗口摆成当初选点时的样子(尺寸一致),再点这个按钮,
        之后窗口再怎么缩放都能打中同一个位置。
        """
        if not self._target_hwnd:
            QMessageBox.warning(self, "重算比例", "请先选择目标窗口。")
            return
        pts = self._points()
        if not pts:
            QMessageBox.information(self, "重算比例", "还没有点位。")
            return
        cw, ch = wf.client_size(self._target_hwnd)
        if cw <= 0 or ch <= 0:
            QMessageBox.warning(self, "重算比例", "读不到窗口客户区尺寸,无法计算。")
            return
        for p in pts:
            p.set_ratio(cw, ch)
        self._reload_points()
        QMessageBox.information(
            self, "重算比例",
            f"已按当前窗口客户区 {cw} × {ch} 重算 {len(pts)} 个点位的比例坐标。\n\n"
            "以后窗口被聚焦铺满、手动缩放、甚至挪到别的显示器,"
            "都会按比例换算成新的像素位置,不会再打偏。",
        )

    def _on_del_point(self) -> None:
        for it in self._points_list.selectedItems():
            self._points_list.takeItem(self._points_list.row(it))

    def _on_clear_points(self) -> None:
        self._points_list.clear()

    def _reload_points(self) -> None:
        pts = self._points()
        self._points_list.clear()
        for cp in pts:
            self._add_point(cp)

    def _add_point(self, cp: ClickPoint) -> None:
        btn = "左键" if cp.button == MouseButton.LEFT else cp.button.value
        item = QListWidgetItem(
            f"({cp.x}, {cp.y}) · {btn}{' · 双击' if cp.double else ''} · {cp.space_text()}"
        )
        if not cp.has_ratio():
            item.setToolTip("没有比例信息:窗口尺寸一变就可能打偏,可用「重算比例」修复")
        else:
            item.setToolTip("已记比例坐标:窗口缩放 / 铺满后依然打中同一位置")
        item.setData(Qt.ItemDataRole.UserRole, cp)
        self._points_list.addItem(item)

    def apply_preset(self, preset: Preset) -> None:
        """应用预设的点位和间隔."""
        cfg = preset.autoclicker or {}
        self._interval.setValue(int(cfg.get("interval_ms", 200)))
        self._jitter.setValue(int(cfg.get("jitter_pct", 10)))
        self._total.setValue(int(cfg.get("total_clicks", 0)))
        self._loop.setChecked(bool(cfg.get("loop", True)))
        self._points_list.clear()
        for cp in autoclicker_dict_to_points(cfg):
            self._add_point(cp)


# === 键盘宏面板 ===
class KeyMacroPanel(QWidget):
    """键盘宏编辑 + 启停.

    同样支持「单窗口独立」与「多窗口同步」两种执行方式。
    """

    sync_start_requested = Signal()

    def __init__(self, hub: AutomationHub | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._hub = hub
        self._macro: KeyMacro | None = None
        self._target_hwnd = 0
        self._stats = None

        v = QVBoxLayout(self)
        v.setContentsMargins(8, 8, 8, 8)

        # 目标
        target_box = QGroupBox("目标窗口")
        tf = QFormLayout(target_box)
        self._target_lab = QLabel("(未选择)")
        tf.addRow("窗口:", self._target_lab)
        v.addWidget(target_box)

        # 步骤列表
        steps_box = QGroupBox("按键步骤")
        sv = QVBoxLayout(steps_box)
        self._steps_list = QListWidget()
        self._steps_list.setMaximumHeight(160)
        sv.addWidget(self._steps_list)

        addb = QHBoxLayout()
        self._key_input = QLineEdit()
        self._key_input.setPlaceholderText("按键名,如 F1 / space / ctrl+a")
        self._key_input.setMaximumWidth(180)
        addb.addWidget(self._key_input)
        self._hold = NumberField(minimum=10, maximum=5000, step=10, suffix=" ms", value=50)
        addb.addWidget(self._hold)
        self._btn_add = QPushButton("添加")
        self._btn_add.clicked.connect(self._on_add_step)
        addb.addWidget(self._btn_add)
        self._btn_del_step = QPushButton("删除")
        self._btn_del_step.clicked.connect(lambda: [
            self._steps_list.takeItem(self._steps_list.row(i))
            for i in self._steps_list.selectedItems()
        ])
        addb.addWidget(self._btn_del_step)
        sv.addLayout(addb)

        # 通用预设按钮
        pb = QHBoxLayout()
        for name, steps in [
            ("F1 循环", [("f1", 50)]),
            ("空格循环", [("space", 50)]),
            ("QWER 循环", [("q", 30), ("w", 30), ("e", 30), ("r", 30)]),
            ("1234", [("1", 50), ("2", 50), ("3", 50), ("4", 50)]),
        ]:
            b = QPushButton(name)
            b.clicked.connect(lambda _=False, ss=steps: self._fill_steps(ss))
            pb.addWidget(b)
        sv.addLayout(pb)

        v.addWidget(steps_box)

        # 控制
        ctl_box = QGroupBox("控制")
        cf = QFormLayout(ctl_box)
        self._loop = QCheckBox("循环")
        self._loop.setChecked(True)
        cf.addRow("", self._loop)
        self._round_delay = NumberField(minimum=0, maximum=60000, step=100, suffix=" ms", value=0)
        cf.addRow("轮间延迟:", self._round_delay)
        self._mode = QComboBox()
        self._mode.addItems(["post 消息", "send 真实输入"])
        cf.addRow("发送方式:", self._mode)
        v.addWidget(ctl_box)

        cb = QHBoxLayout()
        self._btn_start = QPushButton("启动")
        self._btn_start.setStyleSheet("QPushButton { font-weight:bold; padding:6px 18px; }")
        self._btn_start.setToolTip("只对当前目标窗口生效(单窗口独立执行)")
        self._btn_start.clicked.connect(self._on_start)
        self._btn_stop = QPushButton("停止")
        self._btn_stop.clicked.connect(self._on_stop)
        cb.addWidget(self._btn_start)
        cb.addWidget(self._btn_stop)
        v.addLayout(cb)

        sb = QHBoxLayout()
        self._btn_sync_start = QPushButton("📡 同步启动(勾选窗口)")
        self._btn_sync_start.setToolTip(
            "把当前按键序列复制到左侧所有勾选的窗口,并同时启动。"
        )
        self._btn_sync_start.setStyleSheet("QPushButton { font-weight:bold; }")
        self._btn_sync_start.clicked.connect(lambda: self.sync_start_requested.emit())
        self._btn_sync_stop = QPushButton("■ 同步停止")
        self._btn_sync_stop.clicked.connect(self._on_sync_stop)
        sb.addWidget(self._btn_sync_start)
        sb.addWidget(self._btn_sync_stop)
        v.addLayout(sb)

        self._status = QLabel("就绪")
        self._status.setWordWrap(True)
        self._status.setStyleSheet("color:#aaa; font-size:11px;")
        v.addWidget(self._status)
        v.addStretch()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh_status)
        self._timer.start(500)

    def set_target(self, hwnd: int, title: str) -> None:
        self._target_hwnd = int(hwnd)
        self._target_lab.setText(f"[{hwnd}] {title}")

    def set_stats(self, stats) -> None:
        self._stats = stats

    def set_hub(self, hub: AutomationHub) -> None:
        self._hub = hub

    def stop_all(self) -> None:
        self._on_stop()

    def _on_add_step(self) -> None:
        key = self._key_input.text().strip()
        if not key:
            return
        self._fill_steps([(key, int(self._hold.value()))])
        self._key_input.clear()

    def _fill_steps(self, steps: list[tuple[str, int]]) -> None:
        for name, hold in steps:
            try:
                vk = KeyStep(key=name, hold_ms=hold).vk or 0
                # 直接解析 vk
                from ..core.input_sender import parse_vk
                vk = parse_vk(name)
            except Exception as e:
                QMessageBox.warning(self, "按键", f"未知按键: {name}\n{e}")
                continue
            step = KeyStep(key=name, vk=vk, hold_ms=hold)
            item = QListWidgetItem(f"{name} (vk={vk}) · {hold}ms")
            item.setData(Qt.ItemDataRole.UserRole, step)
            self._steps_list.addItem(item)

    # --- 配置构建(供单窗口 / 同步两用) ---
    def build_config(self) -> KeyMacroConfig | None:
        steps: list[KeyStep] = []
        for i in range(self._steps_list.count()):
            it = self._steps_list.item(i)
            s = it.data(Qt.ItemDataRole.UserRole)
            if isinstance(s, KeyStep):
                steps.append(s)
        if not steps:
            return None
        return KeyMacroConfig(
            hwnd=self._target_hwnd,
            steps=steps,
            loop=self._loop.isChecked(),
            round_delay_ms=int(self._round_delay.value()),
            round_max=0,
        )

    def _on_start(self) -> None:
        if not self._target_hwnd:
            QMessageBox.warning(self, "键盘宏", "请先在左侧选择目标窗口。")
            return
        cfg = self.build_config()
        if cfg is None:
            QMessageBox.warning(self, "键盘宏", "请至少添加一个按键步骤。")
            return

        if self._hub is not None:
            self._hub.set_key_macro_config(self._target_hwnd, cfg)
            if self._hub.start_key_macro(self._target_hwnd):
                self._status.setText(styled_message("键盘宏已启动(单窗口)", level="ok"))
            else:
                self._status.setText(styled_message("启动失败(按键无效或窗口无效)", level="error"))
            return

        self._macro = KeyMacro(cfg, sender=InputSender(default_mode="post"))

        def _on_step(round_idx, step):
            if self._stats:
                self._stats.add_key(1, self._target_hwnd)

        self._macro.on_step = _on_step
        if self._macro.start():
            self._status.setText(styled_message("键盘宏已启动", level="ok"))
        else:
            self._status.setText(styled_message("启动失败", level="error"))

    def _on_sync_stop(self) -> None:
        if self._hub is None:
            return
        targets = self._hub.running_hwnds()
        self._hub.stop_many(targets)
        self._status.setText(styled_message(f"已同步停止 {len(targets)} 个窗口", level="info"))

    def _on_stop(self) -> None:
        if self._hub is not None:
            if self._target_hwnd:
                self._hub.stop_key_macro(self._target_hwnd)
                self._status.setText(styled_message("键盘宏已停止", level="info"))
            return
        if self._macro and self._macro.is_running:
            self._macro.stop()
            self._status.setText(styled_message("键盘宏已停止", level="info"))

    def _refresh_status(self) -> None:
        if self._hub is not None:
            running = [h for h in self._hub.configured_hwnds()
                       if self._hub.is_key_macro_running(h)]
            if running:
                self._status.setText(styled_message(
                    f"{len(running)} 个窗口正在跑键盘宏", level="ok"))
            return
        if self._macro and self._macro.is_running:
            self._status.setText(styled_message(
                f"运行中 · 已按键 {self._macro.keys} 次 · {self._macro.rounds} 轮",
                level="ok"))

    def apply_preset(self, preset: Preset) -> None:
        cfg = preset.key_macro or {}
        self._steps_list.clear()
        for s in key_macro_dict_to_steps(cfg):
            item = QListWidgetItem(f"{s.key} · {s.hold_ms}ms")
            item.setData(Qt.ItemDataRole.UserRole, s)
            self._steps_list.addItem(item)
        self._loop.setChecked(bool(cfg.get("loop", True)))


# === 宏录制面板 ===
class RecorderPanel(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._recorder = MacroRecorder()
        self._player: MacroPlayer | None = None
        self._target_hwnd = 0

        v = QVBoxLayout(self)
        v.setContentsMargins(8, 8, 8, 8)

        # 录制控制
        rec_box = QGroupBox("录制")
        rv = QVBoxLayout(rec_box)
        recb = QHBoxLayout()
        self._btn_rec = QPushButton("● 开始录制")
        self._btn_rec.clicked.connect(self._on_toggle_record)
        self._btn_save = QPushButton("保存宏")
        self._btn_save.clicked.connect(self._on_save)
        self._btn_load = QPushButton("加载宏")
        self._btn_load.clicked.connect(self._on_load)
        recb.addWidget(self._btn_rec)
        recb.addWidget(self._btn_save)
        recb.addWidget(self._btn_load)
        rv.addLayout(recb)

        self._rec_status = QLabel("未录制")
        self._rec_status.setStyleSheet("color:#aaa; font-size:11px;")
        rv.addWidget(self._rec_status)

        self._events_list = QListWidget()
        self._events_list.setMaximumHeight(160)
        rv.addWidget(self._events_list)
        v.addWidget(rec_box)

        # 回放
        play_box = QGroupBox("回放")
        pv = QVBoxLayout(play_box)
        pfb = QFormLayout()
        self._target_lab = QLabel("(未选择目标窗口)")
        pfb.addRow("目标窗口:", self._target_lab)
        self._mode = QComboBox()
        self._mode.addItems(["window 模式(post,推荐)", "absolute 模式(send,需前台)"])
        pfb.addRow("回放模式:", self._mode)
        self._speed = NumberField(minimum=0.1, maximum=10, step=0.1, decimals=1, value=1.0, suffix=" x")
        pfb.addRow("速度:", self._speed)
        self._loop = QCheckBox("循环回放")
        pfb.addRow("", self._loop)
        pv.addLayout(pfb)

        pb = QHBoxLayout()
        self._btn_play = QPushButton("▶ 播放")
        self._btn_play.clicked.connect(self._on_play)
        self._btn_stop = QPushButton("停止")
        self._btn_stop.clicked.connect(self._on_stop)
        pb.addWidget(self._btn_play)
        pb.addWidget(self._btn_stop)
        pv.addLayout(pb)
        v.addWidget(play_box)

        self._script: MacroScript | None = None
        self._status = QLabel("就绪")
        self._status.setStyleSheet("color:#aaa; font-size:11px;")
        v.addWidget(self._status)
        v.addStretch()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh)
        self._timer.start(500)

    def set_target(self, hwnd: int, title: str) -> None:
        self._target_hwnd = int(hwnd)
        self._target_lab.setText(f"[{hwnd}] {title}")

    def stop_all(self) -> None:
        self._on_stop()
        if self._recorder.is_recording:
            self._recorder.stop()

    def _on_toggle_record(self) -> None:
        if not self._recorder.is_recording:
            if self._recorder.start():
                self._btn_rec.setText("■ 停止录制")
                self._btn_rec.setStyleSheet("color:#ff7575; font-weight:bold;")
                self._status.setText(styled_message("正在录制鼠标键盘...", level="warn"))
                self._events_list.clear()
        else:
            script = self._recorder.stop()
            self._btn_rec.setText("● 开始录制")
            self._btn_rec.setStyleSheet("")
            if script:
                self._script = script
                # 记下录制时目标窗口的客户区尺寸:回放时按「当前尺寸/基准尺寸」
                # 等比缩放,这样窗口被聚焦铺满或手动缩放后宏依然打在同一个位置。
                if self._target_hwnd:
                    cw, ch = wf.client_size(self._target_hwnd)
                    if cw > 0 and ch > 0:
                        script.base_w, script.base_h = int(cw), int(ch)
                base_txt = (f" · 基准 {script.base_w}×{script.base_h}"
                            if script.base_w and script.base_h else " · 无基准尺寸")
                self._rec_status.setText(
                    f"已录制: {script.name} · {len(script.events)} 事件 · "
                    f"{script.duration_ms}ms{base_txt}"
                )
                self._events_list.clear()
                for e in script.events[:200]:
                    self._events_list.addItem(self._fmt_event(e))

    def _fmt_event(self, e: MacroEvent) -> str:
        if e.type == MacroEventType.MOUSE_CLICK.value:
            return f"click ({e.x},{e.y}) {e.button}"
        if e.type == MacroEventType.MOUSE_MOVE.value:
            return f"move ({e.x},{e.y})"
        if e.type == MacroEventType.MOUSE_SCROLL.value:
            return f"scroll {e.scroll}"
        if e.type == MacroEventType.KEY_DOWN.value:
            return f"keydown vk={e.vk}"
        if e.type == MacroEventType.KEY_UP.value:
            return f"keyup vk={e.vk}"
        return e.type

    def _on_save(self) -> None:
        if not self._script:
            QMessageBox.information(self, "保存宏", "没有可保存的宏。")
            return
        path, _ = QFileDialog.getSaveFileName(self, "保存宏", f"{self._script.name}.json",
                                              "Macro (*.json)")
        if not path:
            return
        from ..utils.paths import atomic_write
        atomic_write(__import__('pathlib').Path(path), self._script.to_json())
        self._status.setText(styled_message("宏已保存", level="ok"))

    def _on_load(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "加载宏", "", "Macro (*.json)")
        if not path:
            return
        try:
            text = open(path, "r", encoding="utf-8").read()
            self._script = MacroScript.from_json(text)
            self._rec_status.setText(
                f"已加载: {self._script.name} · {len(self._script.events)} 事件"
            )
            self._events_list.clear()
            for e in self._script.events[:200]:
                self._events_list.addItem(self._fmt_event(e))
        except Exception as e:
            QMessageBox.warning(self, "加载宏", f"加载失败:{e}")

    def _on_play(self) -> None:
        if not self._script or not self._script.events:
            QMessageBox.information(self, "回放", "没有可播放的宏。")
            return
        if not self._target_hwnd:
            QMessageBox.information(self, "回放", "请先选择目标窗口。")
            return
        mode = "window" if self._mode.currentIndex() == 0 else "absolute"
        self._player = MacroPlayer(
            script=self._script,
            hwnd=self._target_hwnd,
            target_mode=mode,
            speed=float(self._speed.value()),
            loop=self._loop.isChecked(),
        )
        if self._player.start():
            self._status.setText(styled_message("回放中...", level="ok"))

    def _on_stop(self) -> None:
        if self._player and self._player.is_running:
            self._player.stop()
            self._status.setText(styled_message("回放已停止", level="info"))

    def _refresh(self) -> None:
        if self._recorder.is_recording:
            self._rec_status.setText(f"录制中 · {self._recorder.event_count} 事件")
        if self._player and not self._player.is_running and self._status.text().startswith("回放中"):
            self._status.setText(styled_message("回放完成", level="ok"))


# === 窗口控制面板 ===
class WindowControlPanel(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._target_hwnd = 0
        v = QVBoxLayout(self)
        v.setContentsMargins(8, 8, 8, 8)
        target_box = QGroupBox("目标窗口")
        tf = QFormLayout(target_box)
        self._target_lab = QLabel("(未选择)")
        tf.addRow("窗口:", self._target_lab)
        v.addWidget(target_box)

        b = QHBoxLayout()
        self._btn_focus = QPushButton("聚焦")
        self._btn_focus.clicked.connect(lambda: wf.set_foreground(self._target_hwnd) if self._target_hwnd else None)
        self._btn_min = QPushButton("最小化")
        self._btn_min.clicked.connect(lambda: wf.show_window(self._target_hwnd, wf.SW_SHOWMINIMIZED) if self._target_hwnd else None)
        self._btn_max = QPushButton("最大化")
        self._btn_max.clicked.connect(lambda: wf.show_window(self._target_hwnd, wf.SW_SHOWMAXIMIZED) if self._target_hwnd else None)
        self._btn_restore = QPushButton("还原")
        self._btn_restore.clicked.connect(lambda: wf.show_window(self._target_hwnd, wf.SW_SHOWNORMAL) if self._target_hwnd else None)
        b.addWidget(self._btn_focus); b.addWidget(self._btn_min)
        b.addWidget(self._btn_max); b.addWidget(self._btn_restore)
        v.addLayout(b)

        # 透明度
        op_box = QGroupBox("透明度")
        of = QFormLayout(op_box)
        from PySide6.QtWidgets import QSlider
        self._opacity = QSlider()
        self._opacity.setRange(20, 100)
        self._opacity.setValue(100)
        self._opacity.setOrientation(Qt.Orientation.Horizontal)
        self._opacity.valueChanged.connect(self._on_opacity)
        of.addRow("不透明度:", self._opacity)
        v.addWidget(op_box)
        v.addStretch()

    def set_target(self, hwnd: int, title: str) -> None:
        self._target_hwnd = int(hwnd)
        self._target_lab.setText(f"[{hwnd}] {title}")

    def stop_all(self) -> None:
        pass

    def _on_opacity(self, val: int) -> None:
        if not self._target_hwnd:
            return
        try:
            import ctypes
            GWL_EXSTYLE = -20
            WS_EX_LAYERED = 0x00080000
            LWA_ALPHA = 0x2
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            ex = user32.GetWindowLongW(self._target_hwnd, GWL_EXSTYLE)
            user32.SetWindowLongW(self._target_hwnd, GWL_EXSTYLE, ex | WS_EX_LAYERED)
            user32.SetLayeredWindowAttributes(self._target_hwnd, 0, int(255 * val / 100), LWA_ALPHA)
        except Exception:
            pass


# === 预设面板 ===
class PresetPanel(QWidget):
    """预设列表 + 应用."""

    preset_applied = Signal(object)  # Preset

    def __init__(self, lib: PresetLibrary, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._lib = lib
        v = QVBoxLayout(self)
        v.setContentsMargins(8, 8, 8, 8)
        self._lib_list = QListWidget()
        v.addWidget(self._lib_list)
        self._refresh()

        b = QHBoxLayout()
        self._btn_apply = QPushButton("应用预设")
        self._btn_apply.clicked.connect(self._on_apply)
        self._btn_match = QPushButton("按窗口自动匹配")
        self._btn_match.clicked.connect(self._on_match)
        b.addWidget(self._btn_apply)
        b.addWidget(self._btn_match)
        v.addLayout(b)

        self._info = QLabel("选择列表中的预设,点击「应用预设」即可把推荐设置加载到当前目标窗口。")
        self._info.setWordWrap(True)
        self._info.setStyleSheet("color:#aaa; font-size:11px;")
        v.addWidget(self._info)
        v.addStretch()

    def refresh(self) -> None:
        self._refresh()

    def _refresh(self) -> None:
        self._lib_list.clear()
        for p in self._lib.all():
            tag = "内置" if p.builtin else "自定义"
            label = f"[{tag}] {p.name}"
            if p.steam_appid:
                label += f" (Steam {p.steam_appid})"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, p)
            item.setToolTip(p.description)
            self._lib_list.addItem(item)

    def _on_apply(self) -> None:
        it = self._lib_list.currentItem()
        if not it:
            return
        p = it.data(Qt.ItemDataRole.UserRole)
        self.preset_applied.emit(p)

    def _on_match(self) -> None:
        # 主窗口提供当前目标的 process/title,这里通过 signal 拿不到,改由 main 调
        pass


# === 统计面板 ===
class StatsPanel(QWidget):
    def __init__(self, stats: Statistics, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._stats = stats
        v = QVBoxLayout(self)
        v.setContentsMargins(8, 8, 8, 8)
        self._info = QLabel()
        self._info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        v.addWidget(self._info)
        self._refresh()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh)
        self._timer.start(1000)

    def _refresh(self) -> None:
        s = self._stats.snapshot()
        sec = s.total_runtime_ms // 1000
        h = sec // 3600
        m = (sec % 3600) // 60
        sec = sec % 60
        text = (
            f"<h3>累计统计</h3>"
            f"<table>"
            f"<tr><td>总运行:</td><td><b>{h} 时 {m} 分 {sec} 秒</b></td></tr>"
            f"<tr><td>启动次数:</td><td>{s.run_count}</td></tr>"
            f"<tr><td>总点击:</td><td>{s.total_clicks}</td></tr>"
            f"<tr><td>总按键:</td><td>{s.total_keys}</td></tr>"
            f"<tr><td>首次运行:</td><td>{s.first_run_iso or '尚未记录'}</td></tr>"
            f"<tr><td>最近运行:</td><td>{s.last_run_iso or '尚未记录'}</td></tr>"
            f"</table>"
        )
        if s.per_process:
            text += "<h4>每进程</h4><table border='1' cellpadding='4'>"
            text += "<tr><th>hwnd</th><th>点击</th><th>按键</th><th>运行时长(分)</th></tr>"
            for k, v_ in s.per_process.items():
                rt_min = v_.get("runtime_ms", 0) // 60000
                text += f"<tr><td>{k}</td><td>{v_.get('clicks', 0)}</td><td>{v_.get('keys', 0)}</td><td>{rt_min}</td></tr>"
            text += "</table>"
        self._info.setText(text)


# === 定时任务面板 ===
class SchedulerPanel(QWidget):
    """概览 + 一键进「定时任务管理器」(新增/编辑/删除都在管理器里做)."""

    def __init__(self, sched: Scheduler, pm=None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._sched = sched
        self._pm = pm
        v = QVBoxLayout(self)
        v.setContentsMargins(8, 8, 8, 8)

        self._list = QListWidget()
        self._list.itemDoubleClicked.connect(lambda *_: self._on_manage())
        v.addWidget(self._list, stretch=1)

        b = QHBoxLayout()
        self._btn_new = QPushButton("＋ 新建")
        self._btn_new.clicked.connect(self._on_manage)
        self._btn_manage = QPushButton("管理...")
        self._btn_manage.setToolTip("打开定时任务管理器(新增 / 编辑 / 删除 / 立即执行)")
        self._btn_manage.clicked.connect(self._on_manage)
        self._btn_toggle = QPushButton("启用/停用")
        self._btn_toggle.clicked.connect(self._on_toggle)
        self._btn_run = QPushButton("立即执行")
        self._btn_run.clicked.connect(self._on_run)
        self._btn_del = QPushButton("删除")
        self._btn_del.clicked.connect(self._on_del)
        for w in (self._btn_new, self._btn_manage, self._btn_toggle, self._btn_run, self._btn_del):
            b.addWidget(w)
        v.addLayout(b)

        self._info = QLabel(
            "定时任务会在后台按计划执行,支持:\n"
            "• 启动/停止全部 · 启动或结束游戏(Steam 游戏先唤起 Steam)\n"
            "• 开始/停止连点 · 启动键盘宏 · 调音量 · 退出软件\n"
            "• 频率:一次性 / 间隔 / 每天 / 每周\n"
            "任务保存在 %APPDATA%\\AutoFarmStation\\schedules.json,重启软件依然有效。"
        )
        self._info.setStyleSheet("color:#aaa; font-size:11px;")
        self._info.setWordWrap(True)
        v.addWidget(self._info)
        self._refresh()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh)
        self._timer.start(5000)

    # --- 列表 ---
    def _refresh(self) -> None:
        from ..core.scheduler import action_label

        cur = self._selected_name()
        self._list.clear()
        for t in self._sched.list():
            mark = "✓" if t.enabled else "✗"
            item = QListWidgetItem(
                f"{mark} {t.name} · {t.schedule_text()} · {action_label(t.action)}"
                f" · {self._sched.next_run_text(t)} · 已执行 {t.run_count} 次"
            )
            item.setData(Qt.ItemDataRole.UserRole, t.name)
            self._list.addItem(item)
        if cur:
            self._select(cur)

    def _selected_name(self) -> str:
        it = self._list.currentItem()
        return str(it.data(Qt.ItemDataRole.UserRole)) if it is not None else ""

    def _select(self, name: str) -> None:
        for i in range(self._list.count()):
            it = self._list.item(i)
            if str(it.data(Qt.ItemDataRole.UserRole)) == name:
                self._list.setCurrentItem(it)
                return

    def _targets(self) -> list[tuple[int, str]]:
        if self._pm is None:
            return []
        try:
            return [(int(tp.hwnd), tp.title or tp.name or str(tp.hwnd)) for tp in self._pm.all()]
        except Exception:  # noqa: BLE001
            return []

    # --- 操作 ---
    def _on_manage(self) -> None:
        from .schedule_dialog import ScheduleDialog

        ScheduleDialog(self._sched, self._targets(), self).exec()
        self._refresh()

    def _on_toggle(self) -> None:
        name = self._selected_name()
        t = self._sched.get(name) if name else None
        if t is None:
            return
        self._sched.set_enabled(name, not t.enabled)
        self._refresh()
        self._select(name)

    def _on_run(self) -> None:
        name = self._selected_name()
        if not name:
            return
        self._sched.run_now(name)
        self._refresh()
        self._select(name)

    def _on_del(self) -> None:
        name = self._selected_name()
        if not name:
            return
        if QMessageBox.question(self, "删除", f"确认删除定时任务「{name}」?") \
                != QMessageBox.StandardButton.Yes:
            return
        self._sched.remove(name)
        self._refresh()


# === 主面板 ===
class ActionPanel(QWidget):
    """右侧操作面板 - QTabWidget.

    「同步」相关动作由主窗口注入目标窗口列表(来自左侧预览网格的勾选)。
    """

    request_sync_targets = Signal()  # 请求主窗口提供勾选窗口(结果通过 set_sync_targets 回填)

    def __init__(
        self,
        pm: ProcessManager,
        sched: Scheduler,
        stats: Statistics,
        presets: PresetLibrary,
        hub: AutomationHub | None = None,
        parent: QWidget | None = None,
        *,
        cfg: "Config | None" = None,
    ) -> None:
        super().__init__(parent)
        self._pm = pm
        self._sched = sched
        self._stats = stats
        self._presets = presets
        self._cfg = cfg
        self._hub = hub or AutomationHub(stats=stats)
        self._target_hwnd = 0
        self._sync_targets: list[int] = []
        # v1.6.2:读 cfg.defaults.clicker_* 给面板里的连点器初始化,
        # 这样设置 → 默认连点参数才有真效果。
        # 兼容旧调用方:不传 cfg 也不报错,落到硬编码默认值(行为与 v1.6.1 一致)。
        self._defaults: dict[str, object] = {}
        if cfg is not None:
            try:
                self._defaults = {
                    "interval_ms": int(cfg.get("defaults.clicker_interval_ms", 200)),
                    "jitter_pct": int(cfg.get("defaults.clicker_jitter_ms", 30)),
                    "button": str(cfg.get("defaults.clicker_button", "left")),
                    "mode": str(cfg.get("defaults.clicker_mode", "fixed")),
                }
            except Exception:
                self._defaults = {}

        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        # 当前目标窗口提示
        head = QHBoxLayout()
        head_w = QWidget()
        head_w.setStyleSheet("background:#2a2a2a;")
        hl = QHBoxLayout(head_w)
        hl.setContentsMargins(8, 6, 8, 6)
        self._target_lab = QLabel("未选择目标窗口")
        self._target_lab.setStyleSheet("color:#a0cfff; font-weight:bold;")
        hl.addWidget(self._target_lab)
        hl.addStretch()
        self._sync_lab = QLabel("同步目标:0 个")
        self._sync_lab.setStyleSheet("color:#8fd694; font-size:11px;")
        self._sync_lab.setToolTip("在左侧预览网格勾选窗口,可对它们「同步执行」同一套操作")
        hl.addWidget(self._sync_lab)
        head.addWidget(head_w)
        v.addLayout(head)

        self._tabs = QTabWidget()
        v.addWidget(self._tabs, stretch=1)

        self._clicker_panel = ClickerPanel(self._hub, defaults=self._defaults)
        self._key_panel = KeyMacroPanel(self._hub)
        self._rec_panel = RecorderPanel()
        self._ctrl_panel = WindowControlPanel()
        self._preset_panel = PresetPanel(presets)
        self._sched_panel = SchedulerPanel(sched, pm)
        self._stats_panel = StatsPanel(stats)

        self._clicker_panel.set_stats(stats)
        self._key_panel.set_stats(stats)

        self._tabs.addTab(self._clicker_panel, "连点器")
        self._tabs.addTab(self._key_panel, "键盘宏")
        self._tabs.addTab(self._rec_panel, "宏录制")
        self._tabs.addTab(self._ctrl_panel, "窗口控制")
        self._tabs.addTab(self._preset_panel, "预设")
        self._tabs.addTab(self._sched_panel, "定时")
        self._tabs.addTab(self._stats_panel, "统计")

        self._preset_panel.preset_applied.connect(self._on_preset_applied)
        self._clicker_panel.sync_start_requested.connect(self._on_sync_start_clicker)
        self._key_panel.sync_start_requested.connect(self._on_sync_start_key)

    # ---------- 对外 ----------
    @property
    def hub(self) -> AutomationHub:
        return self._hub

    def set_sync_targets(self, hwnds: list[int]) -> None:
        """由主窗口在勾选变化时调用."""
        self._sync_targets = [int(h) for h in hwnds]
        n = len(self._sync_targets)
        if n:
            self._sync_lab.setText(f"同步目标:{n} 个窗口")
            self._sync_lab.setStyleSheet("color:#8fd694; font-size:11px; font-weight:bold;")
        else:
            self._sync_lab.setText("同步目标:0 个")
            self._sync_lab.setStyleSheet("color:#888; font-size:11px;")

    def set_target(self, hwnd: int, title: str) -> None:
        self._target_hwnd = int(hwnd)
        self._target_lab.setText(f"目标:[{hwnd}] {title}")
        for w in (
            self._clicker_panel, self._key_panel, self._rec_panel,
            self._ctrl_panel,
        ):
            w.set_target(hwnd, title)

    def stop_all(self) -> None:
        self._clicker_panel.stop_all()
        self._key_panel.stop_all()
        self._rec_panel.stop_all()
        self._hub.stop_all()

    def set_autoclicker_state(self, hwnd: int, running: bool) -> None:
        pass

    # ---------- 同步执行 ----------
    def _resolve_targets(self) -> list[int]:
        """优先用勾选窗口;没有勾选时退化为「当前目标窗口」."""
        if self._sync_targets:
            return list(self._sync_targets)
        if self._target_hwnd:
            return [self._target_hwnd]
        return []

    def _on_sync_start_clicker(self) -> None:
        targets = self._resolve_targets()
        if not targets:
            QMessageBox.information(
                self, "同步启动",
                "请先在左侧预览网格勾选要同步的窗口,\n或先点选一个目标窗口。",
            )
            return
        cfg = self._clicker_panel.build_config()
        if cfg is None:
            QMessageBox.warning(self, "同步启动", "请先在「连点器」面板添加至少一个点击点位。")
            return
        self._hub.broadcast_clicker_config(targets, cfg)
        res = self._hub.start_many(targets, clicker=True, key_macro=False)
        QMessageBox.information(
            self, "同步启动",
            f"{res.message}\n\n"
            f"（各窗口已各自保存一份这套点位,后续可在对应窗口上单独调整）",
        )

    def _on_sync_start_key(self) -> None:
        targets = self._resolve_targets()
        if not targets:
            QMessageBox.information(
                self, "同步启动",
                "请先在左侧预览网格勾选要同步的窗口,\n或先点选一个目标窗口。",
            )
            return
        cfg = self._key_panel.build_config()
        if cfg is None:
            QMessageBox.warning(self, "同步启动", "请先在「键盘宏」面板添加至少一个按键步骤。")
            return
        self._hub.broadcast_key_macro_config(targets, cfg)
        res = self._hub.start_many(targets, clicker=False, key_macro=True)
        QMessageBox.information(self, "同步启动", res.message)

    def broadcast_current_config(self, hwnds: list[int]) -> None:
        """把右侧当前编辑的配置复制到指定窗口(不启动),供预览网格按钮调用."""
        if not hwnds:
            QMessageBox.information(self, "应用配置", "请先勾选要应用到的窗口。")
            return
        done = []
        ccfg = self._clicker_panel.build_config()
        if ccfg is not None:
            self._hub.broadcast_clicker_config(hwnds, ccfg)
            done.append("连点器点位")
        kcfg = self._key_panel.build_config()
        if kcfg is not None:
            self._hub.broadcast_key_macro_config(hwnds, kcfg)
            done.append("键盘宏序列")
        if not done:
            QMessageBox.information(
                self, "应用配置",
                "当前「连点器」「键盘宏」面板都还没有内容,\n请先配置好再应用。",
            )
            return
        QMessageBox.information(
            self, "应用配置",
            f"已把{'、'.join(done)} 复制到 {len(hwnds)} 个窗口。\n"
            f"每个窗口各存一份,可分别微调后单独启动。",
        )

    def sync_start(self, hwnds: list[int]) -> None:
        """预览网格「同步启动」:勾选窗口各自按已有配置启动."""
        if not hwnds:
            return
        res = self._hub.start_many(hwnds, clicker=True, key_macro=True)
        QMessageBox.information(self, "同步启动", res.message)

    def sync_stop(self, hwnds: list[int]) -> None:
        if not hwnds:
            return
        self._hub.stop_many(hwnds)

    # ---------- 预设 ----------
    def _on_preset_applied(self, preset: Preset) -> None:
        if not self._target_hwnd:
            QMessageBox.information(self, "预设", "请先在左侧选择目标窗口。")
            return
        self._clicker_panel.apply_preset(preset)
        self._key_panel.apply_preset(preset)
        self._tabs.setCurrentWidget(self._clicker_panel)
        QMessageBox.information(
            self, "预设",
            f"已应用预设:{preset.name}\n\n"
            f"可在「连点器」「键盘宏」面板中单窗口启动,\n"
            f"或勾选多个窗口后点「📡 同步启动」。",
        )