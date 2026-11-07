"""Generate bounded independent main-chart overlays from one formula model.

The official content check accepts 80000 characters and rejects 80001.
Budget Windows CRLF, including the characters added when LF text is
copied through an editor. This is an observed service rule, not a
documented compiler limit. Each drawing belongs to exactly one file; dependencies are copied,
never shared via another indicator or an account/network lookup.
"""
from collections import Counter
import hashlib
import re

from check_formula import (Parser,Statement,NUMBER,CONDITION,STRING,COLOR,
                           SIGNATURES,PREDEFINED_TYPES,walk,check,RESERVED,CLIENT_FUNCTIONS)
from compatibility import variable_names,render,SETTINGS
from export_native import OUTPUTS,ascii_label

MAX_INSTALL_CHARACTERS=79500
MAX_INSTALL_BYTES=MAX_INSTALL_CHARACTERS  # Historical import; count characters.
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
        # The client's saving rule counts Unicode characters. One-letter
        # private identifiers avoid splitting a coherent level across files.
        # Settings, functions, output names and visible labels stay ASCII.
        seen=set()
        def native_key(char):
            # The legacy DLL folds source bytes using the Windows ANSI
            # upper-case table. UTF-8 continuation 9A/9C/9E can therefore
            # collide with 8A/8C/8E. Keep only one spelling per native key.
            parts=[]
            for byte in char.encode('utf8'):
                try:parts.append(bytes([byte]).decode('cp1252').upper().encode('cp1252'))
                except UnicodeError:parts.append(bytes([byte]))
            return b''.join(parts)
        for codepoint in range(0x4E00,0x9FA6):
            char=chr(codepoint)
            try:encoded=char.encode('gb2312')
            except UnicodeEncodeError:continue
            if len(encoded)==2 and min(encoded)>=0xA1 and char not in '买卖开平':
                key=native_key(char)
                if key not in seen:
                    seen.add(key);yield char
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
        if fn=='SUM'and is_condition(args[0]):
            return ('call','COUNT',[cond(args[0]),num(args[1])])
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
            total_bound=('binary','-',ident('DATACOUNT'),('number',1.))
            # BARPOS <= DATACOUNT on every bar. The outer history clamp
            # already enforces both upper bounds, even inside MAX(0,...).
            # Removing the redundant total-history clamp preserves invalid
            # and negative windows, while avoiding hundreds of duplicates.
            def strip_clamp(value):
                if value[0:2]==('call','MIN'):
                    remaining=[v for v in value[2]if v not in (bound,total_bound)]
                    if len(remaining)==1:return strip_clamp(remaining[0])
                if value[0:2]==('call','MAX')and ('number',0.)in value[2]:
                    remaining=[v for v in value[2]if v!=('number',0.)]
                    if len(remaining)==1:return strip_clamp(remaining[0])
                return value
            window=strip_clamp(window)
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
    # Zero-window prefix functions must also have a populated history:
    # guarded SUM(COUNT(flag,1),0) calculates 0 instead of 8 in an 8-row
    # native control. A named prefix or COUNT(flag,0) calculates 8.
    # Materialize prefixes outside the last-bar guard; positive-window
    # snapshot scans stay guarded and retain the performance improvement.
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
                prefix=(node[1]in ('COUNT','SUM','HHV','LLV')and value[2][1]==('number',0.))
                if (node[1]in ('IF','IFELSE','BACKSET')or prefix)and not top:
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
    def compact(node,parent=0,right=False):
        if node[0]=='binary':
            precedence={'OR':1,'AND':2,'=':3,'<>':3,'<':3,'>':3,'<=':3,'>=':3,
                        '+':4,'-':4,'*':5,'/':5}[node[1]]
            op={'AND':'&&','OR':'||'}.get(node[1],node[1])
            value=compact(node[2],precedence)+op+compact(node[3],precedence,True)
            return '('+value+')'if precedence<parent or (right and precedence==parent)else value
        if node[0]=='call':return node[1]+'('+','.join(compact(a)for a in node[2])+')'
        if node[0]=='unary':
            return render(node)
        return render(node)
    expression=compact(statement.expression)
    # Native 5.4 pre-parses AA:=(0-1) but calculates AA as NULL, whereas
    # AA:=0-1 produces -1. Keep grouping in arguments/comparisons, and
    # remove this redundant wrapper only for a complete assignment RHS.
    if statement.name and expression.startswith('(0-')and statement.expression[0]in ('number','unary'):
        expression=expression[1:-1]
    return ((statement.name+(':'if statement.output else':='))if statement.name else'')+expression+(','+','.join(statement.styles)if statement.styles else'')+';'


