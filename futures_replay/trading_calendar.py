from __future__ import annotations

import numpy as np
import pandas as pd


def parse_trading_days(values) -> pd.Series:
    """Parse date labels without interpreting integer YYYYMMDD as epoch ns."""
    raw = pd.Series(values)
    text = raw.astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
    out = pd.Series(pd.NaT, index=raw.index, dtype="datetime64[ns]")
    compact = text.str.fullmatch(r"\d{8}")
    if compact.any():
        out.loc[compact] = pd.to_datetime(text.loc[compact], format="%Y%m%d", errors="coerce")
    remain = out.isna() & raw.notna()
    if remain.any():
        out.loc[remain] = pd.to_datetime(raw.loc[remain], errors="coerce")
    return out.dt.normalize()


def exchange_calendar(values) -> pd.DatetimeIndex:
    days = parse_trading_days(values).dropna()
    # Chinese futures exchanges do not open on weekend make-up workdays.
    return pd.DatetimeIndex(sorted(days.loc[days.dt.day_of_week < 5].unique()))


def session_trading_days(timestamps: pd.Series, calendar_values=()) -> pd.Series:
    """Map exchange-local session timestamps to trading-day labels.

    Prefer the supplied native daily calendar (including known holidays). At
    its unpublished tail, or without daily data, fall back to weekdays. That
    fallback cannot infer an exchange holiday absent from the supplied calendar.
    Friday evening and Saturday morning both belong to Monday, not Saturday.
    """
    dt = pd.to_datetime(timestamps, errors="coerce")
    result = pd.Series(pd.NA, index=dt.index, dtype=object)
    valid = dt.notna().to_numpy()
    if not valid.any():
        return result

    times = dt.loc[valid]
    hours = times.dt.hour.to_numpy()
    # pandas Copy-on-Write (and pandas 3) can expose a read-only NumPy view.
    # Session anchors are shifted below; own this buffer without changing input.
    anchors = times.dt.normalize().to_numpy(dtype="datetime64[ns]", copy=True)
    early = hours < 6
    anchors[early] -= np.timedelta64(1, "D")
    night = (hours >= 18) | early

    # Night sessions require a trading day strictly after their start date;
    # daytime sessions use the first trading day on/after their natural date.
    lower = anchors.astype("datetime64[D]") + night.astype("timedelta64[D]")
    labels = np.busday_offset(lower, 0, roll="forward").astype("datetime64[ns]")
    calendar = exchange_calendar(calendar_values)
    if len(calendar):
        cal = calendar.to_numpy(dtype="datetime64[ns]")
        positions = np.searchsorted(cal, anchors, side="left")
        positions[night] = np.searchsorted(cal, anchors[night], side="right")
        found = positions < len(cal)
        labels[found] = cal[positions[found]]

    result.loc[valid] = pd.DatetimeIndex(labels).strftime("%Y-%m-%d").to_numpy()
    return result
