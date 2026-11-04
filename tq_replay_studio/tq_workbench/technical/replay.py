from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

import numpy as np
import pandas as pd

from .config import PERIOD_SECONDS
from .timeline_utils import trading_days_from_frame


def viewport_right_bar_index(
    current_idx: int, end_offset: int, total_bars: int, visible_bars: int,
    pan_shift_slots: float = 0.0, left_pad_slots: float = 0.0,
    intraday_mode: bool = False, window_start: int | None = None,
    window_end: int | None = None,
) -> int:
    """Return the last candle whose centre remains inside a chart viewport.

    The replay head is the right edge only in follow-latest mode.  When the user
    pans through history, ``end_offset`` moves the window; at the oldest-data
    boundary a larger fractional pan can also expose blank space, so account for
    the horizontal slot shift before choosing a playback restart bar.
    """
    total = max(0, int(total_bars))
    current = int(current_idx)
    if total <= 0 or current < 0:
        return -1
    current = min(current, total - 1)
    end = min(current + 1, total)
    if intraday_mode:
        # A 1-minute chart displays the current trading day as its own viewport.
        # When the user pans within that day, the rightmost visible minute is
        # earlier than the replay head and must become the next playback anchor.
        start = max(0, min(int(window_start if window_start is not None else current), current))
        end = max(start + 1, min(total, int(window_end if window_end is not None else end)))
        slots = max(1, end - start)
        rightmost = int(np.floor(
            float(start) + float(slots) - float(pan_shift_slots) - 0.5 + 1e-9
        ))
        return max(start, min(rightmost, end - 1, total - 1, current))
    end = max(1, min(total, end - max(0, int(end_offset))))
    start = max(0, end - max(1, int(visible_bars)))
    slots = max(1, int(visible_bars))
    rightmost = int(np.floor(
        float(start) + float(slots) - max(0.0, float(left_pad_slots))
        - float(pan_shift_slots) - 0.5 + 1e-9
    ))
    return max(0, min(rightmost, end - 1, total - 1, current))


@dataclass(slots=True)
class ReplayState:
    bar_index: int
    minute_index: int
    timestamp: pd.Timestamp
    trading_day: str
    partial: dict[str, float]
    source_bar: dict[str, float]
    synthetic: bool


def locate_bar_index(bars: pd.DataFrame, period: str, timestamp: pd.Timestamp, trading_day: str) -> int:
    """Locate the bar that is *forming* at replay time.

    Intraday bars use their natural-clock start timestamp. Daily/weekly/monthly
    bars are aligned by trading_day, which is essential for Chinese futures
    night sessions (21:00 belongs to the next trading day).
    """
    if bars.empty:
        return 0
    ts = pd.Timestamp(timestamp)
    td = pd.Timestamp(str(trading_day))
    if period == "1d":
        days = pd.to_datetime(bars["trading_day"], errors="coerce").to_numpy(dtype="datetime64[ns]")
        idx = int(np.searchsorted(days, np.datetime64(td), side="right") - 1)
        return max(0, min(idx, len(bars) - 1))
    if period == "1w":
        # DataRepository.aggregate_calendar uses W-FRI labels.
        label = td.to_period("W-FRI").end_time.normalize()
        times = bars["datetime"].to_numpy(dtype="datetime64[ns]")
        idx = int(np.searchsorted(times, np.datetime64(label), side="left"))
        return max(0, min(idx, len(bars) - 1))
    if period == "1M":
        label = td.to_period("M").end_time.normalize()
        times = bars["datetime"].to_numpy(dtype="datetime64[ns]")
        idx = int(np.searchsorted(times, np.datetime64(label), side="left"))
        return max(0, min(idx, len(bars) - 1))
    times = bars["datetime"].to_numpy(dtype="datetime64[ns]")
    idx = int(np.searchsorted(times, np.datetime64(ts), side="right") - 1)
    return max(0, min(idx, len(bars) - 1))


def interpolate_partial(start: dict[str, float], target: dict[str, float], t: float) -> dict[str, float]:
    """Interpolate a *display-only* forming candle between two known states.

    This helper is intentionally presentation-only. Trading decisions must keep
    using the real 1-minute ``source_bar`` from :class:`ReplayState`. The open
    price is pinned to the target candle open; high/low are constrained so the
    interpolated state is always a valid OHLC candle.
    """
    t = max(0.0, min(float(t), 1.0))
    keys = {"high", "low", "close", "volume", "amount", "open_interest"}
    out: dict[str, float] = {"open": float(target.get("open", start.get("open", 0.0)))}
    for key in keys:
        a = float(start.get(key, target.get(key, 0.0)))
        b = float(target.get(key, a))
        out[key] = a + (b - a) * t
    o = out["open"]
    c = out["close"]
    out["high"] = max(out["high"], o, c)
    out["low"] = min(out["low"], o, c)
    return out


