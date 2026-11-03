"""Generic regression paths, not a transcription of the user's screenshots."""
import unittest
from dataclasses import replace
from unittest.mock import patch
import numpy as np
import pandas as pd
from PySide6.QtCore import QCoreApplication,QEvent
from PySide6.QtWidgets import QApplication

from futures_replay.trend_analysis import analyze_trend
from futures_replay.trend_projections import retracement_fibonacci
from futures_replay.trend_types import TrendPivot
from futures_replay.ui.chart_widget import CandleChart


def complex_decline(*,reflect=False,grandchildren=True,tail=True):
    anchors=[(0,4800),(5,4900),(12,4830),(20,4950),(30,5000),
        (60,4500),(70,4650),(80,4470),(85,4520),(98,4200),(104,4270),
        (120,3900),(132,3918.71),(142,3800),(148,3880),(162,3480),
        (168,3550),(185,3175.45)]
    if grandchildren:
        anchors += [(88,4445),(90,4460),(94,4300),(96,4330)]
    if tail:
        anchors += [(191,3300),(194,3240),(201,3420),(204,3360),(210,3490)]
    anchors.sort();x=np.arange(anchors[-1][0]+1)
    close=np.interp(x,*zip(*anchors));opening=np.r_[close[0]-1,close[:-1]]
    frame=pd.DataFrame(dict(datetime=pd.bdate_range('2024-01-01',periods=len(x)),
        open=opening,high=np.maximum(opening,close)+1,low=np.minimum(opening,close)-1,close=close))
    frame[['open','high','low','close']]=frame[['open','high','low','close']].round(2)
    if reflect:
        frame=frame.assign(open=10000-frame.open,high=10000-frame.low,
                           low=10000-frame.high,close=10000-frame.close)
    return frame


