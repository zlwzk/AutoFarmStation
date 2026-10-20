"""截图选点对话框.

直接对目标窗口截一张图(PrintWindow,不要求游戏在前台、被遮挡也能截),
用户在截图上点击即可标记连点位置 —— 不用切到游戏窗口、不用全屏覆盖层。

为什么不用原来的「从屏幕选点」:那个方案要把游戏窗口置顶并弹全屏覆盖层,
多开 / 嵌入 / 直播场景下很打断操作;截图选点完全在软件内完成。

坐标换算:capture_gdi 截的是**客户区**,返回尺寸 == 客户区尺寸,
所以「截图像素」直接就是 ClickPoint 需要的客户区像素,零换算误差。

v1.8.7 完善:
    * 打开时带入**该窗口已有点位**并在画面上显示,确定后整体替换 ——
      以前是「盲加」,重复点一次就多一份重复点位;
    * 点已有标记 = 选中它(不再叠一个点),方向键可 **1px 微调**(Shift = 10px),
      Delete 删除选中,Ctrl+Z 撤销 —— 截图缩小显示时也能精确对准;
    * 重新截图后若窗口尺寸变了,已有点位按比例坐标自动换算到新尺寸;
    * 截图失败不再靠直接改私有属性清空,走公开的 ``clear_image()``。
"""

from __future__ import annotations

import math
import re

from PySide6.QtCore import Qt, QPoint
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QPushButton, QVBoxLayout, QWidget,
)

from ..core import MouseButton
from ..core.autoclicker import ClickPoint
from ..core.preview_capture import capture_gdi


def map_to_image(
    pos: QPoint,
    canvas_w: int,
    canvas_h: int,
    img_w: int,
    img_h: int,
) -> tuple[int, int] | None:
    """把画布上的点击位置换算成截图(=客户区)像素坐标.

    画面按 KeepAspectRatio 居中显示,四周可能有黑边;点在黑边里返回 None。
    点在画面边缘外一点点时吸附到边界(容差 8px,方便点到最边上)。
    """
    if img_w <= 0 or img_h <= 0 or canvas_w <= 0 or canvas_h <= 0:
        return None
    s = min(canvas_w / img_w, canvas_h / img_h)
    dw, dh = img_w * s, img_h * s
    ox, oy = (canvas_w - dw) / 2.0, (canvas_h - dh) / 2.0
    x = (pos.x() - ox) / s
    y = (pos.y() - oy) / s
    if x < -8 or y < -8 or x > img_w + 8 or y > img_h + 8:
        return None
    return (int(round(max(0.0, min(float(img_w - 1), x)))),
            int(round(max(0.0, min(float(img_h - 1), y)))))


def nearest_point(
    pos: QPoint,
    canvas_w: int,
    canvas_h: int,
    img_w: int,
    img_h: int,
    points: list[ClickPoint],
    tolerance_px: int = 10,
) -> int:
    """返回离鼠标最近的已有点位下标(超过容差返回 -1).

    用于「点已有标记 = 选中」,避免同一个位置被反复叠加。
    """
    if img_w <= 0 or img_h <= 0 or canvas_w <= 0 or canvas_h <= 0:
        return -1
    s = min(canvas_w / img_w, canvas_h / img_h)
    dw, dh = img_w * s, img_h * s
    ox, oy = (canvas_w - dw) / 2.0, (canvas_h - dh) / 2.0
    best = -1
    best_d = float(tolerance_px)
    for i, cp in enumerate(points):
        cx = ox + cp.x * s
        cy = oy + cp.y * s
        d = math.hypot(pos.x() - cx, pos.y() - cy)
        if d <= best_d:
            best_d = d
            best = i
    return best


def rebase_points(points: list[ClickPoint], new_w: int, new_h: int) -> None:
    """截图尺寸变化后,用比例坐标把点位换算到新尺寸(保持相对位置)."""
    if new_w <= 0 or new_h <= 0:
        return
    for cp in points:
        rx = float(getattr(cp, "rx", 0.0) or 0.0)
        ry = float(getattr(cp, "ry", 0.0) or 0.0)
        if 0.0 <= rx <= 1.0 and 0.0 <= ry <= 1.0 and (rx or ry):
            cp.x = int(max(0, min(new_w - 1, round(rx * new_w))))
            cp.y = int(max(0, min(new_h - 1, round(ry * new_h))))
        cp.set_ratio(new_w, new_h)


