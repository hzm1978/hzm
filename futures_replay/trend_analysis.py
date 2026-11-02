from __future__ import annotations

"""Third-edition trend-trading analysis, based exclusively on 3.pdf.

Interval degree -> correction end/A -> wave structure -> line/ratio/candle
context. Only supplied closed bars are read; forming bars are handled by the
chart as a provisional tip. See 指标规则说明.md for source pages/parameters.
"""

from dataclasses import dataclass, replace
from math import isfinite

import numpy as np
import pandas as pd

from .trend_patterns import (
    TrendCorrection, TrendLabel, book_candle_patterns, correction_patterns,
    key_reversals, retracement_labels,
)


from .trend_types import TrendPivot
from .trend_settings import TrendSettings
from .trend_intervals import (BookInterval, WaveStructure, BoundaryPoint, BookLevel,
                              find_intervals, build_structure, compare_spans,
                              failed_interval_candidate, tp_ta)
from .trend_candles import standard_candles, StandardCandle
from .trend_corrections import correction_for_interval
from .trend_signals import BreakDecision, line_break_decision


@dataclass(frozen=True)
class TrendAnalysis:
    direction: str  # "up", "down", "sideways", "unknown"
    status: str
    pivots: tuple[TrendPivot, ...]
    trendline: tuple[TrendPivot, TrendPivot] | None
    trendline_slope: float | None
    trendline_value: float | None
    trendline_broken: bool
    channel_offset: float | None
    channel_value: float | None
    pivot_line_offset: float | None
    pivot_line_value: float | None
    pivot_line_broken: bool
    key_level: float | None
    key_level_kind: str | None
    key_level_index: int | None
    key_level_broken: bool
    breakout: str | None
    pullback_percent: float | None
    pullback_pivot: TrendPivot | None
    signal: str | None
    signal_index: int | None
    signal_pattern: str | None
    signal_price: float | None
    atr_value: float | None
    range_high: float | None
    range_low: float | None
    range_start_index: int | None
    as_of_index: int
    external_line: tuple[TrendPivot, TrendPivot] | None = None
    pivot_line_anchor: TrendPivot | None = None
    retracements: tuple[TrendLabel, ...] = ()
    reversals: tuple[TrendLabel, ...] = ()
    corrections: tuple[TrendCorrection, ...] = ()
    intervals: tuple[BookInterval, ...] = ()
    structure: WaveStructure | None = None
    boundaries: tuple[BoundaryPoint, ...] = ()
    levels: tuple[BookLevel, ...] = ()
    wave_labels: tuple[TrendLabel, ...] = ()
    candle_patterns: tuple[TrendLabel, ...] = ()
    standards: tuple[StandardCandle, ...] = ()
    navigation_pivots: tuple[TrendPivot, ...] = ()
    break_decision: BreakDecision | None = None
    failed_interval: BookInterval | None = None
    timing: tuple[str, ...] = ()
    active_tool: str = "待确认"
    line_degree: str = "级别待定"
    settings: TrendSettings = TrendSettings()
    navigation_line: tuple[TrendPivot, TrendPivot] | None = None
    navigation_offset: float | None = None
    navigation_anchor: TrendPivot | None = None
    angle_slope: float | None = None
    angle_anchor: TrendPivot | None = None


def _wilder_atr(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    period: int,
) -> np.ndarray:
    previous = np.concatenate(([close[0]], close[:-1]))
    tr = np.maximum(
        high - low,
        np.maximum(np.abs(high - previous), np.abs(low - previous)),
    )
    out = np.full(len(tr), np.nan, dtype=float)
    if not len(tr):
        return out
    seed_n = min(max(1, int(period)), len(tr))
    out[seed_n - 1] = float(np.mean(tr[:seed_n]))
    for i in range(seed_n, len(tr)):
        out[i] = (out[i - 1] * (period - 1) + tr[i]) / period
    out[:seed_n - 1] = np.cumsum(tr[:seed_n - 1]) / np.arange(1, seed_n)
    return out


