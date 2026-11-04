import queue
from pathlib import Path
import tempfile
import threading
import unittest

from tq_workbench.bars import now_beijing
from tq_workbench.trade_gate import TradeGate, TradePolicy
from tq_workbench.trade_worker import serve_trade


class TradingApi:
    def __init__(self):
        self.quote = dict(ins_class="FUTURE", expired=False, datetime=str(now_beijing()),
            underlying_symbol="DCE.jm2612", last_price=100, bid_price1=99.5, ask_price1=100,
            volume_multiple=60, price_tick=.5, upper_limit=120, lower_limit=80,
            max_limit_order_volume=100, min_limit_order_volume=1)
        self.position = dict(pos_long=5, pos_long_today=2, pos_long_his=3,
                             pos_short=0, pos_short_today=0, pos_short_his=0)
        self.sent = []; self.orders = {}; self.cancelled = []; self.closed = False

    def get_quote(self, symbol):
        return self.quote

    def get_position(self, symbol=None):
        return self.position if symbol else {"DCE.jm2612": self.position}

    def get_account(self):
        return dict(balance=1_000_000, available=990_000, margin=10000, float_profit=0)

    def get_order(self):
        return self.orders

    def get_trade(self):
        return {}

    def insert_order(self, **kwargs):
        self.sent.append(kwargs)
        identifier = f"order-{len(self.sent)}"
        order = dict(order_id=identifier, status="ALIVE", volume_left=kwargs["volume"],
                     volume_orign=kwargs["volume"], instrument_id=kwargs["symbol"].split(".")[-1],
                     exchange_id=kwargs["symbol"].split(".")[0], **kwargs)
        self.orders[identifier] = order
        return order

    def cancel_order(self, order):
        self.cancelled.append(order["order_id"]); order["status"] = "FINISHED"

    def wait_update(self, deadline):
        threading.Event().wait(.005)

    def close(self):
        self.closed = True


def draft(symbol="DCE.jm2612", **kwargs):
    return dict(symbol=symbol, direction="BUY", offset="OPEN", volume=1, limit_price=100, **kwargs)


