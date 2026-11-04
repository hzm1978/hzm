from dataclasses import asdict
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
import pandas as pd

from tq_workbench.backtest import ma_cross
from tq_workbench.bars import (aggregate_calendar, from_sdk, merge_bars, records,
                               trading_days, validate_frame)
from tq_workbench.paper import Contract, PaperAccount
from tq_workbench.replay import Replay
from tq_workbench.storage import Store


def frame(dates=None, prices=None):
    dates = dates if dates is not None else pd.bdate_range("2026-10-01", periods=30)
    prices = np.asarray(prices if prices is not None else np.arange(len(dates))+100., dtype=float)
    return validate_frame(pd.DataFrame(dict(datetime=dates, open=prices,
        high=prices+2, low=prices-2, close=prices+1, volume=100, open_interest=300, closed=True)))


def sdk_frame(times):
    values = pd.DatetimeIndex(times).tz_localize("Asia/Shanghai").tz_convert("UTC").as_unit("ns").asi8.copy()
    return pd.DataFrame(dict(datetime=values, id=np.arange(len(values)),
        open=100., high=104., low=99., close=102., volume=200, close_oi=300))


class BarTests(unittest.TestCase):
    def test_sdk_nan_padding_and_readonly_source_are_detached(self):
        raw = sdk_frame(["2026-11-04 09:00", "2026-11-04 09:01"])
        raw.loc[0, "id"] = -1
        raw["datetime"].to_numpy().flags.writeable = False
        before = raw.copy(deep=True)
        result = from_sdk(raw, 60, received_at="2026-11-04 09:01:30")
        self.assertEqual(len(result), 1)
        self.assertFalse(result.iloc[-1]["closed"])
        self.assertEqual(result.iloc[-1]["datetime"], pd.Timestamp("2026-11-04 09:01"))
        pd.testing.assert_frame_equal(raw, before)

    def test_night_daily_label_can_be_the_next_trading_day(self):
        raw = sdk_frame(["2026-11-02 00:00"])
        result = from_sdk(raw, 86400, received_at="2026-10-30 21:30")
        self.assertEqual(len(result), 1)
        self.assertFalse(result.iloc[0]["closed"])
        self.assertEqual(result.iloc[0]["trading_day"], "2026-11-02")

    def test_future_intraday_record_is_excluded(self):
        raw = sdk_frame(["2026-11-04 09:00", "2026-11-04 09:01"])
        result = from_sdk(raw, 60, received_at="2026-11-04 09:00:30")
        self.assertEqual(len(result), 1)

    def test_day_close_uses_exchange_session_end(self):
        raw = sdk_frame(["2026-11-04"])
        quote = dict(trading_time={"day": [["09:30:00", "15:15:00"]]})
        self.assertFalse(from_sdk(raw, 86400, quote, "2026-11-04 15:05").iloc[0]["closed"])
        self.assertTrue(from_sdk(raw, 86400, quote, "2026-11-04 15:16").iloc[0]["closed"])

    def test_invalid_ohlc_and_nan_are_rejected(self):
        raw = frame().iloc[:4].copy()
        raw.loc[0, "high"] = raw.loc[0, "low"]-1
        raw.loc[1, "close"] = float("nan")
        raw.loc[2, "datetime"] = pd.NaT
        self.assertEqual(len(validate_frame(raw)), 1)

    def test_latest_correction_wins_without_losing_old_history(self):
        older = frame()
        corrected = older.tail(2).copy()
        corrected["volume"] = 999
        merged = merge_bars(older, corrected)
        self.assertEqual(len(merged), len(older))
        self.assertEqual(merged.iloc[-1]["volume"], 999)
        self.assertEqual(merged.iloc[0]["volume"], 100)

    def test_friday_night_and_saturday_early_belong_to_monday(self):
        dates = pd.Series(pd.to_datetime(["2026-10-30 21:00", "2026-10-31 01:00", "2026-11-02 09:00"]))
        self.assertEqual(trading_days(dates).tolist(), ["2026-11-02"]*3)

    def test_exchange_calendar_handles_holiday_gap(self):
        dates = pd.Series(pd.to_datetime(["2026-09-30 21:00", "2026-10-01 01:00"]))
        self.assertEqual(trading_days(dates, ["2026-09-30", "2026-10-09"]).tolist(), ["2026-10-09"]*2)

    def test_owned_records_round_trip_including_partial(self):
        source = frame(); source.loc[len(source)-1, "closed"] = False
        result = validate_frame(pd.DataFrame(json.loads(json.dumps(records(source)))))
        pd.testing.assert_frame_equal(result, source)


