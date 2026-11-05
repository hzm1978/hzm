"""Generate bounded independent main-chart overlays from one formula model.

The official content check observed on 2026-11-05 accepts 80000 characters
and rejects 80001, even for repeated PN assignments. Keep 1000 characters
of headroom. This is an observed service rule, not a documented compiler
limit. Each drawing belongs to exactly one file; dependencies are copied,
never shared via another indicator or an account/network lookup.
"""
from collections import Counter
import hashlib
import re

from check_formula import (Parser,Statement,NUMBER,CONDITION,STRING,COLOR,
                           SIGNATURES,PREDEFINED_TYPES,walk,check,RESERVED,CLIENT_FUNCTIONS)
from compatibility import variable_names,render,SETTINGS
from export_native import OUTPUTS,ascii_label

MAX_INSTALL_BYTES=79000
FIXED_DISPLAY={'SHOWBOOK':1.,'DETAIL':0.}


def specialize(statements):
    """Remove only display branches disabled in the standard book profile."""
    values={name:('number',value)for name,value in FIXED_DISPLAY.items()}
    def literal(node):
        if node[0]=='number':return node[1]
        if node[0]=='binary'and node[2][0]=='number'and node[3][0]=='number':
            a,b=node[2][1],node[3][1]
            operations={'=':lambda:a==b,'<>':lambda:a!=b,'>':lambda:a>b,'<':lambda:a<b,'>=':lambda:a>=b,'<=':lambda:a<=b}
            if node[1]in operations:return operations[node[1]]()
        return None
    def boolean(value):return ('binary','=',('number',1.),('number',1. if value else 0.))
    def fold(node):
        if node[0]=='identifier':return values.get(node[1],node)
        if node[0]=='binary':
            a,b=fold(node[2]),fold(node[3]);x,y=literal(a),literal(b);op=node[1]
            if op in ('AND','OR'):
                if op=='AND':
                    if x is not None:return b if x else boolean(False)
                    if y is not None:return a if y else boolean(False)
                else:
                    if x is not None:return boolean(True)if x else b
                    if y is not None:return boolean(True)if y else a
            if x is not None and y is not None:
                if op in ('=','<>','>','<','>=','<='):return boolean(literal(('binary',op,('number',x),('number',y))))
                if op=='+' :return ('number',float(x+y))
                if op=='-' :return ('number',float(x-y))
                if op=='*' :return ('number',float(x*y))
                if op=='/'and y!=0:return ('number',float(x/y))
            return ('binary',op,a,b)
        if node[0]=='unary':return ('unary',node[1],fold(node[2]))
        if node[0]=='call':
            args=[fold(a)for a in node[2]];fn=node[1]
            if fn in ('IF','IFELSE')and literal(args[0])is not None:return args[1]if literal(args[0])else args[2]
            if fn in ('IF','IFELSE')and args[1]==args[2]:return args[1]
            if fn in ('MAX','MIN')and args[0]==args[1]:return args[0]
            if fn=='COUNT'and literal(args[0])is not None:
                if not literal(args[0]):return ('number',0.)
                if args[1]==('number',1.):return ('number',1.)
            if fn in ('REF','REFX1')and literal(args[0])is not None:return args[0]
            return ('call',fn,args)
        return node
    output=[];omitted=0
    for s in statements:
        if s.name in FIXED_DISPLAY:continue
        expression=fold(s.expression)
        if s.name and s.name not in SETTINGS and literal(expression)is not None:values[s.name]=expression
        if not s.name and expression[0]=='call'and literal(expression[2][0])is not None and not literal(expression[2][0]):
            omitted+=1;continue
        output.append(Statement(s.name,expression,s.output,s.styles,s.line))
    return output,omitted


