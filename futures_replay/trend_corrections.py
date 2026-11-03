from __future__ import annotations

"""Four correction families from third-edition pp. 504–557.

The end of an abc is c; the end of a triangle/abcde is e. In particular the
last e is not replaced by the lowest/highest price of the entire correction.
Internal counts are reported from observed finer pivots, not forced into 3-3-5.
"""

from dataclasses import replace
from itertools import product
from typing import Sequence

import numpy as np
import pandas as pd

from .trend_types import TrendCorrection, TrendPivot


def segments(points):
    return tuple(((float(a.index), float(a.price)), (float(b.index), float(b.price)))
                 for a, b in zip(points, points[1:]))


def classify_correction(points: Sequence, direction: int,
                        tolerance: float = 0.0) -> tuple[str, str] | None:
    """Points include the start, then 1/3/5 correction legs, in trend context.

    No rule here forbids an end beyond the prior main-wave origin: counting
    rule 6 explicitly allows it. A genuine resumption is verified separately.
    """
    if len(points) not in {2, 4, 6} or direction not in {-1, 1}:
        return None
    prices = direction * np.array([p.price for p in points], dtype=float)
    changes = np.diff(prices)
    if not all((d < 0 if j % 2 == 0 else d > 0) for j, d in enumerate(changes)):
        return None
    if len(points) == 2:
        return "simple", "简单调整"
    if len(points) == 4:
        start, a, b, c = prices
        if b >= start:
            return "abc_strong", "强势abc调整"
        if c > a + tolerance:
            return "abc_contracting", "收缩abc调整"
        return "abc", "标准abc调整"
    sizes = np.abs(changes)
    # The book describes each successive LEG, not merely converging envelopes.
    if np.all(sizes[1:] < sizes[:-1]):
        return "triangle", "收缩三角调整"
    if np.all(sizes[1:] > sizes[:-1]):
        return "expanding_triangle", "扩张三角调整"
    # a,c,e on one side; start,b,d on the other. A trending correction moves
    # against the main direction; same-direction channels are not called flags.
    bottoms, tops = prices[1::2], prices[::2]
    if np.all(np.diff(bottoms) < 0) and np.all(np.diff(tops) < 0):
        return "abcde_trending", "趋势abcde调整"
    height = max(float(np.ptp(prices)), 1e-12)
    flat_band = max(tolerance, height * .20)
    if np.ptp(bottoms) <= flat_band and np.ptp(tops) <= flat_band:
        return "abcde_sideways", "横向abcde调整"
    # Irregular is not a catch-all for a failed geometric fit. Compare the
    # SAME-direction legs (b-c with d-e, and a-b with c-d); the old adjacent
    # b-c/c-d comparison accepted arbitrary six-pivot windows. This two-pair
    # screen is a conservative program convention, not a new book rule.
    durations = np.diff([p.index for p in points])
    similar = lambda a, b: abs(a - b) <= max(1, min(a, b) * .208)
    if (similar(durations[2], durations[4]) and similar(durations[1], durations[3])):
        return "abcde_irregular", "不规则abcde调整"
    return None


def internal_structure(points: Sequence, fine_pivots: Sequence) -> str:
    counts = []
    for a, b in zip(points, points[1:]):
        observed = [p for p in fine_pivots if a.index < p.index < b.index]
        # Endpoints must be of opposite kind; an odd number of legs is needed.
        legs = len(observed) + 1
        counts.append(str(legs) if legs in {1, 3, 5, 7} else "待分级")
    return "-".join(counts)


def make_correction(points: Sequence, direction: int, frame: pd.DataFrame,
                    positions: np.ndarray, *, fine_pivots: Sequence = (),
                    tolerance: float = 0.0) -> TrendCorrection | None:
    identified = classify_correction(points, direction, tolerance)
    if identified is None:
        return None
    kind, name = identified
    available = max(p.confirmed_at for p in points)
    close = frame["close"].to_numpy(dtype=float)
    # Return through the last internal rebound confirms the proposed end, not
    # an unrelated break of a fitted regression envelope.
    trigger = points[-2].price
    counter = points[-1]
    state, label_index, price = "candidate", counter.index, counter.price
    for j in range(int(np.searchsorted(positions, available)), len(close)):
        if int(positions[j]) <= counter.index:
            continue
        if direction * (close[j] - trigger) > 0:
            state, available, label_index, price = "breakout", int(positions[j]), int(positions[j]), float(close[j])
            break
        # A candidate end remains a candidate until broken in the opposite
        # direction; subsequent prices cannot be used to erase that failure.
        if direction * (close[j] - counter.price) < 0:
            state, available, label_index, price = "opposite_break", int(positions[j]), int(positions[j]), float(close[j])
            break
    suffix = {"candidate": "候选", "breakout": "收盘确认结束", "opposite_break": "结束点失效"}[state]
    counts = internal_structure(points, fine_pivots or points)
    names = ("起", "终") if len(points) == 2 else ("起", "a", "b", "c") if len(points) == 4 else ("起", "a", "b", "c", "d", "e")
    durations = np.diff([p.index for p in points])
    evidence = f"各腿周期 {','.join(map(str, durations))}；子结构 {counts}；结束点{names[-1]}"
    return TrendCorrection(kind, name + "·" + suffix, points[0].index, label_index,
                           segments(points), tuple(points), names, label_index, float(price),
                           direction, available, state, counts,
                           TrendPivot(counter.index, counter.price, counter.kind, counter.confirmed_at), evidence)