class _PickCanvas(QWidget):
    """显示截图 + 已标记点位:点击加点 / 点标记选中 / 方向键微调."""

    def __init__(self, on_pick, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._on_pick = on_pick
        self._pix: QPixmap | None = None
        self._img_w = 0
        self._img_h = 0
        self.points: list[ClickPoint] = []
        self.selected = -1
        self.setMinimumHeight(300)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)  # 不按键也跟踪,实时显示光标对应的客户区坐标
        self.on_hover = None       # cb((x, y) | None)
        self.on_select = None      # cb(index)
        self.on_nudge = None       # cb(dx, dy)
        self.on_delete = None      # cb(index)
        self.on_undo = None        # cb()

    # --- 数据 ---
    def set_image(self, width: int, height: int, pixels: bytes) -> None:
        self._img_w, self._img_h = int(width), int(height)
        if self._img_w <= 0 or self._img_h <= 0:
            self.clear_image()
            return
        img = QImage(pixels, self._img_w, self._img_h, self._img_w * 4,
                     QImage.Format.Format_BGRX8888)
        self._pix = QPixmap.fromImage(img)
        self.update()

    def clear_image(self) -> None:
        """清空画面(截图失败时用),已有点位保留."""
        self._pix = None
        self._img_w = 0
        self._img_h = 0
        self.update()

    def has_image(self) -> bool:
        return self._pix is not None and self._img_w > 0 and self._img_h > 0

    def set_points(self, pts: list[ClickPoint]) -> None:
        self.points = list(pts)
        if self.selected >= len(self.points):
            self.selected = len(self.points) - 1
        self.update()

    def select(self, index: int) -> None:
        self.selected = int(index)
        self.update()

    def image_size(self) -> tuple[int, int]:
        return (self._img_w, self._img_h)

    def _to_canvas(self, x: int, y: int) -> tuple[float, float]:
        s = min(self.width() / self._img_w, self.height() / self._img_h)
        dw, dh = self._img_w * s, self._img_h * s
        ox, oy = (self.width() - dw) / 2.0, (self.height() - dh) / 2.0
        return (ox + x * s, oy + y * s)

    # --- 交互 ---
    def mouseMoveEvent(self, ev) -> None:  # noqa: N802
        """光标悬停实时换算客户区坐标,让用户核对标记位置."""
        if self.on_hover is None or self._pix is None:
            return
        got = map_to_image(ev.position().toPoint(), self.width(), self.height(),
                           self._img_w, self._img_h)
        try:
            self.on_hover(got)
        except Exception:
            pass

    def mousePressEvent(self, ev) -> None:  # noqa: N802
        if ev.button() not in (Qt.MouseButton.LeftButton, Qt.MouseButton.RightButton):
            return
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        if self._pix is None:
            return
        pos = ev.position().toPoint()
        got = map_to_image(pos, self.width(), self.height(),
                           self._img_w, self._img_h)
        if got is None:
            return
        # 点在已有标记附近 = 选中它,不重复叠加
        idx = nearest_point(pos, self.width(), self.height(),
                            self._img_w, self._img_h, self.points)
        if idx >= 0:
            self.selected = idx
            self.update()
            if self.on_select is not None:
                try:
                    self.on_select(idx)
                except Exception:
                    pass
            return
        btn = (MouseButton.LEFT if ev.button() == Qt.MouseButton.LeftButton
               else MouseButton.RIGHT)
        self._on_pick(got[0], got[1], btn)

    def keyPressEvent(self, ev) -> None:  # noqa: N802
        k = ev.key()
        if k in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace) and self.selected >= 0:
            if self.on_delete is not None:
                self.on_delete(self.selected)
            return
        if k == Qt.Key.Key_Z and ev.modifiers() & Qt.KeyboardModifier.ControlModifier:
            if self.on_undo is not None:
                self.on_undo()
            return
        if k in (Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Up, Qt.Key.Key_Down) \
                and self.selected >= 0:
            step = 10 if ev.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1
            dx = -step if k == Qt.Key.Key_Left else (step if k == Qt.Key.Key_Right else 0)
            dy = -step if k == Qt.Key.Key_Up else (step if k == Qt.Key.Key_Down else 0)
            if self.on_nudge is not None and (dx or dy):
                self.on_nudge(dx, dy)
            return
        super().keyPressEvent(ev)

    # --- 绘制 ---
    def paintEvent(self, ev) -> None:  # noqa: N802
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#101418"))
        if self._pix is None:
            p.setPen(QColor("#888888"))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                       "(无截图)\n窗口最小化时无法截图,请先还原窗口再点「重新截图」")
            p.end()
            return
        s = min(self.width() / self._img_w, self.height() / self._img_h)
        dw, dh = self._img_w * s, self._img_h * s
        ox, oy = (self.width() - dw) / 2.0, (self.height() - dh) / 2.0
        target = self._pix.scaled(
            int(dw) or 1, int(dh) or 1,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation)
        p.drawPixmap(int(ox), int(oy), target)

        # 已标记点位:蓝色圆点 + 序号;选中的用橙色加粗外圈
        font = QFont()
        font.setPointSize(9)
        font.setBold(True)
        p.setFont(font)
        for i, cp in enumerate(self.points):
            cx = ox + cp.x * s
            cy = oy + cp.y * s
            if i == self.selected:
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(QPen(QColor("#ffb347"), 2))
                p.drawEllipse(QPoint(int(cx), int(cy)), 13, 13)
            color = QColor(240, 144, 64, 220) if cp.button == MouseButton.RIGHT \
                else QColor(74, 158, 255, 210)
            p.setBrush(color)
            p.setPen(QPen(QColor("#ffffff"), 1))
            p.drawEllipse(QPoint(int(cx), int(cy)), 9, 9)
            p.setPen(QColor("#ffffff"))
            p.drawText(int(cx) - 3, int(cy) + 4, str(i + 1))
        p.end()


