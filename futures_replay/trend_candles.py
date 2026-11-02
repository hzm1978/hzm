from __future__ import annotations

"""Third edition pp. 200–295. Shapes are observations, not trading orders."""

from dataclasses import dataclass, replace
from typing import Sequence

import numpy as np
import pandas as pd

from .trend_types import TrendLabel


@dataclass(frozen=True)
class StandardCandle:
    index: int
    direction: int
    strength: float
    threshold: float | None
    standard: bool
    sample_indices: tuple[int, ...]
    reference_price: float


def standard_candles(frame: pd.DataFrame, positions: np.ndarray,
                     window: int = 40, sample: int = 5) -> tuple[StandardCandle, ...]:
    """Previous 40, five largest bodies; mean directional extreme-to-close.

    The current candle is EXCLUDED from its own threshold. Until the full
    reference window exists, the standard is unknown, not silently shortened.
    Ties use chronological order for reproducibility (pp. 200–203).
    """
    opening, high, low, close = (frame[k].to_numpy(dtype=float)
                                for k in ("open", "high", "low", "close"))
    bodies = np.abs(close - opening)
    strength = np.where(close > opening, close - low,
                        np.where(close < opening, high - close, 0.0))
    out = []
    for i in range(len(close)):
        direction = int(np.sign(close[i] - opening[i]))
        threshold, samples = None, ()
        if i >= window:
            ranked = np.argsort(-bodies[i - window:i], kind="stable")[:sample] + i - window
            threshold = float(np.mean(strength[ranked]))
            samples = tuple(int(positions[j]) for j in ranked)
        out.append(StandardCandle(int(positions[i]), direction, float(strength[i]), threshold,
                                  bool(direction and threshold is not None and strength[i] > threshold),
                                  samples, float(low[i] if direction >= 0 else high[i])))
    return tuple(out)


