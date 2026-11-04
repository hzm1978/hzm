"""Deterministic local futures training account; no broker or SDK orders."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from copy import deepcopy
import math


@dataclass
class Contract:
    multiplier: float = 10
    tick: float = 1
    margin_rate: float = .12
    fee_per_lot: float = 3
    slippage_ticks: int = 1

    def validate(self):
        numbers = (self.multiplier, self.tick, self.margin_rate, self.fee_per_lot)
        if not all(math.isfinite(float(x)) for x in numbers):
            raise ValueError("合约参数必须是有限数")
        if self.multiplier <= 0 or self.tick <= 0 or not 0 < self.margin_rate <= 1 or self.fee_per_lot < 0:
            raise ValueError("合约乘数、最小变动价位、保证金或手续费不正确")
        if int(self.slippage_ticks) != self.slippage_ticks or self.slippage_ticks < 0:
            raise ValueError("滑点必须是非负整数跳")


ACTIONS = {"开多": (1, True), "开空": (-1, True), "平多": (-1, False), "平空": (1, False)}


class PaperAccount:
    def __init__(self, capital=1_000_000., contract=None):
        if not math.isfinite(float(capital)) or capital <= 0:
            raise ValueError("初始资金必须大于零")
        self.initial = float(capital)
        self.contract = contract or Contract()
        self.contract.validate()
        self.balance = self.initial
        self.direction = 0
        self.qty = 0
        self.average = 0.
        self.take_profit = self.stop_loss = None
        self.orders = []
        self.fills = []
        self.last_bar = ""
        self.equity_curve = []

    @property
    def has_activity(self):
        return bool(self.fills or self.orders)

    def unrealized(self, mark):
        return (float(mark)-self.average)*self.direction*self.qty*self.contract.multiplier

    def metrics(self, mark):
        floating = self.unrealized(mark)
        margin = abs(float(mark))*self.qty*self.contract.multiplier*self.contract.margin_rate
        equity = self.balance+floating
        return dict(balance=self.balance, unrealized=floating, equity=equity,
                    margin=margin, available=equity-margin, realized=self.balance-self.initial)

    def _price(self, action, reference, *, limit=None):
        sign = ACTIONS[action][0]
        value = float(reference)+sign*self.contract.tick*self.contract.slippage_ticks
        if limit is not None:
            value = min(value, limit) if sign > 0 else max(value, limit)
        return round(round(value/self.contract.tick)*self.contract.tick, 10)

    def execute(self, action, qty, reference, timestamp, *, limit=None, reason="手动"):
        if action not in ACTIONS:
            raise ValueError("未知委托方向")
        if isinstance(qty, bool) or int(qty) != qty or qty <= 0:
            raise ValueError("手数必须是正整数")
        qty = int(qty)
        if not math.isfinite(float(reference)) or reference <= 0:
            raise ValueError("成交参考价无效")
        self.contract.validate()
        sign, opening = ACTIONS[action]
        price = self._price(action, reference, limit=limit)
        if price <= 0:
            raise ValueError("滑点后的成交价无效，请检查最小变动价位")
        fee = qty*self.contract.fee_per_lot
        pnl = 0.
        if opening:
            if self.qty and self.direction != sign:
                raise ValueError("已有反向持仓，请先平仓")
            metrics = self.metrics(price)
            required = abs(price)*qty*self.contract.multiplier*self.contract.margin_rate
            if metrics["available"]+1e-8 < required+fee:
                raise ValueError("可用资金不足")
            self.average = (self.average*self.qty+price*qty)/(self.qty+qty)
            self.qty += qty
            self.direction = sign
        else:
            if not self.qty or self.direction != -sign or qty > self.qty:
                raise ValueError("可平仓手数不足")
            pnl = (price-self.average)*self.direction*qty*self.contract.multiplier
            self.qty -= qty
            if not self.qty:
                self.direction = 0
                self.average = 0.
                self.take_profit = self.stop_loss = None
        self.balance += pnl-fee
        fill = dict(timestamp=str(timestamp), action=action, qty=qty, price=price,
                    pnl=pnl, fee=fee, net=pnl-fee, reason=reason)
        self.fills.append(fill)
        return fill

    def submit_limit(self, action, qty, price, timestamp):
        if action not in ACTIONS or isinstance(qty, bool) or int(qty) != qty or qty <= 0:
            raise ValueError("委托方向或手数无效")
        if not math.isfinite(float(price)) or price <= 0:
            raise ValueError("委托价格无效")
        if abs(price/self.contract.tick-round(price/self.contract.tick)) > 1e-5:
            raise ValueError("委托价格必须是最小变动价位的整数倍")
        order = dict(id=len(self.orders)+1, action=action, qty=int(qty), price=float(price),
                     placed_at=str(timestamp), status="待成交", message="")
        self.orders.append(order)
        return order

    def cancel(self, identifier):
        for order in self.orders:
            if order["id"] == identifier and order["status"] == "待成交":
                order["status"] = "已撤销"

    def set_bracket(self, take=None, stop=None, mark=None):
        if not self.qty:
            raise ValueError("请先建立持仓")
        for value in (take, stop):
            if value is not None and (not math.isfinite(value) or value <= 0):
                raise ValueError("止盈止损必须为有效价格")
        reference = self.average if mark is None else float(mark)
        if take is not None and (take-reference)*self.direction <= 0:
            raise ValueError("止盈应位于当前价格的盈利方向")
        if stop is not None and (reference-stop)*self.direction <= 0:
            raise ValueError("止损应位于当前价格的亏损方向")
        self.take_profit, self.stop_loss = take, stop

    def on_bar(self, row):
        stamp = str(row["datetime"])
        if self.last_bar and stamp <= self.last_bar:
            return []
        opening, high, low, close = [float(row[x]) for x in ("open", "high", "low", "close")]
        before = len(self.fills)
        # Existing bracket is processed first. If both levels touch, assume stop first.
        if self.qty:
            sign = self.direction
            stop, take = self.stop_loss, self.take_profit
            stop_hit = stop is not None and (low <= stop if sign > 0 else high >= stop)
            take_hit = take is not None and (high >= take if sign > 0 else low <= take)
            if stop_hit or take_hit:
                level = stop if stop_hit else take
                if stop_hit:
                    price = min(opening, level) if sign > 0 else max(opening, level)
                else:
                    price = max(opening, level) if sign > 0 else min(opening, level)
                self.execute("平多" if sign > 0 else "平空", self.qty, price, stamp,
                             reason="止损" if stop_hit else "止盈")
        for order in self.orders:
            if order["status"] != "待成交" or stamp <= order["placed_at"]:
                continue
            sign = ACTIONS[order["action"]][0]
            touched = low <= order["price"] if sign > 0 else high >= order["price"]
            if touched:
                price = min(opening, order["price"]) if sign > 0 else max(opening, order["price"])
                try:
                    self.execute(order["action"], order["qty"], price, stamp,
                                 limit=order["price"], reason=f"限价#{order['id']}")
                    order["status"] = "已成交"
                except ValueError as exc:
                    order["status"] = "已拒绝"; order["message"] = str(exc)
        self.last_bar = stamp
        self.equity_curve.append(dict(timestamp=stamp, equity=self.metrics(close)["equity"]))
        return self.fills[before:]

    def to_dict(self):
        return deepcopy(dict(initial=self.initial, contract=asdict(self.contract), balance=self.balance,
                    direction=self.direction, qty=self.qty, average=self.average,
                    take_profit=self.take_profit, stop_loss=self.stop_loss,
                    orders=self.orders, fills=self.fills, last_bar=self.last_bar, equity_curve=self.equity_curve))

    @classmethod
    def from_dict(cls, state):
        obj = cls(state["initial"], Contract(**state["contract"]))
        for key in ("balance", "direction", "qty", "average", "take_profit", "stop_loss",
                    "orders", "fills", "last_bar", "equity_curve"):
            if key in state:
                setattr(obj, key, deepcopy(state[key]))
        return obj
