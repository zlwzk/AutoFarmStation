"""定时任务管理器.

一个任务 = 动作 + 时间规则:

* **一次性**  指定日期时间,跑完自动停用
* **间隔**    每隔 N 秒 / 分钟 / 小时
* **每天**    HH:MM
* **每周**    周几(可多选,例如一/三/五)+ HH:MM

另外每个任务都可以设「延时执行」:到点后再等 N 秒才动手 ——
配两条任务就能串流程,比如「22:00 启动全部」+「22:00 延时 30 秒开始连点」。

动作可以直接指向「全部窗口」或某一个被追踪的窗口。
音量类动作只作用于已加入本软件的窗口,不会动系统总音量。
任务持久化在 ``%APPDATA%\\AutoFarmStation\\schedules.json``。
"""

from __future__ import annotations

import datetime as _dt

from PySide6.QtCore import QDateTime, Qt, QTime, QTimer
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDateTimeEdit, QDialog, QDialogButtonBox, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QSpinBox,
    QStackedWidget, QTableWidget, QTableWidgetItem, QTimeEdit, QVBoxLayout,
    QWidget, QHeaderView,
)

from ..core.scheduler import (
    ACTION_LABELS, ScheduledTask, Scheduler, TaskFreq, action_label,
)
from ..utils.sanitize import sanitize

# 动作下拉:(key, 中文)
_ACTION_ITEMS: list[tuple[str, str]] = [
    ("start_all", ACTION_LABELS["start_all"]),
    ("stop_all", ACTION_LABELS["stop_all"]),
    ("launch_games", ACTION_LABELS["launch_games"]),
    ("kill_games", ACTION_LABELS["kill_games"]),
    ("start_clicker", ACTION_LABELS["start_clicker"]),
    ("stop_clicker", ACTION_LABELS["stop_clicker"]),
    ("play_macro", ACTION_LABELS["play_macro"]),
    ("volume_low", ACTION_LABELS["volume_low"]),
    ("volume_mute", ACTION_LABELS["volume_mute"]),
    ("volume_restore", ACTION_LABELS["volume_restore"]),
    ("shutdown_app", ACTION_LABELS["shutdown_app"]),
]

_UNITS = [("秒", 1), ("分钟", 60), ("小时", 3600)]


