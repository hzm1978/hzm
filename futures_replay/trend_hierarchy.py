from __future__ import annotations

"""Local, bidirectional interval counts inside an independently chosen parent.

The parent bias is not a filter: an upward main leg can contain a downward
five-subwave correction. Each local count must have its own observable Qs.
Origins are observed pivots, Q1 needs a close, and the live last leg is kept
separate from a confirmed turning point. No Elliott wave-2 prohibition is used.
"""

from dataclasses import dataclass, replace
from typing import Sequence

import numpy as np
import pandas as pd

from .trend_types import TrendCorrection, TrendLabel, TrendPivot
from .trend_intervals import (BookInterval, BookLevel, BoundaryPoint, WaveStructure,
                              build_structure, compare_spans)
from .trend_corrections import correction_for_interval, make_correction, compound_correction


@dataclass(frozen=True)
class SubwaveSeries:
    direction: int
    points: tuple[TrendPivot, ...]
    intervals: tuple[BookInterval, ...]
    structure: WaveStructure
    labels: tuple[TrendLabel, ...]
    retracements: tuple[TrendLabel, ...]
    corrections: tuple[TrendCorrection, ...]
    levels: tuple[BookLevel, ...]
    ruler_origin: TrendPivot
    ruler_tip: TrendPivot
    pullback_percent: float
    state: str
    confirmed_at: int
    as_of_index: int
    evidence: str
    pending_interval: BookInterval | None = None
    boundaries: tuple[BoundaryPoint, ...] = ()
    trendline: tuple[TrendPivot, TrendPivot] | None = None
    external_line: tuple[TrendPivot, TrendPivot] | None = None
    pivot_line_anchor: TrendPivot | None = None
    pivot_line_offset: float | None = None

    @property
    def label(self):
        name = "下降" if self.direction < 0 else "上升"
        suffix = ("第5浪候选·未破浪3" if self.state == "fifth_pending" else
                  "末端进行中" if self.state == "running" else "末端转折已观察")
        if self.state != "fifth_pending" and not self.structure.complete:
            suffix += "·" + ("待第三Q" if "five" in self.structure.kind and self.structure.kind != "same_five"
                              else "子结构待耦合")
        return f"{name}{len(self.points)-1}子浪 · {suffix}"


def correction_in_leg(start: TrendPivot, endpoint: TrendPivot | None, direction: int,
                      pivots: Sequence[TrendPivot], frame: pd.DataFrame,
                      positions: np.ndarray, atr: np.ndarray) -> TrendCorrection | None:
    """One correction context, never every rolling a/b/c/d/e permutation.

    An endpoint is the end of the chosen *degree*, not necessarily its most
    extreme a. The full finer path is retained when it cannot be partitioned.
    """
    end = endpoint.index if endpoint else int(positions[-1])
    within = [p for p in pivots if start.index < p.index <= end]
    while within and within[0].kind == start.kind:
        within.pop(0)
    if endpoint:
        within = [p for p in within if p.index < endpoint.index] + [endpoint]
    elif within and within[-1].kind == start.kind:
        within.pop()
    points = (start, *within)
    if len(points) < 2 or len(points) % 2:
        return None
    local = min(int(np.searchsorted(positions, points[-1].index)), len(atr) - 1)
    tolerance = max(.05 * float(atr[local]), 1e-12)
    item = (make_correction(points, direction, frame, positions, fine_pivots=pivots,
                            tolerance=tolerance) if len(points) in {2, 4, 6} else None)
    if item is None:
        item = compound_correction(points, direction, frame, positions, tolerance)
    return item


