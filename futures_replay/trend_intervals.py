from __future__ import annotations

"""Third-edition interval and degree engine (printed pp. 140–193, 428–611).

Q is elapsed *bars*, not elapsed calendar days or an ATR swing. Raw observations
and the current structural interpretation are deliberately separate: new data
may reclassify a degree, but may never be used before its confirmation time.
"""

from dataclasses import dataclass, replace
from typing import Sequence

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class SpanRelation:
    kind: str
    method: str
    value: float

    @property
    def coupled(self):
        return self.kind in {"same", "expanding", "contracting"}

    @property
    def text(self):
        name = {"same": "同级", "expanding": "扩张", "contracting": "收缩",
                "upgrade": "升级", "downgrade": "降级"}[self.kind]
        value = f"{self.value:+.0f}" if self.method == "dif" else f"{self.value:.3f}"
        return f"{name} {self.method}={value}"


def compare_spans(previous: int, current: int) -> SpanRelation:
    if min(previous, current) <= 0:
        raise ValueError("区间跨度必须大于零")
    if max(previous, current) <= 10:
        value = current - previous
        kind = ("same" if abs(value) <= 2 else
                "expanding" if 3 <= value <= 5 else
                "contracting" if -5 <= value <= -3 else
                "upgrade" if value > 5 else "downgrade")
        # The printed mnemonic stops at ±8. ±9 is the same monotonic extension,
        # documented as an implementation convention, not a quoted book rule.
        return SpanRelation(kind, "dif", float(value))
    value = current / previous
    eps = 1e-12
    kind = ("same" if .828 - eps <= value <= 1.208 + eps else
            "contracting" if .382 - eps <= value < .828 else
            "expanding" if 1.208 < value <= 2.618 + eps else
            "downgrade" if value < .382 else "upgrade")
    return SpanRelation(kind, "δ", value)


@dataclass(frozen=True)
class BookInterval:
    direction: int
    start_index: int
    end_index: int
    level: float
    bars: int
    counter_index: int
    counter_price: float
    confirmed_at: int
    first_confirmed_at: int | None
    first_bars: int | None
    hidden: bool = False
    parent_start: int | None = None
    degree: str = "unclassified"
    relation: str = ""
    state: str = "confirmed"
    ambiguous: bool = False
    components: tuple[int, ...] = ()


@dataclass(frozen=True)
class WaveStructure:
    direction: int
    intervals: tuple[BookInterval, ...]
    navigation: tuple[BookInterval, ...]
    kind: str
    label: str
    complete: bool
    rules: tuple[str, ...]
    origin_index: int
    origin_price: float
    reference_bars: int
    confirmed_at: int
    degree: str = "待定"


@dataclass(frozen=True)
class BoundaryPoint:
    index: int
    price: float
    direction: int
    degree: str
    number: int
    pattern: str
    confirmed_at: int
    interval_start: int
    broken_at: int | None = None
    state: str = "confirmed"

    @property
    def label(self):
        prefix = "主" if self.degree == "main" else "子" if self.degree == "subwave" else "导"
        return f"{prefix}A{self.number}"


@dataclass(frozen=True)
class BookLevel:
    price: float
    label: str
    start_index: int
    confirmed_at: int
    kind: str


