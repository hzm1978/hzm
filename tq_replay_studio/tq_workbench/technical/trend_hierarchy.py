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
    parent_leg: tuple[int, int] | None = None
    historical: bool = False
    depth: int = 1
    owner: tuple[int, int] | None = None
    owner_wave: int | None = None
    alternative: bool = False

    @property
    def label(self):
        name = ("备选" if self.alternative else "历史" if self.historical else "") + ("下降" if self.direction < 0 else "上升")
        suffix = ("历史段·确认条件见明细" if self.historical else
                  "第5浪候选·未破浪3" if self.state == "fifth_pending" else
                  "末端进行中" if self.state == "running" else "末端转折已观察")
        if self.state != "fifth_pending" and not self.structure.complete:
            suffix += "·" + ("待第三Q" if "five" in self.structure.kind and self.structure.kind != "same_five"
                              else "子结构待耦合")
        return f"{name}{len(self.points)-1}{'孙' if self.depth > 1 else '子'}浪 · {suffix}"


def correction_in_leg(start: TrendPivot, endpoint: TrendPivot | None, direction: int,
                      pivots: Sequence[TrendPivot], frame: pd.DataFrame,
                      positions: np.ndarray, atr: np.ndarray, *, include_running_tip=False) -> TrendCorrection | None:
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
    elif include_running_tip:
        reference = next((p for p in reversed(within) if p.kind == start.kind),start)
        begin = int(np.searchsorted(positions,reference.index,side="right"))
        if begin < len(positions):
            values = frame["low" if direction > 0 else "high"].to_numpy(dtype=float)
            local = begin+int(np.argmax(-direction*values[begin:]))
            current = TrendPivot(int(positions[local]),float(values[local]),
                                 "L" if direction > 0 else "H",int(positions[-1]))
            if -direction*(current.price-reference.price) > 0:
                if within and within[-1].kind != start.kind:
                    if -direction*(current.price-within[-1].price) > 0:
                        within[-1] = current
                else:
                    within.append(current)
    if not endpoint and within and within[-1].kind == start.kind:
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


def _pending_fifth(points, structure, positions, fine, extreme, begin, close):
    sign, tip = structure.direction, points[-1]
    mapping = {int(x): i for i, x in enumerate(positions)}
    asof = int(positions[-1])
    counters = [p for p in fine if p.index > tip.index and p.kind != tip.kind]
    if not counters or not any(p.index == tip.index and p.kind == tip.kind for p in fine):
        return None
    fourth = max(counters, key=lambda p: -sign*p.price)
    local4 = mapping[fourth.index]
    if local4+1 >= len(positions):
        return None
    j5 = local4+1+int(np.argmax(extreme[local4+1:]) if sign > 0 else np.argmin(extreme[local4+1:]))
    if sign*(extreme[j5]-fourth.price) <= 0 or sign*(extreme[j5]-tip.price) > 0:
        return None
    if np.any(sign*(close[j5:]-fourth.price) < 0):
        # The rebound has closed beyond the proposed wave-4 end. A smaller
        # opposing leg inside that rebound cannot remain a live wave 5.
        return None
    tie = np.flatnonzero(np.isclose(extreme[begin:mapping[tip.index]+1], tip.price, rtol=0, atol=1e-9))
    if not len(tie):
        return None
    qstart = begin+int(tie[0])
    bars = len(positions)-1-qstart
    if compare_spans(structure.intervals[0].bars, max(1, bars)).kind == "upgrade":
        return None
    height = sign*(tip.price-points[-2].price)
    if height <= 0:
        return None
    percent = sign*(tip.price-fourth.price)/height*100
    duration = mapping[fourth.index]-mapping[tip.index]
    ratio = TrendLabel(fourth.index, fourth.price,
        f"子4回调 {percent:.1f}% · {duration}根 · 候选", sign, fourth.confirmed_at,
        tip.index, "subwave_retracement", percent,
        evidence=f"相对子3；起价{points[-2].price:.8g}，末价{tip.price:.8g}，回调末价{fourth.price:.8g}；q2未突破")
    pending = BookInterval(sign, int(positions[qstart]), asof, tip.price, bars,
        fourth.index, fourth.price, asof, None, None, degree="subwave",
        relation="尚未突破子3，当前仅为已走过根数", state="running")
    fifth = TrendPivot(int(positions[j5]), float(extreme[j5]), tip.kind, asof)
    return fourth, fifth, ratio, pending