class HierarchyContextTests(unittest.TestCase):
    def test_a_small_opposing_five_keeps_the_parent_downtrend_and_whole_previous_leg_ruler(self):
        a=analyze_trend(complex_decline());leg=a.last_leg
        self.assertEqual((leg.degree,leg.direction,leg.leg_direction,leg.mode),('主',-1,1,'fibonacci'))
        self.assertTrue(any(s.direction==1 and len(s.points)==6 for s in a.subwaves))
        self.assertFalse(leg.targets)
        self.assertEqual((leg.fibonacci.start.index,leg.fibonacci.base.index),(133,186))
        self.assertEqual((leg.fibonacci.start.price,leg.fibonacci.base.price),(3919.71,3174.45))

    def test_retracements_in_the_small_rebound_do_not_become_a_new_main_wave(self):
        frame=complex_decline()
        for end in (196,205,211):
            with self.subTest(end=end):
                leg=analyze_trend(frame.iloc[:end]).last_leg
                self.assertEqual((leg.direction,leg.mode),(-1,'fibonacci'))
                self.assertAlmostEqual(leg.fibonacci.start.price,3919.71)
                self.assertAlmostEqual(leg.fibonacci.base.price,3174.45)

    def test_the_ruler_matches_the_reference_percentages_and_prices(self):
        ruler=retracement_fibonacci(TrendPivot(10,3919.71,'H',11),TrendPivot(50,3174.45,'L',51),1,51)
        self.assertEqual([p.label for p in ruler.levels],['0%','23.6%','38.2%','50%','61.8%','78.6%','100%'])
        self.assertEqual([round(p.price,2) for p in ruler.levels],
                         [3174.45,3350.33,3459.14,3547.08,3635.02,3760.22,3919.71])

    def test_completed_internal_fives_are_retained_in_both_long_parent_legs(self):
        a=analyze_trend(complex_decline())
        for scope in ((71,120),(133,186)):
            with self.subTest(scope=scope):
                s=next(s for s in a.subwaves if not s.alternative and s.depth==1 and s.owner==scope)
                self.assertEqual(len(s.points),6)
                self.assertEqual(len(s.intervals),2)
                self.assertEqual(len(s.retracements),2)
                self.assertEqual(len(s.boundaries),2)
                self.assertEqual(len(s.corrections),2)
                self.assertIsNotNone(s.trendline);self.assertIsNotNone(s.external_line)
                self.assertTrue(all(p.owner==(s.points[0].index,s.points[-1].index) for p in s.corrections))

    def test_grandchildren_can_overlap_the_parent_and_have_explicit_owner_and_labels(self):
        a=analyze_trend(complex_decline())
        children=[s for s in a.subwaves if s.depth==2 and not s.alternative]
        self.assertTrue(children)
        s=next(s for s in children if s.direction==-1 and len(s.points)==6)
        parent=next(p for p in a.subwaves if p.depth==1 and (p.points[0].index,p.points[-1].index)==s.owner)
        self.assertTrue(any(l.index <= s.points[0].index and s.points[-1].index <= r.index
                            for l,r in zip(parent.points,parent.points[1:])))
        self.assertTrue(all(p.text.startswith('孙') for p in s.labels))
        self.assertTrue(all(p.label.startswith('孙A') for p in s.boundaries))

    def test_old_fifth_candidate_is_invalid_after_a_close_through_its_fourth_point(self):
        a=analyze_trend(complex_decline())
        frame=complex_decline()
        for s in a.subwaves:
            if s.state=='fifth_pending':
                fourth,fifth=s.points[-2:]
                after=frame.iloc[fifth.index+1:a.as_of_index+1]
                self.assertFalse((s.direction*(after.close-fourth.price)<0).any())

    def test_prices_and_timeframe_do_not_change_the_interval_hierarchy(self):
        frame=complex_decline();a=analyze_trend(frame)
        changed=frame.copy()
        changed[['open','high','low','close']]=changed[['open','high','low','close']]*17+24600
        changed['datetime']=pd.date_range('2026-01-01',periods=len(changed),freq='15min')
        b=analyze_trend(changed)
        self.assertEqual(a.last_leg.mode,b.last_leg.mode)
        self.assertEqual(a.last_leg.fibonacci.start.index,b.last_leg.fibonacci.start.index)
        self.assertEqual([(s.depth,s.direction,[q.bars for q in s.intervals]) for s in a.subwaves],
                         [(s.depth,s.direction,[q.bars for q in s.intervals]) for s in b.subwaves])

    def test_an_uptrend_with_a_downward_adjustment_is_the_exact_mirror(self):
        down=analyze_trend(complex_decline());up=analyze_trend(complex_decline(reflect=True))
        self.assertEqual((up.last_leg.direction,up.last_leg.leg_direction,up.last_leg.mode),(1,-1,'fibonacci'))
        np.testing.assert_allclose([p.price for p in up.last_leg.fibonacci.levels],
                                   10000-np.array([p.price for p in down.last_leg.fibonacci.levels]))

    def test_later_prices_cannot_change_a_closed_prefix_context(self):
        frame=complex_decline();a=analyze_trend(frame.iloc[:196])
        changed=frame.copy();changed.loc[196:,['open','high','low','close']]=[7000,8000,6000,7500]
        b=analyze_trend(changed.iloc[:196]);self.assertEqual(a.last_leg,b.last_leg)
        self.assertEqual(a.subwaves,b.subwaves)

    def test_a_new_lower_low_uses_the_current_leg_instead_of_the_closed_historical_one(self):
        frame=complex_decline();close=np.array([3450,3380,3300,3200,3100.])
        opening=np.r_[frame.close.iloc[-1],close[:-1]]
        tail=pd.DataFrame(dict(datetime=pd.bdate_range(frame.datetime.iloc[-1]+pd.Timedelta(days=1),periods=len(close)),
            open=opening,high=np.maximum(opening,close)+1,low=np.minimum(opening,close)-1,close=close))
        a=analyze_trend(pd.concat([frame,tail],ignore_index=True))
        self.assertEqual((a.last_leg.mode,a.last_leg.direction),('targets',-1))
        self.assertIsNone(a.last_leg.fibonacci)
        self.assertEqual((a.last_leg.origin.index,a.last_leg.tip.index),(211,215))
        self.assertEqual(a.last_leg.tip.price,3099)
        self.assertFalse(a.structure.complete)
        self.assertEqual(a.structure.kind,'extension_pending')
        self.assertFalse(any('浪8' in p.text or '浪9' in p.text for p in a.wave_labels))

    def test_actual_first_equal_touches_preserve_rays_without_moving_a_or_q(self):
        a=analyze_trend(complex_decline())
        s=next(s for s in a.subwaves if s.depth==1 and s.owner==(133,186))
        self.assertEqual([p.index for p in s.points],[133,143,149,163,169,186])
        self.assertEqual([p.index for p in s.boundaries],[149,169])
        self.assertIsNotNone(s.external_line)
        self.assertTrue(any(p.index in (142,162,185) for p in s.external_line))

    def test_valid_open_fifth_is_removed_when_the_rebound_closes_above_its_fourth(self):
        from futures_replay.trend_hierarchy import _pending_fifth
        from test_subwave_hierarchy import daily_five
        frame=daily_five().iloc[:82];a=analyze_trend(frame);s=a.subwaves[0]
        points=s.points[:4];pos=np.arange(len(frame));extreme=frame.low.to_numpy()
        before=_pending_fifth(points,s.structure,pos,a.navigation_pivots,extreme,points[-2].index,frame.close.to_numpy())
        self.assertIsNotNone(before)
        row=dict(frame.iloc[-1]);row.update(open=165.,high=176.,low=164.,close=175.)
        changed=pd.concat([frame,pd.DataFrame([row])],ignore_index=True)
        after=_pending_fifth(points,s.structure,np.arange(len(changed)),a.navigation_pivots,
            changed.low.to_numpy(),points[-2].index,changed.close.to_numpy())
        self.assertIsNone(after)

    def test_resolved_c_or_e_rechecks_its_own_price_and_requires_a_following_bar(self):
        import futures_replay.trend_hierarchy as hierarchy
        from test_subwave_hierarchy import daily_five
        frame=daily_five().iloc[:82];a=analyze_trend(frame);s=a.subwaves[0]
        resolver=hierarchy.correction_for_interval
        for end,closing,expected_fifth in ((81,164.5,True),(81,168.,False),(82,168.,False)):
            with self.subTest(end=end,closing=closing):
                row=dict(frame.iloc[-1]);row.update(datetime=frame.datetime.iloc[-1]+pd.offsets.BDay(),
                    open=165.,high=169.,low=164.,close=closing)
                changed=pd.concat([frame,pd.DataFrame([row])],ignore_index=True)
                # Isolate consumption of the structural c/e supplied by the
                # correction resolver; real triangle classification has its
                # own tests. This endpoint is inside the older maximum A.
                resolved=replace(s.corrections[-1],state='breakout',
                    end_point=TrendPivot(end,165.05,'H',82),confirmed_at=82)
                def resolve(item,*args,**kwargs):
                    return resolved if item.state=='running' else resolver(item,*args,**kwargs)
                with patch.object(hierarchy,'correction_for_interval',new=resolve):
                    found=hierarchy._series(s.points[0],s.structure,changed,
                        np.arange(len(changed)),a.navigation_pivots,np.ones(len(changed)))
                self.assertIsNotNone(found)
                self.assertEqual(found.state=='fifth_pending',expected_fifth)
                self.assertEqual(len(found.points),6 if expected_fifth else 4)
                self.assertEqual(found.pending_interval is not None,expected_fifth)


class HierarchyChartTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def test_full_decline_annotations_and_previous_leg_lines_are_actually_drawn(self):
        chart=CandleChart();chart.resize(1600,850);chart.visible_bars=190
        chart.show_ma=False;chart.show_volume=False
        frame=complex_decline();chart.set_series(frame,len(frame)-1);chart.set_trend_visible(True)
        drawn=[];original=chart._draw_wave_tag
        def trace(*args,**kwargs):
            rect=original(*args,**kwargs)
            if rect is not None:drawn.append((args[1],args[3]))
            return rect
        try:
            with patch.object(chart,'_draw_wave_tag',new=trace):
                chart.show();self.app.processEvents();picture=chart.grab()
            self.assertFalse(picture.isNull());self.assertEqual(chart._trend_error,'')
            segments=chart._trend_price_segments
            self.assertEqual(len(segments),7);self.assertEqual({p['kind'] for p in segments},{'fibonacci'})
            self.assertTrue(all(p['x1'] <= chart._index_x(133)+1 for p in segments))
            self.assertTrue(any('78.6%' in p['text'] for p in segments))
            for left,right in ((71,120),(133,186)):
                texts=[text for x,text in drawn if chart._index_x(left)-20 <= x <= chart._index_x(right)+20]
                self.assertTrue(any('q1' in text for text in texts),texts)
                self.assertTrue(any('回调' in text for text in texts),texts)
                self.assertTrue(any('子A' in text for text in texts),texts)
                self.assertTrue(any('调整' in text for text in texts),texts)
            self.assertTrue(any('孙' in text for _,text in drawn))
            self.assertTrue(any('海底' in text or '蓝桥' in text or '抱' in text for _,text in drawn))
        finally:
            chart.close();chart.deleteLater()
            QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete);self.app.processEvents()


if __name__=='__main__':unittest.main()
