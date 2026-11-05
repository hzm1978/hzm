"""Offline OHLC arithmetic/geometry checks using the user's installed DLL.

This never starts a trading client or logs in. It rejects trading formulas
and external-data requests before arithmetic, and disables notice callbacks.
The proprietary parser DLL is not included. Run on 64-bit Windows Python.
"""
import argparse
import ctypes as C
from datetime import datetime,timedelta,timezone
import hashlib
import json
import math
from pathlib import Path
import time
import sys

sys.path.insert(0,str(Path(__file__).resolve().parent))

from native_preparse import NativeParser


class ArithmeticParser(NativeParser):
    def __init__(self,path):
        super().__init__(path)
        lib=self.lib;ptr=C.c_void_p;uint=C.c_uint32;integer=C.c_int32
        lib.set_row_data.argtypes=[ptr,ptr,uint];lib.set_row_data.restype=None
        lib.exec_parser_cal.argtypes=[ptr,integer,C.c_bool,C.POINTER(uint)];lib.exec_parser_cal.restype=ptr
        lib.get_row_num.argtypes=[ptr];lib.get_row_num.restype=integer
        lib.get_field_val_by_index.argtypes=[ptr,integer,integer];lib.get_field_val_by_index.restype=C.c_double

    def profile(self,source,rows,roots=None):
        started=time.perf_counter();handle=self.lib.create_instance()
        size=C.c_uint32(0)
        try:
            encoded=source.encode('ascii')
            buffers=[C.create_string_buffer(v)for v in (encoded,b'',b'')]
            pointer=self.lib.get_pre_parse_res(handle,*buffers,len(encoded),0,0,C.byref(size))
            parsed=json.loads(self.take(pointer,size.value))
            parse_ms=(time.perf_counter()-started)*1000
            if parsed.get('ErrCode')!=0:raise ValueError(parsed)
            if (parsed.get('IsContainsTradeFunction')!='false' or
                    parsed.get('IsNeedRequestExtraData')!='false' or
                    parsed.get('IsNeedRequestTradeDay')!='false'):
                raise ValueError('Only offline arithmetic without trading or external data is permitted')
            payload=json.dumps(rows,allow_nan=False).encode('ascii')
            buffer=C.create_string_buffer(payload)
            self.lib.set_row_data(handle,buffer,len(payload))
            count=self.lib.get_row_num(handle)
            if count!=len(rows):raise ValueError('native row count differs from input')
            started=time.perf_counter()
            pointer=self.lib.exec_parser_cal(handle,0,False,C.byref(size))
            calculated=json.loads(self.take(pointer,size.value))
            calculate_ms=(time.perf_counter()-started)*1000
            if calculated.get('ErrCode')!=0:raise ValueError(calculated)
            pointer=self.lib.get_shapes(handle,C.byref(size))
            shapes=json.loads(self.take(pointer,size.value))
            result={'parse_ms':round(parse_ms,3),'calculate_ms':round(calculate_ms,3),
                    'rows':count,'source_sha256':hashlib.sha256(encoded).hexdigest(),
                    'input_rows_sha256':hashlib.sha256(payload).hexdigest(),
                    'native_response':calculated,'shapes':len(shapes),
                    'notice_callbacks_enabled':False,'account_login':False,
                    'trading_or_external_data':False}
            if roots is not None:
                result['geometry']=self.geometry(handle,shapes,roots,count)
                def output_value(shape,index):
                    value=self.lib.get_field_val_by_index(handle,shape['Field1Id'],index)
                    # Undefined warm-up values of NODRAW title fields are
                    # ordinary native NULLs. Preserve them explicitly; never
                    # normalize visible geometry this way.
                    return value if math.isfinite(value)else None
                result['outputs']={shape['Field1Text']:[output_value(shape,i)for i in range(count)]
                                   for shape,root in zip(shapes,roots)if root['output']}
            return result
        finally:self.lib.delete_instance(handle)

    def geometry(self,handle,shapes,roots,count):
        if len(shapes)!=len(roots):raise ValueError('native shape/root count differs')
        cached={}
        def value(field,index):return self.lib.get_field_val_by_index(handle,field,index)
        def active(field):
            if field not in cached:
                cached[field]=[i for i in range(count)if math.isfinite(value(field,i))and value(field,i)>0]
            return cached[field]
        lines=[];labels=[];line_diagnostics=[]
        for shape,root in zip(shapes,roots):
            if root['output']:continue  # NODRAW title fields, not chart strokes.
            fn=root['function'];visible=active(shape['Field1VisibleId'])
            if fn=='POLYLINE':
                for a,b in zip(visible,visible[1:]):
                    lines.append({'function':fn,'start':[a,value(shape['Field1Id'],a)],'end':[b,value(shape['Field1Id'],b)]})
            elif fn=='DRAWLINE3':
                ends=active(shape['Field2VisibleId'])
                if len(line_diagnostics)<4:
                    line_diagnostics.append({'start_indices':visible[:8],'end_indices':ends[:8],
                        'offset1':[[i,value(shape['HorizontalOffset1FieldId'],i)]for i in visible[:3]],
                        'offset2':[[i,value(shape['HorizontalOffset2FieldId'],i)]for i in ends[:3]]})
                for a in visible:
                    b=next((end for end in ends if end>a),None)
                    if b is None:continue
                    # Native shape fields already store left shifts as
                    # negative offsets (the formula arguments are positive).
                    x1=a+round(value(shape['HorizontalOffset1FieldId'],a))
                    x2=b+round(value(shape['HorizontalOffset2FieldId'],b))
                    if min(x1,x2)<0 or x1>x2:continue
                    lines.append({'function':fn,'start':[x1,value(shape['Field1Id'],a)],'end':[x2,value(shape['Field2Id'],b)]})
            elif fn in ('KTEXT','DRAWNUMBER','DRAWNUMBER1','DRAWTEXT'):
                for i in visible:
                    number=shape.get('NumberFieldId',-1)
                    text=shape.get('Text','')if number==-1 else format(value(number,i),'.'+str(shape['Digit'])+'f')
                    labels.append({'function':fn,'x':i+round(shape.get('HorizontalOffset',0)),
                                   'y':value(shape['Field1Id'],i),'text':text,
                                   'align':['left','center','right'][shape['HorizontalAlign']],
                                   'font_size':shape.get('FontSize')})
            else:raise ValueError('Unmodelled native shape: '+fn)
        examples=[]
        for shape in shapes[:5]:
            field=shape['Field1VisibleId'];counts={}
            for i in range(count):
                key=str(value(field,i));counts[key]=counts.get(key,0)+1
            examples.append({'field':field,'values':counts})
        for line in lines:
            if not all(math.isfinite(v)for p in (line['start'],line['end'])for v in p):
                raise ValueError('nonfinite visible native line')
        for label in labels:
            if not math.isfinite(label['y']):raise ValueError('nonfinite visible native label')
        return {'lines':lines,'labels':labels,'visibility_examples':examples,'line_diagnostics':line_diagnostics}


