"""Current qualified trend and pivot references, from the selected Q family."""


def draw(b,key,trace,gate):
    p=lambda k,e:b.put(key+k,e)
    f=lambda k,e:b.frozen(key+k,e)
    s=trace['scope'];d=s['direction'];qs=trace['q'];n=trace['count']
    a=p('A',f'IF({n}=3,{qs[1]["t"]},IF({n}=2,{qs[0]["t"]},{s["start"]}))')
    pa=p('PA',f'IF({n}=3,{qs[1]["p"]},IF({n}=2,{qs[0]["p"]},{s["first"]}))')
    z=p('Z',f'IF({n}=3,{qs[2]["t"]},IF({n}=2,{qs[1]["t"]},{qs[0]["t"]}))')
    pz=p('PZ',f'IF({n}=3,{qs[2]["p"]},IF({n}=2,{qs[1]["p"]},{qs[0]["p"]}))')
    # A turning price often occurs both at a close and at the next open.
    # A supporting line uses the last equal-price candle of that local
    # plateau; its wave/Q label still belongs to the first occurrence.
    def last_equal(tag,age,price):
        return f(tag,f'LLV(IF(VX<={age} AND VX>=MAX(0,{age}-GN-1) AND ABS(IF({d}=1,L,H)-{price})<=BEPS,VX,DATACOUNT),BC)')
    a=last_equal('ALAST',a,pa);z=last_equal('ZLAST',z,pz)
    slope=p('SLOPE',f'({pz}-{pa})/MAX(1,{a}-{z})')
    line=p('LINE',f'{pz}+{slope}*({z}-VX)')
    env=f('ENV',f'COUNT(VX<={a} AND VX>={z} AND {d}*(IF({d}=1,L,H)-{line})<(0-BEPS),BC)=0')
    known=p('KNOWN',f'MAX(0,{z}-GN)')
    held=f('HELD',f'COUNT(VX<{known} AND {d}*(C-{line})<(0-BEPS),BC)=0')
    on=f('ON',f'SHOWL AND ({gate}) AND {s["on"]} AND {trace["proof"]} AND {n}>0 AND {a}>{z} AND {d}*({pz}-{pa})>BEPS AND {env} AND {held}')
    span=p('SPAN',f'MIN(SPAN,{a})')
    b.draw(f'POLYLINE({on} AND (VX={span} OR ISLASTBAR),{line},COLORWHITE),DOT;')
    b.text(on,'0',f'{pz}+{slope}*{z}+BPAD','TL?','COLORWHITE',0)
    qa,qc=qs[:2]
    ps=p('PSLOPE',f'({qc["r"]}-{qa["r"]})/MAX(1,{qa["a"]}-{qc["a"]})')
    ext=p('EXT',f'{qc["r"]}+{ps}*({qc["a"]}-VX)')
    translated_anchor=last_equal('PLAST',qa['t'],qa['p'])
    translated=p('PIVOT',f'{qa["p"]}+{ps}*({translated_anchor}-VX)')
    outside=f('OUTSIDE',f'COUNT(VX<={qa["a"]} AND VX>={qc["a"]} AND {d}*(IF({d}=1,H,L)-{ext})>BEPS,BC)=0')
    inside=f('INSIDE',f'COUNT(VX<={qa["a"]} AND VX>={qc["a"]} AND {d}*(IF({d}=1,L,H)-{translated})<(0-BEPS),BC)=0')
    ph=f('PHELD',f'COUNT(VX<MAX(0,{qc["a"]}-GN) AND {d}*(C-{translated})<(0-BEPS),BC)=0')
    pon=f('PON',f'SHOWL AND ({gate}) AND {s["on"]} AND {trace["proof"]} AND {n}>=2 AND {qa["a"]}>{qc["a"]} AND {d}*{ps}>BEPS AND {outside} AND {inside} AND {ph}')
    pspan=p('PSPAN',f'MIN(SPAN,{qa["a"]})')
    b.draw(f'POLYLINE({pon} AND (VX={pspan} OR ISLASTBAR),{translated},COLORMAGENTA),DOT;')
    b.text(pon,'0',f'{qa["p"]}+{ps}*{translated_anchor}-BPAD','PL?','COLORMAGENTA',0)
    return dict(trend=on,pivot=pon,trend_first_age=a,trend_second_age=z,
                trend_first_price=pa,trend_second_price=pz,pivot_slope=ps)
