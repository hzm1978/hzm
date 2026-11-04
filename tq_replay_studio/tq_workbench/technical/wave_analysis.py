from __future__ import annotations

"""Deterministic Elliott-wave candidates and candlestick reversal patterns.

The Elliott-wave count is inherently interpretive.  This module therefore does
not claim to discover a unique count: it draws an ATR/percentage ZigZag and only
labels a 1-5 impulse when the basic Elliott rules are satisfied.  Otherwise it
shows the swing structure without forcing a numbered count.
"""

from dataclasses import dataclass, field
from math import isfinite

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class Pivot:
    index: int
    price: float
    kind: str  # "H" or "L"


WAVE_RULER_RATIOS: tuple[float, ...] = (
    0.0, 23.6, 38.2, 50.0, 61.8, 78.6, 100.0, 127.2, 161.8
)


def calculate_wave_ruler_levels(
    a_price: float, b_price: float, c_price: float
) -> tuple[tuple[float, float], ...]:
    """Return (ratio_percent, target_price) pairs for an A-B-C wave ruler.

    AB is the measuring leg.  Each target is projected from C in the same
    signed direction as A->B, so the same formula handles rising and falling
    structures without special cases.
    """
    a = float(a_price)
    b = float(b_price)
    c = float(c_price)
    impulse = b - a
    scale = max(abs(a), abs(b), 1.0)
    if not all(isfinite(v) for v in (a, b, c, impulse)) or abs(impulse) <= scale * 1e-12:
        return ()
    return tuple(
        (ratio, c + impulse * ratio / 100.0)
        for ratio in WAVE_RULER_RATIOS
    )


LAST_COMPLETED_LEG_FIB_RATIOS: tuple[float, ...] = (
    0.0, 23.6, 38.2, 50.0, 61.8, 78.6, 100.0
)


def last_completed_leg_fibonacci_levels(
    pivots: list[Pivot],
) -> tuple[tuple[float, float], ...]:
    """Display-only Fibonacci scale for the most recent completed swing leg.

    The final two confirmed pivots define the completed leg.  The lower price
    is 0% and the higher price is 100%, independent of whether the leg moved
    upward or downward.  This is intentionally separate from the A-B-C
    retracement/extension calculations used by the Elliott-wave analysis.
    """
    if len(pivots) < 2:
        return ()
    first, last = pivots[-2], pivots[-1]
    # The retracement starts at the latest confirmed turning point (0%) and
    # measures back toward the previous turning point (100%).  Therefore:
    # - latest pivot is a LOW: the correction is rising, so draw 0 -> 100
    #   from the bottom upward;
    # - latest pivot is a HIGH: the correction is falling, so draw 0 -> 100
    #   from the top downward.
    zero_price = float(last.price)
    hundred_price = float(first.price)
    if not (isfinite(zero_price) and isfinite(hundred_price)):
        return ()
    scale = max(abs(zero_price), abs(hundred_price), 1.0)
    if abs(hundred_price - zero_price) <= scale * 1e-12:
        return ()
    return tuple(
        (
            ratio,
            zero_price + (hundred_price - zero_price) * ratio / 100.0,
        )
        for ratio in LAST_COMPLETED_LEG_FIB_RATIOS
    )


@dataclass(frozen=True)
class FibProjection:
    """Fibonacci ruler for one A-B-C(-D) N-shaped swing.

    ``start`` and ``end`` are the two endpoints A and B of the preceding
    impulse leg.  Retracement levels are measured from B back toward A using
    exactly this high-low range. ``anchor`` is C (the correction endpoint);
    extension levels are projected from C in the direction A->B.
    """
    anchor: Pivot
    retracement_percent: float
    levels: tuple[tuple[float, float], ...]  # (percentage, projected price)
    endpoint: Pivot | None = None
    actual_percent: float | None = None
    # Standard retracement levels measured from B back toward A.
    retracement_levels: tuple[tuple[float, float], ...] = ()
    # Exact previous-leg anchors.  The retracement ruler is drawn from A-B,
    # while extension levels remain projected forward from C.
    start: Pivot | None = None
    end: Pivot | None = None


@dataclass(frozen=True)
class ChartPattern:
    """A chart-structure candidate with drawable trendline segments.

    Each line is ((x1, price1), (x2, price2)); x values are positional bar
    indices.  ``markers`` is used by the 1-2-3 adjustment structure.
    """
    kind: str
    label: str
    start_index: int
    end_index: int
    lines: tuple[tuple[tuple[float, float], tuple[float, float]], ...]
    markers: tuple[Pivot, ...] = ()
    label_index: int = 0
    label_price: float = 0.0
    direction: int = 0
    confidence: float = 0.0
    marker_labels: tuple[str, ...] = ()
    target_price: float | None = None


@dataclass
class WaveAnalysis:
    pivots: list[Pivot]
    preview: tuple[int, float] | None
    labels: dict[int, str]
    ratios: dict[int, str]
    patterns: list[tuple[int, str, int]]  # pivot index, label, direction (+1 bull/-1 bear/0 neutral)
    impulse_start: int | None = None
    impulse_end: int | None = None
    projection: FibProjection | None = None
    # All visible completed N-shaped rulers, plus the latest unfinished ruler.
    projections: list[FibProjection] = field(default_factory=list)
    # Historical retracement/extension labels: (pivot index, text).
    ratio_annotations: list[tuple[int, str]] = field(default_factory=list)
    # Geometric/reversal structures: V/arc/cup/2B, triangles, flags, wedges, boxes, 1-2-3.
    chart_patterns: list[ChartPattern] = field(default_factory=list)


def _atr(df: pd.DataFrame, period: int = 14) -> np.ndarray:
    high = pd.to_numeric(df["high"], errors="coerce").to_numpy(float)
    low = pd.to_numeric(df["low"], errors="coerce").to_numpy(float)
    close = pd.to_numeric(df["close"], errors="coerce").to_numpy(float)
    prev = np.r_[close[0], close[:-1]] if len(close) else np.array([], dtype=float)
    tr = np.maximum(high - low, np.maximum(np.abs(high - prev), np.abs(low - prev)))
    return pd.Series(tr).rolling(period, min_periods=1).mean().to_numpy(float)


def zigzag_pivots(
    df: pd.DataFrame,
    min_reversal_pct: float = 0.005,
    atr_multiple: float = 1.2,
) -> tuple[list[Pivot], tuple[int, float] | None]:
    """Return confirmed pivots plus a non-confirmed line endpoint.

    A reversal is confirmed only after price moves against the active extreme by
    max(min_reversal_pct, atr_multiple * ATR / price).  Confirmed pivots never
    use bars after the current replay index.
    """
    required = {"high", "low", "close"}
    if df is None or df.empty or not required.issubset(df.columns):
        return [], None
    x = df.copy()
    if "_wave_position" not in x.columns:
        x["_wave_position"] = np.arange(len(x), dtype=int)
    x = x.dropna(subset=["high", "low", "close"])
    if len(x) < 3:
        return [], None
    # Use positional row indices; the chart's x-axis is positional even if a
    # caller happens to supply a DataFrame with a non-integer index.
    original_indices = x["_wave_position"].to_numpy(dtype=int)
    high = pd.to_numeric(x["high"], errors="coerce").to_numpy(float)
    low = pd.to_numeric(x["low"], errors="coerce").to_numpy(float)
    close = pd.to_numeric(x["close"], errors="coerce").to_numpy(float)
    atr = _atr(x)
    if not (np.isfinite(high).all() and np.isfinite(low).all() and np.isfinite(close).all()):
        return [], None

    def reversal(price: float, local_i: int) -> float:
        return max(abs(price) * float(min_reversal_pct), float(atr[local_i]) * float(atr_multiple))

    direction = 0
    low_i = high_i = 0
    low_v, high_v = float(low[0]), float(high[0])
    pivots: list[Pivot] = []

    for i in range(1, len(x)):
        if direction == 0:
            if low[i] < low_v:
                low_i, low_v = i, float(low[i])
            if high[i] > high_v:
                high_i, high_v = i, float(high[i])
            up_move = float(high[i]) - low_v
            down_move = high_v - float(low[i])
            if up_move >= reversal(low_v, i) and low_i < i:
                pivots.append(Pivot(int(original_indices[low_i]), low_v, "L"))
                direction = 1
                high_i, high_v = i, float(high[i])
            elif down_move >= reversal(high_v, i) and high_i < i:
                pivots.append(Pivot(int(original_indices[high_i]), high_v, "H"))
                direction = -1
                low_i, low_v = i, float(low[i])
        elif direction > 0:
            if high[i] >= high_v:
                high_i, high_v = i, float(high[i])
            # Avoid resolving a high/low ordering ambiguity inside one candle.
            if i > high_i and high_v - float(low[i]) >= reversal(high_v, i):
                pivots.append(Pivot(int(original_indices[high_i]), high_v, "H"))
                direction = -1
                low_i, low_v = i, float(low[i])
        else:
            if low[i] <= low_v:
                low_i, low_v = i, float(low[i])
            if i > low_i and float(high[i]) - low_v >= reversal(low_v, i):
                pivots.append(Pivot(int(original_indices[low_i]), low_v, "L"))
                direction = 1
                high_i, high_v = i, float(high[i])

    # The current leg is shown as a dashed preview, never treated as confirmed.
    if not pivots:
        preview = (int(original_indices[-1]), float(close[-1]))
    else:
        preview = (int(original_indices[-1]), float(close[-1]))
    # Remove accidental same-kind pivots caused by pathological/flat input.
    compact: list[Pivot] = []
    for pivot in pivots:
        if compact and compact[-1].kind == pivot.kind:
            better = (pivot.price > compact[-1].price) if pivot.kind == "H" else (pivot.price < compact[-1].price)
            if better:
                compact[-1] = pivot
        elif not compact or pivot.index > compact[-1].index:
            compact.append(pivot)
    return compact, preview