class TaskEditDialog(QDialog):
    """新增 / 编辑单个定时任务."""

    def __init__(self, task: ScheduledTask | None = None, targets=None, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("编辑定时任务" if task else "新增定时任务")
        self.setMinimumWidth(420)
        self._targets: list[tuple[int, str]] = list(targets or [])

        form = QFormLayout(self)

        self._name = QLineEdit(task.name if task else "")
        self._name.setPlaceholderText("例如:每天 8 点启动全部")
        form.addRow("名称:", self._name)

        self._freq = QComboBox()
        for label, val in (
            ("一次性(指定日期时间)", TaskFreq.ONCE),
            ("间隔重复", TaskFreq.INTERVAL),
            ("每天", TaskFreq.DAILY),
            ("每周", TaskFreq.WEEKLY),
        ):
            self._freq.addItem(label, val.value)
        form.addRow("频率:", self._freq)

        # 参数区(按频率切换)
        self._stack = QStackedWidget()
        # --- once ---
        w_once = QWidget()
        f_once = QFormLayout(w_once)
        f_once.setContentsMargins(0, 0, 0, 0)
        self._once_at = QDateTimeEdit()
        self._once_at.setCalendarPopup(True)
        self._once_at.setDisplayFormat("yyyy-MM-dd HH:mm")
        self._once_at.setDateTime(QDateTime.currentDateTime().addSecs(300))
        f_once.addRow("触发时间:", self._once_at)
        self._stack.addWidget(w_once)
        # --- interval ---
        w_int = QWidget()
        f_int = QFormLayout(w_int)
        f_int.setContentsMargins(0, 0, 0, 0)
        row = QWidget()
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 0, 0, 0)
        self._int_num = QSpinBox()
        self._int_num.setRange(1, 9999)
        self._int_num.setValue(30)
        rl.addWidget(self._int_num)
        self._int_unit = QComboBox()
        for label, mult in _UNITS:
            self._int_unit.addItem(label, mult)
        self._int_unit.setCurrentIndex(1)  # 分钟
        rl.addWidget(self._int_unit)
        rl.addStretch()
        f_int.addRow("每隔:", row)
        self._stack.addWidget(w_int)
        # --- daily ---
        w_day = QWidget()
        f_day = QFormLayout(w_day)
        f_day.setContentsMargins(0, 0, 0, 0)
        self._day_time = QTimeEdit(QTime(9, 0))
        self._day_time.setDisplayFormat("HH:mm")
        f_day.addRow("时间:", self._day_time)
        self._stack.addWidget(w_day)
        # --- weekly(可多选)---
        w_week = QWidget()
        f_week = QFormLayout(w_week)
        f_week.setContentsMargins(0, 0, 0, 0)
        self._week_boxes: list[QCheckBox] = []
        days_row = QWidget()
        dl = QHBoxLayout(days_row)
        dl.setContentsMargins(0, 0, 0, 0)
        for i, (short, full) in enumerate(
            zip("一二三四五六日", ("周一", "周二", "周三", "周四", "周五", "周六", "周日"))
        ):
            cb = QCheckBox(short)
            cb.setToolTip(full)
            cb.toggled.connect(self._refresh_day_shortcuts)
            dl.addWidget(cb)
            self._week_boxes.append(cb)
        dl.addStretch()
        f_week.addRow("星期:", days_row)

        quick = QWidget()
        ql = QHBoxLayout(quick)
        ql.setContentsMargins(0, 0, 0, 0)
        for text, days in (
            ("工作日", (0, 1, 2, 3, 4)),
            ("周末", (5, 6)),
            ("每天", tuple(range(7))),
            ("清空", ()),
        ):
            b = QPushButton(text)
            b.setMaximumWidth(72)
            b.clicked.connect(lambda _c=False, d=days: self._set_weekdays(d))
            ql.addWidget(b)
        ql.addStretch()
        f_week.addRow("", quick)

        self._week_time = QTimeEdit(QTime(9, 0))
        self._week_time.setDisplayFormat("HH:mm")
        f_week.addRow("时间:", self._week_time)
        f_week.addRow("", QLabel("可多选,例如勾「一三五」就是每周一、三、五各跑一次。"))
        self._stack.addWidget(w_week)
        form.addRow("", self._stack)

        # 延时执行(四种频率通用)
        delay_row = QWidget()
        drl = QHBoxLayout(delay_row)
        drl.setContentsMargins(0, 0, 0, 0)
        self._delay = QSpinBox()
        self._delay.setRange(0, 86400)
        self._delay.setToolTip(
            "到点后再等这么多秒才真正执行。\n"
            "用来串流程:比如「22:00 启动全部」+「22:00 延时 30 秒开始连点」。"
        )
        drl.addWidget(self._delay)
        drl.addWidget(QLabel("秒"))
        drl.addWidget(QLabel("(0 = 到点立刻执行)"))
        drl.addStretch()
        form.addRow("延时执行:", delay_row)

        # 动作
        self._action = QComboBox()
        for key, label in _ACTION_ITEMS:
            self._action.addItem(label, key)
        self._action.currentIndexChanged.connect(self._on_action_changed)
        form.addRow("动作:", self._action)

        # 目标
        self._target = QComboBox()
        self._target.addItem("全部追踪窗口", 0)
        for hwnd, title in self._targets:
            self._target.addItem(f"[{hwnd}] {sanitize(title)[:36]}", int(hwnd))
        form.addRow("目标:", self._target)

        self._note = QLineEdit()
        form.addRow("备注:", self._note)

        self._enabled = QCheckBox("启用")
        self._enabled.setChecked(True if task is None else bool(task.enabled))
        form.addRow("", self._enabled)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self._on_accept)
        btns.rejected.connect(self.reject)
        form.addRow(btns)

        self._freq.currentIndexChanged.connect(
            lambda _i: self._stack.setCurrentIndex(self._freq.currentIndex())
        )
        if task is not None:
            self._load(task)
        self._stack.setCurrentIndex(self._freq.currentIndex())
        self._on_action_changed()

    # --- 星期多选 ---
    def _set_weekdays(self, days) -> None:
        want = {int(d) for d in days}
        for i, cb in enumerate(self._week_boxes):
            cb.setChecked(i in want)

    def _selected_weekdays(self) -> list[int]:
        return [i for i, cb in enumerate(self._week_boxes) if cb.isChecked()]

    def _refresh_day_shortcuts(self) -> None:
        n = len(self._selected_weekdays())
        if n == 7:
            tip = "每天"
        elif n == 0:
            tip = "未选择(保存时会提示)"
        else:
            tip = f"已选 {n} 天"
        self._week_time.setToolTip(tip)

    # --- 载入 ---
    def _load(self, t: ScheduledTask) -> None:
        idx = {"once": 0, "interval": 1, "daily": 2, "weekly": 3}.get(
            t.freq.value if isinstance(t.freq, TaskFreq) else str(t.freq), 0
        )
        self._freq.setCurrentIndex(idx)
        if t.run_at:
            try:
                self._once_at.setDateTime(QDateTime(_dt.datetime.fromisoformat(t.run_at)))
            except ValueError:
                pass
        sec = max(1, int(t.interval_sec))
        for i, (_label, mult) in enumerate(_UNITS):
            if sec % mult == 0:
                self._int_num.setValue(max(1, sec // mult))
                self._int_unit.setCurrentIndex(i)
                break
        self._day_time.setTime(QTime(int(t.hour), int(t.minute)))
        self._week_time.setTime(QTime(int(t.hour), int(t.minute)))
        days = t.effective_weekdays()
        self._set_weekdays(days if days else (0,))
        self._delay.setValue(max(0, int(t.delay_sec or 0)))
        ai = self._action.findData(t.action)
        if ai >= 0:
            self._action.setCurrentIndex(ai)
        ti = self._target.findData(int(t.target_hwnd or 0))
        if ti >= 0:
            self._target.setCurrentIndex(ti)
        self._note.setText(t.note or "")

    def _on_action_changed(self) -> None:
        # 「全部」类动作不需要指定目标窗口
        all_actions = {"start_all", "stop_all", "launch_games", "kill_games", "shutdown_app",
                       "volume_low", "volume_mute", "volume_restore"}
        self._target.setEnabled(self._action.currentData() not in all_actions)

    # --- 输出 ---
    def _on_accept(self) -> None:
        freq = TaskFreq(self._freq.currentData())
        if freq is TaskFreq.ONCE and self._once_at.dateTime().toPython() <= _dt.datetime.now():
            r = QMessageBox.question(self, "时间已过", "所选的触发时间已经过去了,仍要保存吗?")
            if r != QMessageBox.StandardButton.Yes:
                return
        if freq is TaskFreq.WEEKLY and not self._selected_weekdays():
            QMessageBox.information(self, "每周", "请至少勾一个星期。")
            return
        self._task = self._build()
        self.accept()

    def _build(self) -> ScheduledTask:
        freq = TaskFreq(self._freq.currentData())
        run_at = ""
        interval_sec = 3600
        hour = minute = 9
        weekday = -1
        days: list[int] = []
        if freq is TaskFreq.ONCE:
            run_at = self._once_at.dateTime().toPython().strftime("%Y-%m-%dT%H:%M")
        elif freq is TaskFreq.INTERVAL:
            mult = int(self._int_unit.currentData() or 1)
            interval_sec = max(1, int(self._int_num.value()) * mult)
        elif freq is TaskFreq.DAILY:
            t = self._day_time.time()
            hour, minute = t.hour(), t.minute()
        elif freq is TaskFreq.WEEKLY:
            t = self._week_time.time()
            hour, minute = t.hour(), t.minute()
            days = self._selected_weekdays() or [0]
            # 只勾一天时同时写老字段,方便降级到旧版本也能读
            weekday = days[0] if len(days) == 1 else -1
        return ScheduledTask(
            name=self._name.text().strip(),
            freq=freq,
            run_at=run_at,
            interval_sec=interval_sec,
            hour=hour,
            minute=minute,
            weekday=weekday,
            weekdays=days,
            delay_sec=int(self._delay.value()),
            action=str(self._action.currentData() or "start_all"),
            target_hwnd=int(self._target.currentData() or 0),
            enabled=self._enabled.isChecked(),
            note=self._note.text().strip(),
        )

    def task(self) -> ScheduledTask:
        return self._task


class ScheduleDialog(QDialog):
    """定时任务管理器(列表 + 增删改 + 立即执行)."""

    COLUMNS = ("启用", "名称", "计划", "动作", "目标", "下次触发", "已执行")

    def __init__(self, scheduler: Scheduler, targets=None, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("定时任务")
        self.resize(920, 460)
        self._sched = scheduler
        self._targets = list(targets or [])

        v = QVBoxLayout(self)
        tip = QLabel(
            "定时任务会在后台按计划执行。任务保存在 %APPDATA%\\AutoFarmStation\\schedules.json,\n"
            "关掉软件再打开依然有效。"
        )
        tip.setStyleSheet("color:#888; font-size:11px;")
        v.addWidget(tip)

        self._table = QTableWidget(0, len(self.COLUMNS))
        self._table.setHorizontalHeaderLabels(list(self.COLUMNS))
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        hh = self._table.horizontalHeader()
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        v.addWidget(self._table, stretch=1)

        row = QHBoxLayout()
        self._btn_add = QPushButton("＋ 新增")
        self._btn_add.clicked.connect(self._on_add)
        row.addWidget(self._btn_add)
        self._btn_edit = QPushButton("编辑")
        self._btn_edit.clicked.connect(self._on_edit)
        row.addWidget(self._btn_edit)
        self._btn_del = QPushButton("删除")
        self._btn_del.clicked.connect(self._on_delete)
        row.addWidget(self._btn_del)
        self._btn_toggle = QPushButton("启用 / 停用")
        self._btn_toggle.clicked.connect(self._on_toggle)
        row.addWidget(self._btn_toggle)
        row.addStretch()
        self._btn_run = QPushButton("立即执行一次")
        self._btn_run.clicked.connect(self._on_run_now)
        row.addWidget(self._btn_run)
        v.addLayout(row)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btns.rejected.connect(self.reject)
        btns.accepted.connect(self.accept)
        v.addWidget(btns)

        self._reload()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh_times)
        self._timer.start(5000)

    # --- 列表 ---
    def _row_of(self, name: str) -> int:
        for r in range(self._table.rowCount()):
            item = self._table.item(r, 1)
            if item is not None and item.data(Qt.ItemDataRole.UserRole) == name:
                return r
        return -1

    def _selected_name(self) -> str | None:
        rows = self._table.selectionModel().selectedRows() if self._table.selectionModel() else []
        if not rows:
            return None
        item = self._table.item(rows[0].row(), 1)
        return str(item.data(Qt.ItemDataRole.UserRole)) if item else None

    def _reload(self) -> None:
        tasks = self._sched.list()
        self._table.setRowCount(len(tasks))
        for r, t in enumerate(tasks):
            chk = QTableWidgetItem("✔" if t.enabled else "—")
            chk.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._table.setItem(r, 0, chk)

            name_item = QTableWidgetItem(sanitize(t.name))
            name_item.setData(Qt.ItemDataRole.UserRole, t.name)
            self._table.setItem(r, 1, name_item)
            self._table.setItem(r, 2, QTableWidgetItem(t.schedule_text()))
            self._table.setItem(r, 3, QTableWidgetItem(action_label(t.action)))
            target = "全部" if not t.target_hwnd else f"[{t.target_hwnd}]"
            self._table.setItem(r, 4, QTableWidgetItem(target))
            self._table.setItem(r, 5, QTableWidgetItem(self._sched.next_run_text(t)))
            self._table.setItem(r, 6, QTableWidgetItem(str(t.run_count)))
        self._refresh_times()

    def _refresh_times(self) -> None:
        for r in range(self._table.rowCount()):
            item = self._table.item(r, 1)
            if item is None:
                continue
            t = self._sched.get(str(item.data(Qt.ItemDataRole.UserRole)))
            if t is None:
                continue
            self._table.item(r, 0).setText("✔" if t.enabled else "—")
            self._table.item(r, 5).setText(self._sched.next_run_text(t))
            self._table.item(r, 6).setText(str(t.run_count))

    # --- 操作 ---
    def _on_add(self) -> None:
        dlg = TaskEditDialog(None, self._targets, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            name = self._sched.add(dlg.task())
            self._reload()
            self._select(name)

    def _on_edit(self) -> None:
        name = self._selected_name()
        if not name:
            QMessageBox.information(self, "定时任务", "请先选中一行。")
            return
        task = self._sched.get(name)
        if task is None:
            return
        dlg = TaskEditDialog(task, self._targets, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            new = dlg.task()
            new.name = name
            self._sched.update(name, new)
            self._reload()

    def _on_delete(self) -> None:
        name = self._selected_name()
        if not name:
            return
        if QMessageBox.question(self, "删除", f"确认删除任务「{sanitize(name)}」?") \
                != QMessageBox.StandardButton.Yes:
            return
        self._sched.remove(name)
        self._reload()

    def _on_toggle(self) -> None:
        name = self._selected_name()
        if not name:
            return
        task = self._sched.get(name)
        if task is None:
            return
        self._sched.set_enabled(name, not task.enabled)
        self._reload()

    def _on_run_now(self) -> None:
        name = self._selected_name()
        if not name:
            QMessageBox.information(self, "定时任务", "请先选中一行。")
            return
        if self._sched.run_now(name):
            self._reload()
            QMessageBox.information(self, "定时任务", f"已执行「{sanitize(name)}」。")
        else:
            QMessageBox.warning(self, "定时任务", "执行失败(任务不存在,或动作没有注册处理函数)。")

    def _select(self, name: str) -> None:
        r = self._row_of(name)
        if r >= 0:
            self._table.selectRow(r)


def describe_schedule_summary(scheduler: Scheduler) -> str:
    """给面板用的一行摘要."""
    tasks = scheduler.list()
    if not tasks:
        return "还没有定时任务"
    active = [t for t in tasks if t.enabled]
    if not active:
        return f"{len(tasks)} 个任务(全部已停用)"
    nxt = min(active, key=lambda t: t.next_run or float("inf"))
    return (
        f"{len(active)}/{len(tasks)} 个任务启用中"
        f" · 最近:{sanitize(nxt.name)} {scheduler.next_run_text(nxt)}"
    )