def find_intervals(frame: pd.DataFrame, positions: np.ndarray,
                   min_bars: int = 2) -> tuple[BookInterval, ...]:
    """All observable peak/valley excursions, including nested/hidden ones.

    A local extreme needs just the next closed bar to start its clock; no ATR
    filter is used. Store BOTH first-wick and first-close timings. The first
    interval of any proposed trend must use the close timing, later ones may
    use wick timing (pp. 140–145). A same-bar high/low order is never invented.
    """
    high, low, close, opening = (frame[k].to_numpy(dtype=float)
                                 for k in ("high", "low", "close", "open"))
    output = []
    n = len(close)
    for direction in (1, -1):
        extreme = high if direction > 0 else -low
        closing = direction * close
        counter = low if direction > 0 else -high
        # A cropped history's first candle has no observed predecessor: it
        # cannot establish a NEW high/low. Otherwise a flat warmup becomes a
        # fictitious very large Q and incorrectly dominates all later degrees.
        for i in range(1, n - 2):
            if extreme[i] <= extreme[i - 1]:
                continue
            if extreme[i + 1] > extreme[i]:
                continue
            # An equal high/low is not a NEW record: the clock remains at the
            # first high/low instead of shifting a bar forward across a tie.
            crossings = np.flatnonzero(extreme[i + 1:] > extreme[i])
            if not len(crossings):
                continue
            end = i + 1 + int(crossings[0])
            if end - i < max(2, min_bars):
                continue
            closes = np.flatnonzero(closing[i + 1:] > extreme[i])
            first_end = i + 1 + int(closes[0]) if len(closes) else None
            counter_i = i + 1 + int(np.argmin(counter[i + 1:end + 1]))
            # All colours in the interval, excluding the initial extreme bar.
            hidden = bool(np.all(direction * (close[i + 1:end + 1] - opening[i + 1:end + 1]) > 0))
            output.append(BookInterval(
                direction, int(positions[i]), int(positions[end]),
                float(high[i] if direction > 0 else low[i]), end - i,
                int(positions[counter_i]), float(low[counter_i] if direction > 0 else high[counter_i]),
                int(positions[end]), int(positions[first_end]) if first_end is not None else None,
                first_end - i if first_end is not None else None, hidden,
                ambiguous=counter_i == end,
            ))
    # Tightest containing interval is the direct parent (not a guessed ATR tier).
    result = []
    for item in output:
        parents = [p for p in output if p.direction == item.direction
                   and p.start_index < item.start_index and p.end_index >= item.end_index]
        parent = min(parents, key=lambda p: p.bars) if parents else None
        result.append(replace(item, parent_start=parent.start_index if parent else None))
    return tuple(sorted(result, key=lambda p: (p.end_index, p.start_index, p.direction)))