class ReplayTests(unittest.TestCase):
    def test_visible_prefix_and_higher_period_cannot_see_future_extreme(self):
        source = frame(pd.to_datetime(["2026-11-02", "2026-11-03", "2026-11-04"]), [100, 110, 9000])
        replay = Replay(); replay.load(source, cursor=1)
        self.assertEqual(len(replay.visible()), 2)
        self.assertLess(replay.companion().high.max(), 1000)
        detached = replay.visible()
        detached.loc[0, "close"] = -1
        self.assertNotEqual(replay.frame.iloc[0]["close"], -1)

    def test_weekly_chart_advances_one_observed_day_without_final_week_lookahead(self):
        source = frame(pd.to_datetime(["2026-10-30", "2026-11-02", "2026-11-03", "2026-11-04"]), [100, 110, 120, 9000])
        replay = Replay(); replay.load(source, "周线", 0)
        replay.cursor = replay.day_index(1)
        self.assertEqual(replay.current["datetime"], pd.Timestamp("2026-11-02"))
        self.assertLess(replay.visible().iloc[-1]["high"], 1000)
        self.assertFalse(replay.visible().iloc[-1]["closed"])
        self.assertEqual(replay.day_index(1), 2)
        self.assertEqual(replay.day_index(-1), 0)

    def test_night_day_navigation_uses_trading_day_not_calendar_midnight(self):
        source = frame(pd.to_datetime(["2026-10-30 14:59", "2026-10-30 21:00", "2026-10-31 01:00", "2026-11-02 09:00"]))
        replay = Replay(); replay.load(source, "1分钟", 0)
        self.assertEqual(replay.day_index(1), 1)
        replay.cursor = 1
        self.assertEqual(replay.day_index(-1), 0)

    def test_intraday_companion_is_forming_until_day_session_end(self):
        source = frame(pd.to_datetime(["2026-11-04 09:00", "2026-11-04 09:01", "2026-11-04 14:59"]))
        replay = Replay(); replay.load(source, "1分钟", 1)
        self.assertFalse(replay.companion().iloc[-1]["closed"])
        replay.step()
        self.assertTrue(replay.companion().iloc[-1]["closed"])

    def test_monthly_replay_keeps_a_daily_underlying_cursor(self):
        source = frame(pd.to_datetime(["2026-10-30", "2026-11-02", "2026-11-03"]))
        replay = Replay(); replay.load(source, "月线", 0)
        replay.step()
        self.assertEqual(replay.current["datetime"], pd.Timestamp("2026-11-02"))
        self.assertEqual(len(replay.visible()), 2)
        self.assertEqual(len(replay.frame), 3)

    def test_intraday_multiperiod_projection_is_causal_and_refuses_finer_fake_bars(self):
        source = frame(pd.date_range("2026-11-04 09:00", periods=12, freq="min"), [100]*10+[9000]*2)
        replay = Replay(); replay.load(source, "1分钟", 6)
        projected = replay.project("5分钟")
        self.assertEqual(len(projected), 2)
        self.assertLess(projected.high.max(), 1000)
        self.assertTrue(projected.iloc[0]["closed"])
        self.assertFalse(projected.iloc[1]["closed"])
        replay.load(source, "5分钟", 6)
        with self.assertRaises(ValueError):replay.project("1分钟")