def short_names(statements):
    """Shorten private identifiers, preserving settings and title outputs."""
    protected=set(SETTINGS)|set(OUTPUTS.values())
    reserved=protected|set(PREDEFINED_TYPES)|RESERVED|set(CLIENT_FUNCTIONS)|{'AND','OR','BEGIN','END','THEN','ELSE'}
    # Frequently referenced fields receive the shortest legal names first.
    # Preserve volume's common one-letter alias even if absent in the export.
    reserved.update({'V','LR','PR','TP','TV'})
    alphabet='ABCDEFGHIJKLMNOPQRSTUVWXYZ';tail=alphabet+'0123456789'
    def candidates():
        yield from alphabet
        for a in alphabet:
            for b in tail:yield a+b
        for a in alphabet:
            for b in tail:
                for c in tail:yield a+b+c
    available=(name for name in candidates()if name not in reserved)
    frequency=Counter(n[1]for s in statements for n in walk(s.expression)if n[0]=='identifier')
    private=[s.name for s in statements if s.name and s.name not in protected]
    names={name:next(available)for name in sorted(private,key=lambda name:(-frequency[name],name))}
    def rename(node):
        if node[0]=='identifier':return ('identifier',names.get(node[1],node[1]))
        if node[0]=='call':return ('call',node[1],[rename(a)for a in node[2]])
        if node[0]=='binary':return ('binary',node[1],rename(node[2]),rename(node[3]))
        if node[0]=='unary':return ('unary',node[1],rename(node[2]))
        return node
    return [Statement(names.get(s.name,s.name),rename(s.expression),s.output,s.styles,s.line)for s in statements],names


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
    # A frozen value is already equal on every history bar. Do not freeze
    # it repeatedly for each text/number or wrap constant arithmetic in
    # another last-bar guard. REF windows are already clipped to history.
    constants=set(SETTINGS)|{'DATACOUNT'}
    elementwise={'ABS','MAX','MIN','MAX1','MIN1','SIGN','INTPART','ISNULL','IF','IFELSE'}
    def constant(node):
        if node[0]in ('number','string'):return True
        if node[0]=='identifier':return node[1]in constants or node[1].startswith('COLOR')
        if node[0]=='unary':return constant(node[2])
        if node[0]=='binary':return constant(node[2])and constant(node[3])
        if node[0]=='call':
            if node[1]=='REFX1'and node[2][1]==('number',100000.):return True
            if node[1]in elementwise:return all(constant(a)for a in node[2])
            if node[1]=='COUNT'and node[2][1]==('number',1.):return constant(node[2][0])
        return False
    def simplify(node):
        if node[0]=='call':
            args=[simplify(a)for a in node[2]];fn=node[1]
            if fn=='REFX1'and args[1]==('number',100000.):
                value=args[0]
                if (value[0]=='call'and value[1]in ('IF','IFELSE')and
                        value[2][0]==('identifier','ISLASTBAR')and constant(value[2][1])):
                    return value[2][1]
            if fn in ('REF','REFX1')and constant(args[0]):return args[0]
            return ('call',fn,args)
        if node[0]=='binary':return ('binary',node[1],simplify(node[2]),simplify(node[3]))
        if node[0]=='unary':return ('unary',node[1],simplify(node[2]))
        return node
    simplified=[]
    for statement in statements:
        expression=simplify(statement.expression)
        if statement.name and constant(expression):constants.add(statement.name)
        simplified.append(Statement(statement.name,expression,statement.output,statement.styles,statement.line))
    statements=simplified
    # The native parser rejects IF/IFELSE as nested function arguments
    # (ErrCode 15), despite the catalog's general expression examples.
    # Nested BACKSET also differs from a named series in the native engine:
    # FILTER(BACKSET(...),N) can mark every N+1 bars instead of filtering
    # the completed back-set series. Materialize it before its consumer.
    # Name each nested selector BEFORE its consumer. Keep its original
    # analysis/drawing phase and reuse only an already defined expression.
    result=[];cached={};serial=0
    for statement in statements:
        def hoist(node,top=False):
            nonlocal serial
            if node[0]=='call':
                value=('call',node[1],[hoist(a)for a in node[2]])
                if node[1]in ('IF','IFELSE','BACKSET')and not top:
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
    expression=render(statement.expression)
    # Native 5.4 pre-parses AA:=(0-1) but calculates AA as NULL, whereas
    # AA:=0-1 produces -1. Keep grouping in arguments/comparisons, and
    # remove this redundant wrapper only for a complete assignment RHS.
    if statement.name and expression.startswith('(0-')and statement.expression[0]in ('number','unary'):
        expression=expression[1:-1]
    return ((statement.name+(':'if statement.output else':='))if statement.name else'')+expression+(','+','.join(statement.styles)if statement.styles else'')+';'