def _confirmed_pivots(
    high: np.ndarray,
    low: np.ndarray,
    atr: np.ndarray,
    *,
    left: int,
    right: int,
    min_swing_atr: float,
    min_swing_percent: float,
) -> tuple[TrendPivot, ...]:
    """Return alternating local extrema, each delayed by ``right`` bars."""
    n = len(high)
    if n < left + right + 1:
        return ()

    candidates: list[TrendPivot] = []
    for i in range(left, n - right):
        hwin = high[i - left : i + right + 1]
        lwin = low[i - left : i + right + 1]

        # Asymmetric comparisons make ties deterministic: the candidate must
        # be at least as extreme as all bars to its left and strictly more
        # extreme than every bar to its right.
        is_high = high[i] >= float(np.max(hwin[:left])) and high[i] > float(np.max(hwin[left + 1 :]))
        is_low = low[i] <= float(np.min(lwin[:left])) and low[i] < float(np.min(lwin[left + 1 :]))
        if is_high == is_low:  # neither, or an ambiguous outside bar
            continue

        kind = "H" if is_high else "L"
        price = float(high[i] if is_high else low[i])
        candidates.append(TrendPivot(i, price, kind, i + right))

    pivots: list[TrendPivot] = []
    for point in candidates:
        if not pivots:
            pivots.append(point)
            continue

        previous = pivots[-1]
        if point.kind == previous.kind:
            more_extreme = (
                point.price > previous.price if point.kind == "H"
                else point.price < previous.price
            )
            if more_extreme:
                pivots[-1] = point
            continue

        local_atr = float(atr[point.index]) if isfinite(float(atr[point.index])) else 0.0
        threshold = max(
            local_atr * float(min_swing_atr),
            abs(point.price) * float(min_swing_percent),
            1e-12,
        )
        if abs(point.price - previous.price) >= threshold:
            pivots.append(point)

    return tuple(pivots)


def _line_value(a: TrendPivot, b: TrendPivot, index: int) -> tuple[float, float]:
    dx = int(b.index) - int(a.index)
    if dx == 0:
        return float(b.price), 0.0
    slope = (float(b.price) - float(a.price)) / dx
    return float(a.price) + slope * (int(index) - int(a.index)), float(slope)


def _select_valid_trendline(
    direction: str,
    pivots: tuple[TrendPivot, ...],
    high: np.ndarray,
    low: np.ndarray,
    atr: np.ndarray,
    global_to_local: dict[int, int],
    global_positions: np.ndarray,
    as_of_index: int,
    *,
    min_anchor_bars: int,
) -> tuple[TrendPivot, TrendPivot] | None:
    """Choose a book-style, non-penetrated trendline from confirmed pivots.

    Uptrend: low -> higher low, with a swing high between the anchors.
    Downtrend: high -> lower high, with a swing low between the anchors.
    The line is checked against all intervening candle extremes. The newest
    valid second anchor is preferred; longer spans break ties.
    """
    kind = "L" if direction == "up" else "H"
    candidates = [p for p in pivots if p.kind == kind and p.index in global_to_local][-14:]
    if len(candidates) < 2:
        return None

    opposite = "H" if direction == "up" else "L"
    other_pivots = [p for p in pivots if p.kind == opposite][-20:]
    valid: list[tuple[tuple[int, int, int], TrendPivot, TrendPivot]] = []

    for i, a in enumerate(candidates[:-1]):
        for b in candidates[i + 1 :]:
            span = int(b.index) - int(a.index)
            if span < min_anchor_bars:
                continue

            if direction == "up" and b.price <= a.price:
                continue
            if direction == "down" and b.price >= a.price:
                continue

            between = [p for p in other_pivots if a.index < p.index < b.index]
            if not between:
                continue

            local_a = global_to_local[a.index]
            local_b = global_to_local[b.index]
            # Third edition pp. 308–311: the second anchor precedes the trend's
            # extreme. A 78.6% recovery after the counter-turn permits a failed
            # fifth-wave interpretation and its post-extreme anchor.
            extreme_local = local_a + int(np.argmax(high[local_a:]) if direction == "up" else np.argmin(low[local_a:]))
            if b.index >= int(global_positions[extreme_local]):
                sign = 1 if direction == "up" else -1
                extreme_price = float(high[extreme_local] if sign > 0 else low[extreme_local])
                recovery_price = float(np.max(high[local_b + 1:]) if sign > 0 else np.min(low[local_b + 1:])) if local_b + 1 < len(high) else b.price
                height = sign * (extreme_price - b.price)
                if height <= 0 or sign * (recovery_price - b.price) < .786 * height:
                    continue
            line_a, slope = _line_value(a, b, b.index)
            if direction == "up" and slope <= 0:
                continue
            if direction == "down" and slope >= 0:
                continue

            # The line may be tested only between its two construction points.
            # A later penetration is kept and reported as a break, not used to
            # erase the historical trendline.
            crossed = False
            for local_i in range(local_a, local_b + 1):
                global_i = int(global_positions[local_i])
                line_price = float(a.price) + slope * (global_i - int(a.index))
                tolerance = max(abs(line_price) * 1e-10, 1e-12)
                if direction == "up" and float(low[local_i]) < line_price - tolerance:
                    crossed = True
                    break
                if direction == "down" and float(high[local_i]) > line_price + tolerance:
                    crossed = True
                    break
            if crossed:
                continue

            touches = 0
            for p in candidates:
                if a.index <= p.index <= b.index:
                    line_price, _ = _line_value(a, b, p.index)
                    if abs(p.price - line_price) <= max(
                        float(atr[global_to_local[p.index]]) * 0.35,
                        abs(p.price) * 0.0002,
                    ):
                        touches += 1

            # Prefer the latest valid second anchor, then more touches, then
            # a wider time span. This avoids choosing an obsolete early line.
            score = (int(b.index), int(touches), int(span))
            valid.append((score, a, b))

    if not valid:
        return None
    _score, a, b = max(valid, key=lambda item: item[0])
    return a, b


