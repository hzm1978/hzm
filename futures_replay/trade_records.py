from __future__ import annotations

import csv
import sys
from pathlib import Path
from typing import Any


TRADE_HEADERS = ["time", "symbol", "action", "price", "qty", "realized_pnl", "trade_key"]


def default_record_dir() -> Path:
    """Return a stable, user-visible record directory next to run.py / the exe."""
    try:
        base = Path(sys.argv[0]).resolve().parent
    except Exception:
        base = Path.cwd()
    return base / "交易记录"


class DailyTradeRecorder:
    """Persist today's fills, while keeping order history in memory only.

    * 成交记录.csv is kept for the operator's current real calendar day.
    * 委托记录 is session-only: it is not written to disk and is empty after
      restarting the program.
    * Filled orders are not intended to be shown in the 委托记录 tab; the UI
      filters status == "成交" because the execution already appears in 成交记录.
    """

    def __init__(self, root: Path | None = None):
        self.root = Path(root) if root is not None else default_record_dir()
        self.root.mkdir(parents=True, exist_ok=True)
        self.trade_path = self.root / "成交记录.csv"
        # Compatibility cleanup: older builds persisted 委托记录.csv.  The new
        # requirement is explicitly not to save order history, so remove it.
        self.legacy_order_path = self.root / "委托记录.csv"
        try:
            if self.legacy_order_path.exists():
                self.legacy_order_path.unlink()
        except Exception:
            pass
        self.current_day = ""
        self.trades: list[dict[str, Any]] = []
        self.orders: list[dict[str, Any]] = []
        self._market_seq = 1
        self._conditional_seq = 1
        self._active_conditionals: dict[tuple[str, str], str] = {}

    @staticmethod
    def normalize_day(day: Any) -> str:
        text = str(day or "").strip()
        return text[:10] if text else ""

    def ensure_day(self, day: Any) -> bool:
        """Switch to the operator's real calendar day, never replay trading_day."""
        day_text = self.normalize_day(day)
        if not day_text:
            return False
        if self.current_day == day_text:
            return False

        loaded_trades = self._read_csv(self.trade_path, TRADE_HEADERS)
        saved_day = self.normalize_day(loaded_trades[0].get("time", "")) if loaded_trades else ""

        self.current_day = day_text
        # Order records are deliberately session-only and are never restored.
        self.orders = []
        self._market_seq = 1
        self._conditional_seq = 1
        self._active_conditionals = {}

        if saved_day == day_text:
            self.trades = loaded_trades
            return True

        # Real calendar day changed: clear yesterday and rewrite today's fill file.
        self.trades = []
        self._write_csv(self.trade_path, TRADE_HEADERS, self.trades)
        return True

    @staticmethod
    def _read_csv(path: Path, headers: list[str]) -> list[dict[str, str]]:
        if not path.exists():
            return []
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as f:
                out = []
                for row in csv.DictReader(f):
                    if row:
                        out.append({h: row.get(h, "") for h in headers})
                return out
        except Exception:
            return []

    @staticmethod
    def _write_csv(path: Path, headers: list[str], rows: list[dict[str, Any]]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=headers, extrasaction="ignore")
            w.writeheader()
            for row in rows:
                w.writerow({h: row.get(h, "") for h in headers})
        tmp.replace(path)

    def add_trade(
        self,
        time_text: str,
        symbol: str,
        action: str,
        price: float,
        qty: int,
        realized_pnl: float = 0.0,
        trade_key: str = "",
    ) -> None:
        self.trades.append({
            "time": str(time_text),
            "symbol": str(symbol),
            "action": str(action),
            "price": f"{float(price):.6f}",
            "qty": str(int(qty)),
            "realized_pnl": f"{float(realized_pnl):.2f}",
            # Internal navigation identity. The UI displays the human-readable
            # symbol label, but clicks can return to the exact source/product/
            # contract that generated this fill.
            "trade_key": str(trade_key or ""),
        })
        self._write_csv(self.trade_path, TRADE_HEADERS, self.trades)

    def add_limit_order(self, time_text: str, symbol: str, order_id: int, action: str, price: float, qty: int, status: str = "等待") -> None:
        # Session memory only: do not write an order CSV.
        self.orders.append({
            "time": str(time_text),
            "symbol": str(symbol),
            "order_id": str(int(order_id)),
            "action": str(action),
            "price": f"{float(price):.6f}",
            "qty": str(int(qty)),
            "status": str(status),
        })

    def add_market_order(self, time_text: str, symbol: str, action: str, price: float, qty: int, status: str = "成交") -> str:
        """Keep only non-filled market-order attempts in the in-memory order list.

        Successful market executions are already represented by 成交记录 and
        therefore must not be duplicated in 委托记录.
        """
        oid = f"M{self._market_seq}"
        self._market_seq += 1
        if str(status).strip() != "成交":
            self.orders.append({
                "time": str(time_text),
                "symbol": str(symbol),
                "order_id": oid,
                "action": str(action),
                "price": f"{float(price):.6f}",
                "qty": str(int(qty)),
                "status": str(status),
            })
        return oid


    def get_order(self, order_id: int | str) -> dict[str, Any] | None:
        key = str(order_id)
        for row in reversed(self.orders):
            if str(row.get("order_id", "")) == key:
                return row
        return None

    def upsert_conditional_order(
        self,
        time_text: str,
        symbol: str,
        trade_key: str,
        kind: str,
        action: str,
        price: float,
        qty: int,
        status: str = "未触发",
    ) -> str:
        """Create or update one active TP/SL conditional order.

        Re-dragging the same TP/SL line updates the existing row instead of
        appending duplicates.  Once a row is cancelled/filled, a later newly
        created TP/SL receives a fresh conditional ID.
        """
        trade_key = str(trade_key)
        kind = str(kind)
        map_key = (trade_key, kind)
        oid = self._active_conditionals.get(map_key)
        row = self.get_order(oid) if oid else None
        if row is None or str(row.get("status", "")) not in ("未触发", "等待", "挂单"):
            oid = f"C{self._conditional_seq}"
            self._conditional_seq += 1
            row = {
                "time": str(time_text),
                "symbol": str(symbol),
                "order_id": oid,
                "action": str(action),
                "price": f"{float(price):.6f}",
                "qty": str(int(qty)),
                "status": str(status),
                "trade_key": trade_key,
                "kind": kind,
            }
            self.orders.append(row)
            self._active_conditionals[map_key] = oid
            return oid

        # Mouse release after a drag is treated as a modification of the same
        # conditional order.  The displayed time is therefore the latest edit time.
        row.update({
            "time": str(time_text),
            "symbol": str(symbol),
            "action": str(action),
            "price": f"{float(price):.6f}",
            "qty": str(int(qty)),
            "status": str(status),
            "trade_key": trade_key,
            "kind": kind,
        })
        return str(oid)

    def update_conditional_qty(self, trade_key: str, qty: int) -> bool:
        changed = False
        trade_key = str(trade_key)
        for kind in ("止盈", "止损"):
            oid = self._active_conditionals.get((trade_key, kind))
            row = self.get_order(oid) if oid else None
            if row is not None and str(row.get("status", "")) in ("未触发", "等待", "挂单"):
                value = str(int(qty))
                if row.get("qty") != value:
                    row["qty"] = value
                    changed = True
        return changed

    def remove_conditional(self, trade_key: str, kind: str) -> bool:
        """Remove one active TP/SL conditional from the session order list.

        TP/SL is treated as a live instruction rather than an audit-history row.
        When it is cancelled, deleted from the chart, made obsolete by a manual
        close/reverse, or actually triggered, the row disappears from 委托记录.
        The real execution (if any) is recorded only in 成交记录.
        """
        map_key = (str(trade_key), str(kind))
        oid = self._active_conditionals.pop(map_key, None)
        if not oid:
            return False
        before = len(self.orders)
        self.orders = [
            row for row in self.orders
            if str(row.get("order_id", "")) != str(oid)
        ]
        return len(self.orders) != before

    def remove_conditionals_for_trade_key(self, trade_key: str) -> bool:
        changed = False
        for kind in ("止盈", "止损"):
            changed = self.remove_conditional(trade_key, kind) or changed
        return changed

    def remove_all_conditionals(self) -> bool:
        changed = False
        for trade_key, kind in list(self._active_conditionals.keys()):
            changed = self.remove_conditional(trade_key, kind) or changed
        return changed

    # Backward-compatible names used by older call sites.  Conditional
    # cancellations are intentionally not retained as historical 委托记录 rows.
    def finish_conditional(self, trade_key: str, kind: str, status: str = "") -> bool:
        return self.remove_conditional(trade_key, kind)

    def cancel_conditionals_for_trade_key(self, trade_key: str, status: str = "") -> bool:
        return self.remove_conditionals_for_trade_key(trade_key)

    def cancel_all_conditionals(self, status: str = "") -> bool:
        return self.remove_all_conditionals()

    def update_order_status(self, order_id: int | str, status: str) -> bool:
        key = str(order_id)
        for row in reversed(self.orders):
            if str(row.get("order_id", "")) == key:
                if row.get("status") != status:
                    row["status"] = str(status)
                    return True
                return False
        return False

    def clear_current_day(self) -> None:
        self.trades = []
        self.orders = []
        self._market_seq = 1
        self._conditional_seq = 1
        self._active_conditionals = {}
        self._write_csv(self.trade_path, TRADE_HEADERS, self.trades)
