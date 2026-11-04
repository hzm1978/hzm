from __future__ import annotations

import pandas as pd
import hashlib
from PySide6.QtCore import Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QHBoxLayout,
                              QLabel, QPushButton, QVBoxLayout)

from .. import __version__
from ..realtime_data import RealtimeSnapshot, chart_frame, shanghai_time
from .chart_widget import CandleChart
from .local_history import LocalHistoryLoader
from .trend_dialog import TrendIndicatorDialog


class LiveChartDialog(QDialog):
    closed = Signal()

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller, self.target = controller, None
        self._closed_signature = None
        self._history_target = None
        self._request_key = None
        self._request_token = 0
        self._received_live = False
        self._data_text = self._network_text = self._local_note = ""
        self.history = LocalHistoryLoader(self)
        self.history.ready.connect(self._local_ready)
        self.setWindowTitle(f"实时跟随 v{__version__} · 最新K线与免费行情")
        self.resize(1250, 800)
        layout = QVBoxLayout(self)
        self.title = QLabel("实时行情独立显示，原复盘位置和模拟账户保持不变。")
        self.title.setToolTip(f"运行版本 v{__version__}\n{__file__}")
        layout.addWidget(self.title)
        row = QHBoxLayout(); row.addWidget(QLabel("周期"))
        self.period = QComboBox()
        for label, code in [("1分钟", "1min"), ("5分钟", "5min"), ("15分钟", "15min"),
                            ("30分钟", "30min"), ("1小时", "1h"), ("4小时", "4h"), ("日线", "1d")]:
            self.period.addItem(label, code)
        row.addWidget(self.period)
        self.trend = QCheckBox("趋势交易"); self.trend.setChecked(True); row.addWidget(self.trend)
        self.volume = QCheckBox("成交量"); self.volume.setChecked(True); row.addWidget(self.volume)
        details = QPushButton("指标明细"); details.clicked.connect(self._details); row.addWidget(details)
        row.addStretch(); layout.addLayout(row)
        self.status = QLabel("等待后台行情…"); self.status.setWordWrap(True); layout.addWidget(self.status)
        self.chart = CandleChart(); self.chart.show_ma = False
        self.chart.set_trend_visible(True); self.chart.set_wave_visible(False)
        layout.addWidget(self.chart, 1)
        note = QLabel("关闭此窗口后，后台仍继续更新。末根动态K线用于预览，正式趋势指标排除未结束K线。")
        note.setWordWrap(True); layout.addWidget(note)
        self.period.currentIndexChanged.connect(self._request)
        self.trend.toggled.connect(self.chart.set_trend_visible)
        self.volume.toggled.connect(self.chart.set_volume_visible)
        self.controller.previewReady.connect(self._preview)
        self.controller.statusChanged.connect(self._backend_status)
        self.controller.viewStatusChanged.connect(self._view_status)

    def configure(self, target, period="1min", settings=None, *, history_target=None):
        self.target = target
        self._history_target = history_target or target
        if period in {"1w", "1M"}:
            period = "1d"
        self.title.setText(f"{target.source} · {target.symbol} · {target.contract}　实时跟随（原复盘保持原位）")
        self.chart.set_trend_settings(settings or {})
        self.period.blockSignals(True)
        self.period.setCurrentIndex(max(0, self.period.findData(period)))
        self.period.blockSignals(False)
        self.show(); self._request()

    def _request(self, *_):
        if self.target and self.isVisible():
            period = self.period.currentData()
            key = (str(self.target.root), self.target.source, self.target.symbol,
                   self.target.contract, self.target.tq_symbol, period,
                   str(self._history_target.root), self._history_target.source,
                   self._history_target.symbol, self._history_target.contract)
            if key != self._request_key:
                # Clear only when the contract/period changes. Reopening or
                # restarting the same subscription must retain its last bars.
                self._closed_signature = None
                self._data_text = ""
                self.chart.set_series(pd.DataFrame(columns=["datetime", "open", "high", "low", "close"]), -1)
                self._request_key = key
            self.chart.empty_text = "正在加载最新K线…"
            self.chart.set_follow_latest(True)
            self._request_token += 1
            self._received_live = False
            self._local_note = "正在读取本地最新K线…"
            self._network_text = (f"正在获取 {self.target.symbol} 的{self.period.currentText()}实时行情…"
                                  if self.controller.is_running() else "后台未启动，显示已有本地K线")
            self._refresh_status()
            self.history.request(self._history_target, period, self._request_token)
            self.controller.request_view(self.target.tq_symbol, period, target=self.target)

    def _refresh_status(self):
        parts = [self._data_text, "" if self._data_text else self._local_note, self._network_text]
        self.status.setText("　".join(x for x in parts if x))

    def _local_ready(self, result):
        if (not self.isVisible() or result["token"] != self._request_token or self._received_live):
            return
        self._local_note = ""
        frame = result["frame"]
        if frame.empty:
            self._local_note = "本地暂无该周期K线" if not result["error"] or result["error"].startswith("FileNotFoundError") else "本地K线读取未完成，详情见提示"
            self.status.setToolTip(result["error"])
            if self.chart.df.empty:
                self.chart.empty_text = self._local_note
                self.chart.update()
            self._refresh_status()
            return
        if not self.chart.df.empty and frame.datetime.iloc[-1] < self.chart.df.datetime.iloc[-1]:
            # A completed-bar disk snapshot may lag the already visible live
            # forming bar. Never rewind that view when reopening it.
            return
        period = self.period.currentData()
        code = self.target.tq_symbol.rsplit(".", 1)[-1].rstrip("0123456789")
        day_close = 15*3600+15*60 if "CFFEX" in self.target.tq_symbol and code in {"T","TF","TS","TL"} else 15*3600
        snapshot = RealtimeSnapshot({period: frame}, shanghai_time(), day_close_seconds=day_close)
        bars, partial = chart_frame(snapshot, period)
        self.chart.set_follow_latest(True)
        self._show_bars(bars, partial)
        self._data_text = f"本地最新K线：{bars.datetime.iloc[-1]} · {self._history_target.contract} · 末根{'预览' if partial else '已结束'}"
        self._refresh_status()

    def _backend_status(self, text):
        if not self.isVisible() or text.startswith("免费行情后台运行中"):
            return
        self._network_text = text
        self._refresh_status()

    def _view_status(self, payload):
        if (not self.isVisible() or self.target is None
                or payload.get("tq_symbol") != self.target.tq_symbol
                or payload.get("period") != self.period.currentData()):
            return
        self._network_text = str(payload.get("text", ""))
        self._refresh_status()

    def _preview(self, payload):
        if (not self.isVisible() or self.target is None or payload.get("tq_symbol") != self.target.tq_symbol
                or payload.get("period") != self.period.currentData()):
            return
        frame = pd.DataFrame(payload.get("bars", []))
        if frame.empty:
            self._network_text = "实时服务尚未返回该周期K线；已有画面继续保留"
            self._refresh_status()
            return
        frame["datetime"] = pd.to_datetime(frame["datetime"], errors="coerce")
        frame = frame.dropna(subset=["datetime"]).sort_values("datetime", kind="stable")
        if frame.empty:
            self._network_text = "实时回复暂无有效K线，已有画面继续保留"
            self._refresh_status()
            return
        if not self.chart.df.empty:
            if frame.datetime.iloc[-1] < self.chart.df.datetime.iloc[-1]:
                self._network_text = f"实时数据截至 {frame.datetime.iloc[-1]}，保留较新的已有K线"
                self._refresh_status()
                return
            # A short server window updates matching times without discarding
            # older local bars needed by the trend calculation.
            frame = (pd.concat([self.chart.df, frame], ignore_index=True).sort_values("datetime", kind="stable")
                     .drop_duplicates("datetime", keep="last").tail(2000).reset_index(drop=True))
        snapshot = RealtimeSnapshot({payload["period"]: frame}, shanghai_time(payload["received_at"]),
            shanghai_time(payload["quote_time"]) if payload.get("quote_time") else None,
            payload.get("last_price"), payload.get("underlying", ""), payload.get("day_close_seconds", 15*3600))
        bars, partial = chart_frame(snapshot, payload["period"])
        self._received_live = True
        self._local_note = self._network_text = ""
        self.status.setToolTip("")
        self._show_bars(bars, partial)
        self._data_text = (f"最新K线：{bars.datetime.iloc[-1]}　行情时间（北京时间）：{payload.get('quote_time') or '尚未收到'}　"
                           f"最新价：{payload.get('last_price')}　末根{'动态预览' if partial else '已结束'}")
        self._refresh_status()

    def _show_bars(self, bars, partial):
        closed = bars.iloc[:-1] if partial is not None else bars
        fingerprint = hashlib.sha256(pd.util.hash_pandas_object(closed, index=False).to_numpy().tobytes()).digest()
        signature = self.period.currentData(), len(closed), fingerprint
        if signature != self._closed_signature or len(self.chart.df) != len(bars):
            self.chart.set_series(bars, len(bars)-1, partial=partial)
            self._closed_signature = signature
        else:
            self.chart.set_series(self.chart.df, self.chart.current_idx, partial=partial)

    def hideEvent(self, event):
        self._request_token += 1
        self.history.cancel_pending()
        super().hideEvent(event)

    def _details(self):
        dialog = TrendIndicatorDialog(self.chart, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.chart.set_trend_settings(dialog.result_settings)
        dialog.deleteLater()

    def closeEvent(self, event):
        self.controller.request_view(enabled=False)
        self.closed.emit()
        event.accept()

    def reject(self):
        self.controller.request_view(enabled=False)
        self.closed.emit()
        super().reject()
