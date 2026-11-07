"""S ownership and ruler regressions inspired by the user's five-wave chart."""
import unittest
import numpy as np
from test_complete_p import scene,points


class SWaveRulerTests(unittest.TestCase):
    def test_automatic_scope_keeps_both_early_legs_of_the_s_five(self):
        _,r,t=scene('s_auto_five');g=t['groups'][2]
        self.assertEqual(t['groups'][1]['owner']['end_price'],3123)
        self.assertEqual(points(r,'COLORCYAN'),[3123,3351,3240,3591,3511,3620])
        self.assertEqual(g['classification'],5)
        self.assertEqual(g['H1'],228)
        self.assertEqual([q['candles_excluding_start']for q in g['Q']],[7,7])
        self.assertTrue({'S1','S2','S3','S4','S5'}.issubset({l['text']for l in r['labels']}))

    def test_after_five_the_ruler_measures_3123_to_3620(self):
        _,_,t=scene('s_auto_five');s=t['S_fib']
        self.assertTrue(s['visible']);self.assertEqual(s['basis'],'whole_group')
        self.assertEqual((s['anchors']['start_price'],s['anchors']['end_price']),(3123,3620))
        np.testing.assert_allclose([v['price']for v in s['levels']],
                                  [3620-497*r for r in (0,.191,.382,.5,.618,.786,1)])

    def test_downward_five_has_the_same_scope_and_ruler_by_reflection(self):
        _,r,t=scene('s_auto_five_down');s=t['S_fib']
        self.assertEqual(points(r,'COLORCYAN'),[10000-p for p in (3123,3351,3240,3591,3511,3620)])
        self.assertEqual(t['groups'][2]['classification'],5)
        self.assertEqual(s['basis'],'whole_group')
        self.assertEqual((s['anchors']['start_price'],s['anchors']['end_price']),(6877,6380))
        self.assertLess(s['levels'][0]['price'],s['levels'][-1]['price'])

    def test_five_origin_survives_later_correction_turns(self):
        _,r,t=scene('s_five_tail');g=t['groups'][2]
        self.assertEqual(points(r,'COLORCYAN'),[3123,3351,3240,3591,3511,3620])
        self.assertEqual(g['classification'],5)
        self.assertEqual(t['S_fib']['anchors']['start_price'],3123)
        self.assertEqual(t['S_fib']['anchors']['end_price'],3620)

    def test_a_fourth_wave_correction_measures_only_the_preceding_third(self):
        for name,anchors in [('s_leg_fib',(3240,3591)),('s_leg_fib_down',(6760,6409))]:
            with self.subTest(name=name):
                _,_,t=scene(name);s=t['S_fib']
                self.assertTrue(s['visible']);self.assertEqual(s['basis'],'preceding_subwave')
                self.assertEqual((s['anchors']['start_price'],s['anchors']['end_price']),anchors)
                start,end=anchors
                np.testing.assert_allclose([v['price']for v in s['levels']],
                                          [end+(start-end)*r for r in (0,.191,.382,.5,.618,.786,1)])

    def test_first_leg_and_absence_of_a_pullback_do_not_invent_a_whole_five(self):
        _,_,t=scene('s_first_fib');s=t['S_fib']
        self.assertEqual(s['basis'],'preceding_subwave')
        self.assertEqual((s['anchors']['start_price'],s['anchors']['end_price']),(3123,3351))
        _,r,t=scene('s_five_live')
        self.assertFalse(t['S_fib']['visible'])
        self.assertFalse(any(l['text']=='S 0%'for l in r['labels']))


if __name__=='__main__':unittest.main()
