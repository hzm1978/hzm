from __future__ import annotations

from dataclasses import dataclass, field
from math import isfinite
from typing import Callable

import numpy as np
import pandas as pd

from .config import CONTRACT_MULTIPLIERS, DEFAULT_MARGIN_RATE


# Inspectable strategies.  They use only information
# available at or before the current bar close; orders are executed at the next
# bar open by the backtest engine, preventing same-bar look-ahead.
STRATEGIES: dict[str, str] = {
    "ma_cross": "双均线趋势 MA(10,30)",
    "macd": "MACD 趋势",
    "rsi": "RSI(14) 反转",
    "kdj": "KDJ(9,3,3) 趋势",
    "boll": "BOLL(20,2) 反转",
    "donchian": "唐奇安20突破",
    "turtle": "海龟20/10突破",
    "momentum": "20周期动量",
    "cci": "CCI(20) 趋势",
    "wr": "WR(14) 反转",
    "trend_pullback_resonance": "顺势回调量仓共振",
    "strong_breakout_pullback": "强K突破回调再启动",
    "price_volume_oi_resonance": "价格量仓三共振",
    "n_platform_breakout": "N形平台连环引爆",
    "su_bing_21macd": "21均线MACD量仓突破",
    "ma_pullback_reversal": "均线顺势缩量回踩反K",
    "turning_1plus3": "1+3拐点确认",
    "long_box_60_breakout": "60均线长箱体量仓突破",
}


STRATEGY_DESCRIPTIONS: dict[str, str] = {
    "ma_cross": (
        "双均线趋势 MA(10,30)：用10周期均线代表短期趋势、30周期均线代表中期趋势。"
        "短均线上穿长均线后目标仓位为多头，下穿后目标仓位为空头；信号在本根K线收盘后确认，"
        "下一根K线开盘成交。优点是规则简单、适合持续趋势，缺点是在震荡行情中容易反复止损。"
    ),
    "macd": (
        "MACD趋势：使用EMA(12)与EMA(26)之差得到DIF，再对DIF计算EMA(9)得到DEA。"
        "DIF高于DEA持多，DIF低于DEA持空，下一根K线开盘执行。它对中期趋势较敏感，"
        "但在横盘阶段可能产生较多来回交易。"
    ),
    "rsi": (
        "RSI(14)反转：RSI≤30时寻找超卖反弹并做多，RSI≥70时寻找超买回落并做空；"
        "多头在RSI回升至55附近退出，空头在RSI回落至45附近退出。属于均值回归策略，"
        "更适合震荡市场；单边强趋势中超买/超卖状态可能持续较久。"
    ),
    "kdj": (
        "KDJ(9,3,3)趋势：先计算9周期RSV，再平滑得到K、D。本实现以K与D的相对位置决定方向："
        "K>D持多，K<D持空，并在下一根开盘执行。信号较灵敏，适合较短周期，但噪声也相对较大。"
    ),
    "boll": (
        "BOLL(20,2)反转：20周期均线为中轨，上下轨为中轨±2倍标准差。收盘跌破下轨后做多，"
        "涨破上轨后做空；价格回到中轨附近退出。属于波动率均值回归策略，震荡期较有效，"
        "遇到强趋势沿布林带运行时可能承受较大回撤。"
    ),
    "donchian": (
        "唐奇安20突破：使用前20根K线的最高价和最低价构成通道，收盘突破上轨做多、突破下轨做空；"
        "回到通道中线附近退出。策略只使用过去数据并将通道整体后移一根，避免未来函数。"
        "适合捕捉趋势突破，但假突破会带来频繁损失。"
    ),
    "turtle": (
        "海龟20/10突破：经典趋势跟踪结构。以前20周期最高/最低作为入场通道，向上突破做多、向下突破做空；"
        "持多时跌破前10周期最低退出，持空时突破前10周期最高退出。长趋势中表现通常较好，"
        "震荡期容易连续小亏。"
    ),
    "momentum": (
        "20周期动量：比较当前收盘价与20根K线前的收盘价。20周期收益为正则持多，为负则持空，"
        "方向改变后下一根开盘换仓。它直接刻画价格惯性，参数少、易理解，但对参数长度较敏感。"
    ),
    "cci": (
        "CCI(20)趋势：以典型价格相对20周期均值的偏离程度衡量趋势。CCI≥+100进入多头，CCI≤-100进入空头；"
        "回到0轴附近退出。它可以过滤一部分小波动，但在快速反转时可能存在滞后。"
    ),
    "wr": (
        "WR(14)反转：Williams %R位于[-100,0]。WR≤-80视为超卖并做多，WR≥-20视为超买并做空；"
        "回到-50附近退出。属于短期反转思路，适合区间行情，强趋势中需要特别注意连续逆势信号。"
    ),
    "trend_pullback_resonance": (
        "顺势回调量仓共振：这是根据文档中反复出现的“先定趋势—等待回调到关键位置—企稳反K/突破确认—量价持仓共振—护盘退出”思想量化得到的规则。"
        "趋势用EMA20与EMA60及EMA20斜率过滤；只有EMA20>EMA60且EMA20继续上升时才寻找多头，反之寻找空头。"
        "回调用最近3根K线是否触及EMA20附近（0.35ATR容差）表示；确认K要求向趋势方向突破上一根高/低，且实体至少0.35ATR。"
        "成交量存在时要求当前量不低于20周期均量；持仓量存在时要求趋势方向确认K的持仓量不低于上一根。"
        "入场信号在收盘确认，下一根开盘成交；持仓后若收盘重新跌破/升破EMA20，或出现明显反向破坏，则退出。"
        "它更接近文档的核心交易思想，适合趋势中的回调再启动，不适合窄幅横盘。"
    ),
    "strong_breakout_pullback": (
        "强K突破回调再启动：对应文档中反复强调的‘调整后强K突破—不追高/追低—等待回调到关键位置—出现反K或再突破确认后进入’。"
        "先用EMA20/EMA60过滤大方向；突破K必须以较大实体突破过去20根K线前高/前低，并尽量有放量确认。"
        "突破后不立刻追单，而是在随后最多5根K线内等待价格回踩突破位，且不能明显失守；随后出现向原趋势方向的确认K才入场。"
        "多头确认K为收阳并突破上一根高点，空头相反。持仓后以EMA20和突破位作为动态防守，趋势失效或出现反向突破时退出。"
        "所有信号都在收盘后确认、下一根开盘成交，适合趋势启动后的二次上车，交易次数通常少于直接突破策略。"
    ),
    "price_volume_oi_resonance": (
        "价格量仓三共振：量化文档中的‘价格是方向、成交量是动能、持仓量是势能，三者共振才是真行情’。"
        "趋势方向先由EMA20/EMA60确定；只有价格突破过去10根K线的高/低、成交量至少达到20周期均量的1.20倍、且持仓量较上一根增加时，才确认入场。"
        "多头要求上涨突破+放量+增仓，空头要求下跌突破+放量+增仓；任一条件缺失都不入场。"
        "持仓后以EMA20为趋势防守，若出现反向三共振信号则直接反手。该策略专门过滤缩量或减仓造成的假突破，更适合有open_interest字段的国内期货。"
    ),
    "n_platform_breakout": (
        "N形平台连环引爆：把N形趋势、平台整理、放量突破和‘第二天必须有盈利，否则先走’量化成可回测规则。"
        "先要求前8根K线形成相对紧凑的平台（区间宽度不超过约2.5ATR），然后收盘价有效突破平台上沿做多、跌破下沿做空。"
        "有成交量时要求突破量至少达到20周期均量的1.5倍；有持仓量时要求持仓量不下降。"
        "入场后的下一根K线若仍未向有利方向发展，或价格重新收回平台内，则退出；随后以EMA20和平台边界做趋势防守。"
        "该策略主要寻找‘蓄势—突破—迅速脱离成本’的行情，震荡反复假突破时会连续小亏。"
    ),
    "su_bing_21macd": (
        "21均线MACD量仓突破：依据21均线、MACD、放量突破和持仓量确认组合而成。"
        "多头要求收盘在21均线上方、DIF高于DEA且MACD柱继续放大，同时突破过去10根K线高点；空头条件完全相反。"
        "为避免普通小波动，本回测版要求成交量至少为20周期均量的1.8倍，并在有持仓量数据时要求持仓量增加。"
        "若价格反复穿越21均线则视为震荡并不入场；持仓后跌回或升回21均线，或MACD方向反转时退出。"
        "它比单独MACD更严格，交易次数较少，重点检验‘趋势+动量+量仓确认’是否能过滤假信号。"
    ),
    "ma_pullback_reversal": (
        "均线顺势缩量回踩反K：综合均线支撑/反压、缩量回调以及锤子线、吞噬或突破上一根高低点的反转K思想。"
        "EMA20在EMA60上方且继续上行时只找多，反之只找空；价格必须先回踩EMA20附近，回调量不能明显高于20周期均量。"
        "随后出现多头/空头反转K确认才入场：包括吞噬形态、长影线拒绝，或收盘突破上一根K线高/低。"
        "持仓后以EMA20为主要防守，出现反向确认或趋势均线失效即退出。该策略不是追突破，而是等待趋势中的低风险二次入场。"
    ),
    "turning_1plus3": (
        "1+3拐点确认：根据‘大一+小三确认’思想做成不使用未来数据的状态机。"
        "多头先出现创近10根新低但收阳的锚点K（大一），随后连续3根收盘都站在锚点收盘价上方；第3根必须收阳且收盘越过中间两根的最高价，才确认多头拐点。"
        "空头完全镜像：创新高收阴，随后3根都在锚点收盘下方，第3根收阴并创新低。"
        "确认后下一根开盘交易，若价格重新跌破或突破锚点收盘则退出；出现反向1+3确认可以反手。"
        "这个策略强调‘先有极端点，再用三根K线证明方向’，因此信号比普通反转K更慢但更严格。"
    ),
    "long_box_60_breakout": (
        "60均线长箱体量仓突破：把‘60均线定大方向、长期横盘箱体、箱内缩量，突破时量仓迅速增加、三天不赚钱离场’量化。"
        "用过去30根K线构成箱体，箱体宽度必须相对ATR收敛；向上突破时要求价格位于60均线上方，向下突破时位于60均线下方。"
        "突破量至少达到箱体平均量的1.5倍；若有持仓量，则同时要求持仓量增加。"
        "突破后如果3根K线内仍没有形成有利浮盈，或价格重新回到60均线另一侧，则退出。"
        "它适合寻找长时间蓄势后的趋势发动，通常交易频率低于短平台突破。"
    ),
}


