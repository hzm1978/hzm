"""Render selected same-degree Q families and their owned adjustments."""

SHAPES={0:'ADJ?',1:'A SIMPLE',2:'ABC',3:'ABC CON',4:'ABC STR',5:'TRI CON',
        6:'TRI EXP',7:'ABCDE TREND',8:'ABCDE FLAT',9:'ABCDE IRREG',10:'COMPLEX?'}


def ratio(b,on,age,y,value,color):
    b.number(on,age,y,value,color,3,11,2)
    b.text(on,age,y,' H1',color,0)


def adjustment(b,key,q,d,color='COLORMAGENTA',lane=3):
    show=f'{q["show"]} AND SHOWC';shape=q['shape']
    y=f'{q["p"]}-{d}*BPAD*{lane}'
    for code,label in SHAPES.items():
        b.text(f'{show} AND {shape["code"]}={code}',q['t'],y,label,color,2)
    known=b.put(key+'KNOWN',f'{shape["source"]}>0 AND {shape["length"]}>=3')
    live=shape.get('live','0');points=[]
    only_g=shape.get('only_g',False)
    for i in range(5):
        age_expr=f'IF({shape["source"]}=2,REF(SA{i},{shape["snap"]})+PN,REF(GA{i},{shape["snap"]})+GN)+{shape["snap"]}'
        price_expr=f'IF({shape["source"]}=2,REF(SP{i},{shape["snap"]}),REF(GP{i},{shape["snap"]}))'
        if only_g:
            age_expr=f'REF(GA{i},{shape["snap"]})+GN+{shape["snap"]}'
            price_expr=f'REF(GP{i},{shape["snap"]})'
        if live!='0':
            la=q['t']if i==0 else f'IF({shape["source"]}=2,REF(SA{i-1},{shape["snap"]})+PN,REF(GA{i-1},{shape["snap"]})+GN)+{shape["snap"]}'
            lp=q['p']if i==0 else f'IF({shape["source"]}=2,REF(SP{i-1},{shape["snap"]}),REF(GP{i-1},{shape["snap"]}))'
            if only_g:
                la=q['t']if i==0 else f'REF(GA{i-1},{shape["snap"]})+GN+{shape["snap"]}'
                lp=q['p']if i==0 else f'REF(GP{i-1},{shape["snap"]})'
            age_expr=f'IF({live},{la},{age_expr})';price_expr=f'IF({live},{lp},{price_expr})'
        points.append((b.frozen(key+f'LA{i}',age_expr),b.frozen(key+f'LP{i}',price_expr)))
    mask=b.put(key+'MASK',f'VX={q["a"]} OR VX={q["t"]} OR ('+known+' AND ('+' OR '.join(f'({shape["length"]}>{i} AND VX={a})'for i,(a,_)in enumerate(points))+'))')
    price=q['p']
    for i,(a,p)in reversed(list(enumerate(points))):price=f'IF({known} AND {shape["length"]}>{i} AND VX={a},{p},{price})'
    price=b.put(key+'PRICE',f'IF(VX={q["a"]},{q["r"]},{price})')
    b.draw(f'POLYLINE({show} AND {mask},{price},{color}),LINETHICK1;')
    for j,letter in enumerate('abcde'):
        a,p=points[4-j]
        if j<3:
            a=b.put(key+f'LETTERA{j}',f'IF({shape["length"]}=5,{a},{points[2-j][0]})')
            p=b.put(key+f'LETTERP{j}',f'IF({shape["length"]}=5,{p},{points[2-j][1]})')
        b.text(f'{show} AND {known} AND {shape["length"]}>{j}',a,f'{p}-{d}*BPAD*{0.45 if j%2==0 else -0.45}',letter,color,0)
    return points