def compound_correction(points: Sequence, direction: int, frame: pd.DataFrame,
                        positions: np.ndarray, tolerance=0.0) -> TrendCorrection | None:
    """Resolve observed finer 3/5/7-leg subdivisions into a full abc/abcde.

    Each merged leg must end at its observed directional extreme and contain
    its internal path. An overshooting internal high/low cannot be hidden just
    to manufacture a desired count. Timing similarity ranks competing counts;
    the chosen subdivision is reported, never presented as a unique answer.
    """
    legs = len(points) - 1
    if legs < 5 or legs > 25 or legs % 2 == 0:
        return None
    candidates = []
    prices = np.array([p.price for p in points], dtype=float)
    for count in (3, 5):
        for sizes in product((1, 3, 5, 7), repeat=count):
            if sum(sizes) != legs or max(sizes) == 1:
                continue
            ends = np.r_[0, np.cumsum(sizes)]
            macro = [points[int(j)] for j in ends]
            if classify_correction(macro, direction, tolerance) is None:
                continue
            valid = True
            for first, last in zip(ends, ends[1:]):
                observed = prices[first:last + 1]
                lower, upper = sorted((prices[first], prices[last]))
                if float(np.min(observed)) < lower - tolerance or float(np.max(observed)) > upper + tolerance:
                    valid = False
                    break
            if not valid:
                continue
            item = make_correction(macro, direction, frame, positions, fine_pivots=points, tolerance=tolerance)
            if item is None:
                continue
            durations = np.diff([p.index for p in macro])
            symmetry = abs(durations[0] - durations[2]) / max(1, durations[0], durations[2])
            if count == 5:
                symmetry += abs(durations[1] - durations[3]) / max(1, durations[1], durations[3])
            item = replace(item, label="复杂" + item.label,
                           evidence=item.evidence + "；更细转折合并解释，非唯一数法")
            candidates.append(((symmetry, count, max(sizes)), item))
    return min(candidates, key=lambda x: x[0])[1] if candidates else None


def correction_patterns(frame: pd.DataFrame, pivots: Sequence, positions: np.ndarray,
                        atr: np.ndarray, max_patterns: int = 16,
                        *, direction: int | None = None,
                        fine_pivots: Sequence | None = None) -> tuple[TrendCorrection, ...]:
    """Recognize the four families with an observed preceding main leg.

    The main analyzer supplies degree/context. With no direction a proposed
    preceding leg must break a previous same-side extreme, preventing a small
    counterleg from inventing the opposite trend in a rising/falling market.
    """
    output = []
    as_of = int(positions[-1])
    fine = fine_pivots or pivots

    def is_impulse(a, b, sign):
        if sign * (b.price - a.price) <= 0:
            return False
        if direction is not None and sign != direction:
            return False
        old = [p for p in pivots if p.kind == b.kind and p.index < a.index]
        return not old or sign * (b.price - old[-1].price) > 0

    for length in (2, 4, 6):
        for i in range(1, len(pivots) - length + 1):
            origin = pivots[i - 1]
            points = pivots[i:i + length]
            sign = 1 if points[0].kind == "H" else -1
            if not is_impulse(origin, points[0], sign):
                continue
            local = min(int(np.searchsorted(positions, points[-1].index)), len(atr) - 1)
            item = make_correction(points, sign, frame, positions, fine_pivots=fine,
                                   tolerance=max(float(atr[local]) * .1, 1e-12))
            if item is None:
                continue
            # Historical single-leg candidates are superseded by later swings.
            if item.state == "candidate" and points[-1].index != pivots[-1].index:
                continue
            if item.confirmed_at <= as_of:
                output.append(item)
    # Diamond and X are combinations of two triangles (p. 505). The shared
    # turning leg is counted once. This is explicitly a geometric candidate,
    # not a fabricated eight-leg Elliott correction or a confirmed A point.
    for i in range(1, len(pivots) - 9):
        points = pivots[i:i + 10]
        sign = 1 if points[0].kind == "H" else -1
        if not is_impulse(pivots[i - 1], points[0], sign):
            continue
        legs = np.abs(np.diff([p.price for p in points]))
        first_grow, first_shrink = np.all(np.diff(legs[:5]) > 0), np.all(np.diff(legs[:5]) < 0)
        last_grow, last_shrink = np.all(np.diff(legs[4:]) > 0), np.all(np.diff(legs[4:]) < 0)
        kind = "diamond_combination" if first_grow and last_shrink else "x_combination" if first_shrink and last_grow else None
        if kind:
            name = "钻石三角组合" if first_grow else "X形三角组合"
            end = points[-1]
            output.append(TrendCorrection(kind, name + "·几何候选", points[0].index, end.index,
                          segments(points), tuple(points), ("起", "a", "b", "c", "d", "e", "a′", "b′", "c′", "e′"),
                          end.index, end.price, sign, max(p.confirmed_at for p in points),
                          evidence="扩张/收缩三角组合，需进一步确认结构结束"))
    # Prefer the longer structure if the same end has several interpretations;
    # retain other outcomes in the detail report only when they end elsewhere.
    output.sort(key=lambda x: (x.markers[-1].index, len(x.markers), x.confirmed_at))
    by_end = {}
    for item in output:
        key = (item.direction, item.markers[-1].index)
        by_end[key] = item
    return tuple(sorted(by_end.values(), key=lambda p: p.confirmed_at)[-max_patterns:])


