"""Reusable chart renderer with trend computation outside Qt paint callbacks."""
import hashlib

import numpy as np
from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, QTimer

from .technical.trend_analysis import analyze_trend
from .technical.trend_settings import TrendSettings
from .technical.ui.chart_widget import CandleChart


class Result(QObject):
    ready = Signal(object)


class AnalysisJob(QRunnable):
    def __init__(self, key, source, settings):
        super().__init__()
        self.key, self.source, self.settings = key, source, settings
        self.signals = Result()

    def run(self):
        try:
            result = analyze_trend(self.source, lookback=self.settings.lookback, settings=self.settings)
            self.signals.ready.emit((self.key, result, ""))
        except Exception as exc:
            self.signals.ready.emit((self.key, None, f"{type(exc).__name__}: {exc}"))


class Chart(CandleChart):
    analysisReady = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.set_transition_ms(0)
        self.empty_text = "连接天勤、导入历史数据，或点击「演示行情」开始"
        self._requested = self._working = None
        self._job = None
        self._trend_error = ""
        self._analysis_timer = QTimer(self)
        self._analysis_timer.setSingleShot(True); self._analysis_timer.setInterval(100)
        self._analysis_timer.timeout.connect(self._start_analysis)
        self._queued_source = None

    def _trend_analysis_cached(self, visible_end):
        end = max(0, min(int(visible_end), self.current_idx+1, len(self.df)))
        if end == self.current_idx+1 and self.partial is not None:
            end = max(0, end-1)
        settings = TrendSettings.from_dict(self.trend_settings)
        start = max(0, end-settings.lookback)
        source = self.df.iloc[start:end].copy(deep=True)
        if source.empty:
            self._requested = None
            self._trend_cache = None
            return None
        source["_trend_position"] = np.arange(start, end)
        digest = hashlib.blake2b(source[["open", "high", "low", "close"]].to_numpy(dtype=float).tobytes(), digest_size=12).digest()
        key = (digest, start, end, settings)
        if key == self._trend_cache_key:
            return self._trend_cache
        if self._requested != key:
            self._requested = key
            self._queued_source = (key, source, settings)
            self._trend_cache = None
            self._trend_error = ""
            self._analysis_timer.start()
        return None

    def _start_analysis(self):
        if self._working is not None or self._queued_source is None:
            return
        key, source, settings = self._queued_source
        self._queued_source = None
        self._working = key
        job = AnalysisJob(key, source, settings)
        self._job = job
        job.signals.ready.connect(self._analysis_done)
        QThreadPool.globalInstance().start(job)

    def _analysis_done(self, payload):
        key, result, error = payload
        self._working = None
        self._job = None
        if key == self._requested:
            self._trend_cache_key = key
            self._trend_cache = result
            self._trend_error = error
            self.analysisReady.emit(result)
            self.update()
        if self._queued_source is not None:
            self._analysis_timer.start()

    def show_frame(self, frame):
        partial = dict(frame.iloc[-1]) if len(frame) and not bool(frame.iloc[-1]["closed"]) else None
        self.set_series(frame, len(frame)-1, partial=partial)
        if self.show_trend and len(frame):
            self._trend_analysis_cached(len(frame))

    def clear_drawings(self):
        self.drawings.clear()
        self.drawingChanged.emit()
        self.update()
