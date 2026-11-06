"""Export an inspectable current-prefix trace; never connects to an account."""
from pathlib import Path
import argparse
import csv
import hashlib
import json

import numpy as np

from catalog import RAW
from generate import formula_source,formula_names
from plot_audit import audit,save_preview
from scenarios import NAMES,sample


def snapshot(data,result,mapping=None,source=None):
    """Trace exactly the displayed M/P/S owners, intervals and ruler."""
    from interval_layers import layers as current_layers
    from interval_render import SHAPES
    names=formula_names()if mapping is None else mapping
    n=len(data['C']);env=result['env'];schemas=current_layers()[1]
    def val(name):
        if name in ('0','1'):return float(name)
        v=float(env[names.get(name,name)][-1])
        return v if np.isfinite(v)else None
    def at(age):return n-1-int(age)
    def scope(s):
        visible=bool(val(s['on']))
        if not visible:
            return dict(visible=False,direction=val(s['direction']),start_index=None,end_index=None,
                        start_price=None,end_price=None)
        return {'visible':True,'direction':val(s['direction']),
                'start_index':at(val(s['start'])),'end_index':at(val(s['end'])),
                'start_price':val(s['first']),'end_price':val(s['last'])}
    groups=[]
    for label in ('M','P','S'):
        t=schemas[label];s=t['scope'];count=int(val(t['count']));h1=val(t['h1'])
        qs=[];base=val(s['first']);points=[{'wave':0,'bar_index':at(val(s['start'])),'price':base}]
        for i,q in enumerate(t['q']):
            if i>=count or not val(q['show']):continue
            root,counter=val(q['r']),val(q['p']);shape=int(val(q['shape']['code']))
            qs.append({'ordinal':i+1,'first_uses_close':i==0,
                       'start_index':at(val(q['a'])),'end_index':at(val(q['boundary'])),
                       'candles_excluding_start':val(q['n']),
                       'root_price':root,'counter_index':at(val(q['t'])),'counter_price':counter,
                       'closed':bool(val(q['ch']if i==0 else q['wh'])),
                       'retracement_percent':abs(counter-root)/max(abs(root-base),1e-7)*100,
                       'actual_H1_multiple':(root-base)*val(s['direction'])/h1 if val(t['proof'])and h1 else None,
                       'adjustment_shape_code':shape,'adjustment_shape_label':SHAPES[shape]})
            points.extend([{'wave':2*i+1,'bar_index':at(val(q['a'])),'price':root},
                           {'wave':2*i+2,'bar_index':at(val(q['t'])),'price':counter}])
            base=counter
        points.append({'wave':2*count+1,'bar_index':at(val(s['end'])),'price':val(s['last'])})
        groups.append({'key':label,'owner':scope(s),'intervals':count,'H1':h1 if val(t['proof'])else None,
                       'first_Q_closed':bool(val(t['proof'])),'classification':int(val(t['kind'])),
                       'pending':not bool(val(t['kind']))or bool(val(t['ambiguous'])),
                       'details_limited':bool(val(t['limited'])or val(t['ambiguous'])),
                       'actual_end_H1_multiple':(val(s['last'])-val(t['base']))*val(s['direction'])/h1 if val(t['proof'])and h1 else None,
                       'points':points if val(s['on'])else [],'Q':qs})
    h=schemas['hierarchy'];p=h['P'];phase=int(val(h['phase']))
    plan={key:val(schemas['plan'][key])for key in ('ton','fon','h1','proof','direction','phase','ret')}
    plan.update({'regime':val(h['regime']),'market_type':{0:'待确认',1:'趋势',3:'震荡'}.get(int(val(h['regime'])),'待确认'),
                 'last_leg_type':{0:'待确认',1:'主浪',2:'回调',3:'震荡段'}[phase],
                 'fib_anchors':scope(p),'fib_levels':[{'ratio':r,'price':val('IPF'+str(i))}for i,r in enumerate((0,.236,.382,.5,.618,.786,1))]if plan['fon']else [],
                 'targets':[{'ratio':r,'price':val('IPT'+str(i)),'displayed':bool(val('IPSHOW'+str(i)))}for i,r in enumerate((1.208,1.618,2,2.382,2.618,3,3.236,4.236),1)]})
    return {'release':'1.2.1','bars':n,'native_client_run':False,
            'mode':'current-prefix retrospective drawing, not historical trade signals',
            'catalog_sha256':hashlib.sha256(RAW).hexdigest(),
            'formula_normalized_utf8_sha256':hashlib.sha256((source if source is not None else formula_source()).encode()).hexdigest(),
            'ohlc_float64_sha256':hashlib.sha256(np.column_stack([data[k]for k in('O','H','L','C')]).astype('<f8').tobytes()).hexdigest(),
            'groups':groups,'price_plan':plan,'current_correction_visible':bool(val(schemas['S']['scope']['on'])),
            'turning_observations':[{'bar_index':l['x'],'price':l['y'],'label':l['text']}for l in result['labels']if ' RV'in l['text']],
            'segments':len(result['lines']),'labels':len(result['labels']),
            'fib_segments':int(sum(l['color']=='COLORFFCC00'and l['start'][1]==l['end'][1]for l in result['lines'])),
            'target_segments':sum(l['color']=='COLORFF9900'for l in result['lines'])}


def read_csv(path):
    with Path(path).open(encoding='utf-8-sig',newline='')as stream:
        rows=list(csv.DictReader(stream))
    if not rows:raise ValueError('CSV contains no candles')
    header={k.lower():k for k in rows[0]}
    data={}
    for short,long in [('O','open'),('H','high'),('L','low'),('C','close')]:
        key=header.get(short.lower(),header.get(long))
        if key is None:raise ValueError('CSV needs O,H,L,C or open,high,low,close headers')
        data[short]=np.array([float(row[key])for row in rows])
    matrix=np.column_stack(list(data.values()))
    if not np.isfinite(matrix).all():raise ValueError('CSV contains nonfinite OHLC')
    if np.any(data['H']<np.maximum(data['O'],data['C']))or np.any(data['L']>np.minimum(data['O'],data['C'])):
        raise ValueError('CSV violates high/low OHLC bounds')
    return data


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    group=parser.add_mutually_exclusive_group()
    group.add_argument('--scenario',choices=NAMES,default='fall_rebound')
    group.add_argument('--csv',type=Path)
    parser.add_argument('--output',type=Path,default=Path('structure_snapshot.json'))
    parser.add_argument('--preview',type=Path)
    options=parser.parse_args()
    data,settings=(read_csv(options.csv),None)if options.csv else sample(options.scenario)
    from export_native import load_primary
    source,profile=load_primary()
    result=audit(source,data,settings,analysis_end_line=profile['analysis_end_line'])
    trace=snapshot(data,result,source=source)
    trace['synthetic_ohlc_only']=options.csv is None
    trace['scenario']=None if options.csv else options.scenario
    options.output.parent.mkdir(parents=True,exist_ok=True)
    options.output.write_text(json.dumps(trace,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    if options.preview:
        options.preview.parent.mkdir(parents=True,exist_ok=True)
        save_preview(source,data,options.preview,settings,last=360,analysis_end_line=profile['analysis_end_line'])
    print(options.output)


if __name__=='__main__':main()