@dataclass(slots=True)
class BacktestConfig:
    initial_capital: float = 1_000_000.0
    qty: int = 1
    commission_per_side: float = 0.0  # currency / hand / fill
    slippage: float = 0.0             # price units / fill
    margin_rate: float = DEFAULT_MARGIN_RATE


@dataclass(slots=True)
class BacktestResult:
    symbol: str
    display_name: str
    contract: str
    strategy_id: str
    strategy_name: str
    period: str
    start: str
    end: str
    bars: int
    trades: int
    win_rate: float
    net_profit: float
    return_pct: float
    max_drawdown_pct: float
    profit_factor: float
    avg_trade: float
    composite_score: float
    final_equity: float
    markers: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "display_name": self.display_name,
            "contract": self.contract,
            "strategy_id": self.strategy_id,
            "strategy": self.strategy_name,
            "period": self.period,
            "start": self.start,
            "end": self.end,
            "bars": self.bars,
            "trades": self.trades,
            "win_rate": self.win_rate,
            "net_profit": self.net_profit,
            "return_pct": self.return_pct,
            "max_drawdown_pct": self.max_drawdown_pct,
            "profit_factor": self.profit_factor,
            "avg_trade": self.avg_trade,
            "composite_score": self.composite_score,
            "final_equity": self.final_equity,
            "markers": [dict(x) for x in self.markers],
        }


def symbol_root(symbol: str) -> str:
    root = "".join(ch for ch in str(symbol).upper() if ch.isalpha())
    return root or str(symbol).upper()


def contract_multiplier(symbol: str) -> float:
    return float(CONTRACT_MULTIPLIERS.get(symbol_root(symbol), 10.0))


def _ema(s: pd.Series, span: int) -> pd.Series:
    return s.ewm(span=span, adjust=False, min_periods=span).mean()


