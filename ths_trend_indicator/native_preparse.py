"""Offline syntax/shape checks using an installed QHT parser DLL.

This calls only create_instance, get_pre_parse_res, get_shapes and their
release functions. It never starts the client, calls calculation/trading
functions, logs in, or sends source to a network service. The proprietary
DLL is NOT distributed with this project. Run with 64-bit Windows Python.
"""
import argparse
import ctypes
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import platform
import sys

ROOT=Path(__file__).resolve().parent


def shape_summary(raw):
    """Fail on malformed/nonfinite native geometry, even if ErrCode was 0."""
    try:
        shapes=json.loads(raw)
        if not isinstance(shapes,list):
            raise ValueError('native shapes must be an array')
        offsets=[]
        counts={}
        for shape in shapes:
            name=shape.get('ShapeName','unknown')
            counts[name]=counts.get(name,0)+1
            for key in ('HorizontalOffset','VerticalOffset'):
                if key in shape:
                    value=shape[key]
                    if not isinstance(value,(float,int)) or not math.isfinite(value) or abs(value)>100000:
                        raise ValueError('invalid native '+key)
                    offsets.append(value)
        return {'status':'passed','shapes':len(shapes),'by_shape':counts,
                'constant_offsets_checked':len(offsets),
                'max_absolute_offset':max(map(abs,offsets),default=0)}
    except (ValueError,TypeError,KeyError) as error:
        return {'status':'failed','reason':str(error)}


class NativeParser:
    def __init__(self,path):
        self.path=Path(path).resolve()
        self.lib=ctypes.CDLL(str(self.path))
        void=ctypes.c_void_p
        uint=ctypes.c_uint32
        self.lib.create_instance.argtypes=[]
        self.lib.create_instance.restype=void
        self.lib.delete_instance.argtypes=[void]
        self.lib.delete_instance.restype=None
        self.lib.release_ptr_val.argtypes=[void]
        self.lib.release_ptr_val.restype=None
        self.lib.get_pre_parse_res.argtypes=[void,void,void,void,uint,uint,uint,ctypes.POINTER(uint)]
        self.lib.get_pre_parse_res.restype=void
        self.lib.get_shapes.argtypes=[void,ctypes.POINTER(uint)]
        self.lib.get_shapes.restype=void

    def take(self,pointer,size):
        if not pointer:
            raise RuntimeError('native parser returned a null pointer')
        try:
            if not 0<=size<=64*1024*1024:
                raise RuntimeError('unexpected native result length')
            return ctypes.string_at(pointer,size)
        finally:
            self.lib.release_ptr_val(pointer)

    def check(self,source):
        encoded=source.encode('utf-8')
        buffers=[ctypes.create_string_buffer(value)for value in (encoded,b'',b'')]
        handle=self.lib.create_instance()
        if not handle:
            raise RuntimeError('native parser could not create an instance')
        try:
            size=ctypes.c_uint32(0)
            pointer=self.lib.get_pre_parse_res(handle,*buffers,len(encoded),0,0,ctypes.byref(size))
            result=json.loads(self.take(pointer,size.value))
            shapes={'status':'not_run_parse_failed'}
            if result.get('ErrCode')==0:
                size=ctypes.c_uint32(0)
                pointer=self.lib.get_shapes(handle,ctypes.byref(size))
                shapes=shape_summary(self.take(pointer,size.value))
            return {'parser_input_utf8_sha256':hashlib.sha256(encoded).hexdigest(),
                    'native_response':result,'shape_validation':shapes,
                    'passed':result.get('ErrCode')==0 and shapes['status']=='passed'}
        finally:
            self.lib.delete_instance(handle)