def _select_external_channel(direction, pivots, high, low, atr, global_to_local):
    """Third edition pp. 346–363: find the opposite exterior, then shift through D.

    Rising trend: connect two exterior highs C/E and shift through the low D
    between them. Falling trend: connect two exterior lows and shift through
    the intervening high. Its slope need not equal the primary trendline.
    """
    kind = "H" if direction == "up" else "L"
    exterior = [p for p in pivots if p.kind == kind][-14:]
    local_to_global = {local: global_index for global_index, local in global_to_local.items()}
    candidates = []
    for i, a in enumerate(exterior[:-1]):
        for b in exterior[i + 1:]:
            if (b.price - a.price) * (1 if direction == "up" else -1) <= 0:
                continue
            inside = [p for p in pivots if p.kind != kind and a.index < p.index < b.index]
            if not inside:
                continue
            passed = True
            for j in range(global_to_local[a.index], global_to_local[b.index] + 1):
                position = local_to_global[j]
                value, _ = _line_value(a, b, position)
                tolerance = max(abs(value) * 1e-10, 1e-12)
                if (kind == "H" and high[j] > value + tolerance) or (kind == "L" and low[j] < value - tolerance):
                    passed = False
                    break
            if passed:
                # If more than one interior turn exists, use the exterior
                # displacement (not the lowest/highest nominal price). This
                # keeps the shifted line outside every intervening swing.
                residual = lambda p: p.price - _line_value(a, b, p.index)[0]
                anchor = min(inside, key=lambda p: (residual(p), -p.index)) if direction == "up" else max(inside, key=lambda p: (residual(p), p.index))
                candidates.append((b.index, b.index - a.index, a, b, anchor))
    if not candidates:
        return None, None
    _, _, a, b, anchor = max(candidates, key=lambda item: item[:2])
    return (a, b), anchor


def _direction_and_origin(pivots, high, low, positions):
    """Locate a working trend episode; Q confirmation determines its degree.

    HH/HL or LH/LL only provides an initial direction when the book has no
    established degree yet. Keep its origin until an opposite structure forms,
    instead of resetting the first wave at each small local turn.
    """
    direction, origin = 0, None
    highs, lows = [], []
    for p in pivots:
        (highs if p.kind == "H" else lows).append(p)
        if min(len(highs), len(lows)) < 2:
            continue
        dh, dl = highs[-1].price - highs[-2].price, lows[-1].price - lows[-2].price
        sign = 1 if dh > 0 and dl > 0 else -1 if dh < 0 and dl < 0 else 0
        if sign and sign != direction:
            first_episode = direction == 0
            direction = sign
            origin = min(lows[-2:], key=lambda x: x.price) if sign > 0 else max(highs[-2:], key=lambda x: x.price)
            if first_episode:
                first_peak = highs[-2] if sign > 0 else lows[-2]
                stop = int(np.searchsorted(positions, first_peak.index)) + 1
                j = int(np.argmin(low[:stop]) if sign > 0 else np.argmax(high[:stop]))
                origin = TrendPivot(int(positions[j]), float(low[j] if sign > 0 else high[j]),
                                    "L" if sign > 0 else "H", p.confirmed_at)
    if not direction:
        return 0, None
    return direction, origin


