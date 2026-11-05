"""Current owned correction and its actual c/e endpoint, not the global low."""


def context_formulas():
    return ['{ 保留最后一次调整的起点与恢复时刻；内部小转折不移动已确认A }',
            'CCPHASE:=PHASE=2 OR PHASE=3;',
            'CCTIPRAW:=IF(WD=1,HHVBARS(H,WAGE+1),LLVBARS(L,WAGE+1));',
            'CCTIP:=IF(ISNULL(CCTIPRAW),0,CCTIPRAW);',
            'CCSTART:=CCPHASE AND (REF(CCPHASE,1)=0 OR WD<>REF(WD,1));',
            'CCROOTRAW:=VALUEWHEN(CCSTART,IF(PHASE=3,BP0,WORKEND));',
            'CCROOT:=IF(ISNULL(CCROOTRAW),0,CCROOTRAW);',
            'CCPOSRAW:=VALUEWHEN(CCSTART,BC-IF(PHASE=3,BA0+BN,CCTIP));',
            'CCPOS:=IF(ISNULL(CCPOSRAW),0,CCPOSRAW);',
            'CCDIRRAW:=VALUEWHEN(CCSTART,WD);',
            'CCDIR:=IF(ISNULL(CCDIRRAW),0,CCDIRRAW);',
            'CCRESUME:=PHASE=1 AND (REF(PHASE,1)=2 OR REF(PHASE,1)=3) AND WD=REF(WD,1);',
            'CCSNAP:=IF(CCPHASE,0,MIN(BC-1,BARSLAST(CCRESUME)));',
            'CCCHANGE:=CCSTART AND (CCPOS<>REF(CCPOS,1) OR CCDIR<>REF(CCDIR,1));',
            'CCLASTCHANGE:=MIN(BC-1,BARSLAST(CCCHANGE));',
            'CCPREVAGE1:=MIN(BC-1,CCLASTCHANGE+1+REF(BARSLAST(CCCHANGE),MIN(BC-1,CCLASTCHANGE+1)));',
            'CCPREVAGE2:=MIN(BC-1,CCPREVAGE1+1+REF(BARSLAST(CCCHANGE),MIN(BC-1,CCPREVAGE1+1)));',
            'CCVALID:=SUM(CCPHASE,0)>0 AND WD<>0 AND CCDIR=WD AND (CCPHASE OR SUM(CCRESUME,0)>0) AND CCPOS>=BC-WORKAGE;']


