"""左侧预览网格.

顶部两条工具条:
    * 「多窗口同步」:勾选若干窗口卡片 → 同步启动 / 停止 / 广播配置
    * 「分屏分类」(v1.7.0):把窗口归入分组(如「需点击」「纯挂时长」),
      点击分类条上的组名即可只看该组窗口,并可对整组一键启动 / 停止
    * 卡片各自仍可独立配置(右侧面板只作用于「当前聚焦窗口」)
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QWidget, QGridLayout, QScrollArea, QVBoxLayout, QHBoxLayout,
    QFrame, QLabel, QPushButton, QSizePolicy, QToolButton,
)

from ..core.process_manager import ProcessManager, TrackedProcess
from ..core.automation_hub import AutomationHub
from .preview_widget import GROUP_PRESETS, PreviewWidget, group_color
from .widgets import styled_message


class PreviewGrid(QWidget):
    """左侧预览网格 + 多窗口同步工具条 + 分屏分类条."""

    selection_changed = Signal(list)        # list[int] 当前勾选的 hwnd
    sync_start_requested = Signal(list)     # 请求对勾选窗口同步启动
    sync_stop_requested = Signal(list)      # 请求对勾选窗口同步停止
    sync_broadcast_requested = Signal(list) # 请求把右侧当前配置广播到勾选窗口
    stop_process_requested = Signal(int)    # hwnd → 结束该游戏进程
    resume_process_requested = Signal(int)  # hwnd → 重新唤醒该游戏
    volume_requested = Signal(int)          # hwnd → 打开单窗口音量面板
    group_start_requested = Signal(list)    # v1.7.0 对当前分类下的整组窗口启动
    group_stop_requested = Signal(list)     # v1.7.0 对当前分类下的整组窗口停止
    # v1.8.3:卡片级信号统一转发(卡片是运行期动态创建的,由网格转发才不会漏连)
    focus_requested = Signal(int)              # hwnd 卡片「聚焦」/点预览图
    target_requested = Signal(int)             # hwnd 卡片标题左键 / 勾选 → 设为右侧目标
    close_requested = Signal(int)              # hwnd 卡片「×」
    autoclick_toggle_requested = Signal(int)   # hwnd 卡片「连点 / 停止」

    def __init__(
        self,
        pm: ProcessManager,
        *,
        fps: float = 1.0,
        columns: int = 0,  # 0=自适应
        card_size: tuple[int, int] = (240, 190),
        show_volume: bool = True,
        show_resource: bool = True,
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
        self._show_resource = bool(show_resource)
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
        self._btn_sync_start.setToolTip(
            "让所有勾选的窗口同时启动。\n"
            "每个窗口用自己已保存的配置,可以各不相同(不同逻辑并行);\n"
            "想让大家跑同一套,请先点旁边的「应用当前配置」。"
        )
        self._btn_sync_start.clicked.connect(
            lambda: self.sync_start_requested.emit(self.selected_hwnds()))
        hl.addWidget(self._btn_sync_start)

        self._btn_sync_stop = QPushButton("■ 同步停止")
        self._btn_sync_stop.setToolTip("同时停止所有勾选窗口的连点器 / 键盘宏")
        self._btn_sync_stop.clicked.connect(
            lambda: self.sync_stop_requested.emit(self.selected_hwnds()))
        hl.addWidget(self._btn_sync_stop)

        outer.addWidget(header)

        # ---------- v1.7.0 分屏分类条 ----------
        # 「需点击」= 挂机时要点按钮/收菜的游戏;「纯挂时长」= 挂着不动也行的游戏。
        # 点组名只显示该组窗口,「启动本组/停止本组」对整组批量操作。
        self._filter: str | None = None  # None=全部; ""=未分组; 其他=组名
        gbar = QWidget()
        gbar.setStyleSheet("background:#262626; border-bottom:1px solid #3c3c3c;")
        gl = QHBoxLayout(gbar)
        gl.setContentsMargins(8, 4, 8, 4)
        gl.setSpacing(4)

        glab = QLabel("分类:")
        glab.setStyleSheet("color:#999; font-size:11px;")
        gl.addWidget(glab)

        self._chip_layout = QHBoxLayout()
        self._chip_layout.setSpacing(4)
        gl.addLayout(self._chip_layout)
        gl.addStretch()

        self._btn_group_start = QPushButton("▶ 启动本组")
        self._btn_group_start.setToolTip("对当前选中的分类里的全部窗口同步启动(各自用已有配置)")
        self._btn_group_start.setEnabled(False)
        self._btn_group_start.clicked.connect(self._on_group_start)
        gl.addWidget(self._btn_group_start)

        self._btn_group_stop = QPushButton("■ 停止本组")
        self._btn_group_stop.setToolTip("对当前选中的分类里的全部窗口停止连点/宏等自动化")
        self._btn_group_stop.setEnabled(False)
        self._btn_group_stop.clicked.connect(self._on_group_stop)
        gl.addWidget(self._btn_group_stop)

        outer.addWidget(gbar)

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
        self._empty_label.setOpenExternalLinks(False)
        # 空态里的「← 返回全部」链接(分类过滤后该组无窗口时出现)
        self._empty_label.linkActivated.connect(lambda _l: self._on_filter_chip(None))
        self._layout.addWidget(self._empty_label, 0, 0)

        # v1.8.3:这里必须用 lambda 包一层。之前直接传 self._sync 会把这个
        # 绑定方法缓存进 pm.on_change,主窗口之后替换 left._sync(补连卡片信号)
        # 就对「运行期新添加的窗口」失效 —— 新卡片漏连 target_requested,
        # 表现为勾选/选中窗口后右侧仍显示「未选择目标窗口」。
        self._pm.on_change(lambda: self._sync())
        QTimer.singleShot(100, lambda: self._sync())

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

    def set_resource_visible(self, on: bool) -> None:
        """设置面板里「在卡片上显示 CPU / 内存」改动后调用."""
        self._show_resource = bool(on)
        for w in self._items.values():
            w.set_resource_visible(self._show_resource)

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

    # ---------- v1.7.0 分屏分类 ----------
    def _group_of(self, hwnd: int) -> str:
        item = self._pm.get(int(hwnd))
        return (item.group if item else "") or ""

    def _passes_filter(self, hwnd: int) -> bool:
        """None=全部;""=未分组;其他=只显示该组."""
        if self._filter is None:
            return True
        return self._group_of(hwnd) == self._filter

    def refresh_group_bar(self) -> None:
        """按当前进程数据重建分类条(组名 chip + 数量)."""
        names: list[str] = list(GROUP_PRESETS)
        try:
            for g in self._pm.all_groups():
                if g not in names:
                    names.append(g)
        except Exception:  # noqa: BLE001
            pass
        # 当前过滤目标已不存在 → 回到「全部」
        if self._filter is not None and self._filter not in names and self._filter != "":
            self._filter = None
        # 清空旧 chip
        while self._chip_layout.count():
            it = self._chip_layout.takeAt(0)
            w = it.widget()
            if w is not None:
                w.deleteLater()
        n_all = len(self._items)
        chips: list[tuple[str, str | None, int, str]] = []  # (文本, 过滤值, 数量, 颜色)
        chips.append((f"全部 {n_all}", None, n_all, "#9ad"))
        for g in names:
            cnt = sum(1 for h in self._items if self._group_of(h) == g)
            chips.append((f"{g} {cnt}", g, cnt, group_color(g)))
        n_un = sum(1 for h in self._items if not self._group_of(h))
        chips.append((f"未分组 {n_un}", "", n_un, "#888"))
        for text, fval, _cnt, color in chips:
            try:
                btn = QToolButton()
                btn.setText(text)
                btn.setCheckable(True)
                btn.setChecked(self._filter == fval)
                btn.setStyleSheet(
                    f"QToolButton {{ color:#ccc; border:1px solid #444; border-radius:8px;"
                    f"padding:1px 8px; font-size:11px; background:transparent; }}"
                    f"QToolButton:hover {{ border-color:{color}; }}"
                    f"QToolButton:checked {{ color:{color}; border-color:{color};"
                    f"background:rgba(255,255,255,0.06); font-weight:bold; }}"
                )
                btn.clicked.connect(lambda _c=False, v=fval: self._on_filter_chip(v))
                self._chip_layout.addWidget(btn)
            except Exception:  # noqa: BLE001
                # 单个 chip 出错不能拖垮整条分类条(全局兜底会吞异常,必须局部守住)
                logging.getLogger(__name__).exception("分类条 chip 创建失败: %r", text)
        has_filter = self._filter is not None
        self._btn_group_start.setEnabled(has_filter and n_all > 0)
        self._btn_group_stop.setEnabled(has_filter and n_all > 0)
        self._apply_filter()

    def _on_filter_chip(self, value: str | None) -> None:
        # 再点一次当前已选中的分类 = 返回「全部」(空分组时这就是最直觉的「返回上一级」)
        if value is not None and self._filter == value:
            self._filter = None
        else:
            self._filter = value
        self.refresh_group_bar()

    def _on_group_start(self) -> None:
        hwnds = [h for h in self._items if self._passes_filter(h)]
        if hwnds:
            self.group_start_requested.emit(hwnds)

    def _on_group_stop(self) -> None:
        hwnds = [h for h in self._items if self._passes_filter(h)]
        if hwnds:
            self.group_stop_requested.emit(hwnds)

    def _apply_filter(self) -> None:
        """按当前分类过滤卡片可见性并重排."""
        for hwnd, w in self._items.items():
            w.setVisible(self._passes_filter(hwnd))
        self._relayout()

    # ---------- 内部 ----------
    def _set_all(self, on: bool) -> None:
        # 全选 / 清空只作用于当前分类下可见的窗口(配合分屏分类做组级勾选)
        for hwnd, w in self._items.items():
            if self._passes_filter(hwnd):
                w.set_selected(on)

    def _invert(self) -> None:
        for hwnd, w in self._items.items():
            if self._passes_filter(hwnd):
                w.set_selected(not w.is_selected())

    def _on_card_selection_changed(self, hwnd: int, on: bool) -> None:
        self._update_sel_label()
        self.selection_changed.emit(self.selected_hwnds())

    def _update_sel_label(self) -> None:
        n = len(self.selected_hwnds())
        # 过滤状态下分母只算当前分类里的窗口,数量更直观
        if self._filter is None:
            total = len(self._items)
        else:
            total = sum(1 for h in self._items if self._passes_filter(h))
        suffix = f"(分类内 {total})" if self._filter is not None else f" / {total}"
        self._sel_lab.setText(f"已选 {n}{suffix}")
        has = n > 0
        for b in (self._btn_sync_start, self._btn_sync_stop, self._btn_broadcast):
            b.setEnabled(has)

    def _refresh_statuses(self) -> None:
        cpu_t, mem_t = self._resource_thresholds()
        for h, w in self._items.items():
            if self._hub is not None:
                w.set_status_extra(self._hub.status_text(h))
                w.set_autoclicker_state(self._hub.is_clicker_running(h))
            item = self._pm.get(h)
            if item is None or not item.alive:
                w.set_resource_text("")
                continue
            hot = ((cpu_t > 0 and item.cpu >= cpu_t)
                   or (mem_t > 0 and item.mem_mb >= mem_t))
            w.set_resource_text(self._fmt_resource(item), hot)

    @staticmethod
    def _fmt_resource(item: TrackedProcess) -> str:
        mem = float(item.mem_mb or 0.0)
        mem_txt = f"{mem / 1024:.1f}G" if mem >= 1024 else f"{mem:.0f}M"
        return f"{float(item.cpu or 0.0):.0f}% · {mem_txt}"

    @staticmethod
    def _resource_thresholds() -> tuple[float, float]:
        """读告警阈值;功能关掉时返回 (0, 0) 表示「只显示不标红」."""
        try:
            from ..utils.config import instance as _cfg_instance

            cfg = _cfg_instance()
            if not cfg.get("monitor.resource_alert_enabled", True):
                return (0.0, 0.0)
            return (
                float(cfg.get("monitor.cpu_alert_pct", 90)),
                float(cfg.get("monitor.mem_alert_mb", 4096)),
            )
        except Exception:  # noqa: BLE001
            return (0.0, 0.0)

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
                    show_resource=self._show_resource,
                    pm=self._pm,
                )
                w.selection_changed.connect(self._on_card_selection_changed)
                w.stop_process.connect(self.stop_process_requested.emit)
                w.resume_process.connect(self.resume_process_requested.emit)
                w.volume_requested.connect(self.volume_requested.emit)
                w.group_changed.connect(self._on_group_changed)  # v1.7.0
                # v1.8.3:卡片级信号在创建时就连到网格级信号,动态新增也不漏连
                w.focused.connect(self.focus_requested.emit)
                w.target_requested.connect(self.target_requested.emit)
                w.closed.connect(self.close_requested.emit)
                w.autoclick_toggle.connect(self.autoclick_toggle_requested.emit)
                self._items[it.hwnd] = w
            else:
                # 已存在的卡片:别名 / 强调色可能在别处改过,顺手刷新
                self._items[it.hwnd].refresh_alias()
        # 空提示
        self._empty_label.setVisible(not self._items)
        self.refresh_group_bar()
        self._update_sel_label()

    def _on_group_changed(self) -> None:
        """某张卡片改了分组 → 重建分类条并重新过滤."""
        self.refresh_group_bar()

    def _relayout(self) -> None:
        # 清空 layout(保留 widget 引用)
        while self._layout.count():
            self._layout.takeAt(0)
        n = len(self._items)
        if n == 0:
            self._empty_label.setText(
                "尚未选择任何进程。\n\n"
                "点击左上角「添加窗口」,选择游戏窗口,即可开始挂机。\n"
                "支持任意进程,不限 Steam。\n\n"
                "三种用法:\n"
                "· 单窗口:右侧配置只对该窗口生效,单独启停;\n"
                "· 勾选多个后「同步启动」:各窗口用自己的配置同时跑(可互不相同);\n"
                "· 右键卡片标题可分组(分屏分类),按分类整组启停。"
            )
            self._empty_label.setVisible(True)
            self._layout.addWidget(self._empty_label, 0, 0)
            return
        # v1.7.0:只摆放通过分类过滤的卡片(隐藏的卡片会被布局自动跳过)
        visible = [w for h, w in self._items.items() if self._passes_filter(h)]
        if not visible:
            self._empty_label.setText(
                "该分类下暂无窗口。\n\n"
                '<a href="#all" style="color:#4a9eff;">← 返回「全部」</a>'
                "(或再点一次上方选中的分类)\n\n"
                "右键卡片标题 →「分组(分屏分类)」可把窗口归入分类。"
            )
            self._empty_label.setVisible(True)
            self._layout.addWidget(self._empty_label, 0, 0)
            return
        self._empty_label.setVisible(False)
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
        for idx, w in enumerate(visible):
            r, c = divmod(idx, cols)
            self._layout.addWidget(w, r, c)
        self._layout.setRowStretch(self._layout.rowCount(), 1)
        self._layout.setColumnStretch(cols, 1)