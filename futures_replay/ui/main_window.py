from __future__ import annotations

import os
import random
import time
import traceback
from pathlib import Path
from .. import __version__

import numpy as np
import pandas as pd
from PySide6.QtCore import QByteArray, QDate, QTimer, Qt, Signal
from PySide6.QtGui import QAction, QBrush, QColor
from PySide6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QDockWidget,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QProgressDialog,
    QSlider,
    QSplitter,
    QStatusBar,
    QToolBar,
    QToolButton,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..config import DEFAULT_INITIAL_CAPITAL, PERIODS, SOURCE_DIRS, locate_data_root
from .trend_dialog import TrendIndicatorDialog
from ..realtime_data import RealtimeTarget
from .background_live import BackgroundLiveController, TqLoginDialog, component_issue
from .live_chart_dialog import LiveChartDialog
from ..data_repo import DataRepository
from ..models import Drawing, OrderSide, Side
from ..edb_update import (
    EDBUpdateCancelled,
    EDBUpdateError,
    fetch_edb_bundle,
    full_history_start_times,
    incremental_start_times,
    make_edb_contract_symbol,
    make_edb_main_symbol,
    persist_edb_bundle,
    request_edb_token,
)
from ..persistence import (
    clear_state, load_state, save_state, load_replay_heads, save_replay_heads,
    load_main_list_sort, save_main_list_sort,
)
from ..replay import ReplayEngine, MinutePathProvider, locate_bar_index
from ..timeline_utils import (
    shift_trading_day, trading_days_from_frame,
    first_timestamp_for_trading_day, resolve_trading_day,
    snapshot_timestamp_matches_latest, replay_status,
)
from ..trading import TradingAccount
from ..trade_records import DailyTradeRecorder
from .chart_widget import CandleChart
from .trading_panel import TradingPanel
from .backtest_panel import BacktestPanel


_PERIOD_LABELS = [
    ("分时", "1min", True),
    ("1分钟", "1min", False),
    ("5分钟", "5min", False),
    ("15分钟", "15min", False),
    ("30分钟", "30min", False),
    ("1小时", "1h", False),
    ("4小时", "4h", False),
    ("日线", "1d", False),
    ("周线", "1w", False),
    ("月线", "1M", False),
]


_MAIN_LIST_SORT_ROLE = int(Qt.ItemDataRole.UserRole) + 41
_MAIN_LIST_TIE_ROLE = int(Qt.ItemDataRole.UserRole) + 42


class MainListItem(QTableWidgetItem):
    """QTableWidget item with typed keys and a persistent tie-break rank.

    Qt's table sort is not guaranteed to preserve the previous row order when
    several cells have the same value.  That matters for the watch list after a
    restart: e.g. rows that were "播放中" become "已播放", so many status
    cells become equal.  The secondary key keeps the exact user-visible order
    within equal-value groups across refreshes and restarts.
    """

    def __lt__(self, other):
        if isinstance(other, QTableWidgetItem):
            left = self.data(_MAIN_LIST_SORT_ROLE)
            right = other.data(_MAIN_LIST_SORT_ROLE)

            # First compare the actual column value.  Treat two empty values as
            # equal (the previous implementation returned True for both a<b and
            # b<a when both were None, which could randomly reshuffle rows).
            if left is None and right is not None:
                return True
            if right is None and left is not None:
                return False
            if left is not None and right is not None:
                try:
                    if left != right:
                        return left < right
                except TypeError:
                    ls, rs = str(left).casefold(), str(right).casefold()
                    if ls != rs:
                        return ls < rs

            # Equal primary values use a persisted rank.  The rank is signed
            # according to Asc/Desc so Qt's direction reversal still preserves
            # the same visual row order for ties.
            ltie = self.data(_MAIN_LIST_TIE_ROLE)
            rtie = other.data(_MAIN_LIST_TIE_ROLE)
            if ltie is not None and rtie is not None and ltie != rtie:
                try:
                    return float(ltie) < float(rtie)
                except Exception:
                    return str(ltie) < str(rtie)
        return super().__lt__(other)


class PeriodComboBox(QComboBox):
    """Chinese period labels while keeping the repository period codes.

    Existing replay/data code can continue to call currentText()/setCurrentText()
    with values such as ``1min`` or ``1d``.  The combobox itself displays
    ``1分钟``/``日线``.  ``分时`` deliberately shares the 1-minute data source
    but switches the chart to an intraday line view.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        valid = set(PERIODS)
        for label, code, intraday in _PERIOD_LABELS:
            if code in valid:
                self.addItem(label, {"code": code, "intraday": intraday})
        # Fallback for any future period code not covered above.
        covered = {code for _label, code, _intraday in _PERIOD_LABELS}
        for code in PERIODS:
            if code not in covered:
                self.addItem(str(code), {"code": str(code), "intraday": False})

    def period_code(self) -> str:
        data = super().currentData()
        if isinstance(data, dict):
            return str(data.get("code", ""))
        return str(super().currentText())

    def is_intraday(self) -> bool:
        data = super().currentData()
        return bool(data.get("intraday", False)) if isinstance(data, dict) else False

    def display_text(self) -> str:
        return str(super().currentText())

    # Preserve the old code-facing API used throughout MainWindow.
    def currentText(self) -> str:  # noqa: N802 - Qt API compatibility
        return self.period_code()

    def set_period(self, code: str, intraday: bool = False):
        code = str(code)
        best = -1
        for i in range(self.count()):
            data = self.itemData(i)
            if not isinstance(data, dict) or str(data.get("code")) != code:
                continue
            if bool(data.get("intraday", False)) == bool(intraday):
                best = i
                break
            if best < 0:
                best = i
        if best >= 0:
            self.setCurrentIndex(best)

    def setCurrentText(self, text: str):  # noqa: N802 - Qt API compatibility
        # Allow an explicit Chinese display label first.
        idx = QComboBox.findText(self, str(text))
        if idx >= 0:
            self.setCurrentIndex(idx)
            return
        # Saved/internal period codes restore to the ordinary candlestick item.
        self.set_period(str(text), intraday=False)

    def findText(self, text: str, flags=Qt.MatchFlag.MatchExactly | Qt.MatchFlag.MatchCaseSensitive):  # noqa: N802
        idx = QComboBox.findText(self, str(text), flags)
        if idx >= 0:
            return idx
        code = str(text)
        fallback = -1
        for i in range(self.count()):
            data = self.itemData(i)
            if isinstance(data, dict) and str(data.get("code")) == code:
                if not bool(data.get("intraday", False)):
                    return i
                fallback = i
        return fallback


class ChartPanel(QWidget):
    activated = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        top = QHBoxLayout()
        self.period = PeriodComboBox(); self.period.setCurrentText("5min")
        self.contract = QComboBox()
        self.ma_enabled = QCheckBox("均线"); self.ma_enabled.setChecked(True)
        self.ma_values = QLineEdit("5,10,20,60")
        self.ma_values.setFixedWidth(105)
        self.ma_values.setToolTip("均线周期可自定义，例如 5,10,20,60")
        self.boll = QCheckBox("BOLL"); self.boll.setChecked(False)
        self.boll.setToolTip("布林线叠加在主图上，不占用副图窗口")
        self.wave = QCheckBox("波浪")
        self.wave.setToolTip("主图叠加 ATR/百分比 ZigZag 浪型候选、1–5浪/ABC标记、回调/延伸比例及常见K线反转形态；波浪计数仅作辅助分析")
        self.trend = QCheckBox("趋势交易")
        self.trend.setToolTip(
            "依据最新版3.pdf第三版：区间跨度与浪级别、波浪线、趋势/拐点线、主A/导A、"
            "回调比例、标准K与关键位置反转、四类调整浪。点‘指标明细’查看依据和分层设置。"
        )
        self.trend_details = QPushButton("指标明细")
        self.trend_details.setToolTip("趋势交易法第三版：显示设置、区间/A点/反转判定明细与规则书页")
        self.trend_details.clicked.connect(self._show_trend_details)
        self.volume = QCheckBox("成交量"); self.volume.setChecked(True)
        self.volume.setToolTip("显示/隐藏整个成交量副图；VMA5、VMA10、持仓线在成交量副图右上角独立开关")
        # Hidden state holders: the user-facing switches are painted inside the
        # volume pane.  They remain QCheckBoxes only for session persistence and
        # for compatibility with older saved layouts.
        self.vma5 = QCheckBox("VMA5", self); self.vma5.setChecked(True); self.vma5.hide()
        self.vma10 = QCheckBox("VMA10", self); self.vma10.setChecked(True); self.vma10.hide()
        self.oi_line = QCheckBox("持仓线", self); self.oi_line.setChecked(True); self.oi_line.hide()
        self.indicator = QComboBox(); self.indicator.addItems(["无", "MACD", "KDJ", "RSI", "WR", "CCI", "ATR"])
        self.indicator.setToolTip("副图指标；ATR默认使用Wilder ATR(14)；成交量均线已并入成交量副图；BOLL为主图叠加")
        for w in [
            QLabel("周期"), self.period, QLabel("合约/序列"), self.contract,
            self.ma_enabled, self.ma_values, self.boll, self.wave, self.trend, self.trend_details, self.volume, QLabel("副图"), self.indicator,
        ]:
            top.addWidget(w)
        top.addStretch(1)
        v.addLayout(top)

        self.chart = CandleChart()
        self.chart.activated.connect(lambda: self.activated.emit(self))
        # Keep the pane-local switches and the persisted checkbox state in sync.
        # Without this, playback refreshes can immediately restore the old values
        # after a click, making the buttons appear unresponsive.
        self.chart.volumeOverlayChanged.connect(self._sync_volume_overlay_state)
        v.addWidget(self.chart, 1)
        self.chart.set_intraday_mode(self.period.is_intraday())

    def _show_trend_details(self):
        dialog = TrendIndicatorDialog(self.chart, self)
        if dialog.exec() == TrendIndicatorDialog.DialogCode.Accepted:
            self.chart.set_trend_settings(dialog.result_settings)
            self.trend.setChecked(True)
            self.apply_options()
            self.chart.layoutChanged.emit()

    def _sync_volume_overlay_state(self, vma5: bool, vma10: bool, oi_line: bool):
        for checkbox, value in (
            (self.vma5, vma5),
            (self.vma10, vma10),
            (self.oi_line, oi_line),
        ):
            old = checkbox.blockSignals(True)
            checkbox.setChecked(bool(value))
            checkbox.blockSignals(old)

    def apply_period_mode(self):
        self.chart.set_intraday_mode(self.period.is_intraday())

    def ma_periods(self) -> tuple[int, ...]:
        text = self.ma_values.text().replace("，", ",").replace(";", ",").replace("；", ",")
        vals: list[int] = []
        for part in text.split(","):
            part = part.strip()
            if not part:
                continue
            try:
                value = int(part)
            except ValueError:
                continue
            if 1 <= value <= 1000 and value not in vals:
                vals.append(value)
        if not vals:
            vals = [5, 10, 20, 60]
            self.ma_values.setText("5,10,20,60")
        return tuple(vals[:8])

    def apply_options(self):
        self.chart.set_ma(self.ma_periods(), self.ma_enabled.isChecked())
        self.chart.set_boll_visible(self.boll.isChecked())
        self.chart.set_wave_visible(self.wave.isChecked())
        self.chart.set_trend_visible(self.trend.isChecked())
        self.chart.set_volume_visible(self.volume.isChecked())
        self.chart.set_volume_overlays(self.vma5.isChecked(), self.vma10.isChecked(), self.oi_line.isChecked())
        self.chart.set_indicator(self.indicator.currentText())



class ReplayStatusBar(QStatusBar):
    """Status bar with a persistent replay clock at bottom-left.

    Temporary operation/error messages are shown to the right of the clock and
    do not replace or hide the replay timestamp.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._message_timer = QTimer(self)
        self._message_timer.setSingleShot(True)
        self._message_timer.timeout.connect(self._clear_status_message)

        self._time_label = QLabel("")
        self._time_label.setObjectName("replayTimeStatusLabel")
        self._time_label.setMinimumWidth(390)
        self._time_label.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        self._time_label.setToolTip("当前复盘时间")
        self.addWidget(self._time_label, 0)

        self._message_label = QLabel("")
        self._message_label.setObjectName("replayStatusMessageLabel")
        self._message_label.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        self.addWidget(self._message_label, 1)

    def setReplayTime(self, text: str) -> None:
        self._time_label.setText(str(text or ""))

    def showMessage(self, message: str, timeout: int = 0) -> None:
        # Use a dedicated label instead of QStatusBar's native message area,
        # which hides ordinary widgets while a message is active.
        self._message_timer.stop()
        self._message_label.setText(str(message or ""))
        if int(timeout or 0) > 0:
            self._message_timer.start(int(timeout))

    def clearMessage(self) -> None:
        self._message_timer.stop()
        self._message_label.clear()

    def currentMessage(self) -> str:
        return self._message_label.text()

    def _clear_status_message(self) -> None:
        self._message_label.clear()