def build_structure(intervals: Sequence[BookInterval], direction: int,
                    origin_index: int, origin_price: float,
                    frame: pd.DataFrame, positions: np.ndarray) -> WaveStructure | None:
    """One deterministic count subject to the book's eight counting rules.

    The largest observed Q establishes the working degree. Smaller contained
    intervals are resolved first; only compatible outer intervals count toward
    the five/seven-wave structure. All decisions carry their evidence time.
    """
    candidates = [p for p in intervals if p.direction == direction and p.start_index >= origin_index]
    if not candidates:
        return None
    closed = [p for p in candidates if p.first_confirmed_at is not None and not p.ambiguous]
    if not closed:
        return None
    reference = max(closed, key=lambda p: (p.first_bars or p.bars, p.end_index))
    ref_bars = reference.first_bars or reference.bars
    eligible, navigation, rules = [], [], []
    earlier = [p for p in candidates if p.end_index <= reference.start_index and p.parent_start is None]
    if earlier and compare_spans(max(earlier, key=lambda p: p.end_index).bars, ref_bars).kind == "upgrade":
        rules.append("法则7：最大Q相对之前区间升级，之前所有浪并入前一主浪")
    for p in candidates:
        relation = compare_spans(ref_bars, p.bars)
        if relation.kind == "downgrade" or (p.parent_start is not None and p.start_index != reference.start_index):
            navigation.append(replace(p, degree="navigation", relation=relation.text))
        elif relation.coupled or p.start_index == reference.start_index:
            eligible.append(p)
        else:
            navigation.append(replace(p, degree="navigation", relation=relation.text))
    # Nonoverlapping outer spans; the wider one owns overlapping interpretations.
    selected = []
    for p in sorted(eligible, key=lambda x: (x.start_index, -x.bars)):
        if selected and p.start_index < selected[-1].end_index:
            navigation.append(replace(p, degree="navigation", relation="外区间内，先内后外"))
            continue
        selected.append(p)
    main = []
    for item in selected:
        if not main:
            if item.first_confirmed_at is None:
                navigation.append(replace(item, state="wick_only", degree="navigation", relation="第一Q待收盘越过"))
                continue
            item = replace(item, end_index=item.first_confirmed_at,
                           confirmed_at=item.first_confirmed_at,
                           bars=item.first_bars or item.bars)
        if main and item.start_index < main[-1].end_index:
            navigation.append(replace(item, degree="navigation", relation="第一Q收盘确认前的内区间"))
            continue
        # Rule 3: a continued trend after a 7-wave interpretation resolves the
        # equal trailing pair internally before adding the new outer interval.
        if len(main) >= 3 and compare_spans(main[-2].bars, main[-1].bars).kind == "same":
            navigation.extend(replace(x, degree="navigation", relation="法则3：同级别合并降级") for x in main[-2:])
            main = main[:-2]
            rules.append("法则3：延续后将末两个同级区间合并降级")
        if main:
            relation = compare_spans(main[-1].bars, item.bars)
            if relation.kind == "upgrade":
                navigation.extend(replace(x, degree="navigation", relation="法则7：前浪整体升级合并") for x in main)
                main = []
                rules.append("法则7：新Q升级，之前所有浪并入前一主浪")
            elif relation.kind == "downgrade":
                navigation.append(replace(item, degree="navigation", relation=relation.text))
                rules.append("法则8：降级Q作为子区间")
                continue
            item = replace(item, relation=relation.text)
        main.append(replace(item, degree="main"))
        # Rule 1: three consecutive same-degree spans do not mean wave 7.
        if len(main) >= 3 and all(compare_spans(a.bars, b.bars).kind == "same"
                                  for a, b in zip(main[-3:], main[-2:])):
            navigation.extend(replace(x, degree="navigation", relation="法则1：同级别合并降级") for x in main[-2:])
            main = main[:-2]
            rules.append("法则1：第三同级Q出现，末两个降级，等待新外区间")
    if not main:
        return None
    # Rule 5 compares observed main-leg heights, never time spans or ATR.
    if len(main) >= 2:
        a, b = main[-2:]
        p0 = int(np.searchsorted(positions, a.counter_index))
        p1 = int(np.searchsorted(positions, b.start_index))
        p2 = int(np.searchsorted(positions, b.counter_index))
        if p1 > p0 and p2 < len(positions):
            ext = frame["high" if direction > 0 else "low"].to_numpy(dtype=float)
            previous_height = direction * (ext[p1] - a.counter_price)
            current_extreme = (float(np.max(ext[p2:])) if direction > 0 else float(np.min(ext[p2:])))
            current_height = direction * (current_extreme - b.counter_price)
            if previous_height > 0 and current_height > 1.618 * previous_height:
                navigation.extend(replace(x, degree="navigation", relation="法则5：后主浪高度超过1.618倍") for x in (a, b))
                crossing = np.flatnonzero(direction * (ext[p2:] - b.counter_price) > 1.618 * previous_height)
                triggered_at = int(positions[p2 + int(crossing[0])])
                merged = replace(a, end_index=b.end_index, bars=int(np.searchsorted(positions, b.end_index)) - int(np.searchsorted(positions, a.start_index)),
                                 counter_index=b.counter_index, counter_price=b.counter_price,
                                 confirmed_at=max(b.confirmed_at, triggered_at),
                                 components=(a.start_index, b.start_index), relation="法则5：合并区间")
                main[-2:] = [merged]
                rules.append("法则5：后一主浪高度>前一主浪1.618倍，前后Q合并")
    if navigation:
        rules.append("法则4/8：先内后外，导航Q单独归类")
    rules.append("法则6：不以浪2超过浪1起点否定调整结构")
    kind, label, complete = "three", "3浪·待耦合", False
    if len(main) >= 2:
        first = compare_spans(main[-2].bars, main[-1].bars)
        if len(main) == 2 and first.kind == "same":
            kind, label, complete = "same_five", "同级5浪·主结构候选完整", True
        elif len(main) == 2:
            kind = "expanding_five" if first.kind == "expanding" else "contracting_five"
            label = ("扩张" if first.kind == "expanding" else "收缩") + "5浪·待第三Q"
        else:
            r1 = compare_spans(main[-3].bars, main[-2].bars).kind
            r2 = compare_spans(main[-2].bars, main[-1].bars).kind
            if r1 == "expanding":
                kind = "diamond_seven" if r2 in {"contracting", "downgrade"} else "expanding_seven"
            elif r1 == "contracting":
                kind = "x_seven" if r2 in {"expanding", "upgrade"} else "contracting_seven"
            else:
                kind = "same_five"
            label = {"diamond_seven": "钻石7浪", "expanding_seven": "扩张7浪",
                     "x_seven": "X7浪", "contracting_seven": "收缩7浪", "same_five": "同级5浪"}[kind] + "·主结构候选完整"
            complete = True
    # Completeness of the main degree is not completeness of its last subwave.
    children = [p for p in navigation if p.start_index > main[-1].counter_index]
    if complete and len(children) == 1:
        complete = False
        label += " / 子浪未耦合"
    return WaveStructure(direction, tuple(main), tuple(sorted({(p.direction, p.start_index, p.end_index): p for p in navigation}.values(), key=lambda p: p.end_index)),
                         kind, label, complete, tuple(dict.fromkeys(rules)), origin_index,
                         float(origin_price), ref_bars, max(p.confirmed_at for p in main))


