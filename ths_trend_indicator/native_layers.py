"""Generate bounded independent main-chart overlays from one formula model.

The official content check observed on 2026-11-05 accepts 80000 characters
and rejects 80001, even for repeated PN assignments. Keep 2000 characters
of headroom. This is an observed service rule, not a documented compiler
limit. Each drawing belongs to exactly one file; dependencies are copied,
never shared via another indicator or an account/network lookup.
"""
from collections import Counter
import hashlib

from check_formula import (Parser,Statement,NUMBER,CONDITION,STRING,COLOR,
                           SIGNATURES,PREDEFINED_TYPES,walk,check)
from compatibility import variable_names,render,SETTINGS
from export_native import OUTPUTS,ascii_label

MAX_INSTALL_BYTES=78000


def typed_model(raw):
    """Use documented IF with explicit condition/number types and safe REF.

    Stored conditions remain conditions. COUNT(...,1) is used only where
    arithmetic needs numbers. No fixed parameter values, algorithm changes,
    future-call movement, or nullable arithmetic masking are introduced.
    """
    names=variable_names(raw)|OUTPUTS
    types=dict(PREDEFINED_TYPES)
    def ident(name):return ('identifier',names.get(name,name))
    def is_condition(node):
        if node[0]=='identifier':return types.get(node[1])==CONDITION
        if node[0]=='binary':return node[1]not in ('+','-','*','/')
        if node[0]=='call':return SIGNATURES[node[1]][1]==CONDITION
        return False
    def num(node):
        kind=node[0]
        if kind=='number':return node
        if kind=='identifier':
            value=ident(node[1])
            return ('call','COUNT',[value,('number',1.)])if types.get(node[1])==CONDITION else value
        if kind=='unary':return ('unary',node[1],num(node[2]))
        if kind=='binary':
            if node[1]in ('+','-','*','/'):
                return ('binary',node[1],num(node[2]),num(node[3]))
            return ('call','COUNT',[cond(node),('number',1.)])
        if kind=='call':
            value=call(node)
            return ('call','COUNT',[value,('number',1.)])if SIGNATURES[node[1]][1]==CONDITION else value
        raise ValueError('not a numerical formula expression')
    def cond(node):
        if node[0]=='identifier'and types.get(node[1])==CONDITION:return ident(node[1])
        if node[0]=='binary':
            if node[1]in ('AND','OR'):return ('binary',node[1],cond(node[2]),cond(node[3]))
            if node[1]in ('=','<>','>','<','>=','<='):
                return ('binary',node[1],num(node[2]),num(node[3]))
        if node[0]=='call'and SIGNATURES[node[1]][1]==CONDITION:return call(node)
        return ('binary','<>',num(node),('number',0.))
    def call(node):
        fn,args=node[1],node[2]
        kinds=SIGNATURES[fn][0]
        if fn in ('SORT','SORTPOS','MAX1','MIN1'):kinds=(NUMBER,)*len(args)
        if len(args)!=len(kinds):raise ValueError('invalid function arity')
        out=[]
        for arg,kind in zip(args,kinds):
            if kind==NUMBER:out.append(num(arg))
            elif kind==CONDITION:out.append(cond(arg))
            elif kind==STRING:out.append(('string',ascii_label(arg[1])))
            elif kind==COLOR:out.append(ident(arg[1]))
            else:raise ValueError('unsupported argument type')
        if fn=='REF':
            bound=('binary','-',ident('BC'),('number',1.))
            window=out[1]
            # Some templates already explicitly clamp to BC-1.
            if window[0:2]==('call','MIN')and bound in window[2]:
                remaining=[v for v in window[2]if v!=bound]
                window=remaining[0]if remaining else bound
            out[1]=('call','MIN',[bound,('call','MAX',[('number',0.),window])])
        return ('call',fn,out)
    statements=[]
    for statement in Parser(raw).parse():
        typ=CONDITION if is_condition(statement.expression)else NUMBER
        expression=(cond(statement.expression)if typ==CONDITION else num(statement.expression))if statement.name else call(statement.expression)
        statements.append(Statement(names.get(statement.name,statement.name),expression,statement.output,statement.styles,statement.line))
        if statement.name:types[statement.name]=typ
    marker=raw.find('{ END_ANALYSIS')
    if marker<0:raise ValueError('missing causal boundary')
    # The native parser rejects IF/IFELSE as nested function arguments
    # (ErrCode 15), despite the catalog's general expression examples.
    # Name each nested selector BEFORE its consumer. Keep its original
    # analysis/drawing phase and reuse only an already defined expression.
    result=[];cached={};serial=0
    for statement in statements:
        def hoist(node,top=False):
            nonlocal serial
            if node[0]=='call':
                value=('call',node[1],[hoist(a)for a in node[2]])
                if node[1]in ('IF','IFELSE')and not top:
                    key=render(value)
                    if key not in cached:
                        serial+=1;name=f'J{serial}'
                        result.append(Statement(name,value,False,(),statement.line))
                        cached[key]=name
                    return ('identifier',cached[key])
                return value
            if node[0]=='binary':return ('binary',node[1],hoist(node[2]),hoist(node[3]))
            if node[0]=='unary':return ('unary',node[1],hoist(node[2]))
            return node
        expression=hoist(statement.expression,top=bool(statement.name))
        result.append(Statement(statement.name,expression,statement.output,statement.styles,statement.line))
        if statement.name and expression[0]=='call'and expression[1]in ('IF','IFELSE'):
            cached.setdefault(render(expression),statement.name)
    return result,raw.count('\n',0,marker)+1


