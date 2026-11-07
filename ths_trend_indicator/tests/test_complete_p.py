"""Current product regressions for owned M/P/S intervals and price rulers."""
from functools import lru_cache
from pathlib import Path
import sys,unittest
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from generate import formula_source,formula_names
from interval_layers import layers
from plot_audit import audit
from scenarios import sample,path
from inspect_structure import snapshot
SOURCE=formula_source();MAPPING=formula_names();SCHEMA=layers()[1]
def value(r,k):return float(r['env'][MAPPING.get(k,k)][-1])
@lru_cache(maxsize=24)
def scene(name):
    d,s=sample(name);r=audit(SOURCE,d,s)
    return d,r,snapshot(d,r,mapping=MAPPING,source=SOURCE)
def outline(r,color='COLORLIBLUE'):
    return [l for l in r['lines']if l['color']==color and l['function']=='POLYLINE']
def points(r,color='COLORLIBLUE'):
    lines=outline(r,color)
    return [lines[0]['start'][1]]+[l['end'][1]for l in lines]if lines else []
class CompletePTests(unittest.TestCase):
    def test_raw_turns_keep_internal_intervals_without_inventing_extra_parent_numbers(self):
        d,r,t=scene('p_whole');g=t['groups'][1]
        self.assertEqual(points(r),[1015.38,959,991,922,931,869,901,831])
        self.assertEqual(g['intervals'],2)
        self.assertEqual(g['internal_Q'][0]['root_price'],869)
        self.assertEqual(g['internal_Q'][0]['length'],9)
        self.assertFalse(any(l['text']in ('P8','P9','P10','P11')for l in r['labels']))
        self.assertEqual(t['price_plan']['fib_anchors']['start_price'],1015.38)
        self.assertNotEqual(t['price_plan']['fib_anchors']['start_price'],977.26)
    def test_first_close_later_wick_lengths_use_actual_candles_excluding_root(self):
        for name in ('p_whole','p_white','p_triangle'):
            d,r,t=scene(name);end=t['groups'][1]['owner']['end_index']
            for q in t['groups'][1]['Q']:
                a=q['start_index'];resume=q['counter_index']+1
                root=q['root_price'];column=d['C']if q['first_uses_close']else d['L']
                crossed=np.flatnonzero(column[resume:end+1]<root-1e-7)
                boundary=resume+int(crossed[0])if len(crossed)else end
                self.assertEqual(q['end_index'],boundary)
                self.assertEqual(q['candles_excluding_start'],boundary-a)
    def test_white_structure_keeps_late_c_and_complete_adjustment(self):
        _,r,t=scene('p_white')
        self.assertEqual(points(r),[201,99,151,69,101,39])
        self.assertEqual(t['groups'][1]['Q'][0]['adjustment_shape_label'],'ABC STR')
        self.assertAlmostEqual(t['groups'][1]['Q'][0]['retracement_percent'],52/102*100)
        self.assertTrue(any(l['color']=='COLORMAGENTA'and l['end'][1]==151 for l in r['lines']))
    def test_triangle_end_is_e_not_the_more_extreme_a(self):
        _,r,t=scene('p_triangle');q=t['groups'][1]['Q'][0]
        self.assertEqual(q['adjustment_shape_label'],'TRI CON')
        self.assertEqual(q['counter_price'],121);self.assertNotEqual(q['counter_price'],131)
    def test_endpoint_reports_actual_h1_multiple_not_price_or_target_ratio(self):
        _,r,t=scene('p_whole');g=t['groups'][1]
        self.assertAlmostEqual(g['actual_end_H1_multiple'],100/56.38)
        self.assertNotEqual(round(g['actual_end_H1_multiple'],3),1.208)
        h1_labels=[l for l in r['labels']if l['text']==' H1']
        self.assertTrue(h1_labels)
        expected=f"{g['actual_end_H1_multiple']:.3f}"
        self.assertTrue(any(l['text']==expected and l['function']=='DRAWNUMBER'for l in r['labels']))
    def test_p_keeps_the_whole_selected_parent_frame_after_main_interval_reduction(self):
        for name in ('short_inner_q','earlier_complete','five_up','p_newtip'):
            _,r,t=scene(name);m=SCHEMA['M'];p=SCHEMA['P']['scope']
            self.assertEqual(value(r,p['first']),t['price_plan']['fib_anchors']['start_price'])
            self.assertGreaterEqual(value(r,p['start']),value(r,m['base_age']))
            self.assertEqual(t['groups'][0]['owner']['end_index'],t['groups'][1]['owner']['end_index'])
    def test_inner_high_cannot_shorten_manual_parent_anchor(self):
        _,_,t=scene('p_whole');a=t['price_plan']['fib_anchors']
        self.assertEqual((a['start_price'],a['end_price']),(1015.38,831))
        levels=t['price_plan']['fib_levels']
        self.assertEqual((levels[0]['price'],levels[-1]['price']),(831,1015.38))
        self.assertLess(a['start_index'],a['end_index'])
    def test_parent_line_includes_whole_prior_excursion_not_small_910_turn(self):
        d,s=sample('main_owned');r=audit(SOURCE,d,s);t=snapshot(d,r,mapping=MAPPING,source=SOURCE)
        m=points(r,'COLORYELLOW');p=points(r)
        self.assertIn(864.7,m);self.assertIn(1015.38,m);self.assertEqual(m[-1],831)
        self.assertEqual((p[0],p[-1]),(1015.38,831))
        self.assertNotIn(911,m)
    def test_fib_marks_actual_parent_high_and_low_and_never_draws_targets(self):
        _,r,t=scene('p_whole');a=t['price_plan']['fib_anchors']
        fib=[l for l in r['lines']if l['color']=='COLORFFCC00']
        levels=sorted({l['start'][1]for l in fib if l['start'][1]==l['end'][1]and abs(l['end'][0]-l['start'][0])>2})
        np.testing.assert_allclose(levels,[831+(1015.38-831)*q for q in (0,.191,.382,.5,.618,.786,1)])
        self.assertTrue(any(l['start'][0]==l['end'][0]==a['start_index']for l in fib))
        self.assertFalse(any(l['color']=='COLORFF9900'for l in r['lines']))
    def test_running_impulse_targets_are_exclusive_and_s_is_empty(self):
        _,r,t=scene('trend_targets')
        self.assertEqual(sum(l['color']=='COLORFF9900'for l in r['lines']),3)
        self.assertFalse(any(l['color']=='COLORFFCC00'for l in r['lines']))
        self.assertFalse(t['current_correction_visible'])
        self.assertFalse(outline(r,'COLORCYAN'))
        self.assertFalse(any(l['text'].startswith(('S1','S2','SOUTER'))for l in r['labels']))
    def test_last_correction_is_owned_by_parent_tip_and_has_no_old_history(self):
        _,r,t=scene('p_s_correction');p=t['groups'][1]['owner'];s=t['groups'][2]['owner']
        self.assertTrue(s['visible']);self.assertEqual(s['start_index'],p['end_index'])
        self.assertEqual(s['start_price'],p['end_price']);self.assertEqual(s['direction'],-p['direction'])
        self.assertTrue(all(l['start'][0]>=s['start_index']and l['end'][0]>=s['start_index']for l in outline(r,'COLORCYAN')))
        d,settings=sample('p_s_correction');hidden=audit(SOURCE,d,dict(settings)|{'SHOWGRAND':0})
        self.assertFalse(outline(hidden,'COLORCYAN'));self.assertEqual(outline(hidden),outline(r))
    def test_no_h1_still_shows_whole_owned_swing_without_fabricated_multiple(self):
        _,r,t=scene('p_no_h1');g=t['groups'][1]
        self.assertEqual(points(r),[15316,12999]);self.assertIsNone(g['H1'])
        self.assertIn('H1 ?',{l['text']for l in r['labels']})
    def test_reversal_k_observations_remain_on_completed_p(self):
        _,r,t=scene('p_white');p=t['groups'][1]['owner']
        obs=[l for l in r['labels']if l['text'].startswith('RV')and p['start_index']<=l['x']<=p['end_index']+3]
        self.assertTrue(obs)
        self.assertTrue(all(l['function']=='KTEXT'for l in obs))
    def test_reflected_prices_preserve_wave_times_and_percentages(self):
        _,down,t1=scene('p_white');_,up,t2=scene('p_white_up')
        self.assertEqual([(l['start'][0],l['end'][0])for l in outline(down)],[(l['start'][0],l['end'][0])for l in outline(up)])
        np.testing.assert_allclose(points(down),[600-p for p in points(up)])
        np.testing.assert_allclose([q['retracement_percent']for q in t1['groups'][1]['Q']],[q['retracement_percent']for q in t2['groups'][1]['Q']])
    def test_flat_short_and_negative_inputs_do_not_fabricate_visible_points(self):
        for n,price in ((1,0),(10,-100),(41,0),(80,-50)):
            d={k:np.full(n,price)for k in ('O','H','L','C')};r=audit(SOURCE,d)
            self.assertTrue(all(np.isfinite(l['y'])for l in r['labels']))
            self.assertFalse(outline(r));self.assertFalse(outline(r,'COLORCYAN'))
            self.assertFalse(any(l['color']=='COLORFF9900'for l in r['lines']))
    def test_existing_english_interval_classification_is_not_duplicated(self):
        _,r,_=scene('s_fib');codes=[l for l in r['labels']if l['text']=='5 EQ']
        self.assertEqual(len(codes),1)
        self.assertFalse(any(any('\u4e00'<=c<='\u9fff'for c in l['text'])for l in r['labels']))
if __name__=='__main__':unittest.main()
