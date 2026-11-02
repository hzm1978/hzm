from __future__ import annotations

from bisect import bisect_left
from collections.abc import Iterable


def shift_trading_day(days: Iterable[str], current_day: str, delta: int) -> str | None:
    """Return the adjacent available trading day, skipping weekends/holidays.

    If current_day is not itself a trading day, positive movement selects the
    first available day after it; negative movement selects the last available
    day before it. Returns None when the requested direction reaches an edge.
    """
    values = sorted({
        str(value)[:10]
        for value in days
        if value is not None and len(str(value)) >= 10
        and str(value)[:10] not in {"", "NaT", "nan", "None"}
    })
    if not values or int(delta) == 0:
        return None

    current = str(current_day or "")[:10]
    pos = bisect_left(values, current)
    if pos < len(values) and values[pos] == current:
        base = pos
    elif delta > 0:
        base = pos - 1
    else:
        base = pos

    target = base + (1 if delta > 0 else -1)
    if target < 0 or target >= len(values):
        return None
    return values[target]


def trading_days_from_frame(frame) -> list[str]:
    """Extract sorted, unique trading-day labels from a DataFrame-like object."""
    if frame is None:
        return []
    try:
        values = frame["trading_day"].astype(str).tolist()
    except Exception:
        return []
    return sorted({
        str(value)[:10]
        for value in values
        if value is not None and len(str(value)) >= 10
        and str(value)[:10] not in {"", "NaT", "nan", "None"}
    })


def first_timestamp_for_trading_day(frame, day: str):
    """Return the first real 1-minute timestamp belonging to a trading day."""
    if frame is None or not day:
        return None
    try:
        trading_days = frame["trading_day"].astype(str).str.slice(0, 10)
        rows = frame.loc[trading_days == str(day)[:10]]
        if rows.empty or "datetime" not in rows.columns:
            return None
        rows = rows.sort_values("datetime", kind="stable")
        return rows.iloc[0]["datetime"]
    except Exception:
        return None


def resolve_trading_day(days: Iterable[str], requested_day: str) -> str | None:
    """Resolve a calendar date to itself if available, otherwise next available day."""
    values = sorted({
        str(value)[:10]
        for value in days
        if value is not None and len(str(value)) >= 10
        and str(value)[:10] not in {"", "NaT", "nan", "None"}
    })
    if not values:
        return None
    requested = str(requested_day or "")[:10]
    pos = bisect_left(values, requested)
    if pos < len(values):
        return values[pos]
    return values[-1]


def snapshot_timestamp_matches_latest(
    snapshot_timestamp: str,
    latest_timestamp: str,
    tolerance_seconds: float = 90.0,
) -> bool:
    """Return whether a persisted replay head is at the latest available minute.

    Used to migrate older saved sessions that predate the explicit ``at_end``
    flag.  The comparison tolerates small timestamp serialization differences.
    """
    from datetime import datetime

    def parse(value):
        text = str(value or "").strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if parsed.tzinfo is not None:
                # The replay engine stores exchange-local wall-clock timestamps.
                # Strip the explicit offset without converting through the host
                # machine's local timezone.
                parsed = parsed.replace(tzinfo=None)
            return parsed
        except Exception:
            return None

    left = parse(snapshot_timestamp)
    right = parse(latest_timestamp)
    if left is None or right is None:
        return False
    return abs((left - right).total_seconds()) <= max(0.0, float(tolerance_seconds))


def replay_status(at_end: bool, playing: bool, played: bool) -> str:
    """Resolve main-list state with end-of-data taking precedence."""
    if at_end:
        return "结束"
    if playing:
        return "播放中"
    if played:
        return "已播放"
    return ""
