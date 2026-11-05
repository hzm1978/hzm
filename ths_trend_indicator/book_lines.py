"""Construct candidate trend/pivot tools and audit their anchor envelopes."""


def draw(b,main):
    p=lambda s,e:b.put('LN'+s,e)
    f=lambda s,e:b.frozen('LN'+s,e)
    d=f('DIR','WD')
    start=f('START','WORKSTART');a0=f('A0','WORKAGE');tip=f('TIP','CCTIP')
    # Prefer the last adjustment BEFORE the terminal extreme. A later point
    # is not silently used as the regular second trend anchor.
    choice=p('ANCHOR',f'IF({d}=1,IF(LAGE0>{tip},0,1),IF(HAGE0>{tip},0,1))')
    second=f('SECOND',f'IF({d}=1,IF({choice}=0,BL0,BL1),IF({choice}=0,BH0,BH1))')
    a1=f('A1',f'IF({d}=1,IF({choice}=0,LAGE0,LAGE1),IF({choice}=0,HAGE0,HAGE1))')
    slope=p('SLOPE',f'({second}-{start})/MAX(1,{a0}-{a1})')
    now=p('NOW',f'{second}+{slope}*{a1}')
    value=p('VALUE',f'{now}-{slope}*VX')
    envelope=f('ENVELOPE',f'COUNT(VX<={a0} AND VX>={a1} AND IF({d}=1,L<{value}-BEPS,H>{value}+BEPS),BC)=0')
    valid=f('VALID',f'SHOWC AND SHOWBOOK AND WORKVALID AND {a0}>{a1} AND {a1}>{tip} AND {d}*({second}-{start})>0 AND {envelope}')
    span=p('SPAN',f'MIN(SPAN,{a0})')
    b.lines.append(f'POLYLINE({valid} AND (VX={span} OR ISLASTBAR),{value},COLORYELLOW),LINETHICK1;')
    # Book pivot tool: external side wave-1/wave-3 slope, translated to wave-2.
    # This is not a parallel copy of the trend-line slope.
    length=main['length'];points=main['p'];ages=main['a']
    def select(wave,arr):
        return '+'.join(f'IF({length}={size},{arr[size-wave]},0)'for size in (3,5,7))
    e1=p('EP1',select(1,points));e3=p('EP3',select(3,points))
    ea1=p('EA1',select(1,ages));ea3=p('EA3',select(3,ages))
    anchor=p('PIVOT',select(2,points));anchor_age=p('PIVOTA',select(2,ages))
    eslope=p('ESLOPE',f'({e3}-{e1})/MAX(1,{ea1}-{ea3})')
    ext=p('EXTERNAL',f'{e3}+{eslope}*({ea3}-VX)')
    translated=p('TRANSLATED',f'{anchor}+{eslope}*({anchor_age}-VX)')
    outside=f('OUTSIDE',f'COUNT(VX<={ea1} AND VX>={ea3} AND IF({d}=1,H>{ext}+BEPS,L<{ext}-BEPS),BC)=0')
    inside=f('INSIDE',f'COUNT(VX<={ea1} AND VX>={ea3} AND IF({d}=1,L<{translated}-BEPS,H>{translated}+BEPS),BC)=0')
    pivot_ok=p('PIVOTOK',f'SHOWBOOK AND SHOWC AND {main["on"]} AND {main["direction"]}={d} AND {ea1}>{ea3} AND {outside} AND {inside}')
    pspan=p('PSPAN',f'MIN(SPAN,{ea1})')
    b.lines.append(f'POLYLINE({pivot_ok} AND (VX={pspan} OR ISLASTBAR),{translated},COLORMAGENTA);')
    # A frozen line cannot report a historical break before anchor 2 was known.
    known_age=p('KNOWNAGE',f'MAX(0,{a1}-BN)')
    ba,bh=b.first('LNBREAK',f'VX<{known_age} AND {d}*(C-{value})<(0-BEPS)')
    standard=f('STANDARD',f'REF(IF({d}=1,BEARSTD,BULLSTD),{ba})')
    atr=f('ATR',f'REF(ATRVAL,{ba})')
    extreme=f('BREAKEXTREME',f'REF(WORKEND,{ba})')
    near=f('NEAR',f'ABS(REF(C,{ba})-{extreme})<=2*{atr}')
    deep=f('DEEP',f'REF(RET,{ba})>=62 AND REF(RET,{ba})<=78.6')
    complete=f'({main["key"]}SEVEN OR ({main["key"]}FIVE AND {main["key"]}REL5=0))'
    direct=p('DIRECT',f'{valid} AND {bh} AND {standard} AND {near} AND IF({deep},0,1) AND {complete} AND IF({main["pending"]},0,1)')
    b.text(direct,ba,f'{now}-{slope}*{ba}','标准收破·直接观察','COLORWHITE')
    level=f('DELAYLEVEL',f'REF(IF({d}=1,LLV(L,3),HHV(H,3)),MAX(0,{ba}-2))')
    fresh=f('FRESH',f'COUNT(VX<{ba} AND {d}*(IF({d}=1,H,L)-{extreme})>BEPS,BC)>0')
    delay_ready=p('DELAYREADY',f'{valid} AND {bh} AND {ba}>=2 AND IF({direct} OR {fresh},0,1)')
    b.text(f'{valid} AND {bh} AND {fresh}','0','C','创新极值·延时过滤','COLORGRAY')
    da,dh=b.first('LNDELAY',f'VX<{ba}-2 AND {d}*(C-{level})<(0-BEPS)')
    b.lines.append(f'POLYLINE({delay_ready} AND (VX={ba} OR ISLASTBAR),{level},COLORWHITE),DOT;')
    b.text(f'{delay_ready} AND IF({dh},0,1)','0',level,'延时线·待收盘','COLORWHITE',2)
    b.text(f'{delay_ready} AND {dh}',da,level,'延时收破·观察','COLORWHITE')
    return {'trend_valid':valid,'pivot_valid':pivot_ok,'delay_ready':delay_ready}