def correction_for_interval(interval, pivots: Sequence, frame: pd.DataFrame,
                             positions: np.ndarray, atr: np.ndarray) -> TrendCorrection | None:
    """Resolve the correction ending before an interval's breakout.

    In a contracting triangle a is the low, e is the END. This function uses
    the sequence through e rather than replacing it with the whole-span min.
    """
    sign = interval.direction
    kind = "H" if sign > 0 else "L"
    start = TrendPivot(interval.start_index, interval.level, kind, interval.start_index + 1)
    end_kind = "L" if sign > 0 else "H"
    within = [p for p in pivots if interval.start_index < p.index < interval.end_index
              and p.confirmed_at <= int(positions[-1])]
    ends = [j for j, p in enumerate(within) if p.kind == end_kind]
    if not ends or interval.ambiguous:
        return None
    # The first confirmed, surviving structural end owns the OUTER A. Later
    # small pullbacks during the resuming main wave create NAVIGATION A points;
    # they cannot drag the outer A all the way up toward the old-high breakout.
    # Include the interval's counter-extreme, but do not force the END to equal
    # that extreme: the endpoint of a triangle is e, often above/below a.
    observations = frame["close"].to_numpy(dtype=float)
    cutoff = int(np.searchsorted(positions, interval.end_index, side="right"))
    choices = []
    for end in ends:
        point = within[end]
        if point.index < interval.counter_index:
            continue
        points = [start] + within[:end + 1]
        while len(points) > 1 and points[1].kind == start.kind:
            points.pop(1)
        local = min(int(np.searchsorted(positions, point.index)), len(atr) - 1)
        tolerance = max(.05 * float(atr[local]), 1e-12)
        candidates = []
        if len(points) in {2, 4, 6}:
            plain = make_correction(points, sign, frame, positions, fine_pivots=pivots, tolerance=tolerance)
            if plain:
                candidates.append(plain)
        compound = compound_correction(points, sign, frame, positions, tolerance=tolerance)
        if compound:
            candidates.append(compound)
        for item in candidates:
            if item.state != "breakout":
                continue
            begin = int(np.searchsorted(positions, item.confirmed_at))
            if np.any(sign * (observations[begin:cutoff] - point.price) < 0):
                continue
            choices.append(item)
    if choices:
        return min(choices, key=lambda p: (p.confirmed_at, "待分级" in p.structure, len(p.markers)))
    # If no complete subdivision survives, show the proposed end as a candidate
    # instead of making the latest generic low/high a confirmed main A.
    point = within[ends[-1]]
    points = (start, *within[:ends[-1] + 1])
    return TrendCorrection("compound_pending", "复合调整·结构待分级", start.index, point.index,
                            segments(points), tuple(points), tuple(["起"] + [""] * (len(points) - 2) + ["终?"]),
                            point.index, point.price, sign,
                            max(interval.confirmed_at, point.confirmed_at), "candidate", "待分级", point,
                            "区间已突破，但内层结构不足；不将该候选A作为正式破位条件")
