import contextlib
import io
import json
import os
from pathlib import Path
import queue
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import pandas as pd
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication

from futures_replay.data_repo import DataRepository
from futures_replay.live_store import write_live_snapshot, read_live_overlay
from futures_replay.live_service import collect, resolve_targets, tq_contract_symbol
from futures_replay.realtime_data import RealtimeTarget, build_snapshot
from futures_replay.ui.background_live import BackgroundLiveController
from futures_replay.ui.live_chart_dialog import LiveChartDialog
from test_realtime_data import snapshot, sdk_fixture, sdk_serial


class LiveStoreTests(unittest.TestCase):
    def test_background_shards_preserve_original_history_and_load_as_one_series(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); repo = DataRepository(root)
            target = RealtimeTarget(root, "主力连续", "SN", "主连", "KQ.m@SHFE.sn")
            old = snapshot().frames["1min"].iloc[:2].copy()
            old.loc[old.index[-1], ["open", "high", "low", "close"]] = [89, 91, 88, 90]
            repo.append_bars(target.source, target.symbol, "1min", target.contract, old)
            original_path = repo._resolve_file(target.source, target.symbol, "1min", target.contract)
            original = original_path.read_bytes()
            fingerprints = {}
            result = write_live_snapshot(target, snapshot(), fingerprints)
            self.assertGreater(result["changed"], 0)
            self.assertEqual(original_path.read_bytes(), original)
            combined = repo.load_minutes(target.source, target.symbol, target.contract)
            self.assertEqual(combined.close.iloc[1], snapshot().frames["1min"].close.iloc[1])
            self.assertEqual(str(combined.datetime.max()), "2026-11-03 10:34:00")
            self.assertFalse(combined.datetime.eq("2026-11-03 10:35").any())
            self.assertEqual(write_live_snapshot(target, snapshot(), fingerprints)["changed"], 0)
            self.assertFalse(list(root.rglob("*.tmp")))

    def test_new_raw_contract_is_discoverable_without_a_base_file(self):
        with tempfile.TemporaryDirectory() as directory:
            target = RealtimeTarget(Path(directory), "原始行情", "AG", "AG2612", "SHFE.ag2612")
            write_live_snapshot(target, snapshot())
            repo = DataRepository(target.root)
            self.assertIn("AG2612", repo.list_contracts("AG", "1min", "原始行情"))
            self.assertIn("AG2612", repo.list_contracts("AG", "1d", "原始行情"))
            self.assertFalse(repo.load_bars("原始行情", "AG", "1d", "AG2612").empty)

    def test_completed_minutes_do_not_create_an_unfinished_five_minute_bar(self):
        with tempfile.TemporaryDirectory() as directory:
            target = RealtimeTarget(Path(directory), "主力连续", "SN", "主连", "KQ.m@SHFE.sn")
            serials, quote = sdk_fixture()
            serials["1min"] = sdk_serial(pd.date_range("2026-11-03 10:30", periods=7, freq="min"))
            snap = build_snapshot(serials, quote, "2026-11-03 10:36:20")
            write_live_snapshot(target, snap)
            stored = read_live_overlay(target.root, target.source, target.symbol, target.contract, "5min")
            self.assertEqual(stored.datetime.tolist(), [pd.Timestamp("2026-11-03 10:30")])


class MultiApi:
    def __init__(self, stopped):
        self.stopped, self.count, self.closed = stopped, 0, False
        self.serials, self.quote = sdk_fixture()
        self.requests = []

    def get_quote(self, symbol):
        product = symbol.split(".")[-1]
        if "@" in symbol:
            product = symbol.split(".")[-1]
        return {**self.quote, "underlying_symbol": "SHFE."+product.lower()+"2612"}

    def get_kline_serial(self, symbol, seconds, data_length):
        self.requests.append((symbol, seconds))
        return self.serials["1d" if seconds == 86400 else "1min"]

    def is_serial_ready(self, frame):
        return True

    def wait_update(self, deadline):
        self.count += 1
        if self.count >= 8:
            self.stopped.set()
        return True

    def close(self):
        self.closed = True


class BackgroundServiceTests(unittest.TestCase):
    def test_all_products_are_collected_with_free_minute_and_daily_subscriptions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for symbol in ("SN", "AG", "XX"):
                meta = root/"qihushuju_parquet"/symbol
                (meta/"1min").mkdir(parents=True); (meta/"1d").mkdir()
                exchange = "未知交易所" if symbol == "XX" else "SHFE"
                (meta/"数据说明.md").write_text(f"# {symbol}\n交易所：{exchange}\n", encoding="utf-8")
            stopped = threading.Event(); api = MultiApi(stopped)
            commands = queue.Queue(); commands.put({"command": "view", "enabled": False})
            output = io.StringIO()
            config = {"root": str(root), "source": "原始行情", "mode": "all",
                      "username": "private-user", "password": "private-password"}
            with patch("futures_replay.live_service.lower_priority"), contextlib.redirect_stdout(output):
                collect(config, api_factory=lambda u,p,l: api, command_queue=commands, stop_event=stopped)
            events = [json.loads(line[7:]) for line in output.getvalue().splitlines() if line.startswith("FRLIVE ")]
            saved = [e for e in events if e["kind"] == "saved"]
            self.assertEqual({e["symbol"] for e in saved}, {"SN", "AG"})
            self.assertTrue(any("XX" in e.get("text", "") for e in events))
            self.assertEqual({seconds for _, seconds in api.requests}, {60, 86400})
            self.assertNotIn("private-password", output.getvalue())
            self.assertTrue(api.closed)
            for file in root.rglob("*.parquet"):
                self.assertNotIn("private-password", file.read_bytes().decode("latin1"))

    def test_symbols_follow_exchange_case_and_czce_year_format(self):
        self.assertEqual(tq_contract_symbol("郑商所", "SR2609"), "CZCE.SR609")
        self.assertEqual(tq_contract_symbol("上期所", "AG2612"), "SHFE.ag2612")
        self.assertEqual(tq_contract_symbol("中金所", "if2612"), "CFFEX.IF2612")


