"""单个窗口预览卡片.

支持:
- 勾选(用于「多窗口同步执行」)
- 实时画面预览(GDI PrintWindow,被遮挡也能截到)
- **窗口嵌入**:点击「嵌入」把原窗口收进卡片内显示,桌面上不再单独出现;
  再点「弹出」完整还原。卡片关闭/程序退出时自动还原。
- **别名 / 强调色**:右键卡片可给窗口起名字(多开时一眼分得清)并选一个边框色。
- **资源占用角标**:显示该窗口的 CPU / 内存,超阈值时标红。
"""

from __future__ import annotations

import time

from PySide6.QtCore import Qt, Signal, QTimer, QEvent, QPoint, QRect
from PySide6.QtGui import QAction, QColor, QImage, QPainter, QPixmap, QPen
from PySide6.QtWidgets import (
    QWidget, QLabel, QVBoxLayout, QHBoxLayout, QPushButton,
    QFrame, QSizePolicy, QCheckBox, QInputDialog, QMenu, QDialog,
)

from ..core.preview_capture import PreviewCapture, PreviewFrame
from ..core import window_finder as wf
from ..core import window_host
from ..utils.sanitize import sanitize
from .widgets import styled_message

# 右键菜单里可选的强调色(名称 → 十六进制,空字符串 = 不强调)
CARD_COLORS: tuple[tuple[str, str], ...] = (
    ("不强调", ""),
    ("红", "#e05555"),
    ("橙", "#e09040"),
    ("黄", "#d8c040"),
    ("绿", "#5aa85a"),
    ("青", "#40a8a8"),
    ("蓝", "#4a9eff"),
    ("紫", "#a06ad0"),
)

# v1.7.0 分屏分类:预设分组 + 组名 → 颜色(按名字稳定哈希,同组永远同色)
GROUP_PRESETS: tuple[str, ...] = ("需点击", "纯挂时长")
_GROUP_PALETTE = ("#4a9eff", "#8fd18f", "#e8b34b", "#e07b7b", "#b48ee0", "#5bc8c4", "#c8c8c8")


def group_color(name: str) -> str:
    h = 0
    for ch in name or "?":
        h = (h * 31 + ord(ch)) & 0xFFFFFFFF
    return _GROUP_PALETTE[h % len(_GROUP_PALETTE)]


class _EmbedViewOverlay(QWidget):
    """嵌入画面的「调整视图」覆盖层(v1.8.4).

    游戏原窗口是原生 HWND,不盖一层的话鼠标事件全被游戏吃掉,根本没法拖动。
    这层原生子窗口盖在游戏窗口之上接管鼠标:
        按住拖动 = 平移画面    滚轮 = 缩放
        双击 = 铺满并退出      Esc = 退出(保留当前画面位置)
    """

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        # 原生子窗口才能稳稳压在游戏 HWND 之上、拿到鼠标事件
        self.setAttribute(Qt.WidgetAttribute.WA_NativeWindow)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.SizeAllCursor)
        self._press: QPoint | None = None
        # 回调(由 PreviewWidget 注入)
        self.drag_delta = None      # cb(dx_logical, dy_logical)
        self.zoom_factor = None     # cb(factor)
        self.fill_requested = None  # cb(双击 → 铺满并退出)
        self.exit_requested = None  # cb(Esc → 退出,保留位置)

    def mousePressEvent(self, ev) -> None:  # noqa: N802
        if ev.button() == Qt.MouseButton.LeftButton:
            self._press = ev.position().toPoint()

    def mouseMoveEvent(self, ev) -> None:  # noqa: N802
        if self._press is None:
            return
        cur = ev.position().toPoint()
        d = cur - self._press
        self._press = cur
        if self.drag_delta is not None and (d.x() or d.y()):
            try:
                self.drag_delta(d.x(), d.y())
            except Exception:
                pass

    def mouseReleaseEvent(self, ev) -> None:  # noqa: N802
        self._press = None

    def mouseDoubleClickEvent(self, ev) -> None:  # noqa: N802
        if ev.button() == Qt.MouseButton.LeftButton and self.fill_requested is not None:
            self.fill_requested()

    def wheelEvent(self, ev) -> None:  # noqa: N802
        steps = ev.angleDelta().y() / 120.0
        if steps and self.zoom_factor is not None:
            try:
                self.zoom_factor(1.15 ** steps)
            except Exception:
                pass

    def keyPressEvent(self, ev) -> None:  # noqa: N802
        if ev.key() == Qt.Key.Key_Escape and self.exit_requested is not None:
            self.exit_requested()

    def paintEvent(self, ev) -> None:  # noqa: N802
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(8, 10, 14, 120))
        p.setPen(QPen(QColor("#4a9eff"), 2))
        p.drawRect(self.rect().adjusted(1, 1, -2, -2))
        f = p.font()
        f.setPointSize(10)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor("#d8ecff"))
        p.drawText(QRect(0, 8, self.width(), 44),
                   Qt.AlignmentFlag.AlignHCenter,
                   "拖动 = 移动画面 · 滚轮 = 缩放\n双击 = 铺满 · Esc = 完成调整")
        p.end()


