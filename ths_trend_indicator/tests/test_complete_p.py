"""User regressions: whole P, correct ruler, outer correction end, no blank H1."""
from functools import lru_cache
from pathlib import Path
import sys
import unittest

import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from generate import formula_source,formula_names
from plot_audit import audit
from scenarios import sample,path
from p_complete import MAX_Q

SOURCE=formula_source()
MAPPING=formula_names()

def value(result,name):return float(result['env'][MAPPING.get(name,name)][-1])

@lru_cache(maxsize=8)
def scene(name):
    data,settings=sample(name)
    return data,audit(SOURCE,data,settings)

def outline(result):
    return [l for l in result['lines'] if l['color']=='COLORLIBLUE' and l['function']=='POLYLINE']

class CompletePTests(unittest.TestCase):
    def test_p_covers_whole_ruler_even_beyond_eight_fine_pivots(self):
        data,r=scene('p_whole');lines=outline(r)
        self.assertGreater(len(lines),7)
        self.assertEqual(lines[0]['start'],(50,1015.38))
        self.assertEqual(lines[-1]['end'][1],831)
        for a,b in zip(lines,lines[1:]):self.assertEqual(a['end'],b['start'])
        self.assertEqual(value(r,'PCFIRST'),value(r,'PRFSTART'))
        self.assertEqual(value(r,'PCLAST'),value(r,'PRFEND'))
        self.assertFalse(any(l['text'].startswith(('子','孙'))for l in r['labels']))
        self.assertEqual(value(r,'PCQ4INDEX'),5)
        for i in range(MAX_Q):
            n=int(value(r,f'PCQ{i}N'));root=value(r,f'PCQ{i}ROOT')
            a=len(data['C'])-1-int(value(r,f'PCQ{i}ROOTAGE'))
            column=data['C']if i==0 else data['L']
            k=a+1+int(np.flatnonzero(column[a+1:]<root)[0])
            self.assertEqual(n,k-a)

    def test_white_structure_keeps_later_c_not_first_local_rebound(self):
        _,r=scene('p_white')
        points=[outline(r)[0]['start'][1]]+[l['end'][1]for l in outline(r)]
        self.assertEqual(points,[201,99,151,69,101,39])
        self.assertEqual(value(r,'PCQ0SHAPE'),4)
        self.assertAlmostEqual(value(r,'PCQ0RET'),52/102*100)
        self.assertTrue(any(l['color']=='COLORMAGENTA' and l['start'][1]==97.5 and l['end'][1]==151 for l in r['lines']))

    def test_triangle_end_is_e_instead_of_more_extreme_a(self):
        _,r=scene('p_triangle')
        self.assertEqual(value(r,'PCQ0SHAPE'),5)
        self.assertEqual(value(r,'PCQ0COUNTER'),121)
        self.assertNotEqual(value(r,'PCQ0COUNTER'),131)

    def test_no_h1_still_displays_observed_whole_swing(self):
        _,r=scene('p_no_h1')
        self.assertEqual(value(r,'PRPROOF'),0)
        self.assertEqual(value(r,'PCON'),1)
        self.assertEqual(outline(r)[0]['start'][1],15316)
        self.assertEqual(outline(r)[-1]['end'][1],12999)
        self.assertIn('P END',{l['text']for l in r['labels']})

    def test_qualified_p_origin_replaces_older_merged_parent_origin(self):
        data,settings=sample('p_no_h1')
        overrides={'MWON':1,'MWDIR':-1,'PWLEN':5,'PWON':1,'PWPROOF':1,'PWDIR':-1,
                   'OWN_PREVP1':1163,'OWN_PREVP2':824,'OWN_PREVSTART':80,'OWN_PREVEND':10,
                   'BOOKTIPTURN':0,'PARENTPHASE':2}
        from book_layers import layers
        g=layers()[1][2]
        overrides.update({g['p'][5]:1012,g['a'][5]:50,g['p'][0]:824})
        settings=dict(settings)|{'MANF':0}
        # Drawing snapshots intentionally ignore runtime core overrides;
        # replace these controlled scope definitions in the test formula.
        import re
        controlled=SOURCE
        for k,v in overrides.items():
            k=MAPPING.get(k,k);literal=str(v)if v>=0 else'0-'+str(abs(v))
            controlled,count=re.subn(r'^'+re.escape(k)+r':=[^;\n]+;',k+':='+literal+';',controlled,flags=re.M)
            self.assertEqual(count,1)
        r=audit(controlled,data,settings)
        self.assertEqual(value(r,'PRFUSEP'),1)
        self.assertEqual(value(r,'PRFSTART'),1012)
        self.assertEqual(value(r,'PRFEND'),824)
        self.assertEqual(value(r,'PCFIRST'),1012)
        self.assertEqual(value(r,'PCLAST'),824)

    def test_confirmed_small_rebound_does_not_wait_for_bn_tip_confirmation(self):
        # A complete working down leg followed by 3 rebound bars, with BN=7.
        data=path([2000,1800,1950,1823,2021,1439,1510],[18,18,18,18,32,3],tail=0)
        r=audit(SOURCE,data,{'DIR':-1})
        self.assertGreaterEqual(value(r,'WORKTIPAGE'),2)
        self.assertEqual(value(r,'BOOKTIPTURN'),1)
        self.assertEqual(value(r,'PRPHASE'),2)
        self.assertEqual(value(r,'PRFON'),1)
        self.assertEqual(value(r,'PRFSTART'),2022)
        self.assertEqual(value(r,'PRFEND'),1438)
        self.assertFalse(any(l['color']=='COLORFF9900'for l in r['lines']))
        self.assertGreater(value(r,'BSAFEHI'),max(abs(data['H']).max(),abs(data['L']).max()))
        self.assertEqual(value(r,'MWRUN'),1)

    def test_reversal_observations_are_kept_inside_completed_p_during_correction(self):
        _,r=scene('p_white')
        first=outline(r)[0]['start'][0];last=outline(r)[-1]['end'][0]
        observations=[l for l in r['labels']if l['text'].startswith('转折观察·') and first<=l['x']<=last+3]
        self.assertEqual(value(r,'PRPHASE'),2)
        self.assertGreater(len(observations),0)

    def test_p_is_symmetric_and_every_turning_price_is_finite(self):
        data,settings=sample('p_white')
        mirror={'O':-data['O'],'C':-data['C'],'H':-data['L'],'L':-data['H']}
        _,original=scene('p_white')
        settings=dict(settings)|{'DIR':1,'MFROM':-settings['MFROM'],'MTO':-settings['MTO']}
        reflected=audit(SOURCE,mirror,settings)
        self.assertEqual([(l['start'][0],l['end'][0])for l in outline(original)],
                         [(l['start'][0],l['end'][0])for l in outline(reflected)])
        np.testing.assert_allclose([l['end'][1]for l in outline(original)],
                                   [-l['end'][1]for l in outline(reflected)])

    def test_opposite_complete_p_reorients_a_stale_nearly_reversed_ruler(self):
        from book_layers import layers
        import re
        data,settings=sample('p_no_h1');g=layers()[1][2]
        overrides={'MWON':1,'MWDIR':1,'PWLEN':3,'PWON':1,'PWPROOF':1,'PWDIR':-1,
                   'OWN_PREVP1':1657,'OWN_PREVP2':1845,'OWN_PREVSTART':80,'OWN_PREVEND':10,
                   'BOOKTIPTURN':0,'PARENTPHASE':2,
                   g['p'][3]:1845,g['a'][3]:70,g['p'][0]:1649,g['a'][0]:10}
        source=SOURCE
        for k,v in overrides.items():
            k=MAPPING.get(k,k);literal=str(v)if v>=0 else'0-'+str(abs(v))
            source,count=re.subn(r'^'+re.escape(k)+r':=[^;\n]+;',k+':='+literal+';',source,flags=re.M)
            self.assertEqual(count,1)
        r=audit(source,data,dict(settings)|{'MANF':0})
        self.assertEqual(value(r,'PRFREVERSE'),1)
        self.assertEqual(value(r,'PRF0'),1649)
        self.assertEqual(value(r,'PRF6'),1845)
        self.assertEqual(value(r,'PRDISPLAYDIR'),-1)

    def test_optional_s_is_owned_only_by_current_correction(self):
        data,r=scene('p_s_correction')
        self.assertEqual(value(r,'SCON'),1)
        self.assertEqual(value(r,'SCOS'),value(r,'PCSTOP'))
        self.assertEqual(value(r,'SCDIR'),-value(r,'PCDIR'))
        self.assertTrue(any(l['text'].startswith('回调')for l in r['labels']))
        data,settings=sample('p_s_correction')
        hidden=audit(SOURCE,data,dict(settings)|{'SHOWGRAND':0})
        self.assertFalse(any(l['text'].startswith('回调')for l in hidden['labels']))
        self.assertEqual(outline(hidden),outline(r))

if __name__=='__main__':unittest.main()
