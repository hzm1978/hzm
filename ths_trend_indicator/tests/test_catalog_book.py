"""Independent event scans and geometric cases for the client-library port."""
import hashlib
from functools import lru_cache
from pathlib import Path
import sys
import unittest

import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from catalog import RAW, RECORDS, FUNCTIONS, parameter_bounds
from check_formula import Parser,walk,check,FormulaError,evaluate
from compatibility import normalize,variable_names
from generate import formula_source,formula_names
from plot_audit import audit
from book_layers import Builder,group,layers
from book_corrections import correction_math,drawing as correction_drawing
from scenarios import path,sample

SOURCE=formula_source()
MAP=formula_names()
GROUPS=layers()[1]


@lru_cache(maxsize=8)
def full(name):
    data,settings=sample(name)
    return data,audit(SOURCE,data,settings)


def value(result,name): return float(result['env'][MAP.get(name,name)][-1])


def group_case(prices,steps=18,running=False,mirror=False):
    """Supply explicit observed pivots; independently generated real crossings.

    This isolates Q classification from the separate fractal detector. Each
    equal extreme appears on two adjacent candles: display is the later one,
    but the measured Q clock must use the first.
    """
    if isinstance(steps,int):steps=[steps]*(len(prices)-1)
    data=path(prices,steps,tail=0 if running else 9)
    if mirror:
        data={'C':-data['C'],'O':-data['O'],'H':-data['L'],'L':-data['H']}
    knots=50+np.r_[0,np.cumsum(steps)]
    last=len(prices)-2 if running else len(prices)-1
    indices=list(range(last,-1,-1))
    kind=1 if last%2 else -1
    if mirror:kind=-kind
    points=[]
    for i in indices:
        at=int(knots[i])+1
        if at>=len(data['C']):at-=1
        point=data['H'][at] if (i%2==1)^mirror else data['L'][at]
        points.append((at,point))
    raw=['BC:=BARPOS;','PN:=2;','BN:=7;','GN:=1;','SHOWC:=1;','GHC:=0;','GLC:=0;',f'WD:={-1 if mirror else 1};',
         'SHOWBOOK:=1;','SHOWQ:=1;','BOOKBARS:=600;','PAD:=5;',f'CT:{kind},NODRAW;']
    for j in range(8):
        at,price=points[min(j,len(points)-1)]
        raw.extend([f'CP{j}:={price};',f'CA{j}:=BC-1-{at}-2;',f'CK{j}:={int(j<len(points))};'])
    raw+=['{ END_ANALYSIS }','VX:=REFX1(BC,100000)-BC;',
          'BSAFEHI:=REFX1(MAX(ABS(HHV(H,0)),ABS(LLV(L,0)))+1,100000);',
          'BSAFELO:=0-BSAFEHI;','BPAD:=5;','BEPS:=0.000001;',f'CTYPE:=CT;']
    b=Builder()
    schema=group(b,'J','C','PN',f'{len(data["C"])-1-int(knots[0])+1}','0',
                 str(data['L'][int(knots[0])])if not mirror else str(data['H'][int(knots[0])]),
                 'C','1=1','测','COLORCYAN')
    raw+='\n'.join(b.lines).splitlines()
    template='\n'.join(raw);mapping=variable_names(template);source=normalize(template)
    check(source)
    result=audit(source,data)
    get=lambda name:float(result['env'][mapping.get(name,name)][-1])
    return data,result,schema,get


