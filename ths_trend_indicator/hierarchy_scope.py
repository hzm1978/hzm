"""Own whole parent swings before interpreting their internal Qs.

The origin follows a monotone chain of parent-side extremes, ending at
the first older contrary extreme. A child's last few turns cannot move it.
"""


def build(b):
    p=lambda k,e:b.put('HX'+k,e)
    f=lambda k,e:b.frozen('HX'+k,e)
    candidates={}
    for direction,tag in ((1,'U'),(-1,'D')):
        def put(k,e):return p(tag+k,e)
        def frozen(k,e):return f(tag+k,e)
        tip_age=frozen('BA',f'IF(BTYPE={direction},BA0,BA1)+BN')
        tip_price=frozen('BP',f'IF(BTYPE={direction},BP0,BP1)')
        counter_age=frozen('CA',f'IF(BTYPE={direction},BA1,BA0)+BN')
        mask=put('TIPMASK',f'VX<={counter_age}')
        z=b.extreme('HX'+tag+'TIPZ',mask,f'{direction}*'+('H'if direction==1 else'L'),True)
        # z is a signed trend extreme; choose in signed coordinates.
        extension=put('EXTOK',f'{z}>{direction}*{tip_price}+BEPS')
        run_age,run_has=b.first('HX'+tag+'RUNA',f'{mask} AND ABS('+('H'if direction==1 else'L')+f'-{direction}*{z})<=BEPS')
        end=frozen('END',f'IF({extension},{direction}*{z},{tip_price})')
        end_age=frozen('ENDA',f'IF({extension} AND {run_has},{run_age},{tip_age})')
        value=put('START0','0');age=put('AGE0','0')
        seen=put('SEEN0','0');stopped=put('STOP0','0')
        offset=put('OFFSET',f'IF(BTYPE={direction},1,IF({extension},0,2))')
        for i in range(5):
            indices=(i*2,i*2+1,i*2+2)
            point=put(f'POINT{i}',f'IF({offset}=0,BP{indices[0]},IF({offset}=1,BP{indices[1]},BP{indices[2]}))')
            point_age=put(f'POINTA{i}',f'IF({offset}=0,BA{indices[0]},IF({offset}=1,BA{indices[1]},BA{indices[2]}))+BN')
            available=put(f'POINTK{i}',f'IF({offset}=0,BK{indices[0]},IF({offset}=1,BK{indices[1]},BK{indices[2]}))')
            eligible=put(f'K{i}',f'{available} AND {point_age}>{end_age} AND {point_age}<=BOOKBARS')
            use=put(f'USE{i}',f'{eligible} AND IF({stopped},0,1) AND (IF({seen},0,1) OR {direction}*({point}-{value})<=BEPS)')
            stop=put(f'HALT{i}',f'{stopped} OR ({eligible} AND {seen} AND {direction}*({point}-{value})>BEPS)')
            value=put(f'START{i+1}',f'IF({use},{point},{value})')
            age=put(f'AGE{i+1}',f'IF({use},{point_age},{age})')
            seen=put(f'SEEN{i+1}',f'{seen} OR {eligible}')
            stopped=stop
        # Short histories can still own a complete observed excursion.
        fallback_mask=put('FMASK',f'VX>{end_age} AND VX<=MIN(BOOKBARS,DATACOUNT-1)')
        fallback=b.extreme('HX'+tag+'FALLBACK',fallback_mask,f'{direction}*'+('L'if direction==1 else'H'))
        fallback_age,has_fallback=b.first('HX'+tag+'FA',f'{fallback_mask} AND ABS('+('L'if direction==1 else'H')+f'-{direction}*{fallback})<=BEPS')
        first=frozen('FIRST',f'IF({seen},{value},{direction}*{fallback})')
        start=frozen('START',f'IF({seen},{age},{fallback_age})')
        valid=frozen('ON',f'({seen} OR {has_fallback}) AND {start}>{end_age} AND {direction}*({end}-{first})>BEPS')
        candidates[direction]=dict(first=first,start=start,last=end,end=end_age,on=valid,
                                   height=put('HEIGHT',f'IF({valid},ABS({end}-{first}),0)'))
    up,down=candidates[1],candidates[-1]
    chosen=f('DIR',f'IF(DIR<>0,DIR,IF({down["height"]}>{up["height"]},(0-1),IF({up["height"]}>{down["height"]},1,IF({down["end"]}<{up["end"]},(0-1),1))))')
    for field in ('first','start','last','end','on'):
        p('AUTO'+field.upper(),f'IF({chosen}=1,{up[field]},{down[field]})')
    manual_age,manual_has=b.first('HXMANEND',f'VX<MFAGE AND ABS(IF(MTO>=MFROM,H,L)-MTO)<=BEPS')
    ranged=f('RANGE','KIND=3 OR (KIND=0 AND SIDE)')
    direction=f('PDIR',f'IF(MANF=1,SIGN(MTO-MFROM),IF({ranged},SIGN(BP0-BP1),{chosen}))')
    first=f('PFIRST',f'IF(MANF=1,MFROM,IF({ranged},BP1,HXAUTOFIRST))')
    start=f('PSTART',f'IF(MANF=1,MFAGE,IF({ranged},BA1+BN,HXAUTOSTART))')
    last=f('PLAST',f'IF(MANF=1,MTO,IF({ranged},BP0,HXAUTOLAST))')
    end=f('PEND',f'IF(MANF=1,{manual_age},IF({ranged},BA0+BN,HXAUTOEND))')
    on=f('PON',f'{direction}<>0 AND {start}>{end} AND {start}<DATACOUNT AND HHV(C,BC)-LLV(C,BC)>BEPS AND IF(MANF=1,MFAGE>=1 AND {manual_has},IF({ranged},BK1,HXAUTOON))')
    turned=f('TURNED',f'{end}>=PN AND IF({direction}=1,HHV(H,PN)<{last},LLV(L,PN)>{last})')
    phase=f('PHASE',f'IF(KIND>0,KIND,IF({ranged},3,IF({on},IF({turned},2,1),0)))')
    regime=f('REGIME',f'IF({phase}=3,3,IF({on} OR MH1>0,1,0))')
    # The preceding parent root and its preceding opposite point give the
    # main context: e.g. older high -> 865 -> 1015 -> 831, not all tiny highs.
    def previous(tag,after,kind):
        mask=p(tag+'MASK',f'IF({kind}=1,BHC,BLC) AND VX+BN>{after} AND VX+BN<=BOOKBARS')
        raw=p(tag+'RAW',f'BARSLAST({mask})')
        has=f(tag+'HAS',f'COUNT({mask},BC)>0')
        age=f(tag+'AGE',f'IF({has},{raw}+BN,DATACOUNT)')
        price=f(tag+'PRICE',f'REF(IF({kind}=1,H,L),MIN(DATACOUNT-1,{age}))')
        return price,age,has
    prior,prior_age,has_prior=previous('PRIOR',start,direction)
    # Restore the previous whole parent excursion through its earlier
    # extremes; a small inner retracement cannot become the M origin.
    stopped='0'
    for i in range(2):
        older,older_age,older_has=previous('PRIOROLD'+str(i),prior_age,direction)
        use=p('PRIORUSE'+str(i),f'{has_prior} AND {older_has} AND IF({stopped},0,1) AND {direction}*({older}-{prior})>BEPS')
        stopped=p('PRIORSTOP'+str(i),f'{stopped} OR ({older_has} AND IF({use},0,1))')
        prior=f('PRIORFULL'+str(i),f'IF({use},{older},{prior})')
        prior_age=f('PRIORFULLAGE'+str(i),f'IF({use},{older_age},{prior_age})')
    before,before_age,has_before=previous('BEFORE',prior_age,f'(0-{direction})')
    main_start=f('MSTART',f'IF({has_before},{before_age},{start})')
    main_first=f('MFIRST',f'IF({has_before},{before},{first})')
    main_on=f('MON',f'{on} AND {main_start}>{end} AND {direction}*({last}-{main_first})>BEPS')
    return {'P':dict(first=first,start=start,last=last,end=end,direction=direction,on=on),
            'M':dict(first=main_first,start=main_start,last=last,end=end,direction=direction,on=main_on),
            'prior':dict(price=prior,age=prior_age,on=has_prior),
            'phase':phase,'regime':regime}
