"""日志查看器.

出问题时不用再去翻 ``%APPDATA%\\AutoFarmStation\\logs`` ——
直接在界面里看、过滤、复制、导出。

只读文件尾部(默认最后 512KB),所以日志涨到几十兆也不会卡。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QFont
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout,
    QLabel, QMessageBox, QPlainTextEdit, QPushButton, QSpinBox, QVBoxLayout,
)

from ..utils.logger import log_file_path
from ..utils.sanitize import user_log_dir_display

# 只读文件尾部,避免日志很大时把内存吃满
TAIL_BYTES = 512 * 1024
LEVELS = ("全部", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


class LogViewerDialog(QDialog):
    """看日志的窗口(可过滤 / 自动刷新 / 导出)."""

    def __init__(self, parent=None, *, log_path: Path | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("运行日志")
        self.resize(900, 560)
        self._path = Path(log_path) if log_path else log_file_path()

        v = QVBoxLayout(self)

        # --- 顶部工具条 ---
        bar = QHBoxLayout()
        bar.addWidget(QLabel("级别:"))
        self._level = QComboBox()
        self._level.addItems(LEVELS)
        self._level.setCurrentText("全部")
        self._level.currentTextChanged.connect(lambda _t: self.reload())
        bar.addWidget(self._level)

        bar.addWidget(QLabel("显示:"))
        self._lines = QSpinBox()
        self._lines.setRange(50, 20000)
        self._lines.setSingleStep(100)
        self._lines.setValue(500)
        self._lines.valueChanged.connect(lambda _v: self.reload())
        bar.addWidget(self._lines)
        bar.addWidget(QLabel("行"))

        self._auto = QCheckBox("自动刷新")
        self._auto.setChecked(True)
        self._auto.setToolTip("每 3 秒重新读一次日志文件")
        self._auto.toggled.connect(self._on_auto_toggled)
        bar.addWidget(self._auto)

        btn_reload = QPushButton("刷新")
        btn_reload.clicked.connect(self.reload)
        bar.addWidget(btn_reload)

        btn_copy = QPushButton("复制全部")
        btn_copy.setToolTip("把当前显示的内容复制到剪贴板(贴给朋友排查用)")
        btn_copy.clicked.connect(self._on_copy)
        bar.addWidget(btn_copy)

        btn_export = QPushButton("导出...")
        btn_export.clicked.connect(self._on_export)
        bar.addWidget(btn_export)

        btn_dir = QPushButton("打开目录")
        btn_dir.clicked.connect(self._on_open_dir)
        bar.addWidget(btn_dir)
        bar.addStretch()
        v.addLayout(bar)

        # --- 正文 ---
        self._text = QPlainTextEdit()
        self._text.setReadOnly(True)
        f = QFont("Consolas")
        f.setStyleHint(QFont.StyleHint.Monospace)
        f.setPointSize(9)
        self._text.setFont(f)
        self._text.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        v.addWidget(self._text, stretch=1)

        self._info = QLabel("")
        self._info.setStyleSheet("color:#888; font-size:11px;")
        v.addWidget(self._info)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btns.rejected.connect(self.reject)
        btns.accepted.connect(self.accept)
        v.addWidget(btns)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self.reload)
        if self._auto.isChecked():
            self._timer.start(3000)
        self.reload()

    # --- 内部 ---
    def _on_auto_toggled(self, on: bool) -> None:
        if on:
            self._timer.start(3000)
        else:
            self._timer.stop()

    def _tail_text(self) -> str:
        if not self._path.is_file():
            return ""
        try:
            size = self._path.stat().st_size
            with self._path.open("rb") as fh:
                if size > TAIL_BYTES:
                    fh.seek(size - TAIL_BYTES)
                    fh.readline()  # 丢掉可能被截断的半行
                data = fh.read()
            return data.decode("utf-8", errors="replace")
        except OSError as e:
            return f"(读取日志失败:{e})"

    def reload(self) -> None:
        raw = self._tail_text()
        want = self._level.currentText()
        lines = raw.splitlines()
        if want and want != "全部":
            lines = [ln for ln in lines if want in ln]
        total = len(lines)
        keep = int(self._lines.value())
        if total > keep:
            lines = lines[-keep:]
        text = "\n".join(lines)
        # 尽量保持「贴着底部看最新」的阅读习惯
        sb = self._text.verticalScrollBar()
        at_bottom = sb.value() >= sb.maximum() - 4
        self._text.setPlainText(text)
        if at_bottom:
            sb.setValue(sb.maximum())

        if not self._path.is_file():
            self._info.setText(f"日志文件还不存在:{self._path}")
        else:
            shown = min(total, keep)
            extra = f"(只显示最后 {keep} 行)" if total > keep else ""
            self._info.setText(
                f"{self._path}  ·  匹配 {total} 行,显示 {shown} 行 {extra} "
                f"·  目录:{user_log_dir_display()}"
            )

    def _on_copy(self) -> None:
        self._text.selectAll()
        self._text.copy()
        sb = self._text.verticalScrollBar()
        sb.setValue(sb.maximum())
        QMessageBox.information(self, "复制", "已复制当前显示的内容到剪贴板。")

    def _on_export(self) -> None:
        dest, _sel = QFileDialog.getSaveFileName(
            self, "导出日志", str(Path.home() / "autofarmstation.log.txt"),
            "文本文件 (*.txt);;所有文件 (*)",
        )
        if not dest:
            return
        try:
            Path(dest).write_text(self._text.toPlainText(), encoding="utf-8")
        except OSError as e:
            QMessageBox.warning(self, "导出日志", f"导出失败:{e}")
            return
        QMessageBox.information(self, "导出日志", f"已导出到\n{dest}")

    def _on_open_dir(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._path.parent)))
