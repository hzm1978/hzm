"""Owned, validated OHLC data. All application timestamps are Beijing time."""
from __future__ import annotations

from datetime import timedelta
import json
import math

import numpy as np
import pandas as pd

PERIODS = {"1分钟": 60, "5分钟": 300, "15分钟": 900, "30分钟": 1800,
           "1小时": 3600, "4小时": 14400, "日线": 86400, "周线": "week", "月线": "month"}
COLUMNS = ["datetime", "open", "high", "low", "close", "volume", "open_interest", "closed", "trading_day"]


def now_beijing():
    return pd.Timestamp.now(tz="Asia/Shanghai").tz_localize(None)


def empty_bars():
    return pd.DataFrame(columns=COLUMNS)


def trading_days(dates, calendar=()):
    values = pd.to_datetime(dates)
    anchors = values.dt.normalize().copy()
    anchors = anchors + pd.to_timedelta(values.dt.hour.ge(18).astype(int), unit="D")
    known = pd.DatetimeIndex(pd.to_datetime(list(calendar), errors="coerce")).dropna().normalize().unique().sort_values()
    weekday = anchors.dt.dayofweek.to_numpy(copy=True)
    shifted = anchors+pd.to_timedelta(np.where(weekday >= 5, 7-weekday, 0), unit="D")
    result = shifted.to_numpy(dtype="datetime64[ns]", copy=True)
    if len(known):
        indices = known.searchsorted(anchors)
        valid = indices < len(known)
        result[valid] = known.to_numpy(dtype="datetime64[ns]", copy=True)[indices[valid]]
    return pd.Series(pd.to_datetime(result).strftime("%Y-%m-%d"), index=values.index, dtype=str)


def validate_frame(frame, *, default_closed=True):
    """Reject invalid OHLC, detach SDK buffers, sort and deduplicate timestamps."""
    if frame is None or frame.empty:
        return empty_bars()
    x = frame.copy(deep=True)
    required = {"datetime", "open", "high", "low", "close"}
    if not required.issubset(x):
        raise ValueError("缺少数据列：" + "、".join(sorted(required - set(x))))
    dates = pd.to_datetime(x["datetime"], errors="coerce")
    if isinstance(dates.dtype, pd.DatetimeTZDtype):
        dates = dates.dt.tz_convert("Asia/Shanghai").dt.tz_localize(None)
    x["datetime"] = dates
    for key in ("open", "high", "low", "close", "volume", "open_interest"):
        value = x.get(key, pd.Series(0., index=x.index))
        x[key] = pd.to_numeric(value, errors="coerce")
    ohlc = x[["open", "high", "low", "close"]].to_numpy(dtype=float, copy=True)
    valid = np.isfinite(ohlc).all(axis=1)
    valid &= (ohlc[:, 1] >= np.maximum(ohlc[:, 0], ohlc[:, 3]))
    valid &= (ohlc[:, 2] <= np.minimum(ohlc[:, 0], ohlc[:, 3]))
    valid &= (ohlc[:, 1] >= ohlc[:, 2])
    valid &= x["datetime"].notna().to_numpy()
    x = x.loc[valid].copy()
    x["volume"] = x["volume"].fillna(0).clip(lower=0)
    x["open_interest"] = x["open_interest"].fillna(0).clip(lower=0)
    if "closed" not in x:
        x["closed"] = default_closed
    else:
        x["closed"] = x["closed"].map(
            lambda value: value is True or value == 1 or str(value).lower() in {"true", "1"})
    if "trading_day" not in x:
        x["trading_day"] = trading_days(x["datetime"])
    else:
        parsed_days = pd.to_datetime(x["trading_day"], errors="coerce")
        x["trading_day"] = parsed_days.dt.strftime("%Y-%m-%d")
        missing = parsed_days.isna()
        if missing.any():
            x.loc[missing, "trading_day"] = trading_days(x.loc[missing, "datetime"])
    return x[COLUMNS].sort_values("datetime").drop_duplicates("datetime", keep="last").reset_index(drop=True)


