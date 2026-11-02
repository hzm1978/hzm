from __future__ import annotations

import re
from collections import OrderedDict
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

from .config import CACHE_ITEMS, SOURCE_DIRS
from .models import InstrumentMeta
from .trading_calendar import exchange_calendar, parse_trading_days, session_trading_days

_REQUIRED = ["open", "high", "low", "close"]
_COL_ALIASES = {
    "datetime": ["datetime", "date_time", "timestamp", "time", "dt"],
    "open": ["open", "o", "开盘", "开盘价"],
    "high": ["high", "h", "最高", "最高价"],
    "low": ["low", "l", "最低", "最低价"],
    "close": ["close", "c", "收盘", "收盘价"],
    "volume": ["volume", "vol", "成交量"],
    "amount": ["amount", "turnover", "成交额"],
    "open_interest": ["open_interest", "oi", "持仓量"],
    "trading_day": ["trading_day", "trade_date", "交易日"],
}


class DataRepository:
    def __init__(self, root: Path):
        self.root = Path(root)
        self._cache: OrderedDict[str, pd.DataFrame] = OrderedDict()
        self._meta_cache: dict[str, InstrumentMeta] = {}
        # Cached dominant-contract rollover dates derived from raw daily data.
        self._main_switch_cache: dict[str, list[dict[str, str]]] = {}

    def source_path(self, source_name: str) -> Path:
        dirname = SOURCE_DIRS.get(source_name, source_name)
        canonical = self.root / dirname
        if canonical.exists():
            return canonical
        # Sample/development layout: root may itself be EC/, or may directly
        # contain EC/, RB/, ... without the qihushuju_parquet wrapper.
        if source_name == "原始行情":
            return self.root
        return canonical

    @staticmethod
    def _looks_like_instrument_dir(path: Path) -> bool:
        return path.is_dir() and (path / "1min").is_dir() and ((path / "数据说明.md").exists() or (path / "1d").is_dir())

    def _instrument_dir(self, symbol: str, source_name: str) -> Path:
        base = self.source_path(source_name)
        if source_name == "原始行情" and self._looks_like_instrument_dir(base) and base.name == symbol:
            return base
        return base / symbol

    def list_instruments(self, source_name: str = "原始行情") -> list[InstrumentMeta]:
        base = self.source_path(source_name)
        if not base.exists():
            return []
        if source_name == "原始行情" and self._looks_like_instrument_dir(base):
            dirs = [base]
        else:
            dirs = [x for x in base.iterdir() if self._looks_like_instrument_dir(x) or source_name != "原始行情" and x.is_dir()]
        return [self.read_metadata(p.name, source_name) for p in sorted(dirs, key=lambda x: x.name)]

    def read_metadata(self, symbol: str, source_name: str = "原始行情") -> InstrumentMeta:
        key = f"{source_name}:{symbol}"
        if key in self._meta_cache:
            return self._meta_cache[key]
        base = self._instrument_dir(symbol, source_name)
        name = ""
        exchange = ""
        date_range = ""
        md = base / "数据说明.md"
        if md.exists():
            try:
                text = md.read_text(encoding="utf-8", errors="ignore")
                for line in text.splitlines():
                    raw_line = line.strip()
                    if raw_line.startswith("#") and not name:
                        title = raw_line.lstrip("# ").strip()
                        mtitle = re.match(r"^([A-Za-z]+\d*)\s+(.+?)(?:\s+[—–-]\s+.*)?$", title)
                        if mtitle and mtitle.group(1).upper() == symbol.upper():
                            name = mtitle.group(2).strip()
                    clean = raw_line.lstrip("#*- ")
                    if "交易所" in clean:
                        exchange = clean.split("：", 1)[-1].split(":", 1)[-1].strip()
                    elif "数据起止" in clean or "数据范围" in clean:
                        date_range = clean.split("：", 1)[-1].split(":", 1)[-1].strip()
                    elif any(k in clean for k in ["品种名称", "中文名", "名称"]):
                        cand = clean.split("：", 1)[-1].split(":", 1)[-1].strip()
                        if cand and cand != clean:
                            name = cand
            except OSError:
                pass
        if not name:
            # Some data trees use directory names like EC_集运欧线.
            m = re.match(r"^[A-Za-z]+[_\- ](.+)$", symbol)
            if m:
                name = m.group(1)
        # Continuous-series folders do not always repeat the product metadata.
        # Fall back to raw-contract metadata so the continuous watch list still
        # shows the Chinese name and exchange correctly.
        if source_name != "原始行情" and (not name or not exchange or not date_range):
            try:
                raw_meta = self.read_metadata(symbol, "原始行情")
                name = name or raw_meta.name
                exchange = exchange or raw_meta.exchange
                date_range = date_range or raw_meta.date_range
            except Exception:
                pass
        meta = InstrumentMeta(symbol=symbol, name=name, exchange=exchange, date_range=date_range)
        self._meta_cache[key] = meta
        return meta

    def list_contracts(self, symbol: str, period: str, source_name: str = "原始行情") -> list[str]:
        base = self._instrument_dir(symbol, source_name)
        if source_name == "原始行情":
            folder = base / period
            if not folder.exists() and period in {"1w", "1M"}:
                folder = base / "1d"
            if not folder.exists():
                return []
            return sorted(p.stem for p in folder.glob("*.parquet"))
        # Continuous data commonly: symbol/sequence/period.parquet
        seqs: list[str] = []
        if base.exists():
            for d in base.iterdir():
                if d.is_dir() and ((d / f"{period}.parquet").exists() or (period in {"1w", "1M"} and (d / "1d.parquet").exists())):
                    seqs.append(d.name)
        return sorted(seqs)


    def clear_cache(self) -> None:
        """Drop cached DataFrames after files are updated on disk."""
        self._cache.clear()
        self._main_switch_cache.clear()

    def main_contract_switches(self, symbol: str) -> list[dict[str, str]]:
        """Return dominant-contract switch dates inferred from raw daily bars.

        Daily *native* rows are used here intentionally.  This function is called
        during chart refresh and must not trigger a full 1-minute reconstruction
        for every historical contract merely to draw rollover separators.
        """
        symbol = str(symbol)
        cache_key = symbol.upper()
        cached = self._main_switch_cache.get(cache_key)
        if cached is not None:
            return [dict(x) for x in cached]

        contracts = self.list_contracts(symbol, "1d", "原始行情")
        candidates: list[pd.DataFrame] = []
        for contract in contracts:
            try:
                df = self._read_native_frame("原始行情", symbol, "1d", contract)
            except Exception:
                continue
            if df.empty:
                continue
            day = pd.to_datetime(df.get("trading_day"), errors="coerce").dt.normalize()
            oi = pd.to_numeric(df.get("open_interest", 0.0), errors="coerce")
            vol = pd.to_numeric(df.get("volume", 0.0), errors="coerce")
            part = pd.DataFrame({
                "day": day,
                "contract": str(contract),
                "oi": oi,
                "volume": vol,
            }).dropna(subset=["day"])
            if not part.empty:
                candidates.append(part)

        if not candidates:
            self._main_switch_cache[cache_key] = []
            return []

        all_rows = pd.concat(candidates, ignore_index=True)
        all_rows["oi_score"] = pd.to_numeric(all_rows["oi"], errors="coerce").fillna(-1.0)
        all_rows["volume_score"] = pd.to_numeric(all_rows["volume"], errors="coerce").fillna(-1.0)

        chosen: list[tuple[pd.Timestamp, str]] = []
        for day, group in all_rows.groupby("day", sort=True):
            positive_oi = group[group["oi_score"] > 0]
            pool = positive_oi if not positive_oi.empty else group
            best = pool.sort_values(
                ["oi_score", "volume_score", "contract"],
                ascending=[False, False, True],
                kind="mergesort",
            ).iloc[0]
            chosen.append((pd.Timestamp(day), str(best["contract"])))

        switches: list[dict[str, str]] = []
        previous: str | None = None
        for day, contract in chosen:
            if previous is None:
                previous = contract
                continue
            if contract != previous:
                switches.append({
                    "trading_day": day.strftime("%Y-%m-%d"),
                    "from": previous,
                    "to": contract,
                })
                previous = contract

        self._main_switch_cache[cache_key] = [dict(x) for x in switches]
        return switches

    def append_bars(
        self, source_name: str, symbol: str, period: str, contract: str, incoming: pd.DataFrame
    ) -> tuple[int, int, pd.Timestamp | None]:
        """Merge online bars into one local parquet file.

        Returns ``(added_rows, total_rows, last_timestamp)``.  Overlapping
        timestamps/trading days are replaced by the incoming row so a current
        incomplete bar can be refreshed safely.
        """
        if period in {"1w", "1M"}:
            raise ValueError("周线和月线由日线运行时聚合，不直接写入文件")
        path = self._resolve_file(source_name, symbol, period, contract)
        path.parent.mkdir(parents=True, exist_ok=True)

        new_df = self.standardize(incoming) if not incoming.empty else incoming.copy()
        if path.exists():
            old_df = self.standardize(pd.read_parquet(path))
        else:
            old_df = pd.DataFrame(columns=[
                "datetime", "open", "high", "low", "close",
                "volume", "amount", "open_interest", "trading_day",
            ])

        if period == "1d":
            new_df = self._clean_daily_frame(new_df)

        if new_df.empty:
            last = old_df["datetime"].max() if not old_df.empty else None
            return 0, len(old_df), pd.Timestamp(last) if last is not None and pd.notna(last) else None

        if period == "1d":
            old_df = self._clean_daily_frame(old_df)

        key = "trading_day" if period == "1d" else "datetime"
        old_keys = set(old_df[key].astype(str)) if key in old_df.columns else set()
        new_keys = set(new_df[key].astype(str))
        added = len(new_keys - old_keys)

        merged = pd.concat([old_df, new_df], ignore_index=True, sort=False)
        merged = self.standardize(merged)
        if period == "1d":
            merged = merged.sort_values(["trading_day", "datetime"]).drop_duplicates("trading_day", keep="last")
        else:
            merged = merged.sort_values("datetime").drop_duplicates("datetime", keep="last")
        merged = merged.reset_index(drop=True)

        keep = [
            "datetime", "open", "high", "low", "close",
            "volume", "amount", "open_interest", "trading_day",
        ]
        for col in keep:
            if col not in merged.columns:
                merged[col] = 0.0 if col not in {"datetime", "trading_day"} else ""
        merged[keep].to_parquet(path, index=False)
        self.clear_cache()
        last = merged["datetime"].max() if not merged.empty else None
        return added, len(merged), pd.Timestamp(last) if last is not None and pd.notna(last) else None

    def _cache_get(self, key: str) -> pd.DataFrame | None:
        df = self._cache.get(key)
        if df is not None:
            self._cache.move_to_end(key)
        return df

    def _cache_put(self, key: str, df: pd.DataFrame) -> None:
        self._cache[key] = df
        self._cache.move_to_end(key)
        while len(self._cache) > CACHE_ITEMS:
            self._cache.popitem(last=False)

    def _resolve_file(self, source_name: str, symbol: str, period: str, contract: str) -> Path:
        base = self._instrument_dir(symbol, source_name)
        if source_name == "原始行情":
            actual_period = "1d" if period in {"1w", "1M"} else period
            return base / actual_period / f"{contract}.parquet"
        actual_period = "1d" if period in {"1w", "1M"} else period
        return base / contract / f"{actual_period}.parquet"

    def _read_native_frame(self, source_name: str, symbol: str, period: str, contract: str) -> pd.DataFrame:
        """Read one parquet file without invoking any runtime reconstruction.

        This is deliberately separate from :meth:`load_bars`: higher-timeframe
        parquet files are treated as boundary/reference data, while the displayed
        OHLC is rebuilt from real 1-minute bars whenever those minutes exist.
        """
        path = self._resolve_file(source_name, symbol, period, contract)
        if not path.exists():
            raise FileNotFoundError(path)
        frame = self.standardize(pd.read_parquet(path))
        if period in {"1d", "1w", "1M"}:
            frame = self._clean_daily_frame(frame)
        return frame

    @staticmethod
    def _clean_daily_frame(frame: pd.DataFrame) -> pd.DataFrame:
        """Exclude legacy weekend daily rows and label valid rows by trading day.

        Do not relabel a bad daily row's OHLC as Monday. Real night-session
        prices are recovered from the minute data with the repaired calendar.
        Loading never rewrites the underlying user's parquet files.
        """
        if frame.empty:
            return frame.copy()
        days = parse_trading_days(frame["trading_day"])
        valid = days.notna() & (days.dt.day_of_week < 5)
        out = frame.loc[valid].copy()
        out["datetime"] = days.loc[valid]
        out["trading_day"] = days.loc[valid].dt.strftime("%Y-%m-%d")
        return out.sort_values("datetime").drop_duplicates("trading_day", keep="last").reset_index(drop=True)

    @staticmethod
    def _trading_day_repair_mask(minutes: pd.DataFrame) -> np.ndarray:
        """Return a vectorized mask for rows whose trading day needs repair.

        A row is a candidate when its trading_day was inferred, or when a night
        session row (18:00-23:59 / 00:00-05:59) is incorrectly labelled with
        its natural calendar date, or when any row has a weekend trading label.
        """
        if minutes.empty:
            return np.zeros(0, dtype=bool)

        inferred_col = minutes.get("_trading_day_inferred")
        if inferred_col is None:
            inferred = np.zeros(len(minutes), dtype=bool)
        else:
            inferred = pd.Series(inferred_col, index=minutes.index).fillna(False).to_numpy(dtype=bool)

        if "trading_day" not in minutes.columns or "datetime" not in minutes.columns:
            return inferred

        dt = minutes["datetime"]
        # `standardize` already converts datetime to datetime64[ns].
        parsed_td = DataRepository._parse_trading_day_values(minutes["trading_day"])
        natural_day = dt.dt.normalize()
        hour = dt.dt.hour.to_numpy(dtype=np.int16, copy=False)
        night = (hour >= 18) | (hour < 6)
        same_natural_day = (
            parsed_td.notna().to_numpy()
            & (parsed_td.dt.normalize().to_numpy(dtype="datetime64[ns]") ==
               natural_day.to_numpy(dtype="datetime64[ns]"))
        )
        weekend_label = parsed_td.dt.day_of_week.to_numpy() >= 5
        return inferred | (night & same_natural_day) | weekend_label

    @staticmethod
    def _repair_inferred_trading_day(
        minutes: pd.DataFrame,
        daily_template: pd.DataFrame | None,
        candidate_mask: np.ndarray | None = None,
    ) -> pd.DataFrame:
        """Vectorized repair of inferred or obviously mislabelled night-session days.

        Evening bars map to the first daily trading date strictly after their
        calendar date. After-midnight bars map to the first trading date strictly
        after the PREVIOUS calendar date, so Friday 21:00 and Saturday 00:30
        both map to Monday when Monday is the next exchange session. Day-session
        inferred rows map to the first available trading date on/after their
        calendar date. Missing/unpublished daily dates fall back to weekdays,
        so an incomplete calendar never leaves Saturday as a trading-day label.

        The old implementation iterated row-by-row with DataFrame.at, which made
        loading a full year of minute data block the Qt UI. This implementation
        uses NumPy searchsorted in vectorized batches.
        """
        if minutes.empty:
            return minutes

        if candidate_mask is None:
            candidate_mask = DataRepository._trading_day_repair_mask(minutes)
        candidate_mask = np.asarray(candidate_mask, dtype=bool)
        if candidate_mask.size != len(minutes):
            raise ValueError("candidate_mask length does not match minute frame")
        candidate_rows = np.flatnonzero(candidate_mask)
        if candidate_rows.size == 0:
            minutes.attrs["trading_day_repaired_count"] = 0
            minutes.attrs["trading_day_unrepaired_count"] = 0
            return minutes

        calendar_values = (
            daily_template["trading_day"]
            if daily_template is not None and "trading_day" in daily_template.columns
            else ()
        )
        calendar = exchange_calendar(calendar_values)

        dt = minutes["datetime"]
        hours = dt.dt.hour.to_numpy(dtype=np.int16, copy=False)
        natural_days = dt.dt.normalize().to_numpy(dtype="datetime64[ns]")
        inferred_col = minutes.get("_trading_day_inferred")
        if inferred_col is None:
            inferred_all = np.zeros(len(minutes), dtype=bool)
        else:
            inferred_all = pd.Series(inferred_col, index=minutes.index).fillna(False).to_numpy(dtype=bool)

        # An after-midnight row whose trading_day equals its natural date is
        # often already correct when that date is itself an exchange trading day
        # (e.g. Monday 21:00 -> Tuesday 00:15 belongs to Tuesday). Only repair
        # that explicit label when the natural date is absent from the daily
        # trading calendar, as happens on Saturday/Sunday or an exchange holiday.
        candidate_natural = natural_days[candidate_rows]
        candidate_hours = hours[candidate_rows]
        candidate_inferred = inferred_all[candidate_rows]
        candidate_td = DataRepository._parse_trading_day_values(
            minutes.iloc[candidate_rows]["trading_day"]
        ).dt.normalize().to_numpy(dtype="datetime64[ns]")
        natural_is_trading_day = np.isin(
            candidate_natural, calendar.to_numpy(dtype="datetime64[ns]")
        )
        # Beyond the known calendar, an explicit weekday midnight label is
        # already valid. Missing daily records must not flag it as unrepaired.
        weekdays = dt.dt.day_of_week.to_numpy()[candidate_rows] < 5
        if len(calendar):
            last_known_day = calendar.to_numpy(dtype="datetime64[ns]")[-1]
            natural_is_trading_day |= weekdays & (candidate_natural > last_known_day)
        else:
            natural_is_trading_day = weekdays
        explicit_early_already_correct = (
            (candidate_hours < 6)
            & (~candidate_inferred)
            & (~np.isnat(candidate_td))
            & (candidate_td == candidate_natural)
            & natural_is_trading_day
        )
        effective_rows = candidate_rows[~explicit_early_already_correct]

        valid_dt = ~np.isnat(natural_days[effective_rows])
        rows = effective_rows[valid_dt]
        invalid_count = int(effective_rows.size - rows.size)

        if rows.size == 0:
            minutes.attrs["trading_day_repaired_count"] = 0
            minutes.attrs["trading_day_unrepaired_count"] = invalid_count
            return minutes

        labels = session_trading_days(dt.iloc[rows], calendar)
        out = minutes.copy()
        out["trading_day"] = out["trading_day"].astype(object)
        col = out.columns.get_loc("trading_day")
        out.iloc[rows, col] = labels.to_numpy()

        out.attrs.update(minutes.attrs)
        out.attrs["trading_day_repaired_count"] = int(rows.size)
        out.attrs["trading_day_unrepaired_count"] = invalid_count
        return out

    @staticmethod
    def _aggregate_part(part: pd.DataFrame, *, label: pd.Timestamp, trading_day: str) -> dict[str, object]:
        part = part.sort_values("datetime")
        oi = pd.to_numeric(part.get("open_interest", pd.Series(dtype=float)), errors="coerce").dropna()
        volume = pd.to_numeric(part.get("volume", pd.Series(0.0, index=part.index)), errors="coerce").fillna(0.0)
        amount = pd.to_numeric(part.get("amount", pd.Series(0.0, index=part.index)), errors="coerce").fillna(0.0)
        return {
            "datetime": pd.Timestamp(label),
            "open": float(part.iloc[0]["open"]),
            "high": float(pd.to_numeric(part["high"], errors="coerce").max()),
            "low": float(pd.to_numeric(part["low"], errors="coerce").min()),
            "close": float(part.iloc[-1]["close"]),
            "volume": float(volume.sum()),
            "amount": float(amount.sum()),
            "open_interest": float(oi.iloc[-1]) if not oi.empty else 0.0,
            "trading_day": str(trading_day),
            "_trading_day_inferred": False,
        }

    @classmethod
    def _aggregate_daily_minutes(cls, minutes: pd.DataFrame) -> pd.DataFrame:
        if minutes.empty:
            return pd.DataFrame(columns=[
                "datetime", "open", "high", "low", "close", "volume", "amount",
                "open_interest", "trading_day", "_trading_day_inferred",
            ])
        x = minutes.sort_values("datetime").copy()
        grouped = x.groupby("trading_day", sort=True, dropna=False).agg(
            open=("open", "first"),
            high=("high", "max"),
            low=("low", "min"),
            close=("close", "last"),
            volume=("volume", "sum"),
            amount=("amount", "sum"),
            open_interest=("open_interest", "last"),
            first_datetime=("datetime", "first"),
        ).reset_index()
        labels = pd.Series(pd.to_datetime(grouped["trading_day"], errors="coerce").dt.normalize(), index=grouped.index).copy()
        fallback_labels = pd.to_datetime(grouped["first_datetime"], errors="coerce").dt.normalize()
        labels = labels.where(labels.notna(), fallback_labels)
        grouped.insert(0, "datetime", labels)
        grouped["_trading_day_inferred"] = False
        grouped = grouped.drop(columns=["first_datetime"])
        return grouped.sort_values("datetime").reset_index(drop=True)

    @staticmethod
    def _merge_daily_prefer_native(native_daily: pd.DataFrame, minute_daily: pd.DataFrame) -> pd.DataFrame:
        """Use the downloaded native daily series as the source of truth.

        Minute-derived daily bars fill only dates missing from the native daily
        file.  This is important for EDB updates: daily, weekly and monthly views
        must be based on EDB's native daily records rather than silently replacing
        them with a reconstruction from minute bars.
        """
        native_daily = DataRepository._clean_daily_frame(native_daily)
        minute_daily = DataRepository._clean_daily_frame(minute_daily)
        if native_daily.empty:
            out = minute_daily.copy()
            out.attrs["minute_fallback_bars"] = 0
            return out
        if minute_daily.empty:
            out = native_daily.copy()
            out.attrs["minute_fallback_bars"] = 0
            return out
        native = native_daily.copy()
        rebuilt = minute_daily.copy()
        native_days = set(native["trading_day"].astype(str))
        fallback = rebuilt.loc[~rebuilt["trading_day"].astype(str).isin(native_days)]
        out = pd.concat([native, fallback], ignore_index=True, sort=False)
        out = out.sort_values(["trading_day", "datetime"]).drop_duplicates("trading_day", keep="last").reset_index(drop=True)
        out.attrs["minute_fallback_bars"] = len(fallback)
        return out

    @classmethod
    def _aggregate_daily_period(cls, daily: pd.DataFrame, period: str) -> pd.DataFrame:
        """Aggregate weeks/months by *trading_day*, never by wall-clock datetime."""
        if daily.empty:
            return daily.copy()
        x = daily.copy()
        td = pd.to_datetime(x["trading_day"], errors="coerce")
        x = x.loc[td.notna()].copy()
        td = td.loc[td.notna()]
        if x.empty:
            return x
        if period == "1w":
            keys = td.dt.to_period("W-FRI")
        elif period == "1M":
            keys = td.dt.to_period("M")
        else:
            raise ValueError(period)
        x["_period_key"] = keys.astype(str).to_numpy()
        rows: list[dict[str, object]] = []
        for key, part in x.groupby("_period_key", sort=True):
            part = part.sort_values("trading_day")
            last_td = str(part.iloc[-1]["trading_day"])
            p = pd.Period(key, freq="W-FRI" if period == "1w" else "M")
            label = p.end_time.normalize()
            rows.append(cls._aggregate_part(part, label=label, trading_day=last_td))
        return pd.DataFrame(rows).sort_values("datetime").reset_index(drop=True)

    @staticmethod
    def _detect_template_label_mode(day_minutes: pd.DataFrame, day_template: pd.DataFrame) -> str:
        """Return ``start`` or ``end`` for native intraday bar timestamps.

        Most project files use start labels, but some vendors save bar-end labels.
        Detecting the convention from the first real minute prevents an otherwise
        systematic one-bar shift for only some instruments.
        """
        if day_minutes.empty or day_template.empty:
            return "start"
        first_min = pd.Timestamp(day_minutes["datetime"].min())
        first_bar = pd.Timestamp(day_template["datetime"].min())
        # Equal/near-equal timestamps are start-labelled.  A template timestamp
        # several minutes after the first real minute is almost certainly an end label.
        return "start" if abs((first_bar - first_min).total_seconds()) <= 90 else ("end" if first_bar > first_min else "start")

    @classmethod
    def _rebuild_intraday_from_template(cls, minutes: pd.DataFrame, template: pd.DataFrame) -> pd.DataFrame:
        """Use native bars only as boundaries; calculate OHLCV from real minutes.

        Bar assignment is vectorized across the full history.  This keeps first
        instrument loads fast while still respecting each trading day's native
        boundary layout (including exchange breaks and 4-hour custom boundaries).
        """
        if template.empty or minutes.empty:
            out = template.copy()
            out.attrs["minute_rebuilt_bars"] = 0
            out.attrs["minute_fallback_bars"] = len(out)
            return out

        m = minutes.sort_values("datetime").copy()
        t = template.sort_values("datetime").copy()
        m["trading_day"] = m["trading_day"].astype(str)
        t["trading_day"] = t["trading_day"].astype(str)

        # Some vendor higher-timeframe files keep the night-session *calendar*
        # date in trading_day even though the minute file correctly belongs to
        # the next exchange trading day.  Align each stored bar boundary to the
        # nearest real minute first, then inherit that minute's trading_day.
        # The tolerance only identifies the boundary label; it never changes OHLC.
        minute_keys = (
            m[["datetime", "trading_day"]]
            .drop_duplicates("datetime", keep="last")
            .sort_values("datetime")
            .rename(columns={"trading_day": "_minute_td"})
        )
        try:
            aligned = pd.merge_asof(
                t[["datetime"]].sort_values("datetime"),
                minute_keys,
                on="datetime",
                direction="nearest",
                tolerance=pd.Timedelta(minutes=2),
            )
            if "_minute_td" in aligned.columns:
                mask = aligned["_minute_td"].notna().to_numpy()
                if mask.any():
                    t.loc[mask, "trading_day"] = aligned.loc[mask, "_minute_td"].astype(str).to_numpy()
        except Exception:
            pass

        common_days = set(m["trading_day"]).intersection(set(t["trading_day"]))
        if not common_days:
            out = t.reset_index(drop=True)
            out.attrs["minute_rebuilt_bars"] = 0
            out.attrs["minute_fallback_bars"] = len(out)
            return out

        # Timestamp convention is normally uniform within one vendor file.  Detect
        # it from the first common trading day so partial minute histories do not
        # get compared with an older unmatched template date.
        first_common = min(
            common_days,
            key=lambda day: pd.Timestamp(m.loc[m["trading_day"] == day, "datetime"].min()),
        )
        mode = cls._detect_template_label_mode(
            m.loc[m["trading_day"] == first_common],
            t.loc[t["trading_day"] == first_common],
        )
        direction = "backward" if mode == "start" else "forward"

        left = m.loc[m["trading_day"].isin(common_days)].sort_values("datetime")
        right = (
            t.loc[t["trading_day"].isin(common_days), ["trading_day", "datetime"]]
            .rename(columns={"datetime": "_bar_time"})
            .sort_values("_bar_time")
        )
        assigned = pd.merge_asof(
            left, right,
            left_on="datetime", right_on="_bar_time", by="trading_day",
            direction=direction,
        ).dropna(subset=["_bar_time"])

        if assigned.empty:
            out = t.reset_index(drop=True)
            out.attrs["minute_rebuilt_bars"] = 0
            out.attrs["minute_fallback_bars"] = len(out)
            return out

        agg = assigned.groupby(["trading_day", "_bar_time"], sort=True).agg(
            open=("open", "first"),
            high=("high", "max"),
            low=("low", "min"),
            close=("close", "last"),
            volume=("volume", "sum"),
            amount=("amount", "sum"),
            open_interest=("open_interest", "last"),
        )

        ti = t.set_index(["trading_day", "datetime"])
        matched = ti.index.intersection(agg.index)
        cols = ["open", "high", "low", "close", "volume", "amount", "open_interest"]
        # Higher-timeframe parquet files can store integral-looking prices as
        # int columns.  Rebuilt minute OHLC may contain decimals, so cast before
        # assignment to avoid truncation / pandas incompatible-dtype failures.
        for col in cols:
            if col in ti.columns:
                ti[col] = pd.to_numeric(ti[col], errors="coerce").astype(float)
        if len(matched):
            ti.loc[matched, cols] = agg.loc[matched, cols].to_numpy(dtype=float)
        ti["_trading_day_inferred"] = False
        out = ti.reset_index().sort_values("datetime").reset_index(drop=True)
        out.attrs["minute_rebuilt_bars"] = len(matched)
        out.attrs["minute_fallback_bars"] = len(out) - len(matched)
        out.attrs["bar_label_mode"] = mode
        return out

    @staticmethod
    def _count_native_ohlc_mismatches(native: pd.DataFrame, rebuilt: pd.DataFrame, period: str) -> int:
        if native.empty or rebuilt.empty:
            return 0
        try:
            key = "trading_day" if period == "1d" else "datetime"
            left = native.copy()
            right = rebuilt.copy()
            if key == "trading_day":
                left["_key"] = left[key].astype(str)
                right["_key"] = right[key].astype(str)
            else:
                left["_key"] = pd.to_datetime(left[key], errors="coerce")
                right["_key"] = pd.to_datetime(right[key], errors="coerce")
            cols = ["_key", *_REQUIRED]
            left = left[cols].dropna(subset=["_key"]).drop_duplicates("_key", keep="last").set_index("_key")
            right = right[cols].dropna(subset=["_key"]).drop_duplicates("_key", keep="last").set_index("_key")
            common = left.index.intersection(right.index)
            if len(common) == 0:
                return 0
            a = left.loc[common, _REQUIRED].to_numpy(dtype=float)
            b = right.loc[common, _REQUIRED].to_numpy(dtype=float)
            equal = np.isclose(a, b, rtol=1e-10, atol=1e-10, equal_nan=True).all(axis=1)
            return int((~equal).sum())
        except Exception:
            return 0

    def load_bars(self, source_name: str, symbol: str, period: str, contract: str) -> pd.DataFrame:
        key = f"bars:{source_name}:{symbol}:{period}:{contract}"
        cached = self._cache_get(key)
        if cached is not None:
            return cached.copy(deep=False)

        if period == "1min":
            df = self.load_minutes(source_name, symbol, contract)
            self._cache_put(key, df)
            return df.copy(deep=False)

        # Read the vendor/native file first.  For weekly/monthly the resolved file
        # is daily data, which is then aggregated by trading_day below.
        native = self._read_native_frame(source_name, symbol, period, contract)

        try:
            minutes = self.load_minutes(source_name, symbol, contract)
        except Exception:
            minutes = pd.DataFrame()

        if period == "1d":
            minute_daily = self._aggregate_daily_minutes(minutes)
            df = self._merge_daily_prefer_native(native, minute_daily)
            df.attrs["native_ohlc_mismatch_count"] = self._count_native_ohlc_mismatches(native, minute_daily, period)
        elif period in {"1w", "1M"}:
            # native is the daily parquet because _resolve_file maps 1w/1M -> 1d.
            minute_daily = self._aggregate_daily_minutes(minutes)
            daily = self._merge_daily_prefer_native(native, minute_daily)
            fallback_days = int(daily.attrs.get("minute_fallback_bars", 0))
            df = self._aggregate_daily_period(daily, period)
            df.attrs["minute_fallback_days"] = fallback_days
            df.attrs["native_ohlc_mismatch_count"] = self._count_native_ohlc_mismatches(native, minute_daily, "1d")
        else:
            if minutes.empty:
                df = native.copy()
                df.attrs["minute_rebuilt_bars"] = 0
                df.attrs["minute_fallback_bars"] = len(df)
                df.attrs["native_ohlc_mismatch_count"] = 0
            else:
                df = self._rebuild_intraday_from_template(minutes, native)
                df.attrs["native_ohlc_mismatch_count"] = self._count_native_ohlc_mismatches(native, df, period)

        # Preserve minute trading-day diagnostics so the UI can report them.
        if not minutes.empty:
            df.attrs["trading_day_repaired_count"] = int(minutes.attrs.get("trading_day_repaired_count", 0))
            df.attrs["trading_day_unrepaired_count"] = int(minutes.attrs.get("trading_day_unrepaired_count", 0))
        self._cache_put(key, df)
        return df.copy(deep=False)

    def load_minutes(self, source_name: str, symbol: str, contract: str) -> pd.DataFrame:
        key = f"mins:{source_name}:{symbol}:{contract}"
        cached = self._cache_get(key)
        if cached is not None:
            return cached.copy(deep=False)

        path = self._resolve_file(source_name, symbol, "1min", contract)
        if not path.exists():
            raise FileNotFoundError(path)

        df = self.standardize(pd.read_parquet(path))

        # Fast path: most EDB minute files already have valid trading_day values.
        # Do not read the daily parquet at all unless at least one row actually
        # needs inference/repair.
        candidate_mask = self._trading_day_repair_mask(df)
        if candidate_mask.any():
            daily_template = None
            try:
                daily_path = self._resolve_file(source_name, symbol, "1d", contract)
                if daily_path.exists():
                    daily_template = self.standardize(pd.read_parquet(daily_path))
            except Exception:
                daily_template = None
            df = self._repair_inferred_trading_day(
                df, daily_template, candidate_mask=candidate_mask
            )
        else:
            df.attrs["trading_day_repaired_count"] = 0
            df.attrs["trading_day_unrepaired_count"] = 0

        self._cache_put(key, df)
        return df.copy(deep=False)

    @staticmethod
    def _parse_datetime_values(values: pd.Series) -> pd.Series:
        """Parse common vendor datetime encodings without silently treating YYYYMMDD... as epoch ns."""
        raw = pd.Series(values, index=values.index)
        text = raw.astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
        out = pd.Series(pd.NaT, index=raw.index, dtype="datetime64[ns]")

        for digits, fmt in ((14, "%Y%m%d%H%M%S"), (12, "%Y%m%d%H%M"), (8, "%Y%m%d")):
            mask = text.str.fullmatch(rf"\d{{{digits}}}")
            if mask.any():
                out.loc[mask] = pd.to_datetime(text.loc[mask], format=fmt, errors="coerce")

        remain = out.isna() & raw.notna()
        if remain.any():
            numeric = pd.to_numeric(raw.loc[remain], errors="coerce")
            numeric_mask = numeric.notna()
            if numeric_mask.any():
                vals = numeric.loc[numeric_mask].abs()
                median = float(vals.median()) if len(vals) else 0.0
                # Approximate epoch magnitudes in the current era.
                unit = "ns" if median >= 1e17 else "us" if median >= 1e14 else "ms" if median >= 1e11 else "s"
                idx = numeric.loc[numeric_mask].index
                out.loc[idx] = pd.to_datetime(numeric.loc[numeric_mask], unit=unit, errors="coerce")
            non_numeric_idx = numeric.index[~numeric_mask]
            if len(non_numeric_idx):
                out.loc[non_numeric_idx] = pd.to_datetime(raw.loc[non_numeric_idx], errors="coerce")
        return out

    @staticmethod
    def _parse_trading_day_values(values: pd.Series) -> pd.Series:
        """Parse trading-day columns including integer/string YYYYMMDD values."""
        return parse_trading_days(values)

    @staticmethod
    def standardize(raw: pd.DataFrame) -> pd.DataFrame:
        df = raw.copy()
        lower_map = {str(c).lower(): c for c in df.columns}
        rename: dict[object, str] = {}
        for target, aliases in _COL_ALIASES.items():
            for alias in aliases:
                col = lower_map.get(alias.lower())
                if col is not None:
                    rename[col] = target
                    break
        df = df.rename(columns=rename)
        # Datetime may be in index.
        if "datetime" not in df.columns:
            if isinstance(df.index, pd.DatetimeIndex):
                df = df.reset_index().rename(columns={df.index.name or "index": "datetime"})
            else:
                for c in df.columns:
                    if np.issubdtype(df[c].dtype, np.datetime64):
                        df = df.rename(columns={c: "datetime"})
                        break
        if "datetime" not in df.columns:
            raise ValueError("行情文件缺少可识别的时间列")
        df["datetime"] = DataRepository._parse_datetime_values(df["datetime"])
        df = df.dropna(subset=["datetime"]).sort_values("datetime").drop_duplicates("datetime", keep="last")
        for c in _REQUIRED + ["volume", "amount", "open_interest"]:
            if c not in df.columns:
                df[c] = 0.0 if c not in _REQUIRED else np.nan
            df[c] = pd.to_numeric(df[c], errors="coerce")
        df = df.dropna(subset=_REQUIRED)

        existing_inferred = df.get("_trading_day_inferred")
        if "trading_day" not in df.columns:
            df["trading_day"] = df["datetime"].dt.date.astype(str)
            df["_trading_day_inferred"] = True
        else:
            raw_td = df["trading_day"]
            td = DataRepository._parse_trading_day_values(raw_td)
            invalid = td.isna() | raw_td.astype(str).str.strip().str.lower().isin({"", "nan", "nat", "none"})
            out = raw_td.astype(str)
            valid = ~invalid
            out.loc[valid] = td.loc[valid].dt.strftime("%Y-%m-%d")
            out.loc[invalid] = df.loc[invalid, "datetime"].dt.date.astype(str)
            df["trading_day"] = out
            if existing_inferred is None:
                df["_trading_day_inferred"] = invalid.to_numpy(dtype=bool)
            else:
                prior = pd.Series(existing_inferred, index=df.index).fillna(False).astype(bool)
                df["_trading_day_inferred"] = (prior | invalid).to_numpy(dtype=bool)
        return df.reset_index(drop=True)

    @staticmethod
    def aggregate_calendar(df: pd.DataFrame, freq: str) -> pd.DataFrame:
        """Backward-compatible helper; prefer trading-day aggregation for futures."""
        if df.empty:
            return df.copy()
        # Existing callers historically passed W-FRI/ME.  If trading_day exists,
        # route through the futures-safe path rather than resampling wall-clock time.
        if "trading_day" in df.columns and freq in {"W-FRI", "ME", "M"}:
            period = "1w" if freq == "W-FRI" else "1M"
            return DataRepository._aggregate_daily_period(df, period)
        x = df.set_index("datetime")
        agg = {
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
            "amount": "sum",
            "open_interest": "last",
            "trading_day": "last",
        }
        out = x.resample(freq).agg(agg).dropna(subset=["open", "high", "low", "close"]).reset_index()
        return out

    def select_active_contract(self, symbol: str, period: str = "1d", source_name: str = "原始行情") -> str | None:
        """Choose the currently active raw contract without rebuilding full charts.

        The previous implementation preferred the contract with the most stored
        rows, which can select an old long-lived contract.  Use the latest daily
        trading date first, then open interest (volume as fallback), matching the
        main-contract convention used elsewhere in the application.
        """
        contracts = self.list_contracts(symbol, period, source_name)
        if not contracts:
            return None
        info: list[tuple[str, pd.Timestamp, float, float, int]] = []
        max_day = pd.Timestamp.min
        for c in contracts:
            try:
                # Read native daily data directly: selecting a contract should be
                # fast and must not trigger minute reconstruction for every file.
                df = self._read_native_frame(source_name, symbol, "1d", c)
            except Exception:
                try:
                    df = self._read_native_frame(source_name, symbol, period, c)
                except Exception:
                    continue
            if df.empty:
                continue
            days = pd.to_datetime(df["trading_day"], errors="coerce")
            if days.notna().any():
                last = pd.Timestamp(days.max()).normalize()
                mask = days == days.max()
                row = df.loc[mask].iloc[-1]
            else:
                last = pd.Timestamp(df["datetime"].max()).normalize()
                row = df.iloc[-1]
            oi = float(pd.to_numeric(pd.Series([row.get("open_interest", 0.0)]), errors="coerce").fillna(0.0).iloc[0])
            vol = float(pd.to_numeric(pd.Series([row.get("volume", 0.0)]), errors="coerce").fillna(0.0).iloc[0])
            max_day = max(max_day, last)
            info.append((c, last, oi, vol, len(df)))
        if not info:
            return contracts[0]
        latest = [x for x in info if x[1] == max_day]
        pool = latest if latest else info
        return max(pool, key=lambda x: (x[2] > 0.0, x[2], x[3], x[4], x[0]))[0]

    @staticmethod
    def filter_exchange(items: Iterable[InstrumentMeta], exchange: str) -> list[InstrumentMeta]:
        if not exchange or exchange == "全部":
            return list(items)
        return [x for x in items if x.exchange == exchange]
