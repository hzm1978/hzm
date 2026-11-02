import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pandas as pd
from PySide6.QtWidgets import QApplication

from futures_replay.trend_analysis import (
    TrendPivot, _line_value, _select_external_channel,
    _select_valid_trendline, analyze_trend,
)
from futures_replay.trend_patterns import (
    book_candle_patterns, correction_patterns, key_reversals, retracement_labels,
)
from futures_replay.ui.chart_widget import CandleChart


def swings(points, tail=None, extra=5):
    """Closed OHLC fixture with explicit confirmed-pivot construction points."""
    length = points[-1][0] + extra + 1
    positions = np.arange(length)
    anchors = [p[0] for p in points]
    values = [p[1] for p in points]
    if tail is not None:
        anchors.append(length - 1)
        values.append(tail)
    curve = np.interp(positions, anchors, values)
    frame = pd.DataFrame({
        "datetime": pd.date_range("2026-01-01", periods=length),
        "open": curve - 0.2, "high": curve + 0.5,
        "low": curve - 0.5, "close": curve + 0.2,
        "volume": 10.0,
    })
    for index, price, kind in points:
        if kind == "H":
            frame.loc[index, ["open", "high", "low", "close"]] = [price - 0.4, price, price - 0.8, price - 0.2]
        else:
            frame.loc[index, ["open", "high", "low", "close"]] = [price + 0.2, price + 0.8, price, price + 0.4]
    pivots = tuple(TrendPivot(index, price, kind, index + 2) for index, price, kind in points)
    return frame, pivots, positions, np.full(length, 2.0)


class BookConstructionTests(unittest.TestCase):
    def test_external_highs_shift_through_intervening_low_with_independent_slope(self):
        frame, pivots, positions, atr = swings([
            (0, 90, "L"), (10, 110, "H"), (20, 100, "L"),
            (30, 130, "H"), (40, 120, "L"), (50, 145, "H"),
        ], tail=138, extra=10)
        # Use exact candle extremes at construction points, as in book figures.
        high, low = frame.high.to_numpy(copy=True), frame.low.to_numpy(copy=True)
        for p in pivots:
            if p.kind == "H": high[p.index] = p.price
            else: low[p.index] = p.price
        mapping = {int(i): int(i) for i in positions}
        exterior, anchor = _select_external_channel("up", pivots, high, low, atr, mapping)
        self.assertEqual(tuple(p.index for p in exterior), (30, 50))
        self.assertEqual(anchor.index, 40)
        slope = _line_value(*exterior, 60)[1]
        self.assertAlmostEqual(slope, 0.75)
        shift = anchor.price - _line_value(*exterior, anchor.index)[0]
        self.assertAlmostEqual(_line_value(*exterior, anchor.index)[0] + shift, anchor.price)
        self.assertAlmostEqual(_line_value(*exterior, 60)[0] + shift, 135)
        main = _select_valid_trendline("up", pivots, high, low, atr, mapping, positions, 60, min_anchor_bars=6)
        self.assertIsNotNone(main)
        self.assertNotAlmostEqual(_line_value(*main, 60)[1], slope)

    def test_second_anchor_after_highest_point_is_rejected(self):
        frame, pivots, positions, atr = swings([
            (0, 90, "L"), (10, 130, "H"), (20, 100, "L"),
            (30, 120, "H"), (40, 110, "L"),
        ])
        mapping = {int(i): int(i) for i in positions}
        line = _select_valid_trendline("up", pivots, frame.high.to_numpy(), frame.low.to_numpy(),
                                      atr, mapping, positions, len(frame) - 1, min_anchor_bars=6)
        self.assertIsNone(line)

    def test_confirmed_double_top_neckline_exception(self):
        frame, pivots, positions, atr = swings([
            (0, 90, "L"), (10, 130, "H"), (20, 110, "L"), (30, 130, "H"),
        ])
        mapping = {int(i): int(i) for i in positions}
        line = _select_valid_trendline("up", pivots, frame.high.to_numpy(), frame.low.to_numpy(),
                                      atr, mapping, positions, len(frame) - 1, min_anchor_bars=6)
        self.assertIsNotNone(line)
        self.assertEqual(line[1].index, 20)

    def test_falling_exterior_is_shifted_through_intervening_high(self):
        frame, pivots, positions, atr = swings([
            (0, 110, "H"), (10, 90, "L"), (20, 100, "H"),
            (30, 70, "L"), (40, 80, "H"), (50, 55, "L"),
        ], tail=62, extra=10)
        mapping = {int(i): int(i) for i in positions}
        exterior, anchor = _select_external_channel("down", pivots, frame.high.to_numpy(), frame.low.to_numpy(), atr, mapping)
        self.assertEqual(tuple(p.index for p in exterior), (30, 50))
        self.assertEqual(anchor.index, 40)
        self.assertAlmostEqual(_line_value(*exterior, 60)[1], -0.75)
        shift = anchor.price - _line_value(*exterior, anchor.index)[0]
        self.assertAlmostEqual(_line_value(*exterior, 60)[0] + shift, 65)

    def test_trendline_does_not_tolerate_a_small_intervening_price_cross(self):
        frame, pivots, positions, atr = swings([
            (0, 90, "L"), (10, 130, "H"), (20, 110, "L"), (30, 145, "H"),
        ])
        low = frame.low.to_numpy(copy=True)
        low[15] = 104.99  # 0.01 below the 90 -> 110 construction line.
        mapping = {int(i): int(i) for i in positions}
        line = _select_valid_trendline("up", pivots, frame.high.to_numpy(), low, atr,
                                      mapping, positions, len(frame) - 1, min_anchor_bars=6)
        self.assertIsNone(line)

    def test_retracement_uses_previous_leg_and_keeps_history(self):
        pivots = tuple(TrendPivot(i * 10, price, kind, i * 10 + 3) for i, (price, kind) in enumerate([
            (100, "L"), (120, "H"), (110, "L"), (130, "H"), (116, "L"),
        ]))
        labels = retracement_labels(pivots)
        self.assertEqual([round(p.percent, 2) for p in labels], [50, 100, 70])
        self.assertEqual([p.context for p in labels], ["retracement", "extension", "retracement"])
        self.assertEqual([p.index for p in labels], [20, 30, 40])
        self.assertEqual(labels[-1].confirmed_at, 43)