def _valid_impulse(seq: list[Pivot]) -> tuple[bool, int]:
    if len(seq) != 6:
        return False, 0
    p = [float(x.price) for x in seq]
    kinds = "".join(x.kind for x in seq)
    direction = 1 if p[-1] > p[0] else -1
    expected = "LHLHLH" if direction > 0 else "HLHLHL"
    if kinds != expected:
        return False, direction
    w1, w2, w3, w4, w5 = abs(p[1] - p[0]), abs(p[2] - p[1]), abs(p[3] - p[2]), abs(p[4] - p[3]), abs(p[5] - p[4])
    if min(w1, w2, w3, w4, w5) <= 1e-12:
        return False, direction
    if direction > 0:
        rules = p[2] > p[0] and p[3] > p[1] and p[4] > p[1] and p[5] > p[3]
    else:
        rules = p[2] < p[0] and p[3] < p[1] and p[4] < p[1] and p[5] < p[3]
    # Elliott rule: wave 3 cannot be the shortest of waves 1, 3 and 5.
    rules = rules and w3 >= min(w1, w5) - 1e-9
    # Reject implausibly deep wave-2 retracements that invalidate wave 1.
    rules = rules and (w2 / w1) < 1.0
    return bool(rules), direction


def _find_latest_impulse(pivots: list[Pivot]) -> tuple[int | None, int | None, int]:
    if len(pivots) < 6:
        return None, None, 0
    # Latest valid count wins; this is a candidate count, not a unique Elliott count.
    for start in range(len(pivots) - 6, -1, -1):
        ok, direction = _valid_impulse(pivots[start : start + 6])
        if ok:
            return start, start + 5, direction
    return None, None, 0


def _ratio(numerator: float, denominator: float) -> float | None:
    if abs(denominator) <= 1e-12:
        return None
    value = abs(numerator) / abs(denominator) * 100.0
    return value if isfinite(value) else None


def _make_fibonacci_projection(
    a: Pivot, b: Pivot, c: Pivot, endpoint: Pivot | None = None,
) -> FibProjection | None:
    first_leg = float(b.price) - float(a.price)
    correction = float(c.price) - float(b.price)
    length = abs(first_leg)
    if length <= 1e-12 or first_leg * correction >= 0:
        return None
    retracement = abs(correction) / length * 100.0
    # A correction that crosses the start of A-B is not a valid simple N.
    if not isfinite(retracement) or retracement > 100.0 + 1e-9:
        return None
    direction = 1.0 if first_leg > 0 else -1.0
    # Fibonacci extension levels are projected from C in the direction A->B.
    levels = tuple(
        (ratio, float(c.price) + direction * length * ratio / 100.0)
        for ratio in (0.0, 38.2, 61.8, 100.0, 127.2, 138.2, 161.8, 200.0, 261.8)
    )
    # Retracement levels are measured from B back toward A.  These are shown
    # beside the latest ruler as a compact reference scale.
    retracement_levels = tuple(
        (ratio, float(b.price) - direction * length * ratio / 100.0)
        for ratio in (0.0, 23.6, 38.2, 50.0, 61.8, 78.6, 100.0)
    )
    actual = None
    if endpoint is not None:
        third_leg = float(endpoint.price) - float(c.price)
        if first_leg * third_leg <= 0:
            return None
        # Do not publish the third-leg ruler ratio merely because price has
        # turned away from C.  It becomes a confirmed N-wave extension only
        # after D breaks the prior B extreme: above B for an upward N, below B
        # for a downward N.  A failed retest/short third leg receives no label.
        direction = 1.0 if first_leg > 0 else -1.0
        breakout = direction * (float(endpoint.price) - float(b.price))
        tolerance = max(abs(float(b.price)) * 1e-10, 1e-9)
        if breakout <= tolerance:
            return None
        actual = abs(third_leg) / length * 100.0
        if not isfinite(actual):
            return None
    return FibProjection(
        anchor=c,
        retracement_percent=retracement,
        levels=levels,
        endpoint=endpoint,
        actual_percent=actual,
        retracement_levels=retracement_levels,
        start=a,
        end=b,
    )


def build_fibonacci_projections(pivots: list[Pivot]) -> list[FibProjection]:
    """Build a ruler for every completed N and one for the latest open N.

    For A-B-C-D, B-C is measured against A-B as a retracement.  C-D is
    measured as an extension only after D breaks the prior B extreme (above B
    for an upward N, below B for a downward N).  Sliding the
    four-pivot window over the full swing history gives historical ratios rather
    than annotating only the most recent impulse.
    """
    out: list[FibProjection] = []
    if len(pivots) < 3:
        return out
    for i in range(len(pivots) - 3):
        item = _make_fibonacci_projection(
            pivots[i], pivots[i + 1], pivots[i + 2], pivots[i + 3]
        )
        if item is not None:
            out.append(item)
    # A final A-B-C has no confirmed D yet.  Keep its forward ruler as a preview.
    if len(pivots) >= 3:
        last = _make_fibonacci_projection(*pivots[-3:])
        if last is not None and (not out or out[-1].anchor.index != last.anchor.index):
            out.append(last)
    return out


def build_fibonacci_projection(pivots: list[Pivot]) -> FibProjection | None:
    """Backward-compatible helper returning the latest N-shaped ruler."""
    projections = build_fibonacci_projections(pivots)
    return projections[-1] if projections else None


def build_wave_ratio_annotations(pivots: list[Pivot]) -> list[tuple[int, str]]:
    """Return every N-pattern retracement and third-leg ruler ratio.

    For each A-B-C, label the B-C retracement at C relative to A-B.  When D
    exists, label C-D at D relative to that same A-B ruler.  The same pivot may
    legitimately carry two labels: it can be D of the preceding N and C of the
    next N.
    """
    annotations: list[tuple[int, str]] = []
    for i in range(len(pivots) - 2):
        a, b, c = pivots[i : i + 3]
        projection = _make_fibonacci_projection(a, b, c)
        if projection is None:
            continue
        annotations.append((c.index, f"回调 {projection.retracement_percent:.1f}%"))
        if i + 3 < len(pivots):
            d = pivots[i + 3]
            completed = _make_fibonacci_projection(a, b, c, d)
            if completed is not None and completed.actual_percent is not None:
                annotations.append((d.index, f"波浪尺 {completed.actual_percent:.1f}%"))
    return annotations


def filter_patterns_at_pivots(
    patterns: list[tuple[int, str, int]],
    pivots: list[Pivot],
    max_distance: int = 3,
) -> list[tuple[int, str, int]]:
    """Keep reversal labels only when they coincide with a confirmed swing turn."""
    if not patterns or not pivots:
        return []
    filtered: list[tuple[int, str, int]] = []
    seen: set[tuple[int, str]] = set()
    for index, label, direction in patterns:
        eligible = []
        for pivot in pivots:
            distance = abs(int(index) - int(pivot.index))
            if distance > int(max_distance):
                continue
            if direction < 0 and pivot.kind != "H":
                continue
            if direction > 0 and pivot.kind != "L":
                continue
            eligible.append((distance, pivot.index, pivot))
        if not eligible:
            continue
        _distance, _idx, nearest = min(eligible, key=lambda item: (item[0], item[1]))
        key = (nearest.index, str(label))
        if key not in seen:
            filtered.append((nearest.index, str(label), int(direction)))
            seen.add(key)
    return sorted(filtered, key=lambda item: (item[0], item[1]))


