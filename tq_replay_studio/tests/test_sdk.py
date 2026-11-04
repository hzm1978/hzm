import queue
from pathlib import Path
import tempfile
import threading
import unittest

import pandas as pd

from tq_workbench.sdk_worker import MarketPump, drain, read_catalog, redact, serve
from tq_workbench.storage import Store
from test_core import sdk_frame


class FakeApi:
    def __init__(self):
        self.calls = []
        self.streams = {}
        self.ready = set()
        self.quotes = {}
        self.closed = False

    def get_quote(self, symbol):
        self.calls.append(("quote", symbol))
        return self.quotes.setdefault(symbol, dict(datetime="2026-11-04 10:00:30", last_price=102.,
            volume_multiple=60, price_tick=.5, underlying_symbol="DCE.jm2612"))

    def get_kline_serial(self, symbol, seconds, data_length=200):
        self.calls.append(("serial", symbol, seconds, data_length))
        value = sdk_frame(["2026-11-04 09:59", "2026-11-04 10:00"]) if seconds != 86400 else sdk_frame(["2026-11-03", "2026-11-04"])
        self.streams[(symbol, seconds)] = value
        self.ready.add(id(value))
        return value

    def is_serial_ready(self, frame):
        return id(frame) in self.ready

    def wait_update(self, deadline):
        self.calls.append(("wait", deadline))
        threading.Event().wait(.005)
        return True

    def close(self):
        self.closed = True

    def query_quotes(self, **kwargs):
        self.calls.append(("catalog", kwargs))
        return ["KQ.m@DCE.jm", "DCE.jm2612"]

    def query_symbol_info(self, symbols):
        return pd.DataFrame([dict(instrument_id=symbol, instrument_name="焦煤",
            exchange_id="DCE", ins_class="CONT" if symbol.startswith("KQ") else "FUTURE",
            volume_multiple=60, price_tick=.5, underlying_symbol="DCE.jm2612") for symbol in symbols])


