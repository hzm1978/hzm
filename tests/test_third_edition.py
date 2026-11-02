import json
import os
import unittest
from dataclasses import replace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pandas as pd
from PySide6.QtWidgets import QApplication

from futures_replay.trend_analysis import analyze_trend, _select_valid_trendline
from futures_replay.trend_candles import book_candle_patterns, key_reversals, standard_candles
from futures_replay.trend_corrections import classify_correction, correction_for_interval, make_correction, compound_correction
from futures_replay.trend_intervals import BookInterval, build_structure, compare_spans, find_intervals, tp_ta, failed_interval_candidate
from futures_replay.trend_report import analysis_html, export_analysis
from futures_replay.trend_settings import TrendSettings
from futures_replay.trend_signals import line_break_decision
from futures_replay.trend_types import TrendLabel, TrendPivot
from futures_replay.ui.chart_widget import CandleChart
from futures_replay.ui.trend_dialog import TrendIndicatorDialog
from test_book_trend import swings


def candles(rows, warmup=0):
    return pd.DataFrame([(100, 100.7, 99.8, 100.5)] * warmup + rows,
                        columns=["open", "high", "low", "close"], dtype=float)


def pivot_sequence(prices, start=5, step=5, first="H"):
    return tuple(TrendPivot(start + i * step, float(price), first if i % 2 == 0 else ("L" if first == "H" else "H"),
                            start + i * step + 1) for i, price in enumerate(prices))


