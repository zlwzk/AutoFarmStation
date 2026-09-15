"""左侧预览网格.

顶部增加一条「多窗口同步」工具条:
    * 勾选若干窗口卡片 = 选中一组窗口
    * 「同步启动 / 同步停止」= 对这一组窗口同时施加操作
    * 卡片各自仍可独立配置(右侧面板只作用于「当前聚焦窗口」)

这样就同时满足:
    - 多个窗口同步执行同一套连贯操作
    - 单个窗口分开执行各自不同的操作
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QWidget, QGridLayout, QScrollArea, QVBoxLayout, QHBoxLayout,
    QFrame, QLabel, QPushButton, QSizePolicy,
)

from ..core.process_manager import ProcessManager, TrackedProcess
from ..core.automation_hub import AutomationHub
from .preview_widget import PreviewWidget
from .widgets import styled_message


class PreviewGrid(QWidget):
    """左侧预览网格 + 多窗口同步工具条."""

    selection_changed = Signal(list)        # list[int] 当前勾选的 hwnd
    sync_start_requested = Signal(list)     # 请求对勾选窗口同步启动
    sync_stop_requested = Signal(list)      # 请求对勾选窗口同步停止
    sync_broadcast_requested = Signal(list) # 请求把右侧当前配置广播到勾选窗口
    stop_process_requested = Signal(int)    # hwnd → 结束该游戏进程
    resume_process_requested = Signal(int)  # hwnd → 重新唤醒该游戏
    volume_requested = Signal(int)          # hwnd → 打开单窗口音量面板

    def __init__(
        self,
        pm: ProcessManager,
        *,
        fps: float = 1.0,
        columns: int = 0,  # 0=自适应
        card_size: tuple[int, int] = (240, 190),
        show_volume: bool = True,
        hub: AutomationHub | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._pm = pm
        self._hub = hub
        self._fps = fps
        self._columns = columns
        self._card_size = (int(card_size[0]), int(card_size[1]))
        self._show_volume = bool(show_volume)
        self._items: dict[int, PreviewWidget] = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ---------- 多窗口同步工具条 ----------
        header = QWidget()
        header.setStyleSheet("background:#2b2b2b; border-bottom:1px solid #3c3c3c;")
        hl = QHBoxLayout(header)
        hl.setContentsMargins(8, 5, 8, 5)
        hl.setSpacing(6)

        self._hint = QLabel("勾选窗口 → 同步执行")
        self._hint.setStyleSheet("color:#9ad; font-size:11px; font-weight:bold;")
        hl.addWidget(self._hint)

        self._btn_all = QPushButton("全选")
        self._btn_all.setFixedWidth(50)
        self._btn_all.clicked.connect(lambda: self._set_all(True))
        hl.addWidget(self._btn_all)

        self._btn_invert = QPushButton("反选")
        self._btn_invert.setFixedWidth(50)
        self._btn_invert.clicked.connect(self._invert)
        hl.addWidget(self._btn_invert)

        self._btn_none = QPushButton("清空")
        self._btn_none.setFixedWidth(50)
        self._btn_none.clicked.connect(lambda: self._set_all(False))
        hl.addWidget(self._btn_none)

        hl.addStretch()

        self._sel_lab = QLabel("已选 0 个")
        self._sel_lab.setStyleSheet("color:#ddd; font-size:11px;")
        hl.addWidget(self._sel_lab)

        self._btn_broadcast = QPushButton("📡 应用当前配置")
        self._btn_broadcast.setToolTip(
            "把右侧面板当前编辑的那套操作(点位/按键)复制到所有勾选的窗口,\n"
            "每个窗口各存一份,之后可分别调整。"
        )
        self._btn_broadcast.clicked.connect(
            lambda: self.sync_broadcast_requested.emit(self.selected_hwnds()))
        hl.addWidget(self._btn_broadcast)

        self._btn_sync_start = QPushButton("▶ 同步启动")
        self._btn_sync_start.setStyleSheet("QPushButton { font-weight:bold; }")
        self._btn_sync_start.clicked.connect(
            lambda: self.sync_start_requested.emit(self.selected_hwnds()))
        hl.addWidget(self._btn_sync_start)

        self._btn_sync_stop = QPushButton("■ 同步停止")
        self._btn_sync_stop.clicked.connect(
            lambda: self.sync_stop_requested.emit(self.selected_hwnds()))
        hl.addWidget(self._btn_sync_stop)

        outer.addWidget(header)

        # ---------- 滚动网格 ----------
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._inner = QWidget()
        self._layout = QGridLayout(self._inner)
        self._layout.setContentsMargins(8, 8, 8, 8)
        self._layout.setSpacing(8)
        self._scroll.setWidget(self._inner)
        outer.addWidget(self._scroll, stretch=1)

        # 空白提示
        self._empty_label = QLabel(
            "尚未选择任何进程。\n\n"
            "点击左上角「添加窗口」,选择游戏窗口,即可开始挂机。\n\n"
            "支持任意进程,不限 Steam。\n\n"
            "提示:勾选多个窗口后,可用上方「同步启动」让它们执行同一套操作。"
        )
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_label.setStyleSheet("color:#888; font-size:13px;")
        self._layout.addWidget(self._empty_label, 0, 0)

        self._pm.on_change(self._sync)
        QTimer.singleShot(100, self._sync)

        # 状态刷新(每 1.2s):把 AutomationHub 的运行状态回填到卡片
        self._status_timer = QTimer(self)
        self._status_timer.timeout.connect(self._refresh_statuses)
        self._status_timer.start(1200)

    # ---------- 对外 ----------
    def set_fps(self, fps: float) -> None:
        self._fps = max(0.1, min(30.0, fps))
        for w in self._items.values():
            w.set_fps(self._fps)

    def set_columns(self, cols: int) -> None:
        self._columns = max(0, cols)
        self._relayout()

    def set_card_size(self, width: int, height: int) -> None:
        """设置面板里「卡片大小」改动后调用."""
        self._card_size = (max(160, int(width)), max(120, int(height)))
        for w in self._items.values():
            w.set_card_size(*self._card_size)
        self._relayout()

    def card_size(self) -> tuple[int, int]:
        return self._card_size

    def set_volume_visible(self, on: bool) -> None:
        self._show_volume = bool(on)
        for w in self._items.values():
            w.set_volume_button_visible(self._show_volume)

    def refresh_cards(self) -> None:
        """设置改动后让所有卡片立刻按新配置刷新."""
        for w in self._items.values():
            w.refresh_now()

    def card(self, hwnd: int) -> PreviewWidget | None:
        return self._items.get(int(hwnd))

    def set_autoclicker_state(self, hwnd: int, running: bool) -> None:
        w = self._items.get(int(hwnd))
        if w:
            w.set_autoclicker_state(running)

    def selected_hwnds(self) -> list[int]:
        """当前勾选的窗口(按网格顺序)."""
        return [h for h, w in self._items.items() if w.is_selected()]

    def all_hwnds(self) -> list[int]:
        return list(self._items.keys())

    def select_all(self, on: bool = True) -> None:
        self._set_all(on)

    def stop_all(self) -> None:
        for w in self._items.values():
            w.stop()

    # ---------- 内部 ----------
    def _set_all(self, on: bool) -> None:
        for w in self._items.values():
            w.set_selected(on)

    def _invert(self) -> None:
        for w in self._items.values():
            w.set_selected(not w.is_selected())

    def _on_card_selection_changed(self, hwnd: int, on: bool) -> None:
        self._update_sel_label()
        self.selection_changed.emit(self.selected_hwnds())

    def _update_sel_label(self) -> None:
        n = len(self.selected_hwnds())
        total = len(self._items)
        self._sel_lab.setText(f"已选 {n} / {total} 个")
        has = n > 0
        for b in (self._btn_sync_start, self._btn_sync_stop, self._btn_broadcast):
            b.setEnabled(has)

    def _refresh_statuses(self) -> None:
        if self._hub is None:
            return
        for h, w in self._items.items():
            txt = self._hub.status_text(h)
            w.set_status_extra(txt)
            w.set_autoclicker_state(self._hub.is_clicker_running(h))

    def _sync(self) -> None:
        items = self._pm.all()
        current_hwnds = {it.hwnd for it in items}
        # 移除
        for hwnd in list(self._items.keys()):
            if hwnd not in current_hwnds:
                w = self._items.pop(hwnd)
                w.setParent(None)
                w.stop()
                w.deleteLater()
                if self._hub:
                    self._hub.forget(hwnd)
        # 新增(phase 错峰:多开时各窗口截图时刻错开,避免同一瞬间抢 GDI)
        for idx, it in enumerate(items):
            if it.hwnd not in self._items:
                w = PreviewWidget(
                    it.hwnd,
                    fps=self._fps,
                    phase=idx * 0.13,
                    card_size=self._card_size,
                    show_volume=self._show_volume,
                )
                w.selection_changed.connect(self._on_card_selection_changed)
                w.stop_process.connect(self.stop_process_requested.emit)
                w.resume_process.connect(self.resume_process_requested.emit)
                w.volume_requested.connect(self.volume_requested.emit)
                self._items[it.hwnd] = w
        # 空提示
        self._empty_label.setVisible(not self._items)
        self._relayout()
        self._update_sel_label()

    def _relayout(self) -> None:
        # 清空 layout(保留 widget 引用)
        while self._layout.count():
            self._layout.takeAt(0)
        n = len(self._items)
        if n == 0:
            self._layout.addWidget(self._empty_label, 0, 0)
            return
        cols = self._columns
        if cols <= 0:
            # 按当前卡片宽度自适应列数(卡片越大,一行放得越少)
            cell = max(180, self._card_size[0] + 16)
            try:
                vw = max(cell, self._scroll.viewport().width() - 24)
                cols = max(1, vw // cell)
            except Exception:
                cols = 2
            cols = min(max(1, cols), 6)
        for idx, w in enumerate(self._items.values()):
            r, c = divmod(idx, cols)
            self._layout.addWidget(w, r, c)
        self._layout.setRowStretch(self._layout.rowCount(), 1)
        self._layout.setColumnStretch(cols, 1)