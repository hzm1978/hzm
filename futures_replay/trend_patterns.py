from __future__ import annotations

"""Public annotation API for 鹿希武《趋势交易法》第三版 (3.pdf).

Shapes, intervals and their structural confirmation are separate observations.
See 指标规则说明.md for source pages and explicit numerical conventions.
"""

from typing import Sequence
from .trend_types import TrendLabel, TrendCorrection
from .trend_candles import book_candle_patterns, key_reversals, standard_candles
from .trend_corrections import correction_patterns


def retracement_labels(pivots: Sequence) -> tuple[TrendLabel, ...]:
    """Measure corrections against their impulse; extensions against like legs.

    A new high / low is not mislabeled a >100% retracement of the small
    preceding counter-swing. Its extension is measured against the previous
    same-direction impulse (third-edition pp. 280–293, 473–490).
    """
    labels = []
    for i, (a, b, c) in enumerate(zip(pivots, pivots[1:], pivots[2:])):
        if a.kind != c.kind or a.kind == b.kind:
            continue
        impulse = abs(float(b.price) - float(a.price))
        if impulse <= max(abs(float(a.price)), 1.0) * 1e-12:
            continue
        percent = abs(float(c.price) - float(b.price)) / impulse * 100.0
        context = "retracement"
        text = f"回调 {percent:.1f}%"
        if percent > 100.0:
            if i == 0:
                continue
            previous = pivots[i - 1]
            same_direction_impulse = abs(float(a.price) - float(previous.price))
            if same_direction_impulse <= max(abs(float(a.price)), 1.0) * 1e-12:
                continue
            percent = abs(float(c.price) - float(b.price)) / same_direction_impulse * 100.0
            context, text = "extension", f"延伸 {percent:.1f}%"
        labels.append(TrendLabel(
            c.index, c.price, text, 1 if c.kind == "L" else -1,
            max(a.confirmed_at, b.confirmed_at, c.confirmed_at),
            b.index, context=context, percent=percent,
        ))
    return tuple(labels)