def deduplicate(statements,boundary):
    """Share identical immutable calculation columns, preserving every root.

    Never inline a history function or move it through a last-bar guard.
    Only replace references to an earlier, byte-identical expression in the
    same analysis/drawing phase. Editable settings and outputs stay named.
    """
    aliases={};seen={};result=[]
    def rewrite(node):
        if node[0]=='identifier':return aliases.get(node[1],node)
        if node[0]=='call':return ('call',node[1],[rewrite(a)for a in node[2]])
        if node[0]=='binary':return ('binary',node[1],rewrite(node[2]),rewrite(node[3]))
        if node[0]=='unary':return ('unary',node[1],rewrite(node[2]))
        return node
    for s in statements:
        expression=rewrite(s.expression)
        if s.name and not s.output and s.name not in SETTINGS:
            key=(s.line<=boundary,render(expression))
            target=(expression if expression[0]=='identifier'and expression[1]not in PREDEFINED_TYPES else seen.get(key))
            if target is not None:
                aliases[s.name]=target
                continue
            seen[key]=('identifier',s.name)
        result.append(Statement(s.name,expression,s.output,s.styles,s.line))
    return result


def compact_builtins(statements,boundary):
    """Name repeated long built-ins once; no evaluation or phase changes."""
    names={'DATACOUNT':'NATIVE_TOTAL','ISLASTBAR':'NATIVE_LAST'}
    used={n[1]for s in statements for n in walk(s.expression)if n[0]=='identifier'}
    names={k:v for k,v in names.items()if k in used}
    def rewrite(node):
        if node[0]=='identifier':return ('identifier',names.get(node[1],node[1]))
        if node[0]=='call':return ('call',node[1],[rewrite(a)for a in node[2]])
        if node[0]=='binary':return ('binary',node[1],rewrite(node[2]),rewrite(node[3]))
        if node[0]=='unary':return ('unary',node[1],rewrite(node[2]))
        return node
    leading=[Statement(name,('identifier',builtin),False,(),boundary+1)for builtin,name in names.items()]
    result=[];inserted=False
    for s in statements:
        if s.line>boundary and not inserted:
            result.extend(leading);inserted=True
        result.append(Statement(s.name,rewrite(s.expression)if s.line>boundary else s.expression,s.output,s.styles,s.line))
    return result,names


def share_elementwise(statements,boundary):
    """Factor repeated pure elementwise expressions at their first use.

    Stateful/windowed functions and selectors remain at their original
    guard and phase. COUNT(cond,1) is a numeric cast of this bar only.
    """
    pure={'ABS','MAX','MIN','SIGN','INTPART','ISNULL'}
    def safe(node):
        if node[0]in ('number','identifier'):return True
        if node[0]=='binary':return safe(node[2])and safe(node[3])
        if node[0]=='unary':return safe(node[2])
        if node[0]=='call':
            allowed=node[1]in pure or (node[1]=='COUNT'and node[2][1]==('number',1.))
            return allowed and all(safe(a)for a in node[2])
        return False
    counts=Counter((s.line<=boundary,render(n))for s in statements for n in walk(s.expression)
                   if n[0]in ('binary','unary','call')and safe(n))
    shared={};out=[];serial=0
    def short_size(node):
        # Factoring is priced AFTER private names become single letters.
        # Long model identifiers otherwise make A<B look expensive and
        # generate a binding larger than the two original comparisons.
        def abbreviated(value):
            if value[0]=='identifier':
                name=value[1]
                return ('identifier',name if name in SETTINGS or name.startswith('COLOR')else'Z')
            if value[0]=='call':return ('call',value[1],[abbreviated(a)for a in value[2]])
            if value[0]=='binary':return ('binary',value[1],abbreviated(value[2]),abbreviated(value[3]))
            if value[0]=='unary':return ('unary',value[1],abbreviated(value[2]))
            return value
        return len(render(abbreviated(node)))
    def visit(node,phase,line,root=False):
        nonlocal serial
        if node[0]in ('number','identifier','string'):return node
        key=(phase,render(node))
        if key in shared:return ('identifier',shared[key])
        if node[0]=='binary':value=('binary',node[1],visit(node[2],phase,line),visit(node[3],phase,line))
        elif node[0]=='unary':value=('unary',node[1],visit(node[2],phase,line))
        elif node[0]=='call':value=('call',node[1],[visit(a,phase,line)for a in node[2]])
        else:return node
        size=short_size(value);n=counts[key]
        if not root and safe(node)and n>=2 and (n-1)*size-n-5>2:
            serial+=1;name=f'PURE{serial}'
            out.append(Statement(name,value,False,(),line));shared[key]=name
            return ('identifier',name)
        return value
    for s in statements:
        expression=visit(s.expression,s.line<=boundary,s.line,root=True)
        out.append(Statement(s.name,expression,s.output,s.styles,s.line))
        if s.name and s.name not in SETTINGS and not s.output and s.expression[0]not in ('number','identifier')and safe(s.expression):
            shared.setdefault((s.line<=boundary,render(s.expression)),s.name)
    return out