def _series(origin, structure, frame, positions, fine, atr):
    sign = structure.direction
    mapping = {int(x): i for i, x in enumerate(positions)}
    asof = int(positions[-1])
    points, ratios, corrections, boundaries = [origin], [], [], []
    high, low, close = (frame[k].to_numpy(dtype=float) for k in ("high", "low", "close"))
    for i, item in enumerate(structure.intervals):
        # Q starts at the FIRST equal high/low. A confirmed pivot can be the
        # last equal extreme a bar later; keep the Q clock and pivot distinct.
        same = [p for p in fine if p.kind == ("H" if sign > 0 else "L")
                and item.start_index <= p.index < item.counter_index
                and np.isclose(p.price, item.level, rtol=0, atol=1e-9)]
        peak = (same[-1] if same else TrendPivot(item.start_index, item.level,
                "H" if sign > 0 else "L", item.confirmed_at))
        correction = correction_for_interval(item, fine, frame, positions, atr)
        endpoint = correction.end_point if correction and correction.state == "breakout" else None
        if endpoint is None:
            same_counter = [p for p in fine if p.kind != peak.kind
                            and item.counter_index <= p.index < item.end_index
                            and np.isclose(p.price, item.counter_price, rtol=0, atol=1e-9)]
            endpoint = (same_counter[-1] if same_counter else TrendPivot(
                item.counter_index, item.counter_price, "L" if sign > 0 else "H", item.confirmed_at))
        if not points[-1].index < peak.index < endpoint.index < item.end_index:
            return None
        height = sign * (peak.price - points[-1].price)
        if height <= 0:
            return None
        available = max(item.confirmed_at, endpoint.confirmed_at,
                        correction.confirmed_at if correction else item.confirmed_at)
        percent = sign * (peak.price - endpoint.price) / height * 100
        duration = mapping[endpoint.index] - mapping[peak.index]
        ratios.append(TrendLabel(endpoint.index, endpoint.price,
            f"子{2*i+2}回调 {percent:.1f}% · {duration}根", sign,
            available, peak.index, "subwave_retracement", percent,
            evidence=f"相对本组子{2*i+1}；起价{points[-1].price:.8g}，末价{peak.price:.8g}，回调末价{endpoint.price:.8g}"))
        if correction:
            corrections.append(replace(correction, label=f"子{2*i+2}·" + correction.label,
                confirmed_at=max(correction.confirmed_at, item.confirmed_at)))
            if correction.state == "breakout":
                after = int(np.searchsorted(positions, available, side="right"))
                breaks = np.flatnonzero(sign*(close[after:]-endpoint.price) < 0)
                broken = int(positions[after+int(breaks[0])]) if len(breaks) else None
                boundaries.append(BoundaryPoint(endpoint.index, endpoint.price, sign,
                    "subwave", i+1, correction.kind, available, item.start_index, broken))
        points.extend((peak, endpoint))
    begin = mapping[points[-1].index]
    extreme = high if sign > 0 else low
    j = begin + int(np.argmax(extreme[begin:]) if sign > 0 else np.argmin(extreme[begin:]))
    if int(positions[j]) <= points[-1].index:
        return None
    same = [p for p in fine if p.kind == ("H" if sign > 0 else "L")
            and p.index >= int(positions[j]) and np.isclose(p.price, extreme[j], rtol=0, atol=1e-9)]
    tip = same[-1] if same else TrendPivot(int(positions[j]), float(extreme[j]),
                                          "H" if sign > 0 else "L", asof)
    state = "observed_turn" if same else "running"
    points.append(tip)
    pending = None
    if len(structure.intervals) == 1:
        # A fifth leg can already be running before it takes out wave 3. Its
        # second Q is an OPEN clock, not a completed interval or a confirmed 5.
        counters = [p for p in fine if p.index > tip.index and p.kind != tip.kind]
        if not same or not counters:
            return None
        fourth = max(counters, key=lambda p: -sign*p.price)
        local4 = mapping[fourth.index]
        if local4 + 1 >= len(positions):
            return None
        j5 = local4 + 1 + int(np.argmax(extreme[local4+1:]) if sign > 0 else np.argmin(extreme[local4+1:]))
        if sign*(extreme[j5]-fourth.price) <= 0 or sign*(extreme[j5]-tip.price) > 0:
            return None
        # Find the first equal extreme for Q's clock, not the later pivot tie.
        tie = np.flatnonzero(np.isclose(extreme[begin:mapping[tip.index]+1], tip.price, rtol=0, atol=1e-9))
        qstart = begin + int(tie[0])
        bars = len(positions)-1-qstart
        relation = compare_spans(structure.intervals[0].bars, max(1, bars))
        if relation.kind == "upgrade":
            return None
        fifth = TrendPivot(int(positions[j5]), float(extreme[j5]), tip.kind, asof)
        height3 = sign*(tip.price-points[-2].price)
        if height3 <= 0:
            return None
        percent4 = sign*(tip.price-fourth.price)/height3*100
        duration4 = mapping[fourth.index]-mapping[tip.index]
        ratios.append(TrendLabel(fourth.index, fourth.price,
            f"子4回调 {percent4:.1f}% · {duration4}根 · 候选", sign,
            fourth.confirmed_at, tip.index, "subwave_retracement", percent4,
            evidence=f"相对子3；起价{points[-2].price:.8g}，末价{tip.price:.8g}，回调末价{fourth.price:.8g}；q2未突破"))
        pending = BookInterval(sign, int(positions[qstart]), asof, tip.price, bars,
            fourth.index, fourth.price, asof, None, None, degree="subwave",
            relation="尚未突破子3，当前仅为已走过根数", state="running")
        points.extend((fourth, fifth))
        tip, state = fifth, "fifth_pending"
    labels = tuple(TrendLabel(p.index, p.price,
        f"子{i}" + ("?·未破浪3" if i == 5 and state == "fifth_pending" else
                    "·进行中" if i == len(points)-1 and state == "running" else ""),
        -sign if i % 2 else sign, max(p.confirmed_at, structure.confirmed_at),
        points[i-1].index, "subwave", evidence="本组独立Q计数，未以父趋势方向过滤")
        for i, p in enumerate(points) if i)
    ruler_origin = points[-2]
    height = sign * (tip.price - ruler_origin.price)
    if height <= 0:
        return None
    levels = tuple(BookLevel(tip.price - sign * ratio * height,
        f"子{len(points)-1} {ratio*100:g}%回调", tip.index, asof, "subwave_retracement")
        for ratio in (.4, .62, .786))
    percent = sign * (tip.price - float(close[-1])) / height * 100
    return SubwaveSeries(sign, tuple(points), structure.intervals, structure, labels,
        tuple(ratios), tuple(corrections), levels, ruler_origin, tip, percent, state,
        max(structure.confirmed_at, *(p.confirmed_at for p in points[:-1])), asof,
        "本组Q独立计时；第一Q收盘、后续Q影线突破；父区间在本组范围外时重新归属；"
        + "；".join(structure.rules), pending, tuple(boundaries))