def emit(statement):
    return ((statement.name+(':'if statement.output else':='))if statement.name else'')+render(statement.expression)+(','+','.join(statement.styles)if statement.styles else'')+';'


def generate(raw,max_bytes=MAX_INSTALL_BYTES):
    statements,boundary=typed_model(raw)
    lines=[emit(s)for s in statements]
    definitions={s.name:i for i,s in enumerate(statements)if s.name}
    refs=[{definitions[n[1]]for n in walk(s.expression)if n[0]=='identifier'and n[1]in definitions}for s in statements]
    memo={}
    def closure(i):
        if i not in memo:
            needed={i}
            for dependency in refs[i]:needed.update(closure(dependency))
            memo[i]=needed
        return memo[i]
    size=lambda needed:sum(len(lines[i])+2 for i in needed)
    parameters={i for i,s in enumerate(statements)if s.name in SETTINGS}
    roots=[i for i,s in enumerate(statements)if not s.name or s.output]
    bins=[]
    for i in sorted(roots,key=lambda j:(-size(closure(j)|parameters),j)):
        needed=closure(i)|parameters
        if size(needed)>max_bytes:raise ValueError('one drawing exceeds the installation budget')
        candidates=[(size(group['needed']|needed)-size(group['needed']),j)for j,group in enumerate(bins)if size(group['needed']|needed)<=max_bytes]
        if candidates:
            _,j=min(candidates)
            bins[j]['needed'].update(needed);bins[j]['roots'].append(i)
        else:bins.append({'needed':set(needed),'roots':[i]})
    bins.sort(key=lambda group:min(group['roots']))
    files={}
    for number,group in enumerate(bins,1):
        indices=sorted(group['needed'])
        text='\n'.join(lines[i]for i in indices)+'\n'
        phase=sum(statements[i].line<=boundary for i in indices)
        profile={'analysis_end_line':phase,'ascii_only':True,'bom':False,'comments':False,'encoding':'ascii','role':'install_layer',
                 'root_ids':sorted(group['roots']),'parameter_names':sorted(SETTINGS),'max_install_bytes':max_bytes}
        check(text,analysis_end_line=phase)
        data=text.replace('\n','\r\n').encode('ascii')
        if len(data)>max_bytes:raise ValueError('installation byte budget exceeded')
        files[f'EWT{number:02}.txt']=(data,profile|{'sha256':hashlib.sha256(data).hexdigest()})
    observed=Counter(i for group in bins for i in group['roots'])
    if observed!=Counter(roots):raise ValueError('a drawing was lost or duplicated')
    return files,{'drawing_roots':len(roots),'layers':len(files),'all_roots_exactly_once':True,
                  'max_install_bytes':max_bytes,'observed_service_boundary':{'accepted_characters':80000,'rejected_characters':80001,'observed_date':'2026-11-05','documented_compiler_limit':False},
                  'model':'typed IF; same settings and algorithms; dependency pruning only'}
