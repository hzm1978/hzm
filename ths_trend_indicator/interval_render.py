"""Render selected same-degree Q families and their owned adjustments."""

SHAPES={0:'ADJ?',1:'A SIMPLE',2:'ABC',3:'ABC CON',4:'ABC STR',5:'TRI CON',
        6:'TRI EXP',7:'ABCDE TREND',8:'ABCDE FLAT',9:'ABCDE IRREG',10:'COMPLEX?'}


def ratio(b,on,age,y,value,color):
    b.number(on,age,y,value,color,3,9,2)
    b.text(on,age,y,' H1',color,0)


def adjustment(b,key,q,d,color='COLORMAGENTA',lane=3):
    show=f'{q["show"]} AND SHOWC';shape=q['shape']
    y=f'{q["p"]}-{d}*BPAD*{lane}'
    for code,label in SHAPES.items():
        b.text(f'{show} AND {shape["code"]}={code}',q['t'],y,label,color,2)
    gh=b.put(key+'GH','FILTER(BACKSET(GHC,GN+1),GN)')
    gl=b.put(key+'GL','FILTER(BACKSET(GLC,GN+1),GN)')
    sh=b.put(key+'SH','FILTER(BACKSET(SHC,PN+1),PN)')
    sl=b.put(key+'SL','FILTER(BACKSET(SLC,PN+1),PN)')
    known=b.put(key+'KNOWN',f'{shape["source"]}>0 AND {shape["length"]}>=3')
    turns=b.put(key+'TURNS',f'IF({shape["source"]}=2,{sh} OR {sl},{gh} OR {gl})')
    mask=b.put(key+'MASK',f'VX={q["a"]} OR VX={q["t"]} OR ({turns} AND VX<{q["a"]} AND VX>{q["t"]})')
    price=b.put(key+'PRICE',f'IF(VX={q["a"]},{q["r"]},IF(VX={q["t"]},{q["p"]},IF({shape["source"]}=2,IF({sh},H,L),IF({gh},H,L))))')
    b.draw(f'POLYLINE({show} AND {mask},{price},{color}),LINETHICK1;')
    for i in range(5):
        age=b.frozen(key+f'LA{i}',f'IF({shape["source"]}=2,REF(SA{i},{shape["snap"]})+PN,REF(GA{i},{shape["snap"]})+GN)+{shape["snap"]}')
        point=b.frozen(key+f'LP{i}',f'IF({shape["source"]}=2,REF(SP{i},{shape["snap"]}),REF(GP{i},{shape["snap"]}))')
        for size,letters in ((3,('c','b','a')),(5,('e','d','c','b','a'))):
            if i<len(letters):
                b.text(f'{show} AND {known} AND {shape["length"]}={size}',age,
                       f'{point}-{d}*BPAD*{0.25 if i%2==0 else -0.25}',letters[i],color,0)


def patterns(b,key,high,low,gate,lane,label=None):
    for side,mask,code,price in (('B',low,'BPCODE',f'L-BPAD*{lane}'),('S',high,'SPCODE',f'H+BPAD*{lane}')):
        seen=b.put(key+side+'SEEN',f'COUNT({mask},0)>0')
        near=b.put(key+side+'NEAR',f'{seen} AND BARSLAST({mask})<=3')
        on=b.put(key+side+'OBS',f'SHOWK AND {gate} AND FILTER({near} AND {code}>0,3)')
        for number in range(1,14):
            b.draw(f"KTEXT({on} AND {code}={number},0,{price},2,COLORWHITE,'{label or key} RV{number:02}'),FONTSIZE9;")


