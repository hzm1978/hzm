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
    def origin_age(g):return f'IF({g["length"]}=7,{g["a"][7]},IF({g["length"]}=5,{g["a"][5]},{g["a"][3]}))'
    origin=p('ORIGIN',f'IF(MH1>0,MORG,IF({use_main},{origin(main)},IF({fallback}=2,{origin(previous)},{origin(live)})))')
    oa=p('ORIGINAGE',f'IF({use_main},{origin_age(main)},IF({fallback}=2,{origin_age(previous)},{origin_age(live)}))')
    close=f('CLOSE','C')
    # Candidate counts with no first close-Q cannot supply a same-degree H1.
    def qualified(g):return g['proof']
    proof=p('PROOF',f'IF(MH1>0,MAGE>=1 AND MAGE<DATACOUNT,IF({use_main},{qualified(main)},IF({fallback}=2,{qualified(previous)},IF({fallback}=1,{qualified(live)},0))))')
    phase=f('PHASE',f'IF(KIND>0,KIND,IF(WD<>0,PHASE,IF({direction}*({close}-{end})<0,2,1)))')
    ton=f('TON',f'SHOWBOOK AND SHOWT AND {proof} AND {phase}=1 AND {direction}<>0 AND {h}>0 AND {age}>=1 AND {age}<DATACOUNT')
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
        b.text(visible,f'MIN(12,{length})',price,f'{ratios[i-1]:g}H1候选','COLORFF9900',0)
        b.number(visible,f'MIN(35,{length})',price,price,'COLORFF9900',2)
    b.text(f'{ton} AND IF({" OR ".join(ok)},0,1)','0','C','本组候选目标已触及','COLORWHITE')
    b.text(f'SHOWBOOK AND SHOWT AND {phase}=1 AND IF({proof},0,1)','0','H+PAD*3','同级H1依据不足','COLORWHITE')
    # If no parent ruler exists, retain the WHOLE observed completed count,
    # rather than using the latest small return inside it.
    fon=f('FON',f'SHOWBOOK AND SHOWF AND IF(FON,0,1) AND {fallback}>0 AND ({phase}=2 OR {phase}=3) AND ABS({origin}-{end})>BEPS')
    fl=p('FSPAN',f'MIN(DATACOUNT-1,MIN(SPAN,{oa}))')
    for i,ratio in enumerate((0,.236,.382,.5,.618,.786,1)):
        value=p(f'F{i}',f'{end}+({origin}-{end})*{ratio}')
        b.segment(fon,fl,value,'0',value,'COLORFFCC00')
        b.text(fon,f'MIN(10,{fl})',value,f'{ratio*100:g}%','COLORFFCC00',0)
        b.number(fon,f'MIN(40,{fl})',value,value,'COLORFFCC00',2)
    return {'direction':direction,'phase':phase,'target_on':ton,'fib_on':fon,'source':fallback}
