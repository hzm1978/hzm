from datetime import date, timedelta
import sys

from PySide6.QtCore import QDate
from PySide6.QtWidgets import (
    QComboBox, QDateEdit, QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QVBoxLayout,
)

from .bars import PERIODS, now_beijing
from .connection import Connection


class HistoryDialog(QDialog):
    def __init__(self, database, symbol, period, credentials, parent=None):
        super().__init__(parent); self.setWindowTitle("从天勤按日期载入历史回放"); self.resize(620, 430)
        self.database, self.credentials = database, credentials
        self.connection = None; self.result = None
        layout = QVBoxLayout(self)
        intro = QLabel("使用天勤 TqBacktest 历史回放接口，逐步接收并缓存所选日期的数据。")
        intro.setWordWrap(True); layout.addWidget(intro)
        form = QFormLayout()
        self.symbol = QLineEdit(symbol); form.addRow("主连／实际合约代码", self.symbol)
        self.period = QComboBox(); self.period.addItems(list(PERIODS)[:7])
        self.period.setCurrentText(period if period in list(PERIODS)[:7] else "日线")
        form.addRow("基础周期", self.period)
        self.start = QDateEdit(); self.end = QDateEdit()
        today = now_beijing().date()
        self.start.setDate(QDate(today.year, today.month, today.day).addMonths(-1))
        self.end.setDate(QDate(today.year, today.month, today.day))
        for edit in (self.start, self.end):
            edit.setCalendarPopup(True); edit.setDisplayFormat("yyyy-MM-dd")
            edit.setMaximumDate(QDate(today.year, today.month, today.day))
        form.addRow("开始交易日", self.start); form.addRow("结束交易日", self.end)
        layout.addLayout(form)
        note = QLabel(
            "实际最早日期取决于合约上市、服务器数据和账号权限，不预设所有品种都从2016年开始。\n"
            "分钟数据建议分月／季度载入；数据持续保留，单场训练最多装入最近100000根基础K线。\n"
            "这不是专业版历史下载接口；若服务拒绝，会显示原始错误。实时看盘继续独立运行。")
        note.setWordWrap(True); layout.addWidget(note)
        self.progress = QLabel("等待开始"); self.progress.setWordWrap(True); layout.addWidget(self.progress)
        self.phase = QLabel(); self.phase.setWordWrap(True); layout.addWidget(self.phase)
        row = QHBoxLayout()
        self.run = QPushButton("开始载入"); self.run.setObjectName("Primary"); self.run.clicked.connect(self.begin)
        self.stop = QPushButton("停止"); self.stop.setEnabled(False)
        self.stop.clicked.connect(lambda:self.connection.stop() if self.connection else None)
        self.use = QPushButton("用已载入数据开始复盘"); self.use.setEnabled(False); self.use.clicked.connect(self.accept)
        close = QPushButton("关闭"); close.clicked.connect(self.reject)
        for widget in (self.run, self.stop, self.use, close):
            row.addWidget(widget)
        layout.addLayout(row)

    def begin(self):
        if self.start.date() > self.end.date():
            self.progress.setText("开始日期不能晚于结束日期"); return
        symbol = self.symbol.text().strip()
        if not symbol or "." not in symbol or symbol.startswith(("LOCAL.", "DEMO.")):
            self.progress.setText("请输入有效的天勤主连或实际合约代码"); return
        if self.connection and self.connection.process is not None:
            return
        config = dict(symbol=symbol, seconds=PERIODS[self.period.currentText()],
                      start=self.start.date().toString("yyyy-MM-dd"), end=self.end.date().toString("yyyy-MM-dd"))
        arguments = ["--history-worker"] if getattr(sys, "frozen", False) else ["-X", "utf8", "-u", "-m", "tq_workbench.history_worker"]
        self.connection = Connection(self.database, self, arguments=arguments, configuration=config)
        self.connection.statusChanged.connect(self.phase.setText)
        self.connection.message.connect(self.receive)
        self.connection.changed.connect(self.changed)
        self.run.setEnabled(False); self.stop.setEnabled(True); self.use.setEnabled(False)
        self.result = None
        self.connection.connect_account(*self.credentials)

    def receive(self, message):
        if message.get("kind") in ("progress", "complete"):
            self.progress.setText(
                f"已接收 {message.get('count', 0):,} 根  |  "
                f"实际起点 {message.get('first') or '—'}\n实际末端 {message.get('last') or '—'}")
        if message.get("kind") == "complete":
            self.result = dict(message, period=self.period.currentText())
            self.use.setEnabled(bool(message.get("count")))
            self.run.setEnabled(True); self.stop.setEnabled(False)
            if not message.get("count"):
                self.progress.setText("该范围没有返回可用K线，请核对合约上市日期、范围及服务权限。")
        if message.get("kind") == "error":
            self.run.setEnabled(True); self.stop.setEnabled(False)

    def changed(self):
        if self.connection and self.connection.error:
            self.phase.setText(self.connection.error)
            self.run.setEnabled(True); self.stop.setEnabled(False)

    def done(self, result):
        if self.connection is not None:
            self.connection.shutdown()
        self.credentials = ()
        super().done(result)
