from __future__ import annotations

"""Full-history futures rebuild via Tushare Pro.

This module is intentionally dependency-free beyond pandas/pyarrow already used
by the project.  It talks to Tushare's HTTP API directly.

Important coverage notes (per Tushare documentation):
- futures minute history (ft_mins): 1/5/15/30/60 minute, over 10 years of
  history, separate paid permission required;
- futures daily history (fut_daily): starts in 1995;
- contract metadata (fut_basic): requires the normal futures data permission.

The rebuild is atomic per parquet file: data is written to a temporary file and
only replaces the local file after the complete frame has been fetched and
validated.  Existing files are therefore preserved when a request fails.
"""

import json
import os
import time
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable

import pandas as pd

from .online_update import synthesize_4h_from_smaller
from .trading_calendar import session_trading_days


class HistoryUpdateError(RuntimeError):
    pass


class HistoryUpdateCancelled(HistoryUpdateError):
    pass


_API_URL = "https://api.tushare.pro"
_STANDARD_COLUMNS = [
    "datetime", "open", "high", "low", "close",
    "volume", "amount", "open_interest", "trading_day",
]
_EXCHANGES = ("SHFE", "DCE", "CZCE", "CFFEX", "INE", "GFEX")

# Chunk sizes are chosen to stay comfortably below ft_mins' 8000-row limit.
_CHUNK_DAYS = {
    "1min": 18,
    "5min": 85,
    "15min": 250,
    "30min": 500,
    "60min": 1000,
}
_LOCAL_PERIOD = {
    "1min": "1min",
    "5min": "5min",
    "15min": "15min",
    "30min": "30min",
    "60min": "1h",
}


@dataclass(frozen=True)
class ContractInfo:
    ts_code: str
    symbol: str
    exchange: str
    list_date: date
    delist_date: date | None


@dataclass
class InstrumentRebuildStats:
    symbol: str
    contracts: int = 0
    files_written: int = 0
    rows_written: int = 0
    failures: int = 0
    errors: list[str] | None = None

    def __post_init__(self):
        if self.errors is None:
            self.errors = []


