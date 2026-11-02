from __future__ import annotations

from math import floor
import re
from typing import Any

from .config import CONTRACT_MULTIPLIERS, DEFAULT_INITIAL_CAPITAL, DEFAULT_MARGIN_RATE
from .models import FillEvent, LimitOrder, OrderSide, Position, Side


class TradingAccount:
    """Single account with independent net positions for multiple instruments.

    Each symbol keeps its own net position, TP/SL and last known mark.  Switching
    the replay instrument therefore never forces the user to flatten positions in
    other instruments.  Account equity / margin / available funds are calculated
    across *all* held symbols.

    ``position`` is retained as a backward-compatible property for older tests and
    save files.  New code should use ``positions`` / ``position_for(symbol)``.
    """

    def __init__(self, initial_capital: float = DEFAULT_INITIAL_CAPITAL, margin_rate: float = DEFAULT_MARGIN_RATE):
        self.balance = float(initial_capital)  # realized account balance
        self.margin_rate = float(margin_rate)
        self.positions: dict[str, Position] = {}
        self.orders: list[LimitOrder] = []
        self.realized_pnl = 0.0
        self._next_order_id = 1
        self.last_marks: dict[str, float] = {}

    # -------------------------- compatibility / helpers --------------------------
    @property
    def position(self) -> Position | None:
        """Backward compatibility with the old single-position account.

        When there is exactly one position, return it.  With zero or multiple
        positions return None so old code does not accidentally act on an
        arbitrary instrument.
        """
        if len(self.positions) == 1:
            return next(iter(self.positions.values()))
        return None

    @position.setter
    def position(self, value: Position | None) -> None:
        self.positions.clear()
        if value is not None:
            self.positions[str(value.symbol)] = value

    TRADE_KEY_SEP = "||"

    @classmethod
    def make_trade_key(cls, source: str, symbol: str, contract: str) -> str:
        """Build a stable identity for one tradable contract/sequence.

        Older saves used only the product code (for example ``EC``).  New saves
        include data source and contract so EC2610 and EC2612 can coexist
        without being merged into one net position.
        """
        source = str(source or "").strip()
        symbol = str(symbol or "").strip()
        contract = str(contract or "").strip()
        if not source and not contract:
            return symbol
        return cls.TRADE_KEY_SEP.join((source, symbol, contract))

    @classmethod
    def split_trade_key(cls, key: str) -> tuple[str, str, str]:
        text = str(key or "")
        parts = text.split(cls.TRADE_KEY_SEP, 2)
        if len(parts) == 3:
            return parts[0], parts[1], parts[2]
        return "", text, ""

    @classmethod
    def symbol_root(cls, symbol: str) -> str:
        _source, product, _contract = cls.split_trade_key(symbol)
        m = re.match(r"[A-Za-z]+", str(product).upper())
        return m.group(0) if m else str(product).upper()

    def migrate_symbol_key(self, old_key: str, new_key: str) -> bool:
        """Migrate one legacy symbol-only position/order to a contract key.

        This is intentionally conservative: if ``new_key`` already exists the
        old position is left untouched rather than silently merging quantities.
        """
        old_key, new_key = str(old_key), str(new_key)
        if not old_key or not new_key or old_key == new_key:
            return False
        moved = False
        if old_key in self.positions and new_key not in self.positions:
            p = self.positions.pop(old_key)
            p.symbol = new_key
            self.positions[new_key] = p
            moved = True
        for order in self.orders:
            if order.symbol == old_key:
                order.symbol = new_key
                moved = True
        if old_key in self.last_marks and new_key not in self.last_marks:
            self.last_marks[new_key] = self.last_marks.pop(old_key)
            moved = True
        return moved

    def multiplier(self, symbol: str) -> float:
        root = self.symbol_root(symbol)
        return float(CONTRACT_MULTIPLIERS.get(root, 10))

    def position_for(self, symbol: str) -> Position | None:
        return self.positions.get(str(symbol))

    def set_mark(self, symbol: str, price: float) -> None:
        try:
            px = float(price)
        except Exception:
            return
        if px > 0:
            self.last_marks[str(symbol)] = px

    def mark_for(self, symbol: str) -> float:
        symbol = str(symbol)
        if symbol in self.last_marks:
            return float(self.last_marks[symbol])
        p = self.positions.get(symbol)
        return float(p.avg_price) if p is not None else 0.0

    def _position_pnl(self, p: Position, mark: float) -> float:
        direction = 1.0 if p.side == Side.LONG else -1.0
        return (float(mark) - p.avg_price) * direction * p.qty * self.multiplier(p.symbol)

    def _position_margin(self, p: Position, mark: float) -> float:
        return abs(p.qty) * float(mark) * self.multiplier(p.symbol) * self.margin_rate

    # -------------------------- account figures --------------------------
    def floating_pnl(self, mark: float | None = None, symbol: str | None = None) -> float:
        if symbol is not None:
            p = self.positions.get(str(symbol))
            if p is None:
                return 0.0
            px = float(mark) if mark is not None else self.mark_for(p.symbol)
            return self._position_pnl(p, px)

        # Old API compatibility: floating_pnl(mark) for one held instrument.
        if mark is not None and len(self.positions) == 1:
            p = next(iter(self.positions.values()))
            return self._position_pnl(p, float(mark))

        return sum(self._position_pnl(p, self.mark_for(p.symbol)) for p in self.positions.values())

    def margin_used(self, mark: float | None = None, symbol: str | None = None) -> float:
        if symbol is not None:
            p = self.positions.get(str(symbol))
            if p is None:
                return 0.0
            px = float(mark) if mark is not None else self.mark_for(p.symbol)
            return self._position_margin(p, px)

        # Old API compatibility: margin_used(mark) for one held instrument.
        if mark is not None and len(self.positions) == 1:
            p = next(iter(self.positions.values()))
            return self._position_margin(p, float(mark))

        return sum(self._position_margin(p, self.mark_for(p.symbol)) for p in self.positions.values())

    def equity(self, mark: float | None = None) -> float:
        return self.balance + self.floating_pnl(mark)

    def available(self, mark: float | None = None) -> float:
        return self.equity(mark) - self.margin_used(mark)

    def _available_after_full_close(self, symbol: str, price: float) -> float:
        """Available cash/margin *after* fully closing ``symbol`` at ``price``.

        This is a pure pre-flight calculation.  It lets reverse orders verify
        that the new leg is affordable before mutating the existing position.
        """
        symbol = str(symbol)
        p = self.positions.get(symbol)
        if p is None:
            return self.available()
        px = float(price)
        direction = 1.0 if p.side == Side.LONG else -1.0
        realized = (px - p.avg_price) * direction * p.qty * self.multiplier(p.symbol)
        other_float = sum(
            self._position_pnl(op, self.mark_for(op.symbol))
            for key, op in self.positions.items() if key != symbol
        )
        other_margin = sum(
            self._position_margin(op, self.mark_for(op.symbol))
            for key, op in self.positions.items() if key != symbol
        )
        return (self.balance + realized + other_float) - other_margin

    def _max_open_qty_after_full_close(self, symbol: str, price: float) -> int:
        per = float(price) * self.multiplier(symbol) * self.margin_rate
        if per <= 0:
            return 0
        return max(0, floor(max(0.0, self._available_after_full_close(symbol, price)) / per))

    def max_open_qty(self, symbol: str, price: float) -> int:
        price = float(price)
        self.set_mark(symbol, price)
        per = price * self.multiplier(symbol) * self.margin_rate
        if per <= 0:
            return 0
        return max(0, floor(max(0.0, self.available()) / per))

    def deposit(self, amount: float) -> None:
        if amount <= 0:
            raise ValueError("入金金额必须大于 0")
        self.balance += float(amount)

    # -------------------------- position operations --------------------------
    def open_market(self, symbol: str, side: Side, qty: int, price: float) -> FillEvent:
        symbol = str(symbol)
        qty = int(qty)
        if qty <= 0:
            raise ValueError("手数必须大于 0")
        price = float(price)
        self.set_mark(symbol, price)
        p = self.positions.get(symbol)

        if p is None:
            max_qty = self.max_open_qty(symbol, price)
            if qty > max_qty:
                raise ValueError(f"资金不足，最大可开 {max_qty} 手")
            self.positions[symbol] = Position(symbol=symbol, side=side, qty=qty, avg_price=price)
            return FillEvent(f"开{side.value} {qty} 手 @ {price:.4f}", price, qty)

        if p.side == side:
            extra_margin = qty * price * self.multiplier(symbol) * self.margin_rate
            if self.available() < extra_margin:
                max_qty = self.max_open_qty(symbol, price)
                raise ValueError(f"资金不足，最大可加仓 {max_qty} 手")
            total = p.qty + qty
            p.avg_price = (p.avg_price * p.qty + price * qty) / total
            p.qty = total
            return FillEvent(f"加{side.value} {qty} 手 @ {price:.4f}", price, qty)

        # Opposite market order on this symbol only.  Pre-flight the new leg
        # BEFORE closing the old one, otherwise a failed reverse could flatten
        # the user's position and still report "资金不足".
        close_qty = min(qty, p.qty)
        leftover = qty - close_qty
        if leftover > 0:
            max_qty = self._max_open_qty_after_full_close(symbol, price)
            if leftover > max_qty:
                raise ValueError(f"反手后剩余资金不足，最多新开 {max_qty} 手；原持仓未变")
        ev = self.close(close_qty, price, symbol=symbol)
        if leftover > 0:
            self.positions[symbol] = Position(symbol=symbol, side=side, qty=leftover, avg_price=price)
            ev.message += f"；反手开{side.value} {leftover} 手 @ {price:.4f}"
            ev.qty += leftover
        return ev

    def close(self, qty: int | None, price: float, symbol: str | None = None) -> FillEvent:
        """Close one symbol.

        ``symbol`` is optional for backward compatibility. If exactly one symbol
        is held, old calls like ``close(1, 2100)`` continue to work.
        """
        if symbol is None:
            if len(self.positions) == 1:
                symbol = next(iter(self.positions))
            elif not self.positions:
                raise ValueError("当前无持仓")
            else:
                raise ValueError("当前有多个品种持仓，请指定要平仓的品种")
        symbol = str(symbol)
        p = self.positions.get(symbol)
        if p is None:
            raise ValueError(f"当前品种 {symbol} 无持仓")

        price = float(price)
        self.set_mark(symbol, price)
        q = p.qty if qty is None else min(int(qty), p.qty)
        if q <= 0:
            raise ValueError("平仓手数必须大于 0")
        direction = 1.0 if p.side == Side.LONG else -1.0
        pnl = (price - p.avg_price) * direction * q * self.multiplier(p.symbol)
        self.balance += pnl
        self.realized_pnl += pnl
        p.qty -= q
        msg = f"平{p.side.value} {q} 手 @ {price:.4f}，实现盈亏 {pnl:.2f}"
        if p.qty <= 0:
            self.positions.pop(symbol, None)
        return FillEvent(msg, price, q, pnl)

    def close_all(self, current_marks: dict[str, float] | None = None) -> list[tuple[str, FillEvent]]:
        """Close every held symbol using its latest known replay mark."""
        current_marks = current_marks or {}
        for sym, px in current_marks.items():
            self.set_mark(sym, px)
        events: list[tuple[str, FillEvent]] = []
        for symbol in list(self.positions.keys()):
            px = self.mark_for(symbol)
            if px <= 0:
                px = self.positions[symbol].avg_price
            events.append((symbol, self.close(None, px, symbol=symbol)))
        return events

    def reverse(self, symbol: str, side: Side, qty: int, price: float) -> FillEvent:
        """Flatten the current contract and open ``qty`` in ``side`` atomically."""
        symbol = str(symbol)
        qty = int(qty)
        price = float(price)
        if qty <= 0:
            raise ValueError("手数必须大于 0")
        p = self.positions.get(symbol)
        if p is not None:
            max_qty = self._max_open_qty_after_full_close(symbol, price)
        else:
            max_qty = self.max_open_qty(symbol, price)
        if qty > max_qty:
            raise ValueError(f"反手资金不足，最多可开 {max_qty} 手；原持仓未变")

        msgs: list[str] = []
        pnl = 0.0
        if p is not None:
            ev = self.close(None, price, symbol=symbol)
            msgs.append(ev.message)
            pnl += ev.realized_pnl
        ev2 = self.open_market(symbol, side, qty, price)
        msgs.append(ev2.message)
        return FillEvent("；".join(msgs), price, qty, pnl)

    # -------------------------- orders / stops --------------------------
    def place_limit(self, symbol: str, side: OrderSide, price: float, qty: int) -> LimitOrder:
        if qty <= 0 or price <= 0:
            raise ValueError("委托价格和手数必须大于 0")
        order = LimitOrder(self._next_order_id, str(symbol), side, float(price), int(qty))
        self._next_order_id += 1
        self.orders.append(order)
        return order

    def cancel(self, order_id: int) -> bool:
        before = len(self.orders)
        self.orders = [o for o in self.orders if o.id != int(order_id)]
        return len(self.orders) < before

    def cancel_all(self) -> None:
        self.orders.clear()

    def set_take_profit(self, value: float | None, symbol: str | None = None) -> None:
        p = self._resolve_position(symbol)
        if p is not None:
            p.take_profit = None if value is None else float(value)

    def set_stop_loss(self, value: float | None, symbol: str | None = None) -> None:
        p = self._resolve_position(symbol)
        if p is not None:
            p.stop_loss = None if value is None else float(value)

    def _resolve_position(self, symbol: str | None) -> Position | None:
        if symbol is not None:
            return self.positions.get(str(symbol))
        if len(self.positions) == 1:
            return next(iter(self.positions.values()))
        return None

    def process_bar(self, symbol: str, o: float, h: float, l: float, c: float) -> list[FillEvent]:
        """Process one real replay minute for one instrument only.

        Other instruments remain held and are valued at their last replay marks.
        Limit orders use high/low touch semantics. If the bar opens through a
        limit, the better opening price is used. TP/SL ambiguity inside the same
        minute is resolved conservatively: stop-loss first.
        """
        symbol = str(symbol)
        self.set_mark(symbol, c)
        events: list[FillEvent] = []
        remaining: list[LimitOrder] = []
        for order in self.orders:
            if order.symbol != symbol:
                remaining.append(order)
                continue
            touched = (order.side == OrderSide.BUY and l <= order.price) or (order.side == OrderSide.SELL and h >= order.price)
            if not touched:
                remaining.append(order)
                continue
            if order.side == OrderSide.BUY:
                fill = min(float(o), order.price) if o <= order.price else order.price
                side = Side.LONG
            else:
                fill = max(float(o), order.price) if o >= order.price else order.price
                side = Side.SHORT
            try:
                ev = self.open_market(symbol, side, order.qty, fill)
                order.status = "成交"
                events.append(FillEvent(f"限价单#{order.id}成交：{ev.message}", fill, order.qty, ev.realized_pnl))
            except ValueError as exc:
                # A touched order that cannot be afforded is rejected once.
                # Keeping it active would make it retry on every later bar and
                # could unexpectedly execute long after the original decision.
                order.status = "已拒绝"
                events.append(FillEvent(f"限价单#{order.id}已拒绝：{exc}", order.price, 0, 0))
        self.orders = remaining

        p = self.positions.get(symbol)
        if p is None:
            return events
        take = p.take_profit
        stop = p.stop_loss
        if p.side == Side.LONG:
            stop_hit = stop is not None and l <= stop
            take_hit = take is not None and h >= take
            if stop_hit:
                fill = min(float(o), float(stop)) if o < stop else float(stop)
                events.append(self.close(None, fill, symbol=symbol))
            elif take_hit:
                fill = max(float(o), float(take)) if o > take else float(take)
                events.append(self.close(None, fill, symbol=symbol))
        else:
            stop_hit = stop is not None and h >= stop
            take_hit = take is not None and l <= take
            if stop_hit:
                fill = max(float(o), float(stop)) if o > stop else float(stop)
                events.append(self.close(None, fill, symbol=symbol))
            elif take_hit:
                fill = min(float(o), float(take)) if o < take else float(take)
                events.append(self.close(None, fill, symbol=symbol))
        return events

    # -------------------------- persistence --------------------------
    def to_dict(self) -> dict[str, Any]:
        # Keep legacy ``position`` only when there is exactly one position so old
        # versions can still open a new save without crashing. New versions use
        # the explicit ``positions`` map.
        legacy_position = self.position
        return {
            "balance": self.balance,
            "margin_rate": self.margin_rate,
            "realized_pnl": self.realized_pnl,
            "positions": {sym: p.to_dict() for sym, p in self.positions.items()},
            "position": None if legacy_position is None else legacy_position.to_dict(),
            "orders": [x.to_dict() for x in self.orders],
            "next_order_id": self._next_order_id,
            "last_marks": dict(self.last_marks),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "TradingAccount":
        obj = cls(float(d.get("balance", DEFAULT_INITIAL_CAPITAL)), float(d.get("margin_rate", DEFAULT_MARGIN_RATE)))
        obj.realized_pnl = float(d.get("realized_pnl", 0.0))

        raw_positions = d.get("positions")
        if isinstance(raw_positions, dict) and raw_positions:
            for sym, raw in raw_positions.items():
                try:
                    p = Position.from_dict(raw)
                    obj.positions[str(sym or p.symbol)] = p
                except Exception:
                    continue
        elif d.get("position"):
            # Seamless migration from the old single-position save format.
            p = Position.from_dict(d["position"])
            obj.positions[p.symbol] = p

        obj.orders = [LimitOrder.from_dict(x) for x in d.get("orders", [])]
        obj._next_order_id = int(d.get("next_order_id", max([o.id for o in obj.orders], default=0) + 1))
        for sym, px in (d.get("last_marks", {}) or {}).items():
            try:
                obj.last_marks[str(sym)] = float(px)
            except Exception:
                pass
        for sym, p in obj.positions.items():
            obj.last_marks.setdefault(sym, float(p.avg_price))
        return obj
