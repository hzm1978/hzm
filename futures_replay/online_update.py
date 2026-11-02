from __future__ import annotations

"""Best-effort online futures data updater.

The updater deliberately uses only the Python standard library for HTTP, so it
adds no new project dependency.  Data is fetched from Sina's public futures
JSONP endpoints, the same endpoints documented/used by AKShare.

Important limitation: minute endpoints return only a recent window.  Therefore
this module can append the latest minute bars, but it cannot magically fill a
large historical minute-data gap if the upstream endpoint no longer exposes
those missing minutes.  Daily data normally has much deeper history.
"""

import json
import re
import time
import urllib.parse
import urllib.request
from datetime import date, timedelta

import numpy as np
import pandas as pd

from .trading_calendar import session_trading_days


class OnlineUpdateError(RuntimeError):
    pass


_MINUTE_URL = (
    "https://stock2.finance.sina.com.cn/futures/api/jsonp.php/=/"
    "InnerFuturesNewService.getFewMinLine"
)
_DAILY_URL = (
    "https://stock2.finance.sina.com.cn/futures/api/jsonp.php/"
    "var%20_V21052021_4_12=/InnerFuturesNewService.getDailyKLine"
)
_PERIOD_MAP = {
    "1min": "1",
    "5min": "5",
    "15min": "15",
    "30min": "30",
    "1h": "60",
}
_STANDARD_COLUMNS = [
    "datetime", "open", "high", "low", "close",
    "volume", "amount", "open_interest", "trading_day",
]


def _request_text(url: str, params: dict[str, str], timeout: float = 15.0) -> str:
    full_url = url + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(
        full_url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/132 Safari/537.36"
            ),
            "Referer": "https://finance.sina.com.cn/futures/",
            "Accept": "*/*",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            charset = resp.headers.get_content_charset() or "utf-8"
        return raw.decode(charset, errors="replace")
    except Exception as exc:  # network errors vary by Python/Windows version
        raise OnlineUpdateError(f"连接新浪期货数据接口失败：{exc}") from exc


def _parse_jsonp(text: str):
    """Extract the JSON value from Sina's JSONP wrapper."""
    start = text.find("=(")
    if start >= 0:
        end = text.rfind(");")
        payload = text[start + 2 : end if end > start else None]
    else:
        match = re.search(r"\((\s*[\[{].*[\]}]\s*)\)\s*;?\s*$", text, re.S)
        if not match:
            raise OnlineUpdateError("在线接口返回格式无法识别。")
        payload = match.group(1)
    try:
        return json.loads(payload)
    except json.JSONDecodeError as exc:
        raise OnlineUpdateError("在线接口返回的 JSON 数据无法解析。") from exc


def _to_number(df: pd.DataFrame, columns: list[str]) -> None:
    for col in columns:
        df[col] = pd.to_numeric(df[col], errors="coerce")


def fetch_minute_sina(symbol: str, period: str) -> pd.DataFrame:
    """Fetch recent minute bars for an exact contract or continuous symbol."""
    if period not in _PERIOD_MAP:
        raise ValueError(f"不支持的在线分钟周期：{period}")
    text = _request_text(_MINUTE_URL, {"symbol": symbol.upper(), "type": _PERIOD_MAP[period]})
    data = _parse_jsonp(text)
    if not data:
        return pd.DataFrame(columns=["datetime", "open", "high", "low", "close", "volume", "hold"])
    df = pd.DataFrame(data)
    if df.shape[1] < 7:
        raise OnlineUpdateError(f"{symbol} {period} 在线数据字段数量异常。")
    df = df.iloc[:, :7].copy()
    df.columns = ["datetime", "open", "high", "low", "close", "volume", "hold"]
    df["datetime"] = pd.to_datetime(df["datetime"], errors="coerce")
    _to_number(df, ["open", "high", "low", "close", "volume", "hold"])
    return df.dropna(subset=["datetime", "open", "high", "low", "close"]).reset_index(drop=True)


def fetch_daily_sina(symbol: str) -> pd.DataFrame:
    """Fetch Sina daily history for an exact contract or continuous symbol."""
    # Sina's endpoint expects a type argument even though the endpoint returns
    # the symbol's daily history.  This fixed value mirrors the long-standing
    # public interface used by AKShare.
    text = _request_text(_DAILY_URL, {"symbol": symbol.upper(), "type": "2021_04_12"})
    data = _parse_jsonp(text)
    if not data:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume", "hold", "settle"])
    df = pd.DataFrame(data)
    if df.shape[1] < 8:
        raise OnlineUpdateError(f"{symbol} 日线在线数据字段数量异常。")
    df = df.iloc[:, :8].copy()
    df.columns = ["date", "open", "high", "low", "close", "volume", "hold", "settle"]
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    _to_number(df, ["open", "high", "low", "close", "volume", "hold", "settle"])
    return df.dropna(subset=["date", "open", "high", "low", "close"]).reset_index(drop=True)


