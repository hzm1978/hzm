import os
from types import MethodType, SimpleNamespace
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pandas as pd
from PySide6.QtWidgets import QApplication, QPushButton

from futures_replay.data_repo import DataRepository
from futures_replay.replay import ReplayEngine
from futures_replay.timeline_utils import shift_trading_day
from futures_replay.ui.main_window import MainWindow


def daily_frame():
    dates = pd.bdate_range("2026-10-19", "2026-11-06")
    prices = list(range(100, 100 + len(dates)))
    return DataRepository.standardize(pd.DataFrame({
        "datetime": dates, "trading_day": dates.strftime("%Y-%m-%d"),
        "open": prices, "high": [p + 2 for p in prices],
        "low": [p - 1 for p in prices], "close": [p + 1 for p in prices],
        "volume": 10, "amount": 100, "open_interest": 50,
    }))


class DayNavigationTests(unittest.TestCase):
    def setUp(self):
        self.daily = daily_frame()
        self.days = self.daily.trading_day.tolist()

    def engine(self, period, minutes=None, daily=True):
        frame = self.daily if period == "1d" else DataRepository._aggregate_daily_period(self.daily, period)
        return ReplayEngine(frame, period, minutes, allow_synthetic=False, daily=self.daily if daily else None)

    def test_daily_weekly_and_monthly_step_one_trading_day(self):
        for period in ("1d", "1w", "1M"):
            with self.subTest(period=period):
                engine = self.engine(period)
                self.assertEqual(engine.available_trading_days(), self.days)
                for day in self.days:
                    state = engine.jump_trading_day(day)
                    self.assertEqual(state.trading_day, day)
                    next_day = shift_trading_day(engine.available_trading_days(), state.trading_day, 1)
                    expected = self.days[self.days.index(day) + 1] if day != self.days[-1] else None
                    self.assertEqual(next_day, expected)
                self.assertEqual(shift_trading_day(self.days, "2026-10-30", 1), "2026-11-02")

    def test_weekly_without_minutes_uses_daily_prefix_and_flags_coarse_data(self):
        engine = self.engine("1w")
        state = engine.jump_trading_day("2026-10-20")
        self.assertTrue(state.synthetic)
        self.assertEqual(state.partial["open"], 100)
        self.assertEqual(state.partial["close"], 102)
        self.assertEqual(state.partial["high"], 103)
        self.assertEqual(state.partial["volume"], 20)
        self.assertEqual(state.source_bar["volume"], 10)
        # Friday's high / close must not leak into Tuesday's partial weekly bar.
        self.assertLess(state.partial["high"], engine.bars.iloc[0].high)

    def test_partial_minute_history_does_not_remove_earlier_days_or_double_count(self):
        minutes = self.daily.iloc[2:3].copy()
        minutes["datetime"] = pd.to_datetime(["2026-10-21 09:00"])
        minutes["volume"] = 3
        engine = self.engine("1w", minutes)
        self.assertEqual(engine.available_trading_days(), self.days)
        state = engine.jump_timestamp(pd.Timestamp("2026-10-21 09:00"), "2026-10-21")
        self.assertEqual(state.trading_day, "2026-10-21")
        self.assertFalse(state.synthetic)
        self.assertEqual(state.partial["volume"], 23)
        self.assertEqual(state.source_bar["volume"], 3)
        thursday = engine.jump_trading_day("2026-10-22")
        self.assertEqual(thursday.partial["volume"], 33)
        self.assertTrue(thursday.synthetic)

    def test_weekly_without_any_daily_source_disables_day_navigation(self):
        engine = self.engine("1w", daily=False)
        self.assertEqual(engine.available_trading_days(), [])


class DayButtonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_real_button_clicks_move_day_on_weekly_and_monthly_charts(self):
        daily = daily_frame()
        for period in ("1w", "1M"):
            with self.subTest(period=period):
                frame = DataRepository._aggregate_daily_period(daily, period)
                engine = ReplayEngine(frame, period, allow_synthetic=False, daily=daily)
                host = SimpleNamespace(
                    engine=engine, panels=[],
                    btn_prev_day=QPushButton("−"), btn_next_day=QPushButton("＋"),
                    _guard_history_jump=lambda *_: True,
                    _rebase_trade_clock_after_history_jump=lambda: None,
                    _capture_current_session=lambda **_: None,
                    _checkpoint_replay_heads=lambda: None,
                    _schedule_save=lambda: None,
                )
                for name in ("_available_replay_days", "_jump_engine_to_trading_day", "_update_day_step_buttons", "_step_trading_day"):
                    setattr(host, name, MethodType(getattr(MainWindow, name), host))
                host._refresh_all_panels = host._update_day_step_buttons
                host.btn_prev_day.clicked.connect(lambda _=False, h=host: h._step_trading_day(-1))
                host.btn_next_day.clicked.connect(lambda _=False, h=host: h._step_trading_day(1))
                engine.jump_trading_day("2026-10-20")
                host._update_day_step_buttons()
                host.btn_next_day.click()
                self.assertEqual(engine.current().trading_day, "2026-10-21")
                host.btn_prev_day.click()
                self.assertEqual(engine.current().trading_day, "2026-10-20")
                engine.jump_trading_day("2026-10-30")
                host.btn_next_day.click()
                self.assertEqual(engine.current().trading_day, "2026-11-02")
                engine.jump_trading_day(daily.iloc[-1].trading_day)
                host._update_day_step_buttons()
                self.assertFalse(host.btn_next_day.isEnabled())
                engine.jump_trading_day(daily.iloc[0].trading_day)
                host._update_day_step_buttons()
                self.assertFalse(host.btn_prev_day.isEnabled())


if __name__ == "__main__":
    unittest.main()
