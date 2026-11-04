from __future__ import annotations

"""The preceding correction's reverse ruler and book PSTD price references."""

from dataclasses import dataclass
import numpy as np

from .trend_types import TrendPivot, TrendLabel
from .trend_intervals import BookLevel,BoundaryPoint


@dataclass(frozen=True)
class ReverseFibonacci:
    start: TrendPivot
    base: TrendPivot
    direction: int
    levels: tuple[BookLevel, ...]
    confirmed_at: int
    candidate: bool


@dataclass(frozen=True)
class MainWaveTarget:
    degree: str
    wave: int | None
    ratio: float
    price: float
    origin: TrendPivot
    reference_height: float
    confirmed_at: int
    hit_index: int | None
    hit_confirmed_at: int | None
    tier: str
    kind: str = "main"
    historical: bool = False

    @property
    def label(self):
        prefix = ("全趋势" if self.kind == "whole" else
                  f"{self.degree}延续候选" if self.wave is None else f"{self.degree}{self.wave}")
        return f"{prefix} {self.ratio:g}H1" + ("·已达" if self.hit_index is not None else "·历史未达" if self.historical else "·候选止盈")


@dataclass(frozen=True)
class WaveProjection:
    degree: str
    direction: int
    wave: int | None
    first_origin: TrendPivot
    first_tip: TrendPivot
    active_origin: TrendPivot
    active_tip: TrendPivot
    reverse: ReverseFibonacci | None
    targets: tuple[MainWaveTarget, ...]
    historical_arrivals: tuple[TrendLabel, ...]
    historical: bool = False
    level: int = 1
    interval_bars: tuple[int, ...] = ()
    owner: tuple[int, int] | None = None
    owner_wave: int | None = None
    alternative: bool = False
    confirmed_a: BoundaryPoint | None = None


def reverse_fibonacci(start, base, direction, confirmed_at, *, candidate=False):
    height = direction*(start.price-base.price)
    if height <= 0 or start.index >= base.index:
        return None
    levels = tuple(BookLevel(base.price+direction*r*height,
        (f"反向 {r:.3f}" if r else "反向 base"), base.index, confirmed_at, "reverse_fibonacci")
        for r in (0., .191, .382, .5, .618, 1.))
    return ReverseFibonacci(start, base, direction, levels, confirmed_at, candidate)


def retracement_fibonacci(start, base, direction, confirmed_at, *, candidate=True):
    """The user's preceding-leg ruler: standard percentage retracements."""
    height = direction*(start.price-base.price)
    if height <= 0 or start.index >= base.index:
        return None
    levels = tuple(BookLevel(base.price+direction*ratio*height,f"{ratio*100:g}%",
        start.index,confirmed_at,"retracement_fibonacci")
        for ratio in (0.,.236,.382,.5,.618,.786,1.))
    return ReverseFibonacci(start,base,direction,levels,confirmed_at,candidate)


def make_wave_projection(points, direction, degree, frame, positions, intervals, boundaries=(), *,
                         historical=False,level=1,owner=None,owner_wave=None,alternative=False):
    points = tuple(points)
    if (len(points) < 4 or (len(points)-1) % 2 == 0 or not intervals
            or direction not in (-1, 1)):
        return None
    asof = int(positions[-1])
    if any(p.index not in positions or p.confirmed_at > asof for p in points[:-1]):
        return None
    h1 = direction*(points[1].price-points[0].price)
    if h1 <= 0:
        return None
    first_known = max(points[1].confirmed_at, intervals[0].confirmed_at)
    by_index = {p.index: p for p in boundaries if p.state == "confirmed"}
    base, tip = points[-2:]
    preceding = points[-3]
    boundary = by_index.get(base.index)
    base_known = max(base.confirmed_at, boundary.confirmed_at if boundary else base.confirmed_at)
    reverse = reverse_fibonacci(preceding, base, direction,
        max(base_known, preceding.confirmed_at), candidate=boundary is None)
    targets, arrivals = [], []
    values = frame["high" if direction > 0 else "low"].to_numpy(dtype=float)

    def target_for(wave, ratio, origin, end, known, tier, kind="main"):
        price = origin.price+direction*ratio*h1
        begin = int(np.searchsorted(positions, origin.index, side="right"))
        stop = int(np.searchsorted(positions, end.index, side="right"))
        hits = np.flatnonzero(direction*(values[begin:stop]-price) >= 0)
        hit = int(positions[begin+int(hits[0])]) if len(hits) else None
        return MainWaveTarget(degree, wave if wave <= 7 else None, ratio, float(price), origin, h1, known,
            hit, max(hit, known) if hit is not None else None, tier, kind, historical)

    for wave in range(1, len(points), 2):
        origin, end = points[wave-1:wave+1]
        if wave < len(points)-1 or historical:
            actual = direction*(end.price-origin.price)/h1
            arrivals.append(TrendLabel(end.index, end.price,
                f"{degree}{wave}实达 {actual:.3g}H1 · {end.price:.6g}", -direction,
                max(end.confirmed_at, first_known), origin.index, "main_wave_arrival",
                evidence=f"实际主浪高度{abs(end.price-origin.price):.8g} / H1={h1:.8g}；不将事后分级伪装成事前信号"))
        if wave < 3:
            continue
        a = by_index.get(origin.index)
        known = max(first_known, origin.confirmed_at, a.confirmed_at if a else origin.confirmed_at)
        if known > asof:
            continue
        for ratio in (1.208, 1.618, 2., 2.382, 2.618, 3.):
            targets.append(target_for(wave, ratio, origin, end, known,
                "常规参考" if ratio <= 2 else "需强势条件的扩展参考"))
    for ratio in (3.236, 4.236):
        targets.append(target_for(len(points)-1, ratio, points[0], tip,
                                  first_known, "全趋势参考", "whole"))
    return WaveProjection(degree, direction, len(points)-1 if len(points) <= 8 else None, points[0], points[1],
                          base, tip, reverse, tuple(targets), tuple(arrivals), historical,
                          level,tuple(p.bars for p in intervals),owner,owner_wave,alternative,
                          max(by_index.values(),key=lambda p:p.confirmed_at) if by_index else None)
