from pathlib import Path
import tempfile
import threading
import unittest

import pandas as pd

from tq_workbench.bars import validate_frame
from tq_workbench.history_worker import HistoryCollector, collect_history
from tq_workbench.storage import Store
from test_core import frame, sdk_frame


class Finished(Exception):
    pass


class HistoricalApi:
    def __init__(self):
        self.serial = sdk_frame(["2016-01-05 09:00"])
        self.daily = sdk_frame(["2016-01-05"])
        self.quote = dict(datetime="2016-01-05 09:00:00", volume_multiple=15, price_tick=1)
        self.index = -1; self.closed = False; self.requests = []

    def get_quote(self, symbol):
        return self.quote

    def get_kline_serial(self, symbol, seconds, data_length):
        self.requests.append((symbol, seconds, data_length))
        return self.daily if seconds == 86400 else self.serial

    def is_serial_ready(self, value):
        return True

    def wait_update(self, deadline):
        self.index += 1
        if self.index == 5:
            raise Finished()
        stamp = pd.Timestamp("2016-01-05 09:00")+pd.Timedelta(minutes=self.index)
        row = sdk_frame([stamp]).iloc[0]
        self.serial.loc[self.index] = row
        self.quote["datetime"] = str(stamp)

    def close(self):
        self.closed = True


class HistoryTests(unittest.TestCase):
    def config(self, directory):
        return dict(username="offline-user", password="offline-pass", database=str(Path(directory)/"cache.db"),
                    symbol="KQ.m@SHFE.ag", seconds=60, start="2016-01-05", end="2016-01-05")

    def test_public_history_stream_collects_old_bars_and_flushes_last_bar_on_finish(self):
        with tempfile.TemporaryDirectory() as directory:
            api = HistoricalApi(); messages = []; config = self.config(directory)
            collect_history(config, threading.Event(), api_factory=lambda *args:api,
                            finished_exception=Finished, publish=lambda kind, **kw:messages.append(dict(kind=kind, **kw)))
            result = Store(config["database"]).load_bars(config["symbol"], 60)
            self.assertEqual(len(result), 5)
            self.assertTrue(result.closed.all())
            self.assertEqual(result.datetime.iloc[-1], pd.Timestamp("2016-01-05 09:04"))
            complete = [m for m in messages if m["kind"] == "complete"][-1]
            self.assertEqual(complete["count"], 5)
            self.assertTrue(api.closed)
            self.assertTrue(all(request[2] == 300 for request in api.requests))

    def test_cancel_flushes_completed_prefix_without_future_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            api = HistoricalApi(); stopped = threading.Event(); messages = []; config = self.config(directory)
            def publish(kind, **kw):
                messages.append(dict(kind=kind, **kw))
                if kind == "progress" and kw["count"] >= 1:
                    stopped.set()
            # Force a progress callback after every observed bar for this case.
            original = HistoryCollector.accept
            from unittest.mock import patch
            def frequent(self, frame, **kwargs):
                return original(self, frame, force=True)
            with patch.object(HistoryCollector, "accept", new=frequent):
                collect_history(config, stopped, api_factory=lambda *args:api, publish=publish, finished_exception=Finished)
            result = Store(config["database"]).load_bars(config["symbol"], 60)
            self.assertEqual(len(result), 1)
            self.assertTrue(messages[-1]["cancelled"])

    def test_collector_filters_warmup_outside_range_and_incomplete_bars(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory)/"cache.db")
            collector = HistoryCollector(store, "A", 86400, "2016-01-05", "2016-01-06", lambda *args, **kw:None)
            source = frame(pd.to_datetime(["2016-01-04", "2016-01-05", "2016-01-06", "2016-01-07"]))
            source.loc[2, "closed"] = False
            collector.accept(source, force=True)
            self.assertEqual(len(store.load_bars("A", 86400)), 1)
            self.assertEqual(collector.count, 1)
            collector.accept(source, force=True)
            self.assertEqual(collector.count, 1)

    def test_history_authentication_failure_is_reported_without_leaking_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            messages = []
            def fail(*args):
                raise ValueError("offline-user offline-pass access_token=private-token")
            collect_history(self.config(directory), threading.Event(), api_factory=fail, finished_exception=Finished,
                            publish=lambda kind, **kw:messages.append(dict(kind=kind, **kw)))
            error = [m["text"] for m in messages if m["kind"] == "error"][-1]
            self.assertIn("ValueError", error)
            self.assertNotIn("offline-pass", error)
            self.assertNotIn("private-token", error)


if __name__ == "__main__":
    unittest.main()