def analyze_subwaves(frame: pd.DataFrame, positions: np.ndarray, fine: Sequence[TrendPivot],
                     intervals: Sequence[BookInterval], atr: np.ndarray,
                     parent: WaveStructure | None = None) -> tuple[SubwaveSeries, ...]:
    """Resolve recent local degrees in BOTH directions, returning current first.

    Search at most 48 observed turning origins. Select the largest independently
    coupled local Q at a shared current extreme, rather than five arbitrary
    ZigZag points. Overlapping alternative counts remain out of the plot.
    """
    if len(fine) < 4 or not intervals:
        return ()
    candidates = []
    high, low = frame.high.to_numpy(dtype=float), frame.low.to_numpy(dtype=float)
    for origin in fine[-48:]:
        sign = -1 if origin.kind == "H" else 1
        begin = int(np.searchsorted(positions, origin.index))
        if len(positions) - begin < 7:
            continue
        local_frame, local_pos = frame.iloc[begin:], positions[begin:]
        # A main leg's directional extremes must be records of this episode.
        # Excursions entirely inside a rebound belong to a finer degree.
        extreme = high[begin:] if sign > 0 else -low[begin:]
        record = np.maximum.accumulate(extreme)
        eligible = [p for p in intervals if p.direction == sign and p.start_index > origin.index
                    and not p.ambiguous and np.isclose(sign*p.level,
                        record[int(np.searchsorted(local_pos, p.start_index))], rtol=0, atol=1e-9)]
        if not eligible:
            continue
        ids = {p.start_index for p in eligible}
        eligible = [replace(p, parent_start=p.parent_start if p.parent_start in ids else None)
                    for p in eligible]
        structure = build_structure(eligible, sign, origin.index, origin.price,
                                    local_frame, local_pos)
        if structure is None or len(structure.intervals) not in {1, 2, 3}:
            continue
        first = structure.intervals[0]
        first_j = int(np.searchsorted(positions, first.start_index))
        # Choose the observed extreme that actually starts this local episode;
        # a later intervening high/low cannot be hidden inside its first leg.
        initial = high[begin:first_j+1] if sign < 0 else -low[begin:first_j+1]
        if len(initial) and float(np.max(initial)) > -sign*origin.price + 1e-9:
            continue
        earlier = [p for p in eligible if p.start_index < first.start_index]
        if earlier:  # a degree upgrade must use its new observed leg origin
            continue
        if parent and len(structure.intervals) >= 2 and sign == parent.direction and (
                tuple(p.start_index for p in structure.intervals) == tuple(p.start_index for p in parent.intervals)
                and origin.index <= first.start_index):
            continue
        series = _series(origin, structure, frame, positions, fine, atr)
        if series:
            candidates.append(series)
    # Current extreme first, then largest local Q; this preserves the outer
    # five within the recent move instead of replacing it with tiny subcounts.
    candidates.sort(key=lambda s: (s.points[-1].index,
        min(p.bars for p in s.intervals), s.points[-1].index-s.points[0].index), reverse=True)
    selected = []
    for series in candidates:
        if any(not (series.points[-1].index < old.points[0].index
                    or series.points[0].index > old.points[-1].index) for old in selected):
            continue
        selected.append(series)
        if len(selected) == 2:
            break
    return tuple(selected)