def _book_episode(intervals, data, positions):
    """New opposite Q must have degree evidence or a prior A close break.

    Smaller opposite movements can be corrections; they do not reverse the
    main bias merely because a few micro highs/lows moved in that direction.
    """
    sign, origin, reference = 0, None, None
    high, low, close = (data[k].to_numpy(dtype=float) for k in ("high", "low", "close"))
    outer = [x for x in intervals if x.parent_start is None and not x.ambiguous
             and x.first_confirmed_at is not None]
    for item in sorted(outer, key=lambda x: (x.first_confirmed_at, x.bars)):
        local = int(np.searchsorted(positions, item.start_index))
        if reference is None:
            j = int(np.argmin(low[:local + 1]) if item.direction > 0 else np.argmax(high[:local + 1]))
            sign = item.direction
            origin = TrendPivot(int(positions[j]), float(low[j] if sign > 0 else high[j]),
                                "L" if sign > 0 else "H", item.first_confirmed_at)
            reference = item
            continue
        relation = compare_spans(reference.bars, item.first_bars or item.bars)
        if item.direction == sign:
            if relation.kind != "downgrade":
                reference = item
            continue
        observed = int(np.searchsorted(positions, item.first_confirmed_at))
        # Raw counter-extreme is a conservative bound until a structured e is
        # established by the analyzer; no news/shadow deletion is inferred.
        boundary = reference.counter_price
        prior_a_broken = sign * (close[observed] - boundary) < 0
        if relation.kind in {"same", "expanding", "upgrade"} or prior_a_broken:
            begin = int(np.searchsorted(positions, origin.index))
            j = begin + int(np.argmin(low[begin:local + 1]) if item.direction > 0 else np.argmax(high[begin:local + 1]))
            sign = item.direction
            origin = TrendPivot(int(positions[j]), float(low[j] if sign > 0 else high[j]),
                                "L" if sign > 0 else "H", item.first_confirmed_at)
            reference = item
    return sign, origin