def internal_intervals(b,trace,key,color):
    """Retain downgraded closed Qs as children inside an impulse outline.

    These vertices never create additional same-degree wave numbers or H1.
    Already rendered a..e adjustments own their internal turns themselves.
    """
    d=trace['scope']['direction'];base=trace['scope']['first'];points=[];items=[]
    for i,q in enumerate(trace['raw']):
        represented=' OR '.join(f'({s["show"]} AND {q["a"]}<={s["a"]} AND {q["a"]}>={s["t"]})'for s in trace['q'])
        on=b.put(key+f'ON{i}',f'{trace["scope"]["on"]} AND {q.get("picked","1")} AND {q["v"]} AND {q["closed"]} AND IF({represented},0,1)')
        mid=b.put(key+f'MID{i}',f'INTPART(({q["a"]}+{q["boundary"]})/2)')
        ret=b.put(key+f'RET{i}',f'ABS({q["p"]}-{q["r"]})/MAX(ABS({q["r"]}-{base}),0.0000001)*100')
        b.segment(f'{on} AND SHOWQ',q['a'],q['r'],q['boundary'],q['r'],'COLORWHITE','DOT')
        points.extend([(on,q['a'],q['r'],True),(on,q['t'],q['p'],False)])
        length=b.put(key+f'LEN{i}',f'{q["a"]}-{q["boundary"]}')
        items.append(dict(on=on,mid=mid,r=q['r'],p=q['p'],t=q['t'],n=length,ret=ret))
        base=b.put(key+f'BASE{i}',f'IF({q["v"]} AND {q["closed"]},{q["p"]},{base})')
    # Aggregate the per-candle annotations; freezing these series would
    # move every internal interval's label to the current candle.
    for tag,age,price,value,text in (('LEN','mid','r','n','q LEN '),('RET','t','p','ret','q RET% ')):
        gates=[f'({v["on"]} AND VX={v[age]})'for v in items]
        gate=b.put(key+tag+'ON','SHOWQ AND ('+' OR '.join(gates)+')')
        ys=b.put(key+tag+'Y','+'.join(f'IF({g},{v[price]}+{d}*BPAD*'+('2'if tag=='LEN'else'(0-2)')+',0)'for g,v in zip(gates,items)))
        values=b.put(key+tag+'N','+'.join(f'IF({g},{v[value]},0)'for g,v in zip(gates,items)))
        b.draw(f"KTEXT({gate},0,{ys},2,{color},'{text}'),FONTSIZE{b.font};")
        b.draw(f'DRAWNUMBER({gate},{ys},{values},{0 if tag=="LEN"else 1},{color}),ALIGN0,FONTSIZE{b.font};')
    trace['internal']=items
    return points


def patterns(b,key,high,low,gate,lane,label=None):
    for side,mask,code,price in (('B',low,'BPCODE',f'L-BPAD*{lane}'),('S',high,'SPCODE',f'H+BPAD*{lane}')):
        seen=b.put(key+side+'SEEN',f'COUNT({mask},0)>0')
        near=b.put(key+side+'NEAR',f'{seen} AND BARSLAST({mask})<=3')
        on=b.put(key+side+'OBS',f'SHOWK AND {gate} AND FILTER({near} AND {code}>0,3)')
        for number in range(1,14):
            b.draw(f"KTEXT({on} AND {code}={number},0,{price},2,COLORWHITE,'{label or key} RV{number:02}'),FONTSIZE9;")


