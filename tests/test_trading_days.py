from contextlib import nullcontext
from datetime import date, datetime
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

from futures_replay.data_repo import DataRepository
from futures_replay.edb_update import assign_trading_day, fetch_edb_bundle, persist_edb_bundle
from futures_replay.history_update import _map_trading_day
from futures_replay.online_update import _assign_trading_day
from futures_replay.replay import ReplayEngine, locate_bar_index
from futures_replay.trading_calendar import session_trading_days


def bars(timestamps, trading_days=None, prices=None):
    prices = list(prices) if prices is not None else list(range(100, 100 + len(timestamps)))
    frame = pd.DataFrame({
        "datetime": pd.to_datetime(timestamps),
        "open": prices, "high": [p + 2 for p in prices],
        "low": [p - 1 for p in prices], "close": [p + 1 for p in prices],
        "volume": 10.0, "amount": 100.0, "open_interest": 50.0,
    })
    if trading_days is not None:
        frame["trading_day"] = trading_days
    return DataRepository.standardize(frame)


class TradingDayRepairTests(unittest.TestCase):
    def test_friday_night_and_saturday_morning_with_stale_daily_calendar(self):
        minutes = bars(["2026-10-30 14:59", "2026-10-30 21:00", "2026-10-31 00:30", "2026-11-02 09:00"])
        daily = bars(["2026-10-30"], ["2026-10-30"])
        repaired = DataRepository._repair_inferred_trading_day(minutes, daily)
        self.assertEqual(repaired.trading_day.tolist(), ["2026-10-30"] + ["2026-11-02"] * 3)
        merged = DataRepository._merge_daily_prefer_native(daily, DataRepository._aggregate_daily_minutes(repaired))
        self.assertEqual(merged.trading_day.tolist(), ["2026-10-30", "2026-11-02"])
        monday = merged.iloc[-1]
        self.assertEqual((monday.open, monday.high, monday.low, monday.close), (101, 105, 100, 104))
        self.assertEqual(monday.volume, 30)
        self.assertEqual(repaired.attrs["trading_day_unrepaired_count"], 0)

    def test_repair_without_daily_calendar(self):
        minutes = bars(["2026-10-30 21:00", "2026-10-31 00:30"])
        for daily in (None, pd.DataFrame()):
            with self.subTest(daily_empty=daily is None):
                repaired = DataRepository._repair_inferred_trading_day(minutes, daily)
                self.assertEqual(repaired.trading_day.tolist(), ["2026-11-02"] * 2)

    def test_explicit_weekend_labels_are_repaired(self):
        minutes = bars(["2026-10-30 21:00", "2026-10-31 00:30"], ["2026-10-31"] * 2)
        daily = bars(["2026-10-30", "2026-10-31"], ["2026-10-30", "2026-10-31"])
        mask = DataRepository._trading_day_repair_mask(minutes)
        self.assertTrue(mask.all())
        repaired = DataRepository._repair_inferred_trading_day(minutes, daily, mask)
        self.assertEqual(repaired.trading_day.tolist(), ["2026-11-02"] * 2)

    def test_known_exchange_holiday_overrides_weekday_fallback(self):
        minutes = bars(["2026-09-30 21:00", "2026-10-01 00:30"])
        daily = bars(["2026-09-30", "2026-10-08"], ["2026-09-30", "2026-10-08"])
        repaired = DataRepository._repair_inferred_trading_day(minutes, daily)
        self.assertEqual(repaired.trading_day.tolist(), ["2026-10-08"] * 2)

    def test_correct_explicit_labels_and_real_timestamps_stay_unchanged(self):
        minutes = bars(["2026-10-30 21:00", "2026-10-31 00:30", "2026-11-03 00:30"],
                       ["2026-11-02", "2026-11-02", "2026-11-03"])
        repaired = DataRepository._repair_inferred_trading_day(minutes, None)
        pd.testing.assert_frame_equal(minutes, repaired)
        self.assertEqual(repaired.attrs["trading_day_unrepaired_count"], 0)

    def test_native_daily_wins_and_weekend_daily_is_excluded(self):
        native = bars(["2026-10-30", "2026-10-31", "2026-11-02"],
                      ["2026-10-30", "2026-10-31", "2026-11-02"], [200, 999, 300])
        rebuilt = bars(["2026-10-30", "2026-10-31", "2026-11-02"],
                       ["2026-10-30", "2026-10-31", "2026-11-02"])
        merged = DataRepository._merge_daily_prefer_native(native, rebuilt)
        self.assertEqual(merged.trading_day.tolist(), ["2026-10-30", "2026-11-02"])
        self.assertEqual(merged.open.tolist(), [200, 300])
        for left, right in ((native, pd.DataFrame()), (pd.DataFrame(), rebuilt)):
            with self.subTest(native_empty=left.empty):
                result = DataRepository._merge_daily_prefer_native(left, right)
                self.assertEqual(result.trading_day.tolist(), ["2026-10-30", "2026-11-02"])


