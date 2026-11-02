from __future__ import annotations

"""Tianqin EDB historical futures-data client and local period builder.

EDB provides native 1-minute and daily bars.  The application derives 5/15/30m,
1h and 4h templates from the complete local 1-minute series; weekly/monthly bars
remain derived from native daily data by DataRepository.
"""

import io
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd

from .trading_calendar import session_trading_days


EDB_BASE_URL = "https://edb.shinnytech.com"
SHANGHAI = timezone(timedelta(hours=8), name="Asia/Shanghai")


def _now_shanghai() -> datetime:
    return datetime.now(SHANGHAI).replace(tzinfo=None)

EDB_KLINE_URL = f"{EDB_BASE_URL}/md/kline"
EDB_TOKEN_URL = f"{EDB_BASE_URL}/token"
EDB_COVERAGE_START = date(2021, 1, 1)
MINUTE_PERIODS = {
    "5min": 5,
    "15min": 15,
    "30min": 30,
    "1h": 60,
    "4h": 240,
}
# If summed local minute volume is below this fraction of the native daily
# volume, treat the trading day as materially incomplete and refresh it.
MINUTE_VOLUME_COVERAGE_THRESHOLD = 0.80
STANDARD_COLUMNS = [
    "datetime", "open", "high", "low", "close",
    "volume", "amount", "open_interest", "trading_day",
]


class EDBUpdateError(RuntimeError):
    pass


class EDBUpdateCancelled(EDBUpdateError):
    pass


@dataclass
class EDBUpdateStats:
    minute_added: int = 0
    minute_total: int = 0
    daily_added: int = 0
    daily_total: int = 0
    period_rows: dict[str, int] = field(default_factory=dict)
    last_timestamp: pd.Timestamp | None = None
    minute_last_timestamp: pd.Timestamp | None = None
    daily_last_timestamp: pd.Timestamp | None = None
    fetched_minute_last_timestamp: pd.Timestamp | None = None
    fetched_daily_last_timestamp: pd.Timestamp | None = None
    requested_end: pd.Timestamp | None = None


def exchange_code(exchange_text: str) -> str:
    text = str(exchange_text or "").upper().replace(" ", "")
    aliases = {
        "CFFEX": ("CFFEX", "中金所", "中国金融期货交易所"),
        "SHFE": ("SHFE", "上期所", "上海期货交易所"),
        "INE": ("INE", "能源中心", "上海国际能源交易中心", "国际能源交易中心"),
        "DCE": ("DCE", "大商所", "大连商品交易所"),
        "CZCE": ("CZCE", "郑商所", "郑州商品交易所"),
        "GFEX": ("GFEX", "广期所", "广州期货交易所"),
    }
    for code, names in aliases.items():
        if any(name.upper().replace(" ", "") in text for name in names):
            return code
    if text in aliases:
        return text
    raise EDBUpdateError(f"无法识别交易所：{exchange_text or '未填写'}。请检查品种数据说明中的交易所字段。")


def product_code(symbol: str) -> str:
    match = re.match(r"\s*([A-Za-z]+)", str(symbol or ""))
    if not match:
        raise EDBUpdateError(f"无法从品种代码中识别商品代码：{symbol}")
    return match.group(1).upper()


def make_edb_contract_symbol(exchange_text: str, contract: str) -> str:
    """Convert a local contract name (e.g. EC2610) to INE.ec2610."""
    value = str(contract or "").strip()
    if not value:
        raise EDBUpdateError("当前没有选择可更新的合约。")
    if "." in value:
        ex, code = value.split(".", 1)
        if ex.upper() in {"CFFEX", "SHFE", "INE", "DCE", "CZCE", "GFEX"}:
            code_exchange = ex.upper()
        else:
            code_exchange = exchange_code(exchange_text)
            code = value
    else:
        code_exchange = exchange_code(exchange_text)
        code = value
    # Tianqin's documented symbol examples use upper-case product codes for
    # CFFEX/CZCE and lower-case product codes for the commodity exchanges.
    normalized_code = code.upper() if code_exchange in {"CFFEX", "CZCE"} else code.lower()
    return f"{code_exchange}.{normalized_code}"


def make_edb_main_symbol(exchange_text: str, symbol: str) -> str:
    """Return Tianqin's unadjusted main-continuous symbol with exchange case rules.

    Product identifiers are case-sensitive in TqSdk/EDB symbols:
    SHFE, DCE, INE and GFEX commodity ids use lower case; CZCE and CFFEX
    use upper case.  Examples: KQ.m@SHFE.sn, KQ.m@DCE.m, KQ.m@CFFEX.IF.
    """
    exchange = exchange_code(exchange_text)
    code = product_code(symbol)
    if exchange in {"CZCE", "CFFEX"}:
        code = code.upper()
    else:
        code = code.lower()
    return f"KQ.m@{exchange}.{code}"


