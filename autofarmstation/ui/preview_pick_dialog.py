"""截图选点对话框.

直接对目标窗口截一张图(PrintWindow,不要求游戏在前台、被遮挡也能截),
用户在截图上点击即可标记连点位置 —— 不用切到游戏窗口、不用全屏覆盖层。

为什么不用原来的「从屏幕选点」:那个方案要把游戏窗口置顶并弹全屏覆盖层,
多开 / 嵌入 / 直播场景下很打断操作;截图选点完全在软件内完成。

坐标换算:capture_gdi 截的是**客户区**,返回尺寸 == 客户区尺寸,
所以「截图像素」直接就是 ClickPoint 需要的客户区像素,零换算误差。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QPoint
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QComboBox, QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
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


class _PickCanvas(QWidget):
    """显示截图 + 已标记点位,点击即添加."""

    def __init__(self, on_pick, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._on_pick = on_pick
        self._pix: QPixmap | None = None
        self._img_w = 0
        self._img_h = 0
        self.points: list[ClickPoint] = []
        self.setMinimumHeight(300)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)  # 不按键也跟踪,实时显示光标对应的客户区坐标
        self.on_hover = None  # cb((x, y) | None)
        self.setWindowTitle("")

    # --- 数据 ---
    def set_image(self, width: int, height: int, pixels: bytes) -> None:
        self._img_w, self._img_h = int(width), int(height)
        img = QImage(pixels, self._img_w, self._img_h, self._img_w * 4,
                     QImage.Format.Format_BGRX8888)
        self._pix = QPixmap.fromImage(img)
        self.update()

    def set_points(self, pts: list[ClickPoint]) -> None:
        self.points = list(pts)
        self.update()

    def image_size(self) -> tuple[int, int]:
        return (self._img_w, self._img_h)

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
        if self._pix is None:
            return
        got = map_to_image(ev.position().toPoint(), self.width(), self.height(),
                           self._img_w, self._img_h)
        if got is None:
            return
        btn = (MouseButton.LEFT if ev.button() == Qt.MouseButton.LeftButton
               else MouseButton.RIGHT)
        self._on_pick(got[0], got[1], btn)

    # --- 绘制 ---
    def paintEvent(self, ev) -> None:  # noqa: N802
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#101418"))
        if self._pix is None:
            p.setPen(QColor("#888888"))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                       "(无截图)\n窗口最小化时无法截图,请先还原窗口再点「重新截图」")
            return
        s = min(self.width() / self._img_w, self.height() / self._img_h)
        dw, dh = self._img_w * s, self._img_h * s
        ox, oy = (self.width() - dw) / 2.0, (self.height() - dh) / 2.0
        target = self._pix.scaled(
            int(dw) or 1, int(dh) or 1,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation)
        p.drawPixmap(int(ox), int(oy), target)

        # 已标记点位:蓝色圆点 + 序号
        font = QFont()
        font.setPointSize(9)
        font.setBold(True)
        p.setFont(font)
        for i, cp in enumerate(self.points, start=1):
            cx = ox + cp.x * s
            cy = oy + cp.y * s
            p.setBrush(QColor(74, 158, 255, 210))
            p.setPen(QPen(QColor("#ffffff"), 1))
            p.drawEllipse(QPoint(int(cx), int(cy)), 9, 9)
            p.setPen(QColor("#ffffff"))
            p.drawText(int(cx) - 3, int(cy) + 4, str(i))
        p.end()


class PreviewPickDialog(QDialog):
    """在游戏截图上标记连点位置."""

    def __init__(self, target_hwnd: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("截图选点 · 点击画面标记连点位置")
        self.resize(880, 620)
        self._hwnd = int(target_hwnd)
        self.points: list[ClickPoint] = []

        v = QVBoxLayout(self)
        self._hint = QLabel(
            "左键点画面 = 加左键点位,右键点画面 = 加右键点位;可连续标记多个。\n"
            "截图是刚拍的,游戏画面变了就点「重新截图」。确定后点位会加入右侧连点器的点位列表。")
        self._hint.setStyleSheet("color:#a0cfff;")
        self._hint.setWordWrap(True)
        v.addWidget(self._hint)

        mid = QHBoxLayout()
        self._canvas = _PickCanvas(self._on_canvas_pick)
        mid.addWidget(self._canvas, stretch=1)

        side = QVBoxLayout()
        self._list = QListWidget()
        self._list.setMaximumWidth(230)
        side.addWidget(self._list, stretch=1)
        btn_del = QPushButton("删除选中")
        btn_del.clicked.connect(self._on_del)
        side.addWidget(btn_del)
        btn_clear = QPushButton("清空全部")
        btn_clear.clicked.connect(self._on_clear)
        side.addWidget(btn_clear)
        mid.addLayout(side)
        v.addLayout(mid, stretch=1)

        bottom = QHBoxLayout()
        self._coord = QLabel("光标: —")
        self._coord.setStyleSheet("color:#8fd18f;")
        bottom.addWidget(self._coord)
        self._canvas.on_hover = self._on_hover
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

        self._recapture()

    # --- 内部 ---
    def _on_hover(self, got: tuple[int, int] | None) -> None:
        if got is None:
            self._coord.setText("光标: 画面外")
        else:
            self._coord.setText(f"光标: 客户区 ({got[0]}, {got[1]}) px")

    def _recapture(self) -> None:
        res = capture_gdi(self._hwnd)
        if res is None:
            self._canvas.set_image(0, 0, b"")
            self._canvas._pix = None  # noqa: SLF001
            self._canvas.update()
            self._hint.setText(
                "截图失败:窗口最小化(或已失效)时 GDI 不渲染,请先把窗口还原再点「重新截图」;\n"
                "或者改用「从屏幕选点」(需要窗口在前台)。")
            self._hint.setStyleSheet("color:#e0a060;")
            return
        w, h, pixels = res
        self._canvas.set_image(w, h, pixels)
        self._hint.setText(
            f"左键点画面 = 加左键点位,右键 = 加右键点位;截图尺寸 {w}×{h}(即客户区),点位已自动记比例坐标。")
        self._hint.setStyleSheet("color:#a0cfff;")

    def _on_canvas_pick(self, x: int, y: int, btn: MouseButton) -> None:
        iw, ih = self._canvas.image_size()
        cp = ClickPoint(x=int(x), y=int(y), button=btn)
        cp.set_ratio(iw, ih)
        self.points.append(cp)
        self._sync_list()

    def _sync_list(self) -> None:
        self._list.clear()
        for i, cp in enumerate(self.points, start=1):
            btn = "左键" if cp.button == MouseButton.LEFT else "右键"
            item = QListWidgetItem(f"{i}. ({cp.x}, {cp.y}) · {btn}")
            item.setData(Qt.ItemDataRole.UserRole, i - 1)
            self._list.addItem(item)
        self._canvas.set_points(self.points)

    def _on_del(self) -> None:
        rows = sorted((self._list.row(it) for it in self._list.selectedItems()),
                      reverse=True)
        for r in rows:
            if 0 <= r < len(self.points):
                self.points.pop(r)
        self._sync_list()

    def _on_clear(self) -> None:
        self.points.clear()
        self._sync_list()
