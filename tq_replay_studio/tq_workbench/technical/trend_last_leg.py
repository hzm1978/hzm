from __future__ import annotations

"""Choose one price ruler from the latest observed leg, using closed bars only."""

from dataclasses import dataclass, replace
import numpy as np

from .trend_types import TrendPivot, TrendLabel
from .trend_projections import MainWaveTarget, ReverseFibonacci, retracement_fibonacci
from .trend_intervals import compare_spans


def price_text(price):
    return f"{float(price):.8f}".rstrip("0").rstrip(".")


def reference_precision(frame):
    values = frame[["open","high","low","close"]].tail(200).to_numpy(dtype=float)
    for digits in (2,3,4):
        if np.all(np.abs(values-np.round(values,digits)) < 1e-7):
            return digits
    return 2


@dataclass(frozen=True)
class LastLegOverlay:
    mode: str  # targets / fibonacci / pending
    market: str
    direction: int
    leg_direction: int
    origin: TrendPivot | None
    tip: TrendPivot | None
    turns: tuple[TrendPivot, ...]
    degree: str
    wave: int | None
    fibonacci: ReverseFibonacci | None
    targets: tuple[MainWaveTarget, ...]
    all_targets: tuple[MainWaveTarget, ...]
    evidence: str
    working_intervals: tuple[int, ...] = ()
    context_key: tuple[str, int, int] | None = None


def _with_context(leg,group):
    if group is None:
        return leg
    evidence = f"工作{group.degree}级Q根数：{','.join(map(str,group.interval_bars))}；"+leg.evidence
    return replace(leg,evidence=evidence,working_intervals=group.interval_bars,
                   context_key=(group.degree,group.first_origin.index,group.active_tip.index))


def _counter_broke_a(group,frame,positions):
    boundary = group.confirmed_a
    price = boundary.price if boundary else group.first_origin.price
    known = max(group.active_tip.index,boundary.confirmed_at if boundary else group.first_origin.confirmed_at)
    after = int(np.searchsorted(positions,known,side="right"))
    return bool(np.any(group.direction*(frame.close.to_numpy(dtype=float)[after:]-price) < 0))


def working_projection(projections,frame,positions,show_subwaves=True):
    """Keep the working degree separate from the newest internal structure.

    Time-overlapping counts have explicit parent ownership. A smaller opposite
    structure alone does not replace a degree whose A has not closed through.
    """
    main = next((g for g in projections if g.degree == "主" and not g.alternative),None)
    if not show_subwaves:
        return main
    children = [g for g in projections if g.degree == "子" and not g.alternative]
    current = next((g for g in children if not g.historical),None)
    if current is None:
        return main if main is not None else (children[0] if children else None)
    previous = [g for g in children if g.direction == -current.direction
                and g.active_tip.index <= current.first_origin.index
                and abs(g.active_tip.price-current.first_origin.price) <= max(abs(g.active_tip.price)*1e-8,1e-8)]
    if previous and current.interval_bars:
        prior = max(previous,key=lambda g:g.active_tip.index)
        if prior.interval_bars:
            relation = compare_spans(max(prior.interval_bars),max(current.interval_bars))
            if relation.kind in {"contracting","downgrade"} and not _counter_broke_a(prior,frame,positions):
                current = prior
    if main and main.interval_bars and current.interval_bars:
        relation = compare_spans(max(main.interval_bars),max(current.interval_bars))
        inside = (main.direction == current.direction
                  and main.active_origin.index <= current.active_origin.index
                  and current.active_tip.index <= main.active_tip.index)
        counter = (main.direction != current.direction
                   and main.active_tip.index <= current.first_origin.index
                   and len(main.interval_bars) >= 2)
        if ((inside or counter) and relation.kind in {"contracting","downgrade"}
                and not _counter_broke_a(main,frame,positions)):
            return main
    return current


def _sideways(frame, positions, pivots, corrections, origin):
    asof = int(positions[-1])
    for item in corrections:
        if (item.kind in {"triangle", "expanding_triangle", "abcde_sideways"}
                and item.start_index >= origin
                and item.state == "candidate"
                and item.markers[-1].index >= asof-3):
            return True
    # Range observation is separate from a confirmed book correction pattern.
    recent = frame.iloc[-12:]
    start = int(positions[max(0, len(positions)-12)])
    turns = [p for p in pivots if p.index >= start]
    if len(recent) < 8 or len(turns) < 4:
        return False
    close = recent.close.to_numpy(dtype=float)
    path = float(np.abs(np.diff(close)).sum())
    prior = recent.iloc[:-1]
    if close[-1] < prior.low.min()-1e-9 or close[-1] > prior.high.max()+1e-9:
        return False
    pairs = list(zip(turns[-4:], turns[-3:]))
    overlap = min(max(a.price,b.price) for a,b in pairs)-max(min(a.price,b.price) for a,b in pairs)
    span = float(recent.high.max()-recent.low.min())
    return bool(path > 0 and span > 0 and abs(close[-1]-close[0])/path <= .25 and overlap >= .1*span)