class TradingGateTests(unittest.TestCase):
    def gate(self, mode="sim", policy=None):
        api = TradingApi(); gate = TradeGate(api, mode, policy); gate.watch("KQ.m@DCE.jm")
        return api, gate

    def test_live_login_and_direct_submit_are_locked_by_default(self):
        api, gate = self.gate("live")
        with self.assertRaisesRegex(ValueError, "未解锁"):
            gate.prepare(draft())
        with self.assertRaises(ValueError):
            gate.submit("invented-token")
        self.assertEqual(api.sent, [])

    def test_main_is_resolved_to_actual_contract_and_review_is_immutable(self):
        api, gate = self.gate()
        original = draft(); proposal = gate.prepare(original)
        original["volume"] = 999
        gate.submit(proposal["token"])
        self.assertEqual(api.sent[0]["symbol"], "DCE.jm2612")
        self.assertEqual(api.sent[0]["volume"], 1)

    def test_live_armed_review_is_single_use_and_locked_again_on_rearm(self):
        api, gate = self.gate("live"); gate.arm(True)
        proposal = gate.prepare(draft())
        gate.submit(proposal["token"])
        with self.assertRaises(ValueError):gate.submit(proposal["token"])
        self.assertEqual(len(api.sent), 1)
        another = gate.prepare(draft()); gate.arm(False)
        with self.assertRaises(ValueError):gate.submit(another["token"])
        self.assertEqual(len(api.sent), 1)

    def test_review_expires_and_revalidates_quote_before_send(self):
        api, gate = self.gate()
        proposal = gate.prepare(draft(), clock=100)
        with self.assertRaisesRegex(ValueError, "20秒"):
            gate.submit(proposal["token"], clock=121)
        proposal = gate.prepare(draft())
        api.quote["datetime"] = "2016-01-01 10:00"
        with self.assertRaisesRegex(ValueError, "报价"):
            gate.submit(proposal["token"])
        self.assertEqual(api.sent, [])

    def test_failed_send_is_consumed_and_never_automatically_retried(self):
        api, gate = self.gate(); proposal = gate.prepare(draft())
        calls = []
        def uncertain(**kwargs):
            calls.append(kwargs)
            raise RuntimeError("send result uncertain")
        api.insert_order = uncertain
        with self.assertRaises(RuntimeError):gate.submit(proposal["token"])
        with self.assertRaises(ValueError):gate.submit(proposal["token"])
        self.assertEqual(len(calls), 1)

    def test_invalid_prices_limits_lot_cap_and_notional_cap_are_rejected(self):
        api, gate = self.gate(policy=TradePolicy(max_lots=2, max_notional=10000))
        for changes in (dict(limit_price=100.1), dict(limit_price=float("nan")),
                        dict(volume=3), dict(volume=True), dict(limit_price=125),
                        dict(volume=2)):
            order = draft(); order.update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                gate.prepare(order)
        self.assertEqual(api.sent, [])

    def test_changing_contract_invalidates_review_and_main_cannot_be_sent(self):
        api, gate = self.gate()
        proposal = gate.prepare(draft())
        gate.watch("SHFE.ag2612")
        with self.assertRaises(ValueError):gate.submit(proposal["token"])
        with self.assertRaises(ValueError):gate.prepare(draft("KQ.m@DCE.jm"))

    def test_exchange_close_today_history_and_frozen_position_validation(self):
        api, gate = self.gate(); gate.watch("SHFE.ag2612")
        order = draft("SHFE.ag2612"); order.update(direction="SELL", offset="CLOSETODAY", volume=3)
        with self.assertRaisesRegex(ValueError, "可平手数不足"):gate.prepare(order)
        order["volume"] = 2
        gate.submit(gate.prepare(order)["token"])
        self.assertEqual(api.sent[0]["offset"], "CLOSETODAY")
        order["offset"] = "CLOSE"; order["volume"] = 4
        with self.assertRaises(ValueError):gate.prepare(order)
        api.position["volume_long_frozen_his"] = 3
        order["volume"] = 1
        with self.assertRaises(ValueError):gate.prepare(order)

    def test_stale_quote_expired_contract_and_nonfuture_are_rejected(self):
        for changes in (dict(datetime="2016-01-01"), dict(expired=True), dict(ins_class="OPTION")):
            api, gate = self.gate(); api.quote.update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):gate.prepare(draft())

    def test_cancellation_is_available_while_live_is_locked(self):
        api, gate = self.gate("live")
        api.orders["existing"] = dict(order_id="existing", status="ALIVE")
        gate.cancel("existing")
        self.assertEqual(api.cancelled, ["existing"])
        self.assertEqual(api.sent, [])

    def test_real_worker_login_never_sends_orders_without_commands(self):
        api = TradingApi(); stopped = threading.Event(); events = []
        def publish(kind, **kwargs):
            events.append(dict(kind=kind, **kwargs))
            if kind == "trade_snapshot":
                stopped.set()
        serve_trade(dict(mode="live", username="offline-user", password="offline-pass",
            watch=dict(symbol="KQ.m@DCE.jm", revision=1)),
            queue.Queue(), stopped, api_factory=lambda *args:api, publish=publish)
        self.assertEqual(api.sent, [])
        self.assertTrue(api.closed)
        result = [x for x in events if x["kind"] == "trade_snapshot"][-1]
        self.assertFalse(result["armed"])
        self.assertEqual(result["mode"], "live")

    def test_broker_credentials_are_redacted_on_login_failure(self):
        events = []
        def fail(*args):
            raise ValueError('fund-user password="fund-pass" offline-user offline-pass')
        serve_trade(dict(mode="live", username="offline-user", password="offline-pass",
                         broker_account="fund-user", broker_password="fund-pass"),
                    queue.Queue(), threading.Event(), api_factory=fail,
                    publish=lambda kind, **kw:events.append(dict(kind=kind, **kw)))
        text = events[-1]["text"]
        for secret in ("fund-user", "fund-pass", "offline-user", "offline-pass"):
            self.assertNotIn(secret, text)


if __name__ == "__main__":
    unittest.main()