class UpdateTradingDayTests(unittest.TestCase):
    def test_update_with_copy_on_write_read_only_views(self):
        scope = pd.option_context("mode.copy_on_write", True) if int(pd.__version__.split(".")[0]) < 3 else nullcontext()
        with scope:
            minutes = bars(["2026-10-30 21:00", "2026-10-31 00:30", "2026-11-02 09:00"])
            before = minutes.copy(deep=True)
            daily = bars(["2026-10-30"], ["2026-10-30"])
            expected = ["2026-11-02"] * 3
            self.assertEqual(session_trading_days(minutes.datetime).tolist(), expected)
            self.assertEqual(assign_trading_day(minutes, daily).trading_day.tolist(), expected)
            self.assertEqual(_assign_trading_day(minutes, daily.rename(columns={"datetime": "date"})).tolist(), expected)
            self.assertEqual(DataRepository._repair_inferred_trading_day(minutes, daily).trading_day.tolist(), expected)
            pd.testing.assert_frame_equal(minutes, before)

    def test_all_update_paths_skip_weekend_when_daily_series_is_stale(self):
        minutes = bars(["2026-10-30 21:00", "2026-10-31 00:30"])
        daily = bars(["2026-10-30"], ["2026-10-30"])
        expected = ["2026-11-02"] * 2
        self.assertEqual(assign_trading_day(minutes, daily).trading_day.tolist(), expected)
        self.assertEqual(_assign_trading_day(minutes, daily.rename(columns={"datetime": "date"})).tolist(), expected)
        self.assertEqual([_map_trading_day(ts, [date(2026, 10, 30)]) for ts in minutes.datetime], expected)

    def test_compact_daily_calendar_and_holiday(self):
        minutes = bars(["2026-09-30 21:00", "2026-10-01 00:30"])
        daily = pd.DataFrame({"trading_day": [20260930, 20261008]})
        self.assertEqual(assign_trading_day(minutes, daily).trading_day.tolist(), ["2026-10-08"] * 2)


class ParquetReplayTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = DataRepository(Path(self.tmp.name))
        self.minutes = bars(["2026-10-30 14:59", "2026-10-30 21:00", "2026-10-31 00:30", "2026-11-02 09:00"])
        self.daily = bars(["2026-10-30", "2026-10-31"], ["2026-10-30", "2026-10-31"], [200, 999])
        for period, frame in (("1min", self.minutes), ("1d", self.daily)):
            path = self.repo._resolve_file("主力连续", "SN", period, "主连")
            path.parent.mkdir(parents=True, exist_ok=True)
            frame.to_parquet(path, index=False)

    def test_load_daily_weekly_monthly_and_replay_across_weekend(self):
        minutes = self.repo.load_minutes("主力连续", "SN", "主连")
        self.assertEqual(minutes.trading_day.tolist(), ["2026-10-30"] + ["2026-11-02"] * 3)
        daily = self.repo.load_bars("主力连续", "SN", "1d", "主连")
        self.assertEqual(daily.trading_day.tolist(), ["2026-10-30", "2026-11-02"])
        self.assertEqual(daily.datetime.dt.strftime("%Y-%m-%d").tolist(), daily.trading_day.tolist())
        self.assertEqual(daily.volume.tolist(), [10, 30])
        self.assertEqual(daily.open.tolist(), [200, 101])
        for period in ("1w", "1M"):
            grouped = self.repo.load_bars("主力连续", "SN", period, "主连")
            self.assertEqual(grouped.trading_day.tolist(), ["2026-10-30", "2026-11-02"])
            self.assertEqual(grouped.volume.tolist(), [10, 30])
        engine = ReplayEngine(daily, "1d", minutes)
        engine.jump_trading_day("2026-11-02")
        self.assertEqual(engine.current().trading_day, "2026-11-02")
        self.assertEqual(locate_bar_index(daily, "1d", pd.Timestamp("2026-10-31 00:30"), "2026-11-02"), 1)
        # Cached reloads must keep the same calendar and must not rewrite user's files.
        pd.testing.assert_frame_equal(self.repo.load_bars("主力连续", "SN", "1d", "主连"), daily)
        path = self.repo._resolve_file("主力连续", "SN", "1d", "主连")
        pd.testing.assert_frame_equal(pd.read_parquet(path), self.daily)

    def test_appending_daily_cleans_legacy_weekend_rows(self):
        incoming = bars(["2026-11-02"], ["2026-11-02"], [300])
        added, total, last = self.repo.append_bars("主力连续", "SN", "1d", "主连", incoming)
        self.assertEqual((added, total, last), (1, 2, pd.Timestamp("2026-11-02")))
        path = self.repo._resolve_file("主力连续", "SN", "1d", "主连")
        self.assertEqual(pd.read_parquet(path).trading_day.tolist(), ["2026-10-30", "2026-11-02"])

    def test_edb_fetch_normalize_assign_persist_and_reload_with_copy_on_write(self):
        def vendor_frame(timestamps):
            times = pd.DatetimeIndex(pd.to_datetime(timestamps)).tz_localize("Asia/Shanghai").tz_convert("UTC")
            values = list(range(100, 100 + len(times)))
            return pd.DataFrame({
                "datetime_nano": times.as_unit("ns").asi8,
                "open": values, "high": [v + 2 for v in values],
                "low": [v - 1 for v in values], "close": [v + 1 for v in values],
                "volume": 10, "close_oi": 50,
            })

        raw_minutes = vendor_frame(["2026-10-30 14:59", "2026-10-30 21:00", "2026-10-31 00:30", "2026-11-02 09:00"])
        raw_daily = vendor_frame(["2026-10-30", "2026-11-02"])
        def request(_symbol, period, *_args):
            return raw_daily.copy() if period == 86400 else raw_minutes.copy()
        scope = pd.option_context("mode.copy_on_write", True) if int(pd.__version__.split(".")[0]) < 3 else nullcontext()
        with scope, patch("futures_replay.edb_update._request_csv", side_effect=request), patch("futures_replay.edb_update._now_shanghai", return_value=datetime(2026, 11, 2, 15)):
            bundle = fetch_edb_bundle("KQ.m@SHFE.sn", datetime(2026, 10, 30), datetime(2026, 10, 30))
            stats = persist_edb_bundle(self.repo, "主力连续", "SN", "主连", bundle)
            self.assertEqual(stats.minute_total, 4)
            self.assertEqual(stats.daily_total, 2)
            minutes = self.repo.load_minutes("主力连续", "SN", "主连")
            self.assertEqual(minutes.trading_day.tolist(), ["2026-10-30"] + ["2026-11-02"] * 3)
            daily = self.repo.load_bars("主力连续", "SN", "1d", "主连")
            self.assertEqual(daily.trading_day.tolist(), ["2026-10-30", "2026-11-02"])


if __name__ == "__main__":
    unittest.main()