def windows_text(source):
    """Count/save the conservative Windows newline representation once."""
    return source.replace('\r\n','\n').replace('\r','\n').replace('\n','\r\n')


def inline_single_use_pure(statements,boundary):
    """Inline one-use elementwise bindings; keep all history/selector calls.

    Nonconstant expressions are not moved into history functions. Frozen
    arithmetic can be substituted without copying its broadcast or guard.
    IF, COUNT, scans, lookups and BACKSET remain named at their old phase.
    """
    pure={'ABS','MAX','MIN','MAX1','MIN1','SIGN','INTPART','ISNULL'}
    elementwise=pure|{'IF','IFELSE'}
    constants=set(SETTINGS)|{'DATACOUNT'}
    def constant(n):
        if n[0]in ('number','string'):return True
        if n[0]=='identifier':return n[1]in constants or n[1].startswith('COLOR')
        if n[0]=='binary':return constant(n[2])and constant(n[3])
        if n[0]=='unary':return constant(n[2])
        if n[0]=='call':
            if n[1]=='REFX1'and n[2][1]==('number',100000.):return True
            if n[1]in elementwise:return all(constant(a)for a in n[2])
            if n[1]=='COUNT'and n[2][1]==('number',1.):return constant(n[2][0])
        return False
    def safe(n):
        if n[0]in ('number','identifier'):return True
        if n[0]=='binary':return safe(n[2])and safe(n[3])
        if n[0]=='unary':return safe(n[2])
        return n[0]=='call'and n[1]in pure and all(safe(a)for a in n[2])
    counts=Counter();uses={}
    for i,s in enumerate(statements):
        if s.name and constant(s.expression):constants.add(s.name)
        for n in walk(s.expression):
            if n[0]=='identifier':counts[n[1]]+=1;uses.setdefault(n[1],set()).add(i)
    result=list(statements)
    for i in range(len(result)-1,-1,-1):
        s=result[i]
        if not s or not s.name or s.output or s.name in SETTINGS or counts[s.name]!=1 or not safe(s.expression):continue
        j=next(iter(uses[s.name]));consumer=result[j]
        if (s.line<=boundary)!=(consumer.line<=boundary):continue
        movable=constant(s.expression);blocked=False
        def replace(n,history=False):
            nonlocal blocked
            if n[0]=='identifier'and n[1]==s.name:
                if history and not movable:blocked=True
                return s.expression
            if n[0]=='call':return ('call',n[1],[replace(a,history or n[1]not in elementwise)for a in n[2]])
            if n[0]=='binary':return ('binary',n[1],replace(n[2],history),replace(n[3],history))
            if n[0]=='unary':return ('unary',n[1],replace(n[2],history))
            return n
        expression=replace(consumer.expression)
        if blocked:continue
        result[j]=Statement(consumer.name,expression,consumer.output,consumer.styles,consumer.line)
        result[i]=None
        for n in walk(s.expression):
            if n[0]=='identifier':uses[n[1]].discard(i);uses[n[1]].add(j)
    return [s for s in result if s is not None]


def packed_rows(statements,width=400,limit=12):
    """Pack complete statements only; editable parameters keep their rows.

    Analysis and drawing are packed separately by the caller. A physical
    row never straddles the causal boundary, and no token or order changes.
    """
    rows=[];row='';count=0
    for s in statements:
        value=emit(s)
        own_row=s.name in SETTINGS
        if row and (own_row or count>=limit or len(row)+len(value)>width):
            rows.append(row);row='';count=0
        if own_row:
            rows.append(value);continue
        row+=value;count+=1
    if row:rows.append(row)
    return rows