class PaperTests(unittest.TestCase):
    def book(self, capital=100000):
        return PaperAccount(capital, Contract(multiplier=10, tick=1, margin_rate=.1, fee_per_lot=2, slippage_ticks=0))

    def test_round_trip_accounts_for_multiplier_and_both_fees(self):
        book = self.book()
        book.execute("开多", 2, 100, "2026-11-02")
        book.execute("平多", 2, 110, "2026-11-03")
        self.assertEqual(book.balance, 100192)
        self.assertEqual(book.qty, 0)

    def test_short_pnl_and_weighted_scaling(self):
        book = self.book()
        book.execute("开空", 1, 100, "2026-11-02")
        book.execute("开空", 1, 110, "2026-11-02")
        self.assertEqual(book.average, 105)
        self.assertEqual(book.unrealized(95), 200)
        book.execute("平空", 2, 95, "2026-11-03")
        self.assertEqual(book.balance, 100192)

    def test_rejected_open_is_atomic(self):
        book = self.book(100)
        before = book.to_dict()
        with self.assertRaisesRegex(ValueError, "资金不足"):
            book.execute("开多", 100, 100, "2026-11-02")
        self.assertEqual(book.to_dict(), before)

    def test_limit_created_at_close_cannot_fill_the_same_bar(self):
        book = self.book()
        book.submit_limit("开多", 1, 100, "2026-11-02 09:00:00")
        book.on_bar(dict(datetime="2026-11-02 09:00:00", open=101, high=105, low=90, close=103))
        self.assertEqual(book.qty, 0)
        book.on_bar(dict(datetime="2026-11-02 09:01:00", open=95, high=100, low=94, close=99))
        self.assertEqual(book.fills[0]["price"], 95)

    def test_limit_price_cap_respects_adverse_slippage(self):
        book = self.book(); book.contract.slippage_ticks = 4
        book.submit_limit("开多", 1, 100, "2026-11-02")
        book.on_bar(dict(datetime="2026-11-03", open=99, high=105, low=95, close=103))
        self.assertEqual(book.fills[0]["price"], 100)

    def test_simultaneous_tp_sl_uses_stop_and_gap_uses_worse_open(self):
        book = self.book()
        book.execute("开多", 1, 100, "2026-11-02"); book.set_bracket(110, 90)
        book.on_bar(dict(datetime="2026-11-03", open=85, high=120, low=80, close=115))
        self.assertEqual(book.fills[-1]["reason"], "止损")
        self.assertEqual(book.fills[-1]["price"], 85)

    def test_cancelled_limit_does_not_fill(self):
        book = self.book()
        order = book.submit_limit("开空", 1, 100, "2026-11-02"); book.cancel(order["id"])
        book.on_bar(dict(datetime="2026-11-03", open=110, high=120, low=100, close=115))
        self.assertEqual(book.qty, 0)

    def test_duplicate_bar_does_not_repeat_executions(self):
        book = self.book(); book.submit_limit("开多", 1, 100, "2026-11-02")
        row = dict(datetime="2026-11-03", open=100, high=105, low=95, close=102)
        book.on_bar(row); count = len(book.fills); book.on_bar(row)
        self.assertEqual(len(book.fills), count)

    def test_opposite_position_and_invalid_tick_or_bracket_are_rejected(self):
        book = self.book(); book.execute("开多", 1, 100, "2026-11-02")
        with self.assertRaises(ValueError):book.execute("开空", 1, 100, "2026-11-02")
        with self.assertRaises(ValueError):book.submit_limit("开多", 1, 100.5, "2026-11-02")
        with self.assertRaises(ValueError):book.set_bracket(90, 110)

    def test_snapshot_is_owned_and_roundtrips(self):
        book = self.book(); book.execute("开多", 1, 100, "2026-11-02")
        state = book.to_dict(); restored = PaperAccount.from_dict(state)
        restored.fills[0]["price"] = -1
        self.assertEqual(book.fills[0]["price"], 100)
        self.assertEqual(state["fills"][0]["price"], 100)