def rows_from_ohlc(data):
    n=len(data['C'])
    if not n or any(len(data[k])!=n for k in ('O','H','L','C')):raise ValueError('invalid OHLC lengths')
    rows=[]
    for i in range(n):
        values={key:float(data[key][i])for key in ('O','H','L','C')}
        if not all(math.isfinite(v)for v in values.values()):raise ValueError('nonfinite input')
        if values['H']<max(values['O'],values['C'])or values['L']>min(values['O'],values['C']):raise ValueError('invalid OHLC')
        row={'1':int((datetime(2020,1,1)+timedelta(days=i)).strftime('%Y%m%d%H%M%S'))}
        row.update({str(field):values[key]for field,key in ((7,'O'),(8,'H'),(9,'L'),(11,'C'))})
        rows.append(row)
    return rows


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--dll',type=Path,required=True)
    ap.add_argument('--cases',type=Path,required=True,help='local JSON cases with source, OHLC and optional root descriptions')
    ap.add_argument('--report',type=Path,required=True)
    args=ap.parse_args()
    parser=ArithmeticParser(args.dll)
    results=[]
    for case in json.loads(args.cases.read_text(encoding='utf8')):
        result=parser.profile(case['source'],rows_from_ohlc(case['data']),case.get('roots'))
        results.append({'name':case['name'],**result})
        print(case['name'],'parse',result['parse_ms'],'ms, calculate',result['calculate_ms'],'ms',flush=True)
    report={'checked_at_utc':datetime.now(timezone.utc).isoformat(),
            'harness_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'parser_dll_sha256':hashlib.sha256(args.dll.read_bytes()).hexdigest(),
            'parser_client_version':{'c32ededab8d2bfd32d86ea50b08e7ac249429c98642cd040637a619039629c72':'5.4.0.5'}.get(hashlib.sha256(args.dll.read_bytes()).hexdigest(),'unverified'),
            'target_client_ui_run':False,
            'scope':'Offline arithmetic on synthetic OHLC, not UI performance or account trading',
            'cases':results,'passed':True}
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf8')


if __name__=='__main__':main()