def minute_visual_keyframes(start: dict[str, float], target: dict[str, float], source_bar: dict[str, float]) -> list[dict[str, float]]:
    """Build display-only intra-minute keyframes from a *real* 1-minute OHLC bar.

    A 1-minute K-line tells us O/H/L/C but not the true tick order inside the
    minute.  For visual replay we therefore use a deterministic convention:
    bullish/flat minute: O -> L -> H -> C; bearish minute: O -> H -> L -> C.
    This path is **presentation only**.  Trading/execution still uses the real
    1-minute OHLC as one indivisible bar.

    ``start`` is the forming higher-timeframe candle before this minute and
    ``target`` is its exact state after consuming this minute.  Returned
    keyframes keep the higher-timeframe open fixed while progressively revealing
    the minute's high/low, making a daily/5m/15m candle visibly move up and down.
    """
    if not source_bar:
        return [dict(target)]
    try:
        so = float(source_bar['open']); sh = float(source_bar['high'])
        sl = float(source_bar['low']); sc = float(source_bar['close'])
    except Exception:
        return [dict(target)]

    candle_open = float(target.get('open', start.get('open', so)))
    cur_hi = float(start.get('high', candle_open))
    cur_lo = float(start.get('low', candle_open))
    prev_close = float(start.get('close', so))
    # Include the minute open first so overnight/session gaps are visible.
    prices = [so, sl, sh, sc] if sc >= so else [so, sh, sl, sc]
    out: list[dict[str, float]] = []
    n = max(1, len(prices))
    for j, price in enumerate(prices, start=1):
        cur_hi = max(cur_hi, float(price))
        cur_lo = min(cur_lo, float(price))
        u = j / n
        frame = dict(target)
        frame['open'] = candle_open
        frame['high'] = cur_hi
        frame['low'] = cur_lo
        frame['close'] = float(price)
        # Accumulated fields move monotonically toward the exact post-minute state.
        for key in ('volume', 'amount'):
            a = float(start.get(key, 0.0) or 0.0)
            b = float(target.get(key, a) or a)
            frame[key] = a + (b - a) * u
        if 'open_interest' in target:
            a = float(start.get('open_interest', target.get('open_interest', 0.0)) or 0.0)
            b = float(target.get('open_interest', a) or a)
            frame['open_interest'] = a + (b - a) * u
        out.append(frame)
    # Guarantee the last visual frame equals the exact real cumulative state.
    if not out or any(abs(float(out[-1].get(k, 0.0)) - float(target.get(k, 0.0))) > 1e-12 for k in ('high','low','close')):
        out.append(dict(target))
    else:
        out[-1] = dict(target)
    return out