def draw(b,trace,label,color,lane=1,with_patterns=True,side=1,internal=False):
    key='IR'+label
    s=trace['scope'];direction=s['direction'];d=direction if side==1 else f'(0-{direction})';on=s['on'];count=trace['count'];h1=trace['h1']
    points=[];base=s['first']
    for i,q in enumerate(trace['q']):
        show=q['show'];odd=2*i+1;even=odd+1
        b.segment(f'{show} AND SHOWQ',q['a'],q['r'],q['boundary'],q['r'],'COLORWHITE')
        middle=b.put(key+f'QMID{i}',f'INTPART(({q["a"]}+{q["boundary"]})/2)')
        b.text(f'{show} AND SHOWQ',middle,f'{q["r"]}+{d}*BPAD*({lane}+2.5)',f'{label} Q{i+1} LEN ',color,2)
        b.number(f'{show} AND SHOWQ',middle,f'{q["r"]}+{d}*BPAD*({lane}+2.5)',q['n'],color,0)
        b.text(show,q['a'],f'{q["r"]}+{d}*BPAD*{lane}',f'{label}{odd}',color,font=13)
        actual=b.put(key+f'H{i}',f'{direction}*({q["r"]}-{base})/MAX({h1},0.0000001)')
        ratio(b,f'{show} AND {trace["proof"]}',q['a'],f'{q["r"]}+{d}*BPAD*({lane}+1.3)',actual,color)
        b.text(show,q['t'],f'{q["p"]}-{d}*BPAD*{lane}',f'{label}{even}',color,font=13)
        ret=b.put(key+f'RET{i}',f'ABS({q["p"]}-{q["r"]})/MAX(ABS({q["r"]}-{base}),0.0000001)*100')
        b.text(show,q['t'],f'{q["p"]}-{d}*BPAD*({lane}+1.2)','RET% ',color,2)
        b.number(show,q['t'],f'{q["p"]}-{d}*BPAD*({lane}+1.2)',ret,color,1)
        adjustment(b,key+f'AD{i}',q,d,lane=lane+2.4)
        points += [(show,q['a'],q['r'],True),(show,q['t'],q['p'],False)]
        base=q['p']
    extra=internal_intervals(b,trace,key+'INNER',color)if internal else[]
    all_points=points+extra
    mask=b.put(key+'MASK',f'VX={s["start"]} OR VX={s["end"]} OR '+' OR '.join(f'({valid} AND VX={age})'for valid,age,_,_ in all_points))
    price='0'
    for valid,age,p,_ in reversed(all_points):price=f'IF({valid} AND VX={age},{p},{price})'
    price=b.put(key+'PRICE',f'IF(VX={s["start"]},{s["first"]},IF(VX={s["end"]},{s["last"]},{price}))')
    b.draw(f'POLYLINE({on} AND {mask},{price},{color}),LINETHICK2;')
    for n in range(4):
        pending=f'{trace["kind"]}=0 OR {trace["ambiguous"]} OR {trace["pending"]} OR {trace["limited"]}'
        b.text(f'{on} AND {count}={n} AND IF({pending},0,1)',s['end'],f'{s["last"]}+{d}*BPAD*{lane}',f'{label}{2*n+1}',color,font=13)
        b.text(f'{on} AND {count}={n} AND ({pending})',s['end'],f'{s["last"]}+{d}*BPAD*{lane}',f'{label}{2*n+1}?',color,font=13)
    actual=b.put(key+'ENDMULT',f'{direction}*({s["last"]}-{trace["base"]})/MAX({h1},0.0000001)')
    ratio(b,f'{on} AND {trace["proof"]}',s['end'],f'{s["last"]}+{d}*BPAD*({lane}+1.3)',actual,color)
    b.text(f'{on} AND IF({trace["proof"]},0,1)',s['end'],f'{s["last"]}+{d}*BPAD*({lane}+1.3)','H1 ?',color,2)
    for value,text in ((3,'3 Q WAIT'),(5,'5 EQ'),(71,'7 EXP'),(72,'7 CON'),(73,'7 DIA'),(74,'7 X')):
        b.text(f'{on} AND {trace["kind"]}={value} AND {trace["limited"]}=0 AND {trace["ambiguous"]}=0',s['end'],f'{s["last"]}+{d}*BPAD*({lane}+2.6)',text,color,2)
    for value,text in ((1,'5 EXP:Q3?'),(-1,'5 CON:Q3?')):
        b.text(f'{on} AND {count}=2 AND {trace["rel1"]}={value}',s['end'],f'{s["last"]}+{d}*BPAD*({lane}+2.6)',text,color,2)
    b.text(f'{on} AND ({trace["limited"]} OR {trace["ambiguous"]})',s['end'],f'{s["last"]}+{d}*BPAD*({lane}+3.8)','Q EXTENSION?',color,2)
    last_root=b.put(key+'LASTROOT',f'IF({count}=3,{trace["q"][2]["r"]},IF({count}=2,{trace["q"][1]["r"]},{trace["q"][0]["r"]}))')
    b.text(f'{on} AND {count}>=2 AND {trace["pending"]} AND {direction}*({s["last"]}-{last_root})<(0-BEPS)',s['end'],f'{s["last"]}+{d}*BPAD*({lane}+5)','TRUNC?',color,2)
    # Main and P use their own actual turning points, not every minor fractal.
    trend_points=' OR '.join(f'({valid} AND VX={age})'for valid,age,_,trend in points if trend)
    counter_points=' OR '.join(f'({valid} AND VX={age})'for valid,age,_,trend in points if not trend)
    hi=b.put(key+'HIGH',f'IF({direction}=1,VX={s["end"]} OR {trend_points},VX={s["start"]} OR {counter_points})')
    lo=b.put(key+'LOW',f'IF({direction}=1,VX={s["start"]} OR {counter_points},VX={s["end"]} OR {trend_points})')
    if with_patterns:patterns(b,key,hi,lo,on,lane+2.5,label=label)
    return {'mask':mask,'high':hi,'low':lo,'end_multiple':actual}
