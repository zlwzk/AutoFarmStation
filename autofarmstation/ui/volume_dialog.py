"""单窗口(单进程)音量面板.

等价于 Windows「音量合成器」里那一栏:只改这个游戏的音量,
不动系统整体音量,也不影响其它窗口。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QPushButton,
    QSlider, QVBoxLayout,
)

from ..core import audio
from ..utils.sanitize import sanitize


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
            self._hint.setText("改动即时生效。此设置只作用于该窗口,不影响系统音量。")

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btns.rejected.connect(self.reject)
        btns.accepted.connect(self.accept)
        v.addWidget(btns)

    def _on_value_changed(self, val: int) -> None:
        self._value_lab.setText(f"{val}%")
        audio.set_session_volume(self._pid, val / 100.0)


class MasterVolumeDialog(QDialog):
    """整体音量(默认输出设备)."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("整体音量")
        self.setMinimumWidth(380)
        v = QVBoxLayout(self)

        mv = audio.get_master_volume()
        row = QHBoxLayout()
        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setRange(0, 100)
        self._slider.setValue(int(round((mv if mv is not None else 1.0) * 100)))
        self._slider.valueChanged.connect(self._on_value_changed)
        row.addWidget(self._slider, stretch=1)
        self._value_lab = QLabel(f"{self._slider.value()}%")
        self._value_lab.setFixedWidth(44)
        row.addWidget(self._value_lab)
        v.addLayout(row)

        self._mute = QCheckBox("静音")
        self._mute.setChecked(bool(audio.get_master_mute()))
        self._mute.toggled.connect(audio.set_master_mute)
        v.addWidget(self._mute)

        hint = QLabel(
            "这是系统默认输出设备的音量(等同任务栏音量条)。\n"
            "想让某个游戏单独小声,请点卡片上的 ♪ 按钮。"
        )
        hint.setStyleSheet("color:#888; font-size:11px;")
        hint.setWordWrap(True)
        v.addWidget(hint)

        set_all = QPushButton("把该音量同时应用到所有追踪窗口")
        set_all.clicked.connect(self._apply_to_all)
        v.addWidget(set_all)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btns.rejected.connect(self.reject)
        btns.accepted.connect(self.accept)
        v.addWidget(btns)

        self._pids_provider = None

    def set_pids_provider(self, fn) -> None:
        """注入「取所有追踪窗口 pid」的回调."""
        self._pids_provider = fn

    def _on_value_changed(self, val: int) -> None:
        self._value_lab.setText(f"{val}%")
        audio.set_master_volume(val / 100.0)

    def _apply_to_all(self) -> None:
        if self._pids_provider is None:
            return
        try:
            pids = list(self._pids_provider() or [])
        except Exception:  # noqa: BLE001
            return
        n = audio.set_volume_for_pids(pids, self._slider.value() / 100.0)
        self._value_lab.setText(f"{self._slider.value()}%({n} 个窗口已应用)")