class MinutePathProvider:
    """Build the currently-forming target candle from real 1-minute OHLC.

    The real 1-minute prices are never rescaled. `partial` is cumulative OHLCV
    for display; `source_bar` is the *newly traversed 1-minute bar* and must be
    used by the trading engine so earlier highs/lows are not replayed twice.
    """

    def __init__(self, bars: pd.DataFrame, period: str, minutes: pd.DataFrame | None = None, allow_synthetic: bool = True, daily: pd.DataFrame | None = None):
        self.bars = bars.reset_index(drop=True)
        self.period = period
        self.minutes = None if minutes is None else minutes.reset_index(drop=True)
        self.daily = None if daily is None else daily.reset_index(drop=True)
        self.allow_synthetic = allow_synthetic
        self._minute_times = None if self.minutes is None else self.minutes["datetime"].to_numpy(dtype="datetime64[ns]")
        self._td_groups: dict[str, np.ndarray] | None = None
        if self.minutes is not None and "trading_day" in self.minutes.columns:
            self._td_groups = {str(k): np.asarray(v, dtype=int) for k, v in self.minutes.groupby("trading_day").indices.items()}
        mode = str(self.bars.attrs.get("bar_label_mode", "") or "").lower()
        self._bar_label_mode = mode if mode in {"start", "end"} else self._infer_bar_label_mode()

    def _infer_bar_label_mode(self) -> str:
        """Infer whether stored intraday timestamps mark bar starts or ends.

        Rebuilt frames normally carry an explicit ``bar_label_mode`` attribute.
        This fallback is for older/native files.  It compares the first available
        real minute with the first stored bar on a common trading day.
        """
        if self.minutes is None or self.minutes.empty or self.bars.empty or self._td_groups is None:
            return "start"
        try:
            for td in self.bars["trading_day"].astype(str).drop_duplicates():
                idx = self._td_groups.get(str(td))
                if idx is None or len(idx) == 0:
                    continue
                day_bars = self.bars.loc[self.bars["trading_day"].astype(str) == str(td)]
                if day_bars.empty:
                    continue
                first_min = pd.Timestamp(self.minutes.iloc[idx]["datetime"].min())
                first_bar = pd.Timestamp(day_bars["datetime"].min())
                delta = (first_bar - first_min).total_seconds()
                if abs(delta) <= 90:
                    return "start"
                return "end" if delta > 0 else "start"
        except Exception:
            pass
        return "start"

    def _real_slice(self, i: int) -> pd.DataFrame | None:
        """Return the exact real 1-minute rows belonging to stored bar *i*.

        Intraday boundaries are taken from consecutive stored K-line timestamps,
        not from ``start + N minutes``.  This is essential around Chinese-futures
        lunch/night breaks and for vendor-specific 30m/1h/4h grouping rules.
        """
        if self.minutes is None or self.minutes.empty:
            return None
        bar = self.bars.iloc[i]

        if self.period in PERIOD_SECONDS and self.period != "1d":
            td = str(bar.get("trading_day", ""))
            if self._td_groups is None or td not in self._td_groups:
                return None
            idx = self._td_groups[td]
            if len(idx) == 0:
                return None
            part = self.minutes.iloc[idx]
            times = pd.to_datetime(part["datetime"], errors="coerce")
            label = pd.Timestamp(bar["datetime"])

            if self._bar_label_mode == "end":
                # End-labelled bars own minutes after the previous bar-end and up
                # to/including this bar-end.  Restrict by trading_day so a lunch
                # or overnight break can never leak minutes into a neighbouring K.
                mask = times <= label
                if i > 0 and str(self.bars.iloc[i - 1].get("trading_day", "")) == td:
                    prev_end = pd.Timestamp(self.bars.iloc[i - 1]["datetime"])
                    mask &= times > prev_end
            else:
                # Start-labelled bars own minutes from this timestamp until the
                # next stored start.  The last bar consumes the remaining minutes
                # of its own trading day.
                mask = times >= label
                if i + 1 < len(self.bars) and str(self.bars.iloc[i + 1].get("trading_day", "")) == td:
                    next_start = pd.Timestamp(self.bars.iloc[i + 1]["datetime"])
                    mask &= times < next_start

            out = part.loc[mask.to_numpy()]
            return out if not out.empty else None

        if self.period == "1d":
            idx = None if self._td_groups is None else self._td_groups.get(str(bar["trading_day"]))
            if idx is not None and len(idx):
                return self.minutes.iloc[idx]
            return None

        # Weekly/monthly bars are selected by trading_day, never by the calendar
        # timestamp of the night-session minute.
        td = pd.to_datetime(self.minutes["trading_day"], errors="coerce")
        bar_td = pd.to_datetime(bar.get("trading_day"), errors="coerce")
        if pd.isna(bar_td):
            bar_td = pd.Timestamp(bar["datetime"])
        if self.period == "1w":
            period = pd.Timestamp(bar_td).to_period("W-FRI")
        else:
            period = pd.Timestamp(bar_td).to_period("M")
        minute_period = td.dt.to_period("W-FRI" if self.period == "1w" else "M")
        out = self.minutes.loc[minute_period == period]
        return out if not out.empty else None

    @staticmethod
    def _real_path(part: pd.DataFrame) -> list[tuple[pd.Timestamp, str, dict[str, float], dict[str, float], bool]]:
        result = []
        o = float(part.iloc[0]["open"])
        hi = o
        lo = o
        volume = 0.0
        amount = 0.0
        oi = float(part.iloc[0].get("open_interest", 0.0) or 0.0)
        for _, row in part.iterrows():
            sh = float(row["high"]); sl = float(row["low"]); sc = float(row["close"])
            hi = max(hi, sh); lo = min(lo, sl)
            volume += float(row.get("volume", 0.0) or 0.0)
            amount += float(row.get("amount", 0.0) or 0.0)
            oi = float(row.get("open_interest", oi) or oi)
            partial = {"open": o, "high": hi, "low": lo, "close": sc,
                       "volume": volume, "amount": amount, "open_interest": oi}
            source = {"open": float(row["open"]), "high": sh, "low": sl, "close": sc,
                      "volume": float(row.get("volume", 0.0) or 0.0),
                      "amount": float(row.get("amount", 0.0) or 0.0),
                      "open_interest": oi}
            result.append((pd.Timestamp(row["datetime"]), str(row["trading_day"]), partial, source, False))
        return result

    @staticmethod
    def _synthetic_path(bar: pd.Series, i: int) -> list[tuple[pd.Timestamp, str, dict[str, float], dict[str, float], bool]]:
        # Display-only deterministic OHLC path. It does not pretend to reconstruct
        # unknown tick order. When minute data exists (normal case), this is unused.
        o, h, l, c = map(float, [bar["open"], bar["high"], bar["low"], bar["close"]])
        order = [o, l, h, c] if c >= o else [o, h, l, c]
        ts = pd.Timestamp(bar["datetime"])
        td = str(bar.get("trading_day", ts.date()))
        points = []
        hi = lo = o
        for j, price in enumerate(order):
            hi = max(hi, price); lo = min(lo, price)
            partial = {"open": o, "high": hi, "low": lo, "close": price,
                       "volume": 0.0, "amount": 0.0, "open_interest": float(bar.get("open_interest", 0.0) or 0.0)}
            # Synthetic source is marked; trading logic can choose not to execute it.
            source = {"open": price, "high": price, "low": price, "close": price,
                      "volume": 0.0, "amount": 0.0, "open_interest": partial["open_interest"]}
            points.append((ts + pd.Timedelta(seconds=j * 15), td, partial, source, True))
        return points

    def path(self, i: int):
        real = self._real_slice(i)
        if self.period in {"1w", "1M"} and self.daily is not None and not self.daily.empty:
            # Missing minute history must not turn the day buttons into week /
            # month buttons. Use native DAILY records only for uncovered days.
            # These entries remain marked synthetic: they are not minute fills.
            freq = "W-FRI" if self.period == "1w" else "M"
            bar = self.bars.iloc[i]
            period = pd.Timestamp(bar["trading_day"]).to_period(freq)
            days = pd.to_datetime(self.daily["trading_day"], errors="coerce")
            fallback = self.daily.loc[days.dt.to_period(freq) == period].copy()
            if real is not None and not real.empty:
                covered = set(real["trading_day"].astype(str))
                fallback = fallback.loc[~fallback["trading_day"].astype(str).isin(covered)].copy()
            if not fallback.empty:
                fallback["_daily_fallback"] = True
                pieces = [fallback]
                if real is not None and not real.empty:
                    pieces.append(real.assign(_daily_fallback=False))
                source = pd.concat(pieces, ignore_index=True, sort=False).sort_values("datetime")
                result = self._real_path(source)
                return [
                    (ts, td, partial, source_bar, bool(coarse))
                    for (ts, td, partial, source_bar, _), coarse
                    in zip(result, source["_daily_fallback"].to_numpy())
                ]
        if real is not None and not real.empty:
            return self._real_path(real)
        if self.allow_synthetic:
            return self._synthetic_path(self.bars.iloc[i], i)
        bar = self.bars.iloc[i]
        p = {k: float(bar[k]) for k in ["open", "high", "low", "close"]}
        p.update({"volume": float(bar.get("volume", 0.0) or 0.0), "amount": float(bar.get("amount", 0.0) or 0.0),
                  "open_interest": float(bar.get("open_interest", 0.0) or 0.0)})
        return [(pd.Timestamp(bar["datetime"]), str(bar.get("trading_day", "")), p, p.copy(), True)]


