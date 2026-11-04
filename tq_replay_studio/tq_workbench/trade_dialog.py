"""Real-time simulated and live trading, separate from historical training."""
import csv
from pathlib import Path
import sys

from PySide6.QtCore import QSignalBlocker, Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFileDialog, QFormLayout,
    QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMessageBox, QPushButton,
    QSpinBox, QTabWidget, QTableWidget, QVBoxLayout,
)

from .bars import finite, now_beijing
from .connection import Connection
from .dialogs import ConnectionDialog, table_fill

MODES = [("实时模拟 · TqSim本机会话", "sim"), ("快期云端模拟 · TqKq", "kq"),
         ("徽商期货实盘 · TqAccount", "live")]
ORDER_ACTIONS = {"买入开多": ("BUY", "OPEN"), "卖出开空": ("SELL", "OPEN"),
                 "卖出平多": ("SELL", "CLOSE"), "买入平空": ("BUY", "CLOSE")}


class TradeDialog(QDialog):
    def __init__(self, owner):
        super().__init__(owner, Qt.WindowType.Window |
            Qt.WindowType.WindowTitleHint | Qt.WindowType.WindowSystemMenuHint |
            Qt.WindowType.WindowMinMaxButtonsHint | Qt.WindowType.WindowCloseButtonHint)
        self.owner = owner
        self.connection = None
        self.latest = {}
        self._previous_mode = 0
        self._preview_pending = False
        self._pending_order_ids = set()
        self.setWindowTitle("实时模拟／实盘交易 · 天勤复盘工作台")
        self.resize(1120, 820)
        layout = QVBoxLayout(self)
        self.banner = QLabel("实时模拟交易"); self.banner.setObjectName("Brand"); layout.addWidget(self.banner)
        row = QHBoxLayout()
        self.mode = QComboBox()
        for title, value in MODES:
            self.mode.addItem(title, value)
        self.mode.currentIndexChanged.connect(self.mode_changed)
        row.addWidget(self.mode)
        self.connect_button = QPushButton("连接交易账户"); self.connect_button.setObjectName("Primary")
        self.connect_button.clicked.connect(self.connect_account); row.addWidget(self.connect_button)
        disconnect = QPushButton("断开"); disconnect.clicked.connect(self.disconnect); row.addWidget(disconnect)
        details = QPushButton("连接详情"); details.clicked.connect(self.details); row.addWidget(details)
        row.addStretch(); layout.addLayout(row)
        self.broker_form = QFormLayout()
        self.broker = QLineEdit("H徽商期货")
        self.fund_account = QLineEdit(); self.fund_account.setPlaceholderText("期货资金账号，与天勤账号不同")
        self.fund_password = QLineEdit(); self.fund_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.broker_form.addRow("期货公司", self.broker)
        self.broker_form.addRow("资金账号", self.fund_account); self.broker_form.addRow("交易密码", self.fund_password)
        layout.addLayout(self.broker_form)
        row = QHBoxLayout()
        self.max_lots = QSpinBox(); self.max_lots.setRange(1, 10000); self.max_lots.setValue(5)
        self.max_amount = QDoubleSpinBox(); self.max_amount.setRange(100, 1_000_000_000); self.max_amount.setValue(5_000_000); self.max_amount.setDecimals(0)
        self.quote_age = QSpinBox(); self.quote_age.setRange(1, 3600); self.quote_age.setValue(120)
        for label, widget in (("单笔最多手数", self.max_lots), ("名义金额上限", self.max_amount), ("报价时效（秒）", self.quote_age)):
            row.addWidget(QLabel(label)); row.addWidget(widget)
        layout.addLayout(row)
        self.arm = QCheckBox("启用实盘下单（每次委托仍需确认）"); self.arm.setEnabled(False)
        self.arm.toggled.connect(self.arm_changed); layout.addWidget(self.arm)
        self.status = QLabel("先连接天勤行情账号，再连接此处的模拟或实盘账户。")
        self.status.setWordWrap(True); layout.addWidget(self.status)
        self.account = QLabel("权益 —   可用 —   保证金 —   浮动盈亏 —")
        self.account.setObjectName("Metric"); layout.addWidget(self.account)
        self.quote = QLabel("等待实际期货合约报价"); self.quote.setWordWrap(True); layout.addWidget(self.quote)
        row = QHBoxLayout()
        self.actual = QLineEdit(); self.actual.setReadOnly(True); self.actual.setPlaceholderText("主连自动解析为实际合约")
        row.addWidget(self.actual)
        self.action = QComboBox(); self.action.addItems(ORDER_ACTIONS); row.addWidget(self.action)
        self.close_today = QCheckBox("平今（上期所／能源）"); row.addWidget(self.close_today)
        self.quantity = QSpinBox(); self.quantity.setRange(1, 10000); self.quantity.setValue(1)
        row.addWidget(QLabel("手数")); row.addWidget(self.quantity); layout.addLayout(row)
        row = QHBoxLayout()
        self.price_mode = QComboBox(); self.price_mode.addItems(["对手价限价", "指定限价"]); row.addWidget(self.price_mode)
        self.price = QDoubleSpinBox(); self.price.setRange(.0001, 1_000_000_000); self.price.setDecimals(4); self.price.setEnabled(False)
        self.price_mode.currentIndexChanged.connect(lambda index:self.price.setEnabled(index == 1))
        row.addWidget(self.price)
        self.send = QPushButton("提交模拟委托"); self.send.setEnabled(False); self.send.setObjectName("Primary")
        self.send.clicked.connect(self.prepare); row.addWidget(self.send)
        cancel = QPushButton("撤销选中委托"); cancel.clicked.connect(self.cancel_selected); row.addWidget(cancel)
        layout.addLayout(row)
        self.tabs = QTabWidget()
        self.positions = QTableWidget(); self.orders = QTableWidget(); self.trades = QTableWidget()
        for widget, title in ((self.positions, "持仓"), (self.orders, "委托"), (self.trades, "成交")):
            widget.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
            self.tabs.addTab(widget, title)
        layout.addWidget(self.tabs, 1)
        note = QLabel("主连仅用于选品种，委托发送到上方实际合约。平今／平昨需按持仓选择。"
                      "超过报价时效时拒绝发送，确认有效期20秒，发送异常不自动重试。\n"
                      "TqSim会话结束后重新开始；TqKq模拟由云端账户保存。关闭此窗口会锁定实盘，已发送委托仍由柜台管理。")
        note.setWordWrap(True); note.setObjectName("Muted"); layout.addWidget(note)
        row = QHBoxLayout()
        export = QPushButton("导出当前成交"); export.clicked.connect(self.export_trades)
        row.addWidget(export); row.addStretch()
        close = QPushButton("收起窗口"); close.clicked.connect(self.close); row.addWidget(close)
        layout.addLayout(row)
        self.mode_changed()

    def mode_changed(self, *_):
        if self.connection and self.connection.process is not None:
            with QSignalBlocker(self.mode):
                self.mode.setCurrentIndex(self._previous_mode)
            self.status.setText("请先断开当前交易账户，再切换账户类型。")
            return
        self._previous_mode = self.mode.currentIndex()
        real = self.mode.currentData() == "live"
        for row in range(self.broker_form.rowCount()):
            self.broker_form.setRowVisible(row, real)
        self.arm.setVisible(real); self.arm.setChecked(False); self.arm.setEnabled(False)
        self.banner.setText("徽商期货 · 真实资金账户" if real else self.mode.currentText())
        self.banner.setStyleSheet("color:#ff9e8c" if real else "color:#77c8ff")
        self.send.setText("核对并提交实盘委托" if real else "提交模拟委托")
        self.send.setEnabled(False)

    def connect_account(self):
        if self.connection and self.connection.process is not None:
            self.status.setText("当前交易连接仍在运行。需要重连时请先断开。"); return
        if not self.owner.connection._credentials:
            self.owner.login()
        if not self.owner.connection._credentials:
            return
        mode = self.mode.currentData()
        if mode == "live" and (not self.fund_account.text().strip() or not self.fund_password.text()):
            self.status.setText("请输入本机的期货资金账号和交易密码。"); return
        config = dict(mode=mode, policy=dict(max_lots=self.max_lots.value(),
            max_notional=self.max_amount.value(), max_quote_age=self.quote_age.value()))
        if mode == "live":
            config.update(broker=self.broker.text().strip(), broker_account=self.fund_account.text().strip(),
                          broker_password=self.fund_password.text())
        args = ["--trade-worker"] if getattr(sys, "frozen", False) else ["-X", "utf8", "-u", "-m", "tq_workbench.trade_worker"]
        self.connection = Connection(self.owner.store.path, self, arguments=args, configuration=config)
        self.connection.statusChanged.connect(self.status.setText)
        self.connection.message.connect(self.receive)
        self.connection.changed.connect(self.connection_changed)
        self.latest = {}; self._preview_pending = False
        self._pending_order_ids.clear()
        self.follow(self.owner.symbol)
        self.connection.connect_account(*self.owner.connection._credentials)
        for widget in (self.mode, self.broker, self.fund_account, self.fund_password, self.max_lots, self.max_amount, self.quote_age):
            widget.setEnabled(False)
        self.connect_button.setEnabled(False)

    def follow(self, symbol):
        if self.connection is not None and not symbol.startswith(("LOCAL.", "DEMO.")):
            self.connection.select(symbol, [])
            self.actual.clear()
            self.send.setEnabled(False)
            self._preview_pending = False

    def connection_changed(self):
        running = self.connection is not None and self.connection.process is not None
        if not running:
            self.send.setEnabled(False); self.arm.setEnabled(False)
            with QSignalBlocker(self.arm):
                self.arm.setChecked(False)
            self.connect_button.setEnabled(True)
            for widget in (self.mode, self.broker, self.fund_account, self.fund_password, self.max_lots, self.max_amount, self.quote_age):
                widget.setEnabled(True)

    def receive(self, message):
        kind = message.get("kind")
        if kind == "trade_snapshot":
            self.latest = message
            self._pending_order_ids.difference_update(
                order.get("order_id", "") for order in message.get("orders", []))
            real = message["mode"] == "live"
            account = message["account"]
            self.account.setText(
                f"权益 {finite(account.get('balance'), 0):,.2f}   可用 {finite(account.get('available'), 0):,.2f}   "
                f"保证金 {finite(account.get('margin'), 0):,.2f}   浮盈亏 {finite(account.get('float_profit'), 0):+,.2f}")
            if message.get("revision") == self.connection.revision:
                self.actual.setText(message.get("symbol", ""))
                quote = message["quote"]
                self.quote.setText(f"实际合约 {message.get('symbol')}   最新 {quote.get('last_price')}   "
                    f"买一 {quote.get('bid_price1')} / 卖一 {quote.get('ask_price1')}   行情时间 {quote.get('datetime')}")
                tick = finite(quote.get("price_tick"), 1)
                if tick > 0:
                    self.price.setSingleStep(tick)
                self.send.setEnabled(bool(message.get("symbol")) and (not real or message.get("armed", False)) and not self._preview_pending)
            self.arm.setEnabled(real)
            table_fill(self.positions, [("symbol", "合约"), ("pos_long", "多仓"), ("pos_short", "空仓"),
                ("pos_long_today", "今多"), ("pos_long_his", "昨多"), ("pos_short_today", "今空"),
                ("pos_short_his", "昨空"), ("float_profit", "浮动盈亏")], message["positions"])
            table_fill(self.orders, [("order_id", "委托号"), ("instrument_id", "合约"), ("direction", "买卖"),
                ("offset", "开平"), ("volume_orign", "手数"), ("volume_left", "未成"), ("limit_price", "限价"),
                ("status", "状态"), ("last_msg", "柜台信息")], message["orders"])
            table_fill(self.trades, [("trade_id", "成交号"), ("instrument_id", "合约"), ("direction", "买卖"),
                ("offset", "开平"), ("volume", "手数"), ("price", "成交价")], message["trades"])
        elif kind == "armed":
            self.status.setText("实盘已解锁，每单仍需确认" if message.get("enabled") else "实盘下单已锁定")
            self.send.setEnabled(bool(self.actual.text()) and (self.mode.currentData() != "live" or message.get("enabled", False)))
        elif kind == "review":
            self.review(message)
        elif kind in ("command_error", "order_sent", "cancel_sent"):
            if kind == "order_sent" and message.get("mode") == "live":
                self._pending_order_ids.add(message.get("order_id", ""))
            self._preview_pending = False
            self.send.setEnabled(bool(self.actual.text()) and (self.mode.currentData() != "live" or self.arm.isChecked()))
            self.status.setText(message.get("text") or (
                "委托已交给SDK，成交与否以委托／成交表为准：" if kind == "order_sent" else "撤单请求已发送：")+message.get("order_id", ""))

    def arm_changed(self, enabled):
        if not self.connection or not self.connection.running:
            with QSignalBlocker(self.arm):
                self.arm.setChecked(False)
            return
        if enabled:
            suffix = self.fund_account.text()[-4:]
            text, accepted = QInputDialog.getText(self, "启用真实资金账户",
                f"即将允许向 {self.broker.text()} 账号尾号 {suffix} 发送真实委托。\n"
                "输入“启用实盘”解锁本次连接；每次委托仍需核对确认。")
            if not accepted or text != "启用实盘":
                with QSignalBlocker(self.arm):
                    self.arm.setChecked(False)
                return
        self.connection._send(dict(command="arm", enabled=bool(enabled)))

    def prepare(self):
        if not self.connection or self._preview_pending:
            return
        direction, offset = ORDER_ACTIONS[self.action.currentText()]
        if offset != "OPEN" and self.close_today.isChecked():
            offset = "CLOSETODAY"
        quote = self.latest.get("quote", {})
        price = (finite(quote.get("ask_price1" if direction == "BUY" else "bid_price1"))
                 if self.price_mode.currentIndex() == 0 else self.price.value())
        if price is None or price <= 0:
            self.status.setText("对手报价尚未有效返回；可等待新行情或填写有效指定限价。"); return
        self._preview_pending = True; self.send.setEnabled(False)
        self.connection._send(dict(command="prepare", order=dict(symbol=self.actual.text(),
            direction=direction, offset=offset, volume=self.quantity.value(), limit_price=price)))

    def review(self, proposal):
        accepted = True
        if proposal["mode"] == "live":
            label = f"{self.broker.text()} · 资金账号尾号 {self.fund_account.text()[-4:]}"
            text = (
                f"真实账户：{label}\n实际合约：{proposal['symbol']}\n"
                f"方向：{proposal['direction']}  开平：{proposal['offset']}\n"
                f"手数：{proposal['volume']}  限价：{proposal['limit_price']:g}\n"
                f"名义金额：{proposal['notional']:,.2f}\n报价时间：{proposal['quote_time']}\n\n"
                "是否发送这笔真实委托？本次确认20秒内有效，发送异常不自动重试。")
            accepted = QMessageBox.question(self, "确认真实委托", text,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes
        if accepted:
            self.connection._send(dict(command="submit", token=proposal["token"]))
        else:
            self._preview_pending = False
            self.send.setEnabled(self.arm.isChecked())

    def cancel_selected(self):
        if not self.connection or not self.connection.running:
            return
        row = self.orders.currentRow()
        if row < 0 or self.orders.item(row, 0) is None:
            self.status.setText("请在委托表中选中一笔委托"); return
        identifier = self.orders.item(row, 0).text()
        if self.mode.currentData() == "live":
            if QMessageBox.question(self, "确认撤单", "撤销真实委托："+identifier,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
                return
        self.connection._send(dict(command="cancel", order_id=identifier))

    def has_live_orders(self):
        return self.latest.get("mode") == "live" and (
            self._preview_pending or bool(self._pending_order_ids) or
            any(order.get("status") == "ALIVE" and finite(order.get("volume_left"), 0) > 0
                for order in self.latest.get("orders", [])))

    def disconnect(self):
        if self.has_live_orders():
            if QMessageBox.warning(self, "真实委托仍在柜台",
                "断开连接不会自动撤销未成交的真实委托。仍要断开吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
                return
        if self.connection:
            self.arm.setChecked(False)
            self.connection.stop()

    def details(self):
        if self.connection:
            ConnectionDialog(self.connection, self).exec()

    def export_trades(self):
        filename, _ = QFileDialog.getSaveFileName(self, "导出交易账户成交", "实时账户成交.csv", "CSV (*.csv)")
        if filename:
            try:
                rows = self.latest.get("trades", [])
                columns = ["trade_id", "order_id", "exchange_id", "instrument_id", "direction", "offset", "volume", "price", "trade_date_time"]
                with Path(filename).open("w", encoding="utf-8-sig", newline="") as stream:
                    writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore")
                    writer.writeheader(); writer.writerows(rows)
            except OSError as exc:
                self.status.setText(str(exc))

    def closeEvent(self, event):
        if self.arm.isChecked():
            self.arm.setChecked(False)
        super().closeEvent(event)

    def hideEvent(self, event):
        if self.arm.isChecked():
            self.arm.setChecked(False)
        super().hideEvent(event)

    def shutdown(self):
        if self.connection:
            self.connection._send(dict(command="arm", enabled=False))
            self.connection.shutdown()
            self.connection.configuration.pop("broker_password", None)
            self.connection.configuration.pop("broker_account", None)
        self.fund_password.clear()
