"""Causal, immutable replay independent of incoming market data."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .bars import aggregate_calendar, empty_bars, validate_frame


class Replay:
    def __init__(self):
        self.frame = empty_bars()
        self.cursor = -1
        self.period = "日线"

    def load(self, frame, period="日线", cursor=None):
        self.frame = validate_frame(frame)
        self.period = period
        self.cursor = max(-1, min(len(self.frame)-1, len(self.frame)-180 if cursor is None else int(cursor)))
        if len(self.frame) and self.cursor < 0:
            self.cursor = 0

    @property
    def current(self):
        return None if self.cursor < 0 else self.frame.iloc[self.cursor]

    @property
    def at_end(self):
        return self.cursor >= len(self.frame)-1

    def visible(self):
        prefix = self.frame.iloc[:self.cursor+1].copy(deep=True)
        return aggregate_calendar(prefix, self.period) if self.period in ("周线", "月线") else prefix

    def step(self, count=1):
        start = self.cursor
        self.cursor = max(0, min(len(self.frame)-1, self.cursor+int(count))) if len(self.frame) else -1
        return self.frame.iloc[start+1:self.cursor+1].copy(deep=True)

    def seek(self, timestamp):
        if self.frame.empty:
            return -1
        index = self.frame["datetime"].searchsorted(pd.Timestamp(timestamp), side="right")-1
        return max(0, min(len(self.frame)-1, int(index)))

    def day_index(self, direction):
        """Step to the adjacent observed trading date, including night sessions."""
        if self.frame.empty:
            return -1
        dates = pd.to_datetime(self.frame["trading_day"]).reset_index(drop=True)
        here = dates.iloc[self.cursor]
        valid = dates.gt(here) if direction > 0 else dates.lt(here)
        indices = np.flatnonzero(valid.to_numpy())
        return (int(indices[0]) if direction > 0 else int(indices[-1])) if len(indices) else self.cursor

    def companion(self):
        """Higher-period view derived exclusively from revealed source bars."""
        period = "周线" if self.period == "日线" else "月线" if self.period in ("周线", "月线") else "日线"
        return self.project(period)

    def project(self, period):
        """Additional chart from the revealed base bars, with no invented finer bars."""
        prefix = self.frame.iloc[:self.cursor+1].copy(deep=True)
        if prefix.empty:
            return prefix
        from .bars import native_period
        base_seconds = native_period(self.period)
        wanted = native_period(period)
        if period == self.period:
            return self.visible()
        if wanted < base_seconds or wanted % base_seconds:
            raise ValueError("副图周期不能小于复盘基础周期，请在主图选更小周期")
        if wanted == base_seconds and period not in ("周线", "月线"):
            return prefix
        if wanted < 86400:
            groups = prefix.datetime.dt.floor(f"{wanted}s")
        else:
            groups = prefix["trading_day"]
        result = validate_frame(prefix.groupby(groups).agg(
            datetime=("datetime", "first"), open=("open", "first"), high=("high", "max"),
            low=("low", "min"), close=("close", "last"), volume=("volume", "sum"),
            open_interest=("open_interest", "last"), closed=("closed", "all"),
            trading_day=("trading_day", "last")).reset_index(drop=True))
        if wanted == 86400:
            result["datetime"] = pd.to_datetime(result["trading_day"])
        if len(result):
            current = prefix.iloc[-1]
            end = current["datetime"]+pd.Timedelta(seconds=base_seconds)
            close = (pd.Timestamp(current["trading_day"])+pd.Timedelta(hours=15) if wanted == 86400 else
                     current["datetime"].floor(f"{wanted}s")+pd.Timedelta(seconds=wanted))
            result.loc[:len(result)-2, "closed"] = True
            result.loc[len(result)-1, "closed"] = bool(current["closed"]) and end >= close
        return aggregate_calendar(result, period) if period in ("周线", "月线") else result
