"""Read local chart history without using the replay clock or the SDK."""
from __future__ import annotations

import pandas as pd
from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot

from ..data_repo import DataRepository
from ..edb_update import synthesize_intraday_periods
from ..realtime_data import shanghai_time


def load_local_history(target, period, limit=2000):
    # A separate repository owns its cache. Daily preview deliberately reads
    # native daily + live shards, not years of minute data in load_bars().
    repo = DataRepository(target.root)
    try:
        frame = (repo.load_minutes(target.source, target.symbol, target.contract)
                 if period == "1min" else
                 repo._read_native_frame(target.source, target.symbol, period, target.contract))
    except FileNotFoundError:
        minutes = repo.load_minutes(target.source, target.symbol, target.contract)
        if period == "1d":
            frame = repo._aggregate_daily_minutes(minutes)
        else:
            frame = synthesize_intraday_periods(minutes)[period]
    # Local libraries can contain future replay examples. Do not present them
    # as the latest real market bar at the current wall clock.
    now = shanghai_time()
    frame = frame.loc[pd.to_datetime(frame.datetime).le(now)]
    return frame.sort_values("datetime").drop_duplicates("datetime", keep="last").tail(limit).copy().reset_index(drop=True)


class _HistorySignals(QObject):
    finished = Signal(object)


class _HistoryTask(QRunnable):
    def __init__(self, target, period, token):
        super().__init__()
        self.target, self.period, self.token = target, period, token
        self.signals = _HistorySignals()

    def run(self):
        try:
            result = dict(token=self.token, frame=load_local_history(self.target, self.period), error="")
        except Exception as exc:
            result = dict(token=self.token, frame=pd.DataFrame(), error=f"{type(exc).__name__}: {exc}"[:500])
        self.signals.finished.emit(result)


class LocalHistoryLoader(QObject):
    ready = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._active = self._pending = None

    def request(self, target, period, token):
        # Conflate rapid period changes rather than filling a thread pool with
        # obsolete large parquet reads. One private reader runs at a time.
        self._pending = target, period, token
        self._start_next()

    def cancel_pending(self):
        self._pending = None

    def _start_next(self):
        if self._active is not None or self._pending is None:
            return
        task = _HistoryTask(*self._pending)
        self._pending = None
        self._active = task
        task.signals.finished.connect(self._finished)
        QThreadPool.globalInstance().start(task)

    @Slot(object)
    def _finished(self, result):
        self._active = None
        self.ready.emit(result)
        self._start_next()
