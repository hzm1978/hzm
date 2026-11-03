"""Regression for an opposing five-wave move inside an older main trend."""
import unittest
from dataclasses import replace
import json

import numpy as np
import pandas as pd

from futures_replay.trend_analysis import analyze_trend
from futures_replay.trend_hierarchy import analyze_subwaves
from futures_replay.trend_intervals import BookInterval
from futures_replay.trend_report import export_analysis, analysis_html
from futures_replay.trend_settings import TrendSettings
from futures_replay.trend_corrections import classify_correction, correction_patterns
from futures_replay.trend_types import TrendPivot


def daily_five(*, reflect=False, tail=3):
    # A long uptrend owns the outer Q. The recent downtrend has two equal Qs;
    # its five legs must remain visible even while the outer bias is still up.
    anchors = [(0, 90), (5, 160), (20, 115), (48, 170),
               (60, 200), (65, 180), (68, 188), (71, 175),
               (76, 160), (79, 170), (82, 155), (87, 145)]
    if tail:
        anchors.append((87 + tail, 148))
    x = np.arange(anchors[-1][0] + 1)
    y = np.interp(x, *zip(*anchors))
    opening = np.r_[y[0] - .1, y[:-1]]
    frame = pd.DataFrame({"datetime": pd.bdate_range("2026-06-01", periods=len(x)),
                          "open": opening, "high": np.maximum(y, opening) + .05,
                          "low": np.minimum(y, opening) - .05, "close": y})
    if reflect:
        frame = frame.assign(open=300-frame.open, high=300-frame.low,
                             low=300-frame.high, close=300-frame.close)
    return frame


class SubwaveRegressionTests(unittest.TestCase):
    def test_opposing_five_has_its_own_intervals_and_ruler(self):
        a = analyze_trend(daily_five())
        self.assertEqual(a.direction, "up")
        self.assertTrue(a.subwaves, "An outer uptrend must not suppress the down subwaves")
        s = a.subwaves[0]
        self.assertEqual(s.direction, -1)
        self.assertEqual([p.index for p in s.points], [61, 66, 69, 77, 80, 88])
        self.assertEqual([p.bars for p in s.intervals], [5, 6])
        self.assertEqual(len(s.retracements), 2)
        for wave, item in zip((2, 4), s.retracements):
            origin, peak, endpoint = s.points[wave-2:wave+1]
            self.assertAlmostEqual(item.percent,
                (endpoint.price-peak.price)/(origin.price-peak.price)*100)
        origin, tip = s.ruler_origin, s.ruler_tip
        self.assertEqual(origin, s.points[4])
        for ratio, level in zip((.4, .62, .786), s.levels):
            self.assertAlmostEqual(level.price, tip.price + ratio*(origin.price-tip.price))

    def test_symmetric_count_and_three_legs_do_not_become_five(self):
        down = analyze_trend(daily_five()).subwaves[0]
        up = analyze_trend(daily_five(reflect=True)).subwaves[0]
        self.assertEqual(up.direction, 1)
        self.assertEqual([p.index for p in up.points], [p.index for p in down.points])
        self.assertEqual([p.bars for p in up.intervals], [p.bars for p in down.intervals])
        short = analyze_trend(daily_five().iloc[:77])
        self.assertFalse(any(p.direction == -1 and len(p.points) == 6 for p in short.subwaves))

    def test_fifth_leg_is_visible_before_a_future_pivot_confirms_it(self):
        a = analyze_trend(daily_five().iloc[:88])
        s = a.subwaves[0]
        self.assertEqual(s.state, "running")
        self.assertEqual(s.points[-1].index, 87)
        self.assertIn("进行中", s.labels[-1].text)
        self.assertFalse(any(p.index == 87 for p in a.navigation_pivots))
        self.assertTrue(all(p.confirmed_at <= a.as_of_index for p in s.labels))

    def test_not_yet_broken_wave_three_has_an_open_q_clock(self):
        a = analyze_trend(daily_five().iloc[:82])
        s = a.subwaves[0]
        self.assertEqual(s.state, "fifth_pending")
        self.assertEqual(len(s.intervals), 1)
        self.assertIsNotNone(s.pending_interval)
        self.assertEqual(s.pending_interval.state, "running")
        self.assertIsNone(s.pending_interval.first_confirmed_at)
        self.assertIn("未破浪3", s.labels[-1].text)
        confirmed = analyze_trend(daily_five().iloc[:83]).subwaves[0]
        self.assertEqual(len(confirmed.intervals), 2)
        self.assertIsNone(confirmed.pending_interval)

    def test_parent_outside_local_scope_does_not_hide_child_intervals(self):
        frame = daily_five()
        a = analyze_trend(frame)
        baseline = a.subwaves[0]
        raw = tuple(replace(q, parent_start=30) if q.direction == -1 else q for q in a.intervals)
        raw += (BookInterval(-1, 30, 90, 100, 60, 60, 200, 90, 90, 60),)
        found = analyze_subwaves(frame, np.arange(len(frame)), a.navigation_pivots, raw,
                                np.ones(len(frame)), a.structure)[0]
        self.assertEqual(found.points, baseline.points)
        self.assertEqual([p.bars for p in found.intervals], [5, 6])

    def test_corrections_belong_to_wave_two_and_four_not_rolling_abcde_windows(self):
        a = analyze_trend(daily_five())
        s = a.subwaves[0]
        local = [p for p in a.corrections if p.start_index >= s.points[0].index]
        self.assertEqual(len(local), 2)
        self.assertEqual([(p.start_index, p.end_point.index) for p in local], [(65, 69), (76, 80)])
        self.assertTrue(all(p.kind == "simple" and p.state == "breakout" for p in local))
        self.assertEqual([p.label for p in s.boundaries], ["子A1", "子A2"])
        self.assertIsNotNone(s.trendline)

    def test_saved_old_settings_enable_subwaves_and_export_the_actual_ruler_basis(self):
        self.assertTrue(TrendSettings.from_dict({"show_navigation": False}).show_subwaves)
        a = analyze_trend(daily_five())
        exported = json.loads(export_analysis(a))["analysis"]["subwaves"][0]
        self.assertEqual(exported["ruler_origin"]["index"], 80)
        self.assertEqual(exported["ruler_tip"]["index"], 88)
        self.assertEqual([p["bars"] for p in exported["intervals"]], [5, 6])
        self.assertIn("独立子浪", analysis_html(a, daily_five()))

    def test_irregular_needs_same_direction_timing_not_an_accidental_adjacent_match(self):
        prices = [120, 100, 118, 88, 127, 103]
        def points(durations):
            indices = np.r_[0, np.cumsum(durations)]
            return tuple(TrendPivot(int(x), price, "H" if i % 2 == 0 else "L", int(x)+1)
                         for i, (x, price) in enumerate(zip(indices, prices)))
        self.assertIsNone(classify_correction(points([2, 9, 3, 3, 15]), 1))
        self.assertEqual(classify_correction(points([2, 5, 8, 6, 7]), 1)[0], "abcde_irregular")

    def test_direction_hint_does_not_make_every_rebound_an_up_impulse(self):
        frame = daily_five()
        a = analyze_trend(frame)
        patterns = correction_patterns(frame, a.navigation_pivots, np.arange(len(frame)),
                                       np.ones(len(frame)), direction=1)
        # The high at 69 is below 61 and high 80 is below 69. A parent-up hint
        # cannot turn those countertrend rebounds into fresh upward impulses.
        self.assertFalse(any(p.start_index in {69, 80} for p in patterns))


class SubwaveChartTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_compact_chart_keeps_actual_wave_and_measurement_labels_with_old_waves_off(self):
        from PySide6.QtGui import QFont
        from futures_replay.ui.chart_widget import CandleChart
        self.app.setFont(QFont("Noto Sans CJK SC", 9))
        chart = CandleChart()
        chart.resize(553, 330)
        frame = daily_five()
        chart.set_series(frame, len(frame)-1)
        chart.visible_bars = 40
        chart.set_wave_visible(False)
        chart.set_trend_visible(True)
        self.assertFalse(chart.trend_settings.get("show_navigation", False))
        drawn = []
        original = chart._draw_wave_tag
        def traced(*args, **kwargs):
            rect = original(*args, **kwargs)
            if rect is not None:
                drawn.append((args[3], rect))
            return rect
        chart._draw_wave_tag = traced
        chart.show()
        try:
            self.app.processEvents()
            self.assertFalse(chart.grab().isNull())
            texts = {text for text, _ in drawn}
            for name in ("子1", "子2", "子3", "子4", "子5", "子q1=5根", "子q2=6根"):
                self.assertIn(name, texts)
            for prefix in ("子2回调", "子4回调"):
                self.assertTrue(any(t.startswith(prefix) for t in texts), texts)
            self.assertTrue(all(chart._plot_rect.contains(rect) for _, rect in drawn))
        finally:
            chart.close()

    def test_partial_and_future_daily_ohlc_do_not_change_the_formal_subwave_count(self):
        from futures_replay.ui.chart_widget import CandleChart
        frame = daily_five()
        index = 82
        partial = dict(frame.iloc[index])
        chart = CandleChart()
        chart.set_series(frame, index, partial)
        before = chart._trend_analysis_cached(index+1)
        self.assertEqual(before.subwaves[0].state, "fifth_pending")
        changed = frame.copy()
        changed.loc[index:, ["open", "high", "low", "close"]] = [9000, 9999, 1, 2]
        chart.set_series(changed, index, partial)
        after = chart._trend_analysis_cached(index+1)
        self.assertEqual(before, after)
        self.assertEqual(after.as_of_index, index-1)
        self.assertEqual(len(after.subwaves[0].intervals), 1)
        chart.close()


if __name__ == "__main__":
    unittest.main()