class PreviewWidget(QFrame):
    """单个窗口预览卡片(可勾选 / 可嵌入)."""

    focused = Signal(int)  # hwnd(点击预览图/「聚焦」:抢前台 + 设为右侧目标)
    target_requested = Signal(int)  # hwnd(勾选/左键标题:仅设为右侧目标,不抢前台)
    closed = Signal(int)  # hwnd(请求从追踪列表移除)
    autoclick_toggle = Signal(int)  # hwnd
    selection_changed = Signal(int, bool)  # hwnd, 是否选中
    stop_process = Signal(int)  # hwnd → 结束该游戏进程
    resume_process = Signal(int)  # hwnd → 重新唤醒该游戏
    volume_requested = Signal(int)  # hwnd → 打开单窗口音量面板
    group_changed = Signal()  # v1.7.0 分组标签变化(网格据此重建分类条)

    def __init__(
        self,
        hwnd: int,
        *,
        fps: float = 1.0,
        phase: float = 0.0,
        card_size: tuple[int, int] | None = None,
        show_volume: bool = True,
        show_resource: bool = True,
        pm=None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.hwnd = int(hwnd)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setObjectName("PreviewCard")
        cw, ch = card_size or (240, 190)
        self.setMinimumSize(int(cw), int(ch))
        self._capture = PreviewCapture(self.hwnd, fps=fps, phase=phase)
        self._capture.on_frame = self._on_frame
        self._autoclicker_running = False
        self._fps = fps
        self._status_extra = ""
        self._embedded = False
        self._frame_pending = False  # 截图渲染节流标记(见 _on_frame)
        self._show_volume = bool(show_volume)
        self._show_resource = bool(show_resource)
        self._pm = pm
        self._res_text = ""
        self._res_hot = False
        self._proc_alive = True
        self._last_pid = 0
        self._apply_style()
        _info = wf.get_window_info(self.hwnd)
        if _info is not None:
            self._last_pid = _info.pid

        v = QVBoxLayout(self)
        v.setContentsMargins(4, 4, 4, 4)
        v.setSpacing(4)

        # 标题栏
        title_bar = QHBoxLayout()
        title_bar.setSpacing(4)
        self._chk = QCheckBox()
        self._chk.setToolTip("勾选后可与其它勾选窗口「同步执行」同一套操作")
        self._chk.toggled.connect(self._on_check_toggled)
        title_bar.addWidget(self._chk)

        self._title_lab = QLabel("加载中...")
        self._title_lab.setStyleSheet("color:white;")
        f = self._title_lab.font()
        f.setPointSize(9)
        f.setBold(True)
        self._title_lab.setFont(f)
        self._title_lab.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        if self._pm is not None:
            self._title_lab.setToolTip(
                "左键:设为右侧操作面板的目标窗口(不抢前台)\n"
                "右键:起别名 / 选强调色 / 分组(多开时方便分辨)"
            )
            self._title_lab.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            self._title_lab.customContextMenuRequested.connect(self._on_title_menu)
        # 左键点标题 = 把它设为右侧面板目标(不抢前台,区别于点预览图的「聚焦」)
        self._title_lab.mousePressEvent = self._on_title_press  # type: ignore[assignment]
        title_bar.addWidget(self._title_lab)

        # v1.7.0 分屏分类徽标(「需点击」/「纯挂时长」/自定义)
        self._group_badge = QLabel("")
        self._group_badge.setVisible(False)
        self._group_badge.setCursor(Qt.CursorShape.PointingHandCursor)
        self._group_badge.mousePressEvent = self._on_badge_press  # type: ignore[assignment]
        self._group_badge.setStyleSheet(
            "color:#4a9eff; border:1px solid #4a9eff; border-radius:6px;"
            "padding:0 5px; font-size:10px; background:rgba(74,158,255,0.12);"
        )
        title_bar.addWidget(self._group_badge)

        # v1.8.7 标签徽标(#标签,点一下可编辑)
        self._tags_badge = QLabel("")
        self._tags_badge.setVisible(False)
        self._tags_badge.setCursor(Qt.CursorShape.PointingHandCursor)
        self._tags_badge.setStyleSheet(
            "color:#9fd6a8; border:1px solid #4a8f5a; border-radius:6px;"
            "padding:0 5px; font-size:10px; background:rgba(90,168,90,0.12);"
        )
        self._tags_badge.mousePressEvent = self._on_badge_press  # type: ignore[assignment]
        title_bar.addWidget(self._tags_badge)

        # v1.8.7 标签与分类设置入口(显式按钮,不用去翻右键菜单)
        self._btn_tag = QPushButton("🏷")
        self._btn_tag.setFixedWidth(28)
        self._btn_tag.setToolTip("设置这个窗口的「分类」和「标签」(可多选),多开时方便区分和筛选")
        self._btn_tag.clicked.connect(self._on_edit_tags)
        title_bar.addWidget(self._btn_tag)

        self._btn_embed = QPushButton("嵌入")
        self._btn_embed.setCheckable(True)
        self._btn_embed.setFixedWidth(44)
        self._btn_embed.setToolTip(
            "把原窗口收进这张卡片里显示(桌面/任务栏上不再单独出现),\n"
            "画面直接可见可操作;再点「弹出」还原到桌面。"
        )
        self._btn_embed.toggled.connect(self._on_embed_toggled)
        title_bar.addWidget(self._btn_embed)

        self._btn_focus = QPushButton("聚焦")
        self._btn_focus.setFixedWidth(44)
        self._btn_focus.clicked.connect(self._on_focus_clicked)
        title_bar.addWidget(self._btn_focus)

        self._btn_edge = QPushButton("边距")
        self._btn_edge.setFixedWidth(44)
        self._btn_edge.setToolTip(
            "设置这个窗口聚焦时的上/下/左/右边距和画面缩放比例,各窗口独立保存"
        )
        self._btn_edge.clicked.connect(self._on_edge_clicked)
        title_bar.addWidget(self._btn_edge)

        # v1.8.4:嵌入画面的查看方式(仅嵌入时可见)
        self._view_mode = "fill"      # fill=铺满 / fit=完整显示(保持比例) / free=自由查看
        self._view_zoom = 1.0         # free 模式:相对游戏原始客户区尺寸的缩放
        self._view_off = (0, 0)       # free 模式:游戏窗口左上角在容器内的物理像素偏移
        self._view_overlay: _EmbedViewOverlay | None = None
        self._btn_view = QPushButton("视图")
        self._btn_view.setFixedWidth(44)
        self._btn_view.setToolTip(
            "嵌入画面的查看方式:\n"
            "· 铺满容器 —— 拉伸铺满(原默认,比例不同会变形/裁边)\n"
            "· 完整显示 —— 保持比例缩小到正好显示整个游戏画面\n"
            "· 自由查看 —— 拖动移动画面 / 滚轮缩放,看到游戏某一局部"
        )
        self._btn_view.clicked.connect(self._on_view_menu)
        self._btn_view.setVisible(False)
        title_bar.addWidget(self._btn_view)

        self._btn_close = QPushButton("✕")
        self._btn_close.setFixedWidth(28)
        self._btn_close.setStyleSheet("QPushButton { color:#ff8888; }")
        self._btn_close.clicked.connect(lambda: self.closed.emit(self.hwnd))
        title_bar.addWidget(self._btn_close)
        v.addLayout(title_bar)

        # 预览图(同时也是嵌入容器)
        self._image_lab = QLabel()
        self._image_lab.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._image_lab.setStyleSheet("background:#000; color:#888;")
        self._image_lab.setMinimumHeight(120)
        self._image_lab.setText("(无图像)")
        self._image_lab.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        v.addWidget(self._image_lab, stretch=1)

        # 底部状态栏:状态文字 + 音量 / 停止进程 / 恢复进程 / 连点
        status_bar = QHBoxLayout()
        status_bar.setSpacing(3)
        self._status_lab = QLabel("未运行")
        self._status_lab.setStyleSheet("color:#aaa; font-size:11px;")
        # Ignored:状态文字不参与最小宽度计算,避免把卡片撑宽
        self._status_lab.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._status_lab.setMinimumWidth(0)
        status_bar.addWidget(self._status_lab, stretch=1)

        self._res_lab = QLabel("")
        self._res_lab.setStyleSheet("color:#888; font-size:10px;")
        self._res_lab.setToolTip("该窗口的 CPU / 内存占用(主进程 + 子进程内存)")
        self._res_lab.setVisible(self._show_resource)
        status_bar.addWidget(self._res_lab)

        self._btn_volume = QPushButton("♪")
        self._btn_volume.setFixedSize(24, 22)
        self._btn_volume.setToolTip("调节该窗口(游戏进程)的音量,不影响系统整体音量")
        self._btn_volume.clicked.connect(lambda: self.volume_requested.emit(self.hwnd))
        self._btn_volume.setVisible(self._show_volume)
        status_bar.addWidget(self._btn_volume)

        self._btn_stop = QPushButton("■")
        self._btn_stop.setFixedSize(24, 22)
        self._btn_stop.setStyleSheet("QPushButton { color:#ff9090; }")
        self._btn_stop.setToolTip("结束该游戏进程(连同子进程)")
        self._btn_stop.clicked.connect(lambda: self.stop_process.emit(self.hwnd))
        status_bar.addWidget(self._btn_stop)

        self._btn_resume = QPushButton("▶")
        self._btn_resume.setFixedSize(24, 22)
        self._btn_resume.setStyleSheet("QPushButton { color:#8fd18f; }")
        self._btn_resume.setToolTip(
            "重新唤醒该游戏进程。\n"
            "Steam 游戏会先唤起 Steam 客户端,再通过 steam:// 拉起游戏。"
        )
        self._btn_resume.clicked.connect(lambda: self.resume_process.emit(self.hwnd))
        status_bar.addWidget(self._btn_resume)

        self._btn_autoclick = QPushButton("连点")
        self._btn_autoclick.setCheckable(True)
        self._btn_autoclick.setFixedWidth(44)
        self._btn_autoclick.toggled.connect(self._on_autoclick_toggle)
        status_bar.addWidget(self._btn_autoclick)
        v.addLayout(status_bar)

        # 点击预览 → 窗口聚焦
        self._image_lab.mousePressEvent = self._on_image_click  # type: ignore[assignment]
        self._image_lab.mouseDoubleClickEvent = self._on_image_dblclick  # type: ignore[assignment]

        # 状态刷新定时器
        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self._refresh_status)
        self._refresh_timer.start(1000)
        self._added_at = time.time()

        self._capture.start()
        self._refresh_status()

    # --- 公共 ---
    def set_fps(self, fps: float) -> None:
        self._capture.set_fps(fps)
        self._fps = fps

    def set_card_size(self, width: int, height: int) -> None:
        """由设置面板驱动:卡片最小尺寸."""
        self.setMinimumSize(max(160, int(width)), max(120, int(height)))
        self.updateGeometry()

    def set_volume_button_visible(self, on: bool) -> None:
        self._show_volume = bool(on)
        self._btn_volume.setVisible(self._show_volume)

    def set_resource_visible(self, on: bool) -> None:
        self._show_resource = bool(on)
        self._res_lab.setVisible(self._show_resource)

    def set_resource_text(self, text: str, hot: bool = False) -> None:
        """由预览网格每轮刷新推送:卡片的 CPU / 内存角标."""
        text = text or ""
        if text == self._res_text and hot == self._res_hot:
            return
        self._res_text, self._res_hot = text, bool(hot)
        if not self._show_resource:
            return
        self._res_lab.setText(text)
        self._res_lab.setStyleSheet(
            "color:#e08080; font-size:10px; font-weight:bold;" if hot
            else "color:#888; font-size:10px;"
        )
        if hot:
            self._res_lab.setToolTip("占用偏高 —— 具体数值看设置里的阈值,不会自动处置")
        else:
            self._res_lab.setToolTip("该窗口的 CPU / 内存占用(主进程 + 子进程内存)")

    # --- 别名 / 强调色 ---
    def _alias(self) -> str:
        if self._pm is None:
            return ""
        item = self._pm.get(self.hwnd)
        return (item.alias if item else "") or ""

    def _color(self) -> str:
        if self._pm is None:
            return ""
        item = self._pm.get(self.hwnd)
        return (item.color if item else "") or ""

    # === v1.7.0 分屏分类 ===
    def _group_name(self) -> str:
        if self._pm is None:
            return ""
        item = self._pm.get(self.hwnd)
        return (item.group if item else "") or ""

    def _set_group(self, group: str) -> None:
        if self._pm is None:
            return
        self._pm.set_group(self.hwnd, group)
        self._update_group_badge()
        self.group_changed.emit()

    def _on_new_group(self) -> None:
        name, ok = QInputDialog.getText(
            self, "新建分组",
            "分组名(如:需点击 / 纯挂时长 / 主力号):\n"
            "同名窗口归入同一组,可在顶部分类条里按组过滤和批量启动。",
        )
        if ok and name.strip():
            self._set_group(name.strip())

    def _update_group_badge(self) -> None:
        g = self._group_name()
        if g == getattr(self, "_last_group_shown", None):
            return  # 没变化就不重设样式(每秒刷一次,避免无谓的重新渲染)
        self._last_group_shown = g
        self._group_badge.setText(g)
        self._group_badge.setVisible(bool(g))
        if g:
            c = group_color(g)
            self._group_badge.setStyleSheet(
                f"color:{c}; border:1px solid {c}; border-radius:6px;"
                f"padding:0 5px; font-size:10px;"
            )
            self._group_badge.setToolTip(f"分类:{g}\n点击可修改分类和标签")
            self._group_badge.setCursor(Qt.CursorShape.PointingHandCursor)

    def _apply_style(self) -> None:
        """边框:选中 > 自定义强调色 > 默认;悬停统一高亮."""
        chk = getattr(self, "_chk", None)
        selected = bool(chk is not None and chk.isChecked())
        border = "#4a9eff" if selected else (self._color() or "#3c3c3c")
        width = 3 if border not in ("#3c3c3c", "#4a9eff") else 2
        self.setStyleSheet(
            f"#PreviewCard {{ border: {width}px solid {border}; background:#222; }}"
            "#PreviewCard:hover { border: 2px solid #4a9eff; }"
        )

    def _on_title_menu(self, pos) -> None:
        """右键标题:起别名 / 选强调色."""
        if self._pm is None:
            return
        menu = QMenu(self)
        # v1.8.7:标签与分类统一入口(分类 + 多个标签一次设完)
        act_tag = QAction("🏷 标签与分类...", self)
        act_tag.triggered.connect(self._on_edit_tags)
        menu.addAction(act_tag)
        menu.addSeparator()

        act_rename = QAction("重命名(别名)...", self)
        act_rename.triggered.connect(self._on_rename)
        menu.addAction(act_rename)

        sub = menu.addMenu("强调色")
        cur = self._color()
        for name, hexv in CARD_COLORS:
            act = QAction(("● " if hexv else "○ ") + name, self)
            act.setCheckable(True)
            act.setChecked((cur or "") == hexv)
            act.triggered.connect(lambda _c=False, h=hexv: self._set_color(h))
            sub.addAction(act)

        act_clear = QAction("清除别名", self)
        act_clear.setEnabled(bool(self._alias()))
        act_clear.triggered.connect(lambda: self._set_alias(""))
        menu.addAction(act_clear)

        # v1.7.0 分屏分类:把窗口归入「需点击」/「纯挂时长」等分组
        sub_g = menu.addMenu("分组(分屏分类)")
        cur_g = self._group_name()
        names = list(GROUP_PRESETS)
        try:
            for g in self._pm.all_groups():
                if g not in names:
                    names.append(g)
        except Exception:  # noqa: BLE001
            pass
        for g in names:
            act = QAction(("● " if cur_g == g else "○ ") + g, self)
            act.setCheckable(True)
            act.setChecked(cur_g == g)
            act.triggered.connect(lambda _c=False, s=g: self._set_group(s))
            sub_g.addAction(act)
        act_ungroup = QAction("未分组", self)
        act_ungroup.setCheckable(True)
        act_ungroup.setChecked(not cur_g)
        act_ungroup.triggered.connect(lambda: self._set_group(""))
        sub_g.addAction(act_ungroup)
        sub_g.addSeparator()
        act_new_g = QAction("新建分组...", self)
        act_new_g.triggered.connect(self._on_new_group)
        sub_g.addAction(act_new_g)

        # v1.8.6:省电降帧(EcoQoS)—— 把游戏进程设为能效模式+低优先级,
        # 嵌入/后台挂机时显著降低 CPU 占用(游戏帧率会随之降低)。
        menu.addSeparator()
        act_eco = QAction("省电降帧(降低 CPU 占用)", self)
        act_eco.setCheckable(True)
        try:
            act_eco.setChecked(self._pm.eco_mode(self.hwnd))
        except Exception:  # noqa: BLE001
            pass
        act_eco.setToolTip(
            "把该游戏进程设为能效模式(EcoQoS)+ 低于正常的优先级:\n"
            "CPU 占用明显下降,软件多开不卡;代价是游戏帧率降低。\n"
            "适合「放在软件里挂着」的窗口;要手动玩时关掉即可。"
        )
        act_eco.triggered.connect(self._on_eco_toggled)
        menu.addAction(act_eco)

        menu.exec(self._title_lab.mapToGlobal(pos))

    # --- v1.8.7 标签与分类 ---
    def _on_badge_press(self, ev) -> None:  # noqa: N802
        """点标签徽标 = 打开标签与分类设置."""
        if ev.button() == Qt.MouseButton.LeftButton:
            self._on_edit_tags()

    def _on_edit_tags(self) -> None:
        """打开「标签与分类」对话框,设置当前窗口的分类和标签."""
        if self._pm is None:
            return
        try:
            item = self._pm.get(self.hwnd)
        except Exception:  # noqa: BLE001
            item = None
        if item is None:
            return
        from .tag_dialog import TagGroupDialog

        alias = getattr(item, "alias", "") or ""
        label = f"[{item.pid}] {alias or sanitize(item.title)}"
        dlg = TagGroupDialog(
            group=item.group,
            tags=list(item.tags),
            known_groups=self._pm.all_groups(),
            known_tags=self._pm.all_tags(),
            window_label=label,
            parent=self,
        )
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            self._pm.set_group(self.hwnd, dlg.group())
            self._pm.set_tags(self.hwnd, dlg.tags())
            self._update_group_badge()
            self._update_tags_badge(force=True)
            # 让网格重建筛选条(可能新增了分类 / 标签)
            self.group_changed.emit()
            self._refresh_status()
        except Exception:  # noqa: BLE001
            pass

    def _update_tags_badge(self, *, force: bool = False) -> None:
        """把该窗口的标签显示成 #a #b(最多 3 个,其余折叠成 +N)."""
        if self._pm is None:
            self._tags_badge.setVisible(False)
            return
        try:
            tags = self._pm.tags_of(self.hwnd)
        except Exception:  # noqa: BLE001
            tags = []
        shown = ("|".join(tags),)
        if not force and getattr(self, "_tags_shown", None) == shown:
            return
        self._tags_shown = shown
        if not tags:
            self._tags_badge.setVisible(False)
            self._tags_badge.setText("")
            return
        head = " ".join(f"#{t}" for t in tags[:3])
        if len(tags) > 3:
            head += f" +{len(tags) - 3}"
        self._tags_badge.setText(head)
        self._tags_badge.setToolTip("标签:" + "、".join(tags) + "\n点击可编辑")
        self._tags_badge.setVisible(True)

    def _on_eco_toggled(self, on: bool) -> None:
        if self._pm is None:
            return
        try:
            ok = self._pm.set_eco_mode(self.hwnd, bool(on))
            if ok:
                self._refresh_status()
        except Exception:  # noqa: BLE001
            pass

    def _on_rename(self) -> None:
        info = wf.get_window_info(self.hwnd)
        default = self._alias() or (sanitize(info.title) if info else "")
        text, ok = QInputDialog.getText(
            self, "窗口别名",
            "给这个窗口起个名字(留空恢复成窗口标题):\n"
            "多开同一个游戏时,用别名一眼就能分清是哪个号。",
            text=default,
        )
        if ok:
            self._set_alias(text.strip())

    def _set_alias(self, alias: str) -> None:
        if self._pm is None:
            return
        self._pm.set_alias(self.hwnd, alias)
        self._refresh_status()

    def _set_color(self, color: str) -> None:
        if self._pm is None:
            return
        self._pm.set_alias(self.hwnd, self._alias(), color)
        self._apply_style()

    def pid(self) -> int:
        """当前 pid(窗口失效时回退到最近一次记录的值)."""
        info = wf.get_window_info(self.hwnd)
        if info is not None and info.pid:
            self._last_pid = info.pid
        return self._last_pid

    def refresh_now(self) -> None:
        """外部(如设置变更后)立刻刷新一次状态."""
        self._refresh_status()

    def refresh_alias(self) -> None:
        """别名 / 强调色变更后刷新标题与边框."""
        self._apply_style()
        self._refresh_status()

    def is_selected(self) -> bool:
        return self._chk.isChecked()

    def set_selected(self, on: bool) -> None:
        self._chk.setChecked(bool(on))

    def is_embedded(self) -> bool:
        return self._embedded

    def set_autoclicker_state(self, running: bool) -> None:
        """由 main_window 在连点器状态变化时同步."""
        self._autoclicker_running = running
        if self._btn_autoclick.isChecked() != running:
            self._btn_autoclick.blockSignals(True)
            self._btn_autoclick.setChecked(running)
            self._btn_autoclick.blockSignals(False)
        self._btn_autoclick.setText("停止" if running else "连点")
        self._refresh_status()

    def set_status_extra(self, text: str) -> None:
        """预览网格统一刷新运行状态(来自 AutomationHub)."""
        self._status_extra = text or ""
        self._refresh_status()

    def stop(self) -> None:
        """卡片销毁前调用:必须先还原嵌入窗口,再停截图."""
        self._release_embed(quiet=True)
        self._capture.stop()
        self._refresh_timer.stop()

    # --- 嵌入 ---
    def _on_embed_toggled(self, on: bool) -> None:
        if on:
            if not self._do_embed():
                self._btn_embed.blockSignals(True)
                self._btn_embed.setChecked(False)
                self._btn_embed.blockSignals(False)
        else:
            self._release_embed()

    def _do_embed(self) -> bool:
        """把原窗口收进预览区."""
        info = wf.get_window_info(self.hwnd)
        if info is None:
            return False
        if window_host.is_embedded(self.hwnd):
            return True
        # 保证原生句柄存在
        container = int(self._image_lab.winId())
        ok = window_host.embed_window(self.hwnd, container)
        if not ok:
            return False
        self._embedded = True
        self._capture.stop()  # 画面直接可见,截图线程省掉
        self._image_lab.setText("")
        self._image_lab.setPixmap(QPixmap())
        self._image_lab.installEventFilter(self)
        self._btn_embed.setText("弹出")
        # v1.8.4:嵌入后启用「视图」,默认铺满(与旧行为一致)
        self._view_mode = "fill"
        self._view_zoom = 1.0
        self._view_off = (0, 0)
        self._show_adjust_overlay(False)
        self._btn_view.setVisible(True)
        self._refresh_status()
        return True

    def _release_embed(self, quiet: bool = False) -> None:
        """把窗口还原回桌面."""
        self._image_lab.removeEventFilter(self)
        self._show_adjust_overlay(False)  # v1.8.4:弹出时收掉调整层
        self._view_mode = "fill"
        self._btn_view.setVisible(False)
        if window_host.release_window(self.hwnd) or self._embedded:
            self._embedded = False
            self._btn_embed.blockSignals(True)
            self._btn_embed.setChecked(False)
            self._btn_embed.blockSignals(False)
            self._btn_embed.setText("嵌入")
            self._image_lab.setText("(无图像)")
            self._capture.start()
            if not quiet:
                self._refresh_status()

    def eventFilter(self, obj, ev) -> bool:  # noqa: N802
        """嵌入窗口跟随容器尺寸变化(逻辑像素 → 物理像素)."""
        if obj is self._image_lab and self._embedded and ev.type() == QEvent.Type.Resize:
            try:
                # v1.8.4:按当前查看模式重摆(铺满=旧行为;fit/free 保持各自语义)
                self._apply_embed_view()
                if self._view_overlay is not None and self._view_overlay.isVisible():
                    self._view_overlay.setGeometry(self._image_lab.rect())
            except Exception:
                pass
        return super().eventFilter(obj, ev)

    # === v1.8.4:嵌入画面查看模式 ===
    def _on_view_menu(self) -> None:
        if not self._embedded:
            return
        menu = QMenu(self)
        for key, label in (
            ("fill", "铺满容器(拉伸,可能变形)"),
            ("fit", "完整显示(保持比例,整个画面可见)"),
            ("free", "自由查看(拖动移动 · 滚轮缩放)"),
        ):
            act = QAction(("● " if self._view_mode == key else "○ ") + label, self)
            act.triggered.connect(lambda _c=False, k=key: self._set_view_mode(k))
            menu.addAction(act)
        menu.exec(self._btn_view.mapToGlobal(QPoint(0, self._btn_view.height())))

    def _set_view_mode(self, mode: str) -> None:
        if not self._embedded:
            return
        if mode == "free":
            if self._view_mode != "free":
                self._init_free_from_fit()  # 从「完整显示」的缩放起步
            self._view_mode = "free"
            self._show_adjust_overlay(True)
        else:
            self._view_mode = mode
            self._show_adjust_overlay(False)
        self._apply_embed_view()
        self._refresh_status()

    def _dpr(self) -> float:
        try:
            return float(self.devicePixelRatioF()) or 1.0
        except Exception:
            return 1.0

    def _embed_viewport(self) -> tuple[int, int]:
        """容器(预览区)的物理像素尺寸."""
        return (max(32, int(self._image_lab.width() * self._dpr())),
                max(32, int(self._image_lab.height() * self._dpr())))

    def _natural_size(self, cw: int, ch: int) -> tuple[int, int]:
        nat = window_host.natural_client_size(self.hwnd)
        nw, nh = nat if nat else (cw, ch)
        return (max(16, int(nw)), max(16, int(nh)))

    def _apply_embed_view(self) -> None:
        """按当前查看模式摆放嵌入的游戏窗口(物理像素).

        fill:铺满容器(旧行为);fit:按原始宽高比完整显示居中;
        free:按 zoom/offset 任意摆放,超出容器部分被裁掉 = 平移缩放。
        """
        if not self._embedded:
            return
        cw, ch = self._embed_viewport()
        nw, nh = self._natural_size(cw, ch)
        if self._view_mode == "fit":
            s = min(cw / nw, ch / nh)
            w, h = max(16, int(nw * s)), max(16, int(nh * s))
            window_host.place(self.hwnd, (cw - w) // 2, (ch - h) // 2, w, h)
        elif self._view_mode == "free":
            z = max(0.05, min(8.0, float(self._view_zoom)))
            w, h = max(16, int(nw * z)), max(16, int(nh * z))
            x = max(-(w - 48), min(int(self._view_off[0]), cw - 48))
            y = max(-(h - 48), min(int(self._view_off[1]), ch - 48))
            self._view_off = (x, y)
            window_host.place(self.hwnd, x, y, w, h)
        else:  # fill
            window_host.place(self.hwnd, 0, 0, cw, ch)

    def _init_free_from_fit(self) -> None:
        """进入自由查看时,先按「完整显示」的缩放居中,方便用户再放大. """
        cw, ch = self._embed_viewport()
        nw, nh = self._natural_size(cw, ch)
        s = min(cw / nw, ch / nh)
        self._view_zoom = max(0.05, min(8.0, s))
        w, h = int(nw * s), int(nh * s)
        self._view_off = ((cw - w) // 2, (ch - h) // 2)

    def _on_view_drag(self, dx: int, dy: int) -> None:
        if self._view_mode != "free":
            return
        d = self._dpr()
        ox, oy = self._view_off
        self._view_off = (ox + int(dx * d), oy + int(dy * d))
        self._apply_embed_view()

    def _on_view_zoom(self, factor: float) -> None:
        if self._view_mode != "free":
            return
        cw, ch = self._embed_viewport()
        z0 = float(self._view_zoom)
        z1 = max(0.05, min(8.0, z0 * factor))
        if z1 == z0:
            return
        ratio = z1 / z0
        cx, cy = cw / 2.0, ch / 2.0
        ox, oy = self._view_off
        self._view_zoom = z1
        self._view_off = (int(cx - (cx - ox) * ratio), int(cy - (cy - oy) * ratio))
        self._apply_embed_view()

    def _on_view_fill(self) -> None:
        self._view_mode = "fill"
        self._show_adjust_overlay(False)
        self._apply_embed_view()
        self._refresh_status()

    def _show_adjust_overlay(self, on: bool) -> None:
        """显示/隐藏「调整视图」覆盖层(显示期间游戏不再直接吃鼠标)."""
        if on:
            if self._view_overlay is None:
                ov = _EmbedViewOverlay(self._image_lab)
                ov.drag_delta = self._on_view_drag
                ov.zoom_factor = self._on_view_zoom
                ov.fill_requested = self._on_view_fill
                ov.exit_requested = lambda: self._show_adjust_overlay(False)
                self._view_overlay = ov
            self._view_overlay.setGeometry(self._image_lab.rect())
            self._view_overlay.show()
            self._view_overlay.raise_()
            self._view_overlay.setFocus()
        elif self._view_overlay is not None:
            self._view_overlay.hide()

    # --- 内部 ---
    def _on_title_press(self, ev) -> None:
        """左键点标题:设为右侧面板目标(不抢前台);右键交给上下文菜单."""
        if ev.button() == Qt.MouseButton.LeftButton:
            self.target_requested.emit(self.hwnd)

    def _on_check_toggled(self, on: bool) -> None:
        # 选中时高亮边框(自定义强调色仍保留在未选中状态)
        self._apply_style()
        # 勾选 = 用户正要操作这个窗口 → 顺手设为右侧面板目标
        if on:
            self.target_requested.emit(self.hwnd)
        self.selection_changed.emit(self.hwnd, bool(on))

    def _on_frame(self, frame: PreviewFrame) -> None:
        """截图回调(子线程).切到主线程渲染.

        两个防崩点:
        - ``QTimer.singleShot(0, self, ...)`` 带上接收者:卡片被销毁后,
          还没执行的回调会被 Qt 直接丢弃,不会在已删除的对象上渲染。
        - ``_frame_pending`` 节流:上一帧还没渲染完就丢弃新帧,
          避免窗口很多 / fps 调高时事件队列被截图回调刷爆。
        """
        if self._frame_pending:
            return
        self._frame_pending = True
        QTimer.singleShot(0, self, lambda: self._render_frame(frame))

    def _render_frame(self, frame: PreviewFrame) -> None:
        self._frame_pending = False
        if self._embedded:
            return
        try:
            # GDI 的 BGRA 数据,alpha 字节不可靠 → 用 BGRX8888 忽略 alpha
            img = QImage(frame.pixels, frame.width, frame.height, frame.bytes_per_line,
                         QImage.Format.Format_BGRX8888)
            pix = QPixmap.fromImage(img)
            target = self._image_lab.size()
            scaled = pix.scaled(target, Qt.AspectRatioMode.KeepAspectRatio,
                                Qt.TransformationMode.SmoothTransformation)
            self._image_lab.setPixmap(scaled)
        except RuntimeError:
            # 底层 C++ 对象已被销毁(卡片关闭竞态),静默丢弃
            pass
        except Exception:
            pass

    def _refresh_status(self) -> None:
        self._update_group_badge()  # v1.7.0 分组标签跟着进程数据走
        self._update_tags_badge()   # v1.8.7 标签徽标跟着进程数据走
        info = wf.get_window_info(self.hwnd)
        if info is None:
            self._title_lab.setText("[已失效]")
            self._title_lab.setStyleSheet("color:#888;")
            alive = self._is_proc_alive()
            self._set_status_text(
                "进程未运行(可点 ▶ 唤醒)" if not alive
                else styled_message("窗口已失效", level="warn")
            )
            self._image_lab.setText("(窗口已失效)")
            self._image_lab.setPixmap(QPixmap())
            self.set_resource_text("")
            self._sync_proc_buttons(alive=False)
            return
        self._last_pid = info.pid
        alias = self._alias()
        base = alias or sanitize(info.title)
        if len(base) > 36:
            base = base[:33] + "..."
        self._title_lab.setText(f"[{info.pid}] {base}")
        self._title_lab.setStyleSheet("color:white;")
        if self._pm is not None:
            tip = "右键:起别名 / 选强调色(多开时方便分辨)"
            if alias:
                tip = f"别名:{alias}\n原名:{sanitize(info.title)}\n\n{tip}"
            self._title_lab.setToolTip(tip)
        runtime = time.time() - self._added_at
        h = int(runtime // 3600)
        m = int((runtime % 3600) // 60)
        s = int(runtime % 60)
        prefix = "已嵌入 · " if self._embedded else ""
        if self._embedded:
            # v1.8.4:顺手把当前查看方式亮出来
            mode_txt = {"fill": "铺满", "fit": "完整显示", "free": "自由查看"}.get(
                self._view_mode, "")
            if mode_txt:
                prefix = f"已嵌入({mode_txt}) · "
        msg = f"{prefix}已追踪 {h:02d}:{m:02d}:{s:02d}"
        if not self._embedded:
            msg += f" · {self._fps:.1f}fps"
        if self._status_extra:
            msg = f"● {self._status_extra} · " + msg
        self._set_status_text(msg)
        self._sync_proc_buttons(alive=True)

    def _is_proc_alive(self) -> bool:
        from ..core.game_launcher import is_process_alive

        if not self._last_pid:
            return False
        return is_process_alive(self._last_pid)

    def _sync_proc_buttons(self, *, alive: bool) -> None:
        """按进程存活状态点亮/灰掉「停止」与「恢复」."""
        self._proc_alive = bool(alive)
        self._btn_stop.setEnabled(alive)
        self._btn_resume.setEnabled(not alive)

    def _set_status_text(self, text: str) -> None:
        """状态栏文字(超长省略,完整内容放 tooltip)."""
        try:
            fm = self._status_lab.fontMetrics()
            avail = max(30, self.width() - 150)
            self._status_lab.setText(
                fm.elidedText(text, Qt.TextElideMode.ElideRight, avail)
            )
        except Exception:
            self._status_lab.setText(text)
        self._status_lab.setToolTip(text)

    def _on_image_click(self, ev) -> None:
        if self._embedded:
            return  # 嵌入时点击直接作用于游戏,不再抢前台
        if ev.button() == Qt.MouseButton.LeftButton:
            self._do_focus()

    def _on_image_dblclick(self, ev) -> None:
        if ev.button() != Qt.MouseButton.LeftButton:
            return
        self._do_focus(force_fit=True)

    def _on_focus_clicked(self) -> None:
        self._do_focus()

    def _do_focus(self, *, force_fit: bool = False) -> None:
        """聚焦:还原最小化 → 铺满所在显示器工作区 → 提到前台.

        目的:让**整个游戏界面完整可见**(不会被任务栏 / 屏幕边缘裁掉,
        也不会因为窗口比屏幕大而被切掉一部分)。
        """
        if self._embedded:
            self._release_embed()  # 嵌在卡片里时聚焦无意义,先弹回桌面
        fit = True
        if not force_fit:
            try:
                from ..utils.config import instance as _cfg_instance

                fit = bool(_cfg_instance().get("ui.focus_fit_screen", True))
            except Exception:  # noqa: BLE001
                fit = True
        # 读取每窗口独立的边距 / 缩放偏好(空 → 全 0 + 100%)
        margin: tuple[int, int, int, int] = (0, 0, 0, 0)
        scale: float = 1.0
        if self._pm is not None:
            try:
                item = self._pm.get(self.hwnd)
                if item is not None:
                    margin = item.margin_tuple()
                    scale = float(getattr(item, "content_scale", 1.0) or 1.0)
            except Exception:  # noqa: BLE001
                margin, scale = (0, 0, 0, 0), 1.0
        if wf.get_window_info(self.hwnd) is not None:
            wf.focus_window(self.hwnd, fit=fit, margin=margin, scale=scale)
        self.focused.emit(self.hwnd)

    def _on_edge_clicked(self) -> None:
        """打开「边距 / 缩放」对话框,保存到当前 TrackedProcess."""
        if self._pm is None:
            return
        item = self._pm.get(self.hwnd)
        if item is None:
            return
        from .edge_margin_dialog import EdgeMargin, EdgeMarginDialog
        initial = EdgeMargin(
            left=int(getattr(item, "margin_left", 0) or 0),
            top=int(getattr(item, "margin_top", 0) or 0),
            right=int(getattr(item, "margin_right", 0) or 0),
            bottom=int(getattr(item, "margin_bottom", 0) or 0),
            scale=float(getattr(item, "content_scale", 1.0) or 1.0),
        )
        dlg = EdgeMarginDialog(
            self,
            title=f"聚焦边距 / 缩放 · {item.display_name()}",
            initial=initial,
            info_text=(
                "边距(单位 px)只在「聚焦」这个窗口时生效;画面缩放需要游戏本身是\n"
                "「边框全屏 / 跟随窗口尺寸」渲染,否则缩放不会真的改变游戏画面。"
                "\n默认 0 + 100% 与未设置时完全一致。"
            ),
        )
        if dlg.exec() == EdgeMarginDialog.DialogCode.Accepted:
            value = dlg.result_value()
            try:
                self._pm.set_margins(
                    self.hwnd,
                    margin=(value.left, value.top, value.right, value.bottom),
                    scale=value.scale,
                )
            except Exception:  # noqa: BLE001
                pass

    def _on_autoclick_toggle(self, checked: bool) -> None:
        self.autoclick_toggle.emit(self.hwnd)
