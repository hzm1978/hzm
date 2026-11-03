from __future__ import annotations

"""Small atomic live-data shards, merged with original history only on read.

Background collection never rewrites a product's entire historical parquet
each minute. Credentials are not part of this storage format.
"""

from pathlib import Path
from tempfile import NamedTemporaryFile
import hashlib

import pandas as pd


def period_folder(root, source, symbol, contract, period):
    period = "1d" if period in {"1w", "1M"} else period
    return Path(root)/".futures_realtime"/source/symbol/contract/period


def overlay_contracts(root, source, symbol, period):
    base = Path(root)/".futures_realtime"/source/symbol
    if not base.exists():
        return []
    return [p.name for p in base.iterdir() if p.is_dir()
            and any(period_folder(root, source, symbol, p.name, period).glob("*.parquet"))]


def read_live_overlay(root, source, symbol, contract, period):
    files = sorted(period_folder(root, source, symbol, contract, period).glob("*.parquet"))
    if not files:
        return pd.DataFrame()
    return pd.concat([pd.read_parquet(path) for path in files], ignore_index=True)


def atomic_parquet(path, frame):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(prefix=path.stem+"-", suffix=".tmp", dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
    try:
        frame.to_parquet(temporary, index=False)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def write_live_snapshot(target, snapshot, fingerprints=None, *, first_minute_id=0):
    from .realtime_data import completed_frame, LIVE_PERIODS
    from .edb_update import synthesize_intraday_periods
    fingerprints = fingerprints if fingerprints is not None else {}
    minutes, daily = (completed_frame(snapshot, key) for key in ("1min", "1d"))
    frames = {"1min": minutes, "1d": daily}
    # The first SDK window may start in the middle of a session. Keep its
    # native minutes, but leave that day's older derived templates untouched.
    derivable = minutes
    if first_minute_id > 0 and not minutes.empty:
        derivable = minutes.loc[minutes.trading_day.ne(minutes.trading_day.iloc[0])]
    for period, frame in synthesize_intraday_periods(derivable).items():
        closed = frame.datetime.add(pd.Timedelta(seconds=LIVE_PERIODS[period])).le(snapshot.received_at)
        closed |= frame.datetime.shift(-1).le(snapshot.received_at)
        frames[period] = frame.loc[closed].copy()
    changed = 0
    for period, frame in frames.items():
        if frame.empty:
            continue
        shard_keys = frame.trading_day.str[:4] if period == "1d" else frame.trading_day
        for shard, rows in frame.groupby(shard_keys, sort=False):
            rows = rows.reset_index(drop=True)
            path = period_folder(target.root, target.source, target.symbol, target.contract, period)/(str(shard)+".parquet")
            digest = hashlib.sha256(pd.util.hash_pandas_object(rows, index=False).to_numpy().tobytes()).hexdigest()
            if fingerprints.get(str(path)) == digest:
                continue
            if path.exists():
                previous = pd.read_parquet(path)
                # Preserve the earlier part of a day/year when the SDK window
                # rolls. The key can be corrected by a later received snapshot.
                key = "trading_day" if period == "1d" else "datetime"
                rows = pd.concat([previous, rows], ignore_index=True).sort_values("datetime", kind="stable").drop_duplicates(key, keep="last")
                rows = rows.reset_index(drop=True)
            atomic_parquet(path, rows)
            fingerprints[str(path)] = digest
            changed += 1
    return {"changed": changed, "minute_latest": str(minutes.datetime.max()) if not minutes.empty else None,
            "daily_latest": str(daily.datetime.max()) if not daily.empty else None}