def correction_math(b):
    p=lambda suffix,expr:b.put('CR'+suffix,expr)
    f=lambda suffix,expr:b.frozen('CR'+suffix,expr)
    base=f('BASE','CCROOT');base_age=f('BASEAGE','BC-CCPOS')
    direction=f('DIR','WD');snap=f('SNAP','CCSNAP')
    gate=f('GATE','CCVALID AND BC-CCPOS<=BOOKBARS AND SHOWC AND SHOWBOOK')
    fine_type=f('FTYPE',f'REF(GTYPE,{snap})')
    points=[];ages=[]
    for i in range(6):
        points.append(f(f'P{i}',f'IF({fine_type}=(0-{direction}),REF(GP{i},{snap}),REF(GP{i+1},{snap}))'))
        ages.append(f(f'A{i}',f'IF({fine_type}=(0-{direction}),REF(GA{i},{snap}),REF(GA{i+1},{snap}))+{snap}+GN'))
    roots=[(base,base_age,'1')]
    for index in (1,2):
        value=f(f'PREVROOT{index}',f'REF(CCROOT,CCPREVAGE{index})')
        age=f(f'PREVAGE{index}',f'BC-REF(CCPOS,CCPREVAGE{index})')
        valid=f(f'PREVVALID{index}',f'SUM(CCCHANGE,0)>{index} AND REF(CCDIR,CCPREVAGE{index})=WD AND BC-REF(CCPOS,CCPREVAGE{index})<=WORKAGE')
        roots.append((value,age,valid))
    matches={}
    for count in (1,3,5):
        # Adjacent equal-price candles may choose different display points;
        # this one-bar allowance never changes the separately measured Q clock.
        enough=f(f'K{count}',f'IF({fine_type}=(0-{direction}),REF(GK{count},{snap}),REF(GK{count+1},{snap}))')
        # An older outer root can survive an internal b/d context switch only
        # if its proposed c/e is NEWER than the current context root. A closed
        # correction from before the latest main tip must not replace the new
        # adjustment with a long retrospective abc window.
        matching=' OR '.join(f'({valid}<>0 AND ABS({points[count]}-{value})<=BEPS AND ABS({ages[count]}-{age})<=GN AND {ages[0]}<{age} AND {ages[0]}<{base_age})'for value,age,valid in roots)
        matches[count]=p(f'MATCH{count}',f'{enough} AND ({matching})')
    length=p('LEN',f'IF({matches[5]},5,IF({matches[3]},3,IF({matches[1]},1,0)))')
    root=p('ROOT',f'IF({length}=5,{points[5]},IF({length}=3,{points[3]},{base}))')
    root_age=p('ROOTAGE',f'IF({length}=5,{ages[5]},IF({length}=3,{ages[3]},{base_age}))')
    z=[p(f'Z{i}',f'{direction}*{v}')for i,v in enumerate(points)]
    sizes=[p(f'H{i}',f'ABS({points[i]}-{points[i+1]})')for i in range(5)]
    duration=[p(f'T{i}',f'{ages[i+1]}-{ages[i]}')for i in range(5)]
    alternating=p('ALT',f'{z[0]}<{z[1]} AND {z[1]}>{z[2]} AND {z[2]}<{z[3]} AND {z[3]}>{z[4]} AND {z[4]}<{z[5]}')
    contracting=p('CONTRACT',' AND '.join(f'{sizes[i]}<{sizes[i+1]}'for i in range(4)))
    expanding=p('EXPAND',' AND '.join(f'{sizes[i]}>{sizes[i+1]}'for i in range(4)))
    trending=p('TREND',f'{z[0]}<{z[2]} AND {z[2]}<{z[4]} AND {z[1]}<{z[3]} AND {z[3]}<{z[5]}')
    band=p('BAND',f'(MAX1({",".join(z)})-MIN1({",".join(z)}))*0.2')
    sideways=p('SIDE',f'MAX1({z[0]},{z[2]},{z[4]})-MIN1({z[0]},{z[2]},{z[4]})<={band} AND MAX1({z[1]},{z[3]},{z[5]})-MIN1({z[1]},{z[3]},{z[5]})<={band}')
    irregular=p('IRREG',f'ABS({duration[0]}-{duration[2]})<=MAX(1,MIN({duration[0]},{duration[2]})*0.208) AND ABS({duration[1]}-{duration[3]})<=MAX(1,MIN({duration[1]},{duration[3]})*0.208)')
    middle_mask=p('MIDMASK',f'VX<={ages[4]} AND VX>={ages[1]}')
    merged_hi=b.extreme('CRMIDHI',middle_mask,'H',True)
    merged_lo=b.extreme('CRMIDLO',middle_mask,'L')
    internal=f('INTERNAL',f'COUNT({middle_mask} AND {direction}*(C-{points[3]})>BEPS,BC)>0')
    compound=p('COMPOUND',f'{internal} AND {merged_hi}<=MAX({points[4]},{points[1]})+BEPS AND {merged_lo}>=MIN({points[4]},{points[1]})-BEPS')
    five=f'IF({contracting},5,IF({expanding},6,IF({trending},7,IF({sideways},8,IF({irregular},9,IF({compound},10,0))))))'
    abc=f'IF({z[1]}>={z[3]},4,IF({z[0]}>{z[2]}+BEPS,3,2))'
    code=p('CODE',f'IF({length}=1,1,IF({length}=3,{abc},IF({length}=5 AND {alternating},{five},0)))')
    known=p('KNOWN',f'{gate} AND {code}>0 AND {ages[0]}<{root_age}')
    end=points[0];end_age=ages[0]
    trigger=f('TRIGGER',f'IF({length}=1,REF(IF({direction}=1,H,L),{end_age}),{points[1]})')
    confirm_age,confirm_has=b.first('CRCONFIRM',f'VX<{end_age} AND {direction}*(C-{trigger})>BEPS')
    fail_age,fail_has=b.first('CRFAIL',f'VX<{end_age} AND {direction}*(C-{end})<(0-BEPS)')
    confirmed=p('CONFIRMED',f'{known} AND {confirm_has} AND (IF({fail_has},0,1) OR {confirm_age}>{fail_age})')
    broken=p('BROKEN',f'{confirmed} AND {fail_has} AND {fail_age}<{confirm_age}')
    failed=p('FAILED',f'{known} AND {fail_has} AND (IF({confirm_has},0,1) OR {fail_age}>{confirm_age})')
    active=p('ACTIVE',f'{confirmed} AND IF({broken},0,1)')
    return dict(root=root,root_age=root_age,direction=direction,known=known,code=code,length=length,
                p=points,a=ages,end=end,end_age=end_age,trigger=trigger,confirmed=confirmed,
                confirm_age=confirm_age,broken=broken,fail_age=fail_age,failed=failed,active=active)