def generate(raw,max_bytes=MAX_INSTALL_BYTES):
    statements,boundary=typed_model(raw)
    statements,omitted=specialize(statements)
    statements=deduplicate(statements,boundary)
    statements=share_elementwise(statements,boundary)
    statements=deduplicate(statements,boundary)
    statements=inline_single_use_pure(statements,boundary)
    statements,builtin_aliases=compact_builtins(statements,boundary)
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
    bins=[{'title':'主级区间数浪、H1倍数及调整反转','families':{'new_M'},'needed':set(parameters),'roots':[]},
          {'title':'完整P区间数浪、H1倍数与唯一价格尺','families':{'new_P','prices'},'needed':set(parameters),'roots':[]},
          {'title':'仅最后一段S及其调整结构','families':{'new_S'},'needed':set(parameters),'roots':[]}]
    for i in roots:
        owner=owners[statements[i].line]
        if owner not in ('new_M','new_P','new_S','prices'):raise ValueError('obsolete/unowned drawing '+owner)
        group=bins[1]if owner in ('new_P','prices')else bins[2]if owner=='new_S'else bins[0]
        group['roots'].append(i);group['needed'].update(closure(i));group['families'].add(owner)
    files={}
    for number,group in enumerate(bins,1):
        indices=sorted(group['needed'])
        selected,aliases=short_names([statements[i]for i in indices])
        aliases={next((builtin for builtin,field in builtin_aliases.items()if field==key),key):value for key,value in aliases.items()}
        analysis_statements=[s for s in selected if s.line<=boundary]
        analysis=packed_rows(analysis_statements)
        drawing=packed_rows([s for s in selected if s.line>boundary])
        text='\r\n'.join(analysis+drawing)+'\r\n'
        phase=len(analysis)
        def ast_digest(items):
            import json
            # Parse the emitted, unpacked text as the source of truth;
            # rendering can canonically simplify a negative literal.
            parsed=Parser('\n'.join(emit(s)for s in items)).parse()
            values=[(s.name,s.expression,s.output,s.styles)for s in parsed]
            return hashlib.sha256(json.dumps(values,ensure_ascii=True,separators=(',',':')).encode()).hexdigest()
        profile={'analysis_end_line':phase,'ascii_only':False,'visible_labels_ascii_only':True,'bom':False,'comments':False,'encoding':'utf-8','role':'install_layer',
                 'root_ids':sorted(group['roots']),'parameter_names':sorted(SETTINGS-set(FIXED_DISPLAY)),'max_install_characters':max_bytes,
                 'title':group['title'],'families':sorted(group['families']),'optional':False,
                 'fixed_display_settings':FIXED_DISPLAY,'native_variable_aliases':aliases,
                 'unpacked_ast_sha256':ast_digest(selected),'unpacked_analysis_ast_sha256':ast_digest(analysis_statements),
                 'statements':len(selected)}
        check(text,analysis_end_line=phase)
        data=text.encode('utf-8')
        profile['line_ending']='CRLF'
        profile['packed_statements']=True
        windows_characters=len(windows_text(text))
        if windows_characters>max_bytes:raise ValueError(f'{group["title"]} Windows character budget exceeded: {windows_characters}')
        files[f'EWT{number:02}.txt']=(data,profile|{'sha256':hashlib.sha256(data).hexdigest(),'characters':len(text),'windows_characters':windows_characters,'bytes':len(data)})
    observed=Counter(i for group in bins for i in group['roots'])
    if observed!=Counter(roots):raise ValueError('a drawing was lost or duplicated')
    return files,{'drawing_roots':len(roots),'layers':len(files),'recommended_layers':3,'all_roots_exactly_once':True,
                  'compile_time_disabled_legacy_roots':omitted,'fixed_display_settings':FIXED_DISPLAY,
                  'max_install_characters':max_bytes,'budget_line_ending':'CRLF','observed_service_boundary':{'accepted_characters':80000,'rejected_characters':80001,'observed_date':'2026-11-05','documented_compiler_limit':False},
                  'model':'typed IF; same settings and algorithms; dependency pruning only'}