def correction_case(prices,final=(94,),mirror=False):
    """Owned, confirmed fine turns, with a real post-end close path."""
    steps=[10]*(len(prices)-1)
    data=path(prices,steps,tail=0)
    for c in final:
        o=data['C'][-1]
        for k,v in [('C',c),('O',o),('H',max(o,c)+.1),('L',min(o,c)-.1)]:
            data[k]=np.r_[data[k],v]
    knots=50+np.r_[0,np.cumsum(steps)]
    if mirror:data={'C':-data['C'],'O':-data['O'],'H':-data['L'],'L':-data['H']}
    n=len(data['C']);sign=-1 if mirror else 1
    # Use exact structural prices independent from the +1 candle wicks.
    raw=['BC:=BARPOS;','GN:=1;','SHOWC:=1;','SHOWBOOK:=1;','BOOKBARS:=600;',
         f'WD:={sign};',f'CCROOT:={sign*prices[0]};',f'CCPOS:={knots[0]+1};',
         'CCSNAP:=0;','CCVALID:=1;','CCPHASE:=1;','CCCHANGE:=0;','CCPREVAGE1:=0;','CCPREVAGE2:=0;',
         f'CCDIR:={sign};',f'WORKAGE:={n-1};',f'GTYPE:={-sign};']
    for i in range(8):
        at=len(prices)-1-min(i,len(prices)-1)
        raw += [f'GP{i}:={sign*prices[at]};',f'GA{i}:=BC-1-{knots[at]}-1;',f'GK{i}:={int(i<len(prices))};']
    raw+=['{ END_ANALYSIS }','VX:=REFX1(BC,100000)-BC;',
          'BEPS:=0.000001;','BPAD:=1;',
          'BSAFEHI:=REFX1(MAX(ABS(HHV(H,0)),ABS(LLV(L,0)))+1,100000);','BSAFELO:=0-BSAFEHI;']
    b=Builder();schema=correction_math(b);correction_drawing(b,schema)
    template='\n'.join(raw+b.lines);mapping=variable_names(template);source=normalize(template)
    check(source);result=audit(source,data)
    get=lambda name:float(result['env'][mapping.get(name,name)][-1])
    return data,result,schema,get


class ClientCatalogTests(unittest.TestCase):
    def test_catalog_is_exact_user_export_with_286_records(self):
        self.assertEqual(len(RECORDS),286)
        self.assertEqual(hashlib.sha256(RAW).hexdigest(),
                         hashlib.sha256((ROOT/'reference'/'all_func.json').read_bytes()).hexdigest())
        self.assertGreater(len(FUNCTIONS),260)

    def test_every_emitted_function_uses_catalogued_arity(self):
        for st in Parser(SOURCE).parse():
            for node in walk(st.expression):
                if node[0]=='call':
                    lo,hi=parameter_bounds(node[1])
                    self.assertLessEqual(lo,len(node[2]));self.assertLessEqual(len(node[2]),hi)

    def test_sort_30_values_limit_and_constant_type_position(self):
        for formula in ['AA:=SORT(1,1,'+','.join(['C']*31)+');',
                        'AA:=SORT(1,C,C,O);','AA:=SORTPOS(C,1,C,O);']:
            with self.subTest(formula=formula),self.assertRaises(FormulaError):check(formula)
        check('AA:=SORTPOS(1,1,'+','.join(['C']*30)+');')

    def test_refx1_window_constant_and_missing_barssincen_guard(self):
        with self.assertRaises(FormulaError):check('AA:=REFX1(C,BARPOS);',False)
        with self.assertRaises(FormulaError):check('AA:=BARSSINCEN(C>O,0);')
        d=path([100,110],5)
        env=evaluate('AA:=BARSSINCEN(C>1000,5);BB:=IFELSE(ISNULL(AA)=1,0,AA);',d)
        self.assertTrue(np.isnan(env['AA']).all());self.assertTrue((env['BB']==0).all())

    def test_all_catalogue_names_reserved_against_variable_collision(self):
        for name in ['SORTPOS','VALUEWHEN','KTEXT','DRAWSHIFTNUMBER']:
            with self.subTest(name=name),self.assertRaises(FormulaError):check(name+':=1;')

    def test_shifted_cross_line_uses_actual_requested_endpoints(self):
        d=path([100,110],5)
        n=len(d['C'])
        source='AA:=1;{ END_ANALYSIS }DRAWLINE3(BARPOS=DATACOUNT-1,100,9,ISLASTBAR,110,3,0),COLORCYAN;'
        line=audit(source,d)['lines'][0]
        self.assertEqual(line['start'],(n-11,100));self.assertEqual(line['end'],(n-4,110))

    def test_text_alignment_and_font_match_native_shape_description(self):
        with self.assertRaises(FormulaError):check("DRAWTEXT(C>O,C,'x'),ALIGN2;")
        check("KTEXT(C>O,0,C,1,COLORRED,'x'),FONTSIZE9;")
        d=path([100,110],5)
        r=audit("AA:=1;{ END_ANALYSIS }KTEXT(ISLASTBAR,(0-3),100,0,COLORRED,'left of candle');",d)
        self.assertEqual(r['labels'][0]['x'],len(d['C'])-4)
        self.assertEqual(r['labels'][0]['align'],'left')


