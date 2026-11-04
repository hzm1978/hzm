import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd
from PySide6.QtWidgets import QApplication

from tq_workbench.technical.trend_analysis import analyze_trend, _book_episode
from tq_workbench.technical.trend_intervals import find_intervals
from tq_workbench.technical.trend_projections import reverse_fibonacci
from tq_workbench.technical.trend_types import TrendPivot
from test_subwave_hierarchy import daily_five


def ag_whipsaw():
    # Constructed AG-scale daily bars, not a transcription of the user's data.
    rng = np.random.default_rng(13)
    close = 3500+np.cumsum(rng.normal(0, 20, 60))
    opening = np.r_[close[0], close[:-1]]
    return pd.DataFrame({"datetime": pd.bdate_range("2014-05-01", periods=60),
        "open": opening, "high": np.maximum(opening, close)+rng.uniform(0, 25, 60),
        "low": np.minimum(opening, close)-rng.uniform(0, 25, 60), "close": close})


class ProjectionTests(unittest.TestCase):
    def test_late_first_close_rebases_an_older_outer_interval_without_empty_argmin(self):
        frame = ag_whipsaw()
        pos = np.arange(len(frame))
        sign, origin = _book_episode(find_intervals(frame, pos), frame, pos)
        self.assertIn(sign, (-1, 1))
        self.assertIsNotNone(origin)
        self.assertLessEqual(origin.confirmed_at, pos[-1])
        for end in range(8, len(frame)+1):
            result = analyze_trend(frame.iloc[:end])
            self.assertIsNotNone(result)
            self.assertEqual(result.as_of_index, end-1)

    def test_reverse_ruler_matches_3915_to_3129_and_mirrors_a_bear_leg(self):
        start = TrendPivot(5, 3915, "H", 6)
        base = TrendPivot(10, 3129, "L", 11)
        up = reverse_fibonacci(start, base, 1, 11)
        self.assertEqual([round(p.price) for p in up.levels], [3129, 3279, 3429, 3522, 3615, 3915])
        down = reverse_fibonacci(TrendPivot(5, -3915, "L", 6), TrendPivot(10, -3129, "H", 11), -1, 11)
        self.assertEqual([p.price for p in down.levels], [-p.price for p in up.levels])

    def test_targets_and_previous_arrivals_use_the_same_child_degree_and_known_basis(self):
        frame = daily_five()
        a = analyze_trend(frame)
        group = a.wave_projections[0]
        self.assertEqual((group.degree, group.direction, group.wave), ("子", -1, 5))
        self.assertEqual((group.reverse.start.index, group.reverse.base.index), (77, 80))
        height = group.first_origin.price-group.first_tip.price
        current = [t for t in group.targets if t.wave == 5 and t.kind == "main"]
        self.assertEqual([t.ratio for t in current], [1.208, 1.618, 2., 2.382, 2.618, 3.])
        for t in current:
            self.assertAlmostEqual(t.price, group.active_origin.price-t.ratio*height)
            self.assertLessEqual(t.confirmed_at, a.as_of_index)
            if t.hit_index is not None:
                self.assertLessEqual(t.hit_index, a.as_of_index)
                self.assertLessEqual(frame.low.iloc[t.hit_index], t.price)
                self.assertGreaterEqual(t.hit_confirmed_at, t.confirmed_at)
        self.assertTrue(any("子3实达" in p.text for p in group.historical_arrivals))
        whole = [t for t in group.targets if t.kind == "whole"]
        self.assertAlmostEqual(whole[0].price, group.first_origin.price-3.236*height)

    def test_one_q_three_child_structure_is_shown_while_coupling_is_pending(self):
        a = analyze_trend(daily_five().iloc[:77])
        self.assertTrue(a.subwaves)
        series = a.subwaves[0]
        self.assertEqual(len(series.points), 4)
        self.assertEqual(len(series.intervals), 1)
        self.assertTrue(series.retracements)
        self.assertTrue(series.boundaries)
        self.assertTrue(series.corrections)

    def test_a_later_parent_interpretation_does_not_cut_a_completed_child_in_half(self):
        frame = daily_five()
        x = np.arange(91, 120)
        close = np.interp(x, [90, 96, 99, 102, 106, 109, 112, 116, 119],
                          [148, 165, 156, 167, 178, 169, 180, 188, 186])
        opening = np.r_[frame.close.iloc[-1], close[:-1]]
        tail = pd.DataFrame({"datetime": pd.bdate_range(frame.datetime.iloc[-1]+pd.Timedelta(days=1), periods=len(x)),
            "open": opening, "high": np.maximum(opening, close)+.05,
            "low": np.minimum(opening, close)-.05, "close": close})
        analysis = analyze_trend(pd.concat([frame, tail], ignore_index=True))
        complete = [s for s in analysis.subwaves if len(s.points) == 6]
        self.assertTrue(any(s.direction == -1 and [p.index for p in s.points] == [61, 66, 69, 77, 80, 88] for s in complete))
        self.assertTrue(any(s.direction == 1 and s.points[0].index == 88 for s in complete))
        historical = next(g for g in analysis.wave_projections if g.historical and g.direction == -1)
        self.assertTrue(any("子5实达" in p.text for p in historical.historical_arrivals))


class ProjectionChartTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_analysis_exception_is_reported_without_escaping_paint_or_dialog(self):
        from tq_workbench.technical.ui.chart_widget import CandleChart
        from tq_workbench.technical.ui.trend_dialog import TrendIndicatorDialog
        chart = CandleChart(); frame = ag_whipsaw()
        chart.set_series(frame, len(frame)-1); chart.set_trend_visible(True)
        chart.resize(900, 500)
        with patch("tq_workbench.technical.ui.chart_widget.analyze_trend", side_effect=ValueError("regression-test")):
            self.assertIsNone(chart._trend_analysis_cached(len(frame)))
            chart.show(); self.app.processEvents()
            self.assertFalse(chart.grab().isNull())
            dialog = TrendIndicatorDialog(chart)
            self.assertIn("regression-test", chart._trend_error)
            dialog.close(); dialog.deleteLater()
        chart.set_trend_settings({"confirmation_bars": 2})
        self.assertIsNotNone(chart._trend_analysis_cached(len(frame)))
        self.assertEqual(chart._trend_error, "")
        chart.close(); chart.deleteLater(); self.app.processEvents()

    def test_intrabar_copies_reuse_analysis_but_historical_price_corrections_invalidate_it(self):
        from tq_workbench.technical.ui.chart_widget import CandleChart
        chart = CandleChart(); frame = daily_five()
        index = len(frame)-1
        partial = dict(frame.iloc[index])
        chart.set_series(frame, index, partial)
        with patch("tq_workbench.technical.ui.chart_widget.analyze_trend", wraps=analyze_trend) as called:
            before = chart._trend_analysis_cached(index+1)
            changed = frame.copy(); partial["high"] += 500
            chart.set_series(changed, index, partial)
            self.assertEqual(before, chart._trend_analysis_cached(index+1))
            self.assertEqual(called.call_count, 1)
            changed.loc[10, "high"] += 1
            chart.set_series(changed, index, partial)
            chart._trend_analysis_cached(index+1)
            self.assertEqual(called.call_count, 2)
        chart.close(); chart.deleteLater(); self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