def failed_interval_candidate(structure: WaveStructure, frame: pd.DataFrame,
                              positions: np.ndarray) -> BookInterval | None:
    """Second-or-later failure candidate, never a completed new high/low."""
    last = structure.intervals[-1]
    direction = structure.direction
    start = int(np.searchsorted(positions, last.end_index))
    ext = frame["high" if direction > 0 else "low"].to_numpy(dtype=float)
    # Last observed record extreme after the previous confirmed interval.
    k = start + int(np.argmax(ext[start:]) if direction > 0 else np.argmin(ext[start:]))
    if k + 2 >= len(ext):
        return None
    counter = frame["low" if direction > 0 else "high"].to_numpy(dtype=float)
    ci = k + 1 + int(np.argmin(counter[k + 1:]) if direction > 0 else np.argmax(counter[k + 1:]))
    for end in range(ci + 1, len(ext)):
        bars = end - k
        if not .828 * last.bars <= bars <= 1.208 * last.bars:
            continue
        recovery = direction * (ext[end] - counter[ci])
        height = direction * (ext[k] - counter[ci])
        if height > 0 and recovery >= .786 * height and direction * (ext[end] - ext[k]) <= 0:
            return BookInterval(direction, int(positions[k]), int(positions[end]), float(ext[k]), bars,
                                int(positions[ci]), float(counter[ci]), int(positions[end]), None, None,
                                degree="main", relation="前Q的0.828–1.208倍 / 回到78.6%", state="failed_candidate")
    return None


def tp_ta(frame: pd.DataFrame, positions: np.ndarray, origin, peak, endpoint,
          *, enabled: bool, triangle: bool = False) -> tuple[float, int, float] | None:
    """Project the endpoint's price horizontally onto the preceding leg.

    Ta is the time from that equal-price crossing to the peak, NOT the whole
    preceding leg (pp. 558–561). Ambiguous intrabar crossings are interpolated
    on closes, explicitly a numerical convention for this OHLC application.
    """
    if not enabled or triangle:
        return None
    a, b, c = (int(np.searchsorted(positions, p.index)) for p in (origin, peak, endpoint))
    if not 0 <= a < b < c < len(frame):
        return None
    values = frame["close"].to_numpy(dtype=float)
    target = float(endpoint.price)
    crossing = None
    for j in range(a, b):
        x, y = values[j], values[j + 1]
        if min(x, y) <= target <= max(x, y) and x != y:
            crossing = j + (target - x) / (y - x)
    if crossing is None:
        return None
    ta = b - crossing
    return ((c - b) / ta, c - b, ta) if ta > 0 else None