class BookStructureTests(unittest.TestCase):
    def test_actual_five_waves_and_each_return_ratio(self):
        _,r=full('five_up');g=GROUPS[0]
        self.assertEqual(value(r,g['length']),5)
        for wave in (2,4):
            prices=[value(r,p)for p in g['p']]
            expected=abs(prices[5-wave]-prices[6-wave])/abs(prices[6-wave]-prices[7-wave])*100
            self.assertAlmostEqual(value(r,'MWRET'+str(wave)),expected)
        self.assertEqual(value(r,'MWPROOF'),1)

    def test_long_down_subwave_is_owned_by_selected_main_leg(self):
        _,r=full('fall_rebound');main,_,previous,_=GROUPS
        self.assertEqual(value(r,'MWMERGEMODE'),1)
        self.assertEqual(value(r,main['length']),3)
        self.assertEqual(value(r,previous['length']),5)
        self.assertEqual(value(r,previous['direction']),-1)
        self.assertEqual(value(r,'PWOS'),value(r,main['a'][1]))
        self.assertEqual(value(r,'PWOE'),value(r,main['a'][0]))
        labels={l['text']for l in r['labels']}
        self.assertTrue({'子前1','子前2','子前3','子前4','子前5','子前2回调%','子前4回调%'}.issubset(labels))
        self.assertIn('子前A·简单调整',labels)
        self.assertEqual(value(r,'CRKNOWN'),0)
        self.assertIn('调整进行中·未确认结束',labels)

    def test_reverse_five_children_do_not_require_established_parent_direction(self):
        _,r=full('nested_five_down')
        self.assertEqual(value(r,'WD'),0)
        self.assertEqual(value(r,'PWLEN'),5);self.assertEqual(value(r,'PWDIR'),-1)

    def test_first_q_close_and_later_q_wick_have_independent_event_clocks(self):
        data,r,g,get=group_case([1000,1400,1200,1600,1400,1800])
        self.assertEqual(get(g['length']),5)
        for j,q in enumerate(g['qs'][:2]):
            root=get(q['root']);clock=int(get(q['clock']));counter=int(get(q['counter_age']))
            start=len(data['C'])-1-clock
            candidates=np.flatnonzero(data['C'][start+1:]>root)+start+1
            wicks=np.flatnonzero(data['H'][start+1:]>root)+start+1
            self.assertTrue(len(candidates));self.assertTrue(len(wicks))
            self.assertEqual(get(q['cn']),candidates[0]-start)
            self.assertEqual(get(q['wn']),wicks[0]-start)
            self.assertGreater(clock,counter)

    def test_q_clock_uses_first_equal_extreme_not_later_display_point(self):
        _,_,g,get=group_case([1000,1400,1200,1600,1400,1800])
        self.assertGreater(get(g['qs'][1]['clock']),get(g['a'][4]))

    def test_seven_wave_coupling_expanding_contracting_diamond_x(self):
        cases=[([18,18,18,30,24,40,32],(1,1)),([32,40,32,30,24,18,18],(-1,-1)),
               ([18,18,18,40,32,30,24],(1,-1)),([32,40,32,18,18,30,24],(-1,1))]
        prices=[1000,1400,1200,1600,1400,1800,1600,2000]
        for steps,relations in cases:
            with self.subTest(steps=steps):
                _,_,g,get=group_case(prices,steps)
                self.assertEqual(get(g['length']),7)
                self.assertEqual((get('JREL71'),get('JREL72')),relations)

    def test_full_automatic_pipeline_can_select_expanding_seven(self):
        _,r=full('seven_expanding')
        self.assertEqual(value(r,'MWLEN'),7)
        self.assertEqual(value(r,'MWREL71'),1);self.assertEqual(value(r,'MWREL72'),1)

    def test_three_consecutive_same_q_merge_instead_of_seven(self):
        _,r,g,get=group_case([1000,1400,1200,1600,1400,1800,1600,2000])
        self.assertEqual(get('JMERGEMODE'),1);self.assertEqual(get(g['length']),3)
        self.assertFalse(any(l['text']=='测7'for l in r['labels']))

    def test_height_greater_than_1618_merges_counts(self):
        for prices,mode in [([1000,1400,1200,2000,1600,2200],2),
                            ([1000,1400,1200,1600,1400,2200],3)]:
            with self.subTest(mode=mode):
                _,_,g,get=group_case(prices)
                self.assertEqual(get('JMERGEMODE'),mode)
                self.assertEqual(get(g['length']),3)
                self.assertAlmostEqual(get(g['h1']),abs(get(g['p'][2])-get(g['p'][3])))

    def test_running_fifth_not_breaking_third_is_marked_candidate(self):
        _,r,g,get=group_case([1000,1400,1200,1600,1400,1500],running=True)
        self.assertEqual(get(g['length']),5);self.assertEqual(get(g['pending']),1)
        self.assertIn('测5?',{l['text']for l in r['labels']})
        self.assertEqual(get(g['proof']),1)

    def test_book_count_is_symmetric_for_negative_prices(self):
        _,a,g,up=group_case([1000,1400,1200,1600,1400,1800])
        _,b,gm,down=group_case([1000,1400,1200,1600,1400,1800],mirror=True)
        self.assertEqual(up(g['length']),down(gm['length']))
        self.assertEqual(up(g['direction']),-down(gm['direction']))
        for qa,qb in zip(g['qs'][:2],gm['qs'][:2]):self.assertEqual(up(qa['cn']),down(qb['cn']))

    def test_second_wave_may_retrace_more_than_100_percent(self):
        _,_,g,get=group_case([1000,2000,900,2200,1800,2400])
        self.assertEqual(get(g['length']),5)
        self.assertGreater(get('JRET2'),100)