def _api_call(
    api_name: str,
    token: str,
    params: dict,
    fields: str = "",
    timeout: float = 30.0,
) -> pd.DataFrame:
    if not token.strip():
        raise HistoryUpdateError("缺少 Tushare Token。")
    body = json.dumps(
        {"api_name": api_name, "token": token.strip(), "params": params, "fields": fields},
        ensure_ascii=False,
    ).encode("utf-8")
    req = urllib.request.Request(
        _API_URL,
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": "FuturesReplayTrainer/1.0"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception as exc:
        raise HistoryUpdateError(f"连接 Tushare 失败：{exc}") from exc

    code = payload.get("code", -1)
    if code != 0:
        msg = payload.get("msg") or "未知错误"
        raise HistoryUpdateError(f"Tushare {api_name} 返回错误：{msg}")
    data = payload.get("data") or {}
    cols = data.get("fields") or []
    items = data.get("items") or []
    return pd.DataFrame(items, columns=cols)


def exchange_from_text(text: str) -> str | None:
    s = (text or "").upper().replace(" ", "")
    mapping = [
        ("CFFEX", ("CFFEX", "中金所", "中国金融期货交易所")),
        ("SHFE", ("SHFE", "上期所", "上海期货交易所")),
        ("INE", ("INE", "能源中心", "上海国际能源交易中心", "国际能源交易中心")),
        ("DCE", ("DCE", "大商所", "大连商品交易所")),
        ("CZCE", ("CZCE", "郑商所", "郑州商品交易所")),
        ("GFEX", ("GFEX", "广期所", "广州期货交易所")),
    ]
    for code, aliases in mapping:
        if any(a.upper().replace(" ", "") in s for a in aliases):
            return code
    return None


def list_product_contracts(token: str, fut_code: str, exchange_text: str = "") -> list[ContractInfo]:
    """Return all ordinary contracts for one product, oldest first."""
    fut_code = fut_code.upper().strip()
    preferred = exchange_from_text(exchange_text)
    exchanges = [preferred] if preferred else list(_EXCHANGES)
    found: list[ContractInfo] = []
    fields = "ts_code,symbol,exchange,fut_code,list_date,delist_date"
    for ex in exchanges:
        df = _api_call(
            "fut_basic",
            token,
            {"exchange": ex, "fut_type": "1", "fut_code": fut_code},
            fields,
        )
        if df.empty:
            continue
        for row in df.to_dict("records"):
            code = str(row.get("fut_code") or "").upper()
            if code and code != fut_code:
                continue
            list_ts = pd.to_datetime(row.get("list_date"), format="%Y%m%d", errors="coerce")
            if pd.isna(list_ts):
                continue
            delist_ts = pd.to_datetime(row.get("delist_date"), format="%Y%m%d", errors="coerce")
            found.append(
                ContractInfo(
                    ts_code=str(row.get("ts_code") or "").upper(),
                    symbol=str(row.get("symbol") or "").upper(),
                    exchange=str(row.get("exchange") or ex).upper(),
                    list_date=list_ts.date(),
                    delist_date=None if pd.isna(delist_ts) else delist_ts.date(),
                )
            )
        # A product belongs to one exchange.  Stop once found.
        if found:
            break
    found = [x for x in found if x.ts_code and x.symbol]
    return sorted({x.ts_code: x for x in found}.values(), key=lambda x: (x.list_date, x.ts_code))


def _parse_day(value: object) -> date | None:
    ts = pd.to_datetime(value, errors="coerce")
    return None if pd.isna(ts) else ts.date()


def _normalize_daily(raw: pd.DataFrame) -> tuple[pd.DataFrame, list[date]]:
    if raw.empty:
        return pd.DataFrame(columns=_STANDARD_COLUMNS), []
    d = raw.copy()
    dt = pd.to_datetime(d["trade_date"].astype(str), format="%Y%m%d", errors="coerce")
    out = pd.DataFrame({
        "datetime": dt,
        "open": pd.to_numeric(d.get("open"), errors="coerce"),
        "high": pd.to_numeric(d.get("high"), errors="coerce"),
        "low": pd.to_numeric(d.get("low"), errors="coerce"),
        "close": pd.to_numeric(d.get("close"), errors="coerce"),
        "volume": pd.to_numeric(d.get("vol"), errors="coerce").fillna(0.0),
        # Tushare fut_daily amount is documented in 10k CNY; normalize to CNY.
        "amount": pd.to_numeric(d.get("amount"), errors="coerce").fillna(0.0) * 10000.0,
        "open_interest": pd.to_numeric(d.get("oi"), errors="coerce").fillna(0.0),
        "trading_day": dt.dt.strftime("%Y-%m-%d"),
    })
    out = out.dropna(subset=["datetime", "open", "high", "low", "close"])
    out = out.sort_values("datetime").drop_duplicates("trading_day", keep="last").reset_index(drop=True)
    days = [x.date() for x in pd.to_datetime(out["datetime"], errors="coerce").dropna()]
    return out[_STANDARD_COLUMNS], sorted(set(days))


def _map_trading_day(ts: pd.Timestamp, trading_days: list[date]) -> str:
    return str(session_trading_days(pd.Series([ts]), trading_days).iloc[0])


def _normalize_minutes(raw: pd.DataFrame, trading_days: list[date]) -> pd.DataFrame:
    if raw.empty:
        return pd.DataFrame(columns=_STANDARD_COLUMNS)
    d = raw.copy()
    dt = pd.to_datetime(d["trade_time"], errors="coerce")
    out = pd.DataFrame({
        "datetime": dt,
        "open": pd.to_numeric(d.get("open"), errors="coerce"),
        "high": pd.to_numeric(d.get("high"), errors="coerce"),
        "low": pd.to_numeric(d.get("low"), errors="coerce"),
        "close": pd.to_numeric(d.get("close"), errors="coerce"),
        "volume": pd.to_numeric(d.get("vol"), errors="coerce").fillna(0.0),
        "amount": pd.to_numeric(d.get("amount"), errors="coerce").fillna(0.0),
        "open_interest": pd.to_numeric(d.get("oi"), errors="coerce").fillna(0.0),
    })
    out = out.dropna(subset=["datetime", "open", "high", "low", "close"])
    out["trading_day"] = session_trading_days(out["datetime"], trading_days)
    out = out.sort_values("datetime").drop_duplicates("datetime", keep="last").reset_index(drop=True)
    return out[_STANDARD_COLUMNS]


def fetch_contract_daily(token: str, contract: ContractInfo) -> tuple[pd.DataFrame, list[date]]:
    end = min(contract.delist_date or date.today(), date.today())
    fields = "ts_code,trade_date,open,high,low,close,vol,amount,oi"
    raw = _api_call(
        "fut_daily",
        token,
        {
            "ts_code": contract.ts_code,
            "start_date": contract.list_date.strftime("%Y%m%d"),
            "end_date": end.strftime("%Y%m%d"),
        },
        fields,
    )
    return _normalize_daily(raw)


def fetch_contract_minutes(
    token: str,
    contract: ContractInfo,
    freq: str,
    trading_days: list[date],
    progress_cb: Callable[[str], None] | None = None,
    cancel_cb: Callable[[], bool] | None = None,
) -> pd.DataFrame:
    if freq not in _CHUNK_DAYS:
        raise ValueError(freq)
    # Tushare documents more than ten years of futures minute history.  Use
    # 2010-01-01 as the practical lower bound advertised by its permission page.
    start = max(contract.list_date, date(2010, 1, 1))
    end = min(contract.delist_date or date.today(), date.today())
    if end < start:
        return pd.DataFrame(columns=_STANDARD_COLUMNS)

    pieces: list[pd.DataFrame] = []
    cur = start
    chunk_days = _CHUNK_DAYS[freq]
    while cur <= end:
        if cancel_cb and cancel_cb():
            raise HistoryUpdateCancelled("用户取消历史数据重建。")
        right = min(cur + timedelta(days=chunk_days - 1), end)
        if progress_cb:
            progress_cb(f"{contract.symbol} {freq} {cur} ~ {right}")
        raw = _api_call(
            "ft_mins",
            token,
            {
                "ts_code": contract.ts_code,
                "freq": freq,
                "start_date": f"{cur:%Y-%m-%d} 00:00:00",
                "end_date": f"{right:%Y-%m-%d} 23:59:59",
            },
            "ts_code,trade_time,open,close,high,low,vol,amount,oi",
        )
        if not raw.empty:
            pieces.append(raw)
        cur = right + timedelta(days=1)
        # ft_mins permission currently allows a high request rate, but a small
        # pause is friendlier to the service and prevents accidental bursts.
        time.sleep(0.08)

    if not pieces:
        return pd.DataFrame(columns=_STANDARD_COLUMNS)
    raw_all = pd.concat(pieces, ignore_index=True, sort=False)
    return _normalize_minutes(raw_all, trading_days)


def _atomic_write_parquet(frame: pd.DataFrame, path: Path) -> None:
    if frame is None or frame.empty:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".rebuild.tmp")
    frame[_STANDARD_COLUMNS].to_parquet(temp, index=False)
    # Read the temp once before replacement so a broken write cannot destroy the
    # existing history file.
    check = pd.read_parquet(temp, columns=["datetime", "open", "high", "low", "close"])
    if check.empty:
        temp.unlink(missing_ok=True)
        raise HistoryUpdateError(f"重建文件校验失败：{path.name}")
    os.replace(temp, path)


