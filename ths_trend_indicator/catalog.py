"""Read the user's client-exported function reference as DATA, never as code."""
from pathlib import Path
import hashlib
import json
import re

PATH = Path(__file__).resolve().parent/'reference'/'all_func.json'
RAW = PATH.read_bytes()
DOCUMENT = json.loads(RAW.decode('utf-8-sig'))
if DOCUMENT.get('Code') != 0 or not isinstance(DOCUMENT.get('Data'), list):
    raise ValueError('invalid client function export')
RECORDS = DOCUMENT['Data']
FUNCTIONS = {}
for record in RECORDS:
    FUNCTIONS.setdefault(record['Name'].upper(), record)


def definition(name):
    return FUNCTIONS[name.upper()]


def parameter_bounds(name):
    """Get fixed/variadic arity from FuncDefine; SORT includes 1..30 values."""
    entry = definition(name)
    text = entry.get('FuncDefine', '').strip().rstrip(';')
    if '(' not in text:
        return 0, 0
    params = text.split('(',1)[1].rsplit(')',1)[0].strip()
    if not params: return 0, 0
    if name.upper() in {'SORT','SORTPOS'}:
        return 3, 32
    if name.upper() in {'MAX1','MIN1'}:
        return 1, 30
    # Only fixed forms used by this package are inferred. Never evaluate docs.
    return len(params.split(',')), len(params.split(','))


def evidence(names):
    used = {}
    for name in sorted(set(names)):
        entry = definition(name)
        used[name] = {key:entry.get(key,'') for key in
                      ('Id','Name','FuncDefine','Category','Instruction','Remark','ParamDesp')}
    return {'source':'user-exported all_func.json','source_sha256':hashlib.sha256(RAW).hexdigest(),
            'records':len(RECORDS),'unique_names':len(FUNCTIONS),'used':used}


def safe_internal_name(name):
    return (bool(re.fullmatch(r'[A-Z][A-Z0-9_]*',name)) and name not in FUNCTIONS)