class PreviewPickDialog(QDialog):
    """在游戏截图上标记 / 编辑连点位置."""

    def __init__(
        self,
        target_hwnd: int,
        parent: QWidget | None = None,
        initial_points: list[ClickPoint] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("截图选点 · 点击画面标记连点位置")
        self.resize(880, 620)
        self._hwnd = int(target_hwnd)
        # 带入已有点位:确定后整体替换(而不是在旧点位后面再叠一份)
        self.points: list[ClickPoint] = [self._clone_point(p)
                                         for p in (initial_points or [])]

        v = QVBoxLayout(self)
        self._hint = QLabel("")
        self._hint.setWordWrap(True)
        v.addWidget(self._hint)

        mid = QHBoxLayout()
        self._canvas = _PickCanvas(self._on_canvas_pick)
        self._canvas.on_hover = self._on_hover
        self._canvas.on_select = self._on_canvas_select
        self._canvas.on_nudge = self._on_nudge
        self._canvas.on_delete = self._on_delete_index
        self._canvas.on_undo = self._on_undo
        mid.addWidget(self._canvas, stretch=1)

        side = QVBoxLayout()
        self._list = QListWidget()
        self._list.setMaximumWidth(240)
        self._list.currentRowChanged.connect(self._on_list_row)
        side.addWidget(self._list, stretch=1)
        btn_del = QPushButton("删除选中")
        btn_del.clicked.connect(self._on_del)
        side.addWidget(btn_del)
        btn_undo = QPushButton("撤销上一步")
        btn_undo.clicked.connect(self._on_undo)
        side.addWidget(btn_undo)
        btn_clear = QPushButton("清空全部")
        btn_clear.clicked.connect(self._on_clear)
        side.addWidget(btn_clear)
        mid.addLayout(side)
        v.addLayout(mid, stretch=1)

        bottom = QHBoxLayout()
        self._coord = QLabel("光标: —")
        self._coord.setStyleSheet("color:#8fd18f;")
        bottom.addWidget(self._coord)
        btn_recap = QPushButton("重新截图")
        btn_recap.clicked.connect(self._recapture)
        bottom.addWidget(btn_recap)
        bottom.addStretch()
        btn_ok = QPushButton("确定")
        btn_ok.setProperty("primary", True)
        btn_ok.clicked.connect(self.accept)
        bottom.addWidget(btn_ok)
        btn_cancel = QPushButton("取消")
        btn_cancel.clicked.connect(self.reject)
        bottom.addWidget(btn_cancel)
        v.addLayout(bottom)

        self._sync_list()
        self._recapture()
        self._canvas.setFocus()

    @staticmethod
    def _clone_point(cp: ClickPoint) -> ClickPoint:
        c = ClickPoint(x=int(cp.x), y=int(cp.y), button=cp.button,
                       double=bool(getattr(cp, "double", False)))
        rx = float(getattr(cp, "rx", 0.0) or 0.0)
        ry = float(getattr(cp, "ry", 0.0) or 0.0)
        if 0.0 < rx <= 1.0 and 0.0 < ry <= 1.0:
            c.rx, c.ry = rx, ry
        return c

    # --- 内部 ---
    def _on_hover(self, got: tuple[int, int] | None) -> None:
        if got is None:
            self._coord.setText("光标: 画面外")
        else:
            self._coord.setText(f"光标: 客户区 ({got[0]}, {got[1]}) px")

    def _recapture(self) -> None:
        old_w, old_h = self._canvas.image_size()
        res = capture_gdi(self._hwnd)
        if res is None:
            self._canvas.clear_image()
            self._hint.setText(
                "截图失败:窗口最小化(或已失效)时 GDI 不渲染,请先把窗口还原再点「重新截图」;\n"
                "或者改用「从屏幕选点」(需要窗口在前台)。")
            self._hint.setStyleSheet("color:#e0a060;")
            return
        w, h, pixels = res
        if self.points and (w, h) != (old_w, old_h) and old_w > 0 and old_h > 0:
            rebase_points(self.points, w, h)
            self._sync_list()
        self._canvas.set_image(w, h, pixels)
        self._hint.setText(
            f"左键点画面 = 加左键点位,右键 = 加右键点位;截图 {w}×{h} 即客户区,点位自动记比例坐标。\n"
            f"点已有标记 = 选中 → 方向键微调 1px(按住 Shift = 10px),Delete 删除,Ctrl+Z 撤销。"
            f"当前 {len(self.points)} 个点位。")
        self._hint.setStyleSheet("color:#a0cfff;")

    def _on_canvas_pick(self, x: int, y: int, btn: MouseButton) -> None:
        iw, ih = self._canvas.image_size()
        cp = ClickPoint(x=int(x), y=int(y), button=btn)
        cp.set_ratio(iw, ih)
        self.points.append(cp)
        self._sync_list(select=len(self.points) - 1)
        self._update_hint_count()

    def _sync_list(self, select: int | None = None) -> None:
        self._list.blockSignals(True)
        self._list.clear()
        for i, cp in enumerate(self.points, start=1):
            btn = "左键" if cp.button == MouseButton.LEFT else "右键"
            item = QListWidgetItem(f"{i}. ({cp.x}, {cp.y}) · {btn}")
            item.setData(Qt.ItemDataRole.UserRole, i - 1)
            self._list.addItem(item)
        if select is not None and 0 <= select < self._list.count():
            self._list.setCurrentRow(select)
            self._canvas.select(select)
        self._list.blockSignals(False)
        self._canvas.set_points(self.points)

    def _update_hint_count(self) -> None:
        t = self._hint.text()
        if "当前" in t:
            t = re.sub(r"当前 \d+ 个点位", f"当前 {len(self.points)} 个点位", t)
            self._hint.setText(t)

    def _on_canvas_select(self, index: int) -> None:
        if 0 <= index < self._list.count():
            self._list.blockSignals(True)
            self._list.setCurrentRow(index)
            self._list.blockSignals(False)

    def _on_list_row(self, row: int) -> None:
        self._canvas.select(row)

    def _on_nudge(self, dx: int, dy: int) -> None:
        i = self._canvas.selected
        if not (0 <= i < len(self.points)):
            return
        iw, ih = self._canvas.image_size()
        cp = self.points[i]
        cp.x = int(max(0, min(max(0, iw - 1), cp.x + dx)))
        cp.y = int(max(0, min(max(0, ih - 1), cp.y + dy)))
        cp.set_ratio(iw, ih)
        self._sync_list(select=i)

    def _on_delete_index(self, index: int) -> None:
        if 0 <= index < len(self.points):
            self.points.pop(index)
            self._sync_list(select=min(index, len(self.points) - 1))
            self._update_hint_count()

    def _on_undo(self) -> None:
        if self.points:
            self.points.pop()
            self._sync_list(select=len(self.points) - 1)
            self._update_hint_count()

    def _on_del(self) -> None:
        rows = sorted((self._list.row(it) for it in self._list.selectedItems()),
                      reverse=True)
        for r in rows:
            if 0 <= r < len(self.points):
                self.points.pop(r)
        self._sync_list(select=min(len(self.points) - 1,
                                   rows[-1] if rows else -1))
        self._update_hint_count()

    def _on_clear(self) -> None:
        self.points.clear()
        self._sync_list()
        self._update_hint_count()