class BookCorrectionTests(unittest.TestCase):
    def find(self, points, kind, tail=None):
        frame, pivots, positions, atr = swings(points, tail=tail)
        patterns = correction_patterns(frame, pivots, positions, atr)
        hits = [p for p in patterns if p.kind == kind]
        self.assertTrue(hits, f"Expected {kind}, got {[p.kind for p in patterns]}")
        return hits[-1]

    def test_simple_standard_abc_and_contracting_abc_are_distinguished(self):
        self.find([(0, 80, "L"), (10, 120, "H"), (20, 100, "L")], "simple", tail=104)
        self.find([(0, 80, "L"), (10, 120, "H"), (20, 100, "L"), (30, 110, "H"), (40, 94, "L")], "abc", tail=98)
        self.find([(0, 80, "L"), (10, 120, "H"), (20, 100, "L"), (30, 119, "H"), (40, 102, "L")], "abc_contracting", tail=106)

    def test_third_edition_triangle_and_abcde_families(self):
        fixtures = [
            ("triangle", [120, 100, 117, 103, 114, 106], 109),
            ("expanding_triangle", [110, 100, 115, 95, 120, 90], 105),
            ("abcde_trending", [120, 110, 118, 108, 116, 106], 110),
            ("abcde_sideways", [120, 100, 120, 100, 120, 100], 105),
        ]
        for kind, prices, tail in fixtures:
            with self.subTest(kind=kind):
                points = [(0, 60, "L")] + [(10 + i * 10, price, "H" if i % 2 == 0 else "L") for i, price in enumerate(prices)]
                item = self.find(points, kind, tail)
                self.assertLessEqual(item.confirmed_at, points[-1][0] + 5)

    def test_triangle_candidate_and_two_breakout_directions(self):
        points = [(0, 60, "L")] + [(10 + i * 10, price, "H" if i % 2 == 0 else "L") for i, price in enumerate([120, 100, 117, 103, 114, 106])]
        self.assertEqual(self.find(points, "triangle", tail=109).state, "candidate")
        self.assertEqual(self.find(points, "triangle", tail=125).state, "breakout")
        self.assertEqual(self.find(points, "triangle", tail=90).state, "opposite_break")

    def test_rising_impulses_are_not_bear_trend_abc_corrections(self):
        frame, pivots, positions, atr = swings([
            (0, 100, "L"), (10, 120, "H"), (20, 110, "L"),
            (30, 135, "H"), (40, 120, "L"), (50, 145, "H"), (60, 130, "L"),
        ])
        patterns = correction_patterns(frame, pivots, positions, atr)
        self.assertFalse(any(item.kind in {"abc", "flat"} for item in patterns))
        self.assertTrue(all(item.direction == 1 for item in patterns))
        # A cropped history may start at the first HIGH rather than a confirmed
        # LOW. The subsequent rising trend must not become a bearish ABC.
        cropped = correction_patterns(frame, pivots[1:], positions, atr)
        self.assertFalse(any(item.kind in {"abc", "flat"} for item in cropped))

    def test_proposed_simple_end_is_invalidated_by_a_later_close(self):
        frame, pivots, positions, atr = swings([(0, 80, "L"), (10, 120, "H"), (20, 100, "L")], tail=104)
        frame.loc[24, ["open", "high", "low", "close"]] = [90, 91, 69, 70]
        patterns = correction_patterns(frame, pivots, positions, atr)
        item = next(p for p in patterns if p.kind == "simple")
        self.assertEqual(item.state, "opposite_break")
        self.assertEqual(item.confirmed_at, 24)


class BookReplayChartTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_trend_checkbox_alone_has_all_annotations_and_ignores_future_ohlc(self):
        frame, _, _, _ = swings([
            (0, 100, "L"), (10, 120, "H"), (20, 110, "L"), (30, 135, "H"),
            (40, 120, "L"), (50, 145, "H"), (60, 130, "L"), (70, 155, "H"), (80, 140, "L"),
        ], extra=10)
        chart = CandleChart()
        chart.set_wave_visible(False)
        chart.set_trend_visible(True)
        partial = {name: float(frame.iloc[65][name]) for name in ("open", "high", "low", "close", "volume")}
        chart.set_series(frame, 65, partial)
        original = chart._trend_analysis_cached(66)
        self.assertGreaterEqual(len(original.pivots), 4)
        self.assertTrue(original.retracements)
        self.assertIsNotNone(original.trendline)
        self.assertIsNotNone(original.external_line)
        self.assertIsNotNone(original.pivot_line_anchor)
        self.assertTrue(all(p.confirmed_at <= original.as_of_index for p in original.pivots))
        self.assertTrue(all(p.confirmed_at <= original.as_of_index for p in original.corrections))
        future = frame.copy()
        future.loc[65:, ["open", "high", "low", "close"]] = [9999, 10000, 9998, 9999]
        chart.set_series(future, 65, partial)
        self.assertEqual(chart._trend_analysis_cached(66), original)
        chart.resize(1100, 620)
        chart.show()
        self.app.processEvents()
        self.assertFalse(chart.grab().isNull())
        chart.close()

    def test_flat_market_does_not_force_lines_or_correction_labels(self):
        frame = pd.DataFrame({name: [value] * 40 for name, value in [("open", 100), ("high", 101), ("low", 99), ("close", 100)]})
        analysis = analyze_trend(frame)
        self.assertEqual(analysis.pivots, ())
        self.assertIsNone(analysis.trendline)
        self.assertEqual(analysis.reversals, ())
        self.assertEqual(analysis.corrections, ())


if __name__ == "__main__":
    unittest.main()
