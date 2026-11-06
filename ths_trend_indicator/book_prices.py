"""Current price plan from one selected degree; no parent/child H1 mixing."""


def draw(b,groups):
    main,live,previous,grand=groups
    p=lambda s,e:b.put('PR'+s,e)
    f=lambda s,e:b.frozen('PR'+s,e)
    # Main degree is preferred. A lower-degree fallback is explicit and only
    # allowed with no established direction or with that SAME direction.
    fallback=p('FALLBACK',f'IF({previous["on"]} AND (WD=0 OR {previous["direction"]}=WD),2,IF({live["on"]} AND (WD=0 OR {live["direction"]}=WD),1,0))')
    use_main=p('MAIN',main['on'])
    direction=f('DIR',f'IF({use_main},{main["direction"]},IF({fallback}=2,{previous["direction"]},IF({fallback}=1,{live["direction"]},WD)))')
    h=p('H',f'IF(MH1>0,MH1,IF({use_main},{main["h1"]},IF({fallback}=2,{previous["h1"]},{live["h1"]})))')
    end=p('END',f'IF({use_main},{main["p"][0]},IF({fallback}=2,{previous["p"][0]},{live["p"][0]}))')
    base=p('BASE',f'IF(MH1>0,MBASE,IF({use_main},{main["p"][1]},IF({fallback}=2,{previous["p"][1]},{live["p"][1]})))')
    age=p('AGE',f'IF(MH1>0,MAGE,IF({use_main},{main["a"][1]},IF({fallback}=2,{previous["a"][1]},{live["a"][1]})))')
    def origin(g):return f'IF({g["length"]}=7,{g["p"][7]},IF({g["length"]}=5,{g["p"][5]},{g["p"][3]}))'
    origin=p('ORIGIN',f'IF(MH1>0,MORG,IF({use_main},{origin(main)},IF({fallback}=2,{origin(previous)},{origin(live)})))')
    close=f('CLOSE','C')
    # Candidate counts with no first close-Q cannot supply a same-degree H1.
    def qualified(g):return g['proof']
    proof=p('PROOF',f'IF(MH1>0,MAGE>=1 AND MAGE<DATACOUNT,IF({use_main},{qualified(main)},IF({fallback}=2,{qualified(previous)},IF({fallback}=1,{qualified(live)},0))))')
    # A minor counter-bounce inside a parent correction must not switch
    # the whole price plan to trend targets. The causal PHASE uses the
    # parent B endpoint, not a PN-sized local turn. Unresolved direction
    # and unproven counts do not silently become a trending market.
    regime=f('REGIME',f'IF(KIND>0,IF(KIND=3,3,1),IF(SIDE,3,IF(WD<>0 OR {proof},1,0)))')
    phase=f('PHASE',f'IF(KIND>0,KIND,IF({regime}=3,3,IF({regime}=1,IF(WD<>0,PARENTPHASE,IF({direction}*({close}-{end})<0,2,1)),0)))')
    ton=f('TON',f'SHOWBOOK AND SHOWT AND {regime}=1 AND {proof} AND {phase}=1 AND {direction}<>0 AND {h}>0 AND {age}>=1 AND {age}<DATACOUNT')
    extreme=f('EXTREME',f'IF({direction}=1,HHV(H,MAX(1,{age})),LLV(L,MAX(1,{age})))')
    ratios=(1.208,1.618,2,2.382,2.618,3,3.236,4.236)
    targets=[];ok=[];distance=[]
    for i,ratio in enumerate(ratios,1):
        price=p(f'T{i}',f'{origin if i>=7 else base}+{direction}*{h}*{ratio}')
        gate='STRONG AND 'if i in (4,5,6)else''
        valid=p(f'OK{i}',f'{ton} AND {gate}{direction}*({price}-{extreme})>BEPS')
        targets.append(price);ok.append(valid)
        distance.append(p(f'D{i}',f'{direction}*({price}-{extreme})'))
    sentinel=p('SENTINEL','MAX1('+','.join(f'ABS({v})'for v in distance)+')+1')
    ranked=[p(f'RANKVAL{i}',f'IF({v},{distance[i-1]},{sentinel})')for i,v in enumerate(ok,1)]
    chosen=[p(f'INDEX{i}',f'SORTPOS(0,{i},{",".join(ranked)})')for i in range(1,4)]
    length=p('SPAN',f'MIN(DATACOUNT-1,MIN(SPAN,MAX(40,{age})))')
    for i,price in enumerate(targets,1):
        visible=p(f'SHOW{i}',f'{ok[i-1]} AND ('+' OR '.join(f'{v}={i}'for v in chosen)+')')
        b.segment(visible,length,price,'0',price,'COLORFF9900')
        caption=f'{price}+BPAD*0.35'
        b.text(visible,f'INTPART({length}*0.15)',caption,f'{ratios[i-1]:g}H1候选','COLORFF9900',0)
        b.number(visible,f'INTPART({length}*0.65)',caption,price,'COLORFF9900',2)
    b.text(f'{ton} AND IF({" OR ".join(ok)},0,1)','0','C','本组候选目标已触及','COLORWHITE')
    b.text(f'SHOWBOOK AND SHOWT AND {phase}=1 AND IF({proof},0,1)','0','H+PAD*3','同级H1依据不足','COLORWHITE')
    # Use the complete leg immediately before the current correction.
    # A count's whole trend origin and its latest impulse origin are
    # different anchors. In a range use the last complete B leg; there is
    # no directional H1 projection. Explicit manual anchors take priority.
    parent=p('FPARENT',f'{use_main} AND {phase}<>3')
    aligned=p('FALIGN',f'{phase}=3 OR {direction}=0 OR BTYPE={direction}')
    # A proved complete P can be a later impulse inside a merged parent.
    # Its origin, not that parent's much earlier origin, is the retracement
    # basis. Keep the owner's later same-direction extreme (an old confirmed
    # P tip may lag an extension), so selecting P cannot truncate its tail.
    pp=p('FPPRICE',f'IF({previous["length"]}=7,{previous["p"][7]},IF({previous["length"]}=5,{previous["p"][5]},{previous["p"][3]}))')
    pa=p('FPAGE',f'IF({previous["length"]}=7,{previous["a"][7]},IF({previous["length"]}=5,{previous["a"][5]},{previous["a"][3]}))')
    p_proven=p('FPPROVEN',f'{previous["on"]} AND {previous["proof"]} AND {previous["direction"]}={direction}')
    use_p=p('FUSEP',f'{parent} AND {p_proven} AND {pa}>OWN_PREVEND AND {pa}<=OWN_PREVSTART AND {direction}*(OWN_PREVP2-{previous["p"][0]})>=0')
    old_end=p('FOLDEND',f'IF({parent},OWN_PREVP2,IF({aligned},BP0,BP1))')
    old_start=p('FOLDSTART',f'IF({parent},OWN_PREVP1,IF({aligned},BP1,BP2))')
    # A near-complete return can leave the old directional ruler inverted
    # over a later, proved opposite P. Select that later complete swing,
    # not the stale opposite traversal of substantially the same range.
    reverse=p('FREVERSE',f'{phase}=2 AND {previous["on"]} AND {previous["proof"]} AND {previous["direction"]}=(0-{direction}) AND ABS({pp}-{previous["p"][0]})>=ABS({old_start}-{old_end})*0.786 AND ABS({pp}-{old_end})<=ABS({old_start}-{old_end})*0.236')
    extended=p('FEXTEND',f'{phase}=2 AND IF({reverse},0,1) AND BOOKTIPTURN AND {direction}=WD AND {direction}*(WORKEND-{old_end})>BEPS')
    extend_start=p('FEXTSTART',f'IF({p_proven},{pp},IF(BTYPE=WD,BP1,BP0))')
    extend_age=p('FEXTAGE',f'IF({p_proven},{pa},IF(BTYPE=WD,BA1+BN,BA0+BN))')
    fib_start=f('FSTART',f'IF(MANF=1,MFROM,IF({reverse},{pp},IF({extended},{extend_start},IF({use_p},{pp},{old_start}))))')
    fib_end=f('FEND',f'IF(MANF=1,MTO,IF({reverse},{previous["p"][0]},IF({extended},WORKEND,{old_end})))')
    fib_age=f('FAGE',f'IF(MANF=1,MFAGE,IF({reverse},{pa},IF({extended},{extend_age},IF({use_p},{pa},IF({parent},OWN_PREVSTART,IF({aligned},BA1+BN,BA2+BN))))))')
    fib_end_age=f('FENDAGE',f'IF(MANF=1,0,IF({reverse},{previous["a"][0]},IF({extended},WORKTIPAGE,IF({parent},OWN_PREVEND,IF({aligned},BA0+BN,BA1+BN)))))')
    fib_valid=p('FVALID',f'IF(MANF=1,MFAGE>=1 AND MFAGE<DATACOUNT,IF({extended},WORKVALID,IF({parent},1,IF({aligned},BK1,BK2)))) AND {fib_age}>{fib_end_age} AND {fib_age}<DATACOUNT AND ABS({fib_start}-{fib_end})>BEPS')
    fon=f('FON',f'SHOWBOOK AND SHOWF AND ({phase}=2 OR {phase}=3) AND {fib_valid}')
    retracement=f('RETRACE',f'IF({fon},ABS({close}-{fib_end})/MAX(ABS({fib_start}-{fib_end}),0.0000001)*100,0)')
    fl=p('FSPAN',f'MIN(DATACOUNT-1,MIN(SPAN,{fib_age}))')
    for i,ratio in enumerate((0,.236,.382,.5,.618,.786,1)):
        value=p(f'F{i}',f'{fib_end}+({fib_start}-{fib_end})*{ratio}')
        b.segment(fon,fl,value,'0',value,'COLORFFCC00')
        caption=f'{value}+BPAD*0.35'
        b.text(fon,f'INTPART({fl}*0.15)',caption,f'{ratio*100:g}%','COLORFFCC00',0)
        b.number(fon,f'INTPART({fl}*0.65)',caption,value,'COLORFFCC00',2)
    b.draw(f'行情类型:{regime},NODRAW;')
    b.draw(f'价格尺状态:{phase},NODRAW;')
    b.draw(f'工作回调百分比:{retracement},NODRAW;')
    display_direction=p('DISPLAYDIR',f'IF({fon},SIGN({fib_end}-{fib_start}),{direction})')
    return {'direction':display_direction,'phase':phase,'regime':regime,'target_on':ton,'fib_on':fon,'source':fallback,
            'fib_start':fib_start,'fib_end':fib_end,'fib_age':fib_age,'fib_end_age':fib_end_age}