def _series(origin, structure, frame, positions, fine, atr, correction_cache=None):
    sign = structure.direction
    mapping = {int(x): i for i, x in enumerate(positions)}
    asof = int(positions[-1])
    points, ratios, corrections, boundaries = [origin], [], [], []
    high, low, close = (frame[k].to_numpy(dtype=float) for k in ("high", "low", "close"))
    correction_cache = correction_cache if correction_cache is not None else {}
    for i, item in enumerate(structure.intervals):
        # Q starts at the FIRST equal high/low. A confirmed pivot can be the
        # last equal extreme a bar later; keep the Q clock and pivot distinct.
        same = [p for p in fine if p.kind == ("H" if sign > 0 else "L")
                and item.start_index <= p.index < item.counter_index
                and np.isclose(p.price, item.level, rtol=0, atol=1e-9)]
        peak = (same[-1] if same else TrendPivot(item.start_index, item.level,
                "H" if sign > 0 else "L", item.confirmed_at))
        cache_key = item, asof
        if cache_key not in correction_cache:
            correction_cache[cache_key] = correction_for_interval(item, fine, frame, positions, atr)
        correction = correction_cache[cache_key]
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
        extra = _pending_fifth(points, structure, positions, fine, extreme, begin, close)
        if extra:
            fourth, fifth, ratio, pending = extra
            # Resolve the whole open correction context as well. A triangle's
            # e can be the end without being its most extreme a. The open Q
            # remains unconfirmed even if its internal return is observable.
            proposed = correction_for_interval(replace(pending, end_index=asof+1),
                                                fine, frame, positions, atr)
            if proposed and proposed.kind == "compound_pending":
                direct = correction_in_leg(tip, fourth, sign, fine, frame, positions, atr)
                if direct and direct.state != "opposite_break":
                    proposed = direct
            if proposed and proposed.end_point and proposed.state == "breakout":
                fourth = proposed.end_point
                k4 = mapping[fourth.index]
                if k4+1 >= len(positions):
                    extra = None
                else:
                    k5 = k4+1+int(np.argmax(extreme[k4+1:]) if sign > 0 else np.argmin(extreme[k4+1:]))
                    # A structural c/e can be less extreme than a. Recheck
                    # this actual endpoint, including the tip bar's close.
                    if (sign*(extreme[k5]-fourth.price) <= 0 or
                            np.any(sign*(close[k5:]-fourth.price) < 0)):
                        extra = None
                    else:
                        fifth = TrendPivot(int(positions[k5]), float(extreme[k5]), tip.kind, asof)
                        height3 = sign*(tip.price-points[-2].price)
                        percent4 = sign*(tip.price-fourth.price)/height3*100
                        ratio = replace(ratio, index=fourth.index, price=fourth.price, percent=percent4,
                            text=f"子4回调 {percent4:.1f}% · {mapping[fourth.index]-mapping[tip.index]}根 · 候选",
                            confirmed_at=max(fourth.confirmed_at, proposed.confirmed_at))
            if extra is not None:
                if proposed:
                    corrections.append(replace(proposed, label="子4·"+proposed.label))
                    boundaries.append(BoundaryPoint(fourth.index, fourth.price, sign, "subwave", 2,
                        proposed.kind, max(fourth.confirmed_at, proposed.confirmed_at), pending.start_index,
                        state="candidate"))
                ratios.append(ratio)
                points.extend((fourth, fifth))
                tip, state = fifth, "fifth_pending"
            else:
                pending = None
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
        tuple(ratios), tuple(replace(c,degree=1,owner=(origin.index,tip.index)) for c in corrections), levels, ruler_origin, tip, percent, state,
        max(structure.confirmed_at, *(p.confirmed_at for p in points[:-1])), asof,
        "本组Q独立计时；第一Q收盘、后续Q影线突破；父区间在本组范围外时重新归属；"
        + "；".join(structure.rules), pending, tuple(boundaries))