CHILD = '''import json,sys,os
cfg=json.loads(sys.stdin.readline())
print('SDK notice ignored by protocol',flush=True)
def send(kind,**kw): print('FRLIVE '+json.dumps(dict(kind=kind,**kw)),flush=True)
send('status',text='background connected')
send('saved',source=cfg['source'],symbol=cfg['symbol'],contract=cfg['contract'],minute_latest='2026-11-03 10:34:00')
for line in sys.stdin:
 cmd=json.loads(line)
 if cmd.get('command')=='stop': break
send('stopped')
'''


class BackgroundGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def spin(self, predicate, seconds=5):
        end = time.monotonic()+seconds
        while not predicate() and time.monotonic() < end:
            self.app.processEvents(); threading.Event().wait(.005)
        self.assertTrue(predicate())

    def test_subprocess_credentials_use_stdin_and_background_does_not_require_a_chart(self):
        controller = BackgroundLiveController(program=sys.executable, arguments=["-u", "-c", CHILD])
        saved, status = [], []
        controller.saved.connect(saved.append); controller.statusChanged.connect(status.append)
        config = {"mode": "current", "root": "/tmp", "source": "主力连续", "symbol": "SN", "contract": "主连"}
        controller.start(config, "test-user", "private-password")
        try:
            self.spin(lambda: bool(saved))
            self.assertNotEqual(controller.process.processId(), os.getpid())
            self.assertNotIn("private-password", " ".join(controller.process.arguments()))
            self.assertIsNone(controller._payload)
            self.assertNotIn("password", controller.context)
            self.assertEqual(saved[0]["symbol"], "SN")
            controller.stop(); self.spin(lambda: controller.process is None)
        finally:
            controller.shutdown(); controller.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_current_tick_preview_reuses_closed_analysis_and_hiding_disables_only_preview(self):
        controller = BackgroundLiveController()
        commands = []
        controller.request_view = lambda *a, **kw: commands.append((a, kw))
        view = LiveChartDialog(controller)
        target = RealtimeTarget(Path("/tmp"), "主力连续", "SN", "主连", "KQ.m@SHFE.sn")
        try:
            view.configure(target)
            snap = snapshot(); frame = snap.frames["1min"]
            payload = {"tq_symbol": target.tq_symbol, "period": "1min",
                "bars": json.loads(frame.to_json(orient="records", date_format="iso")),
                "received_at": "2026-11-03 10:35:20", "quote_time": "2026-11-03 10:35:20", "last_price": 105}
            view._preview(payload)
            original = id(view.chart.df)
            payload["bars"][-1]["high"] = 999
            view._preview(payload)
            self.assertEqual(id(view.chart.df), original)
            self.assertEqual(view.chart.partial["high"], 999)
            view.close(); self.app.processEvents()
            self.assertFalse(commands[-1][1]["enabled"])
            self.assertIsNone(controller.process)
        finally:
            view.deleteLater(); controller.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_real_time_preview_does_not_let_current_minute_ohlc_enter_formal_subwaves(self):
        from test_subwave_hierarchy import daily_five
        controller = BackgroundLiveController()
        view = LiveChartDialog(controller)
        target = RealtimeTarget(Path("/tmp"), "主力连续", "SN", "主连", "KQ.m@SHFE.sn")
        try:
            view.configure(target)
            frame = daily_five()
            frame["datetime"] = pd.date_range("2026-11-03 09:00", periods=len(frame), freq="min")
            frame["trading_day"] = "2026-11-03"
            payload = {"tq_symbol": target.tq_symbol, "period": "1min",
                "bars": json.loads(frame.to_json(orient="records", date_format="iso")),
                "received_at": "2026-11-03 10:30:20", "quote_time": "2026-11-03 10:30:20"}
            view._preview(payload)
            before = view.chart._trend_analysis_cached(len(frame))
            self.assertTrue(before.subwaves)
            payload["bars"][-1].update(high=9999, low=1, close=9990)
            view._preview(payload)
            self.assertEqual(before, view.chart._trend_analysis_cached(len(frame)))
            self.assertEqual(before.as_of_index, len(frame)-2)
        finally:
            view.close(); view.deleteLater(); controller.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


if __name__ == "__main__":
    unittest.main()