def rebuild_instrument_history(
    repo,
    symbol: str,
    token: str,
    exchange_text: str = "",
    progress_cb: Callable[[str], None] | None = None,
    cancel_cb: Callable[[], bool] | None = None,
) -> InstrumentRebuildStats:
    """Rebuild all available raw-contract history for one product."""
    contracts = list_product_contracts(token, symbol, exchange_text)
    if not contracts:
        raise HistoryUpdateError(f"Tushare 未找到 {symbol} 的合约列表。")

    stats = InstrumentRebuildStats(symbol=symbol, contracts=len(contracts))
    for idx, contract in enumerate(contracts, start=1):
        if cancel_cb and cancel_cb():
            raise HistoryUpdateCancelled("用户取消历史数据重建。")
        if progress_cb:
            progress_cb(f"{symbol}: 合约 {contract.symbol}（{idx}/{len(contracts)}）日线")
        try:
            daily, trading_days = fetch_contract_daily(token, contract)
            frames: dict[str, pd.DataFrame] = {"1d": daily}
            for freq in ("1min", "5min", "15min", "30min", "60min"):
                frames[_LOCAL_PERIOD[freq]] = fetch_contract_minutes(
                    token, contract, freq, trading_days, progress_cb, cancel_cb
                )
            frames["4h"] = synthesize_4h_from_smaller(frames.get("1h"), frames.get("1min"))

            for period in ("1min", "5min", "15min", "30min", "1h", "4h", "1d"):
                frame = frames.get(period)
                if frame is None or frame.empty:
                    continue
                path = repo._resolve_file("原始行情", symbol, period, contract.symbol)
                _atomic_write_parquet(frame, path)
                stats.files_written += 1
                stats.rows_written += len(frame)
        except HistoryUpdateCancelled:
            raise
        except Exception as exc:
            stats.failures += 1
            stats.errors.append(f"{contract.symbol}: {exc}")
            # Continue with later contracts; already-completed parquet files are
            # valid and atomic, and the caller will report failure count.
            continue

    repo.clear_cache()
    return stats