def _scan_candidates(frame, positions, fine, intervals, atr, work, parent=None, correction_cache=None,candidate_cache=None):
    candidates, seen = [], set()
    correction_cache = correction_cache if correction_cache is not None else {}
    candidate_cache = candidate_cache if candidate_cache is not None else {}
    high, low = frame.high.to_numpy(dtype=float), frame.low.to_numpy(dtype=float)
    raw_by_sign = {sign: [p for p in intervals if p.direction == sign and not p.ambiguous] for sign in (-1, 1)}
    for origin, scope in work:
        context_parent = (parent if parent and scope[0] <= parent.origin_index
                          and scope[1] >= parent.intervals[-1].end_index else None)
        cache_key = origin.index,scope,bool(context_parent)
        if cache_key in candidate_cache:
            if candidate_cache[cache_key] is not None:
                candidates.append(candidate_cache[cache_key])
            continue
        candidate_cache[cache_key] = None
        sign = -1 if origin.kind == "H" else 1
        begin = int(np.searchsorted(positions, origin.index))
        stop = int(np.searchsorted(positions, scope[1], side="right"))
        if stop - begin < 7:
            continue
        local_frame, local_pos = frame.iloc[begin:stop], positions[begin:stop]
        # A main leg's directional extremes must be records of this episode.
        # Excursions entirely inside a rebound belong to a finer degree.
        extreme = high[begin:stop] if sign > 0 else -low[begin:stop]
        record = np.maximum.accumulate(extreme)
        available = [p for p in raw_by_sign[sign] if origin.index < p.start_index and p.end_index <= scope[1]]
        anchors = np.searchsorted(local_pos, [p.start_index for p in available])
        is_record = np.isclose([sign*p.level for p in available], record[anchors], rtol=0, atol=1e-9)
        eligible = [replace(p, first_confirmed_at=None, first_bars=None)
                    if p.first_confirmed_at is not None and p.first_confirmed_at > scope[1] else p
                    for p, keep in zip(available, is_record) if keep]
        if not eligible:
            continue
        signature = sign, tuple(p.start_index for p in eligible), scope
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
        if signature in seen:
            continue
        seen.add(signature)
        if context_parent and len(structure.intervals) >= 2 and sign == context_parent.direction and (
                tuple(p.start_index for p in structure.intervals) == tuple(p.start_index for p in context_parent.intervals)
                and origin.index <= first.start_index):
            continue
        scoped_fine = tuple(p for p in fine if p.index <= scope[1])
        series = _series(origin, structure, frame.iloc[:stop], positions[:stop], scoped_fine, atr[:stop], correction_cache)
        if (series and len(series.points) == 4 and context_parent
                and sign == context_parent.direction and tuple(p.start_index for p in structure.intervals) == tuple(p.start_index for p in context_parent.intervals)):
            continue
        if series:
            candidate_cache[cache_key] = replace(series,parent_leg=scope,as_of_index=int(positions[-1]))
            candidates.append(candidate_cache[cache_key])
    return candidates


def _select_nonoverlapping(candidates, limit=12):
    chosen = []
    for series in sorted(candidates,key=lambda s:(len(s.points)>=6,
            min(q.bars for q in s.intervals),s.points[-1].index,
            s.points[-1].index-s.points[0].index),reverse=True):
        if any(not(series.points[-1].index <= old.points[0].index
                   or series.points[0].index >= old.points[-1].index) for old in chosen):
            continue
        chosen.append(series)
        if len(chosen) == limit:
            break
    return chosen


def _at_depth(series,depth,owner,wave):
    prefix = "子" if depth == 1 else "孙"
    def caption(text):
        return prefix+text[1:] if text.startswith("子") else text
    return replace(series,depth=depth,owner=owner,owner_wave=wave,
        labels=tuple(replace(p,text=caption(p.text)) for p in series.labels),
        retracements=tuple(replace(p,text=caption(p.text)) for p in series.retracements),
        corrections=tuple(replace(p,label=caption(p.label),degree=depth,
            owner=(series.points[0].index,series.points[-1].index)) for p in series.corrections),
        boundaries=tuple(replace(p,degree="subwave" if depth == 1 else "grandchild") for p in series.boundaries),
        levels=tuple(replace(p,label=caption(p.label)) for p in series.levels))