def detect_reversal_patterns(df: pd.DataFrame) -> list[tuple[int, str, int]]:
    """Detect selected common reversal candles with conservative thresholds.

    Returned indices are positional indices in ``df``.  These are heuristic
    chart annotations, not standalone trading signals.
    """
    if df is None or len(df) < 2:
        return []
    x = df.reset_index(drop=True)
    positions = (
        pd.to_numeric(x["_wave_position"], errors="coerce").fillna(pd.Series(np.arange(len(x)), index=x.index)).to_numpy(dtype=int)
        if "_wave_position" in x.columns else np.arange(len(x), dtype=int)
    )
    o = pd.to_numeric(x["open"], errors="coerce").to_numpy(float)
    h = pd.to_numeric(x["high"], errors="coerce").to_numpy(float)
    l = pd.to_numeric(x["low"], errors="coerce").to_numpy(float)
    c = pd.to_numeric(x["close"], errors="coerce").to_numpy(float)
    atr = _atr(x)
    body = np.abs(c - o)
    span = np.maximum(h - l, 1e-12)
    upper = h - np.maximum(o, c)
    lower = np.minimum(o, c) - l
    out: list[tuple[int, str, int]] = []

    for i in range(1, len(x)):
        prior_up = i >= 3 and c[i - 1] > c[i - 3]
        prior_down = i >= 3 and c[i - 1] < c[i - 3]
        b = max(body[i], 1e-12)
        # Shooting star: compact body near the low, long upper shadow, after a rise.
        if prior_up and body[i] <= 0.35 * span[i] and upper[i] >= 2.0 * b and lower[i] <= 0.6 * b:
            out.append((int(positions[i]), "射击之星", -1))

        # Engulfing patterns use real bodies; equality at the boundaries is allowed.
        if c[i - 1] > o[i - 1] and c[i] < o[i] and o[i] >= c[i - 1] and c[i] <= o[i - 1]:
            out.append((int(positions[i]), "阴吃阳", -1))
        elif c[i - 1] < o[i - 1] and c[i] > o[i] and o[i] <= c[i - 1] and c[i] >= o[i - 1]:
            out.append((int(positions[i]), "阳吃阴", 1))

        # Harami: the current real body is contained inside the previous real body.
        prev_top, prev_bottom = max(o[i - 1], c[i - 1]), min(o[i - 1], c[i - 1])
        cur_top, cur_bottom = max(o[i], c[i]), min(o[i], c[i])
        if body[i - 1] >= 0.7 * max(atr[i - 1], 1e-12) and cur_top <= prev_top and cur_bottom >= prev_bottom:
            out.append((int(positions[i]), "孕线", 1 if c[i] > o[i] else -1 if c[i] < o[i] else 0))

        # Tweezer top/bottom: nearly equal extrema on adjacent candles.
        tolerance = max(0.15 * float(atr[i]), 0.0005 * max(abs(h[i]), abs(l[i]), 1.0))
        if prior_up and abs(h[i] - h[i - 1]) <= tolerance and c[i] < o[i]:
            out.append((int(positions[i]), "平顶", -1))
        elif prior_down and abs(l[i] - l[i - 1]) <= tolerance and c[i] > o[i]:
            out.append((int(positions[i]), "平底", 1))

        # Dark cloud cover: bearish candle penetrates more than half of prior bull body.
        mid_prev = (o[i - 1] + c[i - 1]) / 2.0
        if c[i - 1] > o[i - 1] and c[i] < o[i] and o[i] > c[i - 1] and o[i] > mid_prev and o[i - 1] < c[i] < mid_prev:
            out.append((int(positions[i]), "乌云盖顶", -1))

    for i in range(2, len(x)):
        first_body = body[i - 2]
        middle_body = body[i - 1]
        # Morning star: long bearish candle, small indecision candle, bullish recovery.
        if (
            c[i - 2] < o[i - 2]
            and first_body >= 0.8 * max(float(atr[i - 2]), 1e-12)
            and middle_body <= 0.55 * first_body
            and c[i] > o[i]
            and c[i] >= (o[i - 2] + c[i - 2]) / 2.0
            and (i < 4 or c[i - 2] < c[i - 4])
        ):
            out.append((int(positions[i]), "启明星", 1))

        # Explicit alternating bearish-bullish-bearish three-candle reversal.
        if c[i - 2] < o[i - 2] and c[i - 1] > o[i - 1] and c[i] < o[i] and c[i] < min(l[i - 1], c[i - 2]):
            out.append((int(positions[i]), "阴阳阴", -1))

    # Tussle line: two consecutive small-body candles with long opposite shadows.
    def tussle(j: int) -> bool:
        bj = max(body[j], 1e-12)
        return body[j] <= 0.32 * span[j] and upper[j] >= 1.25 * bj and lower[j] >= 1.25 * bj

    for i in range(1, len(x)):
        if tussle(i - 1) and tussle(i):
            first_bias = upper[i - 1] - lower[i - 1]
            second_bias = upper[i] - lower[i]
            if first_bias * second_bias < 0:
                out.append((int(positions[i]), "搓揉线", 0))

    # De-duplicate and cap annotations per candle to keep the chart legible.
    cleaned: list[tuple[int, str, int]] = []
    seen: set[tuple[int, str]] = set()
    for item in out:
        key = (item[0], item[1])
        if key not in seen:
            cleaned.append(item)
            seen.add(key)
    return cleaned



def _fit_pivot_boundary(points: list[Pivot]) -> tuple[float, float, float] | None:
    """Least-squares line y=slope*x+intercept and RMS residual."""
    if len(points) < 2:
        return None
    xs = np.asarray([float(v.index) for v in points], dtype=float)
    ys = np.asarray([float(v.price) for v in points], dtype=float)
    if np.ptp(xs) <= 0:
        return None
    slope, intercept = np.polyfit(xs, ys, 1)
    residual = float(np.sqrt(np.mean((ys - (slope * xs + intercept)) ** 2)))
    return float(slope), float(intercept), residual


def _line_value(line: tuple[float, float, float], x: float) -> float:
    return line[0] * float(x) + line[1]


def _chart_pattern_lines(
    top: tuple[float, float, float], bottom: tuple[float, float, float],
    x0: int, x1: int,
) -> tuple[tuple[tuple[float, float], tuple[float, float]], ...]:
    return (
        ((float(x0), _line_value(top, x0)), (float(x1), _line_value(top, x1))),
        ((float(x0), _line_value(bottom, x0)), (float(x1), _line_value(bottom, x1))),
    )


def _local_position(positions: np.ndarray, global_index: int) -> int:
    return int(np.searchsorted(positions, int(global_index), side="left"))



def _detect_simple_corrections(
    pivots: list[Pivot], positions: np.ndarray, close: np.ndarray,
) -> tuple[list[ChartPattern], set[tuple[int, int, int]]]:
    """Detect a one-leg/simple pullback without an internal ABC/ABCDE swing.

    A-B is the impulse leg and B-C is the single counter-trend correction.
    The correction is a candidate while C is the latest confirmed pivot; it is
    confirmed only after a later confirmed swing closes through B.  The
    consecutive-pivot requirement is the operational meaning of "simple" here:
    no extra confirmed counter-swing is present inside B-C.
    """
    out: list[ChartPattern] = []
    simple_triples: set[tuple[int, int, int]] = set()
    for i in range(len(pivots) - 2):
        a, b, c = pivots[i:i + 3]
        if a.kind == "L" and b.kind == "H" and c.kind == "L" and c.price > a.price:
            direction = 1
            first_leg = float(b.price) - float(a.price)
            retracement = (float(b.price) - float(c.price)) / max(first_leg, 1e-12)
            trigger = float(b.price)
        elif a.kind == "H" and b.kind == "L" and c.kind == "H" and c.price < a.price:
            direction = -1
            first_leg = float(a.price) - float(b.price)
            retracement = (float(c.price) - float(b.price)) / max(first_leg, 1e-12)
            trigger = float(b.price)
        else:
            continue
        if first_leg <= 1e-12 or not 0.236 <= retracement <= 0.786:
            continue
        # Keep historical completed cases and only the latest live candidate.
        d = pivots[i + 3] if i + 3 < len(pivots) else None
        confirmed = False
        if d is not None:
            if direction > 0 and d.kind == "H" and float(d.price) > trigger:
                confirmed = True
            elif direction < 0 and d.kind == "L" and float(d.price) < trigger:
                confirmed = True
            elif i != len(pivots) - 3:
                continue
        elif i != len(pivots) - 3:
            continue

        end_index = int(d.index if d is not None else c.index)
        label_index = end_index if confirmed else int(c.index)
        label_price = float(d.price if confirmed else c.price)
        lines = [
            ((float(a.index), float(a.price)), (float(b.index), float(b.price))),
            ((float(b.index), float(b.price)), (float(c.index), float(c.price))),
        ]
        if d is not None:
            lines.append(((float(c.index), float(c.price)), (float(d.index), float(d.price))))
        out.append(ChartPattern(
            "simple_correction",
            "简单型回调确认" if confirmed else "简单型回调候选",
            int(a.index), end_index, tuple(lines),
            markers=(a, b, c),
            label_index=label_index, label_price=label_price,
            direction=direction, confidence=0.86 if confirmed else 0.66,
            marker_labels=("起点", "推动端", "回调端"),
        ))
        simple_triples.add((int(a.index), int(b.index), int(c.index)))
    return out, simple_triples


