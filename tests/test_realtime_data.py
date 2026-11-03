import asyncio
from dataclasses import replace
from pathlib import Path
import tempfile
import threading
import time
import types
import unittest
import importlib.util
from unittest.mock import patch

import numpy as np
import pandas as pd

from futures_replay.data_repo import DataRepository
from futures_replay.realtime_data import (LIVE_PERIODS, RealtimeTarget, build_snapshot,
    chart_frame, completed_frame, normalize_serial, day_close_seconds, create_tq_api)


def sdk_serial(times, base=100.):
    return pd.DataFrame({"id": np.arange(len(times)),
        "datetime": [pd.Timestamp(t, tz="Asia/Shanghai").value for t in times],
        "open": base+np.arange(len(times)), "high": base+np.arange(len(times))+2,
        "low": base+np.arange(len(times))-1, "close": base+np.arange(len(times))+1,
        "volume": 10., "open_oi": 50., "close_oi": 51.})


def sdk_fixture():
    minutes = sdk_serial(["2026-10-30 21:00", "2026-10-31 00:00",
        "2026-11-02 09:00", "2026-11-03 10:34", "2026-11-03 10:35"])
    daily = sdk_serial(["2026-10-30", "2026-11-02", "2026-11-03"])
    serials = {key: daily if key == "1d" else minutes.copy() for key in LIVE_PERIODS}
    quote = {"datetime": "2026-11-03 10:35:20.000000", "last_price": 105.,
             "underlying_symbol": "SHFE.sn2612", "trading_time": {"day": [["09:00:00", "15:00:00"]]}}
    return serials, quote


def snapshot():
    serials, quote = sdk_fixture()
    return build_snapshot(serials, quote, "2026-11-03 10:35:20")


class RealtimeDataTests(unittest.TestCase):
    def test_sdk_factory_explicitly_uses_simulator_not_a_brokerage_account(self):
        calls = []
        class Sim: pass
        class Auth:
            def __init__(self, username, password): pass
        module = types.SimpleNamespace(TqSim=Sim, TqAuth=Auth, TqApi=lambda **kw: calls.append(kw))
        loop = asyncio.new_event_loop()
        try:
            with patch.dict("sys.modules", {"tqsdk": module}):
                create_tq_api("local-user", "local-password", loop)
        finally:
            loop.close()
        self.assertIsInstance(calls[0]["account"], Sim)
        self.assertFalse(calls[0]["debug"])
        self.assertFalse(calls[0]["web_gui"])
    @unittest.skipIf(importlib.util.find_spec("tqsdk") is None, "官方SDK未安装到此测试环境")
    def test_real_sdk_block_manager_dataframe_can_be_copied_without_network(self):
        from tqsdk import TqApi
        class NoNetworkApi:
            def create_task(self, coroutine, **kwargs):
                coroutine.close()
                return None
        default = dict(datetime=0., open=float("nan"), high=float("nan"),
            low=float("nan"), close=float("nan"), volume=0., open_oi=0., close_oi=0.)
        serial = TqApi._init_serial(NoNetworkApi(), [{"last_id": 1,
            "_path": ["klines", "KQ.m@SHFE.sn", "60000000000"]}], 2, default, None)
        raw = sdk_serial(["2026-11-03 10:34", "2026-11-03 10:35"])
        for i, key in enumerate(["datetime", "id", *[k for k in default if k != "datetime"]]):
            serial["array"][:, i] = raw[key].to_numpy()
        copied = normalize_serial(serial["df"], "1min")
        serial["array"][-1, 5] = 9999
        self.assertEqual(copied.close.iloc[-1], 102)
        self.assertEqual(str(copied.datetime.iloc[-1]), "2026-11-03 10:35:00")

    def test_timezone_night_calendar_and_sdk_buffer_ownership(self):
        serials, quote = sdk_fixture()
        original = serials["1min"].copy(deep=True)
        snap = build_snapshot(serials, quote, "2026-11-03 02:35:20+00:00")
        frame = snap.frames["1min"]
        self.assertEqual(str(frame.datetime.iloc[-1]), "2026-11-03 10:35:00")
        self.assertEqual(frame.trading_day.iloc[:2].tolist(), ["2026-11-02"]*2)
        self.assertEqual(str(snap.received_at), "2026-11-03 10:35:20")
        pd.testing.assert_frame_equal(serials["1min"], original)
        serials["1min"].loc[4, "close"] = 999
        self.assertEqual(frame.close.iloc[-1], 105)

    def test_warmup_nan_invalid_ohlc_and_weekend_daily_are_not_persistable(self):
        raw = sdk_serial(["2026-10-30", "2026-10-31", "2026-11-02", "2026-11-03"])
        raw.loc[0, "id"] = -1
        raw.loc[3, "high"] = 1
        result = normalize_serial(raw, "1d")
        self.assertEqual(result.trading_day.tolist(), ["2026-11-02"])

    def test_current_minute_and_day_remain_preview_until_their_ends(self):
        snap = snapshot()
        self.assertEqual(str(completed_frame(snap, "1min").datetime.iloc[-1]), "2026-11-03 10:34:00")
        frame, partial = chart_frame(snap, "1min")
        self.assertEqual(partial["datetime"], frame.datetime.iloc[-1])
        self.assertEqual(completed_frame(snap, "1d").trading_day.tolist(), ["2026-10-30", "2026-11-02"])
        later = replace(snap, received_at=pd.Timestamp("2026-11-03 10:36:00"))
        self.assertEqual(len(completed_frame(later, "1min")), 5)
        self.assertIsNone(chart_frame(later, "1min")[1])
        self.assertNotEqual(completed_frame(snap, "1min").datetime.max(), completed_frame(later, "1min").datetime.max())

    def test_bond_day_close_and_future_night_trading_label(self):
        serials, quote = sdk_fixture()
        quote["trading_time"]["day"][-1][1] = "15:15:00"
        self.assertEqual(day_close_seconds(str(quote["trading_time"])), 15*3600+15*60)
        snap = build_snapshot(serials, quote, "2026-11-03 15:10")
        self.assertEqual(len(completed_frame(snap, "1d")), 2)
        self.assertEqual(len(completed_frame(replace(snap, received_at=pd.Timestamp("2026-11-03 15:15")), "1d")), 3)
        # A Monday trading-day candle can already exist on Friday night; it
        # must not become completed history until Monday's daytime close.
        friday = replace(snap, received_at=pd.Timestamp("2026-10-30 21:01"))
        self.assertFalse(completed_frame(friday, "1d").trading_day.eq("2026-11-02").any())


    def test_failed_background_write_keeps_original_parquet_intact(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = DataRepository(Path(directory))
            frame = snapshot().frames["1min"].iloc[:2]
            repo.append_bars("主力连续", "SN", "1min", "主连", frame)
            path = repo._resolve_file("主力连续", "SN", "1min", "主连")
            original = path.read_bytes()
            with patch.object(pd.DataFrame, "to_parquet", side_effect=OSError("模拟磁盘写入失败")):
                with self.assertRaises(OSError):
                    repo.append_bars("主力连续", "SN", "1min", "主连", frame)
            self.assertEqual(path.read_bytes(), original)
            self.assertFalse(list(path.parent.glob("*.tmp")))







if __name__ == "__main__":
    unittest.main()
