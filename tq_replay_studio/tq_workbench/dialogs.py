from dataclasses import asdict
import csv
from pathlib import Path

import pandas as pd
from PySide6.QtCore import Qt, QDateTime, QTimer
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDateTimeEdit, QDialog, QDialogButtonBox,
    QDoubleSpinBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMessageBox, QPlainTextEdit, QPushButton, QSpinBox,
    QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from .backtest import ma_cross
from .bars import PERIODS
from .credentials import LocalLoginStore
from .paper import Contract
from .tasks import launch


def buttons(dialog, layout, text="确定"):
    box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
    box.button(QDialogButtonBox.StandardButton.Ok).setText(text)
    box.accepted.connect(dialog.accept); box.rejected.connect(dialog.reject)
    layout.addWidget(box)
    return box


def table_fill(table, columns, rows):
    table.setColumnCount(len(columns)); table.setHorizontalHeaderLabels([label for _, label in columns])
    table.setRowCount(len(rows))
    for r, row in enumerate(rows):
        for c, (key, _) in enumerate(columns):
            value = row.get(key, "")
            text = f"{value:,.2f}" if isinstance(value, float) else str(value)
            item = QTableWidgetItem(text); item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            table.setItem(r, c, item)
    table.resizeColumnsToContents()
    table.horizontalHeader().setStretchLastSection(True)


FILL_COLUMNS = [("timestamp", "时间"), ("action", "方向"), ("qty", "手数"),
                ("price", "成交价"), ("pnl", "平仓毛盈亏"), ("fee", "手续费"), ("reason", "来源")]


def export_fills(parent, fills):
    filename, _ = QFileDialog.getSaveFileName(parent, "导出成交记录", "模拟成交记录.csv", "CSV (*.csv)")
    if not filename:
        return
    try:
        with Path(filename).open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow([label for _, label in FILL_COLUMNS])
            writer.writerows([[row.get(key, "") for key, _ in FILL_COLUMNS] for row in fills])
    except OSError as exc:
        QMessageBox.warning(parent, "导出失败", str(exc))


class LoginDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent); self.setWindowTitle("连接天勤行情"); self.resize(460, 260)
        layout = QVBoxLayout(self)
        intro = QLabel('使用天勤／快期账号。<a href="https://account.shinnytech.com/">注册免费账号</a>')
        intro.setOpenExternalLinks(True); layout.addWidget(intro)
        form = QFormLayout()
        self.username = QLineEdit(); self.username.setObjectName("username")
        self.password = QLineEdit(); self.password.setObjectName("password")
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("账号", self.username); form.addRow("密码", self.password); layout.addLayout(form)
        self.store = LocalLoginStore()
        self.remember = QCheckBox("记住登录（Windows当前用户加密保存）")
        self.remember.setEnabled(self.store.available); self.remember.setChecked(self.store.available)
        layout.addWidget(self.remember)
        saved = self.store.load()
        if saved:
            self.username.setText(saved[0]); self.password.setText(saved[1])
        note = QLabel("免费按每周期最近最多10000根订阅；本程序自动缓存接收的行情。\n模拟交易由本机训练账户记录。")
        note.setWordWrap(True); layout.addWidget(note)
        self.error = QLabel(); layout.addWidget(self.error)
        buttons(self, layout, "连接行情")

    def accept(self):
        if not self.username.text().strip() or not self.password.text():
            self.error.setText("请输入账号和密码"); return
        try:
            if self.remember.isChecked():
                self.store.save(self.username.text().strip(), self.password.text())
            elif self.store.available:
                self.store.clear()
        except OSError as exc:
            self.error.setText(str(exc)); return
        super().accept()


class ConnectionDialog(QDialog):
    def __init__(self, connection, parent=None):
        super().__init__(parent); self.setWindowTitle("行情连接详情"); self.resize(900, 580)
        layout = QVBoxLayout(self)
        self.text = QPlainTextEdit(); self.text.setReadOnly(True); layout.addWidget(self.text)
        self.text.setPlainText(connection.details())
        row = QHBoxLayout()
        refresh = QPushButton("刷新"); refresh.clicked.connect(lambda:self.text.setPlainText(connection.details()))
        copy = QPushButton("复制详情"); copy.clicked.connect(lambda:QApplication.clipboard().setText(self.text.toPlainText()))
        close = QPushButton("关闭"); close.clicked.connect(self.reject)
        row.addWidget(refresh); row.addWidget(copy); row.addStretch(); row.addWidget(close)
        layout.addLayout(row)