class SdkTests(unittest.TestCase):
    def test_public_catalog_queries_six_exchanges_and_metadata(self):
        api = FakeApi(); result = read_catalog(api)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["multiplier"], 60)
        self.assertEqual(api.calls[0][1]["expired"], False)
        self.assertIn("GFEX", api.calls[0][1]["exchange_id"])

    def test_all_requested_periods_use_10000_and_share_the_daily_calendar(self):
        with tempfile.TemporaryDirectory() as directory:
            api = FakeApi(); messages = []
            pump = MarketPump(api, Store(Path(directory)/"cache.db"), lambda kind, **kw:messages.append(dict(kind=kind, **kw)))
            pump.watch(dict(symbol="KQ.m@DCE.jm", seconds=[60, 300], revision=4))
            serial_calls = [call for call in api.calls if call[0] == "serial"]
            self.assertEqual({call[2] for call in serial_calls}, {60, 300, 86400})
            self.assertTrue(all(call[3] == 10000 for call in serial_calls))
            pump.tick(clock=10)
            bars = [m for m in messages if m["kind"] == "bars"]
            self.assertEqual({m["seconds"] for m in bars}, {60, 300, 86400})
            self.assertTrue(all(m["revision"] == 4 for m in bars))

    def test_ready_period_is_emitted_even_if_another_period_is_pending(self):
        with tempfile.TemporaryDirectory() as directory:
            api = FakeApi(); messages = []
            pump = MarketPump(api, Store(Path(directory)/"cache.db"), lambda kind, **kw:messages.append(dict(kind=kind, **kw)))
            pump.watch(dict(symbol="KQ.m@DCE.jm", seconds=[60], revision=1))
            api.ready.remove(id(api.streams[("KQ.m@DCE.jm", 86400)]))
            pump.tick(clock=10)
            self.assertEqual([m["seconds"] for m in messages if m["kind"] == "bars"], [60])

    def test_updates_are_saved_and_unchanged_kline_is_not_retransmitted(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory)/"cache.db"); api = FakeApi(); messages = []
            pump = MarketPump(api, store, lambda kind, **kw:messages.append(dict(kind=kind, **kw)))
            pump.watch(dict(symbol="KQ.m@DCE.jm", seconds=[60], revision=1))
            pump.tick(clock=10); initial = len([m for m in messages if m["kind"] == "bars"])
            pump.tick(clock=12)
            self.assertEqual(len([m for m in messages if m["kind"] == "bars"]), initial)
            serial = api.streams[("KQ.m@DCE.jm", 60)]
            serial.loc[len(serial)-1, "volume"] = 999
            pump.tick(clock=14)
            last = [m for m in messages if m["kind"] == "bars"][-1]
            self.assertFalse(last["full"])
            self.assertEqual(store.load_bars("KQ.m@DCE.jm", 60).iloc[-1]["volume"], 999)

    def test_reselect_reuses_public_sdk_subscription_and_changes_revision(self):
        with tempfile.TemporaryDirectory() as directory:
            api = FakeApi(); messages = []
            pump = MarketPump(api, Store(Path(directory)/"cache.db"), lambda kind, **kw:messages.append(dict(kind=kind, **kw)))
            pump.watch(dict(symbol="KQ.m@DCE.jm", seconds=[60], revision=1)); pump.tick(clock=10)
            count = len([c for c in api.calls if c[0] == "serial"])
            pump.watch(dict(symbol="KQ.m@DCE.jm", seconds=[60], revision=2)); pump.tick(clock=12)
            self.assertEqual(len([c for c in api.calls if c[0] == "serial"]), count)
            self.assertEqual([m for m in messages if m["kind"] == "bars"][-1]["revision"], 2)

    def test_reconnect_gap_and_old_row_correction_are_not_limited_to_the_last_four_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            api = FakeApi(); store = Store(Path(directory)/"cache.db"); messages = []
            pump = MarketPump(api, store, lambda kind, **kw:messages.append(dict(kind=kind, **kw)))
            pump.watch(dict(symbol="KQ.m@DCE.jm", seconds=[60], revision=1)); pump.tick(clock=10)
            serial = api.streams[("KQ.m@DCE.jm", 60)]
            replacement = sdk_frame(pd.date_range("2026-11-04 09:59", periods=15, freq="min"))
            for col in serial.columns:
                serial[col] = replacement[col]
            # Assigning longer Series preserves the current index; explicitly
            # append the newly observed native rows as an SDK rolling buffer does.
            api.ready.discard(id(serial))
            api.streams[("KQ.m@DCE.jm", 60)] = replacement
            pump.active["streams"][60] = replacement
            api.ready.add(id(replacement))
            pump.tick(clock=12)
            self.assertEqual(len(store.load_bars("KQ.m@DCE.jm", 60)), 15)
            replacement.loc[0, "volume"] = 777
            pump.tick(clock=14)
            self.assertEqual(store.load_bars("KQ.m@DCE.jm", 60).iloc[0]["volume"], 777)

    def test_latest_selection_is_coalesced_before_initial_subscription_and_close(self):
        with tempfile.TemporaryDirectory() as directory:
            api = FakeApi(); stopped = threading.Event(); commands = queue.Queue()
            commands.put(dict(command="watch", symbol="KQ.m@SHFE.ag", seconds=[60], revision=2))
            commands.put(dict(command="watch", symbol="KQ.m@DCE.jm", seconds=[60], revision=3))
            def publish(kind, **payload):
                if kind == "heartbeat":
                    stopped.set()
            config = dict(username="offline-user", password="offline-pass", database=str(Path(directory)/"cache.db"),
                          watch=dict(command="watch", symbol="OLD", seconds=[86400], revision=1))
            serve(config, commands, stopped, api_factory=lambda *args:api, publish=publish)
            self.assertTrue(api.closed)
            symbols = {c[1] for c in api.calls if c[0] == "serial"}
            self.assertEqual(symbols, {"KQ.m@DCE.jm"})

    def test_failure_diagnostics_hide_credentials_and_tokens(self):
        with tempfile.TemporaryDirectory() as directory:
            messages = []
            def factory(*args):
                raise ValueError('offline-user password="offline-pass" Authorization: Bearer secret-token')
            serve(dict(username="offline-user", password="offline-pass", database=str(Path(directory)/"cache.db")),
                  queue.Queue(), threading.Event(), api_factory=factory,
                  publish=lambda kind, **kw:messages.append(dict(kind=kind, **kw)))
            text = messages[-1]["text"]
            for secret in ("offline-user", "offline-pass", "secret-token"):
                self.assertNotIn(secret, text)
            self.assertIn("ValueError", text)


if __name__ == "__main__":
    unittest.main()
