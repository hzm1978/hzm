"""One ruler from the complete owned P, independent of internal numbering."""

RETRACEMENTS=(0,.191,.382,.5,.618,.786,1)
TARGETS=(1.208,1.618,2,2.382,2.618,3,3.236,4.236)


def correction_ruler(b,key,scope,correction_on,trace):
    """Measure a completed group, or the preceding leg inside an open group."""
    whole=b.frozen(key+'WHOLE',f'({trace["kind"]}=5 OR {trace["kind"]}>=71) AND {trace["limited"]}=0 AND {trace["ambiguous"]}=0 AND {trace["pending"]}=0')
    scope=scope|{
        'first':b.frozen(key+'FIRST',f'IF({whole},{scope["first"]},{trace["base"]})'),
        'start':b.frozen(key+'START',f'IF({whole},{scope["start"]},{trace["base_age"]})')}
    on=b.frozen(key+'ON',f'SHOWF AND {scope["on"]} AND {correction_on} AND {scope["start"]}>{scope["end"]} AND {scope["direction"]}*({scope["last"]}-{scope["first"]})>BEPS')
    span=b.put(key+'SPAN',f'MIN({scope["start"]},BOOKBARS)')
    levels=[]
    for i,value in enumerate(RETRACEMENTS):
        price=b.put(key+str(i),f'{scope["last"]}+({scope["first"]}-{scope["last"]})*{value}')
        levels.append(price)
        b.segment(on,span,price,'0',price,'COLORGRAY','DOT')
        b.text(on,f'INTPART({span}*0.30)',price+'-BPAD*0.5',f'S {value*100:g}%','COLORWHITE',0)
        b.number(on,f'INTPART({span}*0.75)',price+'-BPAD*0.5',price,'COLORWHITE',2)
    for age,price,text in ((scope['start'],scope['first'],'S 100%'),(scope['end'],scope['last'],'S 0%')):
        b.segment(on,age,price+'-BPAD*0.25',age,price+'+BPAD*0.25','COLORWHITE','LINETHICK2')
        b.text(on,age,price+'+BPAD*1.8',text,'COLORWHITE',2)
    return dict(on=on,levels=levels,scope=scope,whole_group=whole)


def draw(b,hierarchy,trace):
    p=lambda k,e:b.put('IP'+k,e)
    f=lambda k,e:b.frozen('IP'+k,e)
    s=hierarchy['P'];d=s['direction'];phase=hierarchy['phase']
    ruler=s|{'last':s.get('reference_tip',s['last']),'end':s.get('reference_age',s['end'])}
    h=p('H1',f'IF(MH1>0,MH1,{trace["h1"]})')
    base=p('BASE',f'IF(MH1>0,MBASE,{trace["base"]})')
    origin=p('ORIGIN',f'IF(MH1>0,MORG,{s["first"]})')
    age=p('AGE',f'IF(MH1>0,MAGE,{trace["base_age"]})')
    proof=p('PROOF',f'IF(MH1>0,MAGE>=1 AND MAGE<DATACOUNT,{trace["proof"]} AND {trace["limited"]}=0 AND {trace["ambiguous"]}=0)')
    ton=f('TON',f'SHOWT AND {phase}=1 AND {proof} AND {d}<>0 AND {h}>BEPS AND {age}>=1 AND {age}<DATACOUNT')
    extreme=f('EXTREME',f'IF({d}=1,HHV(H,MAX(1,{age})),LLV(L,MAX(1,{age})))')
    prices=[];valid=[];ranked=[]
    strong=p('STRONG',f'STRONG OR {d}*(C-({base}+{d}*{h}*2))>BEPS')
    for i,ratio in enumerate(TARGETS,1):
        price=p(f'T{i}',f'{origin if i>=7 else base}+{d}*{h}*{ratio}')
        ok=p(f'OK{i}',f'{ton} AND '+(strong+' AND 'if i in (4,5,6)else'')+f'{d}*({price}-{extreme})>BEPS')
        prices.append(price);valid.append(ok)
    sentinel=p('SENTINEL','MAX1('+','.join(f'ABS({v}-{extreme})'for v in prices)+')+1')
    for i,(price,ok)in enumerate(zip(prices,valid)):
        ranked.append(p(f'R{i}',f'IF({ok},{d}*({price}-{extreme}),{sentinel})'))
    choices=[p(f'I{i}',f'SORTPOS(0,{i},{",".join(ranked)})')for i in range(1,4)]
    span=p('SPAN',f'MIN(DATACOUNT-1,MAX(1,SPAN))')
    for i,(price,ok,ratio)in enumerate(zip(prices,valid,TARGETS),1):
        gate=p(f'SHOW{i}',f'{ok} AND ('+' OR '.join(f'{v}={i}'for v in choices)+')')
        b.segment(gate,span,price,'0',price,'COLORFF9900')
        b.text(gate,f'INTPART({span}*'+('0.12)'if i<7 else'0.52)'),price+'+BPAD*0.65',f'{"LEG"if i<7 else"TOTAL"} {ratio:g}H1?','COLORFF9900',0)
        b.number(gate,f'INTPART({span}*'+('0.85)'if i<7 else'0.42)'),price+'+BPAD*0.65',price,'COLORFF9900',2)
    fon=f('FON',f'SHOWF AND {s["on"]} AND ({phase}=2 OR {phase}=3)')
    fl=p('FSPAN',f'MIN({s["start"]},BOOKBARS)')
    for i,value in enumerate(RETRACEMENTS):
        price=p(f'F{i}',f'{ruler["last"]}+({ruler["first"]}-{ruler["last"]})*{value}')
        b.segment(fon,fl,price,'0',price,'COLORFFCC00')
        b.text(fon,f'INTPART({fl}*0.15)',price+'+BPAD*0.35',f'{value*100:g}%','COLORFFCC00',0)
        b.number(fon,f'INTPART({fl}*0.65)',price+'+BPAD*0.35',price,'COLORFFCC00',2)
    # Actual origin/tip are marked even when the first child starts much
    # later. No child origin is allowed to overwrite either anchor.
    for role,age_,price in (('100%',ruler['start'],ruler['first']),('0%',ruler['end'],ruler['last'])):
        b.segment(fon,age_,price+'-BPAD*0.18',age_,price+'+BPAD*0.18','COLORFFCC00','LINETHICK2')
        b.segment(fon,f'MIN(DATACOUNT-1,{age_}+1)',price,f'MAX(0,{age_}-1)',price,'COLORFFCC00','LINETHICK2')
        b.text(fon,age_,price+'+BPAD*1.1',role,'COLORFFCC00',2)
        b.number(fon,age_,price+'+BPAD*1.1',price,'COLORFFCC00',2)
    ret=f('RET',f'IF({fon},ABS(C-{ruler["last"]})/MAX(ABS({ruler["first"]}-{ruler["last"]}),0.0000001)*100,0)')
    b.text(f'SHOWT AND {phase}=1 AND IF({proof},0,1)','0','H+BPAD*4','H1 BASIS?','COLORWHITE',2)
    return dict(ton=ton,fon=fon,h1=h,proof=proof,direction=d,phase=phase,ret=ret,
                ruler=ruler,leg_base=base,total_origin=origin,targets=prices)
