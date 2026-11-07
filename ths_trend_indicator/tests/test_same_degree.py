"""Current engine must preserve a same-degree reference through inner families."""
from functools import lru_cache
from collections import Counter
from pathlib import Path
import sys,unittest
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from generate import formula_source,formula_names,core,drawing
from compatibility import normalize
from check_formula import check
from plot_audit import audit
from scenarios import sample,path
from inspect_structure import snapshot
SOURCE=formula_source();NAMES=formula_names()


@lru_cache(maxsize=12)
def scene(name):
    d,s=sample(name)
    r=audit(SOURCE,d,s)
    return d,r,snapshot(d,r,NAMES,SOURCE)


class SameDegreeTests(unittest.TestCase):
    def test_three_same_degree_q_demote_trailing_pair_without_a_price_union(self):
        _,_,t=scene('same_three');p=t['groups'][1]
        self.assertEqual(p['intervals'],1)
        self.assertEqual([q['root_price']for q in p['Q']],[121])
        self.assertEqual(p['H1'],23)
        self.assertEqual(len(p['internal_Q']),2)

    def test_fourth_same_degree_q_recouples_with_original_reference(self):
        _,_,t=scene('same_four');p=t['groups'][1]
        self.assertEqual(p['intervals'],2);self.assertEqual(p['classification'],5)
        self.assertEqual([q['root_price']for q in p['Q']],[121,157])
        self.assertEqual([q['candles_excluding_start']for q in p['Q']],[11,11])
        self.assertEqual(p['H1'],23)
        self.assertFalse(p['details_limited'])
        m=t['groups'][0]
        self.assertEqual(m['classification'],5)
        self.assertEqual([q['root_price']for q in m['Q']],[121,157])

    def test_different_then_equal_can_remain_a_seven_wave_candidate(self):
        _,_,t=scene('contract_equal');p=t['groups'][1]
        self.assertEqual(p['intervals'],3);self.assertEqual(p['classification'],72)
        self.assertEqual([q['candles_excluding_start']for q in p['Q']],[26,15,15])

    def test_continuation_recouples_later_interval_with_original_same_degree(self):
        _,_,t=scene('recouple_equal');p=t['groups'][1]
        self.assertEqual(p['intervals'],2);self.assertEqual(p['classification'],5)
        self.assertEqual([q['root_price']for q in p['Q']],[151,236])
        self.assertEqual([q['candles_excluding_start']for q in p['Q']],[26,26])
        self.assertEqual(p['H1'],53)

    def test_completed_inner_pair_does_not_move_the_original_reference(self):
        _,_,t=scene('p_whole');p=t['groups'][1]
        self.assertEqual(p['classification'],5)
        self.assertEqual([q['root_price']for q in p['Q']],[959,869])
        self.assertEqual([q['candles_excluding_start']for q in p['Q']],[7,9])
        self.assertAlmostEqual(p['H1'],56.38)

    def test_height_extension_precedes_time_demotion(self):
        _,_,t=scene('q_early');p=t['groups'][1]
        self.assertEqual([q['root_price']for q in p['Q']],[3719,3199])
        self.assertEqual([q['counter_price']for q in p['Q']],[3726,3281])
        self.assertEqual(p['H1'],189)

    def test_white_structure_reaches_late_equal_q_despite_startup_noise(self):
        _,r,t=scene('white_same');p=t['groups'][1]
        self.assertEqual(p['classification'],5)
        self.assertEqual([q['root_price']for q in p['Q']],[8036,8770])
        self.assertEqual([q['counter_price']for q in p['Q']],[7464,8493])
        self.assertEqual([q['candles_excluding_start']for q in p['Q']],[40,37])
        self.assertEqual(p['H1'],1332)
        self.assertFalse(p['details_limited'])
        vertices={float(y)for line in r['lines']if line['color']=='COLORMAGENTA'
                  for _,y in (line['start'],line['end'])}
        self.assertTrue({8255,8093,9056}.issubset(vertices))

    def test_same_degree_choice_is_symmetric_under_price_reflection(self):
        _,_,a=scene('white_same');_,_,b=scene('white_same_down')
        pa,pb=a['groups'][1],b['groups'][1]
        self.assertEqual(pa['classification'],pb['classification'])
        np.testing.assert_allclose([p['price']for p in pa['points']],
                                   [20000-p['price']for p in pb['points']])
        self.assertEqual([q['candles_excluding_start']for q in pa['Q']],
                         [q['candles_excluding_start']for q in pb['Q']])

    def test_real_new_interval_upgrade_remains_allowed(self):
        _,_,t=scene('earlier_complete');p=t['groups'][1]
        self.assertEqual(p['intervals'],1)
        self.assertEqual(p['Q'][0]['candles_excluding_start'],29)
        self.assertEqual(p['Q'][0]['root_price'],16975)

    def test_expanding_seven_is_not_forced_into_same_five(self):
        _,_,t=scene('seven_expanding');p=t['groups'][1]
        self.assertEqual(p['classification'],71)
        self.assertEqual(p['intervals'],3)

    def test_current_model_has_a_measured_bounded_review_budget(self):
        report=check(SOURCE)
        self.assertLessEqual(report['variables'],5120)
        self.assertLessEqual(len(SOURCE.encode('gb18030')),524288)

    def test_seven_wave_grading_compares_adjacent_outer_intervals(self):
        for name,kind in [('graded_expand',71),('graded_contract',72)]:
            _,_,t=scene(name);p=t['groups'][1]
            self.assertEqual(p['intervals'],3,name)
            self.assertEqual(p['classification'],kind,name)
            self.assertEqual(p['Q'][0]['root_price'],151)
            spans=[q['candles_excluding_start']for q in p['Q']]
            if name=='graded_expand':
                self.assertGreater(spans[-1]/spans[0],2.618)
                self.assertLessEqual(spans[-1]/spans[-2],2.618)
            else:
                self.assertLess(spans[-1]/spans[0],.382)
                self.assertGreaterEqual(spans[-1]/spans[-2],.382)

    def test_shared_text_keeps_every_unshared_label_event(self):
        import interval_layers
        from book_layers import Builder
        original=interval_layers.Builder
        def unshared(*args,**kwargs):
            kwargs['collect_text']=False
            return Builder(*args,**kwargs)
        try:
            interval_layers.Builder=unshared
            raw=normalize('\n'.join(core()+drawing()))
        finally:interval_layers.Builder=original
        key=lambda l:(l['x'],round(float(l['y']),7),l['text'],l['align'],l['color'],l['function'],l.get('font_size'))
        for name in ('white_same','p_triangle','main_owned','s_fib'):
            d,s=sample(name);expected=audit(raw,d,s);actual=audit(SOURCE,d,s)
            self.assertEqual(Counter(map(key,expected['labels'])),Counter(map(key,actual['labels'])),name)


if __name__=='__main__':unittest.main()
