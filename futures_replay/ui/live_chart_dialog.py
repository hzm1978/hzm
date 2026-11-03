from __future__ import annotations

import pandas as pd
import hashlib
from PySide6.QtCore import Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QHBoxLayout,
                              QLabel, QPushButton, QVBoxLayout)

from ..realtime_data import RealtimeSnapshot, chart_frame, shanghai_time
from .chart_widget import CandleChart
from .trend_dialog import TrendIndicatorDialog


class LiveChartDialog(QDialog):
    closed = Signal()

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller, self.target = controller, None
        self._closed_signature = None
        self.setWindowTitle("实时跟随 · 免费期货行情")
        self.resize(1250, 800)
        layout = QVBoxLayout(self)
        self.title = QLabel("实时行情独立显示，原复盘位置和模拟账户保持不变。")
        layout.addWidget(self.title)
        row = QHBoxLayout(); row.addWidget(QLabel("周期"))
        self.period = QComboBox()
        for label, code in [("1分钟", "1min"), ("5分钟", "5min"), ("15分钟", "15min"),
                            ("30分钟", "30min"), ("1小时", "1h"), ("4小时", "4h"), ("日线", "1d")]:
            self.period.addItem(label, code)
        row.addWidget(self.period)
        self.trend = QCheckBox("趋势交易"); self.trend.setChecked(True); row.addWidget(self.trend)
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
        self.controller.previewReady.connect(self._preview)
        self.controller.statusChanged.connect(self._backend_status)
        self.controller.viewStatusChanged.connect(self._view_status)

    def configure(self, target, period="1min", settings=None):
        self.target = target
        self._closed_signature = None
        if period in {"1w", "1M"}:
            period = "1d"
        self.title.setText(f"{target.source} · {target.symbol} · {target.contract}　实时跟随（原复盘保持原位）")
        self.chart.set_trend_settings(settings or {})
        self.period.blockSignals(True)
        self.period.setCurrentIndex(max(0, self.period.findData(period)))
        self.period.blockSignals(False)
        self.chart.set_series(pd.DataFrame(columns=["datetime", "open", "high", "low", "close"]), -1)
        self.status.setText(self.controller.last_status)
        self.show(); self._request()

    def _request(self, *_):
        if self.target and self.isVisible():
            # Do not leave a different period's bars under the new selector.
            self._closed_signature = None
            self.chart.set_series(pd.DataFrame(columns=["datetime", "open", "high", "low", "close"]), -1)
            self.status.setText(f"正在获取 {self.target.symbol} 的{self.period.currentText()}实时K线…")
            self.controller.request_view(self.target.tq_symbol, self.period.currentData())

    def _backend_status(self, text):
        if not self.isVisible() or text.startswith("免费行情后台运行中"):
            return
        if self.chart.df.empty or text.startswith(("后台实时更新已停止", "后台行情连接未完成")):
            self.status.setText(text)

    def _view_status(self, payload):
        if (not self.isVisible() or self.target is None
                or payload.get("tq_symbol") != self.target.tq_symbol
                or payload.get("period") != self.period.currentData()):
            return
        self.status.setText(str(payload.get("text", "")))

    def _preview(self, payload):
        if (not self.isVisible() or self.target is None or payload.get("tq_symbol") != self.target.tq_symbol
                or payload.get("period") != self.period.currentData()):
            return
        frame = pd.DataFrame(payload.get("bars", []))
        if frame.empty:
            self.status.setText("当前合约尚未返回K线；到期合约可改选主连查看最新行情。")
            return
        frame["datetime"] = pd.to_datetime(frame["datetime"], errors="coerce")
        snapshot = RealtimeSnapshot({payload["period"]: frame}, shanghai_time(payload["received_at"]),
            shanghai_time(payload["quote_time"]) if payload.get("quote_time") else None,
            payload.get("last_price"), payload.get("underlying", ""), payload.get("day_close_seconds", 15*3600))
        bars, partial = chart_frame(snapshot, payload["period"])
        closed = bars.iloc[:-1] if partial is not None else bars
        fingerprint = hashlib.sha256(pd.util.hash_pandas_object(closed, index=False).to_numpy().tobytes()).digest()
        signature = payload["period"], len(closed), fingerprint
        if signature != self._closed_signature or len(self.chart.df) != len(bars):
            self.chart.set_series(bars, len(bars)-1, partial=partial)
            self._closed_signature = signature
        else:
            self.chart.set_series(self.chart.df, self.chart.current_idx, partial=partial)
        self.status.setText(f"行情时间（北京时间）：{payload.get('quote_time') or '尚未收到'}　"
                            f"最新价：{payload.get('last_price')}　末根{'动态预览' if partial else '已结束'}")

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
