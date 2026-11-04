from __future__ import annotations

"""Owned snapshots of TqSdk data; no SDK objects cross the worker boundary."""

from dataclasses import dataclass
import ast
import json
from pathlib import Path

import numpy as np
import pandas as pd

from .edb_update import STANDARD_COLUMNS
from .trading_calendar import session_trading_days


LIVE_PERIODS = {"1min": 60, "5min": 300, "15min": 900, "30min": 1800,
                "1h": 3600, "4h": 14400, "1d": 86400}
LIVE_HISTORY_BARS = 10000


def create_tq_api(username, password, loop, *, progress_cb=None):
    from tqsdk import TqApi, TqAuth, TqSim
    def report(stage, text):
        if progress_cb is not None:
            progress_cb(stage, text)
    class ProgressAuth(TqAuth):
        def login(self):
            report("authenticating", "正在认证天勤账号…")
            result = super().login()
            report("authenticated", "账号认证通过，正在连接行情服务…")
            return result
    report("connecting", "正在初始化天勤行情组件…")
    return TqApi(account=TqSim(), auth=ProgressAuth(username, password), loop=loop,
                 debug=False, web_gui=False, disable_print=True)


@dataclass(frozen=True)
class RealtimeTarget:
    root: Path
    source: str
    symbol: str
    contract: str
    tq_symbol: str


@dataclass(frozen=True)
class RealtimeSnapshot:
    frames: dict[str, pd.DataFrame]
    received_at: pd.Timestamp
    quote_time: pd.Timestamp | None = None
    last_price: float | None = None
    underlying: str = ""
    day_close_seconds: int = 15 * 3600


def shanghai_time(value=None) -> pd.Timestamp:
    value = pd.Timestamp.now(tz="Asia/Shanghai") if value is None else pd.Timestamp(value)
    if value.tzinfo is not None:
        value = value.tz_convert("Asia/Shanghai").tz_localize(None)
    return value


def normalize_serial(serial: pd.DataFrame, period: str, calendar=()) -> pd.DataFrame:
    """Copy before coercion: TqSdk mutates its DataFrame as ticks arrive."""
    if serial is None or serial.empty:
        return pd.DataFrame(columns=STANDARD_COLUMNS)
    x = serial.copy(deep=True)
    ns = pd.to_numeric(x["datetime"], errors="coerce")
    valid = ns.notna() & ns.gt(0)
    if "id" in x:
        valid &= pd.to_numeric(x["id"], errors="coerce").ge(0)
    x = x.loc[valid].copy()
    if x.empty:
        return pd.DataFrame(columns=STANDARD_COLUMNS)
    x["datetime"] = pd.to_datetime(ns.loc[valid], unit="ns", utc=True, errors="coerce").dt.tz_convert("Asia/Shanghai").dt.tz_localize(None)
    for key in ("open", "high", "low", "close"):
        x[key] = pd.to_numeric(x[key], errors="coerce")
    values = x[["open", "high", "low", "close"]].to_numpy(dtype=float)
    usable = np.isfinite(values).all(axis=1)
    usable &= (values[:, 1] >= np.maximum(values[:, 0], values[:, 3]))
    usable &= (values[:, 2] <= np.minimum(values[:, 0], values[:, 3]))
    x = x.loc[usable & x["datetime"].notna().to_numpy()].copy()
    for key in ("volume", "amount"):
        x[key] = pd.to_numeric(x[key], errors="coerce").fillna(0) if key in x else 0.
    oi = x["close_oi"] if "close_oi" in x else x.get("open_interest", pd.Series(0., index=x.index))
    x["open_interest"] = pd.to_numeric(oi, errors="coerce").fillna(0.)
    if period == "1d":
        x["datetime"] = x["datetime"].dt.normalize()
        x = x.loc[x["datetime"].dt.dayofweek < 5].copy()
        x["trading_day"] = x["datetime"].dt.strftime("%Y-%m-%d")
    else:
        x["trading_day"] = session_trading_days(x["datetime"], calendar)
    return x[STANDARD_COLUMNS].sort_values("datetime").drop_duplicates("datetime", keep="last").reset_index(drop=True)


def day_close_seconds(trading_time) -> int:
    """The final daytime session also covers bond futures' 15:15 close."""
    try:
        if isinstance(trading_time, str):
            try:
                trading_time = json.loads(trading_time)
            except json.JSONDecodeError:
                trading_time = ast.literal_eval(trading_time)
        ends = [str(row[1]).split(":") for row in trading_time["day"]]
        return max(int(h)*3600+int(m)*60+int(s) for h, m, s in ends)
    except (KeyError, TypeError, ValueError, SyntaxError):
        return 15*3600


def build_snapshot(serials, quote, received_at=None) -> RealtimeSnapshot:
    received_at = shanghai_time(received_at)
    daily = normalize_serial(serials["1d"], "1d")
    calendar = daily["trading_day"]
    frames = {key: (daily if key == "1d" else normalize_serial(value, key, calendar))
              for key, value in serials.items()}
    for key in frames:
        if key != "1d":
            frames[key] = frames[key].loc[frames[key].datetime.le(received_at)].reset_index(drop=True)
    quote_time = pd.to_datetime(quote.get("datetime", ""), errors="coerce")
    quote_time = shanghai_time(quote_time) if pd.notna(quote_time) else None
    try:
        price = float(quote.get("last_price", float("nan")))
    except (TypeError, ValueError):
        price = float("nan")
    return RealtimeSnapshot(frames, received_at, quote_time,
        price if np.isfinite(price) else None, str(quote.get("underlying_symbol", "") or ""),
        day_close_seconds(quote.get("trading_time", {})))


def completed_frame(snapshot: RealtimeSnapshot, period: str) -> pd.DataFrame:
    frame = snapshot.frames[period]
    if frame.empty:
        return frame.copy()
    if period == "1d":
        ends = pd.to_datetime(frame["trading_day"]) + pd.Timedelta(seconds=snapshot.day_close_seconds)
    else:
        ends = pd.to_datetime(frame["datetime"]) + pd.Timedelta(seconds=LIVE_PERIODS[period])
    closed = ends.le(snapshot.received_at)
    # The presence of a subsequent native bar proves closure even where a
    # session ends before a large nominal duration (e.g. a 4-hour night bar).
    if len(frame) > 1 and period != "1d":
        next_observed = frame["datetime"].shift(-1).le(snapshot.received_at)
        closed |= next_observed
    return frame.loc[closed].copy().reset_index(drop=True)


def chart_frame(snapshot: RealtimeSnapshot, period: str):
    frame = snapshot.frames[period].tail(2000).reset_index(drop=True)
    if frame.empty:
        return frame, None
    closed = completed_frame(snapshot, period)
    partial = dict(frame.iloc[-1]) if closed.empty or closed.iloc[-1]["datetime"] < frame.iloc[-1]["datetime"] else None
    return frame, partial
