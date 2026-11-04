"""Explicit synthetic data for trying the application without an account."""
import numpy as np
import pandas as pd

from .bars import validate_frame

DEMO_SYMBOL = "DEMO.SHFE.ag"


def demo_bars(count=760):
    rng = np.random.default_rng(32018)
    times = pd.bdate_range(end="2026-11-03", periods=count)
    legs = [10000, 11500, 10600, 13800, 12000, 15800, 14800, 16800,
            14000, 14700, 12000, 13000, 10200, 11100, 9100, 9800]
    trend = np.interp(np.arange(count), np.linspace(0, count-1, len(legs)), legs)
    close = trend+rng.normal(0, 80, count)
    opening = np.r_[close[0], close[:-1]]+rng.normal(0, 35, count)
    return validate_frame(pd.DataFrame(dict(datetime=times, open=opening,
        high=np.maximum(opening, close)+rng.uniform(20, 125, count),
        low=np.minimum(opening, close)-rng.uniform(20, 125, count),
        close=close, volume=rng.integers(20000, 180000, count),
        open_interest=rng.integers(20000, 100000, count), closed=True)))