CONTROLS={
    'valid_assignment':('PN:=2;\nCHK:PN;',0,'passed'),
    'valid_unicode_private':('甲:=MA(C,2);乙:=甲+1;CHK:乙;',0,'passed'),
    'valid_compact_boolean':('AA:=COUNT(C>O&&H>=C||C=O,1);CHK:AA;',0,'passed'),
    'valid_ma':('CHK:MA(C,5);',0,'passed'),
    'valid_custom_color':('POLYLINE(C>O,C,COLORFFCC00);',0,'passed'),
    'invalid_nested_selection':('CHK:MAX(1,IF(C>O,H,L));',15,'not_run_parse_failed'),
    'valid_named_selection':('AA:=IF(C>O,H,L);CHK:MAX(1,AA);',0,'passed'),
    'undefined_function':('CHK:UNKNOWNFUNCTION(C);',2,'not_run_parse_failed'),
    'invalid_shift_variable':('AA:=BARPOS;\nDRAWSHIFTNUMBER(C>O,H,C,2,COLORYELLOW,0,AA);',2012,'not_run_parse_failed'),
    'invalid_wrapped_shift':('AA:=BARPOS;\nDRAWSHIFTNUMBER(C>O,H,C,2,COLORYELLOW,0,INTPART(AA));',0,'failed'),
    'invalid_ktext_variable':("AA:=BARPOS;\nKTEXT(C>O,AA,H,1,COLORYELLOW,'LABEL');",0,'failed'),
    'valid_target_candle':("VX:=DATACOUNT-BARPOS;\nDRAWNUMBER(VX=3,REFX1(H,100000),REFX1(C,100000),2,COLORYELLOW);\nKTEXT(VX=3,0,REFX1(H,100000),1,COLORYELLOW,'LABEL');",0,'passed'),
}


def main():
    if hasattr(sys.stdout,'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8',errors='backslashreplace')
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--dll',type=Path,required=True)
    ap.add_argument('--parser-version',default='not_provided')
    ap.add_argument('--all',action='store_true',help='check every file listed in export profile')
    ap.add_argument('--install',action='store_true',help='check only the bounded installation overlays')
    ap.add_argument('--source',type=Path)
    ap.add_argument('--encoding',default='utf-8')
    ap.add_argument('--report',type=Path,required=True)
    args=ap.parse_args()
    if sum((args.all,args.install,bool(args.source)))!=1:
        ap.error('choose exactly one of --all, --install and --source')
    parser=NativeParser(args.dll)
    controls={}
    for name,(source,code,shape_status) in CONTROLS.items():
        result=parser.check(source)
        controls[name]=result|{'expected_code':code,'expected_shape_status':shape_status,
                               'matches_expectation':result['native_response'].get('ErrCode')==code and result['shape_validation']['status']==shape_status}
    profiles=json.loads((ROOT/'导出配置.json').read_text(encoding='utf-8')) if args.all or args.install else None
    selected=profiles['install'] if args.install else list(profiles['files'])if profiles else []
    sources=[(ROOT/'formulas'/name,profiles['files'][name]['encoding'])for name in selected] if profiles else [(args.source,args.encoding)]
    files={}
    for path,encoding in sources:
        raw=path.read_bytes()
        name=path.relative_to(ROOT/'formulas').as_posix()if profiles else path.name
        files[name]={'source_bytes':len(raw),'source_encoding':encoding,'source_sha256':hashlib.sha256(raw).hexdigest(),**parser.check(raw.decode(encoding))}
        print(name,files[name]['native_response'].get('ErrCode'),files[name]['shape_validation']['status'],flush=True)
    export_profile=profiles or json.loads((ROOT/'导出配置.json').read_text(encoding='utf-8'))
    report={'checked_at_utc':datetime.now(timezone.utc).isoformat(),
            'release':export_profile['release'],
            'check_kind':'official_native_syntax_preparse_and_shape_description',
            'execution_platform':platform.platform(),'python':platform.python_version(),
            'parser_client_version':args.parser_version,'parser_dll_sha256':hashlib.sha256(args.dll.read_bytes()).hexdigest(),
            'harness_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'target_client_version':'4.39.1.2','target_client_ui_run':False,
            'network_content_check':'not_run','account_login':False,'market_or_trade_calculation':False,
            'controls':controls,'files':files,
            'passed':all(v['matches_expectation']for v in controls.values()) and all(v['passed']for v in files.values()),
            'limitations':['Parser version differs from the target client','Does not verify UI save/content checks, full native rendering, or speed','Shape descriptions are checked before market-data calculation']}
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    if not report['passed']:
        raise SystemExit(1)


if __name__=='__main__':
    main()