def session_close_seconds(trading_time):
    try:
        if isinstance(trading_time, str):
            trading_time = json.loads(trading_time)
        return max(sum(int(n) * k for n, k in zip(end.split(":"), (3600, 60, 1)))
                   for _, end in trading_time["day"])
    except (ValueError, TypeError, KeyError):
        return 15 * 3600


def from_sdk(serial, seconds, quote=None, received_at=None, calendar=()):
    if serial is None or serial.empty:
        return empty_bars()
    x = serial.copy(deep=True)
    ns = pd.to_numeric(x["datetime"], errors="coerce")
    valid = ns.gt(0)
    if "id" in x:
        valid &= pd.to_numeric(x["id"], errors="coerce").ge(0)
    x = x.loc[valid].copy()
    x["datetime"] = pd.to_datetime(ns.loc[valid], unit="ns", utc=True).dt.tz_convert("Asia/Shanghai").dt.tz_localize(None)
    x["open_interest"] = x.get("close_oi", 0)
    now = now_beijing() if received_at is None else pd.Timestamp(received_at)
    if now.tzinfo is not None:
        now = now.tz_convert("Asia/Shanghai").tz_localize(None)
    if seconds == 86400:
        x["datetime"] = x["datetime"].dt.normalize()
        x["trading_day"] = x["datetime"].dt.strftime("%Y-%m-%d")
        end = x["datetime"] + pd.Timedelta(seconds=session_close_seconds((quote or {}).get("trading_time", {})))
    else:
        x["trading_day"] = trading_days(x["datetime"], calendar)
        end = x["datetime"] + pd.Timedelta(seconds=seconds)
    x["closed"] = end.le(now)
    if len(x) > 1:
        # A subsequent exchange bar proves the preceding native bar is complete.
        x["closed"] |= x["datetime"].shift(-1).le(now)
    # During the night session a DAILY label is the next trading day's midnight.
    # That label may be later than the wall clock although its OHLC is already real.
    return validate_frame(x if seconds == 86400 else x.loc[x["datetime"].le(now)])


def merge_bars(older, newer):
    if older is None or older.empty:
        return validate_frame(newer)
    if newer is None or newer.empty:
        return older.copy(deep=True)
    return validate_frame(pd.concat([older, newer], ignore_index=True))


def records(frame):
    values = frame.copy()
    values["datetime"] = values["datetime"].dt.strftime("%Y-%m-%d %H:%M:%S")
    return values.to_dict("records")


def native_period(period):
    value = PERIODS[period]
    return value if isinstance(value, int) else 86400


def aggregate_calendar(frame, period):
    """Aggregate only the caller's visible daily prefix; never inspect future rows."""
    if frame.empty or period not in ("周线", "月线"):
        return frame.copy(deep=True)
    x = frame.copy()
    grouper = x["datetime"].dt.to_period("W-SUN" if period == "周线" else "M")
    out = x.groupby(grouper, sort=True).agg(
        datetime=("datetime", "first"), open=("open", "first"), high=("high", "max"),
        low=("low", "min"), close=("close", "last"), volume=("volume", "sum"),
        open_interest=("open_interest", "last"), closed=("closed", "all"),
        trading_day=("trading_day", "last")).reset_index(drop=True)
    # At the right edge a week/month may still be forming, even if its daily bars closed.
    last_day = pd.Timestamp(x.iloc[-1]["datetime"])
    calendar_finished = last_day.weekday() >= 4 if period == "周线" else last_day.is_month_end
    if len(out):
        out.loc[len(out)-1, "closed"] = bool(out.iloc[-1]["closed"]) and calendar_finished
    return validate_frame(out)


def finite(value, default=None):
    try:
        value = float(value)
        return value if math.isfinite(value) else default
    except (ValueError, TypeError):
        return default