def book_candle_patterns(frame: pd.DataFrame, positions: np.ndarray, *,
                         standard_window: int = 40, standard_sample: int = 5,
                         doji_ratio: float = .1, shadow_ratio: float = 1.618) -> tuple[TrendLabel, ...]:
    """Single, two and multi-candle patterns from the THIRD edition.

    Nonstandard morning/evening stars remain qualified=False until the final
    close crosses the FIRST open or a later yin-yang-yin confirmation appears.
    A neutral doji requires contextual direction. Quantities such as 'small'
    and 'quick' are explicit, documented application tolerances.
    """
    values = frame[["open", "high", "low", "close"]].to_numpy(dtype=float)
    standards = standard_candles(frame, positions, standard_window, standard_sample)
    labels = []

    def add(i, text, direction, start, qualified=None, evidence=""):
        lo, hi = float(np.min(values[start:i + 1, 2])), float(np.max(values[start:i + 1, 1]))
        threshold = standards[start].threshold
        # For a multi-bar shape, use the corresponding merged OHLC distance.
        net_strength = values[i, 3] - lo if direction > 0 else hi - values[i, 3]
        size_ok = bool(threshold is not None and net_strength > threshold)
        if qualified is None:
            qualified = size_ok
        if direction == 0:
            qualified = False
        evidence = evidence + (f"；标准阈值{threshold:.4g}" if threshold is not None else "；不足40根标准样本")
        labels.append(TrendLabel(int(positions[i]), lo if direction >= 0 else hi, text,
                                 direction, int(positions[i]), int(positions[start]),
                                 qualified=bool(qualified), evidence=evidence))

    for i, (o, h, l, c) in enumerate(values):
        body, span = abs(c - o), h - l
        if span <= 0:
            continue
        lower, upper = min(o, c) - l, h - max(o, c)
        epsilon = max(span * .03, abs(c) * 1e-10)
        if body <= span * doji_ratio:
            sign = 1 if lower >= 2 * max(upper, epsilon) else -1 if upper >= 2 * max(lower, epsilon) else 0
            name = "看涨十字星" if sign > 0 else "看跌十字星" if sign < 0 else "十字星"
            threshold = standards[i].threshold
            distance = c - l if sign > 0 else h - c if sign < 0 else 0
            add(i, name, sign, i, qualified=bool(sign and threshold and distance > threshold), evidence="长影线至少为另一侧2倍")
        elif lower > shadow_ratio * body and lower > upper:
            add(i, "锤子线", 1, i, evidence="下影线>1.618倍实体")
        elif upper > shadow_ratio * body and upper > lower:
            add(i, "倒锤子线", -1, i, evidence="上影线>1.618倍实体")

        if i:
            po, ph, pl, pc = values[i - 1]
            if abs(pc - po) <= (ph - pl) * doji_ratio and standards[i].standard:
                if c > ph and c > o:
                    add(i, "十字星向上确认", 1, i - 1, qualified=True,
                        evidence="后续标准K收盘越过十字星高点")
                elif c < pl and c < o:
                    add(i, "十字星向下确认", -1, i - 1, qualified=True,
                        evidence="后续标准K收盘越过十字星低点")
            if c > o and pc < po and o <= pc and c >= po:
                add(i, "阳抱阴", 1, i - 1, evidence="后一阳实体覆盖前一阴实体")
            if c < o and pc > po and o >= pc and c <= po:
                add(i, "阴抱阳", -1, i - 1, evidence="后一阴实体覆盖前一阳实体")
            previous_top, previous_bottom = max(po, pc), min(po, pc)
            if c > o and ph > previous_top + epsilon and o <= previous_top + epsilon and c >= ph:
                add(i, "上补缺影线", 1, i - 1, evidence="后一实体覆盖前一上影线")
            if c < o and pl < previous_bottom - epsilon and o >= previous_bottom - epsilon and c <= pl:
                add(i, "下补缺影线", -1, i - 1, evidence="后一实体覆盖前一下影线")
            pspan = max(ph - pl, 1e-12)
            peps = max(pspan * .03, abs(pc) * 1e-10)
            if pc > po and c > o and ph - pc <= peps and o - l <= epsilon and o >= pc:
                add(i, "魂断蓝桥·上", 1, i - 1, evidence="同向前光头、后光脚")
            if pc < po and c < o and pc - pl <= peps and h - o <= epsilon and o <= pc:
                add(i, "魂断蓝桥·下", -1, i - 1, evidence="同向前光脚、后光头")
            threshold = standards[i].threshold
            if threshold is not None and threshold > 0:
                gap = o - pc
                if abs(gap) > threshold and np.sign(c - pc) == np.sign(gap):
                    add(i, "跳空高开" if gap > 0 else "跳空低开", int(np.sign(gap)), i - 1,
                        qualified=True, evidence="跳空折算为以前收为开盘的虚拟标准K")

        if i >= 2:
            fo, fh, fl, fc = values[i - 2]
            mo, mh, ml, mc = values[i - 1]
            first_body = abs(fc - fo)
            small_middle = first_body > 0 and abs(mc - mo) <= .5 * first_body
            if small_middle and first_body >= .45 * max(fh - fl, 1e-12):
                if fc < fo and max(mo, mc) <= (fo + fc) / 2 and c > o and c > (fo + fc) / 2:
                    full = c > fo
                    add(i, "启明星" if full else "非标准启明星", 1, i - 2,
                        qualified=bool(full and standards[i].standard), evidence="最后收盘必须越过第一根阴线开盘")
                if fc > fo and min(mo, mc) >= (fo + fc) / 2 and c < o and c < (fo + fc) / 2:
                    full = c < fo
                    add(i, "黄昏星" if full else "非标准黄昏星", -1, i - 2,
                        qualified=bool(full and standards[i].standard), evidence="最后收盘必须越过第一根阳线开盘")
            # Full candle closures matter, not merely the sequence of colours.
            sign = 1 if c > o and fc > fo and mc < mo else -1 if c < o and fc < fo and mc > mo else 0
            if sign and sign * (c - fc) >= 0 and abs(mc - mo) <= first_body * 1.208:
                previous = [x for x in labels if x.index >= int(positions[max(0, i - 5)])
                            and x.index < int(positions[i]) and x.direction == sign
                            and not x.qualified and "星" in x.text]
                if previous:
                    add(i, "阳阴阳确认" if sign > 0 else "阴阳阴确认", sign, i - 2,
                        evidence=f"确认{previous[-1].text}，收盘回到或越过前同向K")
                else:
                    add(i, "阳阴阳" if sign > 0 else "阴阳阴", sign, i - 2,
                        evidence="三根组合须在关键位置使用，合并K达到标准门槛")
            if standards[i - 2].standard and standards[i].standard and abs(mc - mo) <= .382 * first_body:
                first_sign, final_sign = standards[i - 2].direction, standards[i].direction
                if first_sign == final_sign and final_sign * (c - fc) > 0:
                    add(i, "急停加速·上" if final_sign > 0 else "急停加速·下", final_sign, i - 2,
                        qualified=True, evidence="标准K+小K+同向标准K")
            # Three consecutive long shadows at roughly the same price region.
            last = values[i - 2:i + 1]
            bodies = np.maximum(np.abs(last[:, 3] - last[:, 0]), (last[:, 1] - last[:, 2]) * .05)
            ups = last[:, 1] - np.maximum(last[:, 0], last[:, 3])
            downs = np.minimum(last[:, 0], last[:, 3]) - last[:, 2]
            if np.all(ups > shadow_ratio * bodies):
                add(i, "上三影", -1, i - 2, evidence="连续三根上影线>1.618倍实体")
            if np.all(downs > shadow_ratio * bodies):
                add(i, "下三影", 1, i - 2, evidence="连续三根下影线>1.618倍实体")

        # Ghost gate admits multiple small candles between two standard bars.
        if standards[i].standard:
            for start in range(max(0, i - 5), i - 1):
                first = standards[start]
                if not first.standard or first.direction == standards[i].direction:
                    continue
                sizes = np.abs(values[start + 1:i, 3] - values[start + 1:i, 0])
                if len(sizes) and np.all(sizes <= abs(values[start, 3] - values[start, 0]) * .382):
                    sign = standards[i].direction
                    if sign * (c - values[start, 0]) > 0:
                        add(i, "上鬼门关" if sign > 0 else "下鬼门关", sign, start,
                            qualified=True, evidence="标准K+十字/小K+反向标准K，收盘关门")
                        break

        # Quick false break/reclaim. A wick piercing yesterday's low alone
        # never means an automatic reversal; the contextual filter is separate.
        if i >= 6:
            for pierced in range(max(5, i - 2), i + 1):
                old_low = float(np.min(values[pierced - 5:pierced, 2]))
                old_high = float(np.max(values[pierced - 5:pierced, 1]))
                if values[pierced, 2] < old_low and c > old_low and c > o:
                    add(i, "海底捞月·下破收回", 1, pierced,
                        evidence="破前5根低点后最多2根收盘收回")
                    break
                if values[pierced, 1] > old_high and c < old_high and c < o:
                    add(i, "海底捞月·上破收回", -1, pierced,
                        evidence="破前5根高点后最多2根收盘收回")
                    break
    return tuple(labels)


