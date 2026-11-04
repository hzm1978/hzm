from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
import random
import sqlite3

import pandas as pd
from PySide6.QtCore import QDateTime, QSignalBlocker, Qt, QTimer
from PySide6.QtGui import QAction, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFileDialog, QFrame, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMessageBox, QDateTimeEdit,
    QPushButton, QSlider, QSpinBox, QDoubleSpinBox, QSplitter, QTabWidget,
    QTableWidget, QVBoxLayout, QWidget, QSizePolicy, QGridLayout,
)

from . import APP_NAME, __version__
from .bars import (PERIODS, aggregate_calendar, empty_bars, merge_bars, native_period,
                   now_beijing, records, validate_frame)
from .chart import Chart
from .connection import Connection
from .credentials import LocalLoginStore
from .demo import DEMO_SYMBOL, demo_bars
from .dialogs import (
    BacktestDialog, ConnectionDialog, FILL_COLUMNS, ImportDialog, LoginDialog,
    SessionDialog, SessionsDialog, export_fills, table_fill,
)
from .history_dialog import HistoryDialog
from .paper import Contract, PaperAccount
from .replay import Replay
from .storage import Store
from .statistics import StatisticsPanel
from .tasks import launch
from .technical.models import Drawing
from .technical.ui.trend_dialog import TrendIndicatorDialog
from .trade_dialog import TradeDialog

EXCHANGE_NAMES = {"SHFE": "上期所", "DCE": "大商所", "CZCE": "郑商所",
                  "CFFEX": "中金所", "INE": "能源中心", "GFEX": "广期所"}


def starter_catalog():
    names = [
        ("SHFE.ag", "沪银", 15, 1), ("SHFE.au", "沪金", 1000, .02),
        ("SHFE.sn", "沪锡", 1, 10), ("SHFE.cu", "沪铜", 5, 10),
        ("SHFE.rb", "螺纹钢", 10, 1), ("SHFE.al", "沪铝", 5, 5),
        ("DCE.jm", "焦煤", 60, .5), ("DCE.j", "焦炭", 100, .5),
        ("DCE.i", "铁矿石", 100, .5), ("DCE.m", "豆粕", 10, 1),
        ("DCE.p", "棕榈油", 10, 2), ("CZCE.SA", "纯碱", 20, 1),
        ("CZCE.MA", "甲醇", 10, 1), ("CZCE.FG", "玻璃", 20, 1),
        ("CZCE.SR", "白糖", 10, 1), ("CFFEX.IF", "沪深300", 300, .2),
        ("CFFEX.IH", "上证50", 300, .2), ("CFFEX.IM", "中证1000", 200, .2),
        ("INE.sc", "原油", 1000, .1), ("INE.ec", "集运欧线", 50, .1),
        ("GFEX.si", "工业硅", 5, 5), ("GFEX.lc", "碳酸锂", 1, 20),
    ]
    return [dict(symbol="KQ.m@"+symbol, name=name, multiplier=multiple, tick=tick,
                 exchange=symbol.split(".")[0], category="CONT") for symbol, name, multiple, tick in names]


def button(text, callback, name=""):
    widget = QPushButton(text)
    if name:
        widget.setObjectName(name)
    widget.clicked.connect(callback)
    return widget