def _rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    rs = up / dn.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def _cci(df: pd.DataFrame, n: int = 20) -> pd.Series:
    tp = (df["high"] + df["low"] + df["close"]) / 3.0
    ma = tp.rolling(n, min_periods=n).mean()
    md = tp.rolling(n, min_periods=n).apply(lambda x: np.mean(np.abs(x - np.mean(x))), raw=True)
    return (tp - ma) / (0.015 * md.replace(0, np.nan))


def _wr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    hh = df["high"].rolling(n, min_periods=n).max()
    ll = df["low"].rolling(n, min_periods=n).min()
    return -100.0 * (hh - df["close"]) / (hh - ll).replace(0, np.nan)


def _stateful_threshold(index: pd.Index, values: pd.Series,
                        enter_long: Callable[[float], bool], enter_short: Callable[[float], bool],
                        exit_long: Callable[[float], bool], exit_short: Callable[[float], bool]) -> pd.Series:
    out = pd.Series(0, index=index, dtype=np.int8)
    pos = 0
    for i, value in enumerate(values.to_numpy(dtype=float, na_value=np.nan)):
        if not np.isfinite(value):
            out.iat[i] = pos
            continue
        if pos == 0:
            if enter_long(value):
                pos = 1
            elif enter_short(value):
                pos = -1
        elif pos > 0:
            if enter_short(value):
                pos = -1
            elif exit_long(value):
                pos = 0
        else:
            if enter_long(value):
                pos = 1
            elif exit_short(value):
                pos = 0
        out.iat[i] = pos
    return out