def _targets(group, origin, wave, frame, positions):
    templates = [p for p in group.targets if p.kind == "whole" or p.wave == group.wave]
    result = []
    for target in templates:
        if target.kind == "whole":
            result.append(target)
            continue
        price = origin.price+group.direction*target.ratio*target.reference_height
        first = int(np.searchsorted(positions, origin.index, side="right"))
        values = frame["high" if group.direction > 0 else "low"].to_numpy(dtype=float)
        hits = np.flatnonzero(group.direction*(values[first:]-price) >= 0)
        hit = int(positions[first+int(hits[0])]) if len(hits) else None
        known = max(target.confirmed_at, origin.confirmed_at)
        result.append(replace(target, wave=wave, price=float(price), origin=origin,
            confirmed_at=known, hit_index=hit, hit_confirmed_at=max(hit,known) if hit is not None else None,
            historical=False))
    return tuple(result)


def last_leg_overlay(frame, positions, pivots, projections, corrections=(), *, show_subwaves=True):
    asof = int(positions[-1])
    group = working_projection(projections,frame,positions,show_subwaves)
    direction = group.direction if group else 0
    degree, wave = (group.degree, group.wave) if group else ("观察", 0)
    sequence = list((group.active_origin, group.active_tip)) if group else list(pivots[-3:])
    observed = bool(group and any(p.index == group.active_tip.index and p.kind == group.active_tip.kind for p in pivots))
    running = bool(group and not observed)
    if len(sequence) < 2:
        return _with_context(LastLegOverlay("pending", "unresolved", direction, 0, None, None,
            tuple(sequence), degree, wave, None, (), (), "尚无可观察的前一完整浪段"),group)
    if running:
        origin, tip = sequence[:2]
        leg_direction = direction
        # The start and preceding turn are the two junctions of interest.
        previous = group.reverse.start if group.reverse else next((p for p in reversed(pivots) if p.index < origin.index), None)
        turns = tuple(p for p in (previous, origin) if p is not None)
        preceding = previous
    else:
        preceding, origin = sequence[-2:]
        leg_direction = 1 if origin.kind == "L" else -1
        begin = int(np.searchsorted(positions, origin.index, side="right"))
        if begin >= len(positions):
            return _with_context(LastLegOverlay("pending", "unresolved", direction, leg_direction,
                origin, None, (preceding,origin), degree, wave, None, (), (), "转折后尚无已收盘K线"),group)
        values = frame["high" if leg_direction > 0 else "low"].to_numpy(dtype=float)
        j = begin+int(np.argmax(leg_direction*values[begin:]))
        tip = TrendPivot(int(positions[j]), float(values[j]), "H" if leg_direction > 0 else "L", asof)
        turns = (preceding, origin)
    sideways = _sideways(frame, positions, pivots, corrections,
                         group.active_origin.index if group else int(positions[0]))
    if group and leg_direction == direction and not sideways:
        all_targets = _targets(group, origin, wave, frame, positions)
        # Show the nearest three still possible book levels; completed/hit and
        # farther extensions stay in details, rather than filling the plot.
        future = sorted((p for p in all_targets if p.hit_index is None and direction*(p.price-tip.price) > 1e-8),
                        key=lambda p: direction*(p.price-tip.price))
        selected, seen = [], set()
        for target in future:
            key = round(target.price,8)
            if key not in seen:
                selected.append(target); seen.add(key)
            if len(selected) == 3:
                break
        return _with_context(LastLegOverlay("targets", "trend", direction, leg_direction, origin, tip,
            turns, degree, wave, None, tuple(selected), all_targets,
            "末段顺所属级别推进；仅显示同级H1的候选止盈线。扩展位仍需强势条件；不同时画黄金分割"),group)
    ruler = (retracement_fibonacci(preceding, origin, leg_direction,
                max(preceding.confirmed_at,origin.confirmed_at), candidate=True) if preceding else None)
    return _with_context(LastLegOverlay("fibonacci" if ruler else "pending", "sideways" if sideways else "correction",
        direction, leg_direction, origin, tip, turns, degree, wave, ruler, (), (),
        "横盘调整：仅用最后一段之前的完整浪段画黄金分割" if sideways else
        "末段反向调整：从前一主浪末端量回起点，只画黄金分割，不显示候选止盈"),group)


def turn_observations(patterns, turns, asof, reversals=()):
    result = []
    priority = ("鬼门关", "确认", "补缺", "蓝桥", "明星", "昏星", "海底", "三影", "抱", "锤", "十字", "跳空")
    formal = {(p.index,p.direction,p.text) for p in reversals}
    for turn in turns[-2:]:
        sign = 1 if turn.kind == "L" else -1
        candidates = [p for p in patterns if p.direction == sign and p.confirmed_at <= asof
                      and p.start_index-1 <= turn.index <= p.index+1
                      and abs(p.index-turn.index) <= 3]
        candidates.sort(key=lambda p:(not p.qualified,
            next((i for i,s in enumerate(priority) if s in p.text),99),abs(p.index-turn.index)))
        used = set()
        for p in candidates:
            if p.text in used or (p.index,p.direction,p.text) in formal:
                continue
            used.add(p.text)
            result.append(replace(p, text="转折观察·"+p.text+("·位置待确认" if p.qualified else "·候选"),
                context=f"转折#{turn.index}",
                confirmed_at=max(p.confirmed_at,turn.confirmed_at),
                evidence=p.evidence+f"；对应最近转折#{turn.index}，仅作形态观察，不提升为正式反转信号"))
            if len(used) == 2:
                break
    return tuple({(p.index,p.direction,p.text):p for p in result}.values())
