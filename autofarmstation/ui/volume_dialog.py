"""音量面板.

三类音量,影响范围从窄到宽,别搞混:

* :class:`SessionVolumeDialog` —— **单个窗口**(卡片上的 ♪ 按钮),
  等价于 Windows「音量合成器」里那一栏:只改这个游戏。
* :class:`GroupVolumeDialog` —— **全部已加入的窗口**,
  只是对上一档做批量,不影响任何其它程序。这是本软件的默认「整体音量」。
* 系统总音量(任务栏音量条)—— 会影响所有程序,所以放在 GroupVolumeDialog
  里的「高级」折叠区,**默认不接管**,勾了才会去动。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QFrame, QHBoxLayout, QLabel,
    QPushButton, QSlider, QVBoxLayout, QWidget,
)

from ..core import audio
from ..utils.sanitize import sanitize

# 拖动滑块时 valueChanged 会连着触发几十次,每次都写盘会把磁盘刷爆。
# 改成「停手 400ms 后写一次」;关闭对话框时再强制补一次,保证不漏。
_SAVE_DEBOUNCE_MS = 400


class SessionVolumeDialog(QDialog):
    """调某个进程的音频会话音量."""

    def __init__(self, pid: int, title: str = "", parent=None) -> None:
        super().__init__(parent)
        self._pid = int(pid or 0)
        self.setWindowTitle("窗口音量")
        self.setMinimumWidth(380)
        self._title = sanitize(title or f"PID {self._pid}")

        v = QVBoxLayout(self)
        lbl = QLabel(f"{self._title}\n进程 PID: {self._pid}")
        lbl.setWordWrap(True)
        v.addWidget(lbl)

        if not audio.available():
            warn = QLabel(
                "音量功能不可用:缺少 pycaw 组件。\n"
                "装上后即可按窗口单独调音量(不影响系统整体音量)。"
            )
            warn.setStyleSheet("color:#e08080;")
            v.addWidget(warn)
            btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
            btns.rejected.connect(self.reject)
            btns.accepted.connect(self.accept)
            v.addWidget(btns)
            return

        cur = audio.get_session_volume(self._pid)
        row = QHBoxLayout()
        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setRange(0, 100)
        self._slider.setValue(int(round((cur if cur is not None else 1.0) * 100)))
        self._slider.valueChanged.connect(self._on_value_changed)
        row.addWidget(self._slider, stretch=1)
        self._value_lab = QLabel(f"{self._slider.value()}%")
        self._value_lab.setFixedWidth(44)
        row.addWidget(self._value_lab)
        v.addLayout(row)

        self._mute = QCheckBox("静音该窗口")
        m = audio.get_session_mute(self._pid)
        self._mute.setChecked(bool(m))
        self._mute.toggled.connect(lambda on: audio.set_session_mute(self._pid, on))
        v.addWidget(self._mute)

        self._hint = QLabel("")
        self._hint.setStyleSheet("color:#888; font-size:11px;")
        self._hint.setWordWrap(True)
        v.addWidget(self._hint)
        if cur is None:
            self._hint.setText(
                "该进程当前还没有音频会话(没在发声),因此读不到现成音量。\n"
                "拖动滑块会在它开口说话时立即生效 —— 若无效,先让游戏发出声音再调。"
            )
        else:
            self._hint.setText(
                "改动即时生效。此设置只作用于该窗口,不影响系统音量,也不影响其它窗口。"
            )

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btns.rejected.connect(self.reject)
        btns.accepted.connect(self.accept)
        v.addWidget(btns)

    def _on_value_changed(self, val: int) -> None:
        self._value_lab.setText(f"{val}%")
        audio.set_session_volume(self._pid, val / 100.0)


class GroupVolumeDialog(QDialog):
    """全部已加入窗口的音量 / 静音.

    「加入」= 主界面里当前追踪中的窗口。这里做的一切都只走
    **按窗口的音频会话**(音量合成器那一栏),不碰系统总音量 ——
    所以不会把其它软件一起静音。

    系统总音量(任务栏音量条)藏在下面的「高级」里,默认不接管。
    """

    def __init__(self, pids_provider=None, cfg=None, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("全部窗口音量")
        self.setMinimumWidth(420)
        self._pids_provider = pids_provider
        self._cfg = cfg
        # 防抖落盘:拖动滑块 / 连续勾选时不会每动一下就写盘
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(_SAVE_DEBOUNCE_MS)
        self._save_timer.timeout.connect(self._flush_config)

        v = QVBoxLayout(self)
        tip = QLabel(
            "这里调的是「已加入本软件的窗口」—— 也就是主界面里追踪中的那些游戏。\n"
            "其它程序(QQ、浏览器、视频)完全不受影响。"
        )
        tip.setStyleSheet("color:#888; font-size:11px;")
        tip.setWordWrap(True)
        v.addWidget(tip)

        if not audio.available():
            warn = QLabel(
                "音量功能不可用:当前环境缺少 pycaw 组件(打包版已内置)。\n"
                "源码运行时执行:pip install pycaw"
            )
            warn.setStyleSheet("color:#e08080;")
            warn.setWordWrap(True)
            v.addWidget(warn)
            self._add_close_buttons(v)
            return

        # --- 全部已加入窗口的音量 ---
        row = QHBoxLayout()
        cur = float(self._cfg.get("audio.group_volume", -1) if self._cfg else -1)
        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setRange(0, 100)
        self._slider.setValue(100 if cur < 0 else int(round(cur * 100)))
        self._slider.valueChanged.connect(self._on_value_changed)
        row.addWidget(self._slider, stretch=1)
        self._value_lab = QLabel(f"{self._slider.value()}%")
        self._value_lab.setFixedWidth(96)
        row.addWidget(self._value_lab)
        v.addLayout(row)

        self._mute = QCheckBox("静音全部已加入窗口")
        self._mute.setChecked(bool(self._cfg.get("audio.group_mute", False)) if self._cfg else False)
        self._mute.toggled.connect(self._on_mute_toggled)
        v.addWidget(self._mute)

        row2 = QHBoxLayout()
        self._status = QLabel("")
        self._status.setStyleSheet("color:#888; font-size:11px;")
        self._status.setWordWrap(True)
        row2.addWidget(self._status, stretch=1)
        btn_refresh = QPushButton("刷新")
        btn_refresh.setToolTip("重新读一遍各窗口的会话音量")
        btn_refresh.clicked.connect(self._refresh_status)
        row2.addWidget(btn_refresh)
        v.addLayout(row2)

        self._hint = QLabel(
            "进程还没发出声音时读不到它的会话音量,此时拖动滑块会在它出声后依然生效。"
        )
        self._hint.setStyleSheet("color:#888; font-size:11px;")
        self._hint.setWordWrap(True)
        v.addWidget(self._hint)

        # --- 高级:系统总音量(默认不接管) ---
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("color:#555;")
        v.addWidget(line)

        self._sys_on = QCheckBox("同时调整系统总音量(任务栏音量条,会影响所有程序)")
        self._sys_on.setChecked(bool(self._cfg.get("audio.control_system_master", False))
                                if self._cfg else False)
        self._sys_on.toggled.connect(self._on_sys_toggled)
        v.addWidget(self._sys_on)

        self._sys_box = QWidget()
        sb = QVBoxLayout(self._sys_box)
        sb.setContentsMargins(18, 0, 0, 0)
        srow = QHBoxLayout()
        mv = audio.get_master_volume()
        self._sys_slider = QSlider(Qt.Orientation.Horizontal)
        self._sys_slider.setRange(0, 100)
        self._sys_slider.setValue(int(round((mv if mv is not None else 1.0) * 100)))
        self._sys_slider.valueChanged.connect(self._on_sys_value_changed)
        srow.addWidget(self._sys_slider, stretch=1)
        self._sys_lab = QLabel(f"{self._sys_slider.value()}%")
        self._sys_lab.setFixedWidth(96)
        srow.addWidget(self._sys_lab)
        sb.addLayout(srow)
        self._sys_mute = QCheckBox("系统静音")
        self._sys_mute.setChecked(bool(audio.get_master_mute()))
        self._sys_mute.toggled.connect(audio.set_master_mute)
        sb.addWidget(self._sys_mute)
        snote = QLabel("⚠ 这一项才是「真的把电脑静音」,会连带静掉其它软件。")
        snote.setStyleSheet("color:#e0a080; font-size:11px;")
        snote.setWordWrap(True)
        sb.addWidget(snote)
        v.addWidget(self._sys_box)
        self._sys_box.setVisible(self._sys_on.isChecked())

        self._add_close_buttons(v)
        self._refresh_status()

    # --- 内部 ---
    def _add_close_buttons(self, layout: QVBoxLayout) -> None:
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btns.rejected.connect(self.reject)
        btns.accepted.connect(self.accept)
        layout.addWidget(btns)

    def _pids(self) -> list[int]:
        if self._pids_provider is None:
            return []
        try:
            return [int(p) for p in (self._pids_provider() or []) if p]
        except Exception:  # noqa: BLE001
            return []

    # --- 关闭兜底 ---
    def done(self, code: int) -> None:
        """对话框关闭前把还没落盘的设置补写一次.

        拖动滑块后 400ms 内直接关窗口,防抖定时器还没点火,
        不在这里补一刀,这次改动就白改了。
        """
        try:
            self._flush_config()
        except Exception:  # noqa: BLE001
            pass
        super().done(code)

    def closeEvent(self, event) -> None:  # noqa: ANN001
        """标题栏 X 关闭同样兜底(done 覆盖不到这条路径)."""
        try:
            self._flush_config()
        except Exception:  # noqa: BLE001
            pass
        super().closeEvent(event)

    def _refresh_status(self) -> None:
        if not audio.available():
            return
        pids = self._pids()
        self._status.setText(audio.describe_for_pids(pids))

    def _flush_config(self) -> None:
        """把内存里的音量设置真正写到 config.json.

        之前这里是 4 处 ``cfg.set()`` 却一次 ``cfg.save()`` 都没有 ——
        用户在音量对话框调完,一关对话框 / 一重启软件,设置全部丢干净
        (设置页里同样的字段有 save,所以「设置里改能记住、音量对话框改记不住」)。
        """
        if self._cfg is None:
            return
        try:
            self._cfg.save()
        except Exception as ex:  # noqa: BLE001
            # 落盘失败不能让界面跟着炸,只在状态栏提示
            try:
                self._status.setText(f"保存失败:{ex}")
            except Exception:
                pass

    def _schedule_save(self) -> None:
        """防抖保存:连续操作只写最后一次."""
        if self._cfg is None:
            return
        self._save_timer.start()

    def _on_value_changed(self, val: int) -> None:
        pids = self._pids()
        n = audio.set_volume_for_pids(pids, val / 100.0)
        self._value_lab.setText(f"{val}%({n}/{len(pids)})")
        if self._cfg is not None:
            self._cfg.set("audio.group_volume", val / 100.0)
            self._schedule_save()

    def _on_mute_toggled(self, on: bool) -> None:
        pids = self._pids()
        n = audio.set_mute_for_pids(pids, on)
        if on:
            self._status.setText(
                f"已静音 {n}/{len(pids)} 个已加入窗口"
                + ("(没生效的是此刻没在发声的窗口)" if n < len(pids) else "")
            )
        else:
            self._refresh_status()
        if self._cfg is not None:
            self._cfg.set("audio.group_mute", bool(on))
            self._schedule_save()

    def _on_sys_toggled(self, on: bool) -> None:
        self._sys_box.setVisible(on)
        if self._cfg is not None:
            self._cfg.set("audio.control_system_master", bool(on))
            self._schedule_save()

    def _on_sys_value_changed(self, val: int) -> None:
        audio.set_master_volume(val / 100.0)
        self._sys_lab.setText(f"{val}%")
        if self._cfg is not None:
            self._cfg.set("audio.system_master_volume", val / 100.0)
            self._schedule_save()