def generate(raw,max_bytes=MAX_INSTALL_BYTES):
    statements,boundary=typed_model(raw)
    statements,omitted=specialize(statements)
    definitions={s.name:i for i,s in enumerate(statements)if s.name}
    refs=[{definitions[n[1]]for n in walk(s.expression)if n[0]=='identifier'and n[1]in definitions}for s in statements]
    memo={}
    def closure(i):
        if i not in memo:
            needed={i}
            for dependency in refs[i]:needed.update(closure(dependency))
            memo[i]=needed
        return memo[i]
    parameters={i for i,s in enumerate(statements)if s.name in SETTINGS}
    roots=[i for i,s in enumerate(statements)if not s.name or s.output]
    owners={i+1:(re.search(r'\{ OWNER:([^}]+) \}',line).group(1)if '{ OWNER:'in line else'overview')for i,line in enumerate(raw.splitlines())}
    bins=[{'title':'主浪、趋势拐点与反转形态','families':set(),'needed':set(parameters),'roots':[]},
          {'title':'当前及前一段子浪、回调和止盈价位','families':{'SW','PW','prices'},'needed':set(parameters),'roots':[]},
          {'title':'孙浪（可选）','families':{'GW'},'needed':set(parameters),'roots':[]}]
    for i in roots:
        owner=owners[statements[i].line]
        group=bins[1]if owner in ('SW','PW','prices')else bins[2]if owner=='GW'else bins[0]
        group['roots'].append(i);group['needed'].update(closure(i));group['families'].add(owner)
    files={}
    for number,group in enumerate(bins,1):
        indices=sorted(group['needed'])
        selected,aliases=short_names([statements[i]for i in indices])
        text='\n'.join(emit(s)for s in selected)+'\n'
        phase=sum(statements[i].line<=boundary for i in indices)
        profile={'analysis_end_line':phase,'ascii_only':True,'bom':False,'comments':False,'encoding':'ascii','role':'install_layer',
                 'root_ids':sorted(group['roots']),'parameter_names':sorted(SETTINGS-set(FIXED_DISPLAY)),'max_install_bytes':max_bytes,
                 'title':group['title'],'families':sorted(group['families']),'optional':number==3,
                 'fixed_display_settings':FIXED_DISPLAY,'native_variable_aliases':aliases}
        check(text,analysis_end_line=phase)
        data=text.replace('\n','\r\n').encode('ascii')
        if len(data)>max_bytes:raise ValueError(f'{group["title"]} installation byte budget exceeded: {len(data)}')
        files[f'EWT{number:02}.txt']=(data,profile|{'sha256':hashlib.sha256(data).hexdigest()})
    observed=Counter(i for group in bins for i in group['roots'])
    if observed!=Counter(roots):raise ValueError('a drawing was lost or duplicated')
    return files,{'drawing_roots':len(roots),'layers':len(files),'recommended_layers':2,'all_roots_exactly_once':True,
                  'compile_time_disabled_legacy_roots':omitted,'fixed_display_settings':FIXED_DISPLAY,
                  'max_install_bytes':max_bytes,'observed_service_boundary':{'accepted_characters':80000,'rejected_characters':80001,'observed_date':'2026-11-05','documented_compiler_limit':False},
                  'model':'typed IF; same settings and algorithms; dependency pruning only'}
