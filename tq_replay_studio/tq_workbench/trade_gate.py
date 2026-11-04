"""Immutable order review, explicit live arming and one-use submission tokens."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import math
import time
import uuid

import pandas as pd

from .bars import finite, now_beijing


@dataclass(frozen=True)
class TradePolicy:
    max_lots: int = 5
    max_notional: float = 5_000_000
    max_quote_age: float = 120

    def validate(self):
        if self.max_lots < 1 or not math.isfinite(self.max_notional) or self.max_notional <= 0:
            raise ValueError("单笔手数和名义金额上限必须大于零")
        if not 1 <= self.max_quote_age <= 3600:
            raise ValueError("报价时效限制需为1至3600秒")


class TradeGate:
    def __init__(self, api, mode="sim", policy=None):
        if mode not in ("sim", "kq", "live"):
            raise ValueError("未知账户类型")
        self.api, self.mode = api, mode
        self.policy = policy or TradePolicy(); self.policy.validate()
        self.armed = False
        self.symbol = ""
        self.quote = {}
        self.reviews = {}

    def arm(self, enabled):
        self.armed = enabled is True
        self.reviews.clear()

    def watch(self, requested):
        quote = self.api.get_quote(requested)
        actual = str(quote.get("underlying_symbol", "") or "") if requested.startswith("KQ.m@") else requested
        if not actual or actual.startswith(("KQ.", "KQ.m@", "LOCAL.", "DEMO.")):
            raise ValueError("无法解析实际期货合约，不能交易主连／本地代码")
        self.symbol = actual
        self.quote = self.api.get_quote(actual)
        self.reviews.clear()
        return actual

    def validate_order(self, draft, now=None):
        if self.mode == "live" and not self.armed:
            raise ValueError("实盘下单尚未解锁")
        symbol = draft.get("symbol")
        if not symbol or symbol != self.symbol or symbol.startswith(("KQ.", "KQ.m@", "LOCAL.", "DEMO.")):
            raise ValueError("实际合约已改变或代码无效，请重新核对")
        quote = self.quote
        if quote.get("ins_class") != "FUTURE" or quote.get("expired", False):
            raise ValueError("只能交易未到期的实际期货合约")
        direction, offset = draft.get("direction"), draft.get("offset")
        if direction not in ("BUY", "SELL") or offset not in ("OPEN", "CLOSE", "CLOSETODAY"):
            raise ValueError("买卖方向或开平标志无效")
        qty = draft.get("volume")
        if isinstance(qty, bool) or not isinstance(qty, int) or not 1 <= qty <= self.policy.max_lots:
            raise ValueError(f"单笔手数需为1至{self.policy.max_lots}的整数")
        price, tick, multiplier = finite(draft.get("limit_price")), finite(quote.get("price_tick")), finite(quote.get("volume_multiple"))
        if price is None or price <= 0 or tick is None or tick <= 0 or multiplier is None or multiplier <= 0:
            raise ValueError("委托价格或合约参数尚未有效返回")
        if abs(price/tick-round(price/tick)) > 1e-5:
            raise ValueError("限价必须是最小变动价位的整数倍")
        now = now_beijing() if now is None else pd.Timestamp(now)
        if now.tzinfo is not None:
            now = now.tz_convert("Asia/Shanghai").tz_localize(None)
        stamp = pd.to_datetime(quote.get("datetime", ""), errors="coerce")
        if pd.isna(stamp):
            raise ValueError("尚未取得报价时间")
        if stamp.tzinfo is not None:
            stamp = stamp.tz_convert("Asia/Shanghai").tz_localize(None)
        age = (now-stamp).total_seconds()
        if age < -5 or age > self.policy.max_quote_age:
            raise ValueError(f"报价超过{self.policy.max_quote_age:g}秒或本机时间异常；等待新报价后再下单")
        notional = price*qty*multiplier
        if notional > self.policy.max_notional:
            raise ValueError(f"超过单笔名义金额上限 {self.policy.max_notional:g}")
        upper, lower = finite(quote.get("upper_limit")), finite(quote.get("lower_limit"))
        if (upper is not None and upper > 0 and price > upper) or (lower is not None and lower > 0 and price < lower):
            raise ValueError("限价超出交易所涨跌停范围")
        maximum = finite(quote.get("max_limit_order_volume"))
        minimum = finite(quote.get("min_limit_order_volume"))
        if maximum is not None and maximum > 0 and qty > maximum:
            raise ValueError("超过交易所单笔最大限价手数")
        if minimum is not None and minimum > 0 and qty < minimum:
            raise ValueError("低于交易所最小限价手数")
        if offset != "OPEN":
            position = self.api.get_position(symbol)
            side = "long" if direction == "SELL" else "short"
            exchange = symbol.split(".")[0]
            if offset == "CLOSETODAY" and exchange not in ("SHFE", "INE"):
                raise ValueError("该交易所请使用普通平仓标志")
            suffix = "_today" if offset == "CLOSETODAY" else "_his" if exchange in ("SHFE", "INE") else ""
            available = finite(position.get("pos_"+side+suffix), 0)
            frozen_key = "volume_"+side+"_frozen"+suffix
            available -= finite(position.get(frozen_key), 0)
            if qty > available:
                raise ValueError("所选开平标志下可平手数不足，请核对平今／平昨及冻结委托")
        return dict(symbol=symbol, direction=direction, offset=offset, volume=qty,
                    limit_price=float(price), notional=notional, quote_time=str(stamp))

    def prepare(self, draft, now=None, clock=None):
        reviewed = self.validate_order(draft, now)
        clock = time.monotonic() if clock is None else clock
        self.reviews = {key:value for key,value in self.reviews.items() if value["expires"] > clock}
        if len(self.reviews) >= 32:
            self.reviews.clear()
        token = uuid.uuid4().hex
        self.reviews[token] = dict(order=reviewed, expires=clock+20)
        return dict(token=token, mode=self.mode, **reviewed)

    def submit(self, token, now=None, clock=None):
        # Consume BEFORE touching the SDK: a failed/uncertain send is never retried.
        review = self.reviews.pop(token, None)
        clock = time.monotonic() if clock is None else clock
        if review is None or clock > review["expires"]:
            raise ValueError("确认已使用或超过20秒，请重新核对委托")
        order = self.validate_order(review["order"], now)
        return self.api.insert_order(symbol=order["symbol"], direction=order["direction"],
            offset=order["offset"], volume=order["volume"], limit_price=order["limit_price"])

    def cancel(self, identifier):
        order = self.api.get_order().get(identifier)
        if order is None or order.get("status") != "ALIVE":
            raise ValueError("该委托当前不可撤销")
        self.api.cancel_order(order)