def generate_target(df: pd.DataFrame, strategy_id: str) -> pd.Series:
    """Return target position in {-1, 0, +1} decided at each bar close."""
    close = pd.to_numeric(df["close"], errors="coerce").astype(float)

    if strategy_id == "ma_cross":
        fast = close.rolling(10, min_periods=10).mean()
        slow = close.rolling(30, min_periods=30).mean()
        target = np.sign(fast - slow)
        return pd.Series(target, index=df.index).fillna(0).astype(np.int8)

    if strategy_id == "macd":
        dif = _ema(close, 12) - _ema(close, 26)
        dea = _ema(dif, 9)
        return pd.Series(np.sign(dif - dea), index=df.index).fillna(0).astype(np.int8)

    if strategy_id == "rsi":
        r = _rsi(close, 14)
        return _stateful_threshold(df.index, r,
                                   lambda x: x <= 30, lambda x: x >= 70,
                                   lambda x: x >= 55, lambda x: x <= 45)

    if strategy_id == "kdj":
        ll = df["low"].rolling(9, min_periods=9).min()
        hh = df["high"].rolling(9, min_periods=9).max()
        rsv = 100 * (close - ll) / (hh - ll).replace(0, np.nan)
        k = rsv.ewm(alpha=1/3, adjust=False).mean()
        d = k.ewm(alpha=1/3, adjust=False).mean()
        return pd.Series(np.sign(k - d), index=df.index).fillna(0).astype(np.int8)

    if strategy_id == "boll":
        mid = close.rolling(20, min_periods=20).mean()
        std = close.rolling(20, min_periods=20).std(ddof=0)
        upper, lower = mid + 2 * std, mid - 2 * std
        out = pd.Series(0, index=df.index, dtype=np.int8)
        pos = 0
        for i in range(len(df)):
            c, m, u, l = close.iat[i], mid.iat[i], upper.iat[i], lower.iat[i]
            if not all(np.isfinite([c, m, u, l])):
                out.iat[i] = pos; continue
            if pos == 0:
                if c < l: pos = 1
                elif c > u: pos = -1
            elif pos > 0 and c >= m:
                pos = 0
            elif pos < 0 and c <= m:
                pos = 0
            out.iat[i] = pos
        return out

    if strategy_id == "donchian":
        upper = df["high"].rolling(20, min_periods=20).max().shift(1)
        lower = df["low"].rolling(20, min_periods=20).min().shift(1)
        mid = (upper + lower) / 2
        out = pd.Series(0, index=df.index, dtype=np.int8)
        pos = 0
        for i in range(len(df)):
            c, u, l, m = close.iat[i], upper.iat[i], lower.iat[i], mid.iat[i]
            if not all(np.isfinite([c, u, l, m])):
                out.iat[i] = pos; continue
            if c > u: pos = 1
            elif c < l: pos = -1
            elif pos > 0 and c < m: pos = 0
            elif pos < 0 and c > m: pos = 0
            out.iat[i] = pos
        return out

    if strategy_id == "turtle":
        en_hi = df["high"].rolling(20, min_periods=20).max().shift(1)
        en_lo = df["low"].rolling(20, min_periods=20).min().shift(1)
        ex_hi = df["high"].rolling(10, min_periods=10).max().shift(1)
        ex_lo = df["low"].rolling(10, min_periods=10).min().shift(1)
        out = pd.Series(0, index=df.index, dtype=np.int8)
        pos = 0
        for i in range(len(df)):
            c = close.iat[i]
            vals = [c, en_hi.iat[i], en_lo.iat[i], ex_hi.iat[i], ex_lo.iat[i]]
            if not all(np.isfinite(vals)):
                out.iat[i] = pos; continue
            if pos == 0:
                if c > en_hi.iat[i]: pos = 1
                elif c < en_lo.iat[i]: pos = -1
            elif pos > 0 and c < ex_lo.iat[i]:
                pos = 0
            elif pos < 0 and c > ex_hi.iat[i]:
                pos = 0
            out.iat[i] = pos
        return out

    if strategy_id == "momentum":
        roc = close / close.shift(20) - 1.0
        return pd.Series(np.sign(roc), index=df.index).fillna(0).astype(np.int8)

    if strategy_id == "cci":
        cci = _cci(df, 20)
        return _stateful_threshold(df.index, cci,
                                   lambda x: x >= 100, lambda x: x <= -100,
                                   lambda x: x <= 0, lambda x: x >= 0)

    if strategy_id == "wr":
        wr = _wr(df, 14)
        return _stateful_threshold(df.index, wr,
                                   lambda x: x <= -80, lambda x: x >= -20,
                                   lambda x: x >= -50, lambda x: x <= -50)
    if strategy_id == "trend_pullback_resonance":
        # 文档综合策略：趋势 -> 回调 -> 企稳/突破 -> 量价仓确认。
        # 所有条件仅使用当前及过去K线，信号在收盘确认，回测引擎下一根开盘成交。
        high = pd.to_numeric(df["high"], errors="coerce").astype(float)
        low = pd.to_numeric(df["low"], errors="coerce").astype(float)
        open_ = pd.to_numeric(df["open"], errors="coerce").astype(float)

        ema20 = _ema(close, 20)
        ema60 = _ema(close, 60)
        ema20_slope = ema20 - ema20.shift(3)

        prev_close = close.shift(1)
        tr = pd.concat([
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ], axis=1).max(axis=1)
        atr = tr.rolling(14, min_periods=14).mean()

        # “回调到关键位置”量化为最近三根曾触及20EMA附近。
        # 容差使用ATR，避免不同品种、不同价格量级采用同一固定点数。
        tol = 0.35 * atr
        touched_long = ((low <= ema20 + tol) & (high >= ema20 - tol)).rolling(3, min_periods=1).max().astype(bool)
        touched_short = touched_long.copy()

        body = (close - open_).abs()
        bull_confirm = (close > open_) & (close > high.shift(1)) & (body >= 0.35 * atr)
        bear_confirm = (close < open_) & (close < low.shift(1)) & (body >= 0.35 * atr)

        trend_long = (ema20 > ema60) & (ema20_slope > 0)
        trend_short = (ema20 < ema60) & (ema20_slope < 0)

        # 成交量/持仓量字段存在时用于确认；字段不存在时不强制过滤。
        volume_ok_long = pd.Series(True, index=df.index)
        volume_ok_short = pd.Series(True, index=df.index)
        if "volume" in df.columns:
            vol = pd.to_numeric(df["volume"], errors="coerce").astype(float)
            vol_ma20 = vol.rolling(20, min_periods=20).mean()
            vol_ok = vol_ma20.isna() | (vol >= vol_ma20)
            volume_ok_long = vol_ok
            volume_ok_short = vol_ok

        oi_ok_long = pd.Series(True, index=df.index)
        oi_ok_short = pd.Series(True, index=df.index)
        if "open_interest" in df.columns:
            oi = pd.to_numeric(df["open_interest"], errors="coerce").astype(float)
            oi_ok = oi.isna() | oi.shift(1).isna() | (oi >= oi.shift(1))
            oi_ok_long = oi_ok
            oi_ok_short = oi_ok

        long_entry = trend_long & touched_long & bull_confirm & volume_ok_long & oi_ok_long
        short_entry = trend_short & touched_short & bear_confirm & volume_ok_short & oi_ok_short

        out = pd.Series(0, index=df.index, dtype=np.int8)
        pos = 0
        for i in range(len(df)):
            c = close.iat[i]
            e20 = ema20.iat[i]
            if not (np.isfinite(c) and np.isfinite(e20)):
                out.iat[i] = pos
                continue

            if pos == 0:
                if bool(long_entry.iat[i]):
                    pos = 1
                elif bool(short_entry.iat[i]):
                    pos = -1
            elif pos > 0:
                # 趋势支撑失效，或出现反向强确认，则离场/反手。
                if bool(short_entry.iat[i]):
                    pos = -1
                elif c < e20 or (i > 0 and c < low.iat[i - 1] and close.iat[i] < open_.iat[i]):
                    pos = 0
            else:
                if bool(long_entry.iat[i]):
                    pos = 1
                elif c > e20 or (i > 0 and c > high.iat[i - 1] and close.iat[i] > open_.iat[i]):
                    pos = 0
            out.iat[i] = pos
        return out

    if strategy_id == "strong_breakout_pullback":
        # 文档策略：强K突破 -> 等回调突破位 -> 反K/再突破确认。
        # 不在突破当根追单，最多等待5根K线回踩；所有条件只使用当前与历史K线。
        high = pd.to_numeric(df["high"], errors="coerce").astype(float)
        low = pd.to_numeric(df["low"], errors="coerce").astype(float)
        open_ = pd.to_numeric(df["open"], errors="coerce").astype(float)

        ema20 = _ema(close, 20)
        ema60 = _ema(close, 60)
        trend_long = (ema20 > ema60) & (ema20 > ema20.shift(3))
        trend_short = (ema20 < ema60) & (ema20 < ema20.shift(3))

        prev_close = close.shift(1)
        tr = pd.concat([
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ], axis=1).max(axis=1)
        atr = tr.rolling(14, min_periods=14).mean()
        body = (close - open_).abs()
        bar_range = (high - low).replace(0, np.nan)

        prev20_hi = high.rolling(20, min_periods=20).max().shift(1)
        prev20_lo = low.rolling(20, min_periods=20).min().shift(1)
        close_near_high = (close - low) / bar_range >= 0.65
        close_near_low = (high - close) / bar_range >= 0.65

        volume_confirm = pd.Series(True, index=df.index)
        if "volume" in df.columns:
            vol = pd.to_numeric(df["volume"], errors="coerce").astype(float)
            vol_ma20 = vol.rolling(20, min_periods=20).mean()
            volume_confirm = vol_ma20.isna() | (vol >= 1.20 * vol_ma20)

        bull_break = (
            trend_long & (close > prev20_hi) & (close > open_) &
            (body >= 0.70 * atr) & close_near_high & volume_confirm
        )
        bear_break = (
            trend_short & (close < prev20_lo) & (close < open_) &
            (body >= 0.70 * atr) & close_near_low & volume_confirm
        )

        out = pd.Series(0, index=df.index, dtype=np.int8)
        pos = 0
        setup_side = 0
        setup_level = np.nan
        setup_atr = np.nan
        setup_expires = -1
        stop_level = np.nan

        for i in range(len(df)):
            c, o, h, l = close.iat[i], open_.iat[i], high.iat[i], low.iat[i]
            a = atr.iat[i]
            e20 = ema20.iat[i]
            if not all(np.isfinite([c, o, h, l])):
                out.iat[i] = pos
                continue

            # 突破只建立等待回调的观察窗口，不立即追单。
            if bool(bull_break.iat[i]) and np.isfinite(prev20_hi.iat[i]) and np.isfinite(a):
                setup_side = 1
                setup_level = float(prev20_hi.iat[i])
                setup_atr = float(a)
                setup_expires = i + 5
            elif bool(bear_break.iat[i]) and np.isfinite(prev20_lo.iat[i]) and np.isfinite(a):
                setup_side = -1
                setup_level = float(prev20_lo.iat[i])
                setup_atr = float(a)
                setup_expires = i + 5

            if i > setup_expires:
                setup_side = 0

            if pos == 0 and i > 0 and setup_side != 0 and np.isfinite(setup_level) and np.isfinite(setup_atr):
                prev_h, prev_l = high.iat[i - 1], low.iat[i - 1]
                if setup_side > 0:
                    touched = (l <= setup_level + 0.35 * setup_atr) and (l >= setup_level - 0.60 * setup_atr)
                    held = c >= setup_level - 0.20 * setup_atr
                    confirm = (c > o) and (c > prev_h)
                    if touched and held and confirm:
                        pos = 1
                        stop_level = setup_level - 0.60 * setup_atr
                        setup_side = 0
                else:
                    touched = (h >= setup_level - 0.35 * setup_atr) and (h <= setup_level + 0.60 * setup_atr)
                    held = c <= setup_level + 0.20 * setup_atr
                    confirm = (c < o) and (c < prev_l)
                    if touched and held and confirm:
                        pos = -1
                        stop_level = setup_level + 0.60 * setup_atr
                        setup_side = 0
            elif pos > 0:
                if bool(bear_break.iat[i]):
                    pos = -1
                    stop_level = np.nan
                elif (np.isfinite(e20) and c < e20) or (np.isfinite(stop_level) and c < stop_level):
                    pos = 0
                    stop_level = np.nan
            elif pos < 0:
                if bool(bull_break.iat[i]):
                    pos = 1
                    stop_level = np.nan
                elif (np.isfinite(e20) and c > e20) or (np.isfinite(stop_level) and c > stop_level):
                    pos = 0
                    stop_level = np.nan

            out.iat[i] = pos
        return out

    if strategy_id == "price_volume_oi_resonance":
        # 文档策略：价格决定方向，成交量确认动能，持仓量确认新资金。
        # 缺少成交量或持仓量字段时不产生入场，避免把“共振”降级成普通突破。
        high = pd.to_numeric(df["high"], errors="coerce").astype(float)
        low = pd.to_numeric(df["low"], errors="coerce").astype(float)
        ema20 = _ema(close, 20)
        ema60 = _ema(close, 60)

        if "volume" not in df.columns or "open_interest" not in df.columns:
            return pd.Series(0, index=df.index, dtype=np.int8)

        prev10_hi = high.rolling(10, min_periods=10).max().shift(1)
        prev10_lo = low.rolling(10, min_periods=10).min().shift(1)
        trend_long = (ema20 > ema60) & (ema20 > ema20.shift(3))
        trend_short = (ema20 < ema60) & (ema20 < ema20.shift(3))

        vol = pd.to_numeric(df["volume"], errors="coerce").astype(float)
        oi = pd.to_numeric(df["open_interest"], errors="coerce").astype(float)
        vol_ma20 = vol.rolling(20, min_periods=20).mean()
        vol_confirm = vol >= 1.20 * vol_ma20
        oi_confirm = oi > oi.shift(1)

        long_entry = trend_long & (close > prev10_hi) & vol_confirm & oi_confirm
        short_entry = trend_short & (close < prev10_lo) & vol_confirm & oi_confirm

        out = pd.Series(0, index=df.index, dtype=np.int8)
        pos = 0
        for i in range(len(df)):
            c = close.iat[i]
            e20 = ema20.iat[i]
            if not (np.isfinite(c) and np.isfinite(e20)):
                out.iat[i] = pos
                continue
            if pos == 0:
                if bool(long_entry.iat[i]):
                    pos = 1
                elif bool(short_entry.iat[i]):
                    pos = -1
            elif pos > 0:
                if bool(short_entry.iat[i]):
                    pos = -1
                elif c < e20:
                    pos = 0
            else:
                if bool(long_entry.iat[i]):
                    pos = 1
                elif c > e20:
                    pos = 0
            out.iat[i] = pos
        return out

    if strategy_id == "n_platform_breakout":
        high = pd.to_numeric(df["high"], errors="coerce").astype(float)
        low = pd.to_numeric(df["low"], errors="coerce").astype(float)
        open_ = pd.to_numeric(df["open"], errors="coerce").astype(float)
        prev_close = close.shift(1)
        tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
        atr = tr.rolling(14, min_periods=14).mean()
        ema20 = _ema(close, 20)
        box_hi = high.rolling(8, min_periods=8).max().shift(1)
        box_lo = low.rolling(8, min_periods=8).min().shift(1)
        compact = (box_hi - box_lo) <= 2.5 * atr
        vol_ok = pd.Series(True, index=df.index)
        if "volume" in df.columns:
            vol = pd.to_numeric(df["volume"], errors="coerce").astype(float)
            vol_ma = vol.rolling(20, min_periods=20).mean()
            vol_ok = vol_ma.isna() | (vol >= 1.5 * vol_ma)
        oi_ok = pd.Series(True, index=df.index)
        if "open_interest" in df.columns:
            oi = pd.to_numeric(df["open_interest"], errors="coerce").astype(float)
            oi_ok = oi.isna() | oi.shift(1).isna() | (oi >= oi.shift(1))
        long_entry = compact & (close > box_hi) & (close > open_) & vol_ok & oi_ok
        short_entry = compact & (close < box_lo) & (close < open_) & vol_ok & oi_ok
        out = pd.Series(0, index=df.index, dtype=np.int8)
        pos, entry_i, entry_ref, breakout_level = 0, -1, np.nan, np.nan
        for i in range(len(df)):
            c, e20 = close.iat[i], ema20.iat[i]
            if not np.isfinite(c): out.iat[i] = pos; continue
            if pos == 0:
                if bool(long_entry.iat[i]): pos, entry_i, entry_ref, breakout_level = 1, i, c, box_hi.iat[i]
                elif bool(short_entry.iat[i]): pos, entry_i, entry_ref, breakout_level = -1, i, c, box_lo.iat[i]
            elif pos > 0:
                no_follow = (i == entry_i + 1) and np.isfinite(entry_ref) and c <= entry_ref
                failed = np.isfinite(breakout_level) and c < breakout_level
                if bool(short_entry.iat[i]): pos, entry_i, entry_ref, breakout_level = -1, i, c, box_lo.iat[i]
                elif no_follow or failed or (np.isfinite(e20) and c < e20): pos = 0
            else:
                no_follow = (i == entry_i + 1) and np.isfinite(entry_ref) and c >= entry_ref
                failed = np.isfinite(breakout_level) and c > breakout_level
                if bool(long_entry.iat[i]): pos, entry_i, entry_ref, breakout_level = 1, i, c, box_hi.iat[i]
                elif no_follow or failed or (np.isfinite(e20) and c > e20): pos = 0
            out.iat[i] = pos
        return out

    if strategy_id == "su_bing_21macd":
        high = pd.to_numeric(df["high"], errors="coerce").astype(float)
        low = pd.to_numeric(df["low"], errors="coerce").astype(float)
        ma21 = close.rolling(21, min_periods=21).mean()
        dif = _ema(close, 12) - _ema(close, 26)
        dea = _ema(dif, 9)
        hist = dif - dea
        prev_hi = high.rolling(10, min_periods=10).max().shift(1)
        prev_lo = low.rolling(10, min_periods=10).min().shift(1)
        side = np.sign(close - ma21)
        crosses = pd.Series(side, index=df.index).diff().ne(0).rolling(8, min_periods=1).sum()
        trend_ok = crosses <= 2
        vol_ok = pd.Series(True, index=df.index)
        if "volume" in df.columns:
            vol = pd.to_numeric(df["volume"], errors="coerce").astype(float)
            vol_ma = vol.rolling(20, min_periods=20).mean()
            vol_ok = vol_ma.isna() | (vol >= 1.8 * vol_ma)
        oi_ok = pd.Series(True, index=df.index)
        if "open_interest" in df.columns:
            oi = pd.to_numeric(df["open_interest"], errors="coerce").astype(float)
            oi_ok = oi.isna() | oi.shift(1).isna() | (oi > oi.shift(1))
        long_entry = trend_ok & (close > ma21) & (dif > dea) & (hist > 0) & (hist > hist.shift(1)) & (close > prev_hi) & vol_ok & oi_ok
        short_entry = trend_ok & (close < ma21) & (dif < dea) & (hist < 0) & (hist < hist.shift(1)) & (close < prev_lo) & vol_ok & oi_ok
        out = pd.Series(0, index=df.index, dtype=np.int8)
        pos = 0
        for i in range(len(df)):
            c, m = close.iat[i], ma21.iat[i]
            if not (np.isfinite(c) and np.isfinite(m)): out.iat[i] = pos; continue
            if pos == 0:
                if bool(long_entry.iat[i]): pos = 1
                elif bool(short_entry.iat[i]): pos = -1
            elif pos > 0:
                if bool(short_entry.iat[i]): pos = -1
                elif c < m or (np.isfinite(dif.iat[i]) and np.isfinite(dea.iat[i]) and dif.iat[i] < dea.iat[i]): pos = 0
            else:
                if bool(long_entry.iat[i]): pos = 1
                elif c > m or (np.isfinite(dif.iat[i]) and np.isfinite(dea.iat[i]) and dif.iat[i] > dea.iat[i]): pos = 0
            out.iat[i] = pos
        return out

    if strategy_id == "ma_pullback_reversal":
        high = pd.to_numeric(df["high"], errors="coerce").astype(float)
        low = pd.to_numeric(df["low"], errors="coerce").astype(float)
        open_ = pd.to_numeric(df["open"], errors="coerce").astype(float)
        ema20, ema60 = _ema(close, 20), _ema(close, 60)
        prev_close = close.shift(1)
        tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
        atr = tr.rolling(14, min_periods=14).mean()
        tol = 0.35 * atr
        trend_long = (ema20 > ema60) & (ema20 > ema20.shift(3))
        trend_short = (ema20 < ema60) & (ema20 < ema20.shift(3))
        touch = (low <= ema20 + tol) & (high >= ema20 - tol)
        vol_ok = pd.Series(True, index=df.index)
        if "volume" in df.columns:
            vol = pd.to_numeric(df["volume"], errors="coerce").astype(float)
            vol_ma = vol.rolling(20, min_periods=20).mean()
            vol_ok = vol_ma.isna() | (vol <= 1.10 * vol_ma)
        body = (close - open_).abs().replace(0, np.nan)
        lower_wick = np.minimum(open_, close) - low
        upper_wick = high - np.maximum(open_, close)
        bull_engulf = (close > open_) & (open_ <= close.shift(1)) & (close >= open_.shift(1))
        bear_engulf = (close < open_) & (open_ >= close.shift(1)) & (close <= open_.shift(1))
        bull_hammer = (close > open_) & (lower_wick >= 1.5 * body) & (upper_wick <= body)
        bear_star = (close < open_) & (upper_wick >= 1.5 * body) & (lower_wick <= body)
        bull_confirm = bull_engulf | bull_hammer | ((close > open_) & (close > high.shift(1)))
        bear_confirm = bear_engulf | bear_star | ((close < open_) & (close < low.shift(1)))
        long_entry = trend_long & touch & vol_ok & bull_confirm
        short_entry = trend_short & touch & vol_ok & bear_confirm
        out = pd.Series(0, index=df.index, dtype=np.int8)
        pos = 0
        for i in range(len(df)):
            c, e20 = close.iat[i], ema20.iat[i]
            if not (np.isfinite(c) and np.isfinite(e20)): out.iat[i] = pos; continue
            if pos == 0:
                if bool(long_entry.iat[i]): pos = 1
                elif bool(short_entry.iat[i]): pos = -1
            elif pos > 0:
                if bool(short_entry.iat[i]): pos = -1
                elif c < e20: pos = 0
            else:
                if bool(long_entry.iat[i]): pos = 1
                elif c > e20: pos = 0
            out.iat[i] = pos
        return out

    if strategy_id == "turning_1plus3":
        high = pd.to_numeric(df["high"], errors="coerce").astype(float)
        low = pd.to_numeric(df["low"], errors="coerce").astype(float)
        open_ = pd.to_numeric(df["open"], errors="coerce").astype(float)
        prior_lo = low.rolling(10, min_periods=10).min().shift(1)
        prior_hi = high.rolling(10, min_periods=10).max().shift(1)
        bull_anchor = (low < prior_lo) & (close > open_)
        bear_anchor = (high > prior_hi) & (close < open_)
        out = pd.Series(0, index=df.index, dtype=np.int8)
        pos, bull_i, bear_i = 0, -10000, -10000
        bull_close = bear_close = long_stop = short_stop = np.nan
        for i in range(len(df)):
            if bool(bull_anchor.iat[i]): bull_i, bull_close = i, close.iat[i]
            if bool(bear_anchor.iat[i]): bear_i, bear_close = i, close.iat[i]
            long_confirm = short_confirm = False
            if i - bull_i == 3 and bull_i >= 0 and np.isfinite(bull_close):
                mids = close.iloc[bull_i + 1:i + 1]
                prev_h = high.iloc[bull_i + 1:i].max() if i > bull_i + 1 else -np.inf
                long_confirm = bool((mids > bull_close).all() and close.iat[i] > open_.iat[i] and close.iat[i] > prev_h)
            if i - bear_i == 3 and bear_i >= 0 and np.isfinite(bear_close):
                mids = close.iloc[bear_i + 1:i + 1]
                prev_l = low.iloc[bear_i + 1:i].min() if i > bear_i + 1 else np.inf
                short_confirm = bool((mids < bear_close).all() and close.iat[i] < open_.iat[i] and close.iat[i] < prev_l)
            c = close.iat[i]
            if pos == 0:
                if long_confirm: pos, long_stop = 1, bull_close
                elif short_confirm: pos, short_stop = -1, bear_close
            elif pos > 0:
                if short_confirm: pos, short_stop = -1, bear_close
                elif np.isfinite(long_stop) and c < long_stop: pos = 0
            else:
                if long_confirm: pos, long_stop = 1, bull_close
                elif np.isfinite(short_stop) and c > short_stop: pos = 0
            out.iat[i] = pos
        return out

    if strategy_id == "long_box_60_breakout":
        high = pd.to_numeric(df["high"], errors="coerce").astype(float)
        low = pd.to_numeric(df["low"], errors="coerce").astype(float)
        ma60 = close.rolling(60, min_periods=60).mean()
        prev_close = close.shift(1)
        tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
        atr = tr.rolling(14, min_periods=14).mean()
        box_hi = high.rolling(30, min_periods=30).max().shift(1)
        box_lo = low.rolling(30, min_periods=30).min().shift(1)
        compact = (box_hi - box_lo) <= 5.0 * atr
        if "volume" in df.columns:
            vol = pd.to_numeric(df["volume"], errors="coerce").astype(float)
            box_vol = vol.rolling(30, min_periods=30).mean().shift(1)
            vol_ok = vol >= 1.5 * box_vol
        else:
            vol_ok = pd.Series(True, index=df.index)
        oi_ok = pd.Series(True, index=df.index)
        if "open_interest" in df.columns:
            oi = pd.to_numeric(df["open_interest"], errors="coerce").astype(float)
            oi_ok = oi.isna() | oi.shift(1).isna() | (oi > oi.shift(1))
        long_entry = compact & (close > ma60) & (close > box_hi) & vol_ok & oi_ok
        short_entry = compact & (close < ma60) & (close < box_lo) & vol_ok & oi_ok
        out = pd.Series(0, index=df.index, dtype=np.int8)
        pos, entry_i, entry_ref = 0, -1, np.nan
        for i in range(len(df)):
            c, m = close.iat[i], ma60.iat[i]
            if not np.isfinite(c): out.iat[i] = pos; continue
            if pos == 0:
                if bool(long_entry.iat[i]): pos, entry_i, entry_ref = 1, i, c
                elif bool(short_entry.iat[i]): pos, entry_i, entry_ref = -1, i, c
            elif pos > 0:
                time_fail = (i >= entry_i + 3) and np.isfinite(entry_ref) and c <= entry_ref
                if bool(short_entry.iat[i]): pos, entry_i, entry_ref = -1, i, c
                elif (np.isfinite(m) and c < m) or time_fail: pos = 0
            else:
                time_fail = (i >= entry_i + 3) and np.isfinite(entry_ref) and c >= entry_ref
                if bool(long_entry.iat[i]): pos, entry_i, entry_ref = 1, i, c
                elif (np.isfinite(m) and c > m) or time_fail: pos = 0
            out.iat[i] = pos
        return out

    raise KeyError(f"未知策略: {strategy_id}")


