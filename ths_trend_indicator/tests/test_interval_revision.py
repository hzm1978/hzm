"""Regressions for early/internal Q ownership and whole-frame price bases."""
from pathlib import Path
import sys,unittest
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from test_complete_p import scene,points,outline,SOURCE,MAPPING,SCHEMA
from plot_audit import audit
from scenarios import sample
from inspect_structure import snapshot


class IntervalRevisionTests(unittest.TestCase):
    def test_early_sideways_interval_remains_in_five_leg_outline(self):
        _,r,t=scene('q_early')
        self.assertEqual(points(r),[3908,3719,3726,3199,3281,3129])
        self.assertEqual(t['groups'][1]['intervals'],2)
        self.assertEqual(t['groups'][1]['H1'],189)

    def test_merged_root_and_return_clock_follow_book_c_endpoint(self):
        d,r,t=scene('q_merge');q=t['groups'][1]['Q'][0]
        self.assertEqual(points(r),[201,159,176,99])
        self.assertNotEqual(q['root_price'],151)
        self.assertGreater(q['counter_index'],60)
        start=q['counter_index']+1
        crossing=start+int(np.flatnonzero(d['C'][start:]<q['root_price']-1e-7)[0])
        self.assertEqual(q['end_index'],crossing)
        self.assertEqual(q['candles_excluding_start'],crossing-q['start_index'])

    def test_bearish_wick_only_interval_stays_inside_main_wave_one(self):
        _,r,t=scene('q_tiny')
        self.assertEqual(points(r),[4988,4592,4776,4470])
        self.assertEqual(t['groups'][1]['H1'],396)
        self.assertFalse(any(q['root_price']==4900 for q in t['groups'][1]['internal_Q']))

    def test_internal_intervals_survive_downgrade_without_renumbering_parent(self):
        _,r,t=scene('earlier_complete');g=t['groups'][1]
        self.assertEqual(g['owner']['start_price'],14015)
        self.assertEqual([q['root_price']for q in g['internal_Q']],[15401,16001])
        self.assertTrue({15401,14999,16001,15599}.issubset(set(points(r))))
        self.assertTrue(any(l['text']=='q LEN 'for l in r['labels']))
        self.assertFalse(any(l['text']in ('P8','P9','P10','P11')for l in r['labels']))

    def test_later_lower_peak_is_a_terminal_candidate_and_keeps_true_ruler_high(self):
        _,r,t=scene('q_failed_auto');g=t['groups'][1]
        self.assertEqual([p['price']for p in g['points']][1:],[8657,8368,9286,8839,9195])
        self.assertTrue(g['pending'])
        self.assertIn('TRUNC?',{l['text']for l in r['labels']})
        self.assertEqual(t['price_plan']['fib_anchors']['end_price'],9286)
        self.assertEqual(t['groups'][2]['owner']['end_price'],9195)

    def test_new_down_three_does_not_replace_whole_prior_up_three_tip(self):
        _,r,t=scene('q_reversal')
        self.assertEqual([p['price']for p in t['groups'][1]['points']],[432.3,621,500,929.4])
        self.assertEqual([p['price']for p in t['groups'][2]['points']],[929.4,649,769.9,604.8])
        self.assertEqual(t['price_plan']['fib_anchors']['end_price'],929.4)
        self.assertEqual(t['current_P']['end_price'],604.8)
        self.assertEqual(t['current_P_adjustment_code'],2)
        self.assertTrue(any(l['color']=='COLORMAGENTA'and l['end'][1]==604.8 for l in r['lines']))

    def test_main_left_origin_and_simple_three_are_not_shortened(self):
        for name,want in [('main_left',[826.5,731.4,765.1,693.5,741.5,666.7]),
                          ('main_three',[3513,3184,3415,3141])]:
            _,r,t=scene(name)
            self.assertEqual(points(r,'COLORYELLOW'),want)
            self.assertEqual(points(r),want)

    def test_s_expands_latest_p_main_leg_as_well_as_correction(self):
        _,r,t=scene('s_main');s=t['groups'][2]['owner']
        self.assertTrue(s['visible']);self.assertEqual(t['S_parent_leg'],'main')
        self.assertEqual((s['start_price'],s['end_price']),(439750,337400))
        self.assertEqual(points(r,'COLORCYAN'),[439750,337400])
        self.assertFalse(t['S_fib']['visible'])
        self.assertTrue(all(l['start'][0]>=s['start_index']for l in outline(r,'COLORCYAN')))

    def test_s_correction_ruler_uses_whole_preceding_s_impulse(self):
        _,r,t=scene('s_fib');s=t['S_fib']
        self.assertTrue(s['visible'])
        self.assertEqual((s['anchors']['start_price'],s['anchors']['end_price']),(3129,3625))
        np.testing.assert_allclose([q['price']for q in s['levels']],
                                  [3625-(3625-3129)*r for r in (0,.191,.382,.5,.618,.786,1)])
        self.assertNotEqual(s['anchors']['start_price'],3379)
        self.assertIn('S 19.1%',{l['text']for l in r['labels']})

    def test_nearby_targets_expose_distinct_leg_and_total_origins(self):
        _,r,t=scene('target_bases')
        shown={q['ratio']:q for q in t['price_plan']['targets']if q['displayed']}
        self.assertEqual(set(shown),{1.618,2,4.236})
        self.assertEqual(shown[2]['origin'],3500)
        self.assertEqual(shown[4.236]['origin'],4000)
        self.assertAlmostEqual(shown[2]['price'],3100)
        self.assertAlmostEqual(shown[4.236]['price'],3152.8)
        self.assertAlmostEqual(abs(shown[2]['price']-shown[4.236]['price']),52.8)
        labels={l['text']:l for l in r['labels']}
        self.assertIn('LEG 2H1?',labels);self.assertIn('TOTAL 4.236H1?',labels)
        self.assertNotEqual(labels['LEG 2H1?']['x'],labels['TOTAL 4.236H1?']['x'])

    def test_fonts_are_larger_and_guides_can_be_hidden_without_changing_count(self):
        d,s=sample('guides_up');r=audit(SOURCE,d,s);t=snapshot(d,r,MAPPING,SOURCE)
        self.assertTrue(t['guides']['P']['trend'])
        self.assertTrue(any(l['text']=='TL?'for l in r['labels']))
        hidden=audit(SOURCE,d,dict(s)|{'SHOWL':0})
        self.assertEqual(outline(r),outline(hidden))
        self.assertFalse(any(l['text']in ('TL?','PL?')for l in hidden['labels']))
        _,q,_=scene('q_early')
        self.assertTrue(all(l['font_size']>=11 for l in q['labels']))
        self.assertTrue(all(l['font_size']==13 for l in q['labels']if l['text']in ('P1','P2','P3','P4')))

    def test_broken_trend_reference_is_suppressed(self):
        d,s=sample('guides_up');old=len(d['C'])
        for key,v in [('O',180),('C',100),('H',181),('L',99)]:d[key]=np.r_[d[key],v]
        settings=dict(s)|{'KIND':1,'MANF':1,'MFROM':98,'MTO':181,'MFAGE':old}
        r=audit(SOURCE,d,settings);t=snapshot(d,r,MAPPING,SOURCE)
        self.assertFalse(t['guides']['P']['trend'])
        self.assertFalse(any(l['text']=='TL?'for l in r['labels']))


if __name__=='__main__':unittest.main()