def drawing(b,c):
    names={1:'简单调整',2:'标准abc',3:'收缩abc',4:'强势abc',5:'收缩三角',6:'扩张三角',
           7:'趋势abcde',8:'横向abcde',9:'不规则abcde',10:'复杂abc·1-3-1候选'}
    d=c['direction'];end=c['end'];age=c['end_age'];known=c['known'];code=c['code']
    for number,name in names.items():
        b.text(f'{known} AND {code}={number}',age,f'{end}-{d}*PAD*3',name,'COLORMAGENTA',2)
    for count,letters in ((3,('c','b','a')),(5,('e','d','c','b','a'))):
        for i,letter in enumerate(letters):
            b.text(f'{known} AND {c["length"]}={count} AND {code}<>10',c['a'][i],
                   f'{c["p"][i]}-{d}*{1 if i%2==0 else -1}*PAD*0.4',letter,'COLORMAGENTA',0)
    for i,letter in ((0,'c'),(1,'b'),(4,'a')):
        b.text(f'{known} AND {code}=10',c['a'][i],f'{c["p"][i]}-{d}*PAD*0.4',letter,'COLORMAGENTA')
    mask=b.put('CRLINEMASK',' OR '.join(f'({c["length"]}>={i} AND VX={c["a"][i]})'for i in range(6)))
    value=b.put('CRLINEPRICE','+'.join(f'IF({c["length"]}>={i} AND VX={c["a"][i]},{c["p"][i]},0)'for i in range(6)))
    b.lines.append(f'POLYLINE({known} AND {mask},{value},COLORMAGENTA),LINETHICK2;')
    b.text(f'{known} AND IF({c["confirmed"]} OR {c["failed"]},0,1)',age,f'{end}-{d}*PAD*4','A?·待收盘确认','COLORMAGENTA')
    b.text(c['active'],age,f'{end}-{d}*PAD*4','调整A·收盘确认','COLORCYAN',2)
    b.text(c['failed'],c['fail_age'],f'{end}-{d}*PAD*4','调整结束点失效','COLORGRAY')
    b.text(c['broken'],c['fail_age'],f'{end}-{d}*PAD*4','A失守','COLORRED')
    b.segment(c['active'],age,end,'0',end,'COLORCYAN','DOT')
    b.number(c['active'],f'MIN({age},12)',end,end,'COLORCYAN',2)
    b.text(f'SHOWC AND SHOWBOOK AND CCPHASE AND IF({known},0,1)','0','C-BPAD*5*'+d,'调整进行中·未确认结束','COLORMAGENTA',2)