def _detect_irregular_12345(
    pivots: list[Pivot],
) -> list[ChartPattern]:
    """Mark five-swing 1-2-3-4-5 sequences that fail standard impulse rules.

    This is deliberately called an irregular 1-2-3-4-5 *candidate* rather
    than a valid Elliott impulse.  It requires alternating pivots and at least
    one identifiable rule violation (wave-2 origin breach, wave-4 overlap,
    wave-3 shortest, or truncated wave 5).  It is a structural annotation, not
    an assertion that all such sequences have one canonical Elliott meaning.
    """
    out: list[ChartPattern] = []
    for i in range(len(pivots) - 5):
        seq = pivots[i:i + 6]
        kinds = ''.join(q.kind for q in seq)
        if kinds not in {"LHLHLH", "HLHLHL"}:
            continue
        prices = [float(q.price) for q in seq]
        direction = 1 if prices[-1] > prices[0] else -1
        expected = "LHLHLH" if direction > 0 else "HLHLHL"
        if kinds != expected:
            continue
        legs = [abs(prices[j + 1] - prices[j]) for j in range(5)]
        if min(legs) <= 1e-12:
            continue
        valid, _ = _valid_impulse(seq)
        if valid:
            continue
        if direction > 0:
            violations = (
                prices[2] <= prices[0]          # wave 2 breaches wave-1 origin
                or prices[4] <= prices[1]       # wave 4 overlaps wave-1 territory
                or legs[2] < min(legs[0], legs[4]) - 1e-9
                or prices[5] <= prices[3]       # truncated/failed fifth wave
            )
        else:
            violations = (
                prices[2] >= prices[0]
                or prices[4] >= prices[1]
                or legs[2] < min(legs[0], legs[4]) - 1e-9
                or prices[5] >= prices[3]
            )
        if not violations:
            continue
        lines = tuple(
            ((float(seq[j].index), float(seq[j].price)),
             (float(seq[j + 1].index), float(seq[j + 1].price)))
            for j in range(5)
        )
        out.append(ChartPattern(
            "irregular_12345", "12345不规则型", int(seq[0].index), int(seq[-1].index),
            lines, markers=tuple(seq[1:]),
            label_index=int(seq[-1].index), label_price=float(seq[-1].price),
            direction=direction, confidence=0.76,
            marker_labels=("1", "2", "3", "4", "5"),
        ))
    return out


def _detect_trend_guides(
    pivots: list[Pivot], positions: np.ndarray, atr: np.ndarray, close: np.ndarray,
) -> list[ChartPattern]:
    """Return the latest parallel price channel and its mid-trendline, or one
    well-supported rising-support/falling-resistance trendline.
    """
    if len(pivots) < 4 or len(positions) == 0:
        return []
    recent = pivots[-8:]
    highs = [q for q in recent if q.kind == "H"][-3:]
    lows = [q for q in recent if q.kind == "L"][-3:]
    x1 = int(positions[-1])
    atr_last = max(float(atr[-1]), 1e-9)
    last_close = max(abs(float(close[-1])), 1.0)

    if len(highs) >= 2 and len(lows) >= 2:
        top = _fit_pivot_boundary(highs)
        bottom = _fit_pivot_boundary(lows)
        if top is not None and bottom is not None:
            x0 = max(int(highs[0].index), int(lows[0].index))
            span = x1 - x0
            if span >= 6:
                top0, top1 = _line_value(top, x0), _line_value(top, x1)
                bot0, bot1 = _line_value(bottom, x0), _line_value(bottom, x1)
                width0, width1 = top0 - bot0, top1 - bot1
                width_mid = max((width0 + width1) / 2.0, 1e-9)
                slope_tol = max(atr_last / span * 0.025, last_close * 1e-8)
                same_direction = (
                    (top[0] > slope_tol and bottom[0] > slope_tol)
                    or (top[0] < -slope_tol and bottom[0] < -slope_tol)
                )
                parallel_error = abs(top[0] - bottom[0]) * span
                residual_ok = max(top[2], bottom[2]) <= max(1.10 * atr_last, 0.20 * width_mid)
                parallel_ok = parallel_error <= max(0.85 * atr_last, 0.22 * width_mid)
                if width0 > 0 and width1 > 0 and same_direction and residual_ok and parallel_ok:
                    direction = 1 if (top[0] + bottom[0]) > 0 else -1
                    label = "上升通道" if direction > 0 else "下降通道"
                    channel = ChartPattern(
                        "trend_channel_up" if direction > 0 else "trend_channel_down",
                        label, x0, x1,
                        _chart_pattern_lines(top, bottom, x0, x1),
                        label_index=x1, label_price=top1,
                        direction=direction, confidence=0.79,
                    )
                    mid_line = (
                        (top[0] + bottom[0]) / 2.0,
                        (top[1] + bottom[1]) / 2.0,
                        0.0,
                    )
                    mid = ChartPattern(
                        "trendline_up" if direction > 0 else "trendline_down",
                        "上升趋势线" if direction > 0 else "下降趋势线",
                        x0, x1,
                        (((float(x0), _line_value(mid_line, x0)),
                          (float(x1), _line_value(mid_line, x1))),),
                        label_index=x1, label_price=_line_value(mid_line, x1),
                        direction=direction, confidence=0.77,
                    )
                    return [channel, mid]

    # If a parallel channel is not sufficiently supported, retain a single
    # trendline anchored by the latest three higher lows or lower highs.
    candidates: list[ChartPattern] = []
    slope_tol = max(atr_last / max(x1 - int(recent[0].index), 1) * 0.025, last_close * 1e-8)
    if len(lows) >= 2:
        line = _fit_pivot_boundary(lows)
        if line is not None and line[0] > slope_tol:
            monotonic = all(lows[k + 1].price > lows[k].price for k in range(len(lows) - 1))
            if monotonic:
                x0 = int(lows[0].index)
                candidates.append(ChartPattern(
                    "trendline_up", "上升趋势线", x0, x1,
                    (((float(x0), _line_value(line, x0)),
                      (float(x1), _line_value(line, x1))),),
                    label_index=x1, label_price=_line_value(line, x1),
                    direction=1, confidence=0.70,
                ))
    if len(highs) >= 2:
        line = _fit_pivot_boundary(highs)
        if line is not None and line[0] < -slope_tol:
            monotonic = all(highs[k + 1].price < highs[k].price for k in range(len(highs) - 1))
            if monotonic:
                x0 = int(highs[0].index)
                candidates.append(ChartPattern(
                    "trendline_down", "下降趋势线", x0, x1,
                    (((float(x0), _line_value(line, x0)),
                      (float(x1), _line_value(line, x1))),),
                    label_index=x1, label_price=_line_value(line, x1),
                    direction=-1, confidence=0.70,
                ))
    if not candidates:
        return []
    # Prefer the line whose fitted pivots are most coherent, with a small
    # preference for the latest support/resistance anchor.
    return [max(candidates, key=lambda q: (q.confidence, q.start_index))]


def _polyline_segments(
    points: list[tuple[float, float]],
) -> tuple[tuple[tuple[float, float], tuple[float, float]], ...]:
    """Convert a sequence of points into drawable straight-line segments."""
    return tuple(
        (points[i], points[i + 1])
        for i in range(len(points) - 1)
    )


def _smooth_prices(values: np.ndarray, window: int = 5) -> np.ndarray:
    """Small centered moving average used only for geometric shape fitting."""
    if len(values) < 3:
        return values.astype(float, copy=True)
    width = min(max(3, int(window)), len(values))
    if width % 2 == 0:
        width -= 1
    return (
        pd.Series(values.astype(float))
        .rolling(width, center=True, min_periods=1)
        .mean()
        .to_numpy(dtype=float)
    )