def _filter_range(df: pd.DataFrame, start: str | None, end: str | None) -> pd.DataFrame:
    if df.empty or (not start and not end):
        return df.reset_index(drop=True)
    if "trading_day" in df.columns:
        dt = pd.to_datetime(df["trading_day"], errors="coerce")
    else:
        dt = pd.to_datetime(df["datetime"], errors="coerce")
    mask = pd.Series(True, index=df.index)
    if start:
        mask &= dt >= pd.Timestamp(start)
    if end:
        mask &= dt < pd.Timestamp(end) + pd.Timedelta(days=1)
    return df.loc[mask].reset_index(drop=True)


def _composite_score(return_pct: float, max_drawdown_pct: float,
                     profit_factor: float, win_rate: float, trades: int) -> float:
    """Calculate a bounded 0--100 score for comparing backtest results.

    Profitability, drawdown-adjusted return, profit factor, win rate and sample
    size are all bounded before weighting so one extreme statistic cannot
    dominate.  This is a comparison aid, not a forecast of future returns.
    """
    ret = float(return_pct)
    dd = max(0.0, float(max_drawdown_pct))
    pf = max(0.0, float(profit_factor))
    wr = min(100.0, max(0.0, float(win_rate)))
    n = max(0, int(trades))

    return_score = 50.0 + 50.0 * float(np.tanh(ret / 20.0))
    calmar_like = ret / max(dd, 1.0)
    risk_score = 50.0 + 50.0 * float(np.tanh(calmar_like / 2.0))
    pf_for_score = min(pf, 5.0)
    pf_score = 50.0 + 50.0 * float(np.tanh(pf_for_score - 1.0))
    sample_score = min(100.0, n / 30.0 * 100.0)

    score = (
        0.25 * return_score
        + 0.30 * risk_score
        + 0.20 * pf_score
        + 0.15 * wr
        + 0.10 * sample_score
    )
    if n == 0:
        return 0.0

    # A visually impressive result based on one or two trades is not reliable.
    # Apply an explicit sample-confidence multiplier; 20 completed trades are
    # treated as full confidence, while very small samples are heavily capped.
    reliability = min(1.0, float(np.sqrt(n / 20.0)))
    score *= reliability
    if n < 3:
        score = min(score, 25.0)
    elif n < 5:
        score = min(score, 40.0)
    return round(min(100.0, max(0.0, score)), 2)


