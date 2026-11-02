from __future__ import annotations

import numpy as np
import pandas as pd


def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=1).mean()


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False, min_periods=1).mean()


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    dif = ema(close, fast) - ema(close, slow)
    dea = ema(dif, signal)
    hist = (dif - dea) * 2
    return pd.DataFrame({"DIF": dif, "DEA": dea, "MACD": hist})


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    delta = close.diff()
    up = delta.clip(lower=0)
    down = -delta.clip(upper=0)
    avg_up = up.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    avg_down = down.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    rs = avg_up / avg_down.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    return out.fillna(50)


def kdj(df: pd.DataFrame, n: int = 9) -> pd.DataFrame:
    low_n = df["low"].rolling(n, min_periods=1).min()
    high_n = df["high"].rolling(n, min_periods=1).max()
    rsv = (df["close"] - low_n) / (high_n - low_n).replace(0, np.nan) * 100
    k = rsv.ewm(alpha=1 / 3, adjust=False).mean().fillna(50)
    d = k.ewm(alpha=1 / 3, adjust=False).mean().fillna(50)
    j = 3 * k - 2 * d
    return pd.DataFrame({"K": k, "D": d, "J": j})


def williams_r(df: pd.DataFrame, n: int = 14) -> pd.Series:
    hh = df["high"].rolling(n, min_periods=1).max()
    ll = df["low"].rolling(n, min_periods=1).min()
    return -100 * (hh - df["close"]) / (hh - ll).replace(0, np.nan)


def cci(df: pd.DataFrame, n: int = 14) -> pd.Series:
    tp = (df["high"] + df["low"] + df["close"]) / 3
    ma = tp.rolling(n, min_periods=1).mean()
    md = tp.rolling(n, min_periods=1).apply(lambda x: np.mean(np.abs(x - np.mean(x))), raw=True)
    return (tp - ma) / (0.015 * md.replace(0, np.nan))


def bollinger(close: pd.Series, n: int = 20, k: float = 2.0) -> pd.DataFrame:
    mid = close.rolling(n, min_periods=1).mean()
    std = close.rolling(n, min_periods=2).std(ddof=0).fillna(0)
    return pd.DataFrame({"BOLL": mid, "UPPER": mid + k * std, "LOWER": mid - k * std})


def volume_ma(volume: pd.Series, periods=(5, 10)) -> pd.DataFrame:
    return pd.DataFrame({f"VMA{n}": sma(volume, n) for n in periods})
