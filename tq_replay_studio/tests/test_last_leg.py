import unittest
from unittest.mock import patch
import numpy as np
import pandas as pd
from PySide6.QtCore import QCoreApplication,QEvent
from PySide6.QtWidgets import QApplication

from tq_workbench.technical.trend_analysis import analyze_trend
from tq_workbench.technical.trend_last_leg import price_text
from tq_workbench.technical.ui.chart_widget import CandleChart
from test_subwave_hierarchy import daily_five


def sideways_frame():
    frame = daily_five().iloc[:89].copy()
    values = np.array([149,153,148,152,149,153,148,152,149,153,150,149,153,150.])
    tail = pd.DataFrame(dict(datetime=pd.bdate_range(frame.datetime.iloc[-1]+pd.Timedelta(days=1),periods=len(values)),
        open=values-.1,close=values,high=values+.2,low=values-.2))
    return pd.concat([frame,tail],ignore_index=True)


class LastLegTests(unittest.TestCase):
    def test_downward_running_main_leg_has_only_three_forward_book_targets(self):
        a = analyze_trend(daily_five().iloc[:88]);leg = a.last_leg
        self.assertEqual((leg.mode,leg.direction,leg.leg_direction),("targets",-1,-1))
        self.assertIsNone(leg.fibonacci)
        self.assertEqual(len(leg.targets),3)
        self.assertTrue(all(t.price < leg.tip.price for t in leg.targets))
        self.assertEqual([t.price for t in leg.targets],sorted((t.price for t in leg.targets),reverse=True))
        for target in leg.targets:
            self.assertLessEqual(target.confirmed_at,a.as_of_index)
            self.assertAlmostEqual(target.price,target.origin.price-target.ratio*target.reference_height)

    def test_upward_adjustment_uses_bottom_to_previous_top_and_no_profit_targets(self):
        a = analyze_trend(daily_five());leg = a.last_leg
        self.assertEqual((leg.mode,leg.market,leg.direction,leg.leg_direction),("fibonacci","correction",-1,1))
        self.assertFalse(leg.targets);self.assertFalse(leg.all_targets)
        self.assertEqual((leg.fibonacci.start.index,leg.fibonacci.base.index),(80,88))
        self.assertEqual(leg.fibonacci.levels[0].price,leg.origin.price)
        self.assertEqual(leg.fibonacci.levels[-1].price,leg.fibonacci.start.price)
        self.assertTrue(any(p.index == 88 for g in a.wave_projections for p in g.historical_arrivals))

    def test_rulers_are_symmetric_for_an_uptrend(self):
        for length in (88,91):
            down = analyze_trend(daily_five().iloc[:length]).last_leg
            up = analyze_trend(daily_five(reflect=True).iloc[:length]).last_leg
            self.assertEqual(up.mode,down.mode)
            self.assertEqual(up.leg_direction,-down.leg_direction)
            prices1 = [p.price for p in down.targets] if down.mode == "targets" else [p.price for p in down.fibonacci.levels]
            prices2 = [p.price for p in up.targets] if up.mode == "targets" else [p.price for p in up.fibonacci.levels]
            np.testing.assert_allclose(prices2,300-np.array(prices1))

    def test_sideways_adjustment_never_draws_trend_profit_targets(self):
        leg = analyze_trend(sideways_frame()).last_leg
        self.assertEqual((leg.market,leg.mode),("sideways","fibonacci"))
        self.assertFalse(leg.targets);self.assertIsNotNone(leg.fibonacci)

    def test_recent_turn_patterns_keep_observation_status_and_real_candle_time(self):
        a = analyze_trend(daily_five());leg = a.last_leg
        self.assertTrue(a.turn_observations)
        self.assertLessEqual(len(a.turn_observations),4)
        self.assertTrue(any(not p.qualified for p in a.turn_observations))
        for item in a.turn_observations:
            self.assertLessEqual(item.confirmed_at,a.as_of_index)
            self.assertIn("转折观察",item.text)
            self.assertTrue(any(item.context == f"转折#{p.index}" for p in leg.turns))
            self.assertTrue(any(p.index == item.index and p.price == item.price for p in a.candle_patterns))
            self.assertNotIn(item,a.reversals)

    def test_exact_endpoint_price_is_not_rounded_to_six_significant_digits(self):
        self.assertEqual(price_text(362880.5),"362880.5")
        self.assertEqual(price_text(3129.000000000002),"3129")


class LastLegChartTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def render(self,frame):
        chart = CandleChart();chart.resize(1100,650);chart.visible_bars=40
        chart.show_ma=False;chart.show_volume=False;chart.set_series(frame,len(frame)-1)
        chart.set_trend_settings(dict(show_reverse_fibonacci=True,show_main_wave_targets=True,show_targets=True))
        chart.set_trend_visible(True);chart.show();self.app.processEvents()
        self.assertEqual(chart._trend_error,"")
        return chart

    def finish(self,chart):
        chart.close();chart.deleteLater()
        QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete);self.app.processEvents()

    def test_running_main_leg_draws_three_horizontal_price_segments_not_a_table(self):
        chart = self.render(daily_five().iloc[:88])
        try:
            segments = chart._trend_price_segments
            self.assertEqual(len(segments),3)
            self.assertEqual({p['kind'] for p in segments},{'target'})
            for item in segments:
                self.assertIn(price_text(item['price']),item['text'])
                self.assertAlmostEqual(item['y'],chart._price_y(item['price']))
                self.assertAlmostEqual(item['label_y'],item['y'])
                self.assertLess(item['x1'],item['x2'])
        finally:
            self.finish(chart)

    def test_adjustment_draws_standard_retracement_levels_and_one_price_per_historical_endpoint(self):
        chart = self.render(daily_five())
        try:
            self.assertEqual(len(chart._trend_price_segments),7)
            self.assertEqual({p['kind'] for p in chart._trend_price_segments},{'fibonacci'})
            for item in chart._trend_price_segments:
                self.assertAlmostEqual(item['y'],item['label_y'])
            original = chart._draw_wave_tag;drawn=[]
            def record(*args,**kwargs):
                result = original(*args,**kwargs)
                if result is not None: drawn.append(args[3])
                return result
            # A MagicMock would retain its QPainter arguments past grab(),
            # leaving a painter alive after the native pixmap is destroyed.
            with patch.object(chart,'_draw_wave_tag',new=record):
                chart.grab()
            self.assertIn('144.95',drawn)
            self.assertEqual(drawn.count('144.95'),1)
            self.assertFalse(any('实达' in text or ('H1' in text and '已达' in text) for text in drawn))
        finally:
            self.finish(chart)


if __name__ == '__main__':
    unittest.main()