class MainWindow(QMainWindow):
    def __init__(self, data_root: Path | None = None):
        super().__init__()
        self.setWindowTitle(f"期货复盘训练系统 v{__version__}")
        self.resize(1500, 900)
        self.state = load_state()
        # Main-list sorting is also stored in its own tiny preference file.
        # This is intentionally independent from the larger session JSON: the
        # list order should survive even if a later startup/refresh saves other
        # UI state before the watch list has finished rebuilding.
        try:
            self._main_list_sort_pref = dict(load_main_list_sort() or {})
        except Exception:
            self._main_list_sort_pref = {}
        # Keep an immutable copy of the last replay location during startup.
        # Several combobox signals fire while the UI is being reconstructed;
        # without this snapshot an intermediate load can accidentally replace
        # the position that should be restored.
        self._startup_replay = dict(self.state.get("replay", {}) or {})
        self._startup_replay_pending = bool(self._startup_replay)
        self.data_root = data_root or (Path(self.state["data_root"]) if self.state.get("data_root") else locate_data_root(Path.cwd()))
        self.repo: DataRepository | None = DataRepository(self.data_root) if self.data_root else None
        self.account = TradingAccount.from_dict(self.state.get("account", {})) if self.state.get("account") else TradingAccount()
        self.records = DailyTradeRecorder()
        self.engine: ReplayEngine | None = None
        self._visible_engine_key = ""
        self.symbol = ""
        self.source = "原始行情"
        self.panels: list[ChartPanel] = []
        self.active_panel: ChartPanel | None = None
        self.play_timer = QTimer(self)
        self.play_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.play_timer.timeout.connect(self._tick)
        self.save_timer = QTimer(self); self.save_timer.setSingleShot(True); self.save_timer.timeout.connect(self._save_now)
        self._building = True
        self._animate_forward = False
        self._reset_replay_on_next_load = False
        # High-speed playback is driven by elapsed wall-clock time rather than
        # assuming QTimer callbacks arrive exactly on schedule.
        self._play_last_clock: float | None = None
        self._play_budget_ms = 0.0
        self._last_replay_checkpoint = time.monotonic()
        self._replay_checkpoint_seconds = 1.0
        self._programmatic_date_update = False
        self._market_load_busy = False
        self._startup_symbol_load_pending = True
        # Contract-aware multi-instrument execution state.
        self._market_processed_ts: dict[str, pd.Timestamp] = {}
        self._sync_minute_cache: dict[str, tuple[pd.DataFrame, np.ndarray]] = {}
        # One independent replay session per source/product.  A session keeps its
        # own historical clock and can continue playing after the user switches
        # the visible chart to another product.  Runtime engines are never put
        # into QSettings; only compact clock snapshots are persisted.
        raw_sessions = self.state.get("replay_sessions", {}) or {}
        self._saved_replay_sessions: dict[str, dict] = {
            str(k): dict(v) for k, v in raw_sessions.items() if isinstance(v, dict)
        }
        try:
            replay_heads = load_replay_heads()
            for _key, _row in replay_heads.items():
                if not isinstance(_row, dict):
                    continue
                _key = str(_key)
                _old = self._saved_replay_sessions.get(_key)
                # Never let an accidental played-at-(0,0) checkpoint overwrite
                # a later valid position from the full session file.
                if self._snapshot_is_suspicious_first_bar(_row) and isinstance(_old, dict):
                    _op = self._snapshot_position(_old)
                    _np = self._snapshot_position(_row)
                    if _op and _np:
                        _ots, _obi, _omi = _op
                        _nts, _nbi, _nmi = _np
                        if (_ots is not None and _nts is not None and _ots > _nts) or _obi > 0 or _omi > 0:
                            continue
                self._saved_replay_sessions[_key] = self._prefer_snapshot(_old, _row, force=False)
        except Exception:
            pass
        self._replay_sessions: dict[str, dict] = {}
        # Runtime-only group pause state.  "全部暂停" remembers exactly which
        # products were playing at that moment so "全播放" resumes only those
        # products; products that were already paused stay paused.
        self._all_pause_active = False
        self._all_pause_resume_keys: list[str] = []
        self._played_order: list[str] = [
            str(x) for x in (self.state.get("played_sessions", []) or []) if str(x)
        ]
        self._main_list_rows: dict[str, int] = {}
        self._main_quote_cache: dict[str, tuple[float | None, float | None]] = {}
        self._main_quote_queue: list[str] = []
        self._main_quote_timer = QTimer(self)
        self._main_quote_timer.setInterval(120)
        self._main_quote_timer.timeout.connect(self._load_main_quote_batch)
        self._live_dirty = set()
        self._live_chart = None
        self.live_controller = BackgroundLiveController(self)
        self.live_controller.statusChanged.connect(self._live_status)
        self.live_controller.saved.connect(self._live_saved)
        self.live_controller.runningChanged.connect(self._live_running_changed)
        self._build_ui()
        self._building = False
        self._restore_ui()
        self._load_instruments()

    def _build_ui(self):
        tb = QToolBar("工具栏", self); self.addToolBar(tb)
        self.source_combo = QComboBox(); self.source_combo.addItems(["原始行情", "主力连续"])
        self.exchange_combo = QComboBox(); self.exchange_combo.addItem("全部")
        self.symbol_combo = QComboBox()
        self.tool_combo = QComboBox(); self.tool_combo.addItems(["光标", "趋势线", "射线", "水平线", "垂直线", "矩形", "黄金分割", "波浪尺", "限价买", "限价卖"])
        self.tool_combo.setItemData(
            self.tool_combo.findText("波浪尺"),
            "依次点击 A、B、C 三点；以 AB 为基准，从 C 向 A→B 方向投射延伸比例",
            Qt.ItemDataRole.ToolTipRole,
        )
        self.window_combo = QComboBox(); self.window_combo.addItems(["1", "2", "3"])
        for label, widget in [("数据源",self.source_combo),("交易所",self.exchange_combo),("品种",self.symbol_combo),("工具",self.tool_combo),("窗口",self.window_combo)]:
            tb.addWidget(QLabel(label+" ")); tb.addWidget(widget); tb.addSeparator()
        choose = QAction("选择数据目录", self); choose.triggered.connect(self._choose_data_root); tb.addAction(choose)
        self.realtime_action = QAction("免费实时更新当前", self)
        self.realtime_action.setObjectName("tianqin_realtime_action")
        self.realtime_action.setToolTip("用普通天勤账号订阅当前合约的实时分钟行情，并保存到本地复盘数据")
        self.realtime_action.triggered.connect(self._start_current_live)
        self.realtime_all_action = QAction("免费实时更新全部", self)
        self.realtime_all_action.setObjectName("tianqin_realtime_all_action")
        self.realtime_all_action.triggered.connect(self._start_all_live)
        self.realtime_all_action.setToolTip("免费行情在独立后台进程中更新当前数据源的全部品种，不移动复盘位置")
        self.live_view_action = QAction("实时跟随", self)
        self.live_view_action.setCheckable(True)
        self.live_view_action.setToolTip("按需打开实时K线窗口；关闭后后台更新继续，原复盘进度保持不变")
        self.live_view_action.toggled.connect(self._toggle_live_view)
        tb.addAction(self.live_view_action)
        update_data = QAction("更新数据", self); update_data.triggered.connect(self._update_edb_data)
        update_all = QAction("全部更新", self); update_all.triggered.connect(self._update_all_edb_data)
        for action, live_action in ((update_data, self.realtime_action), (update_all, self.realtime_all_action)):
            button = QToolButton(self)
            button.setDefaultAction(action)
            menu = QMenu(button); menu.addAction(live_action)
            button.setMenu(menu); button.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
            button.setToolTip("点击按钮保留原历史更新；点击右侧箭头可选择免费后台实时更新或停止")
            tb.addWidget(button)
        rebuild_history = QAction("更新历史数据", self); rebuild_history.triggered.connect(self._rebuild_all_history); tb.addAction(rebuild_history)
        rebuild_history.setToolTip("通过天勤 EDB 扫描并更新本地所有原始合约；分钟周期由1分钟合成，周线/月线由日线合成")
        clear_draw = QAction("清画线", self); clear_draw.triggered.connect(self._clear_drawings); tb.addAction(clear_draw)
        clear_save = QAction("清存档", self); clear_save.triggered.connect(self._clear_state); tb.addAction(clear_save)

        central = QWidget(); self.setCentralWidget(central); cv = QVBoxLayout(central); cv.setContentsMargins(3,3,3,3)
        replay = QHBoxLayout()
        self.replay_layout = replay
        replay.setContentsMargins(0, 0, 0, 0)
        replay.setSpacing(5)
        self.btn_random_symbol = QPushButton("随机主连")
        self.btn_play = QPushButton("播放")
        self.btn_all_play = QPushButton("全部暂停")
        self.btn_all_play.setToolTip("暂停当前所有正在播放的品种；再次点击可恢复刚才被暂停的品种")

        # Speed is expressed directly in seconds per real 1-minute replay point.
        # The slider runs left=slow, right=fast. Internally its value is inverted.
        self.speed = QSlider(Qt.Orientation.Horizontal)
        self.speed.setRange(1, 200)
        self.speed.setValue(171)
        self.speed.setFixedWidth(165)
        self.speed.setToolTip("回放速度：越往右越快，范围 2.00秒 到 0.01秒")

        self.lbl_speed = QLabel()
        self.lbl_speed.setFixedWidth(62)
        self.lbl_speed.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        self.btn_speed_slower = QPushButton("−")
        self.btn_speed_slower.setFixedWidth(28)
        self.btn_speed_slower.setToolTip("速度降低一档")
        self.btn_speed_faster = QPushButton("＋")
        self.btn_speed_faster.setFixedWidth(28)
        self.btn_speed_faster.setToolTip("速度提高一档")

        self.follow = QCheckBox("自动跟随")
        self.follow.setChecked(True)

        # These two controls intentionally follow Auto Follow, as requested.
        self.btn_random_date = QPushButton("随机日期")
        self.btn_start = QPushButton("从最早开始")

        self.progress = QSlider(Qt.Orientation.Horizontal)
        self.progress.setRange(0, 10000)
        self.progress.setValue(0)
        self.progress.setFixedWidth(220)
        self.progress.setToolTip("拖动时间轴跳转到对应复盘位置")

        self.btn_prev_day = QPushButton("−")
        self.btn_prev_day.setFixedWidth(28)
        self.btn_prev_day.setToolTip("前一个有行情的交易日")
        self.btn_next_day = QPushButton("＋")
        self.btn_next_day.setFixedWidth(28)
        self.btn_next_day.setToolTip("后一个有行情的交易日")

        self.date_edit = QDateEdit()
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setFixedWidth(125)

        self.lbl_speed_title = QLabel("速度")
        self._apply_top_replay_order()
        cv.addLayout(replay)
        self.main_split = QSplitter(Qt.Orientation.Horizontal)
        self.chart_split = QSplitter(Qt.Orientation.Vertical)
        self.right_tabs = QTabWidget()
        self.right_tabs.setObjectName("rightFixedTabs")
        self.right_tabs.setMinimumWidth(360)
        self.backtest = BacktestPanel(self._backtest_context)
        self.backtest.resultActivated.connect(self._show_backtest_result)
        self.backtest.settingsChanged.connect(self._schedule_save)
        # The watch list and backtest are fixed pages inside this right-hand tab
        # area.  They are intentionally NOT QDockWidgets: only the order-entry
        # window may float/dock around the main window.
        self.main_split.addWidget(self.chart_split); self.main_split.addWidget(self.right_tabs); self.main_split.setStretchFactor(0,1)
        cv.addWidget(self.main_split, 1)

        # Trading becomes a real QDockWidget.  It can be dragged out as an owned
        # floating tool window (therefore stays in front of this main window),
        # resized from its edges, or docked to any side again.  QMainWindow state
        # plus the dock's floating geometry are persisted below.
        self.trade = TradingPanel()
        self.trade_dock = QDockWidget("下单窗口", self)
        self.trade_dock.setObjectName("tradeDock")
        self.trade_dock.setAllowedAreas(Qt.DockWidgetArea.AllDockWidgetAreas)
        self.trade_dock.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetMovable
            | QDockWidget.DockWidgetFeature.DockWidgetFloatable
            | QDockWidget.DockWidgetFeature.DockWidgetClosable
        )
        self.trade_dock.setWidget(self.trade)
        self.trade_dock.setMinimumSize(720, 245)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.trade_dock)

        # Main-continuous watch list.  It is a FIXED page of the right-side tab
        # area (beside the backtest page), not a floating/dockable window. Columns
        # remain movable/resizable and their visual order/width are persisted.
        self.main_list = QTableWidget(0, 8)
        self.main_list.setHorizontalHeaderLabels([
            "序号", "品种", "名称", "交易所", "现价", "涨跌幅", "复盘时间", "状态"
        ])
        self.main_list.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.main_list.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.main_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.main_list.setToolTip("可用 Ctrl/Shift 多选品种；多选后点击‘播放’可同时播放/暂停选中品种")
        self.main_list.setAlternatingRowColors(True)
        self.main_list.verticalHeader().setVisible(False)
        h = self.main_list.horizontalHeader()
        h.setSectionsMovable(True)
        h.setSectionsClickable(True)
        h.setSortIndicatorShown(True)
        h.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.main_list.setSortingEnabled(True)
        self.main_list.setMinimumWidth(330)
        self.right_tabs.addTab(self.main_list, "主连列表")
        self.right_tabs.addTab(self.backtest, "回测")

        tb.addSeparator()
        trade_toggle = self.trade_dock.toggleViewAction(); trade_toggle.setText("下单窗口"); tb.addAction(trade_toggle)
        list_action = QAction("主连列表", self)
        list_action.triggered.connect(lambda _=False: self.right_tabs.setCurrentWidget(self.main_list))
        tb.addAction(list_action)
        backtest_action = QAction("回测", self)
        backtest_action.triggered.connect(lambda _=False: self.right_tabs.setCurrentWidget(self.backtest))
        tb.addAction(backtest_action)

        _replay_status_bar = ReplayStatusBar(self)
        self.setStatusBar(_replay_status_bar)
        self.live_status_label = QLabel("后台行情：未启动")
        self.statusBar().addPermanentWidget(self.live_status_label)
        # Playback controls stay in the top toolbar; the status bar remains
        # dedicated to the changing replay timestamp and temporary messages.
        self.btn_all_play.setFixedWidth(88)
        self.btn_play.setFixedWidth(58)
        self._apply_top_replay_order()
        self._rebuild_panels(1)

        self.source_combo.currentTextChanged.connect(self._source_changed)
        self.exchange_combo.currentTextChanged.connect(self._filter_symbols)
        self.symbol_combo.currentIndexChanged.connect(self._symbol_changed)
        self.tool_combo.currentTextChanged.connect(self._tool_changed)
        self.window_combo.currentTextChanged.connect(lambda t: self._rebuild_panels(int(t)))
        self.btn_play.clicked.connect(self._toggle_play)
        self.btn_all_play.clicked.connect(self._toggle_all_playback)
        self.btn_start.clicked.connect(self._go_start); self.btn_random_date.clicked.connect(self._random_date); self.btn_random_symbol.clicked.connect(self._random_symbol)
        self.speed.valueChanged.connect(self._speed_changed); self.follow.toggled.connect(self._follow_changed)
        self.btn_speed_slower.clicked.connect(lambda _=False: self._adjust_speed_level(-1))
        self.btn_speed_faster.clicked.connect(lambda _=False: self._adjust_speed_level(1))
        self.progress.sliderReleased.connect(self._progress_jump); self.date_edit.dateChanged.connect(self._date_jump)
        self.btn_prev_day.clicked.connect(lambda _=False: self._step_trading_day(-1))
        self.btn_next_day.clicked.connect(lambda _=False: self._step_trading_day(1))
        self.trade.actionRequested.connect(self._trade_action); self.trade.depositRequested.connect(self._deposit)
        self.trade.resetAccountRequested.connect(self._reset_account)
        self.trade.fractionRequested.connect(self._set_fraction); self.trade.capitalSizingRequested.connect(self._set_capital_sizing); self.trade.quantityEdited.connect(self._qty_manually_edited); self.trade.cancelOrderRequested.connect(self._cancel_order); self.trade.cancelAllRequested.connect(self._cancel_all)
        self.trade.ratiosChanged.connect(self._schedule_save)
        self.trade.tableLayoutChanged.connect(self._schedule_save)
        # Clicking a live position should navigate to exactly the same market
        # context as selecting the product from the main-continuous list, while
        # preserving the position's own source/contract when available.
        self.trade.positionSymbolClicked.connect(self._position_symbol_clicked)
        self.trade.tradeRecordSymbolClicked.connect(self._trade_record_symbol_clicked)
        self.right_tabs.currentChanged.connect(lambda _i: self._schedule_save())
        self.main_list.cellClicked.connect(self._main_list_clicked)
        self.main_list.horizontalHeader().sectionMoved.connect(lambda *_: self._schedule_save())
        self.main_list.horizontalHeader().sectionResized.connect(lambda *_: self._schedule_save())
        self.main_list.horizontalHeader().sortIndicatorChanged.connect(self._main_list_sort_changed)

        # Final runtime layout pass: do it after the event loop starts so any
        # late startup/state restoration cannot put Play/All Pause back at the
        # beginning of the replay row.
        QTimer.singleShot(0, self._apply_top_replay_order)
        QTimer.singleShot(120, self._apply_top_replay_order)
        # Only the order-entry dock can float/move.  The right-side main-list /
        # backtest tabs are part of the central splitter and therefore fixed.
        self.trade_dock.topLevelChanged.connect(lambda *_: self._schedule_save())
        self.trade_dock.visibilityChanged.connect(lambda *_: self._schedule_save())
        self.trade_dock.dockLocationChanged.connect(lambda *_: self._schedule_save())
        self._update_speed_label()


    # -------------------------- dock/watch-list helpers --------------------------
    @staticmethod
    def _replay_session_key(source: str, symbol: str) -> str:
        return f"{str(source)}::{str(symbol)}"

    def _session_for(self, source: str | None = None, symbol: str | None = None, create: bool = True):
        source = str(source if source is not None else self.source_combo.currentText())
        symbol = str(symbol if symbol is not None else self.symbol)
        if not symbol:
            return None
        key = self._replay_session_key(source, symbol)
        sess = self._replay_sessions.get(key)
        if sess is None and create:
            saved = dict(self._saved_replay_sessions.get(key, {}) or {})
            sess = {
                "key": key,
                "source": source,
                "symbol": symbol,
                "exchange": str(saved.get("exchange", "") or ""),
                "period": str(saved.get("period", "") or ""),
                "intraday": bool(saved.get("intraday", False)),
                "contract": str(saved.get("contract", "") or ""),
                "timestamp": str(saved.get("timestamp", "") or ""),
                "trading_day": str(saved.get("trading_day", "") or ""),
                "bar_index": saved.get("bar_index"),
                "minute_index": saved.get("minute_index"),
                "price": saved.get("price"),
                "change_pct": saved.get("change_pct"),
                "engine": None,
                "at_end": bool(saved.get("at_end", False)),
                # Never auto-resume after a program restart.  The saved clock is
                # restored, but playback resumes only when the user presses Play.
                "playing": False,
                "played": bool(saved.get("played", key in self._played_order)),
            }
            self._replay_sessions[key] = sess
        return sess

    @staticmethod
    def _compact_session_snapshot(sess: dict) -> dict:
        """Return the JSON-safe authoritative replay head for one product."""
        return {
            "source": str(sess.get("source", "")),
            "symbol": str(sess.get("symbol", "")),
            "exchange": str(sess.get("exchange", "")),
            "period": str(sess.get("period", "")),
            "intraday": bool(sess.get("intraday", False)),
            "contract": str(sess.get("contract", "")),
            "timestamp": str(sess.get("timestamp", "")),
            "trading_day": str(sess.get("trading_day", "")),
            "bar_index": sess.get("bar_index"),
            "minute_index": sess.get("minute_index"),
            "price": sess.get("price"),
            "change_pct": sess.get("change_pct"),
            "played": bool(sess.get("played", False)),
            "at_end": bool(sess.get("at_end", False)),
        }

    def _update_saved_session_shadow(self, sess: dict | None, *, force: bool = False) -> None:
        """Update the persisted shadow without allowing passive regressions.

        Symbol switching and engine reconstruction are passive operations: they
        must never move a played product to an older candle.  Explicit history
        jumps use ``force=True`` and are therefore still allowed to move back.
        """
        if not sess:
            return
        key = str(sess.get("key", "") or "")
        if not key:
            return
        row = self._compact_session_snapshot(sess)
        old = self._saved_replay_sessions.get(key)
        self._saved_replay_sessions[key] = self._prefer_snapshot(old, row, force=force)

    @staticmethod
    def _restore_engine_snapshot(engine: ReplayEngine, snapshot: dict | None) -> bool:
        """Restore a replay head and verify it against both time and coordinates.

        Timestamp restoration is stable when history files are extended, while
        bar/minute coordinates are exact when the data file is unchanged.  We
        evaluate both candidates and choose the one closest to the saved minute.
        This avoids a timestamp mapping edge case silently landing on bar 0.
        """
        if not snapshot:
            return False

        target_ts = None
        raw_ts = str(snapshot.get("timestamp", "") or "").strip()
        if raw_ts:
            try:
                target_ts = pd.Timestamp(raw_ts)
                if pd.isna(target_ts):
                    target_ts = None
            except Exception:
                target_ts = None

        candidates: list[tuple[float, int, int]] = []

        if target_ts is not None:
            try:
                engine.jump_timestamp(target_ts, snapshot.get("trading_day"))
                cur = engine.current()
                diff = abs((pd.Timestamp(cur.timestamp) - target_ts).total_seconds())
                candidates.append((float(diff), int(engine.bar_index), int(engine.minute_index)))
            except Exception:
                pass

        if snapshot.get("bar_index") is not None:
            try:
                engine.jump_bar(int(snapshot.get("bar_index")))
                if snapshot.get("minute_index") is not None:
                    mi = int(snapshot.get("minute_index"))
                    engine.minute_index = max(0, min(mi, engine.path_length - 1))
                cur = engine.current()
                diff = 0.0 if target_ts is None else abs((pd.Timestamp(cur.timestamp) - target_ts).total_seconds())
                candidates.append((float(diff), int(engine.bar_index), int(engine.minute_index)))
            except Exception:
                pass

        if not candidates:
            return False

        # Prefer the candidate closest to the saved timestamp.  When tied, the
        # later bar/minute wins rather than accidentally falling back to bar 0.
        _diff, bi, mi = min(candidates, key=lambda x: (x[0], -x[1], -x[2]))
        # A played product with a valid stored coordinate past bar 0 must never
        # be accepted at bar 0 merely because a stale timestamp candidate won.
        try:
            saved_bi = int(snapshot.get("bar_index")) if snapshot.get("bar_index") is not None else -1
            saved_mi = int(snapshot.get("minute_index")) if snapshot.get("minute_index") is not None else 0
            if bool(snapshot.get("played", False)) and saved_bi > 0 and int(bi) == 0 and saved_bi < len(engine.bars):
                bi, mi = saved_bi, saved_mi
        except Exception:
            pass
        try:
            engine.jump_bar(int(bi))
            engine.minute_index = max(0, min(int(mi), engine.path_length - 1))
            return True
        except Exception:
            return False

    def _sync_session_from_engine(self, sess: dict | None, *, force: bool = False) -> None:
        """Copy a live engine head into one replay session safely.

        If a passive rebuild somehow produced an older engine (the AP/AD first-
        candle failure), restore that engine from the last authoritative shadow
        instead of checkpointing the regression.
        """
        if not sess:
            return
        engine = sess.get("engine")
        if engine is None:
            return
        key = str(sess.get("key", "") or "")
        try:
            st = engine.current()
            candidate = dict(sess)
            candidate.update({
                "timestamp": str(st.timestamp),
                "trading_day": str(st.trading_day),
                "bar_index": int(engine.bar_index),
                "minute_index": int(engine.minute_index),
                "at_end": bool(getattr(engine, "at_end", False)),
            })
            saved = self._saved_replay_sessions.get(key) if key else None
            if not force and self._snapshot_is_regression(saved, candidate):
                if self._restore_engine_snapshot(engine, saved):
                    st = engine.current()
                    candidate.update({
                        "timestamp": str(st.timestamp),
                        "trading_day": str(st.trading_day),
                        "bar_index": int(engine.bar_index),
                        "minute_index": int(engine.minute_index),
                        "at_end": bool(getattr(engine, "at_end", False)),
                    })
                else:
                    return
            sess.update({
                "timestamp": candidate.get("timestamp", ""),
                "trading_day": candidate.get("trading_day", ""),
                "bar_index": candidate.get("bar_index"),
                "minute_index": candidate.get("minute_index"),
                "at_end": bool(candidate.get("at_end", False)),
            })
            px, pct = self._session_price_pct(sess)
            sess["price"] = px
            sess["change_pct"] = pct
            trade_key = TradingAccount.make_trade_key(
                str(sess.get("source", "")),
                str(sess.get("symbol", "")),
                str(sess.get("contract", "")),
            )
            if trade_key and px is not None:
                self.account.set_mark(trade_key, float(px))
            self._update_saved_session_shadow(sess, force=force)
        except Exception:
            pass

    @staticmethod
    def _snapshot_position(snapshot: dict | None):
        if not isinstance(snapshot, dict):
            return None
        raw = str(snapshot.get("timestamp", "") or "").strip()
        ts = None
        if raw:
            try:
                ts = pd.Timestamp(raw)
                if pd.isna(ts):
                    ts = None
            except Exception:
                ts = None
        try:
            bi = int(snapshot.get("bar_index")) if snapshot.get("bar_index") is not None else -1
        except Exception:
            bi = -1
        try:
            mi = int(snapshot.get("minute_index")) if snapshot.get("minute_index") is not None else -1
        except Exception:
            mi = -1
        return ts, bi, mi

    @classmethod
    def _snapshot_is_regression(cls, old: dict | None, new: dict | None) -> bool:
        """Return True for an accidental passive move to an older replay head.

        Normal playback only moves forward.  Backward motion is allowed only by
        explicit user history-jump actions, which call the sync helpers with
        ``force=True``.  This guard is therefore safe for symbol switching,
        engine rebuilding and background checkpointing.
        """
        if not isinstance(old, dict) or not isinstance(new, dict):
            return False
        if str(old.get("source", "")) != str(new.get("source", "")):
            return False
        if str(old.get("symbol", "")) != str(new.get("symbol", "")):
            return False
        op = cls._snapshot_position(old)
        np = cls._snapshot_position(new)
        if op is None or np is None:
            return False
        ots, obi, omi = op
        nts, nbi, nmi = np
        if ots is not None and nts is not None:
            if nts < ots:
                return True
            if nts > ots:
                return False
        # Same/unknown timestamp: coordinates provide the tie-breaker.
        if obi >= 0 and nbi >= 0:
            if nbi < obi:
                return True
            if nbi == obi and omi >= 0 and nmi >= 0 and nmi < omi:
                return True
        return False

    @classmethod
    def _prefer_snapshot(cls, old: dict | None, new: dict | None, *, force: bool = False) -> dict:
        old_row = dict(old) if isinstance(old, dict) else {}
        new_row = dict(new) if isinstance(new, dict) else {}
        if not old_row:
            return new_row
        if not new_row:
            return old_row
        if not force and cls._snapshot_is_regression(old_row, new_row):
            return old_row
        return new_row

    @staticmethod
    def _snapshot_is_suspicious_first_bar(snapshot: dict | None) -> bool:
        if not isinstance(snapshot, dict) or not bool(snapshot.get("played", False)):
            return False
        try:
            return int(snapshot.get("bar_index") or 0) == 0 and int(snapshot.get("minute_index") or 0) == 0
        except Exception:
            return False

    def _capture_current_session(self, *, force: bool = False):
        if not self.symbol:
            return
        sess = self._session_for(self.source, self.symbol, create=True)
        if sess is None:
            return
        sess["exchange"] = self._exchange_for_symbol(self.symbol) or self.exchange_combo.currentText()
        if self.panels:
            sess["period"] = self.panels[0].period.currentText()
            sess["intraday"] = self.panels[0].period.is_intraday()
            sess["contract"] = self.panels[0].contract.currentText()
        # Never let a transient UI switch overwrite this product with another
        # product's engine.  This was the intermittent cause of a background
        # product returning to its original/old candle after some switches.
        expected_key = str(sess.get("key", ""))
        owns_engine = (
            self.engine is not None
            and (self._visible_engine_key == expected_key or sess.get("engine") is self.engine)
        )
        if owns_engine:
            try:
                st = self.engine.current()
                candidate = dict(sess)
                candidate.update({
                    "engine": self.engine,
                    "timestamp": str(st.timestamp),
                    "trading_day": str(st.trading_day),
                    "bar_index": int(self.engine.bar_index),
                    "minute_index": int(self.engine.minute_index),
                })
                saved = self._saved_replay_sessions.get(expected_key)
                if not force and self._snapshot_is_regression(saved, candidate):
                    if self._restore_engine_snapshot(self.engine, saved):
                        st = self.engine.current()
                        candidate.update({
                            "timestamp": str(st.timestamp),
                            "trading_day": str(st.trading_day),
                            "bar_index": int(self.engine.bar_index),
                            "minute_index": int(self.engine.minute_index),
                        })
                    else:
                        candidate = dict(saved or candidate)
                        candidate["engine"] = self.engine
                sess.update({
                    "engine": self.engine,
                    "timestamp": candidate.get("timestamp", ""),
                    "trading_day": candidate.get("trading_day", ""),
                    "bar_index": candidate.get("bar_index"),
                    "minute_index": candidate.get("minute_index"),
                })
                price, pct = self._session_price_pct(sess)
                sess["price"] = price
                sess["change_pct"] = pct
            except Exception:
                pass
        self._update_saved_session_shadow(sess, force=force)

    def _serialised_replay_sessions(self) -> dict[str, dict]:
        self._capture_current_session()
        out = {str(k): dict(v) for k, v in self._saved_replay_sessions.items() if isinstance(v, dict)}
        for key, sess in self._replay_sessions.items():
            self._sync_session_from_engine(sess)
            row = self._compact_session_snapshot(sess)
            row["played"] = bool(sess.get("played", key in self._played_order))
            # Never let serialization itself re-introduce a passive regression.
            # v48 overwrote the protected shadow here with a live engine that had
            # accidentally been rebuilt at bar 0; AP/AD exposed exactly this path.
            out[str(key)] = self._prefer_snapshot(out.get(str(key)), row, force=False)
            self._saved_replay_sessions[str(key)] = dict(out[str(key)])
        return out

    def _checkpoint_replay_heads(self, sessions: dict[str, dict] | None = None) -> None:
        """Persist only replay locations, independently from the full UI state."""
        try:
            payload = sessions if sessions is not None else self._serialised_replay_sessions()
            save_replay_heads(payload)
        except Exception:
            pass


    def _current_session_playing(self) -> bool:
        sess = self._session_for(create=False)
        return bool(sess and sess.get("playing"))

    def _any_session_playing(self) -> bool:
        return any(bool(s.get("playing")) and s.get("engine") is not None for s in self._replay_sessions.values())

    def _update_play_button(self):
        self.btn_play.setText("暂停" if self._current_session_playing() else "播放")
        if hasattr(self, "btn_all_play"):
            self.btn_all_play.setText("全播放" if self._all_pause_active else "全部暂停")

    def _toggle_all_playback(self, *_args):
        """Pause every product currently playing, then resume exactly that set.

        The remembered set is runtime-only by design: restarting the program never
        auto-resumes playback.  A product that was already paused before clicking
        "全部暂停" is not included and therefore stays paused when "全播放" is
        clicked.
        """
        if not self._all_pause_active:
            keys = [
                str(key) for key, sess in self._replay_sessions.items()
                if bool(sess.get("playing")) and sess.get("engine") is not None
            ]
            if not keys:
                self.statusBar().showMessage("当前没有正在播放的品种。", 3000)
                self._update_play_button()
                return

            self._all_pause_resume_keys = keys
            self._all_pause_active = True
            for key in keys:
                sess = self._replay_sessions.get(key)
                if not sess:
                    continue
                sess["playing"] = False
                self._sync_session_from_engine(sess)
                self._update_saved_session_shadow(sess, force=False)

            self.play_timer.stop()
            self._play_last_clock = None
            self._play_budget_ms = 0.0
            self._checkpoint_replay_heads()
            self._update_play_button()
            self._refresh_main_list()
            self._schedule_save()
            self.statusBar().showMessage(f"已暂停 {len(keys)} 个正在播放的品种；再次点击“全播放”可恢复。", 4000)
            return

        resume_keys = list(self._all_pause_resume_keys)
        resumed = 0
        for key in resume_keys:
            sess = self._replay_sessions.get(key)
            if not sess:
                continue
            engine = sess.get("engine")
            if engine is None:
                continue
            try:
                if engine.at_end:
                    sess["playing"] = False
                    continue
            except Exception:
                continue
            sess["playing"] = True
            sess["played"] = True
            if key not in self._played_order:
                self._played_order.append(key)
            self._sync_session_from_engine(sess)
            resumed += 1

        self._all_pause_active = False
        self._all_pause_resume_keys = []
        if resumed:
            self._play_last_clock = time.monotonic()
            self._play_budget_ms = 0.0
            if not self.play_timer.isActive():
                self.play_timer.start(self._play_timer_interval_ms())
            else:
                self.play_timer.setInterval(self._play_timer_interval_ms())
        elif not self._any_session_playing():
            self.play_timer.stop()

        self._checkpoint_replay_heads()
        self._update_play_button()
        self._refresh_main_list()
        self._schedule_save()
        if resumed:
            self.statusBar().showMessage(f"已恢复刚才暂停的 {resumed} 个品种。", 4000)
        else:
            self.statusBar().showMessage("刚才暂停的品种当前没有可继续播放的行情。", 4000)

    def _pause_all_sessions(self):
        # Internal hard pause used by data-directory/account/reset operations.
        # Unlike the toolbar group toggle, these operations must not retain a
        # later resume set.
        self._all_pause_active = False
        self._all_pause_resume_keys = []
        for sess in self._replay_sessions.values():
            sess["playing"] = False
        self.play_timer.stop()
        self._play_last_clock = None
        self._play_budget_ms = 0.0
        self._update_play_button()
        self._refresh_main_list()

    @staticmethod
    def _header_state(table: QTableWidget) -> dict:
        header = table.horizontalHeader()
        count = table.columnCount()
        try:
            sort_column = int(header.sortIndicatorSection())
            sort_order = "desc" if header.sortIndicatorOrder() == Qt.SortOrder.DescendingOrder else "asc"
        except Exception:
            sort_column, sort_order = 0, "asc"
        return {
            "visual_order": [int(header.logicalIndex(v)) for v in range(count)],
            "widths": [int(table.columnWidth(i)) for i in range(count)],
            "sort_column": sort_column,
            "sort_order": sort_order,
        }

    @staticmethod
    def _restore_header_state(table: QTableWidget, state) -> None:
        if not isinstance(state, dict):
            return
        widths = state.get("widths", [])
        if isinstance(widths, (list, tuple)):
            for i, width in enumerate(widths[:table.columnCount()]):
                try:
                    table.setColumnWidth(i, max(24, int(width)))
                except Exception:
                    pass
        order = state.get("visual_order", [])
        if isinstance(order, (list, tuple)):
            header = table.horizontalHeader()
            desired = []
            for x in order:
                try:
                    ix = int(x)
                    if 0 <= ix < table.columnCount() and ix not in desired:
                        desired.append(ix)
                except Exception:
                    pass
            desired += [i for i in range(table.columnCount()) if i not in desired]
            for visual, logical in enumerate(desired):
                current_visual = header.visualIndex(logical)
                if current_visual >= 0 and current_visual != visual:
                    header.moveSection(current_visual, visual)
        try:
            sort_column = int(state.get("sort_column", table.horizontalHeader().sortIndicatorSection()))
            if 0 <= sort_column < table.columnCount():
                raw_order = str(state.get("sort_order", "asc")).lower()
                sort_order = Qt.SortOrder.DescendingOrder if raw_order.startswith("desc") else Qt.SortOrder.AscendingOrder
                table.horizontalHeader().setSortIndicator(sort_column, sort_order)
                if table.isSortingEnabled():
                    table.sortItems(sort_column, sort_order)
        except Exception:
            pass

    @staticmethod
    def _preferred_main_sequence(sequences: list[str]) -> str:
        if not sequences:
            return ""
        priorities = ("主连", "主力连续", "主力", "main", "continuous")
        lowered = {str(x).lower(): str(x) for x in sequences}
        for name in priorities:
            if name.lower() in lowered:
                return lowered[name.lower()]
        for seq in sequences:
            text = str(seq)
            low = text.lower()
            if "主连" in text or "主力" in text or "main" in low:
                return text
        return str(sequences[0])

    def _rebuild_main_list_rows(self):
        rows: dict[str, int] = {}
        if hasattr(self, "main_list"):
            for row in range(self.main_list.rowCount()):
                item = self.main_list.item(row, 1)
                if item is None:
                    continue
                symbol = str(item.data(Qt.ItemDataRole.UserRole) or item.text()).strip()
                if symbol:
                    rows[symbol] = row
        self._main_list_rows = rows
        return rows

    def _selected_main_symbols(self) -> list[str]:
        """Return selected main-continuous symbols in visible row order."""
        if not hasattr(self, "main_list"):
            return []
        selected_rows = sorted({idx.row() for idx in self.main_list.selectionModel().selectedRows()})
        symbols: list[str] = []
        for row in selected_rows:
            item = self.main_list.item(int(row), 1)
            if item is None:
                continue
            symbol = str(item.data(Qt.ItemDataRole.UserRole) or item.text()).strip()
            if symbol and symbol not in symbols:
                symbols.append(symbol)
        return symbols

    def _restore_main_list_selection(self, symbols: list[str] | tuple[str, ...] | None) -> None:
        """Restore row selection by symbol, robust to sorting and row movement."""
        if not hasattr(self, "main_list"):
            return
        wanted = [str(x) for x in (symbols or []) if str(x)]
        rows = self._rebuild_main_list_rows()
        self.main_list.blockSignals(True)
        try:
            self.main_list.clearSelection()
            for symbol in wanted:
                row = rows.get(symbol)
                if row is None:
                    continue
                for col in range(self.main_list.columnCount()):
                    item = self.main_list.item(int(row), col)
                    if item is not None:
                        item.setSelected(True)
        finally:
            self.main_list.blockSignals(False)

    def _main_list_symbol_order(self) -> list[str]:
        order: list[str] = []
        if not hasattr(self, "main_list"):
            return order
        for row in range(self.main_list.rowCount()):
            item = self.main_list.item(row, 1)
            if item is None:
                continue
            symbol = str(item.data(Qt.ItemDataRole.UserRole) or item.text()).strip()
            if symbol and symbol not in order:
                order.append(symbol)
        return order

    def _apply_main_list_tie_order(self, symbol_order: list[str] | tuple[str, ...] | None = None) -> None:
        """Attach a deterministic secondary rank to every cell in each row.

        ``symbol_order`` is the exact visual order saved on shutdown.  When it is
        omitted, the current visual order becomes the new stable order after the
        user clicks a header.
        """
        if not hasattr(self, "main_list"):
            return
        current = self._main_list_symbol_order()
        requested = [str(x) for x in (symbol_order or []) if str(x)]
        ordered = []
        for symbol in requested + current:
            if symbol not in ordered:
                ordered.append(symbol)
        rank = {symbol: i for i, symbol in enumerate(ordered)}
        descending = self.main_list.horizontalHeader().sortIndicatorOrder() == Qt.SortOrder.DescendingOrder
        sign = -1 if descending else 1
        was_sorting = self.main_list.isSortingEnabled()
        if was_sorting:
            self.main_list.setSortingEnabled(False)
        try:
            for row in range(self.main_list.rowCount()):
                symbol_item = self.main_list.item(row, 1)
                if symbol_item is None:
                    continue
                symbol = str(symbol_item.data(Qt.ItemDataRole.UserRole) or symbol_item.text()).strip()
                tie = sign * int(rank.get(symbol, len(rank) + row))
                for col in range(self.main_list.columnCount()):
                    item = self.main_list.item(row, col)
                    if item is not None:
                        item.setData(_MAIN_LIST_TIE_ROLE, tie)
        finally:
            if was_sorting:
                self.main_list.setSortingEnabled(True)

    def _persist_main_list_sort(self) -> None:
        """Persist the current header sort and exact visible symbol order now.

        Do not rely on the normal 800-ms whole-session save for this preference.
        Header sorting is a lightweight UI action and may be followed immediately
        by exit/startup work that rebuilds the table.  Writing the tiny dedicated
        sort file here makes the user's order deterministic across restarts.
        """
        if not hasattr(self, "main_list"):
            return
        try:
            header = self.main_list.horizontalHeader()
            column = int(header.sortIndicatorSection())
            direction = "desc" if header.sortIndicatorOrder() == Qt.SortOrder.DescendingOrder else "asc"
            order = self._main_list_symbol_order()
            pref = {
                "sort_column": column,
                "sort_order": direction,
                "symbol_order": list(order),
            }
            self._main_list_sort_pref = pref
            save_main_list_sort(column, direction, order)
        except Exception:
            # Sorting must never make the application unusable merely because a
            # preference file could not be written.  The normal state save below
            # still provides a fallback copy.
            pass

    def _finish_main_list_sort(self):
        # Capture the row order only after QTableWidget has physically completed
        # the sort triggered by the header click.  That exact visual order is the
        # stable secondary order for rows whose primary values later become equal
        # (for example, "播放中" becomes "已播放" after a restart).
        self._apply_main_list_tie_order()
        self._rebuild_main_list_rows()
        self._persist_main_list_sort()
        self._schedule_save()

    def _main_list_sort_changed(self, _column: int, _order):
        # A zero-delay callback can still run before all queued table/layout work
        # has settled on some PySide6/Windows builds.  A short delay is cheap and
        # ensures we persist the order the user actually sees.
        QTimer.singleShot(80, self._finish_main_list_sort)

    @staticmethod
    def _set_main_list_sort_key(item: QTableWidgetItem | None, value) -> None:
        if item is not None:
            item.setData(_MAIN_LIST_SORT_ROLE, value)

    def _populate_main_list(self):
        if not hasattr(self, "main_list"):
            return
        try:
            items = self.repo.list_instruments("主力连续") if self.repo else []
        except Exception:
            items = []
        self._main_items = items
        self._main_list_rows = {}
        was_sorting = self.main_list.isSortingEnabled()
        self.main_list.setSortingEnabled(False)
        self.main_list.blockSignals(True)
        self.main_list.setRowCount(len(items))
        for row, meta in enumerate(items):
            symbol = str(meta.symbol)
            values = [str(row + 1), symbol, str(meta.name or ""), str(meta.exchange or ""), "", "", "", ""]
            sort_values = [row + 1, symbol.casefold(), str(meta.name or "").casefold(), str(meta.exchange or "").casefold(), None, None, "", ""]
            for col, value in enumerate(values):
                item = MainListItem(value)
                item.setData(_MAIN_LIST_SORT_ROLE, sort_values[col])
                if col == 1:
                    item.setData(Qt.ItemDataRole.UserRole, symbol)
                if col in (0, 4, 5):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.main_list.setItem(row, col, item)
        self.main_list.blockSignals(False)
        # Apply the exact order saved at the previous shutdown as the secondary
        # key before the first startup sort.  This prevents equal status/quote
        # values from falling back to the natural A, AD, AG... load order.
        restored_order = getattr(self, "_restore_main_list_symbol_order", None)
        if not restored_order:
            pref_order = self._main_list_sort_pref.get("symbol_order", []) if isinstance(self._main_list_sort_pref, dict) else []
            if isinstance(pref_order, (list, tuple)):
                restored_order = [str(x) for x in pref_order if str(x)]
        self._apply_main_list_tie_order(restored_order)
        self.main_list.setSortingEnabled(was_sorting)
        if was_sorting:
            header = self.main_list.horizontalHeader()
            section = int(header.sortIndicatorSection())
            if 0 <= section < self.main_list.columnCount():
                self.main_list.sortItems(section, header.sortIndicatorOrder())
        if restored_order:
            self._restore_main_list_symbol_order = []
        self._rebuild_main_list_rows()
        if items:
            self._main_quote_queue = [
                str(x.symbol) for x in items
                if str(x.symbol) not in self._main_quote_cache
                and not self._saved_replay_sessions.get(self._replay_session_key("主力连续", str(x.symbol)), {}).get("price")
            ]
            self._schedule_main_quote_warmup(1200)
        self._refresh_main_list()

    def _schedule_main_quote_warmup(self, delay_ms: int = 800):
        self._main_quote_timer.stop()
        QTimer.singleShot(max(0, int(delay_ms)), self._start_main_quote_warmup)

    def _start_main_quote_warmup(self):
        if not self._main_quote_queue or self._market_load_busy or self._any_session_playing():
            return
        if not self._main_quote_timer.isActive():
            self._main_quote_timer.start()

    def _load_main_quote_batch(self):
        if not self.repo or not self._main_quote_queue:
            self._main_quote_timer.stop()
            return
        # Never compete with the visible product load or replay. Disk scanning of
        # watch-list quotes is background decoration, not a prerequisite for Play.
        if self._market_load_busy or self._any_session_playing():
            self._main_quote_timer.stop()
            return
        symbol = self._main_quote_queue.pop(0)
        session = self._session_for("主力连续", symbol, create=False)
        if not (session and session.get("engine") is not None):
            try:
                seq = self._preferred_main_sequence(self.repo.list_contracts(symbol, "1d", "主力连续"))
                if seq:
                    bars = self.repo.load_bars("主力连续", symbol, "1d", seq)
                    if not bars.empty:
                        close = float(bars.iloc[-1]["close"])
                        prev = float(bars.iloc[-2]["close"]) if len(bars) >= 2 else float(bars.iloc[-1]["open"])
                        pct = ((close / prev) - 1.0) * 100.0 if prev else 0.0
                        self._main_quote_cache[symbol] = (close, pct)
            except Exception:
                self._main_quote_cache[symbol] = (None, None)
        self._refresh_main_list()
        if not self._main_quote_queue:
            self._main_quote_timer.stop()

    def _session_price_pct(self, sess) -> tuple[float | None, float | None]:
        if not sess:
            return None, None
        engine = sess.get("engine")
        if engine is None:
            return None, None
        try:
            st = engine.current()
            close = float(st.partial["close"])
            if engine.bar_index > 0:
                prev = float(engine.bars.iloc[engine.bar_index - 1]["close"])
            else:
                prev = float(engine.bars.iloc[0]["open"])
            pct = ((close / prev) - 1.0) * 100.0 if prev else 0.0
            return close, pct
        except Exception:
            return None, None

    def _legacy_snapshot_at_end(self, symbol: str, saved: dict | None) -> bool:
        """Infer EOF for older saved sessions that do not yet store ``at_end``."""
        if not isinstance(saved, dict) or "at_end" in saved:
            return bool(saved.get("at_end", False)) if isinstance(saved, dict) else False
        if not bool(saved.get("played", False)):
            return False
        timestamp = str(saved.get("timestamp", "") or "")
        if not timestamp or self.repo is None:
            return False
        contract = str(saved.get("contract", "") or "")
        try:
            if not contract:
                contracts = self.repo.list_contracts(str(symbol), "1min", "主力连续")
                contract = self._preferred_main_sequence(contracts)
            if not contract:
                return False
            path = self.repo._resolve_file("主力连续", str(symbol), "1min", contract)
            if not path.exists():
                return False
            stat = path.stat()
            cache = getattr(self, "_legacy_end_status_cache", {})
            cache_key = (str(path), stat.st_mtime_ns, stat.st_size, timestamp)
            if cache_key in cache:
                return bool(cache[cache_key])
            latest_frame = pd.read_parquet(path, columns=["datetime"])
            latest = pd.to_datetime(latest_frame["datetime"], errors="coerce").dropna().max()
            result = False if pd.isna(latest) else snapshot_timestamp_matches_latest(timestamp, str(latest), 90.0)
            cache[cache_key] = bool(result)
            self._legacy_end_status_cache = cache
            return bool(result)
        except Exception:
            return False

    def _invalidate_replay_session_engine(self, source: str, symbol: str) -> None:
        """Drop a cached ReplayEngine after local bars are changed on disk.

        Keep the replay head and its EOF flag in the persisted snapshot.  The
        next load will construct a fresh engine from the updated Parquet files.
        """
        key = self._replay_session_key(source, symbol)
        sess = self._replay_sessions.get(key)
        if sess is not None:
            engine = sess.get("engine")
            if engine is not None:
                self._sync_session_from_engine(sess, force=True)
            sess["engine"] = None
            self._update_saved_session_shadow(sess, force=True)
        if str(getattr(self, "_visible_engine_key", "")) == key:
            self.engine = None

    def _refresh_main_list(self):
        if not hasattr(self, "main_list"):
            return
        selected_symbols = self._selected_main_symbols()
        held = set()
        for key in self.account.positions.keys():
            try:
                _src, product, _contract = TradingAccount.split_trade_key(key)
                held.add(str(product))
            except Exception:
                pass

        # Updating the currently sorted column can move a QTableWidget row
        # immediately.  Temporarily suspend sorting so every symbol is updated
        # atomically, then reapply the user's selected sort order.
        was_sorting = self.main_list.isSortingEnabled()
        header = self.main_list.horizontalHeader()
        sort_column = int(header.sortIndicatorSection())
        sort_order = header.sortIndicatorOrder()
        if was_sorting:
            self.main_list.setSortingEnabled(False)
        rows = self._rebuild_main_list_rows()
        self.main_list.blockSignals(True)
        for symbol, row in rows.items():
            skey = self._replay_session_key("主力连续", symbol)
            sess = self._replay_sessions.get(skey)
            saved = self._saved_replay_sessions.get(skey, {})
            price, pct = self._session_price_pct(sess)
            if price is None:
                holder = sess or saved
                try:
                    price = None if holder.get("price") is None else float(holder.get("price"))
                    pct = None if holder.get("change_pct") is None else float(holder.get("change_pct"))
                except Exception:
                    price, pct = None, None
            if price is None:
                price, pct = self._main_quote_cache.get(symbol, (None, None))

            price_item = self.main_list.item(row, 4)
            if price_item is not None:
                price_item.setText("" if price is None else f"{price:.4f}".rstrip("0").rstrip("."))
                self._set_main_list_sort_key(price_item, None if price is None else float(price))
            pct_item = self.main_list.item(row, 5)
            if pct_item is not None:
                pct_item.setText("" if pct is None else f"{pct:+.2f}%")
                self._set_main_list_sort_key(pct_item, None if pct is None else float(pct))
                if pct is not None:
                    pct_item.setForeground(QBrush(QColor("#c62828" if pct > 0 else "#16803a" if pct < 0 else "#444444")))

            timestamp = ""
            if sess:
                engine = sess.get("engine")
                if engine is not None:
                    try:
                        timestamp = str(engine.current().timestamp)
                    except Exception:
                        timestamp = str(sess.get("timestamp", ""))
                else:
                    timestamp = str(sess.get("timestamp", ""))
            if not timestamp:
                timestamp = str(saved.get("timestamp", ""))
            time_text = timestamp[:19].replace("T", " ") if timestamp else ""
            time_item = self.main_list.item(row, 6)
            if time_item is not None:
                time_item.setText(time_text)
                self._set_main_list_sort_key(time_item, time_text)

            playing = bool(sess and sess.get("playing"))
            engine = sess.get("engine") if sess else None
            if engine is not None:
                at_end = bool(getattr(engine, "at_end", False))
            elif "at_end" in saved:
                at_end = bool(saved.get("at_end", False))
            else:
                # Migrate older state files: if the saved replay head is exactly
                # the last real minute, display "结束" before rebuilding its engine.
                at_end = self._legacy_snapshot_at_end(symbol, saved)
                if at_end:
                    saved = dict(saved)
                    saved["at_end"] = True
                    self._saved_replay_sessions[skey] = saved
                    if sess is not None:
                        sess["at_end"] = True
                        sess["playing"] = False
                        self._update_saved_session_shadow(sess, force=True)
            if at_end and sess is not None:
                sess["playing"] = False
                sess["played"] = True
                sess["at_end"] = True
                self._update_saved_session_shadow(sess, force=True)
            played = bool(skey in self._played_order or (sess or saved).get("played", False))
            status = replay_status(at_end, playing, played)
            status_item = self.main_list.item(row, 7)
            if status_item is not None:
                status_item.setText(status)
                self._set_main_list_sort_key(status_item, status)

            bg = QBrush(QColor("#fff0b3")) if symbol in held else QBrush()
            for col in range(self.main_list.columnCount()):
                item = self.main_list.item(row, col)
                if item is not None:
                    item.setBackground(bg)
        self.main_list.blockSignals(False)

        if was_sorting:
            self.main_list.setSortingEnabled(True)
            if 0 <= sort_column < self.main_list.columnCount():
                self.main_list.sortItems(sort_column, sort_order)
        self._rebuild_main_list_rows()

        # Preserve the user's multi-selection across value refreshes and sorting.
        # Only fall back to the visible product when there was no explicit row
        # selection at all.
        if selected_symbols:
            self._restore_main_list_selection(selected_symbols)
        elif self.symbol in self._main_list_rows and self.source_combo.currentText() == "主力连续":
            self._restore_main_list_selection([self.symbol])

    def _main_list_clicked(self, row: int, _column: int):
        item = self.main_list.item(int(row), 1)
        if item is None:
            return
        symbol = str(item.data(Qt.ItemDataRole.UserRole) or item.text()).strip()
        if not symbol:
            return
        self._capture_current_session()
        exchange = self.main_list.item(int(row), 3).text().strip() if self.main_list.item(int(row), 3) else ""
        if not exchange:
            for meta in getattr(self, "_main_items", []) or []:
                if str(meta.symbol) == symbol:
                    exchange = str(meta.exchange or "")
                    break
        # Main-list activation always means the main-continuous data source.
        self._restore_symbol = symbol
        self._restore_exchange = exchange
        if self.source_combo.currentText() != "主力连续":
            self.source_combo.blockSignals(True)
            self.source_combo.setCurrentText("主力连续")
            self.source_combo.blockSignals(False)
            self.symbol = ""
            self.source = "主力连续"
            self._load_instruments()
        else:
            self._sync_exchange_and_symbol_list(symbol)
            idx = self.symbol_combo.findData(symbol)
            if idx >= 0:
                self.symbol_combo.blockSignals(True)
                self.symbol_combo.setCurrentIndex(idx)
                self.symbol_combo.blockSignals(False)
            self.symbol = symbol
            self._load_symbol()
        self._update_play_button()
        self._refresh_main_list()
        self._schedule_save()

    def _position_symbol_clicked(self, trade_key: str):
        """Open the chart belonging to a clicked live position.

        A position stores the exact ``source || product || contract`` identity.
        Navigation therefore follows the held position rather than merely
        changing the highlighted row: the data source, exchange, product,
        contract and that product's independent replay clock are restored.
        A background-playing product keeps its running ReplayEngine and is shown
        at its current head when possible.
        """
        key = str(trade_key or "").strip()
        if not key:
            return
        source, symbol, contract = TradingAccount.split_trade_key(key)
        symbol = str(symbol or "").strip()
        if not symbol:
            return
        source = str(source or self.source_combo.currentText() or "主力连续").strip()
        contract = str(contract or "").strip()

        # Save the market currently on screen before changing context.  If it is
        # playing, it continues as an independent background replay session.
        self._capture_current_session()

        # _load_symbol normally prefers the contract saved in the product
        # session.  For a position-table activation the held contract is more
        # specific, so pass it as a one-shot override.  _load_symbol consumes it
        # before contracts are populated; the existing replay timestamp remains
        # available for restoration if a new engine must be created.
        self._position_target_context = (source, symbol, contract)
        self._restore_symbol = symbol
        self._restore_exchange = ""

        if self.source_combo.findText(source) >= 0 and self.source_combo.currentText() != source:
            self.source_combo.blockSignals(True)
            self.source_combo.setCurrentText(source)
            self.source_combo.blockSignals(False)
            self.source = source
            self.symbol = ""
            self._load_instruments()
        else:
            # Same source: switch instrument without routing through
            # _source_changed, exactly like the main-list navigation path.
            self.source = self.source_combo.currentText()
            self._sync_exchange_and_symbol_list(symbol)
            idx = self.symbol_combo.findData(symbol)
            if idx >= 0:
                self.symbol_combo.blockSignals(True)
                self.symbol_combo.setCurrentIndex(idx)
                self.symbol_combo.blockSignals(False)
            self.symbol = symbol
            self._load_symbol()

        self._update_play_button()
        self._refresh_main_list()
        self._schedule_save()

    def _trade_record_symbol_clicked(self, identity_or_label: str):
        """Open the chart associated with a clicked fill record.

        New records carry the exact trade key. Older CSV records only contain a
        display label, so resolve those against the available source instrument
        metadata and, when present, the contract suffix.
        """
        identity = str(identity_or_label or "").strip()
        if not identity or not self.repo:
            return

        # Current-format rows store source||product||contract and can use the
        # same navigation path as a clicked holding.
        source, symbol, contract = TradingAccount.split_trade_key(identity)
        if source and symbol:
            self._position_symbol_clicked(identity)
            return

        # Backward compatibility for fills written before trade_key was stored.
        label = identity
        base_label = label
        contract_hint = ""
        if " · " in label:
            base_label, contract_hint = label.rsplit(" · ", 1)
            base_label = base_label.strip()
            contract_hint = contract_hint.strip()

        current_source = str(self.source_combo.currentText() or "原始行情")
        sources = []
        for candidate_source in (current_source, "原始行情", "主力连续"):
            if candidate_source and candidate_source not in sources:
                sources.append(candidate_source)

        candidates = []
        period = self.panels[0].period.currentText() if self.panels else "1d"
        for candidate_source in sources:
            try:
                items = self.repo.list_instruments(candidate_source)
            except Exception:
                continue
            for meta in items:
                display = str(getattr(meta, "display", "") or "").strip()
                code = str(getattr(meta, "symbol", "") or "").strip()
                if base_label.casefold() not in (display.casefold(), code.casefold()):
                    continue
                try:
                    contracts = self.repo.list_contracts(code, period, candidate_source)
                except Exception:
                    contracts = []
                exact_contract = bool(contract_hint and contract_hint in contracts)
                candidates.append({
                    "source": candidate_source,
                    "symbol": code,
                    "contract": contract_hint if exact_contract else "",
                    "exchange": str(getattr(meta, "exchange", "") or ""),
                    "exact_contract": exact_contract,
                })

        if not candidates:
            self.statusBar().showMessage(
                f"无法从成交记录定位品种“{label}”，请检查该品种的数据是否仍存在。",
                6000,
            )
            return

        # A contract suffix is stronger evidence than the current source. This
        # selects raw-contract data when an old record label identifies it.
        if contract_hint:
            exact = [item for item in candidates if item["exact_contract"]]
            if exact:
                candidates = exact

        chosen = next(
            (item for item in candidates if item["source"] == current_source),
            candidates[0],
        )
        if contract_hint and not chosen["exact_contract"]:
            # The old label cannot prove which contract series was used; do not
            # force a contract that is absent from the selected source.
            chosen = dict(chosen)
            chosen["contract"] = ""

        key = TradingAccount.make_trade_key(
            chosen["source"], chosen["symbol"], chosen["contract"]
        )
        self._position_symbol_clicked(key)

    def _current_trade_key(self) -> str:
        """Identity of the currently displayed tradable contract/sequence."""
        contract = ""
        if self.panels:
            contract = self.panels[0].contract.currentText().strip()
        if not self.symbol:
            return ""
        return TradingAccount.make_trade_key(self.source_combo.currentText(), self.symbol, contract)

    def _trade_key_label(self, key: str) -> str:
        _source, code, contract = TradingAccount.split_trade_key(key)
        base = code
        for meta in getattr(self, "_all_items", []) or []:
            if str(getattr(meta, "symbol", "")) == code:
                base = str(getattr(meta, "display", "") or code).strip() or code
                break
        if contract and contract != code and contract not in base:
            return f"{base} · {contract}"
        return base

    def _migrate_legacy_current_trade_key(self):
        """Upgrade old symbol-only saves once the current contract is known."""
        key = self._current_trade_key()
        if key and self.symbol and key != self.symbol:
            self.account.migrate_symbol_key(self.symbol, key)

    def _has_live_trading_state(self) -> bool:
        return bool(self.account.positions or self.account.orders)

    def _guard_history_jump(self, action: str = "跳转") -> bool:
        """Manual replay-clock jumps are always allowed and never show a dialog."""
        return True

    def _rebase_trade_clock_after_history_jump(self):
        """Rebase matching clocks after a user-driven historical jump.

        Without this rebasing, a forward jump could cause the next replay tick to
        consume every skipped 1-minute bar and accidentally fill an order/TP-SL
        inside an interval the user intentionally skipped.  A backward jump would
        likewise retain a future processed timestamp and block new matching.
        """
        if self.engine is None or not self.symbol:
            return
        try:
            state = self.engine.current()
            target_ts = pd.Timestamp(state.timestamp)
        except Exception:
            return

        source = self.source_combo.currentText()
        current_key = self._current_trade_key()
        matching_keys = []
        for key in sorted(self._active_trade_keys()):
            src, product, _contract = TradingAccount.split_trade_key(key)
            if src == source and product == self.symbol:
                matching_keys.append(key)

        for key in matching_keys:
            # The jump itself never executes skipped bars.  Start future matching
            # strictly after the selected historical clock.
            self._market_processed_ts[key] = target_ts

            # Keep floating P/L aligned with the selected clock without executing
            # any order.  The visible contract has an exact replay close; older
            # held contracts use the nearest real minute at/before the new clock.
            try:
                if key == current_key:
                    self.account.set_mark(key, float(state.partial["close"]))
                else:
                    rows = self._sync_minute_rows(key, None, target_ts)
                    if rows:
                        self.account.set_mark(key, float(rows[-1][1]["close"]))
            except Exception:
                pass

        sess = self._session_for(create=True)
        if sess is not None:
            sess.update({
                "engine": self.engine,
                "timestamp": str(state.timestamp),
                "trading_day": str(state.trading_day),
                "bar_index": int(self.engine.bar_index),
                "minute_index": int(self.engine.minute_index),
            })

    def _mark_current_trade_clock(self):
        if self.engine is None:
            return
        key = self._current_trade_key()
        if key:
            self._market_processed_ts[key] = pd.Timestamp(self.engine.current().timestamp)

    def _active_trade_keys(self) -> set[str]:
        keys = set(self.account.positions.keys())
        keys.update(str(o.symbol) for o in self.account.orders)
        return {k for k in keys if k}

    def _sync_minute_rows(self, key: str, after: pd.Timestamp | None, upto: pd.Timestamp):
        """Return 1-minute bars in ``(after, upto]`` for a held contract."""
        if self.repo is None:
            return []
        source, symbol, contract = TradingAccount.split_trade_key(key)
        if not source or not symbol or not contract:
            return []
        try:
            cached = self._sync_minute_cache.get(key)
            if cached is None:
                df = self.repo.load_minutes(source, symbol, contract)
                times = df["datetime"].to_numpy(dtype="datetime64[ns]")
                cached = (df, times)
                self._sync_minute_cache[key] = cached
            df, times = cached
            hi_ts = np.datetime64(pd.Timestamp(upto))
            hi = int(np.searchsorted(times, hi_ts, side="right"))
            if after is None:
                lo = max(0, hi - 1)
            else:
                lo_ts = np.datetime64(pd.Timestamp(after))
                lo = int(np.searchsorted(times, lo_ts, side="right"))
            out = []
            for idx in range(lo, hi):
                row = df.iloc[idx]
                out.append((pd.Timestamp(row["datetime"]), {
                    "open": float(row["open"]), "high": float(row["high"]),
                    "low": float(row["low"]), "close": float(row["close"]),
                }))
            return out
        except Exception:
            return []

    def _apply_market_bar(self, key: str, timestamp: pd.Timestamp, b: dict):
        # Capture TP/SL state before matching. process_bar removes the position
        # after a stop/take-profit fill, so the trigger identity must be known
        # before the account mutates. Stop-loss keeps precedence when both are
        # touched in the same real minute, matching TradingAccount.process_bar.
        p0 = self.account.position_for(key)
        trigger_kind = None
        if p0 is not None:
            take0 = p0.take_profit
            stop0 = p0.stop_loss
            if p0.side == Side.LONG:
                stop_hit = stop0 is not None and float(b["low"]) <= float(stop0)
                take_hit = take0 is not None and float(b["high"]) >= float(take0)
            else:
                stop_hit = stop0 is not None and float(b["high"]) >= float(stop0)
                take_hit = take0 is not None and float(b["low"]) <= float(take0)
            if stop_hit:
                trigger_kind = "止损"
            elif take_hit:
                trigger_kind = "止盈"

        before = {o.id: o.status for o in self.account.orders if o.symbol == key}
        events = self.account.process_bar(key, b["open"], b["high"], b["low"], b["close"])
        after = {o.id: o for o in self.account.orders}
        for oid, old_status in before.items():
            if oid not in after:
                filled = any(f"限价单#{oid}成交" in str(ev.message) for ev in events)
                self.records.update_order_status(oid, "成交" if filled else "已拒绝")
            else:
                new_status = after[oid].status
                if new_status != old_status:
                    self.records.update_order_status(oid, new_status)

        # TP/SL rows represent only currently-live conditional instructions.
        # Once one side triggers, the actual fill is kept in 成交记录 and both
        # conditional rows disappear from 委托记录 (no cancelled-history row).
        p_after = self.account.position_for(key)
        if trigger_kind and p_after is None:
            self.records.remove_conditionals_for_trade_key(key)
        else:
            # Limit fills can also add to, reduce, flatten or reverse an existing
            # position. Keep conditional rows consistent with the resulting state.
            self._reconcile_conditionals_after_position_change(key)

        for ev in events:
            self.statusBar().showMessage(ev.message, 7000)
            if ev.qty > 0:
                self._record_fill(ev, key)
        self._market_processed_ts[key] = pd.Timestamp(timestamp)

    def _process_replay_session_market(self, sess: dict, state):
        """Match only contracts belonging to the replay session being advanced.

        Independent background sessions may sit on different historical clocks.
        Therefore a tick from RB must never advance AU orders to RB's timestamp.
        Contracts/sequences of the same product are still consumed up to that
        product's own clock so an older held contract remains correctly matched.
        """
        source = str(sess.get("source", ""))
        symbol = str(sess.get("symbol", ""))
        if not source or not symbol:
            return
        timestamp = pd.Timestamp(state.timestamp)
        session_contract = str(sess.get("contract", ""))
        session_key = TradingAccount.make_trade_key(source, symbol, session_contract)
        keys = []
        for key in sorted(self._active_trade_keys()):
            src, product, _contract = TradingAccount.split_trade_key(key)
            if src == source and product == symbol:
                keys.append(key)
        if not keys:
            return
        self._ensure_record_day()
        for key in keys:
            last_ts = self._market_processed_ts.get(key)
            if last_ts is not None and timestamp <= last_ts:
                continue
            if key == session_key and not state.synthetic:
                self._apply_market_bar(key, timestamp, state.source_bar)
                continue
            rows = self._sync_minute_rows(key, last_ts, timestamp)
            for row_ts, bar in rows:
                self._apply_market_bar(key, row_ts, bar)
                if key not in self.account.positions and not any(o.symbol == key for o in self.account.orders):
                    break

    def _process_synchronised_market(self, state):
        """Advance every held/order contract to the visible replay clock.

        For non-current instruments all 1-minute bars between the previous
        processed clock and the new master time are consumed.  This also handles
        different exchange session breaks instead of only checking one matching
        timestamp.
        """
        timestamp = pd.Timestamp(state.timestamp)
        current_key = self._current_trade_key()
        keys = self._active_trade_keys()
        if not keys:
            return
        self._ensure_record_day()
        for key in sorted(keys):
            last_ts = self._market_processed_ts.get(key)
            if last_ts is not None and timestamp <= last_ts:
                continue
            if key == current_key and not state.synthetic:
                self._apply_market_bar(key, timestamp, state.source_bar)
                continue
            rows = self._sync_minute_rows(key, last_ts, timestamp)
            for row_ts, bar in rows:
                # A previous bar may have closed the position and removed the
                # last order; if no live state remains for this contract there is
                # nothing further to execute, but keep its mark at the exit bar.
                self._apply_market_bar(key, row_ts, bar)
                if key not in self.account.positions and not any(o.symbol == key for o in self.account.orders):
                    break
            if rows and self._market_processed_ts.get(key, pd.Timestamp.min) < timestamp:
                # No exact bar at the master clock is fine; keep the last actual
                # market bar timestamp, not an invented mark.
                pass

    def _backtest_context(self) -> dict:
        contract = ""
        if self.panels:
            contract = self.panels[0].contract.currentText().strip()
        return {
            "repo": self.repo,
            "source": self.source_combo.currentText(),
            "symbol": self.symbol,
            "contract": contract,
        }

    def _show_backtest_result(self, result: dict):
        """Load the selected result into the main chart and overlay executions."""
        if not self.repo or not result or not self.panels:
            return
        if not self._guard_history_jump("切换到回测结果"):
            return
        self._set_playing(False)
        source = str(result.get("source") or self.source_combo.currentText())
        symbol = str(result.get("symbol") or "")
        period = str(result.get("period") or self.panels[0].period.currentText())
        contract = str(result.get("contract") or "")
        markers = list(result.get("markers") or [])
        if not symbol:
            return
        try:
            if self.source_combo.findText(source) >= 0 and self.source_combo.currentText() != source:
                self.source_combo.setCurrentText(source)

            # Keep exchange and instrument selectors synchronized with the result.
            self._sync_exchange_and_symbol_list(symbol)
            idx = self.symbol_combo.findData(symbol)
            if idx < 0:
                self.statusBar().showMessage(f"回测结果品种 {symbol} 在当前数据源中不可用", 6000)
                return
            self.symbol_combo.blockSignals(True)
            self.symbol_combo.setCurrentIndex(idx)
            self.symbol_combo.blockSignals(False)
            self._symbol_changed()

            panel = self.panels[0]
            if panel.period.findText(period) >= 0 and panel.period.currentText() != period:
                panel.period.setCurrentText(period)
            if contract and panel.contract.findText(contract) >= 0 and panel.contract.currentText() != contract:
                panel.contract.setCurrentText(contract)

            # Move replay/chart time to the last backtest execution (or the end
            # date if there were no trades), then draw all execution points.
            if self.engine is not None:
                target_time = None
                if markers:
                    target_time = markers[-1].get("datetime")
                if not target_time and result.get("end"):
                    target_time = str(result.get("end"))
                if target_time:
                    try:
                        self.engine.jump_timestamp(pd.Timestamp(target_time))
                        self._rebase_trade_clock_after_history_jump()
                    except Exception:
                        pass
                self._refresh_all_panels()

            # Backtest points belong only to the main chart. Manual symbol/period
            # changes clear them again. Disable auto-follow so the user can drag
            # left/right through the historical entries and exits.
            for p in self.panels:
                p.chart.clear_backtest_markers()
            panel.chart.set_backtest_markers(markers)
            self.follow.blockSignals(True)
            self.follow.setChecked(False)
            self.follow.blockSignals(False)
            panel.chart.set_follow_latest(False)
            self.statusBar().showMessage(
                f"已显示 {result.get('display_name', symbol)} / {result.get('strategy', '')} 的买卖点，共 {len(markers)} 个执行点",
                7000,
            )
        except Exception as exc:
            self._error(exc)

    def _rebuild_panels(self, count: int):
        while self.panels:
            panel = self.panels.pop(); panel.setParent(None); panel.deleteLater()
        for _ in range(count):
            panel = ChartPanel(); self.panels.append(panel); self.chart_split.addWidget(panel)
            panel.activated.connect(self._set_active_panel)
            panel.period.currentTextChanged.connect(lambda _t, p=panel: self._panel_period_changed(p))
            panel.contract.currentTextChanged.connect(lambda _t, p=panel: self._panel_contract_changed(p))
            panel.ma_enabled.toggled.connect(lambda _v, p=panel: p.apply_options())
            panel.ma_values.editingFinished.connect(lambda p=panel: p.apply_options())
            panel.boll.toggled.connect(lambda _v, p=panel: p.apply_options())
            panel.volume.toggled.connect(lambda _v, p=panel: p.apply_options())
            panel.vma5.toggled.connect(lambda _v, p=panel: p.apply_options())
            panel.vma10.toggled.connect(lambda _v, p=panel: p.apply_options())
            panel.oi_line.toggled.connect(lambda _v, p=panel: p.apply_options())
            panel.indicator.currentTextChanged.connect(lambda _t, p=panel: p.apply_options())
            panel.wave.toggled.connect(lambda _v, p=panel: p.apply_options())
            panel.wave.toggled.connect(lambda _v: self._schedule_save())
            panel.trend.toggled.connect(lambda _v, p=panel: p.apply_options())
            panel.trend.toggled.connect(lambda _v: self._schedule_save())
            panel.chart.clickedPrice.connect(lambda price, p=panel: self._price_clicked(p, price))
            panel.chart.tradeLineChanged.connect(self._trade_line_dragged)
            panel.chart.tradeLineDeleteRequested.connect(self._trade_line_delete_requested)
            panel.chart.drawingChanged.connect(self._schedule_save)
            panel.chart.layoutChanged.connect(self._schedule_save)
            panel.chart.followLatestChanged.connect(self._chart_follow_changed)
            panel.chart.volumeOverlayChanged.connect(lambda *_args: self._schedule_save())
            panel.chart.set_follow_latest(self.follow.isChecked())
        self.active_panel = self.panels[0] if self.panels else None
        if not self._building and self.symbol:
            self._load_symbol()
        self._schedule_save()

    def _set_active_panel(self, panel: ChartPanel): self.active_panel = panel

    def _choose_data_root(self):
        if self._has_live_trading_state():
            QMessageBox.information(self, "数据目录", "当前还有持仓或挂单，请先平仓并撤单后再切换数据目录。")
            return
        path = QFileDialog.getExistingDirectory(self, "选择数据目录（qihuodata、其父目录或单个品种目录）")
        if not path:
            return
        # Reset the old replay clock when changing libraries.  Otherwise a saved
        # timestamp from the previous directory can be mapped to the end of the
        # new data, making Play appear to do nothing.
        self._pause_all_sessions()
        self.engine = None
        self.symbol = ""
        self._replay_sessions.clear()
        self._saved_replay_sessions.clear()
        self._played_order.clear()
        self._main_quote_cache.clear()
        self._reset_replay_on_next_load = True
        self.data_root = Path(path)
        self.repo = DataRepository(self.data_root)
        self._load_instruments()
        self._schedule_save()

    def _load_instruments(self):
        if self.repo is None:
            self.statusBar().showMessage("未找到 qihuodata，请点击“选择数据目录”")
            return
        try:
            items = self.repo.list_instruments(self.source_combo.currentText())
        except Exception as exc:
            self._error(exc); return
        self._all_items = items
        exchanges = sorted({x.exchange for x in items if x.exchange})
        self.exchange_combo.blockSignals(True)
        self.exchange_combo.clear(); self.exchange_combo.addItem("全部"); self.exchange_combo.addItems(exchanges)
        saved_exchange = getattr(self, "_restore_exchange", "")
        if saved_exchange and self.exchange_combo.findText(saved_exchange) >= 0:
            self.exchange_combo.setCurrentText(saved_exchange)
        self.exchange_combo.blockSignals(False)
        self._filter_symbols()
        self._populate_main_list()

    def _filter_symbols(self):
        if not hasattr(self, "_all_items"):
            return
        ex = self.exchange_combo.currentText()
        items = self.repo.filter_exchange(self._all_items, ex) if self.repo else []
        prior = self.symbol_combo.currentData()
        restore_symbol = getattr(self, "_restore_symbol", "")
        self.symbol_combo.blockSignals(True)
        self.symbol_combo.clear()
        for m in items:
            self.symbol_combo.addItem(m.display, m.symbol)
        target = restore_symbol or prior
        if target:
            idx = self.symbol_combo.findData(target)
            if idx >= 0:
                self.symbol_combo.setCurrentIndex(idx)
        self.symbol_combo.blockSignals(False)
        if not self.symbol_combo.count():
            return
        if restore_symbol and self.symbol_combo.currentData() == restore_symbol:
            self._restore_symbol = ""
            self._restore_exchange = ""
        # On application startup, metadata/list population is cheap but loading a
        # multi-year 1-minute parquet is not. Let the window become interactive
        # first, then load the selected product on the event loop. A user pressing
        # Play earlier will trigger the same load immediately.
        if self._startup_symbol_load_pending and self.engine is None:
            self.symbol = str(self.symbol_combo.currentData() or "")
            QTimer.singleShot(180, self._finish_startup_symbol_load)
            return
        self._symbol_changed()

    def _finish_startup_symbol_load(self):
        if not self._startup_symbol_load_pending:
            return
        self._startup_symbol_load_pending = False
        sym = self.symbol_combo.currentData()
        if not sym:
            return
        self.symbol = str(sym)
        self._sync_exchange_and_symbol_list(self.symbol)
        self._load_symbol()
        self._update_play_button()
        self._refresh_main_list()
        self._schedule_main_quote_warmup(900)

    def _apply_top_replay_order(self) -> None:
        """Force the top replay controls into the requested visible order."""
        layout = self.replay_layout

        # Remove every current item first. This avoids Qt retaining an older
        # visual position for widgets that were previously reparented/reinserted.
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                layout.removeWidget(widget)

        ordered = [
            self.btn_random_symbol,
            self.lbl_speed_title,
            self.btn_speed_slower,
            self.speed,
            self.btn_speed_faster,
            self.lbl_speed,
            self.follow,
            self.btn_random_date,
            self.btn_start,
            # Timeline controls: previous trading day -> slider -> next trading day -> date.
            self.btn_prev_day,
            self.progress,
            self.btn_next_day,
            self.date_edit,
            self.btn_all_play,
            self.btn_play,
        ]
        for widget in ordered:
            layout.addWidget(widget)
        layout.addStretch(1)

    def _source_changed(self, text: str):
        self._capture_current_session()
        # The visible engine may still belong to the old source while the new
        # combobox is being populated. Clear only the visible symbol reference so
        # _symbol_changed cannot accidentally save that engine under the new source.
        self.symbol = ""
        self.source = str(text)
        self._load_instruments()
        self._update_play_button()
        self._schedule_save()

    def _exchange_for_symbol(self, symbol: str) -> str:
        """Return the exchange recorded for *symbol* in the current data source.

        The exchange combobox is part of the training context.  Keeping it in
        sync with the selected instrument makes the saved session unambiguous
        and ensures a restart restores both the instrument and its exchange.
        """
        for meta in getattr(self, "_all_items", []) or []:
            if str(getattr(meta, "symbol", "")) == str(symbol):
                return str(getattr(meta, "exchange", "") or "")
        return ""

    def _sync_exchange_and_symbol_list(self, symbol: str):
        exchange = self._exchange_for_symbol(symbol)
        if not exchange or self.exchange_combo.findText(exchange) < 0:
            return
        # Keep the exchange selector and the symbol selector describing the same
        # instrument.  Signals are blocked to avoid reloading the engine twice.
        self.exchange_combo.blockSignals(True)
        self.exchange_combo.setCurrentText(exchange)
        self.exchange_combo.blockSignals(False)

        items = self.repo.filter_exchange(self._all_items, exchange) if self.repo else []
        self.symbol_combo.blockSignals(True)
        self.symbol_combo.clear()
        for meta in items:
            self.symbol_combo.addItem(meta.display, meta.symbol)
        idx = self.symbol_combo.findData(symbol)
        if idx >= 0:
            self.symbol_combo.setCurrentIndex(idx)
        self.symbol_combo.blockSignals(False)

    def _symbol_changed(self, *_):
        self._startup_symbol_load_pending = False
        for p in getattr(self, "panels", []):
            p.chart.clear_backtest_markers()
        sym = self.symbol_combo.currentData()
        if not sym:
            return
        new_symbol = str(sym)
        if self.symbol and new_symbol != self.symbol:
            self._capture_current_session()
        self.symbol = new_symbol
        self._sync_exchange_and_symbol_list(self.symbol)
        self._load_symbol()
        self._update_play_button()
        self._refresh_main_list()
        self._schedule_save()

    def _populate_contracts(self, panel: ChartPanel):
        if not self.repo or not self.symbol: return
        period = panel.period.currentText(); contracts = self.repo.list_contracts(self.symbol, period, self.source_combo.currentText())
        prior = getattr(panel, "_restore_contract", "") or panel.contract.currentText()
        panel.contract.blockSignals(True); panel.contract.clear(); panel.contract.addItems(contracts)
        if prior and prior in contracts:
            panel.contract.setCurrentText(prior)
            panel._restore_contract = ""
        elif contracts:
            if self.source_combo.currentText() == "原始行情":
                active = self.repo.select_active_contract(self.symbol, period, self.source_combo.currentText())
                if active in contracts: panel.contract.setCurrentText(active)
        panel.contract.blockSignals(False)

    def _load_symbol(self):
        if not self.repo or not self.symbol:
            return
        self._main_quote_timer.stop()
        self._market_load_busy = True
        try:
            self._sync_minute_cache.clear()
            source = self.source_combo.currentText()
            sess = self._session_for(source, self.symbol, create=True)
            self._sync_session_from_engine(sess)
            if self.panels and sess:
                p0 = self.panels[0]
                saved_period = str(sess.get("period", "") or "")
                saved_intraday = bool(sess.get("intraday", False))
                if saved_period and p0.period.findText(saved_period) >= 0:
                    if p0.period.currentText() != saved_period or p0.period.is_intraday() != saved_intraday:
                        p0.period.blockSignals(True)
                        p0.period.set_period(saved_period, intraday=saved_intraday)
                        p0.period.blockSignals(False)
                        p0.apply_period_mode()
                pending = getattr(self, "_position_target_context", None)
                pending_contract = ""
                if pending and len(pending) == 3:
                    p_source, p_symbol, p_contract = map(str, pending)
                    if p_source == source and p_symbol == self.symbol:
                        pending_contract = p_contract.strip()
                saved_contract = str(sess.get("contract", "") or "")
                if pending_contract:
                    p0._restore_contract = pending_contract
                elif saved_contract:
                    p0._restore_contract = saved_contract
            for panel in self.panels:
                self._populate_contracts(panel)
            # The explicit holding contract has now been applied.  Clear the
            # one-shot override so normal product switching goes back to the
            # product's own saved contract preference.
            pending = getattr(self, "_position_target_context", None)
            if pending and len(pending) == 3:
                if str(pending[0]) == source and str(pending[1]) == self.symbol:
                    self._position_target_context = None
            self._load_main_engine()
            self._refresh_all_panels()
        finally:
            self._market_load_busy = False
            if not self._any_session_playing():
                self._schedule_main_quote_warmup(900)

    def _show_data_quality(self, bars: pd.DataFrame) -> None:
        """Surface market-data mismatches instead of silently hiding them.

        Completed K lines are rebuilt from real 1-minute data whenever possible.
        These diagnostics tell the user when native higher-timeframe OHLC differed,
        when minute coverage is incomplete, or when a night-session trading day
        had to be repaired.  They do not change replay state.
        """
        try:
            mismatch_count = int(bars.attrs.get("native_ohlc_mismatch_count", 0) or 0)
            fallback_count = int(
                bars.attrs.get("minute_fallback_bars", bars.attrs.get("minute_fallback_days", 0)) or 0
            )
            repaired_count = int(bars.attrs.get("trading_day_repaired_count", 0) or 0)
            unrepaired_count = int(bars.attrs.get("trading_day_unrepaired_count", 0) or 0)
        except Exception:
            return

        notes: list[str] = []
        if mismatch_count:
            notes.append(f"{mismatch_count}根原周期K与1分钟聚合不一致，当前以真实1分钟数据为准")
        if fallback_count:
            notes.append(f"{fallback_count}根/日缺少可匹配1分钟数据，保留原周期K且不虚构分钟成交")
        if unrepaired_count:
            notes.append(f"{unrepaired_count}根分钟K无法可靠确定交易日，请检查原始数据")
        if repaired_count:
            notes.append(f"已按实际交易日历校正{repaired_count}根夜盘分钟K")
        # Keep these diagnostics available in DataFrame attrs for debugging, but
        # do not display the long "数据校验：..." line in the normal interface.
        return

    def _set_replay_date_bounds(self, bars: pd.DataFrame) -> None:
        """Update the date control without triggering an unintended history jump.

        QDateEdit emits ``dateChanged`` when a new minimum date clamps its current
        value.  AD starts only on 2025-06-10, so switching from an older product
        could clamp the widget to 2025-06-10; the old signal handler then treated
        that automatic clamp as a *manual* jump and force-saved AD at its first
        candle.  Block signals for the entire bounds update and finally display
        the engine's real replay trading day.
        """
        if bars is None or bars.empty:
            return
        try:
            days = self.engine.available_trading_days() if self.engine is not None else trading_days_from_frame(bars)
            if not days:
                days = trading_days_from_frame(bars)
            first = pd.Timestamp(days[0])
            last = pd.Timestamp(days[-1])
        except Exception:
            return
        previous_block = self.date_edit.blockSignals(True)
        self._programmatic_date_update = True
        try:
            if pd.notna(first):
                self.date_edit.setMinimumDate(QDate(first.year, first.month, first.day))
            if pd.notna(last):
                self.date_edit.setMaximumDate(QDate(last.year, last.month, last.day))
            if self.engine is not None:
                try:
                    td = str(self.engine.current().trading_day)[:10]
                    qd = QDate.fromString(td, "yyyy-MM-dd")
                    if qd.isValid():
                        self.date_edit.setDate(qd)
                except Exception:
                    pass
        finally:
            self._programmatic_date_update = False
            self.date_edit.blockSignals(previous_block)
        self._update_day_step_buttons()

    def _load_main_engine(self):
        if not self.panels or not self.repo or not self.symbol:
            return
        p = self.panels[0]
        if not p.contract.currentText():
            return
        source = self.source_combo.currentText()
        period = p.period.currentText()
        contract = p.contract.currentText()
        sess = self._session_for(source, self.symbol, create=True)
        old_state = None
        if self.engine is not None:
            try:
                old_state = self.engine.current()
            except Exception:
                old_state = None
        if sess is not None and sess.get("engine") is self.engine and old_state is not None:
            sess.update({
                "timestamp": str(old_state.timestamp),
                "trading_day": str(old_state.trading_day),
                "bar_index": int(self.engine.bar_index),
                "minute_index": int(self.engine.minute_index),
            })

        self._sync_session_from_engine(sess)
        cached_engine = sess.get("engine") if sess else None
        if (source, self.symbol) in self._live_dirty:
            # Reload only on an explicit chart/context operation, never for
            # every arriving tick while the user is replaying history.
            self.repo.clear_cache()
            cached_engine = None
            self._live_dirty.discard((source, self.symbol))
        if (cached_engine is not None and sess is not None
                and str(sess.get("period", "")) == str(period)
                and str(sess.get("contract", "")) == str(contract)):
            self.engine = cached_engine
            self._visible_engine_key = str(sess.get("key", ""))
            self.source = source
            self._capture_current_session()
            try:
                bars = self.engine.bars
                self._show_data_quality(bars)
                self._set_replay_date_bounds(bars)
            except Exception:
                pass
            self._migrate_legacy_current_trade_key()
            self._update_play_button()
            self._refresh_main_list()
            return

        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            bars = self.repo.load_bars(source, self.symbol, period, contract)
            self._show_data_quality(bars)
            try:
                mins = bars if period == "1min" else self.repo.load_minutes(source, self.symbol, contract)
            except Exception:
                mins = None
            daily = self.repo.load_bars(source, self.symbol, "1d", contract) if period in {"1w", "1M"} else None
            engine = ReplayEngine(bars, period, mins, allow_synthetic=False, daily=daily)

            restored = False
            authoritative = self._saved_replay_sessions.get(str(sess.get("key", "")), sess) if sess else None
            if authoritative:
                restored = self._restore_engine_snapshot(engine, authoritative)
                if restored and bool(authoritative.get("at_end", False)):
                    # A session that ended before an EDB update should resume at
                    # the new data frontier if newer bars were appended.
                    engine.jump_bar(len(engine.bars) - 1, to_end_of_bar=True)
            if not restored and self._reset_replay_on_next_load:
                engine.jump_bar(0)
                self._reset_replay_on_next_load = False
                restored = True
            if not restored:
                saved = self._startup_replay if self._startup_replay_pending else {}
                saved_matches = (
                    bool(saved) and saved.get("symbol") == self.symbol
                    and (not saved.get("source") or saved.get("source") == source)
                    and (not saved.get("period") or saved.get("period") == period)
                    and (not saved.get("contract") or saved.get("contract") == contract)
                )
                if saved_matches:
                    restored = self._restore_engine_snapshot(engine, saved)
                    self._startup_replay_pending = False
            # Only a truly new product may inherit the currently visible clock.
            has_saved_position = bool(authoritative and (authoritative.get("timestamp") or authoritative.get("bar_index") is not None or authoritative.get("minute_index") is not None))
            if not restored and not has_saved_position and old_state is not None:
                try:
                    engine.jump_timestamp(old_state.timestamp, old_state.trading_day)
                    restored = True
                except Exception:
                    pass
            if not restored:
                legacy = self.state.get("replay", {}) or {}
                if legacy.get("symbol") == self.symbol:
                    restored = self._restore_engine_snapshot(engine, legacy)

            self.engine = engine
            self._visible_engine_key = str(sess.get("key", "")) if sess else self._replay_session_key(source, self.symbol)
            self.source = source
            if sess is not None:
                sess.update({
                    "engine": engine, "source": source, "symbol": self.symbol,
                    "exchange": self._exchange_for_symbol(self.symbol) or self.exchange_combo.currentText(),
                    "period": period, "intraday": p.period.is_intraday(), "contract": contract,
                })
            self._capture_current_session()
            self._migrate_legacy_current_trade_key()
            current_ts = pd.Timestamp(engine.current().timestamp)
            for key in self._active_trade_keys():
                src0, product, _ct = TradingAccount.split_trade_key(key)
                if src0 == source and product == self.symbol:
                    self._market_processed_ts.setdefault(key, current_ts)
            self._set_replay_date_bounds(bars)
        except Exception as exc:
            self._error(exc)
        finally:
            QApplication.restoreOverrideCursor()
        self._update_play_button()
        self._refresh_main_list()

    def _panel_period_changed(self, panel: ChartPanel):
        panel.apply_period_mode()
        panel.chart.clear_backtest_markers()
        self._populate_contracts(panel)
        if panel is self.panels[0]: self._load_main_engine()
        self._refresh_panel(panel); self._schedule_save()

    def _panel_contract_changed(self, panel: ChartPanel):
        panel.chart.clear_backtest_markers()
        if panel is self.panels[0]: self._load_main_engine()
        self._refresh_panel(panel); self._schedule_save()

    def _refresh_panel(self, panel: ChartPanel):
        if not self.repo or not self.engine or not self.symbol or not panel.contract.currentText(): return
        panel.apply_period_mode()
        try:
            bars = self.repo.load_bars(self.source_combo.currentText(), self.symbol, panel.period.currentText(), panel.contract.currentText())
            state = self.engine.current()
            idx = locate_bar_index(bars, panel.period.currentText(), state.timestamp, state.trading_day)
            partial = state.partial if panel is self.panels[0] else None
            source_bar = state.source_bar if panel is self.panels[0] else None
            synthetic = state.synthetic if panel is self.panels[0] else False
            if panel is not self.panels[0]:
                # Reconstruct the sub-window's forming candle at the same replay time.
                try:
                    try:
                        mins = self.repo.load_minutes(self.source_combo.currentText(), self.symbol, panel.contract.currentText())
                    except FileNotFoundError:
                        mins = None
                    period = panel.period.currentText()
                    daily = self.repo.load_bars(self.source_combo.currentText(), self.symbol, "1d", panel.contract.currentText()) if period in {"1w", "1M"} else None
                    provider = MinutePathProvider(bars, period, mins, allow_synthetic=False, daily=daily)
                    path = provider.path(idx)
                    eligible = [x for x in path if x[0] <= state.timestamp]
                    chosen = eligible[-1] if eligible else path[0]
                    partial = chosen[2]
                    source_bar = chosen[3]
                    synthetic = chosen[4]
                except Exception:
                    partial = None
                    source_bar = None
            panel.chart.set_transition_ms(0)
            # Main/continuous charts show an automatic vertical dashed separator
            # whenever the dominant raw contract changes.  Raw-contract charts do
            # not need this separator because each file is already one contract.
            if self.source_combo.currentText() == "主力连续":
                panel.chart.set_roll_markers(self.repo.main_contract_switches(self.symbol))
            else:
                panel.chart.set_roll_markers(())
            panel.chart.set_series(
                bars, idx, partial, synthetic, source_bar=source_bar,
                animate_minute=False,
            )
            # Do not re-apply UI options on every replay frame.  In particular,
            # doing so used to overwrite VMA5/VMA10/open-interest button clicks
            # with the previous persisted values while playback was running.
            self._update_trade_lines(panel)
        except Exception as exc: self.statusBar().showMessage(str(exc), 5000)

    def _refresh_trade_side_only(self):
        if not self.engine:
            return
        st = self.engine.current()
        self.records.ensure_day(pd.Timestamp.now().strftime("%Y-%m-%d"))
        self.trade.set_daily_records(self.records.trades, self.records.orders)
        mark = float(st.partial["close"])
        current_key = self._current_trade_key()
        if current_key:
            self.account.set_mark(current_key, mark)
        labels = {key: self._trade_key_label(key) for key in self._active_trade_keys()}
        if current_key:
            labels[current_key] = self._trade_key_label(current_key)
        self.trade.set_account(self.account, mark, current_key, labels)
        self._refresh_trade_qty_from_available()

    def _refresh_all_panels(self):
        if not self.engine:
            self.statusBar().setReplayTime("")
            self._update_day_step_buttons()
            return
        for p in self.panels:
            self._refresh_panel(p)
        st = self.engine.current()
        self.progress.blockSignals(True)
        self.progress.setValue(int(self.engine.progress * 10000))
        self.progress.blockSignals(False)
        td = str(st.trading_day)
        qd = QDate.fromString(td[:10], "yyyy-MM-dd")
        if qd.isValid():
            self.date_edit.blockSignals(True)
            self.date_edit.setDate(qd)
            self.date_edit.blockSignals(False)
        self._update_day_step_buttons()
        self._refresh_trade_side_only()
        self._capture_current_session()
        self._refresh_main_list()
        # Show only the changing replay timestamp at the far left of the bottom
        # status bar. The extra trading-day/K-count/minute-path diagnostics are
        # intentionally removed from the user-facing interface.
        market_time = str(st.timestamp)[:19].replace("T", " ")
        trading_day = str(st.trading_day)[:10]
        # Chinese-futures trading dates differ from wall-clock dates during
        # night sessions. Show both explicitly so 2026-11-02 trading day can
        # correctly display 2026-10-30 21:00 or 2026-10-31 00:xx market time.
        replay_time = f"交易日 {trading_day}  |  行情时间 {market_time}"
        self.statusBar().setReplayTime(replay_time)

    def _tick(self):
        """Advance every currently-playing product by one real minute point."""
        sessions = [s for s in self._replay_sessions.values() if s.get("playing") and s.get("engine") is not None]
        if not sessions:
            self.play_timer.stop()
            self._update_play_button()
            return

        visible_key = self._replay_session_key(self.source_combo.currentText(), self.symbol) if self.symbol else ""
        visible_moved = False
        any_moved = False
        ended_now = False
        for sess in sessions:
            engine = sess.get("engine")
            if engine is None:
                sess["playing"] = False
                continue
            try:
                old = engine.current()
                state = engine.step(1)
                moved = (
                    state.timestamp != old.timestamp
                    or state.bar_index != old.bar_index
                    or state.minute_index != old.minute_index
                )
                if moved:
                    any_moved = True
                    sess.update({
                        "timestamp": str(state.timestamp),
                        "trading_day": str(state.trading_day),
                        "bar_index": int(engine.bar_index),
                        "minute_index": int(engine.minute_index),
                    })
                    self._sync_session_from_engine(sess)
                    contract = str(sess.get("contract", ""))
                    trade_key = TradingAccount.make_trade_key(
                        str(sess.get("source", "")), str(sess.get("symbol", "")), contract
                    )
                    if trade_key:
                        try:
                            self.account.set_mark(trade_key, float(state.partial["close"]))
                        except Exception:
                            pass
                    self._process_replay_session_market(sess, state)
                    if str(sess.get("key", "")) == visible_key and engine is self.engine:
                        visible_moved = True
                if engine.at_end:
                    sess["playing"] = False
                    sess["played"] = True
                    sess["at_end"] = True
                    self._sync_session_from_engine(sess, force=True)
                    ended_now = True
            except Exception as exc:
                sess["playing"] = False
                self.statusBar().showMessage(f"后台复盘停止：{sess.get('symbol','')} - {exc}", 6000)

        if visible_moved:
            self._animate_forward = True
            try:
                self._refresh_all_panels()
            finally:
                self._animate_forward = False
        elif any_moved:
            # Background products may change account equity, fills and TP/SL while
            # the visible chart is paused. Refresh the lightweight side panels.
            self._refresh_trade_side_only()
            self._refresh_main_list()


        if ended_now or (any_moved and (time.monotonic() - float(self._last_replay_checkpoint)) >= self._replay_checkpoint_seconds):
            self._checkpoint_replay_heads()
            self._last_replay_checkpoint = time.monotonic()

        if not self._any_session_playing():
            self.play_timer.stop()
        self._update_play_button()
        self._refresh_main_list()

    def _step(self, delta: int, refresh: bool = True):
        """Programmatic step of the visible product only."""
        if not self.engine:
            return False
        if delta < 0 and not self._guard_history_jump("回退"):
            return False
        old = self.engine.current()
        state = self.engine.step(delta)
        moved = state.timestamp != old.timestamp or state.bar_index != old.bar_index or state.minute_index != old.minute_index
        sess = self._session_for(create=True)
        if moved and sess is not None:
            sess.update({
                "engine": self.engine,
                "timestamp": str(state.timestamp),
                "trading_day": str(state.trading_day),
                "bar_index": int(self.engine.bar_index),
                "minute_index": int(self.engine.minute_index),
            })
        if delta > 0 and moved and sess is not None:
            self._process_replay_session_market(sess, state)
        elif delta < 0 and moved:
            self._rebase_trade_clock_after_history_jump()
        if refresh:
            self._animate_forward = delta > 0 and moved
            try:
                self._refresh_all_panels()
            finally:
                self._animate_forward = False
            self._schedule_save()
        return moved

    def _speed_seconds(self) -> float:
        """Return the selected replay interval in seconds.

        Slider position 1..200 is intentionally inverted so moving the knob to
        the right means faster playback: value 1 = 2.00 s, value 200 = 0.01 s.
        """
        value = max(1, min(200, int(self.speed.value())))
        centiseconds = 201 - value
        return centiseconds / 100.0

    def _set_speed_seconds(self, seconds: float):
        seconds = max(0.01, min(2.00, float(seconds)))
        centiseconds = int(round(seconds * 100.0))
        self.speed.setValue(max(1, min(200, 201 - centiseconds)))

    def _play_interval_ms(self) -> int:
        return max(10, int(round(self._speed_seconds() * 1000.0)))

    def _play_timer_interval_ms(self) -> int:
        # One timer callback = one real minute.  No hidden batching.
        return self._play_interval_ms()

    def _play_batch_steps(self) -> int:
        # Kept for compatibility with chart code; playback no longer batches.
        return 1

    def _animation_interval_ms(self) -> int:
        """Smooth the forming candle between adjacent real-minute states."""
        interval = self._play_interval_ms()
        return max(10, min(520, int(round(interval * 0.86))))

    def _update_speed_label(self):
        seconds = self._speed_seconds()
        self.lbl_speed.setText(f"{seconds:.2f}秒")
        self.lbl_speed.setToolTip(
            f"每隔约 {seconds:.2f} 秒推进1个真实1分钟数据点；日K/小时K由真实分钟行情逐分钟形成"
        )

    def _speed_changed(self, _value: int):
        self._update_speed_label()
        if self.play_timer.isActive():
            self.play_timer.setInterval(self._play_timer_interval_ms())
        for panel in self.panels:
            panel.chart.set_transition_ms(0)
        self._schedule_save()

    def _adjust_speed_level(self, delta: int):
        """Adjust speed smoothly by 0.01 second per click."""
        value = max(self.speed.minimum(), min(self.speed.maximum(), self.speed.value() + int(delta)))
        self.speed.setValue(value)

    def _follow_changed(self, on: bool):
        for panel in self.panels:
            panel.chart.blockSignals(True)
            panel.chart.set_follow_latest(on)
            panel.chart.blockSignals(False)
        self._schedule_save()

    def _chart_follow_changed(self, on: bool):
        # Manual horizontal panning in any panel suspends auto-follow everywhere.
        if on == self.follow.isChecked():
            return
        self.follow.blockSignals(True)
        self.follow.setChecked(on)
        self.follow.blockSignals(False)
        for panel in self.panels:
            if panel.chart.follow_latest != on:
                panel.chart.blockSignals(True)
                panel.chart.set_follow_latest(on)
                panel.chart.blockSignals(False)
        self._schedule_save()

    def _ensure_main_session_engine(self, symbol: str) -> dict | None:
        """Create/restore a main-continuous replay engine without changing the visible chart."""
        if not self.repo or not symbol:
            return None
        source = "主力连续"
        sess = self._session_for(source, symbol, create=True)
        if sess is None:
            return None

        # Prefer each product's own saved period/contract. A never-opened product
        # inherits the visible chart period and starts at the visible replay time.
        current_period = self.panels[0].period.currentText() if self.panels else "1d"
        period = str(sess.get("period", "") or current_period)
        contracts = self.repo.list_contracts(symbol, period, source)
        if not contracts and period != current_period:
            period = current_period
            contracts = self.repo.list_contracts(symbol, period, source)
        if not contracts:
            return None

        saved_contract = str(sess.get("contract", "") or "")
        contract = saved_contract if saved_contract in contracts else self._preferred_main_sequence(contracts)
        if not contract:
            contract = str(contracts[0])

        engine = sess.get("engine")
        if (
            engine is not None
            and str(sess.get("period", "")) == str(period)
            and str(sess.get("contract", "")) == str(contract)
        ):
            return sess

        bars = self.repo.load_bars(source, symbol, period, contract)
        if bars is None or bars.empty:
            return None
        try:
            mins = bars if period == "1min" else self.repo.load_minutes(source, symbol, contract)
        except Exception:
            mins = None
        daily = self.repo.load_bars(source, symbol, "1d", contract) if period in {"1w", "1M"} else None
        engine = ReplayEngine(bars, period, mins, allow_synthetic=False, daily=daily)

        authoritative = self._saved_replay_sessions.get(str(sess.get("key", "")), sess)
        restored = self._restore_engine_snapshot(engine, authoritative) if authoritative else False
        if not restored and self.engine is not None:
            # New products join the selected group at the currently visible replay
            # clock so the group advances together from the same historical point.
            try:
                visible_state = self.engine.current()
                engine.jump_timestamp(visible_state.timestamp, visible_state.trading_day)
                restored = True
            except Exception:
                pass

        sess.update({
            "engine": engine,
            "source": source,
            "symbol": symbol,
            "exchange": self._exchange_for_symbol(symbol),
            "period": period,
            "intraday": bool(period != "1d"),
            "contract": contract,
        })
        self._sync_session_from_engine(sess)
        return sess

    def _toggle_selected_main_playback(self, symbols: list[str]) -> None:
        """Start/pause all selected main-list products as one playback group."""
        symbols = [str(x) for x in symbols if str(x)]
        symbols = list(dict.fromkeys(symbols))
        if len(symbols) < 2:
            self._set_playing(not self._current_session_playing())
            return
        if not self.repo:
            return

        self._main_quote_timer.stop()
        sessions: list[dict] = []
        failures: list[str] = []
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            for symbol in symbols:
                QApplication.processEvents()
                try:
                    sess = self._ensure_main_session_engine(symbol)
                except Exception:
                    sess = None
                if sess is None or sess.get("engine") is None:
                    failures.append(symbol)
                else:
                    sessions.append(sess)
        finally:
            QApplication.restoreOverrideCursor()

        if not sessions:
            self.statusBar().showMessage("选中的品种没有可播放行情。", 4000)
            return

        # If every selected playable product is running, the same button pauses
        # only this selection. Otherwise it starts/resumes every selected product.
        pause_selected = all(bool(sess.get("playing")) for sess in sessions)
        changed = 0
        if pause_selected:
            for sess in sessions:
                if sess.get("playing"):
                    sess["playing"] = False
                    self._sync_session_from_engine(sess)
                    self._update_saved_session_shadow(sess, force=False)
                    changed += 1
        else:
            # Manual selected playback invalidates the remembered "all paused"
            # resume set, because the runtime playing set has changed explicitly.
            self._all_pause_active = False
            self._all_pause_resume_keys = []
            for sess in sessions:
                engine = sess.get("engine")
                try:
                    at_end = bool(engine.at_end)
                except Exception:
                    at_end = True
                if at_end:
                    sess["playing"] = False
                    continue
                if not sess.get("playing"):
                    changed += 1
                sess["playing"] = True
                sess["played"] = True
                key = str(sess.get("key", ""))
                if key and key not in self._played_order:
                    self._played_order.append(key)
                self._sync_session_from_engine(sess)

        if self._any_session_playing():
            self._play_last_clock = time.monotonic()
            self._play_budget_ms = 0.0
            if not self.play_timer.isActive():
                self.play_timer.start(self._play_timer_interval_ms())
            else:
                self.play_timer.setInterval(self._play_timer_interval_ms())
        else:
            self.play_timer.stop()
            self._play_last_clock = None
            self._play_budget_ms = 0.0

        self._checkpoint_replay_heads()
        self._update_play_button()
        self._refresh_main_list()
        self._schedule_save()
        action = "暂停" if pause_selected else "播放"
        suffix = f"；{len(failures)} 个品种无可用行情" if failures else ""
        self.statusBar().showMessage(f"已{action}选中的 {len(sessions)} 个品种{suffix}。", 3500)

    def _set_playing(self, on: bool):
        if not self.symbol:
            self._update_play_button()
            return
        self._main_quote_timer.stop()
        if self.engine is None:
            self.btn_play.setText("加载中…")
            self.btn_play.repaint()
            self._startup_symbol_load_pending = False
            self._load_symbol()
        if self.engine is None:
            QMessageBox.information(self, "播放", "当前没有可播放的行情数据。请先选择数据目录、品种和合约。")
            self._update_play_button()
            return
        sess = self._session_for(create=True)
        if sess is None:
            return
        sess["engine"] = self.engine
        self._visible_engine_key = str(sess.get("key", ""))
        if not on:
            sess["playing"] = False
            self._sync_session_from_engine(sess)
            if not self._any_session_playing():
                self.play_timer.stop()
                self._play_last_clock = None
                self._play_budget_ms = 0.0
            self._checkpoint_replay_heads()
            self._update_play_button()
            self._refresh_main_list()
            self._schedule_save()
            self._schedule_main_quote_warmup(900)
            return
        # If the user manually panned the main chart into history, the rightmost
        # visible candle becomes the new replay starting point.  Reset auto-follow
        # only after capturing that viewport index, so playback continues from the
        # candle the user is currently inspecting rather than the stale replay head.
        try:
            main_chart = self.panels[0].chart if self.panels else None
            visible_right = main_chart.visible_right_index() if main_chart is not None else -1
            if visible_right >= 0 and (
                int(visible_right) != int(self.engine.bar_index)
                or not bool(main_chart.follow_latest)
            ):
                self.engine.jump_bar(int(visible_right))
                main_chart.set_follow_latest(True)
                self._rebase_trade_clock_after_history_jump()
                self._capture_current_session(force=True)
                self._checkpoint_replay_heads()
                self._refresh_all_panels()
                self._schedule_save()
        except Exception as exc:
            self.statusBar().showMessage(f"按当前图表位置开始播放失败：{exc}", 5000)

        if self.engine.at_end:
            # Never turn Play into an implicit "go to first bar" command.
            sess["playing"] = False
            self._sync_session_from_engine(sess)
            self._checkpoint_replay_heads()
            self._update_play_button()
            self._refresh_main_list()
            self.statusBar().showMessage("当前品种已经到最后一根行情；如需重新开始，请点“从最早开始”。", 5000)
            return
        sess["playing"] = True
        sess["played"] = True
        if sess["key"] not in self._played_order:
            self._played_order.append(sess["key"])
        self._sync_session_from_engine(sess)
        self._checkpoint_replay_heads()
        if not self.play_timer.isActive():
            self._play_last_clock = time.monotonic()
            self._play_budget_ms = 0.0
            self.play_timer.start(self._play_timer_interval_ms())
        else:
            self.play_timer.setInterval(self._play_timer_interval_ms())
        self._update_play_button()
        self._refresh_main_list()
        self._schedule_save()

    def _toggle_play(self, *_args):
        # Ctrl/Shift multi-selection in the main-continuous list turns the normal
        # Play button into a group Play/Pause command. With zero/one selected row,
        # retain the original per-product behavior.
        if self.source_combo.currentText() == "主力连续":
            selected = self._selected_main_symbols()
            if len(selected) > 1:
                self._toggle_selected_main_playback(selected)
                return
        self._set_playing(not self._current_session_playing())

    def _go_start(self):
        if not self.engine or not self._guard_history_jump("回到最早行情"):
            return
        self.engine.jump_bar(0)
        self._rebase_trade_clock_after_history_jump()
        self._capture_current_session(force=True); self._checkpoint_replay_heads(); self._refresh_all_panels(); self._schedule_save()

    def _available_replay_days(self) -> list[str]:
        """Use real minute / daily sources, not weekly/monthly endpoints.

        A weekly chart contains one aggregate candle per week.  Reading dates
        from engine.bars therefore makes the day buttons jump by a week or
        disables them at the last weekly candle even though minute data still
        contains individual trading days. Native daily data covers historical
        days for which the minute file is absent or only partially available.
        """
        engine = self.engine
        if engine is None:
            return []
        cached = getattr(self, "_replay_trading_days_cache", None)
        if cached is not None and cached[0] is engine:
            return list(cached[1])
        days = engine.available_trading_days()
        self._replay_trading_days_cache = (engine, days)
        return list(days)

    def _jump_engine_to_trading_day(self, day: str) -> bool:
        """Jump to the first real minute of one trading day in any chart period."""
        engine = self.engine
        if engine is None:
            return False
        provider = getattr(engine, "provider", None)
        minute_frame = getattr(provider, "minutes", None)
        timestamp = first_timestamp_for_trading_day(minute_frame, day)
        if timestamp is not None:
            # jump_timestamp maps the target day to the containing daily,
            # weekly, monthly or intraday aggregate bar, then selects the exact
            # minute within that aggregate. This avoids a one-week jump on W/M.
            engine.jump_timestamp(pd.Timestamp(timestamp), trading_day=str(day)[:10])
            return True
        # Compatibility fallback for datasets without minute data.
        engine.jump_trading_day(str(day)[:10])
        return True

    def _update_day_step_buttons(self) -> None:
        """Enable +/- according to the available trading-day range."""
        if not hasattr(self, "btn_prev_day") or not hasattr(self, "btn_next_day"):
            return
        if self.engine is None:
            self.btn_prev_day.setEnabled(False)
            self.btn_next_day.setEnabled(False)
            return
        try:
            current_day = str(self.engine.current().trading_day)[:10]
        except Exception:
            current_day = ""
        days = self._available_replay_days()
        self.btn_prev_day.setEnabled(shift_trading_day(days, current_day, -1) is not None)
        self.btn_next_day.setEnabled(shift_trading_day(days, current_day, 1) is not None)

    def _step_trading_day(self, delta: int) -> None:
        """Move the replay head by one available trading day and sync all UI."""
        if self.engine is None or int(delta) == 0:
            return
        if not self._guard_history_jump("切换交易日"):
            self._refresh_all_panels()
            return
        try:
            current_day = str(self.engine.current().trading_day)[:10]
            target_day = shift_trading_day(
                self._available_replay_days(), current_day, int(delta)
            )
            if target_day is None:
                self._update_day_step_buttons()
                return

            # A manual date move should put the selected day at the right edge
            # of every chart, consistent with the date-edit and timeline jumps.
            for panel in self.panels:
                panel.chart.set_follow_latest(True)

            if not self._jump_engine_to_trading_day(target_day):
                return
            self._rebase_trade_clock_after_history_jump()
            self._capture_current_session(force=True)
            self._checkpoint_replay_heads()
            self._refresh_all_panels()
            self._schedule_save()
        except Exception as exc:
            self.statusBar().showMessage(f"切换交易日失败：{exc}", 5000)

    def _progress_jump(self):
        if not self.engine or not self._guard_history_jump("拖动进度条"):
            self._refresh_all_panels()
            return
        target_bar = round(self.progress.value()/10000*(len(self.engine.bars)-1))
        self.engine.jump_bar(
            target_bar,
            to_end_of_bar=(target_bar >= len(self.engine.bars) - 1),
        )
        self._rebase_trade_clock_after_history_jump()
        self._capture_current_session(force=True); self._checkpoint_replay_heads(); self._refresh_all_panels(); self._schedule_save()

    def _date_jump(self, qd: QDate):
        if self._building or self._programmatic_date_update or not self.engine:
            return
        # _refresh_all_panels itself updates date_edit with signals blocked, so
        # reaching here means the user requested a historical jump.
        if not self._guard_history_jump("跳转日期"):
            self._refresh_all_panels()
            return
        # A selected date must become the rightmost visible candle on every chart.
        # Clear any manual pan offset before moving the replay clock to that day.
        for panel in self.panels:
            panel.chart.set_follow_latest(True)
        requested_day = qd.toString("yyyy-MM-dd")
        target_day = resolve_trading_day(self._available_replay_days(), requested_day)
        if target_day is not None:
            self._jump_engine_to_trading_day(target_day)
        else:
            self.engine.jump_trading_day(requested_day)
        self._rebase_trade_clock_after_history_jump()
        self._capture_current_session(force=True); self._checkpoint_replay_heads(); self._refresh_all_panels(); self._schedule_save()

    def _random_date(self):
        if not self.engine or not self._guard_history_jump("随机选择历史日期"):
            return
        idx = random.randrange(len(self.engine.bars))
        self.engine.jump_bar(idx)
        self._rebase_trade_clock_after_history_jump()
        self._capture_current_session(force=True); self._checkpoint_replay_heads(); self._refresh_all_panels(); self._schedule_save()
    def _random_symbol(self):
        # Random-main should draw from all instruments, not only the currently
        # selected exchange.  After a symbol is chosen, make the exchange
        # selector follow that instrument so the two controls never disagree.
        if not hasattr(self, "_all_items") or len(self._all_items) < 2:
            return
        candidates = [m for m in self._all_items if m.symbol != self.symbol]
        random.shuffle(candidates)
        for meta in candidates[:12]:
            exchange = meta.exchange or "全部"
            self.exchange_combo.blockSignals(True)
            if self.exchange_combo.findText(exchange) >= 0:
                self.exchange_combo.setCurrentText(exchange)
            else:
                self.exchange_combo.setCurrentText("全部")
            self.exchange_combo.blockSignals(False)

            visible = self.repo.filter_exchange(self._all_items, self.exchange_combo.currentText()) if self.repo else []
            self.symbol_combo.blockSignals(True)
            self.symbol_combo.clear()
            for item in visible:
                self.symbol_combo.addItem(item.display, item.symbol)
            idx = self.symbol_combo.findData(meta.symbol)
            if idx >= 0:
                self.symbol_combo.setCurrentIndex(idx)
            self.symbol_combo.blockSignals(False)
            if idx < 0:
                continue

            self._symbol_changed()
            if self.engine is not None and len(self.engine.bars) >= 30:
                if not self._has_live_trading_state():
                    self._go_start()
                return

    def _live_status(self, text):
        self.live_status_label.setToolTip(text)
        self.statusBar().showMessage(text, 15000)

    def _live_saved(self, message):
        self._live_dirty.add((message["source"], message["symbol"]))
        if message["symbol"] == self.symbol:
            self._live_status(f"后台已更新 {message['symbol']} 1分钟至 {message.get('minute_latest') or '—'}；复盘位置保持不变")

    def _live_running_changed(self, running):
        self.live_status_label.setText("后台行情：" + (("当前" if self.live_controller.mode == "current" else "全部") if running else "停止"))
        self.realtime_action.setText("停止实时更新当前" if running and self.live_controller.mode == "current" else "免费实时更新当前")
        self.realtime_all_action.setText("停止实时更新全部" if running and self.live_controller.mode == "all" else "免费实时更新全部")
        if not running:
            self.live_view_action.setChecked(False)

    def _start_current_live(self):
        self._start_background_live("current")

    def _start_all_live(self):
        self._start_background_live("all")

    def _start_background_live(self, mode):
        if self.live_controller.is_running() and self.live_controller.mode == mode:
            self.live_controller.stop()
            return
        if self.repo is None:
            QMessageBox.information(self, "免费实时更新", "请先选择数据目录和品种。")
            return
        issue = component_issue()
        if issue:
            QMessageBox.information(self, "免费实时更新", issue)
            return
        try:
            config = {"mode": mode, "root": str(self.repo.root.resolve()), "source": self.source_combo.currentText()}
            if mode == "current":
                target = self._live_target()
                config.update(symbol=target.symbol, contract=target.contract, tq_symbol=target.tq_symbol)
            dialog = TqLoginDialog(self)
            try:
                if dialog.exec() != QDialog.DialogCode.Accepted:
                    return
                username, password = dialog.username.text().strip(), dialog.password.text()
                dialog.password.clear()
            finally:
                dialog.deleteLater()
            self.live_controller.start(config, username, password)
        except Exception as exc:
            self._error(exc)

    def _live_target(self):
        tq_symbol, contract = self._edb_update_target()
        if self.source_combo.currentText() == "原始行情":
            from ..live_service import tq_contract_symbol
            exchange = self._exchange_for_symbol(self.symbol) or self.exchange_combo.currentText()
            tq_symbol = tq_contract_symbol(exchange, contract)
        return RealtimeTarget(self.repo.root, self.source_combo.currentText(), self.symbol, contract, tq_symbol)

    def _toggle_live_view(self, checked):
        if not checked:
            if self._live_chart is not None:
                self._live_chart.hide()
                self.live_controller.request_view(enabled=False)
            return
        if not self.live_controller.is_running():
            self._live_status("请先点击“实时更新当前”或“实时更新全部”开始后台采集。")
            self.live_view_action.setChecked(False)
            return
        try:
            target = self._live_target()
            if self._live_chart is None:
                self._live_chart = LiveChartDialog(self.live_controller, self)
                self._live_chart.closed.connect(self._live_view_closed)
            panel = self.panels[0]
            self._live_chart.configure(target, panel.period.currentText(), panel.chart.trend_settings)
            self._live_chart.raise_()
        except Exception as exc:
            self.live_view_action.setChecked(False)
            self._error(exc)

    def _live_view_closed(self):
        self.live_view_action.setChecked(False)


    def _edb_authentication(self) -> tuple[str | None, bool]:
        """Return (JWT, has_professional_token); credentials are never persisted."""
        token = os.environ.get("TQ_EDB_TOKEN", "").strip()
        if token:
            return token, True
        username = os.environ.get("TQ_EDB_USERNAME", "").strip()
        password = os.environ.get("TQ_EDB_PASSWORD", "")
        if username and password:
            return request_edb_token(username, password), True

        choice = QMessageBox.question(
            self,
            "天勤 EDB 权限",
            "是否使用天勤专业版账号登录 EDB？\n\n"
            "选择“是”输入账号密码，可按账号权限获取历史分钟数据；\n"
            "选择“否”使用免费权限（最近一年 1 分钟数据，日线可查询历史区间）。\n"
            "账号密码仅用于本次获取 Token，不会写入软件存档。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.No,
        )
        if choice == QMessageBox.StandardButton.Cancel:
            raise EDBUpdateCancelled("已取消 EDB 登录。")
        if choice == QMessageBox.StandardButton.No:
            return None, False

        username, ok = QInputDialog.getText(self, "天勤 EDB 登录", "天勤用户名：")
        if not ok or not username.strip():
            raise EDBUpdateCancelled("已取消 EDB 登录。")
        password, ok = QInputDialog.getText(
            self, "天勤 EDB 登录", "天勤密码：", QLineEdit.EchoMode.Password
        )
        if not ok or not password:
            raise EDBUpdateCancelled("已取消 EDB 登录。")
        return request_edb_token(username, password), True

    @staticmethod
    def _is_unadjusted_main_sequence_name(name: str) -> bool:
        value = str(name or "").lower()
        if any(flag in value for flag in ("前复权", "后复权", "复权", "adjust", "index", "指数")):
            return False
        return any(flag in value for flag in ("主连", "主力", "连续", "main", "0"))

    def _edb_update_target(self) -> tuple[str, str]:
        """Return (EDB symbol, local contract/sequence) for the visible chart."""
        if not self.panels or not self.symbol:
            raise EDBUpdateError("请先选择品种。")
        local_contract = self.panels[0].contract.currentText().strip()
        if not local_contract:
            raise EDBUpdateError("请先选择合约/序列。")
        source = self.source_combo.currentText()
        exchange = self._exchange_for_symbol(self.symbol) or self.exchange_combo.currentText()
        if source == "原始行情":
            return make_edb_contract_symbol(exchange, local_contract), local_contract
        if source == "主力连续":
            if not self._is_unadjusted_main_sequence_name(local_contract):
                raise EDBUpdateError(
                    "当前选择的不是未复权的主连/连续序列。为避免把 EDB 主连数据写入复权序列，已停止更新。"
                )
            return make_edb_main_symbol(exchange, self.symbol), local_contract
        raise EDBUpdateError(f"当前数据源“{source}”暂不支持 EDB 更新。")

    def _edb_incremental_ranges(self, source: str, symbol: str, contract: str, has_token: bool):
        minute_path = self.repo._resolve_file(source, symbol, "1min", contract)
        daily_path = self.repo._resolve_file(source, symbol, "1d", contract)
        return incremental_start_times(minute_path, daily_path, has_token)

    @staticmethod
    def _edb_stats_text(stats) -> str:
        def fmt(value):
            return str(value)[:19].replace("T", " ") if value is not None else "本次未返回"

        parts = [
            f"1分钟：新增 {stats.minute_added:,} 条，合计 {stats.minute_total:,} 条",
            f"日线：新增 {stats.daily_added:,} 条，合计 {stats.daily_total:,} 条",
            f"EDB本次返回1分钟最新：{fmt(getattr(stats, 'fetched_minute_last_timestamp', None))}",
            f"EDB本次返回日线最新：{fmt(getattr(stats, 'fetched_daily_last_timestamp', None))}",
            f"本地1分钟最新：{fmt(getattr(stats, 'minute_last_timestamp', None))}",
            f"本地日线最新：{fmt(getattr(stats, 'daily_last_timestamp', None))}",
        ]
        requested_end = getattr(stats, "requested_end", None)
        if requested_end is not None:
            parts.append(f"本次EDB请求截至：{fmt(requested_end)}")
        for period in ("5min", "15min", "30min", "1h", "4h"):
            if period in stats.period_rows:
                parts.append(f"合成 {period}：{stats.period_rows[period]:,} 根")
        minute_last = getattr(stats, "minute_last_timestamp", None)
        daily_last = getattr(stats, "daily_last_timestamp", None)
        if minute_last is not None and daily_last is not None:
            if pd.Timestamp(daily_last).date() > pd.Timestamp(minute_last).date():
                parts.append("注意：分钟数据落后于日线；分钟周期和由分钟合成的周期可能缺少后续交易日。")
        if requested_end is not None and minute_last is not None:
            lag_days = (pd.Timestamp(requested_end).date() - pd.Timestamp(minute_last).date()).days
            if lag_days > 3:
                parts.append(
                    "注意：本地分钟数据明显早于请求终点。请对照上面的“EDB本次返回1分钟最新”判断："
                    "若EDB返回也停在该日期，说明服务端本次没有提供更晚分钟数据；若EDB返回较新而本地较旧，则检查本地合并结果。"
                )
        return "\n".join(parts)

    def _update_edb_data(self):
        if self.repo is None:
            QMessageBox.information(self, "更新数据", "请先选择本地数据目录。")
            return
        try:
            edb_symbol, local_contract = self._edb_update_target()
            token, has_token = self._edb_authentication()
            source = self.source_combo.currentText()
            minute_start, daily_start = self._edb_incremental_ranges(
                source, self.symbol, local_contract, has_token
            )
        except EDBUpdateCancelled:
            return
        except Exception as exc:
            QMessageBox.warning(self, "更新数据", str(exc))
            return

        reply = QMessageBox.question(
            self,
            "天勤 EDB 更新数据",
            f"从天勤 EDB 更新 {edb_symbol}：\n"
            f"1分钟起始：{minute_start:%Y-%m-%d}\n"
            f"日线起始：{daily_start:%Y-%m-%d}\n\n"
            "程序先扫描最近六个自然月的分钟数据：检查有成交的交易日是否缺少分钟K线，并检查盘中连续缺口。"
            "发现缺口时，从最早缺口（含前一交易日晚盘）开始补到当前；没有缺口时只刷新最近数据。\n"
            "本次下载 EDB 原生1分钟和日线；5/15/30分钟、1小时、4小时由本地1分钟数据重新合成；"
            "周线和月线由日线聚合。重叠时间会刷新，不删除已有历史数据。是否继续？",
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._set_playing(False)
        old_state = self.engine.current() if self.engine is not None else None
        old_at_end = bool(self.engine is not None and getattr(self.engine, "at_end", False))
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            def progress(text: str):
                self.statusBar().showMessage("天勤 EDB：" + text)
                QApplication.processEvents()

            progress(f"开始更新 {edb_symbol} …")
            bundle = fetch_edb_bundle(
                edb_symbol, minute_start, daily_start, token,
                progress_cb=progress,
            )
            stats = persist_edb_bundle(self.repo, source, self.symbol, local_contract, bundle)
            # The symbol session may still hold a ReplayEngine built from the
            # pre-update parquet. Invalidate it before _load_symbol() so the
            # chart is rebuilt from the newly merged local bars.
            self._invalidate_replay_session_engine(source, self.symbol)
            self.repo.clear_cache()
            self.engine = None
            self._load_symbol()
            if self.engine is not None:
                if old_at_end:
                    self.engine.jump_bar(len(self.engine.bars) - 1, to_end_of_bar=True)
                elif old_state is not None:
                    self.engine.jump_timestamp(old_state.timestamp, old_state.trading_day)
                self._capture_current_session(force=True)
                self._checkpoint_replay_heads()
                self._refresh_all_panels()

            QMessageBox.information(
                self, "天勤 EDB 更新完成",
                f"{edb_symbol} 更新完成。\n\n{self._edb_stats_text(stats)}\n\n"
                "说明：EDB 免费权限提供最近一年的1分钟数据和历史日线；专业版权限可按账号权限获取更早的分钟历史。",
            )
            self.statusBar().showMessage(f"{edb_symbol} EDB 更新完成", 9000)
        except EDBUpdateCancelled:
            self.statusBar().showMessage("EDB 更新已取消", 7000)
        except Exception as exc:
            self._error(exc)
        finally:
            QApplication.restoreOverrideCursor()

    def _edb_update_target_for_symbol(self, source: str, symbol: str) -> tuple[str, str]:
        """Resolve the EDB symbol and local target for a product in bulk updates."""
        if self.repo is None:
            raise EDBUpdateError("尚未加载本地数据目录。")
        exchange = self._exchange_for_symbol(symbol)
        if source == "原始行情":
            local_contract = self.repo.select_active_contract(symbol, "1d", source)
            if not local_contract:
                contracts = self.repo.list_contracts(symbol, "1min", source)
                local_contract = contracts[-1] if contracts else None
            if not local_contract:
                raise EDBUpdateError(f"{symbol} 找不到可更新的本地合约。")
            return make_edb_contract_symbol(exchange, local_contract), local_contract

        if source == "主力连续":
            contracts = self.repo.list_contracts(symbol, "1d", source)
            if not contracts:
                contracts = self.repo.list_contracts(symbol, "1min", source)
            main = next((c for c in contracts if self._is_unadjusted_main_sequence_name(c)), None)
            if main is None:
                raise EDBUpdateError(f"{symbol} 没有可识别的主连/连续序列。")
            return make_edb_main_symbol(exchange, symbol), main
        raise EDBUpdateError(f"数据源“{source}”暂不支持 EDB 更新。")

    def _update_all_edb_data(self):
        """Incrementally update the active contract/main sequence of each product."""
        if self.repo is None:
            QMessageBox.information(self, "全部更新", "请先选择本地数据目录。")
            return
        source = self.source_combo.currentText()
        try:
            items = self.repo.list_instruments(source)
        except Exception as exc:
            self._error(exc)
            return
        if not items:
            QMessageBox.information(self, "全部更新", f"数据源“{source}”中没有可更新的品种。")
            return
        try:
            token, has_token = self._edb_authentication()
        except EDBUpdateCancelled:
            return
        except Exception as exc:
            QMessageBox.warning(self, "全部更新", str(exc))
            return

        reply = QMessageBox.question(
            self,
            "天勤 EDB 全部更新",
            f"将通过天勤 EDB 更新“{source}”中的 {len(items)} 个品种。\n\n"
            "原始行情：更新每个品种当前/最近的本地合约；主力连续：更新未复权主连序列。\n"
            "每个目标先扫描最近六个自然月：查找整日分钟数据缺失和盘中连续缺口；发现缺口时从最早缺口补到当前，无缺口时只刷新最近数据。\n"
            "只下载EDB原生1分钟和日线，其余分钟周期由本地1分钟数据重新合成，周线/月线由日线合成。\n"
            "重叠数据会刷新，旧数据不会删除。是否继续？",
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._set_playing(False)
        old_state = self.engine.current() if self.engine is not None else None
        old_at_end = bool(self.engine is not None and getattr(self.engine, "at_end", False))
        old_symbol = self.symbol
        progress = QProgressDialog("准备连接天勤 EDB…", "取消", 0, len(items), self)
        progress.setWindowTitle("天勤 EDB 全部更新")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.show()

        total_minute_added = total_daily_added = total_ok = 0
        failures: list[str] = []
        cancelled = False
        try:
            for number, meta in enumerate(items, start=1):
                if progress.wasCanceled():
                    cancelled = True
                    break
                progress.setValue(number - 1)
                progress.setLabelText(f"正在更新 {meta.symbol}（{number}/{len(items)}）…")
                self.statusBar().showMessage(f"EDB 全部更新：{meta.symbol}（{number}/{len(items)}）")
                QApplication.processEvents()
                try:
                    edb_symbol, local_contract = self._edb_update_target_for_symbol(source, meta.symbol)
                    minute_start, daily_start = self._edb_incremental_ranges(
                        source, meta.symbol, local_contract, has_token
                    )

                    def detail(message: str, sym=meta.symbol, n=number):
                        progress.setLabelText(f"{sym}（{n}/{len(items)}）：{message}")
                        QApplication.processEvents()

                    bundle = fetch_edb_bundle(
                        edb_symbol, minute_start, daily_start, token,
                        progress_cb=detail,
                        cancel_cb=progress.wasCanceled,
                    )
                    stats = persist_edb_bundle(self.repo, source, meta.symbol, local_contract, bundle)
                    self._invalidate_replay_session_engine(source, meta.symbol)
                    total_minute_added += stats.minute_added
                    total_daily_added += stats.daily_added
                    total_ok += 1
                except EDBUpdateCancelled:
                    cancelled = True
                    break
                except Exception as exc:
                    failures.append(f"{meta.symbol}: {exc}")
                progress.setValue(number)
                QApplication.processEvents()
        finally:
            progress.close()

        try:
            self.repo.clear_cache()
            if old_symbol and self.symbol == old_symbol:
                self.engine = None
                self._load_symbol()
                if self.engine is not None:
                    if old_at_end:
                        self.engine.jump_bar(len(self.engine.bars) - 1, to_end_of_bar=True)
                    elif old_state is not None:
                        self.engine.jump_timestamp(old_state.timestamp, old_state.trading_day)
                    self._capture_current_session(force=True)
                    self._checkpoint_replay_heads()
                    self._refresh_all_panels()
        except Exception as exc:
            failures.append(f"当前界面重载：{exc}")

        preview = "\n".join(failures[:12])
        if len(failures) > 12:
            preview += f"\n…另有 {len(failures) - 12} 个失败项"
        state_word = "已取消" if cancelled else "完成"
        message = (
            f"天勤 EDB 全部更新{state_word}。\n"
            f"成功品种：{total_ok}/{len(items)}\n"
            f"新增1分钟：{total_minute_added:,} 条\n"
            f"新增日线：{total_daily_added:,} 条"
        )
        if failures:
            message += f"\n\n失败 {len(failures)} 个：\n{preview}"
        message += "\n\n免费权限的1分钟数据范围为最近一年；更早分钟数据需开通专业版 EDB 权限。"
        QMessageBox.information(self, "天勤 EDB 全部更新", message)
        self.statusBar().showMessage(
            f"EDB 全部更新{state_word}：成功 {total_ok}/{len(items)}，新增1分钟 {total_minute_added:,} 条",
            12000,
        )

    def _rebuild_all_history(self):
        """Refresh every locally known raw contract from Tianqin EDB."""
        if self.repo is None:
            QMessageBox.information(self, "更新历史数据", "请先选择本地数据目录。")
            return
        try:
            items = self.repo.list_instruments("原始行情")
        except Exception as exc:
            self._error(exc)
            return
        if not items:
            QMessageBox.information(self, "更新历史数据", "原始行情目录中没有可识别的品种。")
            return
        try:
            token, has_token = self._edb_authentication()
        except EDBUpdateCancelled:
            return
        except Exception as exc:
            QMessageBox.warning(self, "更新历史数据", str(exc))
            return

        minute_start, daily_start = full_history_start_times(has_token)
        access_text = "专业版账号：从 EDB 可提供的历史范围（预计2021年以来）更新分钟线和日线" if has_token else "免费权限：最近一年分钟线 + EDB 可提供的历史日线"
        reply = QMessageBox.warning(
            self,
            "天勤 EDB 更新历史数据",
            f"将扫描原始行情中的 {len(items)} 个品种及其本地合约。\n\n"
            f"{access_text}。\n"
            "每个合约下载1分钟和日线；其他分钟周期由完整本地1分钟重新合成；周线/月线由日线聚合。\n"
            "不会删除 EDB 未覆盖的旧本地数据。全量任务可能耗时较长。是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._set_playing(False)
        old_state = self.engine.current() if self.engine is not None else None
        old_symbol = self.symbol
        total_contracts = total_files = total_minute_added = total_daily_added = 0
        failures: list[str] = []
        cancelled = False
        jobs: list[tuple[object, str]] = []
        for meta in items:
            contracts = set(self.repo.list_contracts(meta.symbol, "1min", "原始行情"))
            contracts.update(self.repo.list_contracts(meta.symbol, "1d", "原始行情"))
            jobs.extend((meta, contract) for contract in sorted(contracts))
        if not jobs:
            QMessageBox.information(self, "更新历史数据", "没有找到可更新的本地原始合约。")
            return

        progress = QProgressDialog("准备天勤 EDB 历史更新…", "取消", 0, len(jobs), self)
        progress.setWindowTitle("天勤 EDB 更新历史数据")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.show()
        try:
            for number, (meta, contract) in enumerate(jobs, start=1):
                if progress.wasCanceled():
                    cancelled = True
                    break
                total_contracts += 1
                progress.setValue(number - 1)
                progress.setLabelText(f"{meta.symbol} {contract}（{number}/{len(jobs)}）")
                QApplication.processEvents()
                try:
                    edb_symbol = make_edb_contract_symbol(meta.exchange, contract)
                    bundle = fetch_edb_bundle(
                        edb_symbol, minute_start, daily_start, token,
                        progress_cb=lambda msg, n=number, m=meta.symbol, c=contract: (
                            progress.setLabelText(f"{m} {c}（{n}/{len(jobs)}）：{msg}"), QApplication.processEvents()
                        ),
                        cancel_cb=progress.wasCanceled,
                    )
                    stats = persist_edb_bundle(self.repo, "原始行情", meta.symbol, contract, bundle)
                    total_minute_added += stats.minute_added
                    total_daily_added += stats.daily_added
                    total_files += 2 + len(stats.period_rows)
                except EDBUpdateCancelled:
                    cancelled = True
                    break
                except Exception as exc:
                    failures.append(f"{meta.symbol}/{contract}: {exc}")
                progress.setValue(number)
                QApplication.processEvents()
        finally:
            progress.close()

        try:
            self.repo.clear_cache()
            self._load_instruments()
            if old_symbol:
                for idx in range(self.symbol_combo.count()):
                    if self.symbol_combo.itemData(idx) == old_symbol:
                        self.symbol_combo.setCurrentIndex(idx)
                        break
            if old_state is not None and self.engine is not None:
                self.engine.jump_timestamp(old_state.timestamp, old_state.trading_day)
                self._refresh_all_panels()
        except Exception as exc:
            failures.append(f"界面重载：{exc}")

        state_word = "已取消" if cancelled else "完成"
        detail = (
            f"天勤 EDB 历史更新{state_word}。\n"
            f"扫描合约：{total_contracts}\n"
            f"新增1分钟：{total_minute_added:,} 条\n"
            f"新增日线：{total_daily_added:,} 条\n"
            f"处理文件：{total_files}"
        )
        if failures:
            detail += f"\n\n失败 {len(failures)} 项：\n" + "\n".join(failures[:15])
        detail += "\n\nEDB免费权限仅提供最近一年的分钟线；更早的分钟历史需专业版权限。"
        QMessageBox.information(self, "天勤 EDB 更新历史数据", detail)
        self.statusBar().showMessage(f"EDB 历史更新{state_word}：新增分钟 {total_minute_added:,} 条", 12000)

    def _tool_changed(self, text):
        for p in self.panels: p.chart.set_tool(text)
        self._schedule_save()

    def _record_symbol_label(self, symbol: str | None = None) -> str:
        """Human-readable product/contract label used by records and positions."""
        raw = str(symbol or self._current_trade_key() or self.symbol or "").strip()
        if not raw:
            return ""
        source, code, contract = TradingAccount.split_trade_key(raw)
        if source or contract:
            return self._trade_key_label(raw)
        for meta in getattr(self, "_all_items", []) or []:
            if str(getattr(meta, "symbol", "")) == code:
                text = str(getattr(meta, "display", "") or "").strip()
                if text:
                    return text
        if code == str(self.symbol or ""):
            text = self.symbol_combo.currentText().strip()
            if text:
                return text
        return code

    def _price_clicked(self, panel: ChartPanel, price: float):
        if panel is not self.active_panel or not self.engine:
            return
        tool = self.tool_combo.currentText(); qty = self.trade.qty.value()
        key = self._current_trade_key()
        if not key:
            return
        self._ensure_record_day()
        if tool == "限价买":
            order = self.account.place_limit(key, OrderSide.BUY, price, qty)
            self.records.add_limit_order(self._record_time(), self._record_symbol_label(key), order.id, "限价买", price, qty, "等待")
            self._mark_current_trade_clock()
        elif tool == "限价卖":
            order = self.account.place_limit(key, OrderSide.SELL, price, qty)
            self.records.add_limit_order(self._record_time(), self._record_symbol_label(key), order.id, "限价卖", price, qty, "等待")
            self._mark_current_trade_clock()
        self._refresh_all_panels(); self._schedule_save()

    def _trade_action(self, action: str, qty: int):
        # The selected-position full-close command is deliberately independent
        # of the currently displayed chart instrument.  It acts only on the
        # exact contract key selected in the holdings table.
        if action == "全平所选":
            selected_key = self.trade.selected_position_key()
            if not selected_key:
                QMessageBox.information(self, "全平所选", "请先在持仓列表中选择要全平的合约。")
                return
            position = self.account.position_for(selected_key)
            if position is None:
                QMessageBox.information(self, "全平所选", "所选持仓已经不存在，请刷新持仓列表后重试。")
                self._refresh_all_panels()
                return
            price = float(self.account.mark_for(selected_key) or position.avg_price or 0.0)
            current_key = self._current_trade_key()
            if self.engine is not None and current_key == selected_key:
                try:
                    price = float(self.engine.current().partial["close"])
                except Exception:
                    pass
            if price <= 0:
                QMessageBox.warning(self, "全平所选", "无法取得所选合约的有效最新价，未执行平仓。")
                return
            self.account.set_mark(selected_key, price)
            self._ensure_record_day()
            try:
                ev = self.account.close(None, price, symbol=selected_key)
                label = self._record_symbol_label(selected_key)
                self.records.add_market_order(
                    self._record_time(), label, "全平所选", ev.price, ev.qty, "成交"
                )
                self._record_fill(ev, selected_key)
                self._reconcile_conditionals_after_position_change(selected_key)
                if current_key == selected_key and self.engine is not None:
                    self._mark_current_trade_clock()
                self.statusBar().showMessage(
                    f"{label}：{ev.message}", 9000
                )
                self._refresh_all_panels()
                self._schedule_save()
            except Exception as exc:
                self.records.add_market_order(
                    self._record_time(), self._record_symbol_label(selected_key),
                    "全平所选", price, abs(int(position.qty)), f"失败: {exc}"
                )
                QMessageBox.warning(self, "全平所选失败", str(exc))
                self._refresh_all_panels()
                self._schedule_save()
            return

        if not self.engine:
            return
        price = float(self.engine.current().partial["close"])
        key = self._current_trade_key()
        if not key:
            return
        self.account.set_mark(key, price)
        self._ensure_record_day()
        try:
            if action == "开多":
                ev = self.account.open_market(key, Side.LONG, qty, price)
            elif action == "开空":
                ev = self.account.open_market(key, Side.SHORT, qty, price)
            elif action == "平仓":
                ev = self.account.close(qty, price, symbol=key)
            elif action == "全部平仓":
                events = self.account.close_all({key: price})
                if not events:
                    raise ValueError("当前无持仓")
                messages = []
                for sym, fill in events:
                    self.records.add_market_order(
                        self._record_time(), self._record_symbol_label(sym), "全部平仓", fill.price, fill.qty, "成交"
                    )
                    self._record_fill(fill, sym)
                    self._reconcile_conditionals_after_position_change(sym)
                    messages.append(f"{self._record_symbol_label(sym)}：{fill.message}")
                self.statusBar().showMessage("；".join(messages), 9000)
                self._refresh_all_panels()
                self._schedule_save()
                return
            elif action == "反多":
                ev = self.account.reverse(key, Side.LONG, qty, price)
            elif action == "反空":
                ev = self.account.reverse(key, Side.SHORT, qty, price)
            else:
                return

            self.records.add_market_order(
                self._record_time(), self._record_symbol_label(key), action, price, ev.qty, "成交"
            )
            self._record_fill(ev, key)
            self._reconcile_conditionals_after_position_change(key)
            self._mark_current_trade_clock()
            self.statusBar().showMessage(ev.message, 7000)
        except Exception as exc:
            self.records.add_market_order(
                self._record_time(), self._record_symbol_label(key), action, price, qty, f"失败: {exc}"
            )
            QMessageBox.warning(self, "交易失败", str(exc))
        self._refresh_all_panels()
        self._schedule_save()

    def _ensure_record_day(self):
        # "Today" means the computer's real calendar day on which the user is
        # operating the trainer.  It must never follow the replay contract date.
        self.records.ensure_day(pd.Timestamp.now().strftime("%Y-%m-%d"))

    def _record_time(self) -> str:
        # Keep the real operation time in CSV; the right-side UI shows HH:MM:SS.
        return pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")

    def _record_fill(self, ev, symbol: str | None = None):
        self._ensure_record_day()
        code = str(symbol or getattr(ev, "symbol", None) or self.symbol or "")
        self.records.add_trade(
            self._record_time(),
            self._record_symbol_label(code),
            ev.message,
            ev.price,
            ev.qty,
            ev.realized_pnl,
            trade_key=code,
        )

    def _refresh_trade_qty_from_available(self):
        """Refresh order quantity for the active quick-sizing mode.

        Ratio buttons use the account's *current available funds*.  The first
        fixed-capital button instead answers a different question: how many lots
        the configured reference capital (default RMB 1,000,000) can open at the
        current price, contract multiplier and margin rate.
        """
        if not self.engine:
            return
        key = self._current_trade_key()
        if not key:
            return
        try:
            price = float(self.engine.current().partial["close"])
            if price <= 0:
                return
            mx = self.account.max_open_qty(key, price)
            if self.trade.is_capital_sizing_active():
                per_lot_margin = price * self.account.multiplier(key) * self.account.margin_rate
                if per_lot_margin <= 0:
                    return
                budget = self.trade.capital_budget()
                budget_qty = max(0, int(float(budget) // float(per_lot_margin)))
                self.trade.refresh_qty_from_capital(budget_qty, per_lot_margin, mx)
                return
        except Exception:
            return
        self.trade.refresh_qty_from_capacity(mx)

    def _set_fraction(self, ratio: float):
        self._refresh_trade_qty_from_available()

    def _set_capital_sizing(self, amount: float):
        self._refresh_trade_qty_from_available()

    def _qty_manually_edited(self):
        self._refresh_trade_qty_from_available()

    def _deposit(self, amount: float):
        self.account.deposit(amount)
        self._refresh_all_panels()
        self._schedule_save()

    def _reset_account(self):
        initial_text = f"{float(DEFAULT_INITIAL_CAPITAL):,.2f}"
        text = (
            f"确定清空模拟账户并恢复初始资金 {initial_text} 吗？\n\n"
            "将清除全部持仓、挂单、止盈止损和累计盈亏。\n"
            "行情数据、当前复盘位置、画线、回测结果和当天成交记录不会删除。"
        )
        if QMessageBox.question(self, "清账户", text) != QMessageBox.StandardButton.Yes:
            return

        self._pause_all_sessions()
        # Normal limit-order history remains auditable. TP/SL conditionals, however,
        # are live instructions only and disappear when the account is reset.
        for order in list(self.account.orders):
            try:
                self.records.update_order_status(order.id, "账户已重置")
            except Exception:
                pass
        self.records.remove_all_conditionals()

        margin_rate = float(getattr(self.account, "margin_rate", 0.1))
        self.account = TradingAccount(DEFAULT_INITIAL_CAPITAL, margin_rate)
        self._market_processed_ts.clear()
        self._sync_minute_cache.clear()
        self._refresh_all_panels()
        self._save_now()
        self.statusBar().showMessage(f"账户已恢复为初始资金 {initial_text}", 5000)

    def _cancel_order(self, oid):
        self._ensure_record_day()
        # Normal limit-order IDs are integers. Conditional TP/SL rows use C... IDs
        # and carry their trade_key/kind in the session record.
        text = str(oid).strip()
        row = self.records.get_order(text)
        if (
            row is not None
            and str(row.get("kind", "")) in ("止盈", "止损")
            and str(row.get("status", "")) in ("未触发", "等待", "挂单")
        ):
            key = str(row.get("trade_key", ""))
            kind = str(row.get("kind", ""))
            p = self.account.position_for(key) if key else None
            if p is not None:
                if kind == "止盈":
                    p.take_profit = None
                else:
                    p.stop_loss = None
            self.records.remove_conditional(key, kind)
        else:
            try:
                numeric_id = int(oid)
            except Exception:
                numeric_id = -1
            if numeric_id >= 0 and self.account.cancel(numeric_id):
                self.records.update_order_status(numeric_id, "已撤销")
        self._refresh_all_panels(); self._schedule_save()

    def _cancel_all(self):
        self._ensure_record_day()
        ids = [o.id for o in self.account.orders]
        self.account.cancel_all()
        for oid in ids:
            self.records.update_order_status(oid, "已撤销")
        # “全部撤销” applies to all active instructions shown in 委托记录.
        # TP/SL rows are removed outright rather than retained as 已撤销 history.
        for p in self.account.positions.values():
            p.take_profit = None
            p.stop_loss = None
        self.records.remove_all_conditionals()
        self._refresh_all_panels(); self._schedule_save()

    def _conditional_action_text(self, p, kind: str) -> str:
        close_side = "卖平" if p.side == Side.LONG else "买平"
        return f"{kind}{close_side}"

    def _record_conditional(self, key: str, kind: str, price: float) -> None:
        p = self.account.position_for(key)
        if p is None:
            return
        self._ensure_record_day()
        self.records.upsert_conditional_order(
            self._record_time(),
            self._record_symbol_label(key),
            key,
            kind,
            self._conditional_action_text(p, kind),
            float(price),
            int(p.qty),
            "未触发",
        )

    def _reconcile_conditionals_after_position_change(self, key: str) -> None:
        """Keep active TP/SL records aligned with the latest position quantity.

        Market opens/closes/reversals may change the position quantity without
        changing a TP/SL price.  Existing conditional rows therefore update their
        hand count.  If the old position disappears or a reverse creates a fresh
        position with no TP/SL, the obsolete conditional rows are removed.
        """
        p = self.account.position_for(key)
        if p is None:
            self.records.remove_conditionals_for_trade_key(key)
            return
        if p.take_profit is None:
            self.records.remove_conditional(key, "止盈")
        if p.stop_loss is None:
            self.records.remove_conditional(key, "止损")
        self.records.update_conditional_qty(key, int(p.qty))

    def _trade_line_dragged(self, name: str, value: float):
        # Cost/TP/SL lines always belong to the instrument currently on the chart.
        # This signal is emitted on mouse release, not for every drag pixel.  The
        # chart itself handles real-time preview; 委托记录 is updated once here.
        key = self._current_trade_key()
        p = self.account.position_for(key)
        if p is None:
            return
        kind = None
        if name == "止盈":
            self.account.set_take_profit(value, key)
            kind = "止盈"
        elif name == "止损":
            self.account.set_stop_loss(value, key)
            kind = "止损"
        elif name == "成本":
            # Dragging cost line is a shortcut: direction relative to average determines TP/SL.
            if p.side == Side.LONG:
                if value >= p.avg_price:
                    self.account.set_take_profit(value, key)
                    kind = "止盈"
                else:
                    self.account.set_stop_loss(value, key)
                    kind = "止损"
            else:
                if value <= p.avg_price:
                    self.account.set_take_profit(value, key)
                    kind = "止盈"
                else:
                    self.account.set_stop_loss(value, key)
                    kind = "止损"
        if kind is not None:
            # First drag creates one conditional row. Later drags update that same
            # active row (price, quantity and latest modification time).
            self._record_conditional(key, kind, value)
        self._refresh_all_panels()
        self._schedule_save()

    def _trade_line_delete_requested(self, name: str):
        """Delete the clicked TP/SL line and remove its live 委托记录 row."""
        key = self._current_trade_key()
        p = self.account.position_for(key) if key else None
        if p is None:
            return
        if name == "止盈":
            p.take_profit = None
            self.records.remove_conditional(key, "止盈")
        elif name == "止损":
            p.stop_loss = None
            self.records.remove_conditional(key, "止损")
        else:
            return
        self._refresh_all_panels()
        self._schedule_save()

    def _update_trade_lines(self, panel: ChartPanel):
        key = self._current_trade_key()
        p = self.account.position_for(key) if key else None
        cost = take = stop = None
        direction = None
        qty = 0
        multiplier = 0.0
        if p is not None:
            cost, take, stop = p.avg_price, p.take_profit, p.stop_loss
            direction = 1.0 if p.side == Side.LONG else -1.0
            qty = int(p.qty)
            multiplier = float(self.account.multiplier(key))
        limits = [(f"#{o.id} {o.side.value}", o.price) for o in self.account.orders if o.symbol == key]
        panel.chart.set_trade_lines(
            cost, take, stop, limits,
            direction=direction, qty=qty, multiplier=multiplier,
        )

    def _clear_drawings(self):
        for p in self.panels: p.chart.drawings.clear(); p.chart.update()
        self._schedule_save()
    def _clear_state(self):
        if QMessageBox.question(self, "确认", "清空存档并将账户、复盘位置、画线和回测结果重置？") != QMessageBox.StandardButton.Yes:
            return
        self._pause_all_sessions()
        self.save_timer.stop()
        clear_state()
        self.state = {}
        self._startup_replay = {}
        self._startup_replay_pending = False
        self._saved_replay_sessions.clear()
        self._replay_sessions.clear()
        self._played_order.clear()
        self._main_quote_cache.clear()
        self.account = TradingAccount(DEFAULT_INITIAL_CAPITAL)
        self._market_processed_ts.clear()
        self._sync_minute_cache.clear()
        for p in self.panels:
            p.chart.drawings.clear()
            p.chart.clear_backtest_markers()
        if hasattr(self, "backtest"):
            self.backtest.reset_session_state()
        self.follow.setChecked(True)
        self._set_speed_seconds(0.30)
        if self.engine is not None:
            self.engine.jump_bar(0)
        self._refresh_all_panels()
        # Persist the clean default state immediately; later closing the program
        # therefore cannot resurrect settings from before the reset.
        self._save_now()
        self.statusBar().showMessage("存档已清空并重置", 5000)

    def _restore_ui(self):
        try:
            ui = self.state.get("ui", {})
            # Restore window/dock geometry before loading market data.
            try:
                if ui.get("window_geometry"):
                    self.restoreGeometry(QByteArray(bytes.fromhex(str(ui.get("window_geometry")))))
                if ui.get("dock_state"):
                    self.restoreState(QByteArray(bytes.fromhex(str(ui.get("dock_state")))))
                if ui.get("trade_dock_geometry") and self.trade_dock.isFloating():
                    self.trade_dock.restoreGeometry(QByteArray(bytes.fromhex(str(ui.get("trade_dock_geometry")))))
                # Layout v2 changes the order-entry panel from a tall column to a
                # wide compact strip.  Old saved geometries would otherwise keep
                # reopening as the obsolete tall shape, so migrate them once.
                if int(ui.get("trade_layout_version", 0) or 0) < 2:
                    if self.trade_dock.isFloating():
                        self.trade_dock.resize(860, 300)
                    else:
                        area = self.dockWidgetArea(self.trade_dock)
                        if area in (Qt.DockWidgetArea.LeftDockWidgetArea, Qt.DockWidgetArea.RightDockWidgetArea):
                            self.resizeDocks([self.trade_dock], [760], Qt.Orientation.Horizontal)
                        else:
                            self.resizeDocks([self.trade_dock], [300], Qt.Orientation.Vertical)
                # V2.5 briefly stored a floating main-list dock geometry.  The
                # main list is fixed in the right tab area from V2.6 onward, so
                # that obsolete geometry is intentionally ignored.
            except Exception:
                pass
            main_list_state = ui.get("main_list_table")
            # The dedicated sort preference is authoritative because it is written
            # immediately on every header sort and on close.  Merge it over the
            # older full-session copy for backward compatibility.
            merged_main_list_state = dict(main_list_state) if isinstance(main_list_state, dict) else {}
            if isinstance(self._main_list_sort_pref, dict):
                for key in ("sort_column", "sort_order", "symbol_order"):
                    if key in self._main_list_sort_pref:
                        merged_main_list_state[key] = self._main_list_sort_pref[key]
            saved_order = merged_main_list_state.get("symbol_order", [])
            self._restore_main_list_symbol_order = [str(x) for x in saved_order if str(x)] if isinstance(saved_order, (list, tuple)) else []
            self._restore_header_state(self.main_list, merged_main_list_state)
            # The symbol/exchange controls are populated only after the repository is
            # scanned.  Keep their saved values aside and apply them in
            # _load_instruments/_filter_symbols so a restart returns to the exact
            # instrument the user was training on instead of silently choosing the
            # first instrument in the list.
            replay_saved = self._startup_replay or self.state.get("replay", {})
            self._restore_exchange = str(ui.get("exchange", "") or replay_saved.get("exchange", "") or "")
            self._restore_symbol = str(ui.get("symbol", "") or replay_saved.get("symbol", "") or "")
            # Show the last replay date immediately on startup; once the engine
            # is restored _refresh_all_panels will set the exact same date again.
            saved_day = str(replay_saved.get("display_date", "") or replay_saved.get("trading_day", "") or "")[:10]
            if saved_day:
                qd = QDate.fromString(saved_day, "yyyy-MM-dd")
                if qd.isValid():
                    self.date_edit.blockSignals(True)
                    self.date_edit.setDate(qd)
                    self.date_edit.blockSignals(False)
            if ui.get("source"):
                self.source_combo.setCurrentText(ui["source"])
            if ui.get("speed_seconds") is not None:
                self._set_speed_seconds(float(ui["speed_seconds"]))
            else:
                # Old versions stored a step-based slider value.  Do not carry
                # that scale into the new seconds-based control.
                self._set_speed_seconds(0.30)
            if ui.get("follow") is not None:
                self.follow.setChecked(bool(ui["follow"]))
            if ui.get("windows"):
                self.window_combo.setCurrentText(str(ui["windows"]))
                self._rebuild_panels(int(ui["windows"]))
            if ui.get("splitter"):
                self.main_split.setSizes([int(x) for x in ui["splitter"]])
            if ui.get("chart_splitter"):
                self.chart_split.setSizes([int(x) for x in ui["chart_splitter"]])
            if ui.get("right_tab") is not None and hasattr(self, "right_tabs"):
                self.right_tabs.setCurrentIndex(max(0, min(int(ui.get("right_tab", 0)), self.right_tabs.count() - 1)))
            if ui.get("capital_budget") is not None:
                self.trade.set_capital_budget(float(ui.get("capital_budget")))
            if ui.get("fractions"):
                self.trade.set_fraction_specs(
                    ui.get("fractions"),
                    replace_legacy_first=(ui.get("capital_budget") is None),
                )
            if ui.get("quick_button_order"):
                self.trade.set_quick_button_order(ui.get("quick_button_order"))
            if ui.get("table_layouts"):
                self.trade.restore_table_layout_state(ui.get("table_layouts"))
            if ui.get("backtest"):
                self.backtest.restore_state(ui.get("backtest"))
            for panel_index, (p, d) in enumerate(zip(self.panels, ui.get("panels", []))):
                saved_intraday = bool(d.get("intraday", False))
                if panel_index == 0 and replay_saved.get("period"):
                    p.period.set_period(str(replay_saved.get("period")), intraday=saved_intraday)
                else:
                    p.period.set_period(str(d.get("period", "5min")), intraday=saved_intraday)
                p.apply_period_mode()
                p.ma_enabled.setChecked(bool(d.get("ma_enabled", True)))
                p.ma_values.setText(str(d.get("ma_values", "5,10,20,60")))
                p.volume.setChecked(bool(d.get("volume", True)))
                p.vma5.setChecked(bool(d.get("volume_vma5", True)))
                p.vma10.setChecked(bool(d.get("volume_vma10", True)))
                p.oi_line.setChecked(bool(d.get("volume_oi_line", True)))
                # Backward compatibility: older saves stored BOLL in the副图 combo.
                saved_indicator = str(d.get("indicator", "无"))
                if saved_indicator == "BOLL":
                    p.boll.setChecked(True)
                    p.indicator.setCurrentText("无")
                else:
                    p.boll.setChecked(bool(d.get("boll", False)))
                    p.indicator.setCurrentText(saved_indicator if p.indicator.findText(saved_indicator) >= 0 else "无")
                p.wave.setChecked(bool(d.get("wave", False)))
                p.trend.setChecked(bool(d.get("trend", False)))
                p.chart.set_trend_settings(d.get("trend_settings", {}))
                if panel_index == 0:
                    p._restore_contract = str(d.get("contract", "") or replay_saved.get("contract", "") or "")
                else:
                    p._restore_contract = d.get("contract", "")
                p.chart.drawings = [Drawing.from_dict(x) for x in d.get("drawings", [])]
                p.chart.restore_panel_layout_state(d.get("chart_layout"))
                p.apply_options()
        except Exception:
            pass

    def _schedule_save(self):
        self.save_timer.start(800)

    def _save_now(self):
        panels = []
        for p in self.panels:
            panels.append({
                "period": p.period.currentText(),
                "intraday": p.period.is_intraday(),
                "contract": p.contract.currentText(),
                "ma_enabled": p.ma_enabled.isChecked(),
                "ma_values": p.ma_values.text(),
                "boll": p.boll.isChecked(),
                "wave": p.wave.isChecked(),
                "trend": p.trend.isChecked(),
                "trend_settings": p.chart.trend_settings,
                "volume": p.volume.isChecked(),
                "volume_vma5": p.vma5.isChecked(),
                "volume_vma10": p.vma10.isChecked(),
                "volume_oi_line": p.oi_line.isChecked(),
                "indicator": p.indicator.currentText(),
                "drawings": [d.to_dict() for d in p.chart.drawings],
                "chart_layout": p.chart.panel_layout_state(),
            })
        replay = {}
        saved_exchange = self._exchange_for_symbol(self.symbol) or self.exchange_combo.currentText()
        if self.engine:
            st = self.engine.current()
            replay = {
                "source": self.source_combo.currentText(),
                "exchange": saved_exchange,
                "symbol": self.symbol,
                "period": self.panels[0].period.currentText(),
                "contract": self.panels[0].contract.currentText(),
                "bar_index": self.engine.bar_index,
                "minute_index": self.engine.minute_index,
                "timestamp": str(st.timestamp),
                "trading_day": st.trading_day,
                "display_date": str(st.trading_day)[:10],
            }
        sessions = self._serialised_replay_sessions()
        self._saved_replay_sessions = {k: dict(v) for k, v in sessions.items()}
        self._checkpoint_replay_heads(sessions)
        main_list_state = self._header_state(self.main_list)
        main_list_state["symbol_order"] = self._main_list_symbol_order()
        try:
            self._main_list_sort_pref = {
                "sort_column": int(main_list_state.get("sort_column", 0)),
                "sort_order": str(main_list_state.get("sort_order", "asc")),
                "symbol_order": list(main_list_state.get("symbol_order", [])),
            }
            save_main_list_sort(
                self._main_list_sort_pref["sort_column"],
                self._main_list_sort_pref["sort_order"],
                self._main_list_sort_pref["symbol_order"],
            )
        except Exception:
            pass
        save_state({
            "data_root": str(self.data_root) if self.data_root else "",
            "account": self.account.to_dict(),
            "replay": replay,
            "replay_sessions": sessions,
            "played_sessions": list(self._played_order),
            "ui": {
                "source": self.source_combo.currentText(),
                "exchange": saved_exchange,
                "symbol": self.symbol,
                "speed": self.speed.value(),
                "speed_seconds": self._speed_seconds(),
                "follow": self.follow.isChecked(),
                "windows": len(self.panels),
                "splitter": self.main_split.sizes(),
                "chart_splitter": self.chart_split.sizes(),
                "right_tab": self.right_tabs.currentIndex() if hasattr(self, "right_tabs") else 0,
                "fractions": self.trade.fraction_specs(),
                "capital_budget": self.trade.capital_budget(),
                "quick_button_order": self.trade.quick_button_order(),
                "table_layouts": self.trade.table_layout_state(),
                "main_list_table": main_list_state,
                "window_geometry": bytes(self.saveGeometry()).hex(),
                "dock_state": bytes(self.saveState()).hex(),
                "trade_dock_geometry": bytes(self.trade_dock.saveGeometry()).hex(),
                "trade_layout_version": 2,
                "backtest": self.backtest.state(),
                "panels": panels,
            },
        })

    def showEvent(self, event):
        super().showEvent(event)
        # Enforce the requested visible order after the window is actually shown:
        # ... 自动跟随 -> 随机日期 -> 从最早开始 ->
        # 时间滑条 -> 时间 -> 全部暂停/全播放 -> 播放/暂停.
        QTimer.singleShot(0, self._apply_top_replay_order)

    def closeEvent(self, event):
        self.live_controller.shutdown()
        self.play_timer.stop()
        self.save_timer.stop()
        # Save the visible list order before any shutdown/session synchronization.
        # This guarantees that the order on the screen at the moment of closing
        # is exactly the order restored next time.
        self._persist_main_list_sort()
        for _sess in list(self._replay_sessions.values()):
            self._sync_session_from_engine(_sess)
        try:
            self._checkpoint_replay_heads()
            self._save_now()
        except Exception:
            pass
        if hasattr(self, "backtest") and not self.backtest.stop_and_wait(5000):
            QMessageBox.information(self, "正在停止回测", "回测线程仍在结束中，请稍等片刻后再关闭软件。")
            event.ignore()
            return
        try:
            self._save_now()
        finally:
            super().closeEvent(event)
    def _error(self, exc: Exception): QMessageBox.critical(self,"错误",f"{exc}\n\n{traceback.format_exc()[-1500:]}")