def _quadratic_shape(
    close: np.ndarray, left: int, right: int, atr: np.ndarray,
) -> dict[str, float | np.ndarray] | None:
    """Fit a quadratic U/inverted-U to a local close series."""
    if right <= left or right - left + 1 < 12:
        return None
    values = close[left:right + 1]
    if not np.isfinite(values).all():
        return None
    smooth = _smooth_prices(values, window=max(3, min(7, len(values) // 7 * 2 + 1)))
    xx = np.linspace(-1.0, 1.0, len(smooth))
    coeff = np.polyfit(xx, smooth, 2)
    fitted = np.polyval(coeff, xx)
    residual = float(np.sqrt(np.mean((smooth - fitted) ** 2)))
    total = float(np.sum((smooth - np.mean(smooth)) ** 2))
    r2 = 1.0 - float(np.sum((smooth - fitted) ** 2)) / max(total, 1e-12)
    a, b, _c = [float(v) for v in coeff]
    if abs(a) <= 1e-12:
        return None
    vertex_x = -b / (2.0 * a)
    vertex_rel = (vertex_x + 1.0) / 2.0
    vertex_price = float(np.polyval(coeff, vertex_x))
    edge_mean = float((fitted[0] + fitted[-1]) / 2.0)
    curvature_depth = edge_mean - vertex_price if a > 0 else vertex_price - edge_mean
    local_atr = float(np.nanmedian(atr[left:right + 1]))
    local_atr = max(local_atr if isfinite(local_atr) else 0.0, 1e-9)
    median_price = max(float(np.nanmedian(np.abs(values))), 1.0)
    endpoint_gap = abs(float(smooth[0] - smooth[-1]))
    quarter = max(2, len(smooth) // 4)
    left_slope_atr = abs(float(smooth[quarter] - smooth[0])) / (quarter * local_atr)
    right_slope_atr = abs(float(smooth[-1] - smooth[-1 - quarter])) / (quarter * local_atr)
    return {
        "coeff": coeff,
        "fitted": fitted,
        "smooth": smooth,
        "r2": r2,
        "residual": residual,
        "vertex_rel": vertex_rel,
        "vertex_price": vertex_price,
        "depth": float(curvature_depth),
        "atr": local_atr,
        "median_price": median_price,
        "endpoint_gap": endpoint_gap,
        "left_slope_atr": left_slope_atr,
        "right_slope_atr": right_slope_atr,
    }


def _detect_turning_shapes(
    pivots: list[Pivot],
    positions: np.ndarray,
    close: np.ndarray,
    atr: np.ndarray,
) -> tuple[list[ChartPattern], set[tuple[int, int, int]]]:
    """Detect V turns, rounded arcs and cup/handle formations.

    Curves are fitted to smoothed closes between confirmed pivots.  All
    recognition is causal: only bars already present in the replay frame are
    inspected.  The returned triples suppress conflicting generic M/W labels
    when the same H-L-H or L-H-L structure is better described as a curve.
    """
    out: list[ChartPattern] = []
    curved_triples: set[tuple[int, int, int]] = set()

    for i in range(len(pivots) - 2):
        p1, p2, p3 = pivots[i:i + 3]
        is_bottom = p1.kind == "H" and p2.kind == "L" and p3.kind == "H"
        is_top = p1.kind == "L" and p2.kind == "H" and p3.kind == "L"
        if not (is_bottom or is_top):
            continue
        if p3.index - p1.index < 8:
            continue

        i0 = min(max(_local_position(positions, p1.index), 0), len(close) - 1)
        i2 = min(max(_local_position(positions, p3.index), 0), len(close) - 1)
        im = min(max(_local_position(positions, p2.index), 0), len(close) - 1)
        if i2 - i0 < 7 or not (i0 <= im <= i2):
            continue

        atr_ref = max(float(np.nanmedian(atr[i0:i2 + 1])), 1e-9)
        left_move = abs(float(p2.price) - float(p1.price))
        right_move = abs(float(p3.price) - float(p2.price))
        depth = min(left_move, right_move)
        median_price = max(abs(float(np.nanmedian(close[i0:i2 + 1]))), 1.0)
        if depth < max(1.6 * atr_ref, 0.003 * median_price):
            continue

        fit = _quadratic_shape(close, i0, i2, atr)
        curve_ok = False
        if fit is not None:
            expected_sign = 1.0 if is_bottom else -1.0
            vertex_rel = float(fit["vertex_rel"])
            pivot_rel = (im - i0) / max(i2 - i0, 1)
            curve_ok = (
                expected_sign * float(np.polyfit(
                    np.linspace(-1.0, 1.0, len(fit["smooth"])),
                    fit["smooth"], 2
                )[0]) > 0
                and 0.22 <= vertex_rel <= 0.78
                and abs(vertex_rel - pivot_rel) <= 0.24
                and float(fit["r2"]) >= 0.56
                and float(fit["depth"]) >= max(1.5 * atr_ref, 0.003 * median_price)
                and float(fit["endpoint_gap"]) <= max(0.70 * float(fit["depth"]), 1.5 * atr_ref)
                and float(fit["residual"]) <= max(0.55 * float(fit["depth"]), 1.0 * atr_ref)
                and max(float(fit["left_slope_atr"]), float(fit["right_slope_atr"])) <= 1.35
            )

        # Cup-and-handle: left rim -> rounded bowl -> right rim -> shallow handle.
        # The first three pivots are the cup; a fourth low pivot is the handle.
        if is_bottom and curve_ok:
            rim_mean = (float(p1.price) + float(p3.price)) / 2.0
            cup_depth = rim_mean - float(p2.price)
            rim_tolerance = max(1.0 * atr_ref, 0.07 * cup_depth, 0.003 * rim_mean)
            cup_duration = p3.index - p1.index
            if (
                cup_duration >= 18
                and cup_depth >= max(2.0 * atr_ref, 0.006 * rim_mean)
                and abs(float(p1.price) - float(p3.price)) <= rim_tolerance
            ):
                handle = None
                if i + 3 < len(pivots):
                    p4 = pivots[i + 3]
                    handle_drop = float(p3.price) - float(p4.price)
                    handle_fraction = handle_drop / max(cup_depth, 1e-12)
                    if (
                        p4.kind == "L"
                        and p4.index > p3.index
                        and 0.08 <= handle_fraction <= 0.50
                        and float(p4.price) >= float(p2.price) + 0.50 * cup_depth
                        and p4.index - p3.index <= max(8, int(0.55 * cup_duration))
                    ):
                        handle = p4

                after_pivot = _local_position(positions, handle.index if handle else p3.index) + 1
                rim_level = max(float(p1.price), float(p3.price))
                breakout_hits = (
                    np.flatnonzero(close[after_pivot:] > rim_level + 0.10 * atr[after_pivot:])
                    if after_pivot < len(close) else np.array([], dtype=int)
                )
                confirmed = len(breakout_hits) > 0
                breakout_local = after_pivot + int(breakout_hits[0]) if confirmed else None
                end_index = int(positions[-1])
                curve_points: list[tuple[float, float]] = []
                cup_span = max(float(p3.index - p1.index), 1.0)
                cup_x = np.asarray([
                    0.0,
                    float(p2.index - p1.index) / cup_span,
                    1.0,
                ])
                coeff = np.polyfit(
                    cup_x,
                    np.asarray([p1.price, p2.price, p3.price], dtype=float),
                    2,
                )
                for t in np.linspace(0.0, 1.0, 15):
                    gx = float(p1.index + t * cup_span)
                    curve_points.append((gx, float(np.polyval(coeff, t))))
                lines = list(_polyline_segments(curve_points))
                markers = [p1, p2, p3]
                marker_labels = ["左杯沿", "杯底", "右杯沿"]
                if handle is not None:
                    lines.append(((float(p3.index), float(p3.price)),
                                  (float(handle.index), float(handle.price))))
                    markers.append(handle)
                    marker_labels.append("把手")
                lines.append(((float(p3.index), rim_level), (float(end_index), rim_level)))
                label_index = int(positions[breakout_local]) if confirmed else (
                    handle.index if handle is not None else p3.index
                )
                label_price = float(close[breakout_local]) if confirmed else (
                    float(handle.price) if handle is not None else float(p3.price)
                )
                label = "杯形突破确认" if confirmed else (
                    "杯柄候选" if handle is not None else "杯形候选"
                )
                out.append(ChartPattern(
                    "cup", label, p1.index, end_index, tuple(lines),
                    markers=tuple(markers), label_index=label_index,
                    label_price=label_price, direction=1,
                    confidence=0.91 if confirmed else 0.78,
                    marker_labels=tuple(marker_labels),
                    target_price=None,
                ))
                curved_triples.add((p1.index, p2.index, p3.index))
                continue

        if curve_ok and fit is not None:
            # Rounded arc (bottom/top): draw a sampled fitted curve rather than
            # a straight V connector.  Cup-like bottoms have already been
            # classified above and are not duplicated here.
            fitted = np.asarray(fit["fitted"], dtype=float)
            curve_points = [
                (
                    float(positions[i0] + (positions[i2] - positions[i0]) * k / max(len(fitted) - 1, 1)),
                    float(fitted[k]),
                )
                for k in range(len(fitted))
            ]
            lines = _polyline_segments(curve_points[::max(1, len(curve_points) // 16)])
            out.append(ChartPattern(
                "rounded_bottom" if is_bottom else "rounded_top",
                "圆弧底" if is_bottom else "圆弧顶",
                p1.index, p3.index, lines,
                markers=(p1, p2, p3),
                label_index=p2.index,
                label_price=float(p2.price),
                direction=1 if is_bottom else -1,
                confidence=0.80,
                marker_labels=("左沿", "弧底" if is_bottom else "弧顶", "右沿"),
            ))
            curved_triples.add((p1.index, p2.index, p3.index))
            continue

        # V-shape: two substantial, relatively fast legs meeting at one pivot.
        left_bars = max(1, p2.index - p1.index)
        right_bars = max(1, p3.index - p2.index)
        left_speed = left_move / (left_bars * atr_ref)
        right_speed = right_move / (right_bars * atr_ref)
        amplitude_ratio = right_move / max(left_move, 1e-12)
        duration_ratio = right_bars / max(left_bars, 1)
        # Validate that the bar closes actually travel into and away from the
        # pivot.  This prevents stale/manual pivots over a flat price series
        # from being mislabeled as a V reversal.
        left_start_close = float(np.nanmedian(close[i0:min(i0 + 3, im + 1)]))
        left_end_close = float(np.nanmedian(close[max(i0, im - 2):im + 1]))
        right_start_close = float(np.nanmedian(close[im:min(im + 3, i2 + 1)]))
        right_end_close = float(np.nanmedian(close[max(im, i2 - 2):i2 + 1]))
        left_close_progress = abs(left_end_close - left_start_close)
        right_close_progress = abs(right_end_close - right_start_close)
        close_path_ok = (
            left_close_progress >= 0.22 * left_move
            and right_close_progress >= 0.22 * right_move
        )
        endpoint_gap = abs(float(p1.price) - float(p3.price))
        if (
            close_path_ok
            and endpoint_gap >= max(0.90 * atr_ref, 0.0030 * median_price)
            and left_move >= max(1.8 * atr_ref, 0.004 * median_price)
            and right_move >= max(1.8 * atr_ref, 0.004 * median_price)
            and left_speed >= 0.18 and right_speed >= 0.18
            and 0.30 <= amplitude_ratio <= 2.8
            and 0.20 <= duration_ratio <= 5.0
            and p3.index - p1.index <= 80
        ):
            label = "V形底" if is_bottom else "倒V形顶"
            out.append(ChartPattern(
                "v_bottom" if is_bottom else "v_top",
                label, p1.index, p3.index,
                (
                    ((float(p1.index), float(p1.price)), (float(p2.index), float(p2.price))),
                    ((float(p2.index), float(p2.price)), (float(p3.index), float(p3.price))),
                ),
                markers=(p1, p2, p3),
                label_index=p2.index, label_price=float(p2.price),
                direction=1 if is_bottom else -1,
                confidence=0.76,
                marker_labels=("左侧", "V点", "右侧"),
            ))

    return out, curved_triples


def _detect_2b_reversals(
    pivots: list[Pivot],
    positions: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    atr: np.ndarray,
) -> list[ChartPattern]:
    """Detect 2B false breakouts of a prior confirmed swing high/low."""
    out: list[ChartPattern] = []
    if len(pivots) < 1:
        return out
    for pivot in pivots[-14:]:
        pivot_local = min(max(_local_position(positions, pivot.index), 0), len(close) - 1)
        after = pivot_local + 1
        if after >= len(close):
            continue
        level = float(pivot.price)
        if pivot.kind == "H":
            for breakout_local in range(after, len(close)):
                margin = max(0.06 * float(atr[breakout_local]), 0.0006 * abs(level))
                if high[breakout_local] <= level + margin:
                    continue
                search_end = min(len(close), breakout_local + 7)
                return_margin = max(0.04 * float(atr[breakout_local]), 0.0003 * abs(level))
                hits = np.flatnonzero(close[breakout_local:search_end] < level - return_margin)
                if not len(hits):
                    break
                return_local = breakout_local + int(hits[0])
                extreme_local = breakout_local + int(np.argmax(high[breakout_local:return_local + 1]))
                extreme = float(high[extreme_local])
                false_pivot = Pivot(int(positions[extreme_local]), extreme, "H")
                return_pivot = Pivot(int(positions[return_local]), float(close[return_local]), "L")
                end_index = int(positions[return_local])
                out.append(ChartPattern(
                    "two_b_top", "2B顶部反转",
                    pivot.index, end_index,
                    (
                        ((float(pivot.index), level), (float(end_index), level)),
                        ((float(pivot.index), level), (float(false_pivot.index), extreme)),
                        ((float(false_pivot.index), extreme),
                         (float(return_pivot.index), float(return_pivot.price))),
                    ),
                    markers=(pivot, false_pivot, return_pivot),
                    label_index=end_index, label_price=float(close[return_local]),
                    direction=-1, confidence=0.92,
                    marker_labels=("前高", "假突破", "收回"),
                ))
                break
        elif pivot.kind == "L":
            for breakout_local in range(after, len(close)):
                margin = max(0.06 * float(atr[breakout_local]), 0.0006 * abs(level))
                if low[breakout_local] >= level - margin:
                    continue
                search_end = min(len(close), breakout_local + 7)
                return_margin = max(0.04 * float(atr[breakout_local]), 0.0003 * abs(level))
                hits = np.flatnonzero(close[breakout_local:search_end] > level + return_margin)
                if not len(hits):
                    break
                return_local = breakout_local + int(hits[0])
                extreme_local = breakout_local + int(np.argmin(low[breakout_local:return_local + 1]))
                extreme = float(low[extreme_local])
                false_pivot = Pivot(int(positions[extreme_local]), extreme, "L")
                return_pivot = Pivot(int(positions[return_local]), float(close[return_local]), "H")
                end_index = int(positions[return_local])
                out.append(ChartPattern(
                    "two_b_bottom", "2B底部反转",
                    pivot.index, end_index,
                    (
                        ((float(pivot.index), level), (float(end_index), level)),
                        ((float(pivot.index), level), (float(false_pivot.index), extreme)),
                        ((float(false_pivot.index), extreme),
                         (float(return_pivot.index), float(return_pivot.price))),
                    ),
                    markers=(pivot, false_pivot, return_pivot),
                    label_index=end_index, label_price=float(close[return_local]),
                    direction=1, confidence=0.92,
                    marker_labels=("前低", "假跌破", "收回"),
                ))
                break
    # Keep the most recent top and bottom reversal only; old events remain
    # visible through the existing confirmed ZigZag/history layers.
    latest_by_kind: dict[str, ChartPattern] = {}
    for pattern in out:
        old = latest_by_kind.get(pattern.kind)
        if old is None or pattern.end_index > old.end_index:
            latest_by_kind[pattern.kind] = pattern
    return list(latest_by_kind.values())


def detect_chart_patterns(
    df: pd.DataFrame,
    pivots: list[Pivot],
    max_patterns: int = 24,
) -> list[ChartPattern]:
    """Detect common correction geometries using confirmed swing pivots.

    These are rule-based candidates, not a unique chart interpretation.  A
    pattern is emitted only when its boundaries have enough pivot contacts and
    a minimum geometric fit.  1-2-3 structures are marked as candidates until
    price closes through point 2, at which point they are marked confirmed.
    """
    required = {"high", "low", "close"}
    if df is None or df.empty or not required.issubset(df.columns) or len(pivots) < 2:
        return []

    x = df.reset_index(drop=True)
    if "_wave_position" in x.columns:
        positions = pd.to_numeric(x["_wave_position"], errors="coerce").fillna(
            pd.Series(np.arange(len(x)), index=x.index)
        ).to_numpy(dtype=int)
    else:
        positions = np.arange(len(x), dtype=int)
    high = pd.to_numeric(x["high"], errors="coerce").to_numpy(float)
    low = pd.to_numeric(x["low"], errors="coerce").to_numpy(float)
    close = pd.to_numeric(x["close"], errors="coerce").to_numpy(float)
    atr = _atr(x)
    if not (np.isfinite(high).all() and np.isfinite(low).all() and np.isfinite(close).all()):
        return []

    candidates: list[ChartPattern] = []

    # Curved reversals and false-breakout reversals are independent of the
    # six-pivot geometry classifier.  Prefer these specific labels over generic
    # M/W labels when the same three pivots form a rounded cup/arc.
    turning_shapes, curved_triples = _detect_turning_shapes(
        pivots, positions, close, atr
    )
    candidates.extend(turning_shapes)
    candidates.extend(_detect_2b_reversals(
        pivots, positions, high, low, close, atr
    ))

    # Geometric structures are fitted to rolling five- or six-pivot windows.
    # Five alternating pivots still provide at least two contacts per boundary
    # and allow early recognition without waiting for an extra full swing.
    for window_size in (5, 6):
        for j in range(window_size, len(pivots) + 1):
            w = pivots[j - window_size:j]
            if any(w[k].kind == w[k + 1].kind for k in range(len(w) - 1)):
                continue
            highs = [v for v in w if v.kind == "H"]
            lows = [v for v in w if v.kind == "L"]
            if len(highs) < 2 or len(lows) < 2:
                continue
            x0, x1 = int(w[0].index), int(w[-1].index)
            span = x1 - x0
            if span < 8:
                continue
            top = _fit_pivot_boundary(highs)
            bottom = _fit_pivot_boundary(lows)
            if top is None or bottom is None:
                continue
            top0, top1 = _line_value(top, x0), _line_value(top, x1)
            bot0, bot1 = _line_value(bottom, x0), _line_value(bottom, x1)
            width0, width1 = top0 - bot0, top1 - bot1
            full_width_valid = min(width0, width1) > 0
            mid_price = float(np.median([v.price for v in w]))
            local_i = min(max(_local_position(positions, x1), 0), len(atr) - 1)
            atr_tol = max(float(atr[local_i]) * 0.55, abs(mid_price) * 0.0020, 1e-9)
            flat_tol = max(float(atr[local_i]) * 1.25, abs(mid_price) * 0.0030, 1e-9)
            slope_tol = atr_tol / max(span, 1)
            fit_ok = top[2] <= max(width0, width1) * 0.36 and bottom[2] <= max(width0, width1) * 0.36

            pattern: ChartPattern | None = None
            top_change, bot_change = top1 - top0, bot1 - bot0
            width_ratio = width1 / width0 if full_width_valid and width0 > 0 else -1.0

            # Flag: a strong pole followed by a short, relatively narrow, parallel
            # counter-trend channel.  It is tested before wedges/triangles.
            p0, p1 = w[0], w[1]
            pole = float(p1.price) - float(p0.price)
            pole_bars = max(1, p1.index - p0.index)
            cons_bars = max(1, w[-1].index - p1.index)
            cons = w[1:]
            cons_highs = [v for v in cons if v.kind == "H"]
            cons_lows = [v for v in cons if v.kind == "L"]
            ch_top = _fit_pivot_boundary(cons_highs)
            ch_bot = _fit_pivot_boundary(cons_lows)
            if ch_top and ch_bot and len(cons_highs) >= 2 and len(cons_lows) >= 2:
                cx0, cx1 = p1.index, w[-1].index
                ctop0, ctop1 = _line_value(ch_top, cx0), _line_value(ch_top, cx1)
                cbot0, cbot1 = _line_value(ch_bot, cx0), _line_value(ch_bot, cx1)
                channel_width = max(ctop0 - cbot0, ctop1 - cbot1)
                pole_i = min(max(_local_position(positions, p1.index), 0), len(atr) - 1)
                strong_pole = abs(pole) >= 1.8 * max(float(atr[pole_i]), 1e-9)
                parallel = abs(ch_top[0] - ch_bot[0]) * cons_bars <= max(atr_tol, 0.35 * max(channel_width, 1e-9))
                counter = (
                    (pole > 0 and ch_top[0] <= slope_tol and ch_bot[0] <= slope_tol)
                    or (pole < 0 and ch_top[0] >= -slope_tol and ch_bot[0] >= -slope_tol)
                )
                if (
                    strong_pole and channel_width > 0 and channel_width <= abs(pole) * 0.80
                    and 0.25 <= cons_bars / pole_bars <= 4.0 and parallel and counter
                ):
                    direction = 1 if pole > 0 else -1
                    label = "看涨旗形" if direction > 0 else "看跌旗形"
                    pattern = ChartPattern(
                        "flag", label, p0.index, w[-1].index,
                        _chart_pattern_lines(ch_top, ch_bot, cx0, cx1),
                        label_index=w[-1].index,
                        label_price=ctop1 if direction < 0 else cbot1,
                        direction=direction, confidence=0.78,
                    )

            # The remaining formations require both full-window boundaries to fit.
            if pattern is None and (not fit_ok or not full_width_valid):
                continue

            # Broadening formation: highs rise while lows fall and the range expands.
            if pattern is None and top[0] > slope_tol and bottom[0] < -slope_tol and width_ratio >= 1.10:
                pattern = ChartPattern(
                    "expanding_triangle", "外扩三角形", x0, x1,
                    _chart_pattern_lines(top, bottom, x0, x1),
                    label_index=x1, label_price=top1,
                    direction=0, confidence=min(0.95, 0.65 + min(width_ratio - 1.10, 0.3)),
                )

            # Converging structures: triangles and wedges are separated by slope signs.
            if pattern is None and 0.0 < width_ratio <= 0.90:
                rising_top = top[0] > slope_tol
                rising_bottom = bottom[0] > slope_tol
                falling_top = top[0] < -slope_tol
                falling_bottom = bottom[0] < -slope_tol
                if abs(top_change) <= flat_tol and rising_bottom:
                    label, kind, direction = "上升三角形", "triangle_ascending", 1
                elif falling_top and abs(bot_change) <= flat_tol:
                    label, kind, direction = "下降三角形", "triangle_descending", -1
                elif falling_top and rising_bottom:
                    label, kind, direction = "收敛三角形", "triangle", 0
                elif rising_top and rising_bottom and bottom[0] > top[0] + slope_tol:
                    label, kind, direction = "上升楔形", "rising_wedge", -1
                elif falling_top and falling_bottom and top[0] < bottom[0] - slope_tol:
                    label, kind, direction = "下降楔形", "falling_wedge", 1
                else:
                    label = kind = None
                    direction = 0
                if label is not None:
                    # A converging pair should intersect to the right of the window.
                    den = top[0] - bottom[0]
                    cross_x = (bottom[1] - top[1]) / den if abs(den) > 1e-12 else -1.0
                    if cross_x > x1:
                        pattern = ChartPattern(
                            kind, label, x0, x1,
                            _chart_pattern_lines(top, bottom, x0, x1),
                            label_index=x1,
                            label_price=top1 if direction <= 0 else bot1,
                            direction=direction, confidence=0.72,
                        )

            # A horizontal trading range / box has nearly flat, stable boundaries.
            if pattern is None and (
                abs(top_change) <= flat_tol and abs(bot_change) <= flat_tol
                and abs(width1 - width0) <= max(width0 * 0.25, flat_tol)
            ):
                pattern = ChartPattern(
                    "box", "箱形整理", x0, x1,
                    _chart_pattern_lines(top, bottom, x0, x1),
                    label_index=x1, label_price=top1,
                    direction=0, confidence=0.68,
                )

            if pattern is not None:
                candidates.append(pattern)

    # M/W reversal structures use three confirmed alternating pivots:
    # H-L-H (double top) and L-H-L (double bottom).  The middle pivot is the
    # neckline. A close through the neckline confirms the pattern; otherwise
    # it remains a candidate. The measured-move target projects the average
    # extreme-to-neckline height from the neckline.
    double_pivot_triples: set[tuple[int, int, int]] = set()
    for i in range(len(pivots) - 2):
        p1, p2, p3 = pivots[i:i + 3]
        if p3.index - p1.index < 5 or p2.index - p1.index < 2 or p3.index - p2.index < 2:
            continue
        if (p1.index, p2.index, p3.index) in curved_triples:
            continue

        local_points = [
            min(max(_local_position(positions, q.index), 0), len(atr) - 1)
            for q in (p1, p2, p3)
        ]
        atr_ref = max(float(atr[k]) for k in local_points)
        if not isfinite(atr_ref) or atr_ref <= 0:
            continue

        if p1.kind == "H" and p2.kind == "L" and p3.kind == "H":
            extreme_mid = (float(p1.price) + float(p3.price)) / 2.0
            extreme_diff = abs(float(p1.price) - float(p3.price))
            tolerance = min(
                max(0.90 * atr_ref, 0.0035 * abs(extreme_mid)),
                0.025 * max(abs(extreme_mid), 1e-9),
            )
            height = extreme_mid - float(p2.price)
            min_height = max(1.2 * atr_ref, 0.0035 * max(abs(extreme_mid), 1e-9))
            if extreme_diff > tolerance or height < min_height:
                continue

            kind, direction = "double_top", -1
            neckline = float(p2.price)
            target_price = neckline - height
            after = _local_position(positions, p3.index) + 1
            hits = np.flatnonzero(
                close[after:] < (neckline - 0.10 * atr[after:])
            ) if after < len(close) else np.array([], dtype=int)
            if len(hits):
                breakout_local = after + int(hits[0])
                breakout_index = int(positions[breakout_local])
                label, label_index = "M型确认", breakout_index
                label_price, confidence = float(close[breakout_local]), 0.91
            else:
                breakout_index = int(positions[-1])
                label, label_index = "M型候选", p3.index
                label_price, confidence = float(p3.price), 0.74

            end_index = int(positions[-1])
            lines = (
                ((float(p1.index), float(p1.price)), (float(p3.index), float(p3.price))),
                ((float(p2.index), neckline), (float(end_index), neckline)),
                ((float(breakout_index if label == "M型确认" else p3.index), target_price),
                 (float(end_index), target_price)),
            )
            candidates.append(ChartPattern(
                kind, label, p1.index, end_index, lines,
                markers=(p1, p2, p3),
                label_index=label_index, label_price=label_price,
                direction=direction, confidence=confidence,
                marker_labels=("M1", "颈线", "M2"),
                target_price=float(target_price),
            ))
            double_pivot_triples.add((p1.index, p2.index, p3.index))

        elif p1.kind == "L" and p2.kind == "H" and p3.kind == "L":
            extreme_mid = (float(p1.price) + float(p3.price)) / 2.0
            extreme_diff = abs(float(p1.price) - float(p3.price))
            tolerance = min(
                max(0.90 * atr_ref, 0.0035 * abs(extreme_mid)),
                0.025 * max(abs(extreme_mid), 1e-9),
            )
            height = float(p2.price) - extreme_mid
            min_height = max(1.2 * atr_ref, 0.0035 * max(abs(extreme_mid), 1e-9))
            if extreme_diff > tolerance or height < min_height:
                continue

            kind, direction = "double_bottom", 1
            neckline = float(p2.price)
            target_price = neckline + height
            after = _local_position(positions, p3.index) + 1
            hits = np.flatnonzero(
                close[after:] > (neckline + 0.10 * atr[after:])
            ) if after < len(close) else np.array([], dtype=int)
            if len(hits):
                breakout_local = after + int(hits[0])
                breakout_index = int(positions[breakout_local])
                label, label_index = "W型确认", breakout_index
                label_price, confidence = float(close[breakout_local]), 0.91
            else:
                breakout_index = int(positions[-1])
                label, label_index = "W型候选", p3.index
                label_price, confidence = float(p3.price), 0.74

            end_index = int(positions[-1])
            lines = (
                ((float(p1.index), float(p1.price)), (float(p3.index), float(p3.price))),
                ((float(p2.index), neckline), (float(end_index), neckline)),
                ((float(breakout_index if label == "W型确认" else p3.index), target_price),
                 (float(end_index), target_price)),
            )
            candidates.append(ChartPattern(
                kind, label, p1.index, end_index, lines,
                markers=(p1, p2, p3),
                label_index=label_index, label_price=label_price,
                direction=direction, confidence=confidence,
                marker_labels=("W1", "颈线", "W2"),
                target_price=float(target_price),
            ))
            double_pivot_triples.add((p1.index, p2.index, p3.index))

    # A one-leg pullback is a simple correction.  The same three pivots are not
    # duplicated as a generic 1-2-3 pattern; the simple-correction label is more
    # informative for this structure.
    simple_patterns, simple_triples = _detect_simple_corrections(pivots, positions, close)
    candidates.extend(simple_patterns)

    # 1-2-3 correction/reversal: 1 is the first extreme, 2 the reaction point,
    # 3 a higher low/lower high. A close through point 2 upgrades it to confirmed.
    # If the same pivots already form an M/W or simple pullback, prefer that
    # explicit classification rather than duplicating the same structure.
    for i in range(len(pivots) - 2):
        p1, p2, p3 = pivots[i:i + 3]
        if (p1.index, p2.index, p3.index) in double_pivot_triples or (p1.index, p2.index, p3.index) in simple_triples:
            continue
        if p1.kind == p3.kind == "L" and p2.kind == "H" and p3.price > p1.price:
            direction = 1
            first_leg = p2.price - p1.price
            retrace = (p2.price - p3.price) / max(first_leg, 1e-12)
            trigger = p2.price
        elif p1.kind == p3.kind == "H" and p2.kind == "L" and p3.price < p1.price:
            direction = -1
            first_leg = p1.price - p2.price
            retrace = (p3.price - p2.price) / max(first_leg, 1e-12)
            trigger = p2.price
        else:
            continue
        if first_leg <= 1e-12 or not 0.236 <= retrace <= 0.786:
            continue
        after = _local_position(positions, p3.index) + 1
        if after >= len(close):
            continue
        if direction > 0:
            hits = np.flatnonzero(close[after:] > trigger)
        else:
            hits = np.flatnonzero(close[after:] < trigger)
        if len(hits):
            breakout_local = after + int(hits[0])
            end_index = int(positions[breakout_local])
            label = "123调整确认"
            confidence = 0.88
        else:
            end_index = int(positions[-1])
            label = "123调整候选"
            confidence = 0.62
        candidates.append(ChartPattern(
            "123", label, p1.index, end_index,
            (
                ((float(p1.index), float(p1.price)), (float(p2.index), float(p2.price))),
                ((float(p2.index), float(p2.price)), (float(p3.index), float(p3.price))),
                ((float(p2.index), float(p2.price)), (float(end_index), float(trigger))),
            ),
            markers=(p1, p2, p3),
            label_index=p3.index if not hits.size else end_index,
            label_price=float(p3.price),
            direction=direction, confidence=confidence,
        ))

    # Five alternating swings that fail one or more strict impulse rules are
    # displayed separately as an irregular 1-2-3-4-5 structure, never as a
    # standards-compliant impulse count.
    candidates.extend(_detect_irregular_12345(pivots))

    # Add only the latest well-supported trend guide(s), so the main chart gets
    # a useful channel/trendline without accumulating dozens of stale lines.
    trend_guides = _detect_trend_guides(pivots, positions, atr, close)

    # Suppress highly overlapping detections of the same geometry.  Keep the
    # higher-confidence/later representative and reserve room for trend guides.
    candidates.sort(key=lambda z: (z.confidence, z.end_index), reverse=True)
    selected: list[ChartPattern] = []
    for candidate in candidates:
        duplicate = False
        for old in selected:
            if old.kind != candidate.kind:
                continue
            inter = max(0, min(old.end_index, candidate.end_index) - max(old.start_index, candidate.start_index))
            union = max(old.end_index, candidate.end_index) - min(old.start_index, candidate.start_index)
            overlap = inter / max(union, 1)
            if overlap >= 0.55:
                duplicate = True
                break
        if not duplicate:
            selected.append(candidate)
        if len(selected) >= max(0, int(max_patterns) - len(trend_guides)):
            break
    selected.extend(trend_guides)
    return sorted(selected, key=lambda z: (z.end_index, z.start_index, z.kind))

def analyze_waves(df: pd.DataFrame) -> WaveAnalysis:
    pivots, preview = zigzag_pivots(df)
    labels: dict[int, str] = {}
    ratios: dict[int, str] = {}
    start, end, direction = _find_latest_impulse(pivots)
    if start is not None and end is not None:
        seq = pivots[start : end + 1]
        for wave_no, pivot in enumerate(seq[1:], start=1):
            labels[pivot.index] = str(wave_no)
        p = [float(v.price) for v in seq]
        w1 = abs(p[1] - p[0])
        r2 = _ratio(p[1] - p[2], w1)
        r3 = _ratio(p[3] - p[2], w1)
        r4 = _ratio(p[3] - p[4], abs(p[3] - p[2]))
        r5 = _ratio(p[5] - p[4], w1)
        if r2 is not None:
            ratios[seq[2].index] = f"回调 {r2:.1f}%"
        if r3 is not None:
            ratios[seq[3].index] = f"延伸 {r3:.1f}%"
        if r4 is not None:
            ratios[seq[4].index] = f"回调 {r4:.1f}%"
        if r5 is not None:
            ratios[seq[5].index] = f"延伸 {r5:.1f}%"

        # An ABC correction is labeled only when it follows the detected impulse.
        corr = pivots[end : end + 4]
        expected = "HLHL" if direction > 0 else "LHLH"
        if len(corr) == 4 and "".join(v.kind for v in corr) == expected:
            cp = [float(v.price) for v in corr]
            a_len = abs(cp[1] - cp[0])
            b_ratio = _ratio(cp[2] - cp[1], a_len)
            c_ratio = _ratio(cp[3] - cp[2], a_len)
            if a_len > 1e-12:
                labels[corr[1].index] = "A"
                labels[corr[2].index] = "B"
                labels[corr[3].index] = "C"
                if b_ratio is not None:
                    ratios[corr[2].index] = f"B回调 {b_ratio:.1f}%"
                if c_ratio is not None:
                    ratios[corr[3].index] = f"C延伸 {c_ratio:.1f}%"

    # Calculate the retracement at every C and the extension at every D, not
    # merely the ratios inside the latest Elliott 1-5 candidate.
    ratio_annotations = build_wave_ratio_annotations(pivots)

    # Retain the familiar impulse/ABC ratios and make sure they are also rendered.
    annotated_indices = {index for index, _text in ratio_annotations}
    for index, text in ratios.items():
        if index not in annotated_indices or str(text).startswith(("B回调", "C延伸")):
            ratio_annotations.append((index, text))
    deduped: list[tuple[int, str]] = []
    seen_ratio: set[tuple[int, str]] = set()
    for item in ratio_annotations:
        if item not in seen_ratio:
            deduped.append(item)
            seen_ratio.add(item)
    ratio_annotations = deduped

    projections = build_fibonacci_projections(pivots)
    projection = projections[-1] if projections else None

    # Pattern names are only useful as reversal context at actual swing turns.
    patterns = filter_patterns_at_pivots(
        detect_reversal_patterns(df), pivots, max_distance=3
    )
    pattern_pivots, _pattern_preview = zigzag_pivots(
        df, min_reversal_pct=0.0025, atr_multiple=0.80
    )
    # Keep the coarser wave count untouched; the lower-threshold pivot set is
    # used only to find smaller chart formations that the main ZigZag filters.
    chart_patterns = detect_chart_patterns(df, pattern_pivots)
    return WaveAnalysis(
        pivots=pivots, preview=preview, labels=labels, ratios=ratios,
        patterns=patterns, impulse_start=start, impulse_end=end,
        projection=projection, projections=projections,
        ratio_annotations=ratio_annotations, chart_patterns=chart_patterns,
    )