def key_reversals(patterns: Sequence[TrendLabel], pivots: Sequence,
                  positions: np.ndarray, atr: np.ndarray,
                  *, contexts: Sequence[TrendLabel] | None = None) -> tuple[TrendLabel, ...]:
    """Qualify only shapes with both standard/confirmation and a known context.

    A bare local pivot is NOT automatically a key entry position. In the full
    engine contexts come from intervals, A points, 40/62/78.6%, G4/G6 or a
    completed wave degree. Historical context may confirm later; its timestamp
    is carried into the label rather than silently moving a signal backwards.
    """
    if contexts is None:
        return ()
    output = []
    as_of = int(positions[-1])
    for pattern in patterns:
        if not pattern.qualified or pattern.direction == 0 or pattern.confirmed_at > as_of:
            continue
        local = min(int(np.searchsorted(positions, pattern.index)), len(atr) - 1)
        tolerance = max(float(atr[local]) * .8, abs(pattern.price) * .001, 1e-12)
        matches = [c for c in contexts if c.direction == pattern.direction and c.confirmed_at <= as_of
                   and pattern.start_index - 2 <= c.index <= pattern.index + 2
                   and abs(c.price - pattern.price) <= tolerance]
        if not matches:
            continue
        context = min(matches, key=lambda c: (abs(c.price - pattern.price), abs(c.index - pattern.index)))
        output.append(replace(pattern, context=context.text,
                              confirmed_at=max(pattern.confirmed_at, context.confirmed_at),
                              evidence=pattern.evidence + "；" + context.evidence))
    # At a single candle prefer the more specific multi-bar explanation.
    priority = ("鬼门关", "确认", "急停", "补缺", "蓝桥", "明星", "昏星", "海底", "三影", "抱", "锤", "十字", "跳空")
    output.sort(key=lambda x: (x.index, next((i for i, s in enumerate(priority) if s in x.text), 99)))
    return tuple({(p.index, p.direction): p for p in reversed(output)}.values())