def analyze_trend(frame: pd.DataFrame, *, left: int | None = None,
                  right: int | None = None, atr_period: int = 14,
                  min_swing_atr: float = 0.0, min_swing_percent: float = 0.0,
                  lookback: int = 1600, settings: TrendSettings | dict | None = None) -> TrendAnalysis | None:
    """Analyze exactly the supplied closed prefix, without future data access.

    The settings' pivot confirmation only controls microstructure availability;
    Q durations and degree relations use price excursions, not ATR ZigZag.
    """
    config = settings if isinstance(settings, TrendSettings) else TrendSettings.from_dict(settings)
    if frame is None or frame.empty or not {"open", "high", "low", "close"}.issubset(frame.columns):
        return None
    raw = frame[["open", "high", "low", "close"]].apply(pd.to_numeric, errors="coerce")
    values = raw.to_numpy(dtype=float)
    valid = np.isfinite(values).all(axis=1)
    valid &= (values[:, 1] >= np.maximum(values[:, 0], values[:, 3]))
    valid &= (values[:, 2] <= np.minimum(values[:, 0], values[:, 3])) & (values[:, 1] >= values[:, 2])
    selected = np.flatnonzero(valid)[-max(80, min(int(lookback), config.lookback)):]
    if not len(selected):
        return None
    data = raw.iloc[selected]
    positions = selected.astype(int)
    if "_trend_position" in frame.columns:
        given = pd.to_numeric(frame["_trend_position"].iloc[selected], errors="coerce").to_numpy(dtype=float)
        if not np.isfinite(given).all() or np.any(np.diff(given) <= 0):
            return None
        positions = given.astype(int)
    opening, high, low, close = (data[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close"))
    n, as_of = len(close), int(positions[-1])
    atr = _wilder_atr(high, low, close, max(2, int(atr_period)))
    atr_now = max(float(atr[-1]), abs(float(close[-1])) * 1e-8, 1e-12)
    raw_pivots = _confirmed_pivots(high, low, atr,
        left=max(1, left if left is not None else config.confirmation_bars),
        right=max(1, right if right is not None else config.confirmation_bars),
        min_swing_atr=min_swing_atr, min_swing_percent=min_swing_percent)
    fine = tuple(TrendPivot(int(positions[p.index]), p.price, p.kind, int(positions[p.confirmed_at])) for p in raw_pivots)
    mapping = {int(p): i for i, p in enumerate(positions)}
    intervals = find_intervals(data, positions, config.minimum_interval)
    sign, origin = _book_episode(intervals, data, positions)
    if not sign:
        sign, origin = _direction_and_origin(fine, high, low, positions)
    # One first interval can establish waves 1/2/3 even before two high/low
    # pairs exist. Compare recent interval evidence, requiring a close for Q1.
    if not sign:
        observed = [p for p in intervals if p.first_confirmed_at is not None and not p.ambiguous]
        if observed:
            candidate = max(observed, key=lambda x: (x.first_confirmed_at, x.bars))
            sign = candidate.direction
            before = mapping[candidate.start_index]
            j = (int(np.argmin(low[:before + 1])) if sign > 0 else int(np.argmax(high[:before + 1])))
            origin = TrendPivot(int(positions[j]), float(low[j] if sign > 0 else high[j]), "L" if sign > 0 else "H", candidate.first_confirmed_at)
    structure = build_structure(intervals, sign, origin.index, origin.price, data, positions) if sign and origin else None
    # When a current episode is too short, retain the observations but do not
    # invent a complete structure or a main A from the most recent local pivot.
    direction = "up" if sign > 0 else "down" if sign < 0 else "sideways" if len(fine) >= 4 else "unknown"
    boundaries, book_corrections, wave_points, wave_labels, timing = [], [], [], [], []
    main_intervals, navigation = (structure.intervals, structure.navigation) if structure else ((), ())

    def boundary_for(item, degree, number):
        correction = correction_for_interval(item, fine, data, positions, atr)
        if correction is None or correction.end_point is None:
            return None
        # An internal rebound close confirms a structural end; it must also be
        # available by the completed interval. Same-bar order remains unknown.
        point = correction.end_point
        available = max(item.confirmed_at, correction.confirmed_at, point.confirmed_at)
        if available > as_of or item.ambiguous:
            return None
        state = "confirmed" if correction.state == "breakout" else "candidate"
        broken = None
        for j in range(int(np.searchsorted(positions, available, side="right")), n):
            if sign * (close[j] - point.price) < 0:
                broken = int(positions[j])
                break
        book_corrections.append(correction)
        return BoundaryPoint(point.index, point.price, sign, degree, number,
                             correction.kind, available, item.start_index, broken, state)

    if structure:
        wave_points.append(origin)
        for i, item in enumerate(main_intervals):
            peak = TrendPivot(item.start_index, item.level, "H" if sign > 0 else "L", item.confirmed_at)
            if not wave_points or peak.index > wave_points[-1].index:
                wave_points.append(peak)
            wave_labels.append(TrendLabel(peak.index, peak.price, f"浪{2 * i + 1}", -sign,
                                           peak.confirmed_at, origin.index, evidence="区间跨度所确定的主浪端点"))
            boundary = boundary_for(item, "main", i + 1)
            if boundary:
                boundaries.append(boundary)
                p = TrendPivot(boundary.index, boundary.price, "L" if sign > 0 else "H", boundary.confirmed_at)
                if p.index > wave_points[-1].index:
                    wave_points.append(p)
                wave_labels.append(TrendLabel(p.index, p.price, f"浪{2 * i + 2}" + ("?" if boundary.state == "candidate" else ""), sign,
                                               p.confirmed_at, peak.index, evidence=boundary.pattern + "的结构结束点"))
        # Navigation A is separately labelled; it never changes the main A merely
        # because it is a newer local low/high.
        nav_number = 0
        for item in navigation:
            nav_number += 1
            boundary = boundary_for(item, "navigation", nav_number)
            if boundary:
                boundaries.append(boundary)
        last_boundary = next((p for p in reversed(boundaries) if p.degree == "main"), None)
        if last_boundary:
            tips = [p for p in fine if p.index > last_boundary.index and p.kind == ("H" if sign > 0 else "L")]
            if tips:
                tip = max(tips, key=lambda p: sign * p.price)
                if tip.index > wave_points[-1].index:
                    wave_points.append(tip)
                    wave_labels.append(TrendLabel(tip.index, tip.price, f"浪{2 * len(main_intervals) + 1}?", -sign,
                                                   tip.confirmed_at, last_boundary.index, evidence="末主浪端点解释，可随新高低延伸"))
        previous = [p for p in intervals if p.direction == -sign and p.end_index <= origin.index]
        degree = "级别待定"
        if previous:
            other = max(previous, key=lambda p: p.end_index)
            degree = "主趋势" if compare_spans(other.bars, structure.reference_bars).kind == "same" else "导航/新级别候选"
        structure = replace(structure, degree=degree)
        if any(p.state == "candidate" for p in boundaries if p.degree == "main"):
            structure = replace(structure, complete=False,
                                label=structure.label + " / 调整子结构待分级")
        # Estimates are listed in details, not inserted as future candles.
        t1 = mapping[main_intervals[0].start_index] - mapping.get(origin.index, 0)
        q1 = main_intervals[0].bars
        timing.append(f"T1={t1}根，Q1={q1}根；Tw≈2.5×(T1+Q1)={2.5 * (t1 + q1):.1f}根（理论估算）")

    # Geometry uses degree-resolved points. A separate navigation geometry may
    # remain useful when the main degree lacks enough anchors.
    points = tuple(sorted({p.index: p for p in wave_points}.values(), key=lambda p: p.index)) or fine
    geometry = points
    main_line = exterior = anchor = None
    if sign:
        main_line = _select_valid_trendline(direction, geometry, high, low, atr, mapping, positions, as_of, min_anchor_bars=2)
        exterior, anchor = _select_external_channel(direction, geometry, high, low, atr, mapping)
    line_degree = structure.degree if structure else "级别待定"
    if sign and exterior is None:
        candidates = tuple(p for p in fine if origin is None or p.index >= origin.index)
        exterior, anchor = _select_external_channel(direction, candidates, high, low, atr, mapping)
        if exterior:
            line_degree = "导航"
    line_value, slope = _line_value(*main_line, as_of) if main_line else (None, None)
    line_broken = bool(sign and line_value is not None and sign * (close[-1] - line_value) < 0)
    offset = pivot_value = None
    pivot_broken = False
    if exterior and anchor:
        value, _ = _line_value(*exterior, anchor.index)
        offset = anchor.price - value
        pivot_value = _line_value(*exterior, as_of)[0] + offset
        pivot_broken = sign * (close[-1] - pivot_value) < 0
    confirmed_main = [p for p in boundaries if p.degree == "main" and p.state == "confirmed"]
    key = max(confirmed_main, key=lambda p: p.confirmed_at) if confirmed_main else None
    key_broken = bool(key and key.broken_at is not None)

    # Historical correction / ratio explanations are restricted to this trend
    # episode; ordinary earlier opposite pivots do not inherit the current bias.
    relevant = tuple(p for p in fine if origin is None or p.index >= origin.index)
    detected = correction_patterns(data, relevant, positions, atr, max_patterns=24,
                                    direction=sign or None, fine_pivots=fine)
    by_end = {(p.direction, p.markers[-1].index): p for p in detected}
    for p in book_corrections:
        by_end[(p.direction, p.markers[-1].index)] = p
    corrections = tuple(sorted(by_end.values(), key=lambda p: p.confirmed_at)[-24:])
    ratios, contexts = [], []
    main_by_start = {p.interval_start: p for p in boundaries if p.degree == "main"}
    previous_origin = origin
    standards = standard_candles(data, positions, config.standard_window, config.standard_sample)
    levels = []
    for i, item in enumerate(main_intervals):
        a = main_by_start.get(item.start_index)
        if previous_origin is None or a is None:
            continue
        height = sign * (item.level - previous_origin.price)
        if height <= 0:
            continue
        percent = sign * (item.level - a.price) / height * 100
        local0, local1 = mapping[previous_origin.index], mapping[item.start_index]
        distance = abs(item.level - previous_origin.price)
        path = float(np.sum(np.abs(np.diff(close[local0:local1 + 1]))))
        strong = (distance >= 3 * max(float(atr[local1]), 1e-12)
                  and path > 0 and distance / path >= .6
                  and any(x.standard and x.direction == sign for x in standards[local0:local1 + 1]))
        text = f"回调 {percent:.1f}%" + ("·候选A" if a.state == "candidate" else "")
        ratios.append(TrendLabel(a.index, a.price, text, sign, a.confirmed_at,
                                item.start_index, "retracement", percent,
                                evidence=f"相对浪{2*i+1}，允许浪2超过100%"))
        hints = []
        if strong and abs(percent - 40) <= 3:
            hints.append("强40%")
        if i == 0 and abs(percent - 78.6) <= 3:
            hints.append("浪2 78.6%")
        if i > 0 and abs(percent - 62) <= 3:
            hints.append(f"浪{2*i+2} 62%")
        if a.state == "confirmed":
            hints.append(a.label + "·调整结构结束")
        if hints:
            contexts.append(TrendLabel(a.index, a.price, " / ".join(hints), sign,
                                       a.confirmed_at, item.start_index, evidence=a.pattern + f"；回调{percent:.1f}%"))
        # Tp/Ta needs a valid location/structure and is disabled for triangles.
        endpoint = TrendPivot(a.index, a.price, "L" if sign > 0 else "H", a.confirmed_at)
        peak = TrendPivot(item.start_index, item.level, "H" if sign > 0 else "L", item.confirmed_at)
        ratio = tp_ta(data, positions, previous_origin, peak, endpoint,
                      enabled=.62 <= percent / 100 <= .786 or bool(structure and structure.complete),
                      triangle="triangle" in a.pattern)
        if ratio:
            r, tp, ta = ratio
            timing.append(f"{a.label} Tp/Ta={tp}/{ta:.2f}={r:.3f}，" + ("满足≥1.208，支持调整解释" if r >= 1.208 else "未达1.208"))
        previous_origin = endpoint

    # Current leg ruler; its high/low uses only closed data at this replay point.
    leg_origin = (TrendPivot(key.index, key.price, "L" if sign > 0 else "H", key.confirmed_at) if key else origin)
    leg_peak = None
    angle_slope = None
    angle_anchor = None
    pullback_percent = None
    if sign and leg_origin is not None:
        begin = mapping[leg_origin.index]
        peak_local = begin + int(np.argmax(high[begin:]) if sign > 0 else np.argmin(low[begin:]))
        price = float(high[peak_local] if sign > 0 else low[peak_local])
        leg_peak = TrendPivot(int(positions[peak_local]), price, "H" if sign > 0 else "L", as_of)
        height = sign * (price - leg_origin.price)
        if height > 0 and peak_local < n - 1:
            pullback_percent = sign * (price - float(close[-1])) / height * 100
        for ratio in (.4, .62, .786):
            levels.append(BookLevel(price - sign * ratio * height, f"{ratio*100:g}%回调参考", leg_peak.index, as_of, "retracement"))
        if main_intervals:
            first_height = sign * (main_intervals[0].level - origin.price)
            for ratio in (1.208, 1.618, 2., 2.382, 2.618, 3.):
                levels.append(BookLevel(leg_origin.price + sign * ratio * first_height,
                                       f"主浪目标 {ratio:g}×H1", leg_origin.index, leg_origin.confirmed_at, "target"))
            for ratio in (3.236, 4.236):
                levels.append(BookLevel(origin.price + sign * ratio * first_height,
                                        f"全趋势目标 {ratio:g}×H1", origin.index,
                                        main_intervals[0].confirmed_at, "target"))
            prior_origin = (TrendPivot(confirmed_main[-2].index, confirmed_main[-2].price,
                                      "L" if sign > 0 else "H", confirmed_main[-2].confirmed_at)
                            if len(confirmed_main) >= 2 else origin)
            last_impulse = main_intervals[-1]
            duration = mapping[last_impulse.start_index] - mapping[prior_origin.index]
            if duration > 0:
                angle_slope = (last_impulse.level - prior_origin.price) / duration
                angle_anchor = leg_origin
                timing.append(f"PSTD角度参考：前主浪{duration}根，每根{angle_slope:+.6g}；从{leg_origin.index}号A点平移")
                timing.append(f"主浪周期参考：前主浪的1或2倍，即{duration}或{2*duration}根；结构与价位须共同核对")
                corrections_main = sorted(confirmed_main, key=lambda x: x.number)
                if len(corrections_main) >= 2:
                    previous_a, latest_a = corrections_main[-2:]
                    previous_q = next(x for x in main_intervals if x.start_index == previous_a.interval_start)
                    latest_q = next(x for x in main_intervals if x.start_index == latest_a.interval_start)
                    previous_time = mapping[previous_a.index] - mapping[previous_q.start_index]
                    latest_time = mapping[latest_a.index] - mapping[latest_q.start_index]
                    depth = abs(latest_q.level - latest_a.price)
                    timing.append(f"前后调整周期 {previous_time} / {latest_time}根；下一调整高度参考 [{.828*depth:.6g}, {depth/.828:.6g}]")

    complete = bool(structure and structure.complete)
    if complete and points:
        peak = next((p for p in reversed(points) if p.kind == ("H" if sign > 0 else "L")), None)
        if peak:
            contexts.append(TrendLabel(peak.index, peak.price, "主结构候选完整·核对子结构", -sign,
                                       max(peak.confirmed_at, structure.confirmed_at), peak.index,
                                       evidence=structure.label))
    shapes = book_candle_patterns(data, positions, standard_window=config.standard_window, standard_sample=config.standard_sample)
    # Early watch markers use a ruler known BEFORE the shape. The A/degree may
    # remain pending; never build a level from the shape's own high/low.
    if sign and leg_origin is not None and leg_peak is not None:
        height = sign * (leg_peak.price - leg_origin.price)
        correction_number = 2 * len(main_intervals) + 2
        begin, peak_j = mapping[leg_origin.index], mapping[leg_peak.index]
        path = float(np.sum(np.abs(np.diff(close[begin:peak_j + 1]))))
        strong = (height >= 3 * max(float(atr[peak_j]), 1e-12) and path > 0 and height / path >= .6
                  and any(s.standard and s.direction == sign for s in standards[begin:peak_j + 1]))
        for shape in shapes:
            if shape.index != as_of or shape.direction != sign or leg_peak.index >= shape.start_index or leg_origin.confirmed_at > shape.start_index:
                continue
            for ratio, enabled in ((.4, strong), (.62, correction_number in {4, 6}), (.786, correction_number == 2)):
                price = leg_peak.price - sign * ratio * height
                if enabled and height > 0 and abs(shape.price - price) <= max(.5 * atr_now, .03 * height):
                    name = "强40%" if ratio == .4 else f"浪{correction_number} {ratio*100:g}%"
                    contexts.append(TrendLabel(shape.index, shape.price, name + "附近·结构待确认", sign,
                                               as_of, shape.start_index, evidence="反转出现前已知的主浪与回调位置"))
    # Current G4/G6 at a known pivot line. The line must already exist on this
    # prefix; no historical candle inherits a subsequently constructed line.
    if pivot_value is not None and len(main_intervals) in {1, 2}:
        wave = 2 * len(main_intervals) + 2
        for shape in shapes:
            if shape.index == as_of and shape.direction == sign and abs(shape.price - pivot_value) <= atr_now * .8:
                contexts.append(TrendLabel(shape.index, shape.price, f"G{wave}拐点附近·调整待确认", sign, as_of,
                                           shape.start_index, evidence="已知拐点线与反转K相邻"))
    reversals = key_reversals(shapes, fine, positions, atr, contexts=contexts)
    latest_shape = next((x for x in reversals if x.index == as_of and x.confirmed_at <= as_of), None)

    active_tool = "趋势拐点线"
    chosen_line, chosen_offset = exterior, float(offset or 0)
    if structure and (len(main_intervals) >= 2 or (pivot_broken and main_line is not None)):
        active_tool, chosen_line, chosen_offset = "趋势线", main_line, 0.0
    if chosen_line is None and main_line:
        active_tool, chosen_line, chosen_offset = "趋势线", main_line, 0.0
    if line_degree == "导航" or complete:
        active_tool = (("导航拐点线" if chosen_line == exterior else "导航趋势线") if line_degree == "导航" else active_tool) + ("·核对子结构" if complete else "")
    nav_line = nav_anchor = nav_offset = None
    if complete and key:
        last_leg = tuple(p for p in fine if p.index >= key.index)
        nav_line, nav_anchor = _select_external_channel(direction, last_leg, high, low, atr, mapping)
        if nav_line and nav_anchor:
            nav_offset = nav_anchor.price - _line_value(*nav_line, nav_anchor.index)[0]
            active_tool, chosen_line, chosen_offset = "导航拐点线·主结构候选完整", nav_line, nav_offset
    decision = line_break_decision(sign, chosen_line, chosen_offset, data, positions, standards, atr,
                                   line_kind=active_tool, structure_complete=complete,
                                   leg_origin=leg_origin, leg_peak=leg_peak) if sign else None
    if decision and decision.delay_level is not None:
        levels.append(BookLevel(decision.delay_level, "延时转向线", decision.break_index, decision.delay_ready_at, "delay"))
    failed = failed_interval_candidate(structure, data, positions) if structure else None
    status = structure.label if structure else "区间/级别尚未确认"
    breakout = None
    if key_broken:
        status += " | 主A收盘破位"
        breakout = "main_a_broken"
    elif decision and decision.state not in {"unavailable", "intact"}:
        status += " | " + decision.label
        breakout = "line_" + decision.state
    if n < config.standard_window:
        status += f" | 标准K样本{n}/{config.standard_window}"
    return TrendAnalysis(
        direction, status, tuple(points), main_line, slope, line_value, line_broken,
        None, None, offset, pivot_value, bool(pivot_broken),
        key.price if key else None, key.label if key else None, key.index if key else None,
        key_broken, breakout, pullback_percent,
        TrendPivot(key.index, key.price, "L" if sign > 0 else "H", key.confirmed_at) if key else None,
        ("watch_buy" if latest_shape.direction > 0 else "watch_sell") if latest_shape else None,
        latest_shape.index if latest_shape else None, latest_shape.text if latest_shape else None,
        latest_shape.price if latest_shape else None, atr_now,
        float(np.max(high[-40:])), float(np.min(low[-40:])), int(positions[max(0, n - 40)]), as_of,
        exterior, anchor, tuple(ratios), tuple(reversals), corrections,
        tuple(intervals), structure, tuple(sorted(boundaries, key=lambda a: a.confirmed_at)),
        tuple(levels), tuple(wave_labels), shapes, standards, fine, decision, failed,
        tuple(timing), active_tool, line_degree, config,
        nav_line, nav_offset, nav_anchor, angle_slope, angle_anchor,
    )
