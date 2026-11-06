"""Legacy 1.1.4 snapshot for isolated historical regression tests only."""
from pathlib import Path
import argparse
import csv
import hashlib
import json

import numpy as np

from book_layers import layers,Builder
from book_corrections import correction_math
from catalog import RAW
from generate import legacy_formula_source as formula_source,legacy_formula_names as formula_names
from plot_audit import audit,save_preview
from scenarios import NAMES,sample


def snapshot(data,result,mapping=None,source=None):
    names=formula_names()if mapping is None else mapping
    n=len(data['C']);env=result['env']
    def val(name):
        v=float(env[names.get(name,name)][-1])
        return v if np.isfinite(v)else None
    def at(age):return n-1-int(age)
    groups=[]
    for schema in layers()[1]:
        key=schema['key'];length=int(val(schema['length']))
        points=[{'wave':length-i,'bar_index':at(val(schema['a'][i])),
                 'price':val(schema['p'][i])}for i in range(length,-1,-1)]if length else []
        qs=[]
        for j,q in enumerate(schema['qs'][:max(0,(length-1)//2)]):
            from book_corrections import ADJUSTMENT_TYPES
            shape=1 if val(key+'QSIMPLE'+str(j))else (int(val(q['shape_code']))if 'shape_code'in q else 0)
            first=j==(length-3)//2
            ongoing=bool(val(schema['pending'])and j==0)
            end_age=val(key+'OE')if ongoing else val(q['ca']if first else q['wa'])
            qs.append({'ordinal':(length-1)//2-j,'first_uses_close':first,
                       'start_index':at(val(q['clock'])),'end_index':at(end_age),
                       'candles_excluding_start':val(q['clock'])-end_age,
                       'first_close_index':at(val(q['ca']))if val(q['cok'])else None,
                       'first_wick_index':at(val(q['wa']))if val(q['ok'])else None,
                       'level':val(q['root']),'adjustment_end_index':at(val(q['counter_age'])),
                       'adjustment_end_price':val(q['counter']),
                       'adjustment_shape_code':shape,'adjustment_shape_label':ADJUSTMENT_TYPES[shape],
                       'simple_A_close_confirmed':bool(val(key+'QSIMPLE'+str(j))),
                       'ongoing':ongoing,'hidden':bool(val(q['hidden']))})
        groups.append({'key':key,'owner':{'MW':'工作范围','SW':'主级当前段','PW':'主级前一段',
                                        'GW':'最近有效子级的最后一段','E1':'较早第1段完整主浪','E2':'较早第2段完整主浪','E3':'较早第3段完整主浪'}[key],
                       'owner_start_index':at(val(key+'OS')),'owner_end_index':at(val(key+'OE')),
                       'direction':val(schema['direction']),'count':length,
                       'pending':bool(val(schema['pending'])),'first_Q_closed':bool(val(schema['proof'])),
                       'merge_mode':val(key+'MERGEMODE'),'H1':val(schema['h1'])if length else None,
                       'points':points,'Q':qs,
                       'retracements_percent':{str(i):val(key+'RET'+str(i))for i in (2,4,6)if i<length}})
    c=correction_math(Builder())
    correction={key:val(c[key])for key in ('known','code','length','root','root_age','end','end_age',
                                          'confirmed','confirm_age','failed','fail_age','active','broken')}
    correction['confirmation_index']=at(correction['confirm_age'])if correction['confirmed']else None
    correction['failure_index']=at(correction['fail_age'])if correction['failed']or correction['broken']else None
    plan={key:val('PR'+key.upper())for key in ('dir','regime','phase','proof','ton','fon','h','base','origin')}
    plan['projection_direction']=plan['dir'];plan['dir']=val('PRDISPLAYDIR')
    plan['fib_anchors']={'start_price':val('PRFSTART'),'end_price':val('PRFEND'),
                         'start_index':at(val('PRFAGE')),'end_index':None if val('MANF')else at(val('PRFENDAGE')),
                         'manual':bool(val('MANF'))}
    plan['legacy_fib_calculation_eligible']=bool(val('FON'))
    plan['retracement_percent']=val('PRRETRACE')
    plan['market_type']={0:'待确认',1:'趋势',3:'震荡'}[int(plan['regime'])]
    plan['last_leg_type']={0:'待确认',1:'主浪',2:'回调',3:'震荡段'}[int(plan['phase'])]
    plan['fib_levels']=[{'ratio':ratio,'price':val('PRF'+str(i))}for i,ratio in enumerate((0,.236,.382,.5,.618,.786,1))]if plan['fon']else []
    plan['targets']=[{'ratio':ratio,'price':val('PRT'+str(i)),
                     'available':bool(val('PROK'+str(i))),'displayed':bool(val('PRSHOW'+str(i)))}
                    for i,ratio in enumerate((1.208,1.618,2,2.382,2.618,3,3.236,4.236),1)]
    from p_complete import MAX_Q
    p_trace={'visible':bool(val('PCON')),'scope_start_index':at(val('PCSTART')),
             'scope_end_index':at(val('PCSTOP')),'start_price':val('PCFIRST'),'end_price':val('PCLAST'),
             'active_tip':bool(val('PCACTIVE')),'details_limited':bool(val('PCOVERFLOW')or val('PCCLIPPED')),
             'numbering':'chronological P legs; a count alone is not proof of a 5/7-wave degree',
             'segments':[{'start_index':int(l['start'][0]),'start_price':float(l['start'][1]),
                          'end_index':int(l['end'][0]),'end_price':float(l['end'][1])}
                         for l in result['lines']if l['function']=='POLYLINE'and l['color']=='COLORLIBLUE'],
             'Q':[]}
    for i in range(MAX_Q):
        k='PCQ'+str(i)
        if not val(k+'VALID'):continue
        p_trace['Q'].append({'ordinal':int(val(k+'INDEX')),'root_index':at(val(k+'ROOTAGE')),
                             'root_price':val(k+'ROOT'),'end_index':at(val(k+'BOUNDARY')),
                             'length_excluding_root':val(k+'N'),'closed':bool(val(k+'COMPLETE')),
                             'counter_index':at(val(k+'COUNTERAGE')),'counter_price':val(k+'COUNTER'),
                             'retracement_percent':val(k+'RET'),'adjustment_code':val(k+'SHAPE')})
    return {'release':'1.1.4','bars':n,'native_client_run':False,
            'mode':'current-prefix retrospective drawing, not historical trade signals',
            'catalog_sha256':hashlib.sha256(RAW).hexdigest(),
            'formula_normalized_utf8_sha256':hashlib.sha256((source if source is not None else formula_source()).encode()).hexdigest(),
            'ohlc_float64_sha256':hashlib.sha256(np.column_stack([data[k]for k in('O','H','L','C')]).astype('<f8').tobytes()).hexdigest(),
            'work_direction':val('WD'),'phase':val('PARENTPHASE'),'correction_context_phase':val('PHASE'),'groups':groups,
            'complete_P':p_trace,'current_correction':correction,'price_plan':plan,
            'turning_observations':[{'bar_index':l['x'],'price':l['y'],'label':l['text']}for l in result['labels']if l['text'].startswith(('转折观察·','OBS '))],
            'segments':len(result['lines']),'labels':len(result['labels']),
            'fib_segments':sum(l['color']=='COLORFFCC00'for l in result['lines']),
            'target_segments':sum(l['color']=='COLORFF9900'for l in result['lines'])}