def analyze_subwaves(frame: pd.DataFrame, positions: np.ndarray, fine: Sequence[TrendPivot],
                     intervals: Sequence[BookInterval], atr: np.ndarray,
                     parent: WaveStructure | None = None,
                     parent_points: Sequence[TrendPivot] = ()) -> tuple[SubwaveSeries, ...]:
    """Resolve recent local degrees in BOTH directions, returning current first.

    Resolve observed parent-leg scopes separately so a completed historical
    child is not swallowed by the next parent leg. Search the full supplied
    lookback; overlapping alternative counts remain out of the plot.
    """
    if len(fine) < 4 or not intervals:
        return ()
    correction_cache, candidate_cache = {}, {}
    scopes = [(a.index, b.index) for a, b in zip(parent_points, parent_points[1:])]
    if parent_points and parent_points[-1].index < int(positions[-1]):
        scopes.append((parent_points[-1].index, int(positions[-1])))
    work = []
    for origin in fine:
        scope = next((s for s in scopes if s[0] <= origin.index < s[1]),
                     (int(positions[0]), int(positions[-1])))
        work.append((origin, scope))
        # A nominal parent pivot can itself be an inner turn before the old
        # outer high/low has been exceeded. Also test the full observed tail;
        # otherwise that parent interpretation cuts a complete child in half.
        if scope[1] < int(positions[-1]):
            work.append((origin, (scope[0], int(positions[-1]))))
    candidates = _scan_candidates(frame,positions,fine,intervals,atr,work,parent,correction_cache,candidate_cache)
    # Resolve each parent leg first. A child of a complex main wave is allowed
    # to overlap its parent in time; only sibling alternatives exclude each
    # other. The old global exclusion discarded exactly these inner counts.
    aligned = []
    for number,scope in enumerate(scopes,1):
        if scope[1]-scope[0] < 7:
            continue
        work = [(p,scope) for p in fine if scope[0] <= p.index < scope[1]]
        local = _scan_candidates(frame,positions,fine,intervals,atr,work,
                                 correction_cache=correction_cache,candidate_cache=candidate_cache)
        aligned += [_at_depth(s,1,scope,number) for s in _select_nonoverlapping(local,4)]
    selected = _select_nonoverlapping(candidates)
    identities = {(s.direction,tuple(p.index for p in s.points)) for s in aligned}
    alternatives = []
    for series in selected:
        key = series.direction,tuple(p.index for p in series.points)
        if key in identities:
            continue
        conflicts = [old for old in aligned if not(series.points[-1].index <= old.points[0].index
                     or series.points[0].index >= old.points[-1].index)]
        if conflicts:
            if len(series.points) >= 6 and all(len(old.points) < 6 for old in conflicts):
                # A later provisional parent pivot must not cut an already
                # coupled five/seven into several incomplete three-wave pieces.
                aligned = [old for old in aligned if old not in conflicts]
                alternatives += [replace(old,alternative=True) for old in conflicts]
                aligned.append(series);identities.add(key)
            else:
                alternatives.append(replace(series,alternative=True))
        else:
            aligned.append(series);identities.add(key)
    # Keep one direct lower level inside every selected child leg. The labels
    # explicitly distinguish 子 and 孙 instead of reusing 子1 in every layer.
    nested = []
    for parent_series in aligned:
        for number,(left,right) in enumerate(zip(parent_series.points,parent_series.points[1:]),1):
            scope = left.index,right.index
            if scope[1]-scope[0] < 7:
                continue
            work = [(p,scope) for p in fine if scope[0] <= p.index < scope[1]]
            local = _scan_candidates(frame,positions,fine,intervals,atr,work,
                                     correction_cache=correction_cache,candidate_cache=candidate_cache)
            nested += [replace(_at_depth(s,2,(parent_series.points[0].index,parent_series.points[-1].index),number),
                               historical=scope[1] < int(positions[-1]))
                       for s in _select_nonoverlapping(local,2)]
    primary = [replace(s,historical=any(other.points[0].index >= s.points[-1].index
               and other.points[-1].index > s.points[-1].index for other in aligned)) for s in aligned]
    primary = [replace(s,labels=tuple(replace(p,text=p.text.replace("·进行中","")) for p in s.labels))
               if s.historical else s for s in primary]
    result = sorted(primary,key=lambda s:s.points[-1].index,reverse=True)
    seen = {(s.direction,tuple(p.index for p in s.points)) for s in result}
    alternatives = [replace(s,historical=any(other.points[0].index >= s.points[-1].index
                    and other.points[-1].index > s.points[-1].index for other in aligned)) for s in alternatives]
    for series in sorted(nested,key=lambda s:s.points[-1].index,reverse=True)+alternatives:
        key = series.direction,tuple(p.index for p in series.points)
        if key not in seen:
            if series.historical:
                series = replace(series,labels=tuple(replace(p,text=p.text.replace("·进行中","")) for p in series.labels))
            result.append(series);seen.add(key)
    return tuple(result)