class SessionDialog(QDialog):
    def __init__(self, frame, contract, parent=None):
        super().__init__(parent); self.setWindowTitle("新建复盘训练"); self.resize(470, 400)
        layout = QVBoxLayout(self); form = QFormLayout()
        self.time = QDateTimeEdit(); self.time.setCalendarPopup(True); self.time.setDisplayFormat("yyyy-MM-dd HH:mm")
        self.time.setDateTime(QDateTime(frame.iloc[max(0, len(frame)-180)]["datetime"].to_pydatetime()))
        self.time.setMinimumDateTime(QDateTime(frame.iloc[0]["datetime"].to_pydatetime()))
        self.time.setMaximumDateTime(QDateTime(frame.iloc[-1]["datetime"].to_pydatetime()))
        form.addRow("从此时开始（就近已存在K线）", self.time)
        self.capital = QDoubleSpinBox(); self.capital.setRange(100, 1_000_000_000); self.capital.setValue(1_000_000)
        self.capital.setDecimals(2); form.addRow("初始资金", self.capital)
        self.parameters = {}
        for key, label, low, high, decimals in (
            ("multiplier", "合约乘数", .001, 1_000_000, 3),
            ("tick", "最小变动价位", .0001, 10000, 4),
            ("margin_rate", "保证金比例", .001, 1, 3),
            ("fee_per_lot", "每手单边手续费", 0, 10000, 2),
            ("slippage_ticks", "滑点（跳）", 0, 100, 0)):
            spin = QDoubleSpinBox(); spin.setRange(low, high); spin.setDecimals(decimals)
            spin.setValue(getattr(contract, key)); form.addRow(label, spin); self.parameters[key] = spin
        layout.addLayout(form)
        note = QLabel("乘数及价位优先使用接口资料；保证金、手续费为可调整的训练参数。\n开始后冻结该训练的数据与参数，上一场训练自动保留。")
        note.setWordWrap(True); layout.addWidget(note)
        buttons(self, layout, "开始新训练")

    def contract(self):
        values = {key: spin.value() for key, spin in self.parameters.items()}
        values["slippage_ticks"] = int(values["slippage_ticks"])
        return Contract(**values)


class SessionsDialog(QDialog):
    def __init__(self, sessions, parent=None):
        super().__init__(parent); self.setWindowTitle("训练记录"); self.resize(760, 440)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("恢复已冻结的数据、复盘位置、持仓、委托和画线。"))
        self.items = QListWidget()
        for entry in sessions:
            item = QListWidgetItem(f"{entry['name']}  |  {entry['period']}  |  {entry['updated'][:19]}")
            item.setData(Qt.ItemDataRole.UserRole, entry["id"]); self.items.addItem(item)
        layout.addWidget(self.items); self.items.itemDoubleClicked.connect(lambda _:self.accept())
        if self.items.count():
            self.items.setCurrentRow(0)
        buttons(self, layout, "恢复训练")

    def identifier(self):
        item = self.items.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None


class ImportDialog(QDialog):
    def __init__(self, period, parent=None):
        super().__init__(parent); self.setWindowTitle("导入历史数据")
        layout = QVBoxLayout(self); form = QFormLayout()
        self.symbol = QLineEdit("MYDATA")
        self.name = QLineEdit("导入历史")
        self.period = QComboBox(); self.period.addItems(list(PERIODS)[:7]); self.period.setCurrentText(period)
        form.addRow("本地品种代码", self.symbol); form.addRow("名称", self.name); form.addRow("文件周期", self.period)
        layout.addLayout(form)
        note = QLabel("保存为 LOCAL.代码，不覆盖天勤行情。\n必需列：datetime, open, high, low, close；支持CSV、Parquet。")
        note.setWordWrap(True); layout.addWidget(note)
        buttons(self, layout, "选择数据文件")

    def accept(self):
        if not self.symbol.text().strip().replace("_", "").replace("-", "").isalnum():
            QMessageBox.warning(self, "代码无效", "代码请使用字母、数字、下划线或短横线"); return
        super().accept()


class BacktestDialog(QDialog):
    def __init__(self, visible, complete, contract, parent=None):
        super().__init__(parent); self.setWindowTitle("均线策略回测"); self.resize(950, 620)
        self.visible, self.complete, self.contract = visible.copy(), complete.copy(), contract
        self.result = None; self.task = None
        layout = QVBoxLayout(self); row = QHBoxLayout()
        self.fast = QSpinBox(); self.fast.setRange(1, 1000); self.fast.setValue(5)
        self.slow = QSpinBox(); self.slow.setRange(2, 5000); self.slow.setValue(20)
        row.addWidget(QLabel("短均线")); row.addWidget(self.fast)
        row.addWidget(QLabel("长均线")); row.addWidget(self.slow)
        self.full = QCheckBox("使用整段已加载历史"); row.addWidget(self.full)
        self.run_button = QPushButton("运行回测"); self.run_button.setObjectName("Primary")
        self.run_button.clicked.connect(self.start); row.addWidget(self.run_button); layout.addLayout(row)
        self.summary = QLabel("默认仅使用复盘已揭示数据。前一收盘确认信号，下一根开盘成交；每次1手，计手续费、滑点和保证金。")
        self.summary.setWordWrap(True); layout.addWidget(self.summary)
        self.table = QTableWidget(); layout.addWidget(self.table)
        row = QHBoxLayout()
        export = QPushButton("导出回测成交"); export.clicked.connect(lambda:export_fills(self, self.result["fills"]) if self.result else None)
        close = QPushButton("关闭"); close.clicked.connect(self.reject)
        row.addWidget(export); row.addStretch(); row.addWidget(close); layout.addLayout(row)

    def start(self):
        self.run_button.setEnabled(False)
        self.summary.setText("正在独立计算，复盘进度保持原位…")
        frame = self.complete if self.full.isChecked() else self.visible
        fast, slow = self.fast.value(), self.slow.value()
        self.task = launch(lambda:ma_cross(frame, fast, slow, contract=self.contract), self.done, self.failed)

    def done(self, result):
        self.result = result; self.run_button.setEnabled(True)
        self.summary.setText(
            f"收益 {result['profit']:,.2f}  |  收益率 {result['return_percent']:.2f}%  |  "
            f"最大回撤 {result['max_drawdown_percent']:.2f}%  |  平仓 {result['closed_trades']}笔  |  "
            f"手续费 {result['fees']:,.2f}  |  资金不足拒绝 {len(result['rejected'])}次")
        table_fill(self.table, FILL_COLUMNS, result["fills"])

    def failed(self, error):
        self.run_button.setEnabled(True); self.summary.setText(error)
