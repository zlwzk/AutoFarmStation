"""单个窗口预览卡片.

支持:
- 勾选(用于「多窗口同步执行」)
- 实时画面预览(GDI PrintWindow,被遮挡也能截到)
- **窗口嵌入**:点击「嵌入」把原窗口收进卡片内显示,桌面上不再单独出现;
  再点「弹出」完整还原。卡片关闭/程序退出时自动还原。
"""

from __future__ import annotations

import time

from PySide6.QtCore import Qt, Signal, QTimer, QEvent
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QWidget, QLabel, QVBoxLayout, QHBoxLayout, QPushButton,
    QFrame, QSizePolicy, QCheckBox,
)

from ..core.preview_capture import PreviewCapture, PreviewFrame
from ..core import window_finder as wf
from ..core import window_host
from .widgets import styled_message


class PreviewWidget(QFrame):
    """单个窗口预览卡片(可勾选 / 可嵌入)."""

    focused = Signal(int)  # hwnd
    closed = Signal(int)  # hwnd(请求从追踪列表移除)
    autoclick_toggle = Signal(int)  # hwnd
    selection_changed = Signal(int, bool)  # hwnd, 是否选中
    stop_process = Signal(int)  # hwnd → 结束该游戏进程
    resume_process = Signal(int)  # hwnd → 重新唤醒该游戏
    volume_requested = Signal(int)  # hwnd → 打开单窗口音量面板

    def __init__(
        self,
        hwnd: int,
        *,
        fps: float = 1.0,
        phase: float = 0.0,
        card_size: tuple[int, int] | None = None,
        show_volume: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.hwnd = int(hwnd)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setObjectName("PreviewCard")
        self.setStyleSheet(
            "#PreviewCard { border: 2px solid #3c3c3c; background:#222; }"
            "#PreviewCard:hover { border: 2px solid #4a9eff; }"
        )
        cw, ch = card_size or (240, 190)
        self.setMinimumSize(int(cw), int(ch))
        self._capture = PreviewCapture(self.hwnd, fps=fps, phase=phase)
        self._capture.on_frame = self._on_frame
        self._autoclicker_running = False
        self._fps = fps
        self._status_extra = ""
        self._embedded = False
        self._show_volume = bool(show_volume)
        self._proc_alive = True
        self._last_pid = 0
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
        title_bar.addWidget(self._title_lab)

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

    def pid(self) -> int:
        """当前 pid(窗口失效时回退到最近一次记录的值)."""
        info = wf.get_window_info(self.hwnd)
        if info is not None and info.pid:
            self._last_pid = info.pid
        return self._last_pid

    def refresh_now(self) -> None:
        """外部(如设置变更后)立刻刷新一次状态."""
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
        self._refresh_status()
        return True

    def _release_embed(self, quiet: bool = False) -> None:
        """把窗口还原回桌面."""
        self._image_lab.removeEventFilter(self)
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
                dpr = self.devicePixelRatioF()
                window_host.fit_to(
                    self.hwnd,
                    int(ev.size().width() * dpr),
                    int(ev.size().height() * dpr),
                )
            except Exception:
                pass
        return super().eventFilter(obj, ev)

    # --- 内部 ---
    def _on_check_toggled(self, on: bool) -> None:
        # 选中时高亮边框
        if on:
            self.setStyleSheet(
                "#PreviewCard { border: 2px solid #4a9eff; background:#222; }"
            )
        else:
            self.setStyleSheet(
                "#PreviewCard { border: 2px solid #3c3c3c; background:#222; }"
                "#PreviewCard:hover { border: 2px solid #4a9eff; }"
            )
        self.selection_changed.emit(self.hwnd, bool(on))

    def _on_frame(self, frame: PreviewFrame) -> None:
        """截图回调(子线程).用 QTimer.singleShot 切到主线程."""
        QTimer.singleShot(0, lambda: self._render_frame(frame))

    def _render_frame(self, frame: PreviewFrame) -> None:
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
        except Exception:
            pass

    def _refresh_status(self) -> None:
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
            self._sync_proc_buttons(alive=False)
            return
        self._last_pid = info.pid
        title = info.title
        if len(title) > 36:
            title = title[:33] + "..."
        self._title_lab.setText(f"[{info.pid}] {title}")
        self._title_lab.setStyleSheet("color:white;")
        runtime = time.time() - self._added_at
        h = int(runtime // 3600)
        m = int((runtime % 3600) // 60)
        s = int(runtime % 60)
        prefix = "已嵌入 · " if self._embedded else ""
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
        if wf.get_window_info(self.hwnd) is not None:
            wf.focus_window(self.hwnd, fit=fit)
        self.focused.emit(self.hwnd)

    def _on_autoclick_toggle(self, checked: bool) -> None:
        self.autoclick_toggle.emit(self.hwnd)