def run_backtest(df: pd.DataFrame, symbol: str, display_name: str, contract: str,
                 period: str, strategy_id: str, config: BacktestConfig,
                 start: str | None = None, end: str | None = None) -> BacktestResult:
    data = _filter_range(df, start, end)
    if len(data) < 35:
        raise ValueError("有效K线不足 35 根")
    for col in ("open", "high", "low", "close"):
        if col not in data.columns:
            raise ValueError(f"缺少字段 {col}")
        data[col] = pd.to_numeric(data[col], errors="coerce")
    data = data.dropna(subset=["open", "high", "low", "close"]).reset_index(drop=True)
    if len(data) < 35:
        raise ValueError("清洗后有效K线不足 35 根")

    target = generate_target(data, strategy_id)
    multiplier = contract_multiplier(symbol)
    qty = max(1, int(config.qty))
    slip = max(0.0, float(config.slippage))
    fee = max(0.0, float(config.commission_per_side)) * qty
    initial = max(1.0, float(config.initial_capital))
    margin_rate = max(0.0, float(getattr(config, "margin_rate", DEFAULT_MARGIN_RATE)))

    cash = initial
    pos = 0
    entry_price = 0.0
    entry_fee = 0.0
    blocked_target = 0  # insufficient-margin signal is not retried until it resets
    trade_pnls: list[float] = []
    equity_curve: list[float] = [cash]
    markers: list[dict] = []

    def _marker_time(i: int) -> str:
        raw = data.at[i, "datetime"] if "datetime" in data.columns else data.at[i, "trading_day"]
        try:
            return pd.Timestamp(raw).isoformat()
        except Exception:
            return str(raw)

    # signal[t-1] is executed at open[t]
    for i in range(1, len(data)):
        desired = int(target.iat[i - 1])
        raw_open = float(data.at[i, "open"])
        if desired != blocked_target:
            blocked_target = 0
        if pos == 0 and blocked_target != 0 and desired == blocked_target:
            close_px = float(data.at[i, "close"])
            equity_curve.append(cash)
            continue
        if desired != pos:
            if pos != 0:
                old_pos = pos
                exit_px = raw_open - slip if old_pos > 0 else raw_open + slip
                gross = (exit_px - entry_price) * old_pos * qty * multiplier
                cash += gross - fee
                # Per-trade statistics include BOTH entry and exit commissions.
                trade_net = gross - fee - entry_fee
                trade_pnls.append(trade_net)
                markers.append({
                    "datetime": _marker_time(i), "price": float(exit_px),
                    "side": "sell" if old_pos > 0 else "buy",
                    "action": "平多" if old_pos > 0 else "平空",
                    "qty": qty, "pnl": float(trade_net),
                })
                pos = 0
                entry_fee = 0.0
            if desired != 0:
                candidate = raw_open + slip if desired > 0 else raw_open - slip
                required_margin = abs(candidate) * multiplier * qty * margin_rate
                # Fixed-quantity backtests must respect the same basic capital
                # constraint as the simulator.  If the selected quantity cannot
                # be margined, leave the account flat rather than creating an
                # impossible leveraged position.
                if required_margin + fee <= max(0.0, cash):
                    entry_price = candidate
                    entry_fee = fee
                    cash -= entry_fee
                    pos = desired
                    blocked_target = 0
                    markers.append({
                        "datetime": _marker_time(i), "price": float(entry_price),
                        "side": "buy" if desired > 0 else "sell",
                        "action": "开多" if desired > 0 else "开空",
                        "qty": qty, "pnl": 0.0,
                    })
                else:
                    blocked_target = desired
        close_px = float(data.at[i, "close"])
        unreal = (close_px - entry_price) * pos * qty * multiplier if pos else 0.0
        equity_curve.append(cash + unreal)

    # Force-close at the last known close so results are comparable.
    if pos != 0:
        old_pos = pos
        last_close = float(data.iloc[-1]["close"])
        exit_px = last_close - slip if old_pos > 0 else last_close + slip
        gross = (exit_px - entry_price) * old_pos * qty * multiplier
        cash += gross - fee
        trade_net = gross - fee - entry_fee
        trade_pnls.append(trade_net)
        markers.append({
            "datetime": _marker_time(len(data) - 1), "price": float(exit_px),
            "side": "sell" if old_pos > 0 else "buy",
            "action": "平多" if old_pos > 0 else "平空",
            "qty": qty, "pnl": float(trade_net),
        })
        pos = 0
        if equity_curve:
            equity_curve[-1] = cash

    eq = np.asarray(equity_curve, dtype=float)
    peaks = np.maximum.accumulate(eq)
    dd = np.divide(peaks - eq, peaks, out=np.zeros_like(eq), where=peaks > 0)
    max_dd = float(np.nanmax(dd) * 100) if len(dd) else 0.0
    wins = [x for x in trade_pnls if x > 0]
    losses = [x for x in trade_pnls if x < 0]
    gross_profit = float(sum(wins))
    gross_loss = float(-sum(losses))
    pf = gross_profit / gross_loss if gross_loss > 0 else (999.0 if gross_profit > 0 else 0.0)
    net = cash - initial
    ret = net / initial * 100
    trades = len(trade_pnls)
    win_rate = len(wins) / trades * 100 if trades else 0.0
    avg_trade = net / trades if trades else 0.0
    composite_score = _composite_score(ret, max_dd, float(pf), win_rate, trades)

    date_series = pd.to_datetime(data.get("trading_day", data["datetime"]), errors="coerce")
    valid_dates = date_series.dropna()
    start_text = valid_dates.iloc[0].strftime("%Y-%m-%d") if not valid_dates.empty else ""
    end_text = valid_dates.iloc[-1].strftime("%Y-%m-%d") if not valid_dates.empty else ""

    return BacktestResult(
        symbol=str(symbol), display_name=display_name, contract=contract,
        strategy_id=strategy_id, strategy_name=STRATEGIES[strategy_id], period=period,
        start=start_text, end=end_text, bars=len(data), trades=trades,
        win_rate=win_rate, net_profit=net, return_pct=ret,
        max_drawdown_pct=max_dd, profit_factor=float(pf), avg_trade=avg_trade,
        composite_score=composite_score, final_equity=cash, markers=markers,
    )