def draw(b,trace,label,color,lane=1,with_patterns=True):
    key='IR'+label
    s=trace['scope'];d=s['direction'];on=s['on'];count=trace['count'];h1=trace['h1']
    points=[];base=s['first']
    for i,q in enumerate(trace['q']):
        show=q['show'];odd=2*i+1;even=odd+1
        b.segment(f'{show} AND SHOWQ',q['a'],q['r'],q['boundary'],q['r'],'COLORWHITE')
        middle=b.put(key+f'QMID{i}',f'INTPART(({q["a"]}+{q["boundary"]})/2)')
        b.text(f'{show} AND SHOWQ',middle,f'{q["r"]}+{d}*BPAD*0.5',f'{label} Q{i+1} LEN ',color,2)
        b.number(f'{show} AND SHOWQ',middle,f'{q["r"]}+{d}*BPAD*0.5',q['n'],color,0)
        b.text(show,q['a'],f'{q["r"]}+{d}*BPAD*{lane}',f'{label}{odd}',color,font=11)
        actual=b.put(key+f'H{i}',f'{d}*({q["r"]}-{base})/MAX({h1},0.0000001)')
        ratio(b,f'{show} AND {trace["proof"]}',q['a'],f'{q["r"]}+{d}*BPAD*({lane}+1.3)',actual,color)
        b.text(show,q['t'],f'{q["p"]}-{d}*BPAD*{lane}',f'{label}{even}',color,font=11)
        ret=b.put(key+f'RET{i}',f'ABS({q["p"]}-{q["r"]})/MAX(ABS({q["r"]}-{base}),0.0000001)*100')
        b.text(show,q['t'],f'{q["p"]}-{d}*BPAD*({lane}+1.2)','RET% ',color,2)
        b.number(show,q['t'],f'{q["p"]}-{d}*BPAD*({lane}+1.2)',ret,color,1)
        adjustment(b,key+f'AD{i}',q,d,lane=lane+2.4)
        points += [(show,q['a'],q['r'],True),(show,q['t'],q['p'],False)]
        base=q['p']
    mask=b.put(key+'MASK',f'VX={s["start"]} OR VX={s["end"]} OR '+' OR '.join(f'({valid} AND VX={age})'for valid,age,_,_ in points))
    price=b.put(key+'PRICE',f'IF(VX={s["start"]},{s["first"]},IF(VX={s["end"]},{s["last"]},'+'+'.join(f'IF({valid} AND VX={age},{p},0)'for valid,age,p,_ in points)+'))')
    b.draw(f'POLYLINE({on} AND {mask},{price},{color}),LINETHICK2;')
    for n in range(4):
        pending=f'{trace["kind"]}=0 OR {trace["ambiguous"]}'
        b.text(f'{on} AND {count}={n} AND IF({pending},0,1)',s['end'],f'{s["last"]}+{d}*BPAD*{lane}',f'{label}{2*n+1}',color,font=11)
        b.text(f'{on} AND {count}={n} AND ({pending})',s['end'],f'{s["last"]}+{d}*BPAD*{lane}',f'{label}{2*n+1}?',color,font=11)
    actual=b.put(key+'ENDMULT',f'{d}*({s["last"]}-{trace["base"]})/MAX({h1},0.0000001)')
    ratio(b,f'{on} AND {trace["proof"]}',s['end'],f'{s["last"]}+{d}*BPAD*({lane}+1.3)',actual,color)
    b.text(f'{on} AND IF({trace["proof"]},0,1)',s['end'],f'{s["last"]}+{d}*BPAD*({lane}+1.3)','H1 ?',color,2)
    for value,text in ((5,'5 EQ'),(71,'7 EXP'),(72,'7 CON'),(73,'7 DIA'),(74,'7 X')):
        b.text(f'{on} AND {trace["kind"]}={value}',s['end'],f'{s["last"]}+{d}*BPAD*({lane}+2.6)',text,color,2)
    for value,text in ((1,'5 EXP:Q3?'),(-1,'5 CON:Q3?')):
        b.text(f'{on} AND {count}=2 AND {trace["rel1"]}={value}',s['end'],f'{s["last"]}+{d}*BPAD*({lane}+2.6)',text,color,2)
    b.text(f'{on} AND ({trace["limited"]} OR {trace["ambiguous"]})',s['end'],f'{s["last"]}+{d}*BPAD*({lane}+3.8)','Q EXTENSION?',color,2)
    # Main and P use their own actual turning points, not every minor fractal.
    trend_points=' OR '.join(f'({valid} AND VX={age})'for valid,age,_,trend in points if trend)
    counter_points=' OR '.join(f'({valid} AND VX={age})'for valid,age,_,trend in points if not trend)
    hi=b.put(key+'HIGH',f'IF({d}=1,VX={s["end"]} OR {trend_points},VX={s["start"]} OR {counter_points})')
    lo=b.put(key+'LOW',f'IF({d}=1,VX={s["start"]} OR {counter_points},VX={s["end"]} OR {trend_points})')
    if with_patterns:patterns(b,key,hi,lo,on,lane+2.5,label=label)
    return {'mask':mask,'high':hi,'low':lo,'end_multiple':actual}