def _next_weekday(d: date) -> date:
    d = d + timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def _assign_trading_day(minutes: pd.DataFrame, daily: pd.DataFrame) -> pd.Series:
    """Assign Chinese futures trading_day to recent minute bars.

    Prefer actual dates from the fetched daily series so weekends/holidays are
    handled by the market calendar represented in that series.  For a currently
    forming night session whose next daily bar is not published yet, fall back
    to the next weekday.
    """
    if minutes.empty:
        return pd.Series(dtype="object")
    return session_trading_days(minutes["datetime"], daily.get("date", ()))


def _normalize_minute(minutes: pd.DataFrame, daily: pd.DataFrame) -> pd.DataFrame:
    if minutes.empty:
        return pd.DataFrame(columns=_STANDARD_COLUMNS)
    out = pd.DataFrame({
        "datetime": pd.to_datetime(minutes["datetime"], errors="coerce"),
        "open": minutes["open"],
        "high": minutes["high"],
        "low": minutes["low"],
        "close": minutes["close"],
        "volume": minutes["volume"],
        "amount": 0.0,
        "open_interest": minutes["hold"],
    })
    out["trading_day"] = _assign_trading_day(out, daily)
    return out[_STANDARD_COLUMNS].dropna(subset=["datetime", "open", "high", "low", "close"])


def _normalize_daily(daily: pd.DataFrame) -> pd.DataFrame:
    if daily.empty:
        return pd.DataFrame(columns=_STANDARD_COLUMNS)
    dt = pd.to_datetime(daily["date"], errors="coerce")
    out = pd.DataFrame({
        "datetime": dt,
        "open": daily["open"],
        "high": daily["high"],
        "low": daily["low"],
        "close": daily["close"],
        "volume": daily["volume"],
        "amount": 0.0,
        "open_interest": daily["hold"],
        "trading_day": dt.dt.strftime("%Y-%m-%d"),
    })
    return out[_STANDARD_COLUMNS].dropna(subset=["datetime", "open", "high", "low", "close"])



def synthesize_4h_from_smaller(
    one_hour: pd.DataFrame | None,
    one_minute: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Build 4-hour bars when the public endpoint has no native 4h series.

    Prefer the endpoint's native 1-hour bars and combine every four consecutive
    1h bars *within the same trading_day*.  This is safer for Chinese futures
    than a plain wall-clock ``resample('4h')`` because the market has night
    sessions and long intra-day breaks.  If 1h data is unavailable, fall back
    to blocks of 240 consecutive 1-minute bars inside each trading day.

    The last block of the latest trading day may contain fewer than four 1h
    bars (or fewer than 240 minutes); keeping it lets the local 4h file refresh
    the currently forming bar on repeated updates.
    """
    src = one_hour if one_hour is not None and not one_hour.empty else one_minute
    if src is None or src.empty:
        return pd.DataFrame(columns=_STANDARD_COLUMNS)

    block_size = 4 if one_hour is not None and not one_hour.empty else 240
    x = src.copy()
    x["datetime"] = pd.to_datetime(x["datetime"], errors="coerce")
    x = x.dropna(subset=["datetime", "open", "high", "low", "close"]).copy()
    if x.empty:
        return pd.DataFrame(columns=_STANDARD_COLUMNS)
    if "trading_day" not in x.columns:
        x["trading_day"] = x["datetime"].dt.strftime("%Y-%m-%d")
    x["trading_day"] = x["trading_day"].astype(str)
    x = x.sort_values(["trading_day", "datetime"]).reset_index(drop=True)
    x["_block"] = x.groupby("trading_day", sort=False).cumcount() // block_size

    agg = {
        "datetime": "first",
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
        "amount": "sum",
        "open_interest": "last",
    }
    out = (
        x.groupby(["trading_day", "_block"], sort=False, as_index=False)
        .agg(agg)
        .drop(columns=["_block"], errors="ignore")
    )
    # groupby(as_index=False) keeps trading_day as a normal column.  Reorder to
    # the project's canonical parquet schema.
    for col in ["volume", "amount", "open_interest"]:
        if col not in out.columns:
            out[col] = 0.0
    out = out[_STANDARD_COLUMNS]
    return out.sort_values("datetime").reset_index(drop=True)


def fetch_sina_bundle(symbol: str) -> dict[str, pd.DataFrame]:
    """Fetch all periods that Sina exposes and return standardized frames."""
    symbol = symbol.strip().upper()
    if not symbol:
        raise OnlineUpdateError("在线更新缺少合约代码。")

    daily_raw = fetch_daily_sina(symbol)
    result: dict[str, pd.DataFrame] = {"1d": _normalize_daily(daily_raw)}
    for period in ["1min", "5min", "15min", "30min", "1h"]:
        # Avoid hammering the free public endpoint when the user clicks update.
        time.sleep(0.18)
        mins = fetch_minute_sina(symbol, period)
        result[period] = _normalize_minute(mins, daily_raw)

    # Sina's public minute endpoint has no native 4-hour option.  The local
    # data library does have a 4h parquet file, so keep it current by deriving
    # 4h bars from the just-fetched smaller-period data.
    result["4h"] = synthesize_4h_from_smaller(
        result.get("1h"), result.get("1min")
    )
    return result