def request_edb_token(username: str, password: str, timeout: float = 10.0) -> str:
    username, password = str(username or "").strip(), str(password or "")
    if not username or not password:
        raise EDBUpdateError("天勤 EDB 用户名和密码不能为空。")
    payload = json.dumps({"username": username, "password": password}).encode("utf-8")
    req = urllib.request.Request(
        EDB_TOKEN_URL,
        data=payload,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise EDBUpdateError(f"天勤 EDB 登录失败（HTTP {exc.code}）：{body or exc.reason}") from exc
    except Exception as exc:
        raise EDBUpdateError(f"连接天勤 EDB Token 服务失败：{exc}") from exc
    token = str(data.get("token") or "").strip()
    if not token:
        raise EDBUpdateError("天勤 EDB Token 服务没有返回有效 token。")
    return token


def _request_csv(
    symbol: str,
    period_seconds: int,
    start_time: datetime,
    end_time: datetime,
    token: str | None = None,
    timeout: float = 60.0,
) -> pd.DataFrame:
    params = {
        "period": str(int(period_seconds)),
        "symbol": str(symbol),
        "start_time": start_time.strftime("%Y-%m-%d %H:%M:%S"),
        "end_time": end_time.strftime("%Y-%m-%d %H:%M:%S"),
        "col": "open,high,low,close,volume,open_oi,close_oi",
    }
    url = EDB_KLINE_URL + "?" + urllib.parse.urlencode(params)

    # The official EDB examples do not send an Accept header.  Some EDB gateway
    # deployments reject an explicit media type with HTTP 406 / "Malformed
    # Accept", even though text/csv is the response format.  Start without the
    # header; only retry a 406 with the generic */* value for gateway variants.
    base_headers = {"User-Agent": "FuturesReplayTrainer/2.1"}
    if token:
        base_headers["Authorization"] = f"Bearer {token}"
    header_attempts = [base_headers, {**base_headers, "Accept": "*/*"}]

    raw = ""
    last_http_error: tuple[int, str] | None = None
    for attempt, headers in enumerate(header_attempts):
        req = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                raw = response.read().decode("utf-8-sig", errors="replace")
            last_http_error = None
            break
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace").strip()
            last_http_error = (int(exc.code), body or str(exc.reason))
            if exc.code == 406 and attempt == 0:
                continue
            if exc.code == 403 or body.startswith("403,"):
                raise EDBUpdateError(
                    "天勤 EDB 拒绝了分钟线请求。免费权限只能获取最近一年的 1 分钟数据；"
                    "如需更早的分钟历史，请确认账号已开通专业版 EDB 权限并重新登录。\n"
                    f"服务返回：{body or exc.reason}"
                ) from exc
            if exc.code == 406:
                raise EDBUpdateError(
                    "天勤 EDB 行情接口仍返回 HTTP 406。程序已先按官方示例省略 Accept 请求头，"
                    "再以 Accept: */* 重试；两次均未通过。请检查网络代理或将完整错误信息反馈给我。\n"
                    f"服务返回：{body or exc.reason}"
                ) from exc
            raise EDBUpdateError(f"天勤 EDB 请求失败（HTTP {exc.code}）：{body or exc.reason}") from exc
        except Exception as exc:
            raise EDBUpdateError(f"连接天勤 EDB 行情历史服务失败：{exc}") from exc

    if last_http_error is not None:
        code, body = last_http_error
        raise EDBUpdateError(f"天勤 EDB 请求失败（HTTP {code}）：{body}")
    if not raw.strip():
        return pd.DataFrame()

    first = raw.lstrip().splitlines()[0].strip()
    if first.startswith(("400,", "401,", "403,", "404,", "406,", "500,")):
        if first.startswith("403,"):
            raise EDBUpdateError(
                "天勤 EDB 分钟数据权限不足。免费权限仅支持最近一年的分钟线；"
                "如需更早数据，请使用已开通专业版 EDB 权限的账号。\n" + first
            )
        raise EDBUpdateError(f"天勤 EDB 返回错误：{first}")
    if first.startswith("{"):
        try:
            payload = json.loads(raw)
            message = payload.get("message") or payload.get("error_msg") or raw[:500]
        except Exception:
            message = raw[:500]
        raise EDBUpdateError(f"天勤 EDB 返回的不是 CSV 数据：{message}")
    try:
        return pd.read_csv(io.StringIO(raw))
    except Exception as exc:
        raise EDBUpdateError(f"天勤 EDB CSV 数据解析失败：{exc}") from exc


def _normalize_edb_chunk(raw: pd.DataFrame, period_seconds: int) -> pd.DataFrame:
    if raw is None or raw.empty:
        return pd.DataFrame(columns=STANDARD_COLUMNS)
    if "datetime_nano" not in raw.columns:
        raise EDBUpdateError("天勤 EDB 数据缺少 datetime_nano 时间字段。")
    ns = pd.to_numeric(raw["datetime_nano"], errors="coerce")
    # EDB timestamps are Unix nanoseconds; the documented wall-clock zone is Asia/Shanghai.
    dt = pd.to_datetime(ns, unit="ns", utc=True, errors="coerce").dt.tz_localize(None) + pd.Timedelta(hours=8)
    close_oi = raw.get("close_oi", pd.Series(0.0, index=raw.index))
    out = pd.DataFrame({
        "datetime": dt,
        "open": pd.to_numeric(raw.get("open"), errors="coerce"),
        "high": pd.to_numeric(raw.get("high"), errors="coerce"),
        "low": pd.to_numeric(raw.get("low"), errors="coerce"),
        "close": pd.to_numeric(raw.get("close"), errors="coerce"),
        "volume": pd.to_numeric(raw.get("volume", pd.Series(0.0, index=raw.index)), errors="coerce").fillna(0.0),
        "amount": 0.0,
        "open_interest": pd.to_numeric(close_oi, errors="coerce").fillna(0.0),
    })
    out = out.dropna(subset=["datetime", "open", "high", "low", "close"])
    if period_seconds == 86400:
        out["trading_day"] = out["datetime"].dt.strftime("%Y-%m-%d")
    else:
        # Temporary natural date; replaced with the actual trading calendar after daily fetch.
        out["trading_day"] = out["datetime"].dt.strftime("%Y-%m-%d")
    return out[STANDARD_COLUMNS].sort_values("datetime").drop_duplicates("datetime", keep="last").reset_index(drop=True)


def _date_chunks(start: datetime, end: datetime, chunk_days: int):
    cursor = start
    while cursor <= end:
        right = min(cursor + timedelta(days=chunk_days - 1), end)
        yield cursor, right
        cursor = right + timedelta(days=1)


def _fetch_period(
    symbol: str,
    period_seconds: int,
    start: datetime,
    end: datetime,
    token: str | None,
    progress_cb: Callable[[str], None] | None,
    cancel_cb: Callable[[], bool] | None,
    chunk_days: int,
) -> pd.DataFrame:
    parts: list[pd.DataFrame] = []
    for left, right in _date_chunks(start, end, chunk_days):
        if cancel_cb and cancel_cb():
            raise EDBUpdateCancelled("用户取消天勤 EDB 数据更新。")
        if progress_cb:
            unit = "1分钟" if period_seconds == 60 else "日线"
            progress_cb(f"{symbol} {unit} {left:%Y-%m-%d} ~ {right:%Y-%m-%d}")
        raw = _request_csv(symbol, period_seconds, left, right.replace(hour=23, minute=59, second=59), token)
        if not raw.empty:
            frame = _normalize_edb_chunk(raw, period_seconds)
            if not frame.empty:
                parts.append(frame)
        # A modest pause avoids sending a burst of back-to-back historical requests.
        time.sleep(0.04)
    if not parts:
        return pd.DataFrame(columns=STANDARD_COLUMNS)
    out = pd.concat(parts, ignore_index=True, sort=False)
    out = out.sort_values("datetime").drop_duplicates("datetime", keep="last").reset_index(drop=True)
    return out[STANDARD_COLUMNS]


def _next_weekday(d: date) -> date:
    target = d + timedelta(days=1)
    while target.weekday() >= 5:
        target += timedelta(days=1)
    return target


def assign_trading_day(minutes: pd.DataFrame, daily: pd.DataFrame) -> pd.DataFrame:
    if minutes.empty:
        return minutes.copy()
    out = minutes.copy()
    calendar = daily.get("trading_day", ()) if daily is not None else ()
    out["trading_day"] = session_trading_days(out["datetime"], calendar)
    return out[STANDARD_COLUMNS].sort_values("datetime").drop_duplicates("datetime", keep="last").reset_index(drop=True)


def fetch_edb_bundle(
    symbol: str,
    minute_start: datetime,
    daily_start: datetime,
    token: str | None = None,
    progress_cb: Callable[[str], None] | None = None,
    cancel_cb: Callable[[], bool] | None = None,
) -> dict[str, pd.DataFrame]:
    """Fetch native EDB 1-minute and daily bars for a specified time range."""
    symbol = str(symbol or "").strip()
    if not symbol:
        raise EDBUpdateError("天勤 EDB 更新缺少合约代码。")
    now = _now_shanghai()
    # Keep minute responses to one trading week.  Large monthly CSV responses
    # can be clipped by gateways or interrupted mid-stream without a clean HTTP
    # error; shorter requests make missing tail data observable and recoverable.
    daily = _fetch_period(symbol, 86400, daily_start, now, token, progress_cb, cancel_cb, 365)
    minutes = _fetch_period(symbol, 60, minute_start, now, token, progress_cb, cancel_cb, 7)
    minutes = assign_trading_day(minutes, daily)
    return {
        "1min": minutes,
        "1d": daily,
        "_meta": {
            "requested_end": pd.Timestamp(now),
            "fetched_minute_last_timestamp": (
                pd.Timestamp(minutes["datetime"].max()) if not minutes.empty else None
            ),
            "fetched_daily_last_timestamp": (
                pd.Timestamp(daily["datetime"].max()) if not daily.empty else None
            ),
        },
    }


def _session_group_ids(frame: pd.DataFrame, gap_minutes: int = 10) -> pd.Series:
    dt = pd.to_datetime(frame["datetime"], errors="coerce")
    td = frame["trading_day"].astype(str)
    gap = dt.diff().dt.total_seconds().fillna(0.0)
    new_session = (gap > int(gap_minutes) * 60) | td.ne(td.shift(1))
    return new_session.cumsum().astype(int)


def synthesize_intraday_periods(minutes: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Aggregate complete 1-minute history into all supported intraday periods.

    Bars are counted within each trading session.  A gap longer than ten minutes
    starts a new session (the 4h builder uses a three-hour threshold), so lunch breaks and the night/day boundary are not
    accidentally merged into one synthetic candle.
    """
    if minutes is None or minutes.empty:
        return {period: pd.DataFrame(columns=STANDARD_COLUMNS) for period in MINUTE_PERIODS}
    x = minutes.copy()
    x["datetime"] = pd.to_datetime(x["datetime"], errors="coerce")
    x = x.dropna(subset=["datetime", "open", "high", "low", "close"]).copy()
    if "trading_day" not in x.columns:
        x["trading_day"] = x["datetime"].dt.strftime("%Y-%m-%d")
    x["trading_day"] = x["trading_day"].astype(str)
    x = x.sort_values("datetime").drop_duplicates("datetime", keep="last").reset_index(drop=True)
    results: dict[str, pd.DataFrame] = {}
    for period, block_size in MINUTE_PERIODS.items():
        part = x.copy()
        # Hourly/short bars must not cross ordinary breaks.  A 4h bar may span
        # the midday break (up to three hours) but must not combine overnight
        # and day sessions into one candle.
        gap_minutes = 180 if period == "4h" else 10
        part["_session"] = _session_group_ids(part, gap_minutes=gap_minutes)
        part["_block"] = part.groupby(["trading_day", "_session"], sort=False).cumcount() // int(block_size)
        grouped = part.groupby(["trading_day", "_session", "_block"], sort=False)
        out = grouped.agg(
            datetime=("datetime", "first"),
            open=("open", "first"),
            high=("high", "max"),
            low=("low", "min"),
            close=("close", "last"),
            volume=("volume", "sum"),
            amount=("amount", "sum"),
            open_interest=("open_interest", "last"),
        ).reset_index()
        out = out[STANDARD_COLUMNS].sort_values("datetime").reset_index(drop=True)
        results[period] = out
    return results


def _replace_rebuilt_period(
    repo, source: str, symbol: str, contract: str, period: str,
    rebuilt: pd.DataFrame, covered_days: set[str],
) -> int:
    """Replace derived bars for trading days covered by minute data.

    A newly repaired minute gap can change the grouping boundaries of every
    following synthetic bar in that trading day.  A plain append/merge would
    leave the old, now-invalid timestamps behind.  We therefore discard old
    derived bars for adequately covered trading days, retain older/uncovered
    days, and then write the rebuilt result atomically.
    """
    path = repo._resolve_file(source, symbol, period, contract)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        old = repo.standardize(pd.read_parquet(path))
    else:
        old = pd.DataFrame(columns=STANDARD_COLUMNS)
    if not old.empty and covered_days:
        old = old.loc[~old["trading_day"].astype(str).isin(covered_days)].copy()
    pieces = [part for part in (old, rebuilt) if part is not None and not part.empty]
    if not pieces:
        return 0
    merged = repo.standardize(pd.concat(pieces, ignore_index=True, sort=False))
    merged = (
        merged.sort_values("datetime")
        .drop_duplicates("datetime", keep="last")
        .reset_index(drop=True)
    )
    tmp = path.with_name(path.name + ".tmp")
    if tmp.exists():
        tmp.unlink()
    merged[STANDARD_COLUMNS].to_parquet(tmp, index=False)
    tmp.replace(path)
    return len(merged)


def persist_edb_bundle(repo, source: str, symbol: str, contract: str, bundle: dict[str, pd.DataFrame]) -> EDBUpdateStats:
    """Merge EDB native bars, rebuild intraday periods and retain freshness diagnostics."""
    stats = EDBUpdateStats()
    minute = bundle.get("1min", pd.DataFrame(columns=STANDARD_COLUMNS))
    daily = bundle.get("1d", pd.DataFrame(columns=STANDARD_COLUMNS))
    meta = bundle.get("_meta", {}) if isinstance(bundle.get("_meta", {}), dict) else {}
    stats.requested_end = (
        pd.Timestamp(meta["requested_end"]) if meta.get("requested_end") is not None else None
    )
    if minute is not None and not minute.empty:
        stats.fetched_minute_last_timestamp = pd.Timestamp(minute["datetime"].max())
        added, total, last = repo.append_bars(source, symbol, "1min", contract, minute)
        stats.minute_added, stats.minute_total = int(added), int(total)
        stats.minute_last_timestamp = pd.Timestamp(last) if last is not None else None
    else:
        path = repo._resolve_file(source, symbol, "1min", contract)
        if path.exists():
            local = repo.standardize(pd.read_parquet(path))
            stats.minute_total = len(local)
            if not local.empty:
                stats.minute_last_timestamp = pd.Timestamp(local["datetime"].max())

    if daily is not None and not daily.empty:
        stats.fetched_daily_last_timestamp = pd.Timestamp(daily["datetime"].max())
        added, total, last = repo.append_bars(source, symbol, "1d", contract, daily)
        stats.daily_added, stats.daily_total = int(added), int(total)
        stats.daily_last_timestamp = pd.Timestamp(last) if last is not None else None
    else:
        path = repo._resolve_file(source, symbol, "1d", contract)
        if path.exists():
            local = repo.standardize(pd.read_parquet(path))
            stats.daily_total = len(local)
            if not local.empty:
                stats.daily_last_timestamp = pd.Timestamp(local["datetime"].max())

    # Re-read the local maxima after merge so the report distinguishes what EDB
    # returned in this request from the actual latest local rows after append.
    minute_path = repo._resolve_file(source, symbol, "1min", contract)
    if minute_path.exists():
        full_minutes = repo.standardize(pd.read_parquet(minute_path))
        if not full_minutes.empty:
            stats.minute_last_timestamp = pd.Timestamp(full_minutes["datetime"].max())
        synthesized = synthesize_intraday_periods(full_minutes)
        minute_counts = full_minutes.groupby(full_minutes["trading_day"].astype(str)).size()
        covered_days = set(minute_counts[minute_counts >= 30].index.astype(str))
        for period, frame in synthesized.items():
            if frame.empty:
                continue
            total = _replace_rebuilt_period(
                repo, source, symbol, contract, period, frame, covered_days
            )
            stats.period_rows[period] = int(total)

    daily_path = repo._resolve_file(source, symbol, "1d", contract)
    if daily_path.exists():
        local_daily = repo.standardize(pd.read_parquet(daily_path))
        if not local_daily.empty:
            stats.daily_last_timestamp = pd.Timestamp(local_daily["datetime"].max())

    maxima = [x for x in (stats.minute_last_timestamp, stats.daily_last_timestamp) if x is not None]
    stats.last_timestamp = max(maxima) if maxima else None
    repo.clear_cache()
    return stats


def _parse_day_values(values: pd.Series) -> pd.Series:
    """Parse trading-day values, including compact YYYYMMDD encodings."""
    raw = pd.Series(values).copy()
    text = raw.astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
    out = pd.Series(pd.NaT, index=raw.index, dtype="datetime64[ns]")
    compact = text.str.fullmatch(r"\d{8}")
    if compact.any():
        out.loc[compact] = pd.to_datetime(text.loc[compact], format="%Y%m%d", errors="coerce")
    remaining = out.isna() & raw.notna()
    if remaining.any():
        out.loc[remaining] = pd.to_datetime(raw.loc[remaining], errors="coerce")
    return out.dt.normalize()


def _normalise_shanghai_now(now: datetime | None = None) -> pd.Timestamp:
    """Return the supplied/current Shanghai time as a timezone-naive timestamp."""
    value = pd.Timestamp(now or _now_shanghai())
    if value.tzinfo is not None:
        value = value.tz_convert(SHANGHAI).tz_localize(None)
    return value


def detect_earliest_minute_gap(
    minutes: pd.DataFrame,
    daily: pd.DataFrame | None = None,
    now: datetime | None = None,
    lookback_days: int | None = None,
    reference_days: int = 60,
    min_missing_run: int = 3,
    lookback_months: int = 6,
) -> datetime | None:
    """Find the earliest missing minute-data point in the rolling six-month window.

    The local daily series is the session calendar.  A positive-volume trading
    day with no minute bars is a definite gap; a material mismatch between
    daily volume and summed minute volume is also treated as incomplete coverage.
    For better-populated days, recurring minute-of-day blocks are learned from
    recent sessions and checked for consecutive holes.

    ``lookback_days`` remains available for callers that need an explicit day
    count.  Normal application updates use six calendar months via
    ``lookback_months``.  The returned timestamp is one calendar day before the
    first affected trading day so a 21:00 night session is included.
    """
    now_ts = _normalise_shanghai_now(now)
    if lookback_days is not None:
        cutoff = (now_ts.normalize() - pd.Timedelta(days=int(lookback_days)))
    else:
        cutoff = (now_ts - pd.DateOffset(months=int(lookback_months))).normalize()
    today = now_ts.normalize()

    # Build the authoritative trading-day calendar.  Ignore zero-volume daily
    # rows when volume is available: they do not prove that minute bars should
    # exist for that date.
    daily_days: set[str] = set()
    daily_volume_by_day: dict[str, float] = {}
    if daily is not None and not daily.empty:
        daily_col = "trading_day" if "trading_day" in daily.columns else ("datetime" if "datetime" in daily.columns else None)
        if daily_col is not None:
            parsed = _parse_day_values(daily[daily_col])
            valid = parsed.notna() & (parsed >= cutoff) & (parsed <= today)
            volume = None
            if "volume" in daily.columns:
                volume = pd.to_numeric(daily["volume"], errors="coerce")
                if volume.notna().any():
                    valid &= volume.fillna(0.0) > 0.0
            daily_days = set(parsed.loc[valid].dt.strftime("%Y-%m-%d").tolist())
            if volume is not None:
                for day_value, volume_value in zip(parsed.loc[valid], volume.loc[valid]):
                    if pd.notna(volume_value):
                        daily_volume_by_day[pd.Timestamp(day_value).strftime("%Y-%m-%d")] = float(volume_value)

    # Preserve trading_day rather than natural calendar date so night-session
    # minutes are assigned to the following exchange trading day.
    day_sets: dict[str, set[int]] = {}
    minute_volume_by_day: dict[str, float] = {}
    if minutes is not None and not minutes.empty and "datetime" in minutes.columns:
        cols = [c for c in ("datetime", "trading_day", "volume") if c in minutes.columns]
        x = minutes[cols].copy()
        x["_dt"] = pd.to_datetime(x["datetime"], errors="coerce")
        if getattr(x["_dt"].dt, "tz", None) is not None:
            x["_dt"] = x["_dt"].dt.tz_convert(SHANGHAI).dt.tz_localize(None)
        x = x.dropna(subset=["_dt"])
        if "trading_day" in x.columns:
            trading_days = _parse_day_values(x["trading_day"])
        else:
            trading_days = x["_dt"].dt.normalize()
        trading_days = trading_days.fillna(x["_dt"].dt.normalize())
        x["_day"] = trading_days.dt.strftime("%Y-%m-%d")
        x = x.loc[(trading_days >= cutoff) & (trading_days <= today)].copy()
        x["_tod"] = x["_dt"].dt.hour * 60 + x["_dt"].dt.minute
        if "volume" in x.columns:
            minute_volume = pd.to_numeric(x["volume"], errors="coerce")
            if minute_volume.notna().any():
                x["_volume"] = minute_volume.fillna(0.0)
        x = x.drop_duplicates(["_day", "_tod"], keep="last")
        for day, part in x.groupby("_day", sort=True):
            day_key = str(day)
            day_sets[day_key] = set(
                pd.to_numeric(part["_tod"], errors="coerce").dropna().astype(int).tolist()
            )
            if "_volume" in part.columns:
                minute_volume_by_day[day_key] = float(part["_volume"].sum())

    candidates: list[date] = []

    # Definite whole-day gaps and severe volume mismatches.  Do not impose a
    # fixed minimum bar count: a genuinely quiet contract day can contain only
    # a small number of traded minutes.  The daily/minute volume comparison is
    # a better signal when both files expose volume.
    for day_text in daily_days:
        count = len(day_sets.get(day_text, set()))
        if count == 0:
            candidates.append(pd.Timestamp(day_text).date())
            continue
        daily_volume = daily_volume_by_day.get(day_text)
        minute_volume = minute_volume_by_day.get(day_text)
        if daily_volume is not None and daily_volume > 0.0:
            if minute_volume is not None and minute_volume < daily_volume * MINUTE_VOLUME_COVERAGE_THRESHOLD:
                candidates.append(pd.Timestamp(day_text).date())
            elif minute_volume is None and count < 5:
                candidates.append(pd.Timestamp(day_text).date())

    # Learn recurring session clocks from the latest well-populated days.
    populated_days = [
        d for d in sorted(day_sets)
        if len(day_sets[d]) >= 30 and (not daily_days or d in daily_days)
    ]
    reference = populated_days[-max(8, int(reference_days)):]
    if len(reference) >= 5:
        from collections import Counter

        frequency: Counter[int] = Counter()
        for day in reference:
            frequency.update(day_sets[day])
        threshold = max(3, int(np.ceil(len(reference) * 0.60)))
        expected = sorted(tod for tod, count in frequency.items() if count >= threshold)

        # Separate night/day sessions and scheduled breaks.  A 45-minute
        # threshold is intentionally conservative across different exchanges.
        blocks: list[list[int]] = []
        for tod in expected:
            if not blocks or tod - blocks[-1][-1] > 45:
                blocks.append([tod])
            else:
                blocks[-1].append(tod)

        ref_threshold = max(3, int(np.ceil(len(reference) * 0.65)))
        recurring_blocks = [
            block for block in blocks
            if len(block) >= 3
            and sum(bool(day_sets[d].intersection(block)) for d in reference) >= ref_threshold
        ]

        for day, actual in day_sets.items():
            if daily_days and day not in daily_days:
                continue
            if len(actual) < 30:
                # Too few bars to infer a reliable clock pattern.  A material
                # volume mismatch, if present, was already captured above.
                continue
            has_gap = False
            for block in recurring_blocks:
                observed = actual.intersection(block)
                if not observed:
                    # A normally recurring session is entirely absent.
                    if day in daily_days:
                        has_gap = True
                        break
                    continue
                run = 0
                for value in block:
                    if value not in actual:
                        run += 1
                        if run >= int(min_missing_run):
                            has_gap = True
                            break
                    else:
                        run = 0
                if has_gap:
                    break
            if has_gap:
                try:
                    candidates.append(pd.Timestamp(day).date())
                except Exception:
                    pass

    if not candidates:
        return None
    earliest = min(candidates) - timedelta(days=1)
    return datetime.combine(earliest, datetime.min.time())


def incremental_start_times(
    minute_path: Path,
    daily_path: Path,
    has_pro_token: bool,
    now: datetime | None = None,
) -> tuple[datetime, datetime]:
    """Return EDB request starts after scanning the rolling six-month window.

    The scanner checks the daily trading calendar and the recurring intraday
    minute schedule.  If it finds a gap, the minute request begins at the
    earliest affected trading day (including the previous evening for night
    sessions) and runs through the present, thereby repairing all later gaps
    in one pass.  If no gap is found, only the latest two calendar days are
    refreshed.  The normal update is deliberately capped at six months for
    both free and professional accounts; the separate full-history action is
    responsible for older history.
    """
    now_ts = _normalise_shanghai_now(now)
    now_dt = now_ts.to_pydatetime()
    # Retain has_pro_token in the public signature for compatibility.  The
    # normal six-month scan is within the free tier's one-year minute window.
    _ = has_pro_token
    coverage_start = pd.Timestamp(EDB_COVERAGE_START)
    scan_start = (now_ts - pd.DateOffset(months=6)).normalize()
    # A trading day may begin with a 21:00 session on the preceding calendar
    # day, so the EDB request floor includes that evening.
    minute_floor_ts = max(coverage_start, scan_start - pd.Timedelta(days=1))
    minute_floor = minute_floor_ts.to_pydatetime()
    daily_floor = datetime.combine(EDB_COVERAGE_START, datetime.min.time())

    local_minutes = pd.DataFrame(columns=["datetime", "trading_day"])
    local_daily = pd.DataFrame(columns=["trading_day", "volume"])
    if minute_path.exists():
        try:
            try:
                local_minutes = pd.read_parquet(minute_path, columns=["datetime", "trading_day", "volume"])
            except Exception:
                try:
                    local_minutes = pd.read_parquet(minute_path, columns=["datetime", "trading_day"])
                except Exception:
                    try:
                        local_minutes = pd.read_parquet(minute_path, columns=["datetime", "volume"])
                    except Exception:
                        local_minutes = pd.read_parquet(minute_path, columns=["datetime"])
        except Exception:
            local_minutes = pd.DataFrame(columns=["datetime", "trading_day"])

    if daily_path.exists():
        try:
            try:
                local_daily = pd.read_parquet(daily_path, columns=["trading_day", "volume"])
            except Exception:
                try:
                    local_daily = pd.read_parquet(daily_path, columns=["datetime", "volume"])
                except Exception:
                    try:
                        local_daily = pd.read_parquet(daily_path, columns=["trading_day"])
                    except Exception:
                        local_daily = pd.read_parquet(daily_path, columns=["datetime"])
        except Exception:
            local_daily = pd.DataFrame(columns=["trading_day", "volume"])

    # Default incremental refresh: overlap the latest two calendar days, but
    # never request data older than the six-month rolling window.
    minute_start = minute_floor
    if not local_minutes.empty and "datetime" in local_minutes.columns:
        last = pd.to_datetime(local_minutes["datetime"], errors="coerce").dropna().max()
        if pd.notna(last):
            minute_start = max(
                minute_floor,
                pd.Timestamp(last).normalize().to_pydatetime() - timedelta(days=2),
            )

    # A usable recent daily calendar is required to prove that whole trading
    # sessions are present.  If it is absent/stale, conservatively request the
    # complete six-month minute window rather than silently missing full-day
    # holes that cannot be inferred from the minute file alone.
    daily_col = "trading_day" if "trading_day" in local_daily.columns else ("datetime" if "datetime" in local_daily.columns else None)
    has_recent_daily_calendar = False
    if daily_col is not None and not local_daily.empty:
        parsed_daily = _parse_day_values(local_daily[daily_col])
        valid_daily = parsed_daily.notna() & (parsed_daily >= scan_start) & (parsed_daily <= now_ts.normalize())
        if "volume" in local_daily.columns:
            daily_volume = pd.to_numeric(local_daily["volume"], errors="coerce")
            if daily_volume.notna().any():
                valid_daily &= daily_volume.fillna(0.0) > 0.0
        has_recent_daily_calendar = bool(valid_daily.any())

    if not has_recent_daily_calendar:
        minute_start = minute_floor
    else:
        gap_start = detect_earliest_minute_gap(
            local_minutes,
            local_daily,
            now=now_dt,
            lookback_months=6,
        )
        if gap_start is not None:
            minute_start = max(minute_floor, min(minute_start, gap_start))

    # Daily bars are also refreshed across the minute request window.  This is
    # important because night-session minute rows must be mapped using the
    # corresponding exchange trading-day calendar, not a weekday approximation.
    daily_start = daily_floor
    if daily_path.exists():
        try:
            try:
                daily_full = pd.read_parquet(daily_path, columns=["trading_day"])
                daily_values = _parse_day_values(daily_full["trading_day"])
            except Exception:
                daily_full = pd.read_parquet(daily_path, columns=["datetime"])
                daily_values = _parse_day_values(daily_full["datetime"])
            last_daily = daily_values.dropna().max()
            if pd.notna(last_daily):
                daily_start = max(
                    daily_floor,
                    pd.Timestamp(last_daily).normalize().to_pydatetime() - timedelta(days=10),
                )
        except Exception:
            daily_start = daily_floor
    daily_start = max(daily_floor, min(daily_start, minute_start))
    return minute_start, daily_start

def full_history_start_times(has_pro_token: bool, now: datetime | None = None) -> tuple[datetime, datetime]:
    now = now or _now_shanghai()
    minute_start = datetime.combine(EDB_COVERAGE_START, datetime.min.time()) if has_pro_token else (now - timedelta(days=365)).replace(hour=0, minute=0, second=0, microsecond=0)
    daily_start = datetime.combine(EDB_COVERAGE_START, datetime.min.time())
    return minute_start, daily_start