class StorageTests(unittest.TestCase):
    def test_cache_merges_by_symbol_period_and_timestamp(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory)/"studio.db")
            source = frame()
            store.upsert_bars("A", 60, source); store.upsert_bars("B", 60, source.tail(2))
            fixed = source.tail(1).copy(); fixed["volume"] = 888
            store.upsert_bars("A", 60, fixed)
            result = store.load_bars("A", 60)
            self.assertEqual(len(result), len(source)); self.assertEqual(result.iloc[-1]["volume"], 888)
            self.assertEqual(len(store.load_bars("B", 60)), 2)
            self.assertTrue(store.load_bars("A", 86400).empty)

    def test_training_snapshot_is_not_changed_by_market_cache_correction(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory)/"studio.db"); source = frame()
            identifier = store.new_session("A", "日线", source, 4, {"account": PaperAccount().to_dict()})
            corrected = source.copy(); corrected["volume"] = 999
            store.upsert_bars("A", 86400, corrected)
            result = store.load_session(identifier)
            pd.testing.assert_frame_equal(result["frame"], source)
            self.assertEqual(result["cursor"], 4)
            store.save_session(identifier, 8, {"account": PaperAccount(50000).to_dict()})
            self.assertEqual(store.load_session(identifier)["state"]["account"]["initial"], 50000)

    def test_catalog_and_settings_preserve_unicode(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory)/"studio.db")
            store.save_catalog([dict(symbol="KQ.m@SHFE.ag", name="沪银")])
            store.set_setting("ui", dict(volume=False, period="日线"))
            self.assertEqual(store.load_catalog()[0]["name"], "沪银")
            self.assertFalse(store.setting("ui")["volume"])

    def test_consistent_workspace_backup_contains_history_and_training_without_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory)/"studio.db")
            source = frame(); book = PaperAccount()
            identifier = store.new_session("A", "日线", source, 4, {"account":book.to_dict()})
            store.upsert_bars("A", 86400, source)
            destination = Path(directory)/"portable.sqlite3"
            store.backup(destination)
            backup = Store(destination)
            self.assertEqual(len(backup.load_bars("A", 86400)), len(source))
            self.assertEqual(backup.load_session(identifier)["cursor"], 4)
            self.assertIsNone(backup.setting("password"))
            with self.assertRaises(ValueError):store.backup(store.path)


class BacktestTests(unittest.TestCase):
    def test_execution_uses_the_open_after_a_closed_signal(self):
        prices = [10, 10, 10, 8, 7, 10, 15, 16, 10, 6, 5, 10]
        source = frame(pd.bdate_range("2026-10-01", periods=len(prices)), prices)
        source["close"] = prices
        result = ma_cross(source, 2, 3, contract=Contract(tick=1, slippage_ticks=0))
        short = source.close.rolling(2).mean(); long = source.close.rolling(3).mean()
        first = result["fills"][0]
        index = int(source.datetime.searchsorted(pd.Timestamp(first["timestamp"])))
        self.assertEqual(first["price"], source.iloc[index]["open"])
        self.assertGreater(index, 0)
        if first["action"] == "开多":
            self.assertGreater(short.iloc[index-1], long.iloc[index-1])
            self.assertLessEqual(short.iloc[index-2], long.iloc[index-2])
        else:
            self.assertLess(short.iloc[index-1], long.iloc[index-1])
            self.assertGreaterEqual(short.iloc[index-2], long.iloc[index-2])
        self.assertAlmostEqual(result["profit"], sum(x["pnl"]-x["fee"] for x in result["fills"]))

    def test_forming_bar_is_excluded_and_parameters_validated(self):
        source = frame(); source.loc[len(source)-1, "closed"] = False
        result = ma_cross(source, 2, 5)
        self.assertEqual(result["bars"], len(source)-1)
        with self.assertRaises(ValueError):ma_cross(source, 5, 2)
        with self.assertRaises(ValueError):ma_cross(source.iloc[:2], 2, 5)


if __name__ == "__main__":
    unittest.main()