def q_fixture(spans):
    """Independent hand-drawn uptrend: each old high is exceeded on bar start+Q."""
    anchors = [(0, 90.), (3, 110.)]
    starts, ends, level, start = [], [], 110., 3
    for q in spans:
        starts.append(start)
        end = start + q
        anchors.extend([(start + max(1, q // 3), level - 6), (end - 1, level - .2), (end, level + .5), (end + 3, level + 10)])
        ends.append(end)
        start, level = end + 3, level + 10
    x = np.arange(start + 2)
    curve = np.interp(x, [p[0] for p in anchors], [p[1] for p in anchors])
    opening = np.r_[curve[0] - .1, curve[:-1]]
    frame = pd.DataFrame({"open": opening, "high": np.maximum(opening, curve) + .05,
                          "low": np.minimum(opening, curve) - .05, "close": curve})
    return frame, np.arange(len(frame)), starts, ends


class IntervalDefinitionTests(unittest.TestCase):
    def test_dif_delta_regimes_and_exact_thresholds(self):
        cases = [(6, 5, "same", "dif"), (5, 10, "expanding", "dif"),
                 (9, 4, "contracting", "dif"), (3, 9, "upgrade", "dif"),
                 (9, 2, "downgrade", "dif"), (10, 11, "same", "δ"),
                 (13, 18, "expanding", "δ"), (43, 24, "contracting", "δ"),
                 (5, 17, "upgrade", "δ"), (17, 5, "downgrade", "δ"),
                 (250, 207, "same", "δ"), (250, 302, "same", "δ"),
                 (500, 1309, "expanding", "δ"), (500, 191, "contracting", "δ")]
        for previous, current, kind, method in cases:
            with self.subTest(previous=previous, current=current):
                r = compare_spans(previous, current)
                self.assertEqual((r.kind, r.method), (kind, method))
        with self.assertRaises(ValueError):
            compare_spans(0, 10)

    def test_cropped_first_bar_and_flat_warmup_cannot_manufacture_a_large_q(self):
        frame = candles([(90, 90.1, 89.9, 90)] * 50 + [(90, 95, 90, 94), (94, 96, 94, 95)])
        intervals = find_intervals(frame, np.arange(len(frame)))
        self.assertFalse(any(p.start_index == 0 for p in intervals))
        self.assertFalse(any(p.bars >= 40 for p in intervals))

    def test_first_q_waits_for_close_and_second_q_can_use_wick(self):
        frame = candles([
            (9, 10, 8.5, 9.5), (9.5, 11, 9, 10.5), (10.5, 12, 10, 11.7),
            (10.4, 11.5, 9.8, 11), (10, 11.6, 9.5, 11.3),
            (11.2, 12.5, 10.5, 11.8), (11.8, 12.8, 11.5, 12.3),
            (11.6, 12.6, 11, 12), (11.8, 12.7, 11.3, 12.3),
            (12.2, 13.2, 11.8, 12.7),
        ])
        positions = np.arange(len(frame))
        intervals = find_intervals(frame, positions)
        first = next(p for p in intervals if p.start_index == 2 and p.direction == 1)
        second = next(p for p in intervals if p.start_index == 6 and p.direction == 1)
        self.assertEqual((first.bars, first.end_index, first.first_bars, first.first_confirmed_at), (3, 5, 4, 6))
        self.assertEqual((second.bars, second.end_index, second.first_confirmed_at), (3, 9, None))
        structure = build_structure(intervals, 1, 0, 8.5, frame, positions)
        self.assertEqual([(p.start_index, p.bars, p.confirmed_at) for p in structure.intervals], [(2, 4, 6), (6, 3, 9)])
        self.assertEqual(structure.kind, "same_five")
        before_close = build_structure(find_intervals(frame.iloc[:6], positions[:6]), 1, 0, 8.5, frame.iloc[:6], positions[:6])
        self.assertIsNone(before_close)

    def test_downtrend_is_symmetric_and_dates_do_not_create_bars(self):
        frame, positions, _, _ = q_fixture([12, 14])
        other = pd.DataFrame({"open": -frame.open, "high": -frame.low, "low": -frame.high, "close": -frame.close})
        up = [p for p in find_intervals(frame, positions) if p.direction == 1]
        down = [p for p in find_intervals(other, positions) if p.direction == -1]
        self.assertEqual([(p.start_index, p.end_index, p.bars) for p in up], [(p.start_index, p.end_index, p.bars) for p in down])
        frame["datetime"] = pd.bdate_range("2026-10-01", periods=len(frame))
        self.assertEqual(find_intervals(frame, positions), tuple(sorted(find_intervals(frame.drop(columns="datetime"), positions), key=lambda p: (p.end_index, p.start_index, p.direction))))

    def test_real_q_fixture_and_third_same_degree_merge(self):
        frame, positions, starts, ends = q_fixture([12, 12, 12])
        raw = find_intervals(frame, positions)
        actual = [p for p in raw if p.direction == 1 and p.start_index in starts]
        self.assertEqual([(p.start_index, p.end_index, p.bars) for p in actual], list(zip(starts, ends, [12] * 3)))
        before = frame.iloc[:ends[1] + 1]
        s2 = build_structure(find_intervals(before, positions[:len(before)]), 1, 0, frame.low.iloc[0], before, positions[:len(before)])
        self.assertEqual(s2.kind, "same_five")
        full = build_structure(raw, 1, 0, frame.low.iloc[0], frame, positions)
        self.assertEqual(len(full.intervals), 1)
        self.assertEqual(len(full.navigation), 2)
        self.assertTrue(any("法则1" in r for r in full.rules))

    def test_nested_and_hidden_intervals_are_separate_observations(self):
        high = [10, 15, 20, 16, 18, 15, 17, 16, 19, 18, 21, 22]
        frame = pd.DataFrame({"open": np.array(high) - 2, "high": high,
                              "low": np.array(high) - 3, "close": np.array(high) - .2})
        found = find_intervals(frame, np.arange(len(frame)))
        child = next(p for p in found if p.direction == 1 and p.start_index == 4)
        self.assertEqual(child.parent_start, 2)
        self.assertTrue(child.hidden)

    def test_three_outer_intervals_define_four_seven_wave_candidates(self):
        for spans, expected in [([12, 18, 24], "expanding_seven"), ([30, 20, 12], "contracting_seven"),
                                ([12, 18, 12], "diamond_seven"), ([30, 18, 28], "x_seven")]:
            with self.subTest(spans=spans):
                frame, positions, _, _ = q_fixture(spans)
                s = build_structure(find_intervals(frame, positions), 1, 0, frame.low.iloc[0], frame, positions)
                self.assertEqual(s.kind, expected)
                self.assertEqual(len(s.intervals), 3)

    def test_rule_three_resolves_equal_trailing_pair_only_after_continuation(self):
        frame, positions, _, ends = q_fixture([12, 18, 18, 22])
        before = frame.iloc[:ends[2] + 1]
        s = build_structure(find_intervals(before, positions[:len(before)]), 1, 0, frame.low.iloc[0], before, positions[:len(before)])
        self.assertEqual(s.kind, "expanding_seven")
        full = build_structure(find_intervals(frame, positions), 1, 0, frame.low.iloc[0], frame, positions)
        self.assertEqual([x.bars for x in full.intervals], [12, 22])
        self.assertTrue(any("法则3" in x for x in full.rules))

    def test_upgrade_and_downgrade_do_not_count_as_a_second_main_interval(self):
        for spans, rule in [([6, 20], "法则7"), ([20, 5], "法则4/8")]:
            with self.subTest(spans=spans):
                frame, positions, _, _ = q_fixture(spans)
                s = build_structure(find_intervals(frame, positions), 1, 0, frame.low.iloc[0], frame, positions)
                self.assertEqual(len(s.intervals), 1)
                self.assertTrue(s.navigation)
                self.assertTrue(any(rule in x for x in s.rules))

    def test_height_merge_is_available_when_threshold_crossed_not_on_every_refresh(self):
        frame, positions, _, _ = q_fixture([12, 14])
        raw = find_intervals(frame, positions)
        trigger = len(frame) - 1
        frame.loc[trigger, ["open", "high", "low", "close"]] = [130, 161, 129, 160]
        s = build_structure(raw, 1, 0, frame.low.iloc[0], frame, positions)
        self.assertEqual(len(s.intervals), 1)
        self.assertEqual(len(s.intervals[0].components), 2)
        self.assertEqual(s.intervals[0].confirmed_at, trigger)
        extended = pd.concat([frame, candles([(160, 161, 159, 160)] * 3)], ignore_index=True)
        later = build_structure(raw, 1, 0, frame.low.iloc[0], extended, np.arange(len(extended)))
        self.assertEqual(later.intervals[0].confirmed_at, trigger)

    def test_failure_interval_is_only_a_timed_786_candidate(self):
        frame, positions, _, _ = q_fixture([12])
        x = np.arange(20, 32)
        close = np.interp(x, [19, 22, 30, 31], [120, 114, 118.8, 118.8])
        opening = np.r_[120, close[:-1]]
        extra = pd.DataFrame({"open": opening, "high": np.maximum(opening, close) + .05,
                              "low": np.minimum(opening, close) - .05, "close": close})
        frame = pd.concat([frame, extra], ignore_index=True); positions = np.arange(len(frame))
        s = build_structure(find_intervals(frame, positions), 1, 0, frame.low.iloc[0], frame, positions)
        item = failed_interval_candidate(s, frame, positions)
        self.assertIsNotNone(item)
        self.assertEqual(item.state, "failed_candidate")
        self.assertTrue(.828 * s.intervals[-1].bars <= item.bars <= 1.208 * s.intervals[-1].bars)
        self.assertEqual(len(s.intervals), 1)


class ThirdEditionCandlesTests(unittest.TestCase):
    def test_standard_40_top_five_excludes_current_and_retains_samples(self):
        frame = candles([], 40)
        for j, strength in enumerate([2, 3, 4, 5, 6]):
            frame.loc[j, ["open", "high", "low", "close"]] = [100, 100 + strength, 99, 100 + strength]
        frame = pd.concat([frame, candles([(100, 110, 99, 109)])], ignore_index=True)
        result = standard_candles(frame, np.arange(len(frame)))
        self.assertIsNone(result[39].threshold)
        self.assertAlmostEqual(result[40].threshold, 5)
        self.assertEqual(set(result[40].sample_indices), set(range(5)))
        self.assertTrue(result[40].standard)
        changed = frame.copy(); changed.loc[40, ["high", "close"]] = [1000, 999]
        self.assertEqual(standard_candles(changed, np.arange(41))[40].threshold, 5)

    def test_current_size_cannot_qualify_itself_and_sample_is_directional(self):
        frame = candles([(100, 103, 98, 99)], 40)
        std = standard_candles(frame, np.arange(41))[-1]
        self.assertEqual(std.strength, 4)  # bearish high -> close
        self.assertAlmostEqual(std.threshold, .7)
        self.assertTrue(std.standard)

    def test_third_edition_single_and_two_bar_shapes(self):
        cases = {
            "锤子线": [(102, 102.2, 95, 101)],  # body colour unrestricted
            "倒锤子线": [(100, 107, 99.9, 101)],
            "看涨十字星": [(100, 100.2, 95, 100.05)],
            "看跌十字星": [(100, 105, 99.8, 100.05)],
            "阳抱阴": [(104, 105, 99, 100), (99, 106, 98, 105)],
            "阴抱阳": [(100, 105, 99, 104), (105, 106, 98, 99)],
            "上补缺影线": [(100, 105, 99, 101), (101, 108, 100.8, 107)],
            "下补缺影线": [(104, 105, 99, 103), (103, 103.1, 96, 97)],
            "魂断蓝桥·上": [(100, 103, 99.8, 103), (104, 108, 104, 107)],
            "魂断蓝桥·下": [(104, 104.2, 101, 101), (100, 100, 96, 97)],
            "跳空高开": [(100, 101, 99, 100.5), (104, 106, 103, 105)],
            "跳空低开": [(100, 101, 99, 100.5), (97, 98, 95, 96)],
        }
        for name, rows in cases.items():
            with self.subTest(name=name):
                frame = candles(rows, 40)
                labels = book_candle_patterns(frame, np.arange(len(frame)))
                hits = [p for p in labels if p.text == name and p.index == len(frame) - 1]
                self.assertTrue(hits)
                self.assertTrue(hits[-1].qualified)

    def test_half_recovery_star_stays_nonstandard_until_later_confirmation(self):
        rows = [(110, 111, 99, 100), (99, 100, 98, 99.5), (100, 109, 99, 108)]
        frame = candles(rows, 40)
        patterns = book_candle_patterns(frame, np.arange(len(frame)))
        candidate = next(p for p in patterns if p.text == "非标准启明星" and p.index == 42)
        self.assertFalse(candidate.qualified)
        self.assertFalse(any(p.text == "启明星" and p.index == 42 for p in patterns))
        frame = pd.concat([frame, candles([(108, 109, 106, 107), (107, 111, 106.5, 110)])], ignore_index=True)
        patterns = book_candle_patterns(frame, np.arange(len(frame)))
        self.assertTrue(any(p.text == "阳阴阳确认" and p.index == 44 and p.qualified for p in patterns))

    def test_full_star_ghost_gate_and_stop_acceleration(self):
        for rows, wanted in [
            ([(110, 111, 99, 100), (99, 100, 98, 99.5), (100, 113, 99, 112)], "上鬼门关"),
            ([(100, 111, 99, 110), (110.1, 112, 109, 110.5), (110, 111, 97, 98)], "下鬼门关"),
            ([(100, 105, 99.5, 104), (104, 104.2, 103.5, 103.8), (104, 110, 103.8, 109)], "急停加速·上"),
        ]:
            with self.subTest(wanted=wanted):
                frame = candles(rows, 40)
                self.assertTrue(any(p.text == wanted and p.qualified for p in book_candle_patterns(frame, np.arange(len(frame)))))

    def test_key_reversal_requires_real_location_and_observable_confirmation(self):
        frame = candles([(102, 102.2, 95, 101)], 40)
        shapes = book_candle_patterns(frame, np.arange(41))
        self.assertEqual(key_reversals(shapes, (), np.arange(41), np.ones(41)), ())
        context = TrendLabel(40, 95, "浪2 78.6%", 1, 43, 30)
        self.assertEqual(key_reversals(shapes, (), np.arange(41), np.ones(41), contexts=[context]), ())
        # After the context is observable, retain the ACTUAL candle 40 with
        # availability 43; never move the event onto a later candle.
        result = key_reversals(shapes, (), np.arange(44), np.ones(44), contexts=[context])
        self.assertEqual([(x.index, x.confirmed_at) for x in result], [(40, 43)])
        self.assertFalse(any(x.qualified for x in book_candle_patterns(candles([(102, 102.2, 95, 101)]), np.arange(1))))


class CorrectionAndBoundaryTests(unittest.TestCase):
    def test_failed_fifth_recovery_allows_post_extreme_anchor_without_a_double_top(self):
        frame, pivots, positions, atr = swings([(0, 90, "L"), (10, 130, "H"), (20, 110, "L"), (30, 127, "H")])
        mapping = {int(i): int(i) for i in positions}
        line = _select_valid_trendline("up", pivots, frame.high.to_numpy(), frame.low.to_numpy(),
                                       atr, mapping, positions, len(frame) - 1, min_anchor_bars=6)
        self.assertIsNotNone(line)
        self.assertEqual(line[1].index, 20)

    def test_observed_one_three_one_abc_is_not_forced_to_three_three_five(self):
        points = [(0, 80, "L")] + [(5 + i * 5, price, "H" if i % 2 == 0 else "L")
                  for i, price in enumerate([120, 100, 104, 102, 110, 95])]
        frame, pivots, positions, _ = swings(points, tail=125, extra=15)
        item = compound_correction(pivots[1:], 1, frame, positions)
        self.assertIsNotNone(item)
        self.assertEqual(item.kind, "abc")
        self.assertEqual(item.structure, "1-3-1")
        self.assertEqual(item.end_point.price, 95)

    def test_four_families_and_abc_subtypes(self):
        cases = [([120, 100], "simple"), ([120, 100, 110, 95], "abc"),
                 ([120, 100, 115, 105], "abc_contracting"), ([120, 100, 125, 110], "abc_strong"),
                 ([120, 100, 117, 103, 114, 106], "triangle"),
                 ([110, 100, 115, 95, 120, 90], "expanding_triangle"),
                 ([120, 110, 118, 108, 116, 106], "abcde_trending"),
                 ([120, 100, 120, 100, 120, 100], "abcde_sideways")]
        for prices, kind in cases:
            with self.subTest(kind=kind):
                self.assertEqual(classify_correction(pivot_sequence(prices), 1)[0], kind)

    def test_triangle_a_is_end_e_not_the_lowest_a(self):
        points = [(0, 60, "L")] + [(10 + i * 10, price, "H" if i % 2 == 0 else "L")
                 for i, price in enumerate([120, 100, 117, 103, 114, 106])]
        frame, pivots, positions, atr = swings(points, tail=125, extra=12)
        interval = BookInterval(1, 10, 70, 120, 60, 20, 100, 70, 70, 60)
        correction = correction_for_interval(interval, pivots, frame, positions, atr)
        self.assertEqual(correction.kind, "triangle")
        self.assertEqual((correction.end_point.index, correction.end_point.price), (60, 106))
        self.assertEqual(correction.state, "breakout")

    def test_main_a_does_not_follow_later_navigation_lows_in_the_resuming_wave(self):
        points = [(0, 60, "L"), (10, 120, "H"), (20, 100, "L"), (30, 115, "H"),
                  (40, 105, "L"), (45, 108, "H"), (50, 106, "L"),
                  (55, 113, "H"), (60, 110, "L"), (65, 118, "H"),
                  (70, 115, "L"), (75, 125, "H")]
        frame, pivots, positions, atr = swings(points, tail=125, extra=4)
        interval = BookInterval(1, 10, 74, 120, 64, 20, 100, 74, 74, 64)
        correction = correction_for_interval(interval, pivots, frame, positions, atr)
        self.assertEqual(correction.kind, "triangle")
        self.assertEqual(correction.end_point.index, 50)
        self.assertNotEqual(correction.end_point.index, 70)

    def test_rule_six_does_not_reject_a_correction_beyond_wave_one_origin(self):
        points = [(0, 100, "L"), (10, 120, "H"), (20, 90, "L"), (30, 108, "H"), (40, 85, "L")]
        frame, pivots, positions, atr = swings(points, tail=125, extra=15)
        result = make_correction(pivots[1:], 1, frame, positions, fine_pivots=pivots)
        self.assertIsNotNone(result)
        self.assertEqual(result.kind, "abc")
        self.assertEqual(result.state, "breakout")

    def test_tp_ta_projects_equal_price_instead_of_full_first_leg(self):
        close = [0, 2, 4, 6, 8, 10, 9, 8, 7, 6]
        frame = pd.DataFrame({"close": close})
        p = [TrendPivot(0, 0, "L", 0), TrendPivot(5, 10, "H", 5), TrendPivot(9, 6, "L", 9)]
        result = tp_ta(frame, np.arange(10), *p, enabled=True)
        self.assertEqual(result, (2., 4, 2.))
        self.assertIsNone(tp_ta(frame, np.arange(10), *p, enabled=True, triangle=True))
        self.assertIsNone(tp_ta(frame, np.arange(10), *p, enabled=False))


class BreakConfirmationTests(unittest.TestCase):
    def fixture(self):
        frame = candles([(100, 101, 99, 100)] * 10)
        line = (TrendPivot(0, 98, "L", 0), TrendPivot(2, 100, "L", 2))
        # This particular line is rising at 1/bar; first close below is bar 3.
        frame.loc[3:, ["open", "high", "low", "close"]] = [101, 102, 99, 100]
        return frame, line

    def test_delay_is_break_plus_two_then_a_later_close(self):
        frame, line = self.fixture()
        positions = np.arange(len(frame))
        std = standard_candles(frame, positions)
        atr = np.ones(len(frame))
        wait = line_break_decision(1, line, 0, frame.iloc[:5], positions[:5], std[:5], atr[:5], structure_complete=True)
        self.assertEqual(wait.state, "delay_wait")
        ready = line_break_decision(1, line, 0, frame.iloc[:6], positions[:6], std[:6], atr[:6], structure_complete=True)
        self.assertEqual((ready.state, ready.delay_ready_at, ready.delay_level), ("delay_ready", 5, 99))
        frame.loc[6, ["open", "high", "low", "close"]] = [100, 100.5, 98, 98.5]
        done = line_break_decision(1, line, 0, frame.iloc[:7], positions[:7], std[:7], atr[:7], structure_complete=True)
        self.assertEqual((done.state, done.confirmed_at), ("delayed", 6))

    def test_wick_touch_and_incomplete_structure_are_not_automatic_reversal(self):
        frame = candles([(100, 101, 99, 100)] * 8)
        line = (TrendPivot(0, 98, "L", 0), TrendPivot(2, 98.2, "L", 2))
        frame.loc[7, "low"] = 95
        result = line_break_decision(1, line, 0, frame, np.arange(8), standard_candles(frame, np.arange(8)), np.ones(8))
        self.assertEqual(result.state, "wick_touch")

    def test_delay_can_be_filtered_and_a_navigation_break_does_not_claim_reversal(self):
        frame, line = self.fixture()
        frame.loc[6, ["open", "high", "low", "close"]] = [100, 104, 99, 103]
        result = line_break_decision(1, line, 0, frame, np.arange(10), standard_candles(frame, np.arange(10)), np.ones(10), structure_complete=True)
        self.assertEqual(result.state, "false_break")
        self.assertEqual(result.confirmed_at, 6)
        frame.loc[6, ["open", "high", "low", "close"]] = [100, 100.5, 98, 98.5]
        result = line_break_decision(1, line, 0, frame, np.arange(10), standard_candles(frame, np.arange(10)), np.ones(10), structure_complete=False)
        self.assertEqual(result.state, "line_warning")

    def test_near_standard_break_with_complete_structure_can_be_direct(self):
        frame, line = self.fixture()
        std = list(standard_candles(frame, np.arange(10)))
        std[3] = replace(std[3], standard=True, direction=-1)
        result = line_break_decision(1, line, 0, frame, np.arange(10), std, np.ones(10), structure_complete=True)
        self.assertEqual((result.state, result.confirmed_at), ("direct", 3))


class IntegratedThirdEditionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_new_fields_export_and_dialog_apply_render_with_legacy_wave_off(self):
        frame, positions, starts, ends = q_fixture([12, 14])
        frame["datetime"] = pd.bdate_range("2026-01-01", periods=len(frame))
        chart = CandleChart(); chart.set_series(frame, len(frame) - 1)
        chart.set_trend_visible(True); chart.set_wave_visible(False)
        chart.resize(1200, 700)
        analysis = chart._trend_analysis_cached(len(frame))
        self.assertTrue(analysis.structure)
        self.assertTrue(analysis.boundaries)
        self.assertTrue(analysis.levels)
        self.assertTrue(analysis.wave_labels)
        payload = json.loads(export_analysis(analysis))
        self.assertEqual(payload["source"]["file"], "3.pdf")
        self.assertEqual(payload["analysis"]["as_of_index"], len(frame) - 1)
        self.assertIn("分界点 A", analysis_html(analysis, frame))
        dialog = TrendIndicatorDialog(chart)
        dialog.numbers["confirmation_bars"].setValue(2)
        dialog.toggles["show_navigation"].setChecked(True)
        dialog._apply()
        chart.set_trend_settings(dialog.result_settings)
        self.assertEqual(chart.trend_settings["confirmation_bars"], 2)
        chart.show(); self.app.processEvents()
        self.assertFalse(chart.grab().isNull())
        dialog.close(); chart.close()

    def test_forming_and_future_bars_never_enter_formal_analysis(self):
        frame, _, _, ends = q_fixture([12, 14, 16])
        chart = CandleChart(); index = ends[1] + 1
        partial = dict(frame.iloc[index])
        chart.set_series(frame, index, partial)
        before = chart._trend_analysis_cached(index + 1)
        changed = frame.copy()
        changed.loc[index:, ["open", "high", "low", "close"]] = [999, 1000, 998, 999]
        chart.set_series(changed, index, partial)
        after = chart._trend_analysis_cached(index + 1)
        self.assertEqual(before, after)
        self.assertEqual(after.as_of_index, index - 1)
        for item in (*after.boundaries, *after.reversals, *after.wave_labels, *after.corrections):
            self.assertLessEqual(item.confirmed_at, after.as_of_index)

    def test_invalid_ohlc_and_settings_do_not_crash_or_create_fake_shapes(self):
        frame = candles([(1, 0, 2, float("nan")), (3, 1, 2, 3)])
        self.assertIsNone(analyze_trend(frame))
        s = TrendSettings.from_dict({"lookback": "bad", "confirmation_bars": -10, "show_waves": "false"})
        self.assertEqual(s.lookback, 1600)
        self.assertEqual(s.confirmation_bars, 1)
        self.assertTrue(s.show_waves)

    def test_live_62_percent_reversal_uses_an_already_known_leg(self):
        frame, _, _, _ = q_fixture([12])
        tail, previous = [], float(frame.close.iloc[-1])
        for close in (119., 117.8, 116.5, 115., 113., 111.):
            tail.append((previous, max(previous, close) + .05, min(previous, close) - .05, close))
            previous = close
        tail.append((117.2, 117.4, 110.068, 117.0))
        # Warmup is outside the manually constructed trend; it only supplies
        # the standard-K baseline. No future interval closes above the old top.
        frame = pd.concat([candles([(90, 90.1, 89.9, 90)] * 50), frame, candles(tail)], ignore_index=True)
        a = analyze_trend(frame)
        hits = [p for p in a.reversals if p.index == len(frame) - 1 and "62%" in p.context]
        self.assertTrue(hits, [(p.text, p.context) for p in a.reversals])
        self.assertEqual(hits[0].confirmed_at, len(frame) - 1)

    def test_whole_trend_target_is_measured_from_wave_one_origin_and_is_symmetric(self):
        frame, _, _, _ = q_fixture([12, 14])
        a = analyze_trend(frame)
        target = next(p for p in a.levels if p.label == "全趋势目标 3.236×H1")
        self.assertAlmostEqual(target.price, 155.2172)
        self.assertIsNotNone(a.angle_slope)
        reflected = pd.DataFrame({"open": -frame.open, "high": -frame.low, "low": -frame.high, "close": -frame.close})
        b = analyze_trend(reflected)
        down = next(p for p in b.levels if p.label == "全趋势目标 3.236×H1")
        self.assertAlmostEqual(down.price, -target.price)
        self.assertAlmostEqual(b.angle_slope, -a.angle_slope)


if __name__ == "__main__":
    unittest.main()