class ReplayEngine:
    def __init__(self, bars: pd.DataFrame, period: str, minutes: pd.DataFrame | None = None, allow_synthetic: bool = True, daily: pd.DataFrame | None = None):
        if bars.empty:
            raise ValueError("bars cannot be empty")
        self.bars = bars.reset_index(drop=True)
        self.period = period
        self.provider = MinutePathProvider(self.bars, period, minutes, allow_synthetic=allow_synthetic, daily=daily)
        self.bar_index = 0
        self.minute_index = 0
        self._path = self.provider.path(0)

    def current(self) -> ReplayState:
        ts, td, partial, source, synthetic = self._path[self.minute_index]
        return ReplayState(self.bar_index, self.minute_index, ts, td, dict(partial), dict(source), synthetic)

    def step(self, delta: int = 1) -> ReplayState:
        if delta >= 0:
            for _ in range(delta): self._forward_once()
        else:
            for _ in range(-delta): self._back_once()
        return self.current()

    def _forward_once(self) -> None:
        if self.minute_index + 1 < len(self._path):
            self.minute_index += 1; return
        if self.bar_index + 1 < len(self.bars):
            self.bar_index += 1; self._path = self.provider.path(self.bar_index); self.minute_index = 0

    def _back_once(self) -> None:
        if self.minute_index > 0:
            self.minute_index -= 1; return
        if self.bar_index > 0:
            self.bar_index -= 1; self._path = self.provider.path(self.bar_index); self.minute_index = len(self._path) - 1

    def jump_bar(self, bar_index: int, to_end_of_bar: bool = False) -> ReplayState:
        self.bar_index = max(0, min(int(bar_index), len(self.bars) - 1))
        self._path = self.provider.path(self.bar_index)
        self.minute_index = len(self._path) - 1 if to_end_of_bar else 0
        return self.current()

    def jump_timestamp(self, timestamp: pd.Timestamp, trading_day: str | None = None) -> ReplayState:
        td = trading_day or str(pd.Timestamp(timestamp).date())
        idx = locate_bar_index(self.bars, self.period, pd.Timestamp(timestamp), td)
        self.bar_index = idx
        self._path = self.provider.path(idx)
        times = np.asarray([np.datetime64(x[0]) for x in self._path])
        mi = int(np.searchsorted(times, np.datetime64(pd.Timestamp(timestamp)), side="right") - 1)
        self.minute_index = max(0, min(mi, len(self._path) - 1))
        return self.current()

    def jump_trading_day(self, day: str) -> ReplayState:
        """Jump to the bar that should sit at the right edge for a selected date.

        Intraday and daily data have one or more bars *within* each trading day,
        so choose the final bar whose trading day is not later than the selected
        date.  Weekly/monthly bars are labeled by their period-ending trading day;
        choose the first period-end on or after the selected date so a date inside
        a week/month maps to that containing higher-timeframe candle.
        """
        days = pd.to_datetime(self.bars["trading_day"], errors="coerce").to_numpy(dtype="datetime64[ns]")
        target = np.datetime64(pd.Timestamp(str(day)).normalize())
        if self.period in {"1w", "1M"}:
            matches = np.flatnonzero(days >= target)
            idx = int(matches[0]) if len(matches) else len(self.bars) - 1
        else:
            matches = np.flatnonzero(days <= target)
            idx = int(matches[-1]) if len(matches) else 0
        self.jump_bar(idx)
        # A W/M candle may contain many real minute days or daily fallback rows.
        # Select the requested DAY inside it, not the candle's first / last day.
        matches = [j for j, entry in enumerate(self._path) if str(entry[1])[:10] == str(day)[:10]]
        if matches:
            self.minute_index = matches[0]
        return self.current()

    def available_trading_days(self) -> list[str]:
        """Return daily navigation dates, never weekly/monthly period endpoints."""
        days = set(trading_days_from_frame(self.provider.minutes))
        if self.period in {"1w", "1M"}:
            days.update(trading_days_from_frame(self.provider.daily))
            if not days:
                # No underlying days are known: disabling +/- is truthful;
                # inventing weekdays or jumping to the next week is not.
                return []
            freq = "W-FRI" if self.period == "1w" else "M"
            periods = set(pd.to_datetime(self.bars["trading_day"]).dt.to_period(freq))
            return sorted(day for day in days if pd.Timestamp(day).to_period(freq) in periods)
        days.update(trading_days_from_frame(self.bars))
        bar_days = trading_days_from_frame(self.bars)
        return sorted(day for day in days if bar_days and bar_days[0] <= day <= bar_days[-1])

    @property
    def path_length(self) -> int:
        return len(self._path)

    @property
    def path_position(self) -> int:
        return self.minute_index + 1

    @property
    def progress(self) -> float:
        if len(self.bars) <= 1: return 1.0
        frac = self.minute_index / max(1, len(self._path))
        return min(1.0, (self.bar_index + frac) / (len(self.bars) - 1))

    @property
    def at_end(self) -> bool:
        return self.bar_index == len(self.bars) - 1 and self.minute_index == len(self._path) - 1
