from __future__ import annotations

from dataclasses import dataclass

import numpy as np

@dataclass(frozen=True)
class BreakDecision:
    state: str
    label: str
    break_index: int | None = None
    confirmed_at: int | None = None
    delay_level: float | None = None
    delay_ready_at: int | None = None
    reason: str = ""
    line_kind: str = ""
    # Frozen construction used to assess this event, available in details.
    anchors: tuple = ()
    offset: float = 0.0


def line_break_decision(direction, line, offset, frame, positions, standards,
                         atr, *, line_kind="趋势线", structure_complete=False,
                         leg_origin=None, leg_peak=None):
    """Third-edition pp. 325–343: break candle plus TWO more, then confirmation.

    A later recross/new extreme explicitly invalidates a delay. We do not
    label a wick-only crossing as a closed-bar structure reversal.
    """
    if not line:
        return BreakDecision("unavailable", "转向线待确认")
    a, b = line
    slope = (b.price - a.price) / (b.index - a.index)
    line_values = a.price + slope * (positions - a.index) + offset
    close = frame["close"].to_numpy(dtype=float)
    high, low = (frame[k].to_numpy(dtype=float) for k in ("high", "low"))
    available = max(a.confirmed_at, b.confirmed_at)
    first = None
    for i in range(int(np.searchsorted(positions, available)), len(close)):
        if direction * (close[i] - line_values[i]) < 0:
            first = i
            break
    kwargs = dict(line_kind=line_kind, anchors=tuple(line), offset=float(offset))
    if first is None:
        touched = low[-1] < line_values[-1] if direction > 0 else high[-1] > line_values[-1]
        return BreakDecision("wick_touch" if touched else "intact",
                             "影线越线·收盘未破" if touched else "转向线未破", **kwargs)
    i = first
    origin_local = int(np.searchsorted(positions, a.index))
    extreme = float(np.max(high[origin_local:i + 1]) if direction > 0 else np.min(low[origin_local:i + 1]))
    distance = direction * (extreme - close[i])
    reasons = []
    if not standards[i].standard or standards[i].direction != -direction:
        reasons.append("非反向标准K")
    if distance > 2 * max(float(atr[i]), 1e-12):
        reasons.append("离极值较远（>2ATR）")
    if leg_origin is not None and leg_peak is not None and leg_origin.confirmed_at <= int(positions[i]) and leg_peak.confirmed_at <= int(positions[i]):
        height = direction * (leg_peak.price - leg_origin.price)
        retrace = direction * (leg_peak.price - close[i]) / height if height > 0 else 0
        if .62 <= retrace <= .786:
            reasons.append("落入62%–78.6%区域")
    if not structure_complete:
        reasons.append("主结构未完整，保留调整解释")
    if not reasons:
        return BreakDecision("direct", "反向标准K收盘突破·直接转向参考",
                             int(positions[i]), int(positions[i]), reason="标准K且距极值≤2ATR；结构已完整", **kwargs)
    reason = "；".join(reasons)
    if i + 2 >= len(close):
        return BreakDecision("delay_wait", "延时观察：等待突破根后的2根收盘",
                             int(positions[i]), reason=reason, **kwargs)
    level = float(np.min(low[i:i + 3]) if direction > 0 else np.max(high[i:i + 3]))
    ready = int(positions[i + 2])
    for j in range(i + 3, len(close)):
        if direction * (close[j] - extreme) > 0:
            return BreakDecision("false_break", "延时过滤：收盘再创新极值",
                                 int(positions[i]), int(positions[j]), level, ready, reason, **kwargs)
        if direction * (close[j] - level) < 0:
            state = "delayed" if structure_complete else "line_warning"
            label = "收盘突破延时转向线" if structure_complete else "延时线已破·主结构仍待确认"
            return BreakDecision(state, label, int(positions[i]), int(positions[j]), level, ready, reason, **kwargs)
    return BreakDecision("delay_ready", "延时线成立·等待后续收盘突破",
                         int(positions[i]), None, level, ready, reason, **kwargs)