class CorrectionAndPriceTests(unittest.TestCase):
    def test_full_automatic_triangle_preserves_outer_root_through_inner_turns(self):
        _,r=full('triangle_e')
        self.assertEqual(value(r,'CRCODE'),5)
        self.assertEqual(value(r,'CRROOT'),201)
        self.assertEqual(value(r,'CRP0'),175)
        self.assertEqual(value(r,'CRACTIVE'),1)

    def test_default_key_markers_keep_specific_shapes_and_reduce_consecutive_duplicates(self):
        data,r=full('triangle_e')
        detail=audit(SOURCE,data,{'DETAIL':1})
        marked=[l for l in r['labels']if l['text'].startswith('关键位·')]
        raw=[l for l in detail['labels']if l['text'].startswith('关键位·')]
        self.assertGreater(len(marked),0);self.assertLess(len(marked),len(raw))
        self.assertTrue(all(l['align']=='left'for l in marked))
        self.assertTrue(all(l['align']=='right'for l in r['labels']if l['text'].startswith('转折观察·')))

    def test_triangle_uses_e_even_when_a_is_lower(self):
        _,r,c,get=correction_case([100,80,96,85,93,88],final=[94])
        self.assertEqual(get(c['code']),5);self.assertEqual(get(c['end']),88)
        self.assertEqual(get(c['active']),1)
        a_lines=[l for l in r['lines']if l['color']=='COLORCYAN']
        self.assertEqual(len(a_lines),1)
        self.assertEqual(a_lines[0]['start'][1],88);self.assertEqual(a_lines[0]['end'][1],88)

    def test_triangle_candidate_confirmed_broken_states_and_dates(self):
        for close,state in [([90],(0,0,0)),([94],(1,0,0)),([87,94],(0,1,0)),([94,87],(1,0,1))]:
            with self.subTest(close=close):
                _,_,c,get=correction_case([100,80,96,85,93,88],final=close)
                self.assertEqual(tuple(get(c[k])for k in ('confirmed','failed','broken')),state)
                self.assertEqual(get(c['active']),int(state==(1,0,0)))

    def test_strong_abc_allows_b_to_cross_origin(self):
        _,_,c,get=correction_case([100,80,106,70],final=[108])
        self.assertEqual(get(c['code']),4);self.assertEqual(get(c['end']),70)
        self.assertEqual(get(c['active']),1)

    def test_triangle_symmetry_and_actual_end(self):
        _,_,c,get=correction_case([100,80,96,85,93,88],final=[94],mirror=True)
        self.assertEqual(get(c['code']),5);self.assertEqual(get(c['end']),-88)
        self.assertEqual(get(c['active']),1)

    def test_whole_prior_wave_fib_excludes_targets(self):
        _,r=full('fall_rebound')
        fib=[l for l in r['lines']if l['color']=='COLORFFCC00']
        self.assertEqual(len(fib),7)
        self.assertEqual(sorted(l['start'][1]for l in fib)[::6],[2199,4801])
        self.assertFalse(any(l['color']=='COLORFF9900'for l in r['lines']))

    def test_only_three_unhit_targets_with_both_ratio_and_number_on_lines(self):
        _,r=full('trend_targets')
        lines=[l for l in r['lines']if l['color']=='COLORFF9900']
        self.assertEqual(len(lines),3)
        # 4.236H1 is projected from the origin and is nearer than 2H1 here.
        self.assertEqual(sorted(l['start'][1]for l in lines),[3152.8,3176.4,3258.4])
        self.assertFalse(any(l['color']=='COLORFFCC00'for l in r['lines']))
        for line in lines:
            labels=[l for l in r['labels']if l['color']=='COLORFF9900'and l['y']==line['start'][1]]
            self.assertEqual(len(labels),2)
            self.assertTrue(all(line['start'][0]<=l['x']<=line['end'][0]for l in labels))

    def test_no_new_degree_is_invented_after_all_targets_touched(self):
        data,_=sample('trend_targets')
        settings={'DIR':-1,'KIND':1,'MH1':200,'MBASE':3500,'MORG':4000,'MAGE':5}
        # Both origin-projected targets must also be behind the observed low.
        data={k:v-1000 for k,v in data.items()}
        r=audit(SOURCE,data,settings)
        self.assertFalse(any(l['color']=='COLORFF9900'for l in r['lines']))
        self.assertIn('本组候选目标已触及',{l['text']for l in r['labels']})

    def test_doji_wicks_do_not_enter_directional_standard_k(self):
        data={k:np.full(80,v)for k,v in [('O',100),('C',100),('H',200),('L',0)]}
        e=evaluate(SOURCE,data)
        self.assertEqual(e[MAP['SK']][-1],0)
        self.assertEqual(e[MAP['SKOK']][-1],0)

    def test_specific_reversal_shapes_and_standard_qualification(self):
        data={k:np.full(80,v,dtype=float)for k,v in [('O',100),('C',101),('H',102),('L',99)]}
        data['O'][-4:]=[120,79,80,79];data['C'][-4:]=[80,80,79,122]
        data['H'][-4:]=[121,81,81,123];data['L'][-4:]=[79,78,78,78]
        e=evaluate(SOURCE,data)
        self.assertEqual(e[MAP['BPCODE']][-1],1)
        self.assertEqual(e[MAP['BPQUAL']][-1],1)
        self.assertEqual(e[MAP['BULLKEY']][-1],0) # geometry alone is not key position

    def test_shadow_cover_and_blue_bridge_have_distinct_observations(self):
        for values,flag in [([(95,100,120,94),(100,121,122,99)],'BCOVER'),
                            ([(90,100,100.1,89),(101,115,115.1,101)],'BBRIDGE')]:
            data={k:np.full(80,v,dtype=float)for k,v in [('O',100),('C',101),('H',102),('L',99)]}
            for i,quad in enumerate(values):
                for key,v in zip(('O','C','H','L'),quad):data[key][i-2]=v
            with self.subTest(flag=flag):self.assertEqual(evaluate(SOURCE,data)[MAP[flag]][-1],1)

    def test_short_flat_zero_and_negative_drawing_endpoints_remain_finite(self):
        for n,price in [(1,0),(10,-100),(41,0),(80,-50)]:
            with self.subTest(n=n,price=price):
                data={k:np.full(n,price)for k in ('O','H','L','C')}
                r=audit(SOURCE,data)
                self.assertTrue(all(np.isfinite(l['y'])for l in r['labels']))
                self.assertFalse(any(l['color']=='COLORFF9900'for l in r['lines']))

    def test_snapshot_retains_actual_ownership_q_counts_and_price_plan(self):
        from inspect_structure import snapshot
        data,r=full('fall_rebound');trace=snapshot(data,r)
        self.assertEqual(trace['groups'][2]['count'],5)
        self.assertEqual([q['candles_excluding_start']for q in trace['groups'][2]['Q']],[35,35])
        self.assertEqual(trace['fib_segments'],7);self.assertEqual(trace['target_segments'],0)
        self.assertFalse(trace['native_client_run'])


if __name__=='__main__':unittest.main()
