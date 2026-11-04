"""Closed-bar MA cross, next-open execution, with local fees and margin."""
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .bars import validate_frame
from .paper import Contract, PaperAccount


def ma_cross(frame, fast=5, slow=20, capital=1_000_000, contract=None):
    if not 1 <= fast < slow:
        raise ValueError("均线参数需满足 1 ≤ 短线 < 长线")
    x = validate_frame(frame)
    x = x.loc[x["closed"]].reset_index(drop=True)
    if len(x) < slow+2:
        raise ValueError("已收盘K线不足，无法回测")
    short = x.close.rolling(fast).mean()
    long = x.close.rolling(slow).mean()
    book = PaperAccount(capital, contract or Contract())
    rejected = []
    equity = []
    for index in range(slow, len(x)):
        row = x.iloc[index]
        # Only yesterday's closed indicator is visible at today's open.
        previous = index-1
        before = index-2
        crossed_up = short.iloc[previous] > long.iloc[previous] and short.iloc[before] <= long.iloc[before]
        crossed_down = short.iloc[previous] < long.iloc[previous] and short.iloc[before] >= long.iloc[before]
        wanted = 1 if crossed_up else -1 if crossed_down else 0
        if wanted and wanted != book.direction:
            if book.qty:
                book.execute("平多" if book.direction > 0 else "平空", book.qty, float(row.open), row.datetime, reason="均线反向")
            try:
                book.execute("开多" if wanted > 0 else "开空", 1, float(row.open), row.datetime, reason="前一收盘交叉/本根开盘")
            except ValueError as exc:
                rejected.append(str(exc))
        equity.append(book.metrics(float(row.close))["equity"])
    if book.qty:
        row = x.iloc[-1]
        book.execute("平多" if book.direction > 0 else "平空", book.qty, float(row.close), row.datetime, reason="回测结束")
        equity[-1] = book.balance
    values = np.asarray(equity or [capital])
    peaks = np.maximum.accumulate(np.r_[capital, values])[1:]
    drawdown = np.maximum(0, (peaks-values)/peaks)
    closes = [fill for fill in book.fills if fill["action"].startswith("平")]
    entry_fees = [fill["fee"] for fill in book.fills if fill["action"].startswith("开")]
    return dict(initial=capital, ending=book.balance, profit=book.balance-capital,
                return_percent=(book.balance/capital-1)*100,
                max_drawdown_percent=float(drawdown.max())*100,
                closed_trades=len(closes), wins=sum(x["net"]-fee > 0 for x, fee in zip(closes, entry_fees)),
                fees=sum(x["fee"] for x in book.fills), fills=book.fills, rejected=rejected,
                equity=equity, bars=len(x))