class LiveWindow(QDialog):
    def __init__(self, owner):
        super().__init__(owner, Qt.WindowType.Window |
            Qt.WindowType.WindowTitleHint | Qt.WindowType.WindowSystemMenuHint |
            Qt.WindowType.WindowMinMaxButtonsHint | Qt.WindowType.WindowCloseButtonHint)
        self.owner = owner
        self.setWindowTitle(f"实时看盘 · {APP_NAME} {__version__}")
        self.resize(1240, 800)
        layout = QVBoxLayout(self)
        self.title = QLabel(); self.title.setObjectName("Brand"); layout.addWidget(self.title)
        row = QHBoxLayout()
        self.period = QComboBox(); self.period.addItems(PERIODS); self.period.setCurrentText("日线")
        row.addWidget(QLabel("周期")); row.addWidget(self.period)
        self.trend = QCheckBox("趋势交易"); row.addWidget(self.trend)
        self.volume = QCheckBox("成交量"); self.volume.setChecked(owner.store.setting("live_volume", False))
        row.addWidget(self.volume)
        row.addWidget(button("指标明细", self.details))
        row.addWidget(button("重新连接", owner.login))
        row.addWidget(button("连接详情", owner.connection_details))
        row.addStretch(); layout.addLayout(row)
        self.status = QLabel(); self.status.setWordWrap(True); layout.addWidget(self.status)
        self.chart = Chart(); self.chart.set_ma((5, 10, 20, 60), False)
        self.chart.set_volume_visible(self.volume.isChecked()); layout.addWidget(self.chart, 1)
        note = QLabel("自动跟随当前品种；复盘位置与模拟账户保持原位。未结束K线仅作预览。")
        note.setObjectName("Muted"); layout.addWidget(note)
        self.period.currentTextChanged.connect(self._period_changed)
        self.trend.toggled.connect(self.chart.set_trend_visible)
        self.volume.toggled.connect(self._volume_changed)
        owner.connection.changed.connect(self.refresh_status)

    def _period_changed(self):
        self.owner.watch_current()
        self.owner.load_cache()
        self.refresh()

    def _volume_changed(self, value):
        self.chart.set_volume_visible(value)
        self.owner.store.set_setting("live_volume", bool(value))

    def refresh_status(self):
        self._update_status()

    def refresh(self):
        symbol = self.owner.symbol
        label = self.owner.catalog.get(symbol, {}).get("name", symbol)
        self.title.setText(f"{label}  ·  {symbol}")
        frame = self.owner.current_frame(self.period.currentText())
        self.chart.show_frame(frame.tail(10000).reset_index(drop=True))
        self._latest = str(frame.iloc[-1]["datetime"]) if len(frame) else "暂无K线"
        self._update_status()

    def _update_status(self):
        symbol = self.owner.symbol
        latest = getattr(self, "_latest", "暂无K线")
        source = "演示行情" if symbol.startswith("DEMO.") else "导入历史" if symbol.startswith("LOCAL.") else "接口／本地缓存"
        quote = self.owner.quote
        quote_text = f"报价：{quote.get('quote_time') or '尚未取得'}  最新价：{quote.get('last_price', '—')}"
        self.status.setText(f"{source}  ·  K线截至 {latest}  ·  {quote_text}\n{self.owner.connection.summary()}")

    def details(self):
        dialog = TrendIndicatorDialog(self.chart, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.chart.set_trend_settings(dialog.result_settings); self.trend.setChecked(True)

    def closeEvent(self, event):
        self.owner.live_action.setChecked(False)
        super().closeEvent(event)


class MainWindow(QMainWindow):
    def __init__(self, database, *, connection=None, auto_connect=True):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} v{__version__} · TqSdk")
        self.resize(1600, 1000)
        self.store = Store(database)
        self.connection = connection or Connection(database, self)
        self.catalog = {x["symbol"]: x for x in starter_catalog()}
        self.catalog.update({x["symbol"]: x for x in self.store.load_catalog()})
        self.symbol = self.store.setting("symbol", "KQ.m@SHFE.ag")
        if self.symbol not in self.catalog:
            self.symbol = "KQ.m@SHFE.ag"
        self.buffers = {}
        self.quote = {}
        self.replay = Replay()
        self.account = PaperAccount()
        self.session_id = None
        self.live = None
        self.trade = None
        self._tasks = {}
        self._cache_token = 0
        self._pending_initial = True
        self._closing = False
        self._selecting = False
        self._seeking_target = None
        self._active_chart = None
        self._workspace_windows = []
        self._save_timer = QTimer(self); self._save_timer.setSingleShot(True); self._save_timer.setInterval(1500)
        self._save_timer.timeout.connect(self.save)
        self._play_timer = QTimer(self); self._play_timer.setInterval(500)
        self._play_timer.timeout.connect(lambda:self.seek_index(self.replay.cursor+1))
        self._build()
        self.connection.message.connect(self.receive)
        self.connection.changed.connect(self.connection_changed)
        self._restore_ui()
        self.populate_catalog()
        self._selecting = True
        last = self.store.setting("last_session")
        if last:
            try:
                self.restore_training(self.store.load_session(last))
            except (ValueError, OSError, KeyError, TypeError):
                pass
        self._selecting = False
        self.watch_current(); self.load_cache()
        self.render()
        if auto_connect:
            credentials = LocalLoginStore().load()
            if credentials:
                QTimer.singleShot(150, lambda:self.connection.connect_account(*credentials))

    def _build(self):
        file_menu = self.menuBar().addMenu("文件")
        for title, action in (("导入历史 CSV / Parquet", self.import_data),
                              ("从天勤按日期载入历史", self.load_api_history),
                              ("导出整个工作区存档", self.backup_workspace),
                              ("打开工作区存档", self.open_workspace),
                              ("保存训练", self.save), ("训练记录", self.open_sessions),
                              ("导出成交 CSV", lambda:export_fills(self, self.account.fills)),
                              ("保存图表截图", self.screenshot)):
            entry = file_menu.addAction(title); entry.triggered.connect(action)
        market_menu = self.menuBar().addMenu("行情")
        market_menu.addAction("连接／重新连接天勤", self.login)
        market_menu.addAction("天勤账号设置", lambda:self.login(edit=True))
        market_menu.addAction("断开行情连接", lambda:self.connection.stop())
        market_menu.addAction("连接详情", self.connection_details)
        market_menu.addAction("演示行情（构造数据）", self.open_demo)
        market_menu.addAction("实时模拟／徽商实盘交易", self.open_trading)
        self.live_action = QAction("实时看盘", self); self.live_action.setCheckable(True)
        self.live_action.toggled.connect(self.toggle_live); market_menu.addAction(self.live_action)
        help_menu = self.menuBar().addMenu("帮助")
        help_menu.addAction("使用说明", self.help)
        central = QWidget(); layout = QVBoxLayout(central); layout.setContentsMargins(14, 10, 14, 8)
        header = QHBoxLayout()
        brand = QVBoxLayout()
        eyebrow = QLabel("TQ REPLAY STUDIO  /  01"); eyebrow.setObjectName("Eyebrow")
        title = QLabel(APP_NAME); title.setObjectName("Brand")
        brand.addWidget(eyebrow); brand.addWidget(title)
        header.addLayout(brand); header.addStretch()
        header.addWidget(button("导入历史", self.import_data))
        header.addWidget(button("天勤历史", self.load_api_history))
        header.addWidget(button("演示行情", self.open_demo))
        self.connect_button = button("连接天勤", self.login, "Primary"); header.addWidget(self.connect_button)
        self.live_button = button("实时看盘 ↗", lambda:self.live_action.setChecked(True))
        header.addWidget(self.live_button); layout.addLayout(header)
        header.addWidget(button("实时模拟／实盘", self.open_trading, "Primary"))
        split = QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(self._instrument_panel())
        split.addWidget(self._chart_panel())
        split.addWidget(self._trade_panel())
        split.setSizes([230, 1030, 305]); split.setStretchFactor(1, 1); layout.addWidget(split, 1)
        self.setCentralWidget(central)
        self.connection_label = QLabel(self.connection.summary())
        self.connection_label.setMinimumWidth(0)
        self.connection_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.connection_label.setToolTip("点击“行情 → 连接详情”查看具体连接阶段和错误。")
        self.statusBar().addWidget(self.connection_label, 1)
        self.message_label = QLabel("行情由天勤接口订阅；本机模拟训练")
        self.message_label.setMaximumWidth(500)
        self.statusBar().addPermanentWidget(self.message_label)
        self._shortcut("Space", self.toggle_play)
        self._shortcut("Right", lambda:self.seek_index(self.replay.cursor+1))
        self._shortcut("Left", lambda:self.seek_index(self.replay.cursor-1))
        self._shortcut("Ctrl+S", self.save)

    def _shortcut(self, key, callback):
        shortcut = QShortcut(QKeySequence(key), self); shortcut.activated.connect(callback)

    def _instrument_panel(self):
        panel = QWidget(); layout = QVBoxLayout(panel); layout.setContentsMargins(0, 5, 9, 0)
        top = QLabel("市场与品种"); top.setObjectName("Brand"); layout.addWidget(top)
        self.search = QLineEdit(); self.search.setPlaceholderText("搜索代码、名称或合约…")
        self.search.textChanged.connect(self.populate_catalog); layout.addWidget(self.search)
        self.exchange = QComboBox(); self.exchange.addItem("全部交易所", "")
        for key, name in EXCHANGE_NAMES.items():
            self.exchange.addItem(name, key)
        self.exchange.currentIndexChanged.connect(self.populate_catalog); layout.addWidget(self.exchange)
        self.category = QComboBox()
        for title, data in (("主力连续", "CONT"), ("实际期货合约", "FUTURE"), ("本地／演示", "LOCAL"), ("全部类型", "")):
            self.category.addItem(title, data)
        self.category.currentIndexChanged.connect(self.populate_catalog); layout.addWidget(self.category)
        self.instruments = QListWidget(); self.instruments.setObjectName("instruments")
        self.instruments.currentItemChanged.connect(self._item_changed)
        layout.addWidget(self.instruments, 1)
        layout.addWidget(button("随机主连品种", self.random_product))
        self.catalog_count = QLabel(); self.catalog_count.setObjectName("Muted"); layout.addWidget(self.catalog_count)
        note = QLabel("连接后从接口读取完整目录。\n切换品种自动保存当前训练。")
        note.setObjectName("Muted"); layout.addWidget(note)
        return panel

    def _chart_panel(self):
        panel = QWidget(); layout = QVBoxLayout(panel); layout.setContentsMargins(3, 0, 3, 0)
        row = QHBoxLayout()
        self.instrument_title = QLabel("请选择品种"); self.instrument_title.setObjectName("Brand")
        row.addWidget(self.instrument_title); row.addStretch()
        self.period = QComboBox(); self.period.addItems(PERIODS); self.period.setCurrentText("日线")
        self.period.currentTextChanged.connect(self.change_period)
        row.addWidget(self.period)
        row.addWidget(button("新建复盘", self.new_training, "Primary"))
        row.addWidget(button("训练记录", self.open_sessions))
        layout.addLayout(row)
        row = QHBoxLayout()
        self.ma = QCheckBox("均线"); self.ma.setChecked(True)
        self.ma_periods = QLineEdit("5,10,20,60"); self.ma_periods.setMaximumWidth(100)
        self.ma_periods.setToolTip("均线周期，逗号分隔；最多8条")
        self.ma_periods.editingFinished.connect(self.chart_options)
        self.boll = QCheckBox("BOLL")
        self.trend = QCheckBox("趋势交易"); self.trend.setChecked(True)
        self.volume = QCheckBox("成交量"); self.volume.setChecked(True)
        self.companion = QCheckBox("高周期"); self.companion.setChecked(True)
        self.companion.toggled.connect(self.chart_options)
        for widget in (self.ma, self.boll, self.trend, self.volume):
            row.addWidget(widget); widget.toggled.connect(self.chart_options)
        row.addWidget(self.ma_periods)
        self.layout_mode = QComboBox(); self.layout_mode.addItems(["单图", "双图", "四图"])
        self.layout_mode.setCurrentIndex(1); row.addWidget(self.layout_mode)
        row.addWidget(button("指标明细", self.trend_details))
        self.indicator = QComboBox(); self.indicator.addItems(["无", "MACD", "KDJ", "RSI", "CCI", "WR"])
        self.indicator.currentTextChanged.connect(self.chart_options)
        row.addWidget(self.indicator); row.addStretch(); layout.addLayout(row)
        row = QHBoxLayout()
        self.tool = QComboBox(); self.tool.addItems(["光标", "趋势线", "射线", "水平线", "矩形", "黄金分割", "波浪尺"])
        self.tool.currentTextChanged.connect(lambda value:self.active_chart().set_tool(value))
        row.addWidget(QLabel("画线")); row.addWidget(self.tool)
        row.addWidget(button("清除画线", lambda:self.active_chart().clear_drawings()))
        row.addWidget(button("保存截图", self.screenshot))
        self.time_jump = QDateTimeEdit(); self.time_jump.setCalendarPopup(True)
        self.time_jump.setDisplayFormat("yyyy-MM-dd HH:mm"); self.time_jump.setMaximumWidth(175)
        self.time_jump.editingFinished.connect(self.seek_time)
        row.addWidget(QLabel("定位")); row.addWidget(self.time_jump)
        row.addStretch(); row.addWidget(button("策略回测", self.backtest))
        layout.addLayout(row)
        self.replay_status = QLabel("连接行情后自动缓存。点击新建复盘选择起点，或先体验演示。")
        self.replay_status.setWordWrap(True); self.replay_status.setObjectName("Muted"); layout.addWidget(self.replay_status)
        self.charts = QWidget()
        self.chart_grid = QGridLayout(self.charts); self.chart_grid.setContentsMargins(0, 0, 0, 0); self.chart_grid.setSpacing(4)
        self.primary = Chart(); self.primary.setObjectName("primary_chart")
        self.primary.activated.connect(lambda:self._activate(self.primary))
        self.primary.drawingChanged.connect(self.schedule_save)
        self.primary.tradeLineChanged.connect(self.trade_line_changed)
        self.primary.tradeLineDeleteRequested.connect(self.trade_line_deleted)
        self.high_panel = QWidget(); high_layout = QVBoxLayout(self.high_panel); high_layout.setContentsMargins(0, 0, 0, 0)
        high_row = QHBoxLayout()
        self.high_title = QLabel("副图 · 已揭示K线合成"); self.high_title.setObjectName("Muted")
        self.high_period = QComboBox(); high_row.addWidget(self.high_title); high_row.addWidget(self.high_period)
        high_layout.addLayout(high_row)
        self.higher = Chart(); self.higher.set_volume_visible(False); self.higher.set_ma((5, 10, 20), True)
        self.higher.activated.connect(lambda:self._activate(self.higher))
        self.higher.drawingChanged.connect(self.schedule_save); high_layout.addWidget(self.higher)
        self.extra_views = []
        for _ in range(2):
            view_panel = QWidget(); view_layout = QVBoxLayout(view_panel); view_layout.setContentsMargins(0, 0, 0, 0)
            line = QHBoxLayout(); line.addWidget(QLabel("副图 · 已揭示K线合成"))
            combo = QComboBox(); line.addWidget(combo); view_layout.addLayout(line)
            chart = Chart(); chart.set_volume_visible(False)
            chart.activated.connect(lambda selected=chart:self._activate(selected))
            chart.drawingChanged.connect(self.schedule_save); view_layout.addWidget(chart, 1)
            self.extra_views.append((view_panel, combo, chart))
            combo.currentTextChanged.connect(self.render_secondary)
        self.high_period.currentTextChanged.connect(self.render_secondary)
        self.layout_mode.currentIndexChanged.connect(self.set_chart_layout)
        self.secondary_options(); self.set_chart_layout()
        layout.addWidget(self.charts, 1)
        controls = QHBoxLayout()
        controls.addWidget(button("− 1日", lambda:self.seek_index(self.replay.day_index(-1))))
        controls.addWidget(button("◀ 1步", lambda:self.seek_index(self.replay.cursor-1)))
        self.play_button = button("播放", self.toggle_play, "Primary"); controls.addWidget(self.play_button)
        controls.addWidget(button("1步 ▶", lambda:self.seek_index(self.replay.cursor+1)))
        controls.addWidget(button("1日 +", lambda:self.seek_index(self.replay.day_index(1))))
        self.speed = QComboBox()
        for title, interval in (("0.25倍", 2000), ("0.5倍", 1000), ("1倍", 500), ("2倍", 250), ("5倍", 100), ("10倍", 50)):
            self.speed.addItem(title, interval)
        self.speed.setCurrentIndex(2); self.speed.currentIndexChanged.connect(lambda:self._play_timer.setInterval(self.speed.currentData()))
        controls.addWidget(self.speed); controls.addWidget(button("随机复盘", self.random_training))
        layout.addLayout(controls)
        self.slider = QSlider(Qt.Orientation.Horizontal); self.slider.setEnabled(False)
        self.slider.sliderReleased.connect(lambda:self.seek_index(self.slider.value()))
        self.slider.valueChanged.connect(lambda value:self.seek_index(value) if not self.slider.isSliderDown() else None)
        layout.addWidget(self.slider)
        self.chart_options()
        return panel

    def _trade_panel(self):
        panel = QWidget(); layout = QVBoxLayout(panel); layout.setContentsMargins(9, 5, 0, 0)
        label = QLabel("模拟训练账户"); label.setObjectName("Brand"); layout.addWidget(label)
        self.equity_label = QLabel("1,000,000.00"); self.equity_label.setObjectName("Metric"); layout.addWidget(self.equity_label)
        self.account_summary = QLabel("暂无持仓"); self.account_summary.setWordWrap(True); layout.addWidget(self.account_summary)
        self.position_label = QLabel("空仓"); self.position_label.setObjectName("Eyebrow"); layout.addWidget(self.position_label)
        row = QHBoxLayout(); row.addWidget(QLabel("手数"))
        self.quantity = QSpinBox(); self.quantity.setRange(1, 10000); row.addWidget(self.quantity); layout.addLayout(row)
        self.limit_mode = QCheckBox("限价委托（从下一根开始撮合）"); layout.addWidget(self.limit_mode)
        self.order_price = QDoubleSpinBox(); self.order_price.setRange(.0001, 1_000_000_000); self.order_price.setDecimals(4)
        self.order_price.setValue(10000); self.order_price.setEnabled(False)
        self.limit_mode.toggled.connect(self.order_price.setEnabled); layout.addWidget(self.order_price)
        row = QHBoxLayout()
        self.buy_button = button("买入开多", lambda:self.order("开多"), "Buy")
        self.sell_button = button("卖出开空", lambda:self.order("开空"), "Sell")
        row.addWidget(self.buy_button); row.addWidget(self.sell_button); layout.addLayout(row)
        row = QHBoxLayout()
        row.addWidget(button("卖出平多", lambda:self.order("平多")))
        row.addWidget(button("买入平空", lambda:self.order("平空"))); layout.addLayout(row)
        row = QHBoxLayout()
        row.addWidget(button("全部平仓", self.flatten))
        row.addWidget(button("撤销挂单", self.cancel_orders)); layout.addLayout(row)
        row = QHBoxLayout()
        self.take = QDoubleSpinBox(); self.stop = QDoubleSpinBox()
        for spin, label in ((self.take, "止盈"), (self.stop, "止损")):
            spin.setRange(0, 1_000_000_000); spin.setDecimals(4); spin.setSpecialValueText("未设置")
            column = QVBoxLayout(); column.addWidget(QLabel(label)); column.addWidget(spin); row.addLayout(column)
        layout.addLayout(row)
        layout.addWidget(button("应用止盈／止损", self.bracket))
        self.trade_note = QLabel("按已知收盘价模拟市价成交。\n一根内同时触发止盈止损时，先计止损。\n当前主连可训练；不会发送真实委托。")
        self.trade_note.setWordWrap(True); self.trade_note.setObjectName("Muted"); layout.addWidget(self.trade_note)
        self.tabs = QTabWidget()
        self.fills = QTableWidget(); self.orders = QTableWidget()
        self.tabs.addTab(self.fills, "成交"); self.tabs.addTab(self.orders, "委托"); layout.addWidget(self.tabs, 1)
        self.statistics = StatisticsPanel(); self.tabs.addTab(self.statistics, "统计")
        layout.addWidget(button("导出成交记录", lambda:export_fills(self, self.account.fills)))
        return panel

    def active_chart(self):
        if self._active_chart is not None and not self._active_chart.isVisible():
            return self.primary
        return self._active_chart or self.primary

    def _activate(self, chart):
        self._active_chart = chart
        chart.set_tool(self.tool.currentText())
        for box, value in ((self.ma, chart.show_ma), (self.boll, chart.show_boll),
                           (self.trend, chart.show_trend), (self.volume, chart.show_volume)):
            with QSignalBlocker(box):
                box.setChecked(value)
        self.ma_periods.setText(",".join(str(x) for x in chart.ma_periods))
        with QSignalBlocker(self.indicator):
            self.indicator.setCurrentText(chart.indicator)

    def chart_options(self, *_):
        if not hasattr(self, "primary"):
            return
        chart = self.active_chart()
        try:
            periods = tuple(int(x.strip()) for x in self.ma_periods.text().split(",") if x.strip())
            if not periods or len(periods) > 8 or any(not 1 <= x <= 1000 for x in periods):
                raise ValueError()
        except ValueError:
            if hasattr(self, "message_label"):
                self.notify("均线周期请填写1至1000的整数，逗号分隔，最多8条")
            return
        chart.set_ma(periods, self.ma.isChecked())
        chart.set_boll_visible(self.boll.isChecked())
        chart.set_trend_visible(self.trend.isChecked())
        chart.set_volume_visible(self.volume.isChecked())
        chart.set_indicator(self.indicator.currentText())
        self.set_chart_layout()
        self.schedule_save()

    def set_chart_layout(self, *_):
        if not hasattr(self, "extra_views"):
            return
        for widget in (self.primary, self.high_panel, *[x[0] for x in self.extra_views]):
            self.chart_grid.removeWidget(widget)
        index = self.layout_mode.currentIndex()
        with QSignalBlocker(self.companion):
            self.companion.setChecked(index > 0)
        if index == 2:
            self.chart_grid.addWidget(self.primary, 0, 0)
            self.chart_grid.addWidget(self.high_panel, 0, 1)
            for column, (panel, _, _) in enumerate(self.extra_views):
                self.chart_grid.addWidget(panel, 1, column)
        else:
            self.chart_grid.addWidget(self.primary, 0, 0, 1, 2)
            self.chart_grid.addWidget(self.high_panel, 1, 0, 1, 2)
        self.high_panel.setVisible(index > 0)
        for panel, _, _ in self.extra_views:
            panel.setVisible(index == 2)
        for row in (0, 1):
            self.chart_grid.setRowStretch(row, 1 if index == 2 or row == 0 else 0)
        self.schedule_save()

    def secondary_options(self):
        base = native_period(self.period.currentText())
        allowed = [key for key in PERIODS if native_period(key) >= base and native_period(key) % base == 0]
        preferred = ("周线", "日线", "月线") if base == 86400 else ("日线", "15分钟", "1小时")
        combos = [self.high_period, *[x[1] for x in self.extra_views]]
        for combo, default in zip(combos, preferred):
            previous = combo.currentText()
            with QSignalBlocker(combo):
                combo.clear(); combo.addItems(allowed)
                combo.setCurrentText(previous if previous in allowed else default if default in allowed else allowed[-1])

    def render_secondary(self, *_):
        if not hasattr(self, "extra_views"):
            return
        for combo, chart in [(self.high_period, self.higher), *[(x[1], x[2]) for x in self.extra_views]]:
            try:
                frame = self.replay.project(combo.currentText())
            except (KeyError, ValueError):
                frame = empty_bars()
            chart.show_frame(frame)
        self.schedule_save()

    def _restore_ui(self):
        preferences = self.store.setting("ui", {})
        for key in ("ma", "boll", "trend", "volume", "companion"):
            if key in preferences:
                getattr(self, key).setChecked(bool(preferences[key]))
        if preferences.get("period") in PERIODS:
            with QSignalBlocker(self.period):
                self.period.setCurrentText(preferences["period"])
        self.primary.set_trend_settings(preferences.get("trend_settings", {}))
        self.secondary_options()
        self.layout_mode.setCurrentIndex(int(preferences.get("chart_layout", 1)))
        for combo, value in zip([self.high_period, *[x[1] for x in self.extra_views]], preferences.get("view_periods", [])):
            if combo.findText(value) >= 0:
                combo.setCurrentText(value)
        for chart, options in zip(self.all_charts(), preferences.get("chart_options", [])):
            chart.set_ma(tuple(options.get("ma_periods", (5, 10, 20, 60))), options.get("ma", True))
            chart.set_boll_visible(options.get("boll", False)); chart.set_trend_visible(options.get("trend", False))
            chart.set_volume_visible(options.get("volume", True)); chart.set_indicator(options.get("indicator", "无"))
            chart.set_trend_settings(options.get("trend_settings", {}))
        self._activate(self.primary)

    def all_charts(self):
        return [self.primary, self.higher, *[x[2] for x in self.extra_views]]

    def populate_catalog(self, *_):
        if not hasattr(self, "instruments"):
            return
        search = self.search.text().strip().lower()
        exchange = self.exchange.currentData(); category = self.category.currentData()
        with QSignalBlocker(self.instruments):
            self.instruments.clear()
            selected = None
            entries = sorted(self.catalog.values(), key=lambda row:(not row["symbol"].startswith("KQ.m@"), row["symbol"]))
            shown = 0
            for row in entries:
                local = row["symbol"].startswith(("LOCAL.", "DEMO."))
                if exchange and row.get("exchange") != exchange:
                    continue
                if category and ((category == "LOCAL" and not local) or
                                 (category != "LOCAL" and row.get("category") != category)):
                    continue
                if search and search not in (row["symbol"]+" "+row.get("name", "")).lower():
                    continue
                item = QListWidgetItem(row.get("name", row["symbol"])+"\n"+row["symbol"])
                item.setData(Qt.ItemDataRole.UserRole, row["symbol"])
                self.instruments.addItem(item); shown += 1
                if row["symbol"] == self.symbol:
                    selected = item
            if selected:
                self.instruments.setCurrentItem(selected)
        self.catalog_count.setText(f"{shown} 个合约  /  目录 {len(self.catalog)}")

    def _item_changed(self, item, *_):
        if item is not None and not self._selecting:
            self.select_symbol(item.data(Qt.ItemDataRole.UserRole))

    def select_symbol(self, symbol):
        if self._selecting or symbol == self.symbol:
            return
        self.save(); self.pause()
        self._seeking_target = None
        self.symbol = symbol
        self.session_id = None; self.buffers = {}; self.quote = {}
        self.replay = Replay(); self.account = PaperAccount(contract=self.contract_defaults())
        self.primary.clear_drawings(); self.higher.clear_drawings()
        self._pending_initial = True
        self.watch_current(); self.load_cache(); self.render()
        self.store.set_setting("symbol", self.symbol)

    def change_period(self, *_):
        if self._selecting:
            return
        self.save(); self.pause(); self._seeking_target = None
        self.session_id = None; self.replay = Replay()
        self.account = PaperAccount(contract=self.contract_defaults())
        self.secondary_options()
        self.primary.clear_drawings(); self.higher.clear_drawings(); self._pending_initial = True
        self.watch_current(); self.load_cache(); self.maybe_initial_training(); self.render()

    def watch_current(self):
        if self.trade is not None:
            self.trade.follow(self.symbol)
        if self.symbol.startswith(("LOCAL.", "DEMO.")):
            return
        periods = [native_period(self.period.currentText()), 86400]
        if self.live is not None and self.live.isVisible():
            periods.append(native_period(self.live.period.currentText()))
        self.connection.select(self.symbol, periods)

    def load_cache(self):
        self._cache_token += 1
        token, symbol = self._cache_token, self.symbol
        seconds = {native_period(self.period.currentText()), 86400}
        if self.live is not None and self.live.isVisible():
            seconds.add(native_period(self.live.period.currentText()))
        key = ("cache", token)
        def load():
            return {duration:self.store.load_bars(symbol, duration) for duration in seconds}
        def done(result):
            self._tasks.pop(key, None)
            if self._closing or token != self._cache_token or symbol != self.symbol:
                return
            for duration, frame in result.items():
                self.buffers[duration] = merge_bars(frame, self.buffers.get(duration))
            self.maybe_initial_training()
            self.render_live()
        def failed(error):
            self._tasks.pop(key, None)
            if token == self._cache_token:
                self.notify("读取缓存失败："+error)
        self._tasks[key] = launch(load, done, failed)

    def current_frame(self, period=None):
        period = period or self.period.currentText()
        frame = self.buffers.get(native_period(period), empty_bars())
        return aggregate_calendar(frame, period) if period in ("周线", "月线") else frame.copy(deep=True)

    def training_frame(self):
        # Weekly/monthly charts still replay the native DAILY prefix one day
        # at a time. Aggregating the entire history here would leak later days.
        return self.buffers.get(native_period(self.period.currentText()), empty_bars()).copy(deep=True)

    def receive(self, message):
        kind = message.get("kind")
        if kind == "catalog":
            self.catalog.update({x["symbol"]: x for x in message.get("instruments", [])})
            self.populate_catalog()
        elif kind == "bars" and message.get("symbol") == self.symbol and message.get("revision") == self.connection.revision:
            frame = validate_frame(pd.DataFrame(message.get("bars", [])))
            duration = int(message["seconds"])
            self.buffers[duration] = merge_bars(self.buffers.get(duration), frame)
            self.quote = {key:message.get(key) for key in ("quote_time", "last_price", "underlying", "multiplier", "tick")}
            self.maybe_initial_training()
            self.render_live()
        elif kind == "heartbeat" and message.get("symbol") == self.symbol:
            self.quote.update(quote_time=message.get("quote_time"), last_price=message.get("last_price"))
        elif kind == "view_error":
            self.notify(message.get("text", "行情暂未取得"))

    def maybe_initial_training(self):
        if self._pending_initial and not self._closing:
            frame = self.training_frame()
            if not frame.empty:
                self.create_training(frame)

    def contract_defaults(self):
        row = self.catalog.get(self.symbol, {})
        return Contract(multiplier=float(row.get("multiplier") or 10), tick=float(row.get("tick") or 1))

    def create_training(self, frame, cursor=None, capital=1_000_000, contract=None):
        self.pause(); self._seeking_target = None
        self.replay.load(frame, self.period.currentText(), cursor)
        self.account = PaperAccount(capital, contract or self.contract_defaults())
        if self.replay.current is not None:
            self.account.last_bar = str(self.replay.current["datetime"])
        self.primary.clear_drawings(); self.higher.clear_drawings()
        for _, _, chart in self.extra_views:
            chart.clear_drawings()
        self.secondary_options()
        self._pending_initial = False
        self.session_id = self.store.new_session(self.symbol, self.replay.period, self.replay.frame,
                                                self.replay.cursor, self.training_state())
        self.store.set_setting("last_session", self.session_id)
        self.render()

    def new_training(self):
        frame = self.training_frame()
        if frame.empty:
            self.notify("该周期暂无缓存。请连接天勤、切换有数据的周期，或导入历史。"); return
        dialog = SessionDialog(frame, self.contract_defaults(), self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.save()
            index = max(0, int(frame.datetime.searchsorted(pd.Timestamp(dialog.time.dateTime().toPython()), side="right")-1))
            self.create_training(frame, index, dialog.capital.value(), dialog.contract())

    def random_training(self):
        frame = self.training_frame()
        if frame.empty:
            self.notify("暂无可复盘数据"); return
        self.save()
        self.create_training(frame, random.randrange(min(50, len(frame)), max(min(50, len(frame))+1, len(frame)-30)))

    def random_product(self):
        choices = [x["symbol"] for x in self.catalog.values()
                   if x.get("category") == "CONT" and x["symbol"] != self.symbol]
        if choices:
            self.category.setCurrentIndex(0); self.exchange.setCurrentIndex(0); self.search.clear()
            self.select_symbol(random.choice(choices)); self.populate_catalog()

    def seek_time(self):
        if not self.replay.frame.empty:
            self.seek_index(self.replay.seek(pd.Timestamp(self.time_jump.dateTime().toPython())))

    def restore_training(self, state):
        self.pause()
        self.symbol = state["symbol"]
        with QSignalBlocker(self.period):
            self.period.setCurrentText(state["period"])
        self.replay.load(state["frame"], state["period"], state["cursor"])
        self.account = PaperAccount.from_dict(state["state"]["account"])
        self.session_id = state["id"]; self._pending_initial = False
        if "chart_layout" in state["state"]:
            self.layout_mode.setCurrentIndex(int(state["state"]["chart_layout"]))
        self.primary.drawings = [Drawing.from_dict(x) for x in state["state"].get("primary_drawings", [])]
        self.higher.drawings = [Drawing.from_dict(x) for x in state["state"].get("higher_drawings", [])]
        self.secondary_options()
        for combo, value in zip([self.high_period, *[x[1] for x in self.extra_views]], state["state"].get("view_periods", [])):
            if combo.findText(value) >= 0:
                with QSignalBlocker(combo):
                    combo.setCurrentText(value)
        for chart, options in zip(self.all_charts(), state["state"].get("view_options", [])):
            chart.set_ma(tuple(options.get("ma_periods", (5, 10, 20, 60))), options.get("ma", True))
            chart.set_boll_visible(options.get("boll", False)); chart.set_trend_visible(options.get("trend", False))
            chart.set_volume_visible(options.get("volume", True)); chart.set_indicator(options.get("indicator", "无"))
            chart.set_trend_settings(options.get("trend_settings", {}))
        for (_, _, chart), values in zip(self.extra_views, state["state"].get("extra_drawings", [[], []])):
            chart.drawings = [Drawing.from_dict(x) for x in values]
        self.buffers = {native_period(state["period"]):state["frame"].copy()}
        self.quote = {}
        self.store.set_setting("last_session", self.session_id)
        self._activate(self.primary)
        self.populate_catalog(); self.render()

    def open_sessions(self):
        dialog = SessionsDialog(self.store.list_sessions(), self)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.identifier():
            self.save()
            self.restore_training(self.store.load_session(dialog.identifier()))
            self.watch_current(); self.load_cache()

    def training_state(self):
        return dict(account=self.account.to_dict(),
                    primary_drawings=[x.to_dict() for x in self.primary.drawings],
                    higher_drawings=[x.to_dict() for x in self.higher.drawings],
                    extra_drawings=[[x.to_dict() for x in chart.drawings] for _, _, chart in self.extra_views],
                    chart_layout=self.layout_mode.currentIndex(),
                    view_periods=[self.high_period.currentText(), *[x[1].currentText() for x in self.extra_views]],
                    view_options=[dict(ma=chart.show_ma, ma_periods=list(chart.ma_periods),
                        boll=chart.show_boll, trend=chart.show_trend, volume=chart.show_volume,
                        indicator=chart.indicator, trend_settings=chart.trend_settings) for chart in self.all_charts()])

    def schedule_save(self, *_):
        if not self._closing and not self._save_timer.isActive():
            self._save_timer.start()

    def save(self):
        self._save_timer.stop()
        try:
            if self.session_id:
                self.store.save_session(self.session_id, self.replay.cursor, self.training_state())
            ui = {name:getattr(self, name).isChecked() for name in ("ma", "boll", "trend", "volume", "companion")}
            ui.update(period=self.period.currentText(), trend_settings=self.primary.trend_settings)
            ui.update(chart_layout=self.layout_mode.currentIndex(),
                      view_periods=[self.high_period.currentText(), *[x[1].currentText() for x in self.extra_views]])
            ui["chart_options"] = [dict(ma=chart.show_ma, ma_periods=list(chart.ma_periods),
                boll=chart.show_boll, trend=chart.show_trend, volume=chart.show_volume, indicator=chart.indicator,
                trend_settings=chart.trend_settings) for chart in self.all_charts()]
            self.store.set_setting("ui", ui); self.store.set_setting("symbol", self.symbol)
        except (OSError, ValueError, sqlite3.Error) as exc:
            self.notify("保存失败："+str(exc))

    def seek_index(self, index):
        if self.replay.frame.empty or self._closing:
            return
        index = max(0, min(len(self.replay.frame)-1, int(index)))
        if index < self.replay.cursor:
            self.pause()
            if self.account.has_activity:
                self.notify("该训练已有委托或成交，不能回退。可新建训练从较早位置重来。")
                self.render(); return
            self.replay.cursor = index
            self.account = PaperAccount(self.account.initial, self.account.contract)
            self.account.last_bar = str(self.replay.current["datetime"])
            self.render(); return
        if self._seeking_target is not None:
            return
        self._seeking_target = index
        self._advance_chunk()

    def _advance_chunk(self):
        if self._seeking_target is None or self._closing:
            return
        end = min(self._seeking_target, self.replay.cursor+200)
        for index in range(self.replay.cursor+1, end+1):
            self.account.on_bar(self.replay.frame.iloc[index])
        self.replay.cursor = end
        if end < self._seeking_target:
            self.notify(f"正在顺序撮合 {end+1}/{self._seeking_target+1}")
            QTimer.singleShot(0, self._advance_chunk)
            return
        self._seeking_target = None
        if self.replay.at_end:
            self.pause()
        self.render()

    def toggle_play(self):
        if self._play_timer.isActive():
            self.pause()
        elif not self.replay.frame.empty and not self.replay.at_end:
            self._play_timer.start(); self.play_button.setText("暂停")

    def pause(self):
        self._play_timer.stop()
        if hasattr(self, "play_button"):
            self.play_button.setText("播放")

    def render(self):
        row = self.catalog.get(self.symbol, {})
        self.instrument_title.setText(row.get("name", self.symbol)+"  ·  "+self.period.currentText())
        prefix = self.replay.visible()
        self.primary.show_frame(prefix)
        self.render_secondary()
        if self.replay.current is None:
            self.replay_status.setText(f"{self.symbol} · 暂无该周期数据；连接天勤后自动加载缓存")
            mark = 0.
            self.time_jump.setEnabled(False)
        else:
            current = self.replay.current
            mark = float(current["close"])
            with QSignalBlocker(self.time_jump):
                self.time_jump.setEnabled(True)
                self.time_jump.setMinimumDateTime(QDateTime(self.replay.frame.iloc[0]["datetime"].to_pydatetime()))
                self.time_jump.setMaximumDateTime(QDateTime(self.replay.frame.iloc[-1]["datetime"].to_pydatetime()))
                self.time_jump.setDateTime(QDateTime(current["datetime"].to_pydatetime()))
            source = "【构造演示行情】 " if self.symbol.startswith("DEMO.") else "本地导入 · " if self.symbol.startswith("LOCAL.") else ""
            self.replay_status.setText(
                f"{source}复盘截至 {current['datetime']}   已揭示 {self.replay.cursor+1:,}/{len(self.replay.frame):,} 根\n"
                f"开 {current['open']:g}   高 {current['high']:g}   低 {current['low']:g}   收 {mark:g}   "
                "后台行情继续缓存；复盘位置固定")
            if not self.limit_mode.isChecked():
                self.order_price.setValue(round(mark/self.account.contract.tick)*self.account.contract.tick)
        with QSignalBlocker(self.slider):
            self.slider.setRange(0, max(0, len(self.replay.frame)-1))
            self.slider.setValue(max(0, self.replay.cursor)); self.slider.setEnabled(bool(len(prefix)))
        self.render_account(mark)
        self.schedule_save()

    def render_account(self, mark):
        account = self.account; metrics = account.metrics(mark)
        self.equity_label.setText(f"{metrics['equity']:,.2f}")
        self.account_summary.setText(
            f"可用 {metrics['available']:,.2f}\n保证金 {metrics['margin']:,.2f}\n"
            f"浮盈亏 {metrics['unrealized']:+,.2f}  已实现 {metrics['realized']:+,.2f}")
        self.position_label.setText("空仓" if not account.qty else f"{'多' if account.direction > 0 else '空'} {account.qty} 手  均价 {account.average:g}")
        self.primary.set_trade_lines(
            account.average if account.qty else None, account.take_profit, account.stop_loss,
            limits=[(f"#{x['id']} {x['action']}", x["price"]) for x in account.orders if x["status"] == "待成交"],
            direction=account.direction or None, qty=account.qty, multiplier=account.contract.multiplier)
        self.take.setValue(account.take_profit or 0); self.stop.setValue(account.stop_loss or 0)
        table_fill(self.fills, FILL_COLUMNS, account.fills[-300:])
        table_fill(self.orders, [("id", "#"), ("action", "方向"), ("qty", "手数"),
            ("price", "限价"), ("status", "状态"), ("message", "原因")], account.orders[-300:])
        self.statistics.refresh(account, mark)

    def order(self, action):
        if self.replay.current is None or self._seeking_target is not None:
            self.notify("请先载入训练数据，并等待跳转结束"); return
        row = self.replay.current
        try:
            if self.limit_mode.isChecked():
                self.account.submit_limit(action, self.quantity.value(), self.order_price.value(), row["datetime"])
            else:
                self.account.execute(action, self.quantity.value(), float(row["close"]), row["datetime"])
            self.render(); self.notify("模拟委托已记录")
        except ValueError as exc:
            self.notify(str(exc))

    def flatten(self):
        if self._seeking_target is not None:
            self.notify("请等待复盘跳转结束"); return
        if self.account.qty and self.replay.current is not None:
            try:
                self.account.execute("平多" if self.account.direction > 0 else "平空", self.account.qty,
                                     float(self.replay.current["close"]), self.replay.current["datetime"])
                self.render()
            except ValueError as exc:
                self.notify(str(exc))

    def cancel_orders(self):
        if self._seeking_target is not None:
            self.notify("请等待复盘跳转结束"); return
        for order in self.account.orders:
            self.account.cancel(order["id"])
        self.render()

    def bracket(self):
        if self._seeking_target is not None:
            self.notify("请等待复盘跳转结束"); return
        if self.replay.current is None:
            return
        try:
            self.account.set_bracket(self.take.value() or None, self.stop.value() or None, self.replay.current["close"])
            self.render()
        except ValueError as exc:
            self.notify(str(exc))

    def trade_line_changed(self, key, price):
        if key == "成本":
            return
        if key == "止盈":
            self.take.setValue(price)
        elif key == "止损":
            self.stop.setValue(price)
        self.bracket()

    def trade_line_deleted(self, key):
        if key == "止盈":
            self.take.setValue(0)
        elif key == "止损":
            self.stop.setValue(0)
        self.bracket()

    def trend_details(self):
        chart = self.active_chart()
        dialog = TrendIndicatorDialog(chart, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            chart.set_trend_settings(dialog.result_settings)
            self.trend.setChecked(True); chart.set_trend_visible(True); self.schedule_save()

    def login(self, edit=False):
        if self.connection._credentials and not edit:
            self.watch_current()
            self.connection.connect_account(*self.connection._credentials)
            return
        dialog = LoginDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.watch_current()
            self.connection.connect_account(dialog.username.text().strip(), dialog.password.text())

    def connection_details(self):
        ConnectionDialog(self.connection, self).exec()

    def connection_changed(self):
        self.connection_label.setText(self.connection.summary())
        self.connect_button.setText("重新连接" if self.connection.running else "连接天勤")

    def toggle_live(self, enabled):
        if enabled:
            if self.live is None:
                self.live = LiveWindow(self)
            self.live.show(); self.live.raise_(); self.watch_current(); self.load_cache(); self.render_live()
        elif self.live is not None:
            self.live.hide()
            self.watch_current()

    def render_live(self):
        if self.live is not None and self.live.isVisible():
            self.live.refresh()

    def open_demo(self):
        frame = demo_bars()
        entry = dict(symbol=DEMO_SYMBOL, name="沪银 · 构造演示", exchange="DEMO", category="LOCAL", multiplier=15, tick=1)
        self.catalog[DEMO_SYMBOL] = entry; self.store.save_catalog([entry])
        self.store.upsert_bars(DEMO_SYMBOL, 86400, frame)
        with QSignalBlocker(self.category):
            self.category.setCurrentIndex(2)
        self.search.clear(); self.exchange.setCurrentIndex(0); self.populate_catalog()
        if self.symbol != DEMO_SYMBOL:
            self.select_symbol(DEMO_SYMBOL)
        with QSignalBlocker(self.period):
            self.period.setCurrentText("日线")
        self.buffers = {86400:frame}; self.save()
        self.create_training(frame, len(frame)-130)
        self.render_live()

    def import_data(self):
        dialog = ImportDialog(self.period.currentText(), self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        filename, _ = QFileDialog.getOpenFileName(self, "选择历史数据", "", "行情数据 (*.csv *.parquet);;所有文件 (*)")
        if not filename:
            return
        symbol = "LOCAL."+dialog.symbol.text().strip().upper()
        name, period = dialog.name.text().strip(), dialog.period.currentText()
        self.notify("正在后台读取导入文件…")
        key = ("import", now_beijing().value)
        def load():
            path = Path(filename)
            frame = pd.read_parquet(path) if path.suffix.lower() == ".parquet" else pd.read_csv(path)
            aliases = {"日期": "datetime", "时间": "datetime", "开盘": "open", "最高": "high",
                       "最低": "low", "收盘": "close", "成交量": "volume", "持仓量": "open_interest", "close_oi": "open_interest"}
            frame.columns = [aliases.get(str(x).strip(), str(x).strip().lower()) for x in frame.columns]
            before = len(frame)
            frame = validate_frame(frame)
            if frame.empty:
                raise ValueError("文件没有有效OHLC记录")
            self.store.upsert_bars(symbol, native_period(period), frame)
            entry = dict(symbol=symbol, name=name or symbol, category="LOCAL", exchange="LOCAL", multiplier=10, tick=1)
            self.store.save_catalog([entry])
            return frame, entry, before-len(frame)
        def done(result):
            self._tasks.pop(key, None)
            if self._closing:
                return
            frame, entry, dropped = result
            self.save(); self.catalog[symbol] = entry
            self.select_symbol(symbol)
            with QSignalBlocker(self.period):
                self.period.setCurrentText(period)
            self.buffers = {native_period(period):frame}
            self.create_training(frame)
            with QSignalBlocker(self.category):
                self.category.setCurrentIndex(2)
            self.populate_catalog(); self.notify(f"已导入 {len(frame):,} 根，过滤无效或重复 {dropped} 行；可在新建复盘设置合约参数")
        def failed(error):
            self._tasks.pop(key, None)
            if not self._closing:
                self.notify("导入失败："+error)
        self._tasks[key] = launch(load, done, failed)

    def backtest(self):
        if self.replay.frame.empty:
            self.notify("请先载入行情"); return
        complete = self.replay.frame
        if self.replay.period in ("周线", "月线"):
            complete = aggregate_calendar(complete, self.replay.period)
        BacktestDialog(self.replay.visible(), complete, self.account.contract, self).exec()

    def open_trading(self):
        if self.trade is None:
            self.trade = TradeDialog(self)
        self.trade.show(); self.trade.raise_()

    def backup_workspace(self):
        filename, _ = QFileDialog.getSaveFileName(self, "导出完整行情与训练存档", "天勤复盘存档.sqlite3", "工作区 (*.sqlite3)")
        if filename:
            self.save()
            key = ("backup", now_beijing().value)
            def done(_):
                self._tasks.pop(key, None); self.notify("工作区存档已导出（不含登录凭据）")
            def failed(error):
                self._tasks.pop(key, None); self.notify("存档导出失败："+error)
            self._tasks[key] = launch(lambda:self.store.backup(filename), done, failed)

    def open_workspace(self):
        filename, _ = QFileDialog.getOpenFileName(self, "在独立窗口打开工作区", "", "工作区 (*.sqlite3)")
        if filename:
            try:
                window = MainWindow(Path(filename), auto_connect=False)
                window.setWindowTitle(f"{APP_NAME} · {Path(filename).name}")
                self._workspace_windows.append(window); window.show()
            except (OSError, ValueError, sqlite3.Error) as exc:
                self.notify("无法打开存档："+str(exc))

    def load_api_history(self):
        if not self.connection._credentials:
            self.login()
        if not self.connection._credentials:
            return
        symbol = self.symbol if not self.symbol.startswith(("LOCAL.", "DEMO.")) else "KQ.m@SHFE.ag"
        dialog = HistoryDialog(self.store.path, symbol, self.period.currentText(), self.connection._credentials, self)
        if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.result:
            return
        self.use_api_history(dialog.result)

    def use_api_history(self, result):
        self.save(); self.pause()
        symbol, period = result["symbol"], result["period"]
        if symbol not in self.catalog:
            entry = dict(symbol=symbol, name=symbol, category="CONT" if symbol.startswith("KQ.m@") else "FUTURE",
                         exchange=symbol.split("@")[-1].split(".")[0],
                         multiplier=result.get("multiplier") or 10, tick=result.get("tick") or 1)
            self.catalog[symbol] = entry; self.store.save_catalog([entry])
        self.select_symbol(symbol)
        with QSignalBlocker(self.period):
            self.period.setCurrentText(period)
        self._pending_initial = False
        self.watch_current(); self.load_cache(); self.populate_catalog()
        key = ("history", now_beijing().value)
        def load():
            return self.store.load_bars(symbol, result["seconds"], start=result["start"], end=result["end"])
        def done(frame):
            self._tasks.pop(key, None)
            if self._closing or self.symbol != symbol or self.period.currentText() != period:
                return
            if frame.empty:
                self.notify("该范围未读到缓存"); return
            self.create_training(frame, cursor=0)
            self.notify(f"已从天勤历史建立训练：{len(frame):,}根，{frame.iloc[0]['datetime']} 至 {frame.iloc[-1]['datetime']}")
        def failed(error):
            self._tasks.pop(key, None)
            if not self._closing:
                self.notify("历史读取失败："+error)
        self._tasks[key] = launch(load, done, failed)

    def screenshot(self):
        filename, _ = QFileDialog.getSaveFileName(self, "保存当前图表", "复盘图表.png", "PNG (*.png)")
        if filename and not self.active_chart().grab().save(filename):
            self.notify("截图保存失败")

    def help(self):
        QMessageBox.information(self, "使用说明",
            "1. 连接天勤后，选择主连或实际合约，K线由接口持续订阅并自动缓存。\n"
            "2. 点“新建复盘”选择起点与合约参数；右箭头推进1根，日按钮推进1个实际交易日。\n"
            "3. 实时看盘在独立窗口显示最新K线，不改变训练位置或账户。\n"
            "4. 模拟开平仓、限价、止盈止损均在本机记录。有交易的训练不能回退。\n"
            "5. 训练自动保存；可从训练记录恢复。CSV/Parquet可补充本地历史。\n"
            "6. 免费订阅每周期最近最多10000根，并非固定一年；旧缓存保留。\n"
            "7. 趋势指标沿用3.pdf第三版规则；证据不足显示候选。详细规则在源码docs目录。\n"
            "数据、训练和加密登录保存在本机用户目录，不写入软件源码目录。")

    def notify(self, text):
        self.message_label.setText(str(text))
        self.message_label.setToolTip(str(text))

    def closeEvent(self, event):
        if self.trade is not None and self.trade.has_live_orders():
            if QMessageBox.warning(self, "实盘尚有未成交委托",
                "关闭软件不会自动撤销柜台真实委托。确认退出吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
                event.ignore(); return
        self.pause(); self._closing = True; self._cache_token += 1
        self._seeking_target = None; self._save_timer.stop(); self.save()
        if self.live is not None:
            self.live.close()
        self.connection.shutdown()
        if self.trade is not None:
            self.trade.shutdown(); self.trade.close()
        super().closeEvent(event)
