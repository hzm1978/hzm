from __future__ import annotations

from dataclasses import dataclass, asdict
from enum import Enum
from typing import Any


class Side(str, Enum):
    LONG = "多"
    SHORT = "空"


class OrderSide(str, Enum):
    BUY = "买"
    SELL = "卖"


@dataclass(slots=True)
class InstrumentMeta:
    symbol: str
    name: str = ""
    exchange: str = ""
    date_range: str = ""

    @property
    def display(self) -> str:
        return f"{self.symbol} {self.name}".strip()


@dataclass(slots=True)
class LimitOrder:
    id: int
    symbol: str
    side: OrderSide
    price: float
    qty: int
    status: str = "等待"

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["side"] = self.side.value
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "LimitOrder":
        return cls(
            id=int(d["id"]),
            symbol=str(d["symbol"]),
            side=OrderSide(d["side"]),
            price=float(d["price"]),
            qty=int(d["qty"]),
            status=str(d.get("status", "等待")),
        )


@dataclass(slots=True)
class Position:
    symbol: str
    side: Side
    qty: int
    avg_price: float
    take_profit: float | None = None
    stop_loss: float | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["side"] = self.side.value
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Position":
        return cls(
            symbol=str(d["symbol"]),
            side=Side(d["side"]),
            qty=int(d["qty"]),
            avg_price=float(d["avg_price"]),
            take_profit=None if d.get("take_profit") is None else float(d["take_profit"]),
            stop_loss=None if d.get("stop_loss") is None else float(d["stop_loss"]),
        )


@dataclass(slots=True)
class FillEvent:
    message: str
    price: float
    qty: int = 0
    realized_pnl: float = 0.0


@dataclass(slots=True)
class Drawing:
    kind: str
    x1: float
    y1: float
    x2: float
    y2: float
    # Optional third anchor used by the three-point wave ruler (A-B-C).
    # Defaults preserve compatibility with all existing two-point drawings.
    c_x: float | None = None
    c_y: float | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        # Keep legacy two-point drawing records unchanged.
        if self.c_x is None:
            payload.pop("c_x", None)
        if self.c_y is None:
            payload.pop("c_y", None)
        return payload

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Drawing":
        return cls(
            str(d["kind"]),
            float(d["x1"]),
            float(d["y1"]),
            float(d["x2"]),
            float(d["y2"]),
            None if d.get("c_x") is None else float(d["c_x"]),
            None if d.get("c_y") is None else float(d["c_y"]),
        )
