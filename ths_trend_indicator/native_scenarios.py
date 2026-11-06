"""Prepare synthetic native cases and compare their actual chart geometry.

Preparation/comparison run locally without a DLL or network. Run the prepared
cases separately with native_runtime.py on 64-bit Windows and an installed
compatible official DLL. No proprietary binaries are distributed.
"""
import argparse
from collections import Counter
from datetime import datetime,timezone
import hashlib
import json
import math
from pathlib import Path
import re

from check_formula import Parser
from plot_audit import audit
from scenarios import NAMES,sample
from native_runtime import rows_from_ohlc

ROOT=Path(__file__).resolve().parent


def cases():
    profile=json.loads((ROOT/'导出配置.json').read_text(encoding='utf8'))
    for scenario in NAMES:
        data,settings=sample(scenario)
        for name in profile['install']:
            source=(ROOT/'formulas'/name).read_bytes().decode(profile['files'][name]['encoding'])
            for key,value in (settings or {}).items():
                # A complete negative parameter assignment must not wrap
                # its entire RHS in parentheses (native NULL regression).
                expression=str(value)if value>=0 else '0-'+str(abs(value))
                source,count=re.subn(r'^'+re.escape(key)+r':=[^;]+;',key+':='+expression+';',source,flags=re.M)
                if count!=1:raise ValueError('missing unique setting '+key)
            roots=[{'function':s.expression[1]if not s.name else 'OUTPUT','output':s.output}
                   for s in Parser(source).parse()if not s.name or s.output]
            yield {'name':scenario+'/'+name,'source':source,
                   'data':{key:list(map(float,value))for key,value in data.items()},'roots':roots}


def line_key(item):
    return (item['function'],tuple((int(x),round(float(y),7))for x,y in (item['start'],item['end'])))


def label_key(item):
    font=item.get('font_size')if item['function']in ('KTEXT','DRAWNUMBER')else None
    return (item['function'],int(item['x']),round(float(item['y']),7),item['text'],item['align'],font)


def compare(reports):
    expected={case['name']:case for case in cases()}
    actual={}
    harness=hashlib.sha256((ROOT/'native_runtime.py').read_bytes()).hexdigest()
    for report in reports:
        if not report.get('passed')or report.get('harness_sha256')!=harness:
            raise ValueError('native run failed or harness bytes changed')
        for case in report['cases']:
            if case['name']in actual:raise ValueError('duplicate case '+case['name'])
            actual[case['name']]=case
    if set(actual)!=set(expected):raise ValueError('native scenario set differs from current release')
    profiles=json.loads((ROOT/'导出配置.json').read_text(encoding='utf8'))
    comparisons=[]
    for name,case in expected.items():
        result=actual[name]
        if result['source_sha256']!=hashlib.sha256(case['source'].encode('utf-8')).hexdigest():
            raise ValueError('native source mismatch '+name)
        payload=json.dumps(rows_from_ohlc(case['data']),allow_nan=False).encode('ascii')
        if result['input_rows_sha256']!=hashlib.sha256(payload).hexdigest():
            raise ValueError('native input mismatch '+name)
        relative=name.split('/',1)[1]
        local=audit(case['source'],case['data'],analysis_end_line=profiles['files'][relative]['analysis_end_line'])
        comparison={'name':name,'source_sha256':result['source_sha256'],
                    'input_rows_sha256':result['input_rows_sha256'],'rows':result['rows'],
                    'parse_ms':result['parse_ms'],'calculate_ms':result['calculate_ms']}
        passed=True
        for kind,key in (('lines',line_key),('labels',label_key)):
            model=Counter(map(key,local[kind]));native=Counter(map(key,result['geometry'][kind]))
            equal=model==native;passed&=equal
            comparison[kind]={'model':sum(model.values()),'native':sum(native.values()),'matches':equal}
            if not equal:
                comparison[kind]['only_model']=list((model-native).elements())[:8]
                comparison[kind]['only_native']=list((native-model).elements())[:8]
        states={}
        for title,values in result.get('outputs',{}).items():
            if title not in ('XWD','XPH','XREG','XRPH','XRDIR','XRET'):continue
            model_value=float(local['env'][title][-1]);native_value=values[-1]
            equal=native_value is not None and math.isclose(model_value,native_value,rel_tol=1e-12,abs_tol=1e-8)
            states[title]={'model':model_value,'native':native_value,'matches':equal};passed&=equal
        comparison['last_bar_states']=states
        comparison['passed']=passed;comparisons.append(comparison)
    return {'release':profiles['release'],'checked_at_utc':datetime.now(timezone.utc).isoformat(),
            'scope':'Actual native arithmetic/coordinates on deterministic synthetic OHLC; not target-client UI, real-market interpretation, or order execution',
            'harness_sha256':harness,'comparison_harness_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'local_evaluator_sha256':{name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest()for name in ('catalog_math.py','check_formula.py','plot_audit.py')},
            'parser_client_versions':sorted({r['parser_client_version']for r in reports}),
            'parser_dll_sha256':sorted({r['parser_dll_sha256']for r in reports}),
            'target_client_ui_run':False,'account_login':False,
            'price_coordinate_rounding_decimal_places':7,
            'cases':comparisons,'passed':all(c['passed']for c in comparisons)}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    subs=parser.add_subparsers(dest='command',required=True)
    prepare=subs.add_parser('prepare');prepare.add_argument('directory',type=Path)
    prepare.add_argument('--batch-size',type=int,default=9)
    check=subs.add_parser('compare');check.add_argument('reports',type=Path,nargs='+')
    check.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.command=='prepare':
        if args.batch_size<1:parser.error('batch size must be positive')
        prepared=list(cases());args.directory.mkdir(parents=True,exist_ok=True)
        for offset in range(0,len(prepared),args.batch_size):
            path=args.directory/('geometry_batch'+str(offset//args.batch_size)+'.json')
            path.write_text(json.dumps(prepared[offset:offset+args.batch_size],ensure_ascii=True,allow_nan=False)+'\n',encoding='utf8')
            print(path)
    else:
        result=compare([json.loads(p.read_text(encoding='utf8'))for p in args.reports])
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
        print(len(result['cases']),'native comparisons; passed:',result['passed'])
        if not result['passed']:raise SystemExit(1)


if __name__=='__main__':main()
