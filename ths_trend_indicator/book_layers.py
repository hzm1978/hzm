"""Generate bounded, explicitly retrospective Q-based structure overlays.

Every numbered count has its own price/time window and actual first crossing.
The current as-of overlay may redraw; it never feeds the causal core or orders.
"""


def relation(a,b):
    return (f'IF({a}<=10 AND {b}<=10,'
            f'IF(ABS({b}-{a})<=2,0,IF({b}-{a}>5,2,IF({b}-{a}>=3,1,IF({b}-{a}<(0-5),(0-2),(0-1))))),'
            f'IF({b}/MAX({a},1)>=0.828 AND {b}/MAX({a},1)<=1.208,0,'
            f'IF({b}/MAX({a},1)<0.382,(0-2),IF({b}/MAX({a},1)<0.828,(0-1),IF({b}/MAX({a},1)<=2.618,1,2)))))')


class Builder:
    def __init__(self): self.lines=[];self.owner='overview'
    def draw(self,text):
        self.lines.append(text+' { OWNER:'+self.owner+' }')
    def put(self,name,expr):
        self.lines.append(f'{name}:={expr};');return name
    def frozen(self,name,expr):
        # A snapshot is needed only at the as-of end. Without this guard
        # native HHV/COUNT scans inside REFX1 repeat for every history bar.
        return self.put(name,f'REFX1(IF(ISLASTBAR,{expr},0),100000)')
    def first(self,name,condition):
        raw=self.put(name+'RAW',f'IF(ISLASTBAR,BARSSINCEN({condition},BC),0)')
        safe=self.put(name+'SAFE',f'IF(ISNULL({raw}),0,{raw})')
        age=self.frozen(name,safe)
        has=self.frozen(name+'HAS',f'COUNT({condition},BC)>0')
        return age,has
    def extreme(self,name,mask,price,maximum=False):
        fn='HHV'if maximum else'LLV';fallback='BSAFELO'if maximum else'BSAFEHI'
        return self.frozen(name,f'{fn}(IF({mask},{price},{fallback}),BC)')
    def text(self,cond,age,price,text,color,align=1,font=9):
        # KTEXT documents position and text to the left/centre/right of its
        # candle. DRAWTEXT only documents COLOR, not ALIGN or FONTSIZE.
        # Native KTEXT LCR and number ALIGN share TextShape.HorizontalAlign:
        # 0/1/2. Reversing it made the RET prefix overlap its number.
        import re
        price=re.sub(r'\bPAD\b','BPAD',price)
        # KTEXT also stores POSITION as a constant HorizontalOffset. A
        # dynamic POSITION can silently become NaN in native shape JSON.
        # Place the label on its historical candle with a literal offset 0.
        self.draw(f"KTEXT(VX=REFX1({age},100000) AND REFX1(IF({cond},1,0),100000)>0,0,REFX1({price},100000),{align},{color},'{text}'),FONTSIZE{font};")
    def number(self,cond,age,price,value,color,precision=1,font=9):
        # The official parser rejects an X variable in DRAWSHIFTNUMBER.
        # Wrapping it in INTPART compiles but yields an invalid constant
        # HorizontalOffset in the native shape description. Draw on the
        # target candle instead, freezing ALL four inputs at the as-of end.
        # In particular PAD/price and the gate may vary over history.
        self.draw(f'DRAWNUMBER(VX=REFX1({age},100000) AND REFX1(IF({cond},1,0),100000)>0,REFX1({price},100000),REFX1({value},100000),{precision},{color}),ALIGN0,FONTSIZE{font};')
    def segment(self,cond,from_age,from_price,to_age,to_price,color,style='LINETHICK1'):
        # DRAWLINE3 pairs a start bar with a LATER end bar. Use penultimate
        # and last bars as those triggers, then shift both to actual endpoints.
        # Do not assume C1/C2 on the same bar are accepted by this API.
        self.draw(f'DRAWLINE3(VX=1 AND {cond},{from_price},MAX(0,{from_age}-1),ISLASTBAR AND {cond},{to_price},{to_age},0),{color},{style};')


def group(b,key,source,n,start_age,end_age,start_price,end_price,gate,label,color,
          lane=1,force_direction=False,correction=None):
    """One count inside one owner leg; lower counts cannot redefine the owner."""
    b.owner=key
    put=lambda suffix,expr:b.put(key+suffix,expr)
    frozen=lambda suffix,expr:b.frozen(key+suffix,expr)
    first=lambda suffix,cond:b.first(key+suffix,cond)
    os=frozen('OS',f'MIN({start_age},BOOKBARS)')
    oe=frozen('OE',end_age)
    od=frozen('OD',f'SIGN({end_price}-{start_price})')
    enabled=frozen('GATE',f'{gate} AND SHOWBOOK AND {start_age}>{end_age}')
    snap=put('SNAP',f'MAX(0,{oe}-{n})')
    kind=frozen('TYPE',f'REF({source}TYPE,{snap})')
    p=[frozen(f'RP{i}',f'REF({source}P{i},{snap})')for i in range(8)]
    a=[frozen(f'RA{i}',f'REF({source}A{i},{snap})+{snap}+{n}')for i in range(8)]
    valid=[frozen(f'RK{i}',f'REF({source}K{i},{snap})')for i in range(8)]
    mask=put('RMASK',f'VX<{a[0]} AND VX>={oe}')
    hi=b.extreme(key+'RHI',mask,'H',True)
    lo=b.extreme(key+'RLO',mask,'L')
    rp=put('RUNP',f'IF({kind}=(0-1),{hi},{lo})')
    ra,rhas=first('RUNA',f'{mask} AND ABS(IF({kind}=(0-1),H,L)-{rp})<=BEPS')
    preference=f'(0-{kind})=WD'if force_direction else f'((0-{kind})={od} OR (0-{kind})*({rp}-{p[1]})>BEPS)'
    run=put('RUN',f'{rhas} AND {valid[0]} AND (0-{kind})*({rp}-{p[0]})>BEPS AND {preference}')
    direction=put('DIR',f'{kind}*(1-2*{run})')
    fp=[put(f'P{i}',f'IF({run},{rp if i==0 else p[i-1]},{p[i]})')for i in range(8)]
    fa=[put(f'A{i}',f'IF({run},{ra if i==0 else a[i-1]},{a[i]})')for i in range(8)]
    raw_fa=list(fa)
    if source=='G':
        # Fractals may select the last equal price of a plateau. A bounce
        # can first reach its high BEFORE the first-break candle but repeat
        # that high at the next open. Restore each point's first occurrence
        # chronologically, so a legitimate two-bar Q is not rejected as a
        # fictitious same-bar bounce/break. No intrabar order is invented.
        for i in range(6,-1,-1):
            extreme=f'IF({direction}={1 if i%2==0 else -1},H,L)'
            raw=put(f'FIRSTRAW{i}',f'IF(ISLASTBAR,BARSSINCEN(VX<={os} AND VX>={oe} AND VX<{fa[i+1]} AND VX>={fa[i]} AND ABS({extreme}-{fp[i]})<=BEPS,BC),0)')
            fa[i]=frozen(f'FIRSTA{i}',f'IF(ISNULL({raw}),{fa[i]},{raw})')
    if correction:
        c=correction
        use=put('CENDUSE',f'{c["known"]} AND {c["direction"]}={direction} AND ABS({c["root"]}-{fp[2]})<=BEPS AND {c["end_age"]}<{fa[2]} AND {c["end_age"]}>{fa[0]}')
        fp[1]=put('CENDP',f'IF({use},{c["end"]},{fp[1]})')
        fa[1]=put('CENDA',f'IF({use},{c["end_age"]},{fa[1]})')
    bounds={length:put(f'B{length}',f'{enabled} AND IF({run},{valid[length-1]},{valid[length]}) AND {fa[length]}<={os} AND {fa[0]}>={oe}')for length in(3,5,7)}
    if force_direction:
        for length in (3,5,7):
            bounds[length]=put(f'BD{length}',f'{bounds[length]} AND {direction}=WD')
    qs=[]
    counter_price=f'IF({direction}=1,L,(0-H))'
    extreme_price=f'IF({direction}=1,H,L)'
    for j,r in enumerate((2,4,6)):
        q=key+f'Q{j}'
        clock,ch=b.first(q+'CLOCK',f'VX<={fa[r+1]} AND VX>={fa[r]} AND ABS({extreme_price}-{fp[r]})<=BEPS')
        band=b.put(q+'BAND',f'VX<{clock} AND VX>={fa[r-2]}')
        wa,wh=b.first(q+'WA',f'{band} AND {direction}*({extreme_price}-{fp[r]})>BEPS')
        ca,cc=b.first(q+'CA',f'{band} AND {direction}*(C-{fp[r]})>BEPS')
        wn=b.put(q+'WN',f'{clock}-{wa}');cn=b.put(q+'CN',f'{clock}-{ca}')
        before=b.extreme(q+'BEFORE',f'VX<{clock} AND VX>={fa[r-1]}',counter_price)
        total=b.extreme(q+'LOW',f'VX<{clock} AND VX>={wa}',counter_price)
        prior=b.frozen(q+'PRIOR',f'REF({extreme_price},{clock}+1)')
        common=b.put(q+'OK',f'{ch} AND {wh} AND {wn}>=2 AND {clock}<DATACOUNT-1 AND {direction}*({fp[r]}-{prior})>BEPS AND {fa[r-1]}>{wa} AND ABS({before}-{total})<=BEPS AND {direction}*({fp[r-2]}-{fp[r]})>BEPS')
        close_ok=b.put(q+'COK',f'{common} AND {cc} AND {cn}>=2')
        hidden=b.frozen(q+'HIDDEN',f'{wh} AND COUNT(VX<{clock} AND VX>={wa} AND {direction}*(C-O)<=0,BC)=0')
        item=dict(clock=clock,wa=wa,ca=ca,wn=wn,cn=cn,ok=common,cok=close_ok,
                  hidden=hidden,root=fp[r],counter=fp[r-1],counter_age=fa[r-1])
        if key in ('E1','E2','E3'):
            item['snapshot_age']=put(f'QSNAP{j}',f'MAX(0,{raw_fa[r-1]}-GN)')
        qs.append(item)
    rel5=put('REL5',relation(qs[1]['cn'],qs[0]['wn']))
    rel71=put('REL71',relation(qs[2]['cn'],qs[1]['wn']))
    rel72=put('REL72',relation(qs[1]['wn'],qs[0]['wn']))
    heights=[put(f'IMP{i}',f'{direction}*({fp[2*i]}-{fp[2*i+1]})')for i in range(4)]
    merge5=put('MERGE5',f'{heights[1]}>1.618*{heights[2]} OR {heights[0]}>1.618*{heights[1]}')
    merge7=put('MERGE7',f'{merge5} OR {heights[2]}>1.618*{heights[3]}')
    same3=put('SAME3',f'{bounds[7]} AND {qs[2]["cok"]} AND {qs[1]["ok"]} AND {qs[0]["ok"]} AND {rel71}=0 AND {rel72}=0')
    seven=put('SEVEN',f'{bounds[7]} AND {qs[2]["cok"]} AND {qs[1]["ok"]} AND {qs[0]["ok"]} AND ABS({rel71})=1 AND ABS({rel72})<=1 AND IF({merge7},0,1)')
    five=put('FIVE',f'{bounds[5]} AND {qs[1]["cok"]} AND {qs[0]["ok"]} AND ABS({rel5})<=1 AND IF({merge5} OR {same3},0,1)')
    closing=frozen('CLOSE',f'REF(C,{oe})')
    pending5=put('PEND5',f'{bounds[5]} AND {run} AND {qs[1]["cok"]} AND IF({qs[0]["ok"]} OR {merge5},0,1) AND {direction}*({closing}-{fp[1]})>=0')
    three=put('THREE',f'{bounds[3]} AND {qs[0]["cok"]}')
    pending3=put('PEND3',f'{bounds[3]} AND {run} AND IF({qs[0]["ok"]},0,1) AND {direction}*({closing}-{fp[1]})>=0 AND {direction}*({fp[0]}-{fp[1]})>BEPS')
    merge_base=put('MERGEBASE',f'{bounds[5]} AND {qs[1]["cok"]} AND {qs[0]["ok"]} AND {heights[1]}>1.618*{heights[2]}')
    merge_last=put('MERGELAST',f'{bounds[5]} AND {qs[1]["cok"]} AND {qs[0]["ok"]} AND {heights[0]}>1.618*{heights[1]}')
    mode=put('MERGEMODE',f'IF({same3},1,IF({merge_base},2,IF({merge_last},3,0)))')
    original_p=list(fp);original_a=list(fa);original_q=[dict(q)for q in qs]
    for index,choices in ((1,(5,1,3)),(2,(6,2,4)),(3,(7,5,5))):
        fp[index]=put(f'MP{index}',f'IF({mode}=1,{original_p[choices[0]]},IF({mode}=2,{original_p[choices[1]]},IF({mode}=3,{original_p[choices[2]]},{original_p[index]})))')
        fa[index]=put(f'MA{index}',f'IF({mode}=1,{original_a[choices[0]]},IF({mode}=2,{original_a[choices[1]]},IF({mode}=3,{original_a[choices[2]]},{original_a[index]})))')
    for field in qs[0]:
        qs[0][field]=put('MERGEDQ'+field.upper(),f'IF({mode}=1,{original_q[2][field]},IF({mode}=3,{original_q[1][field]},{original_q[0][field]}))')
    length=put('LEN',f'IF({mode}>0,3,IF({seven},7,IF({five} OR {pending5},5,IF({three} OR {pending3},3,0))))')
    on=put('ON',f'{length}>0')
    pending=put('PENDING',f'IF({mode}>0,{mode}=2 AND IF({qs[0]["cok"]},0,1),({length}=5 AND {pending5}) OR ({length}=3 AND {pending3}))')
    for leg in range(7):
        cond=f'{on} AND {length}>={leg+1}'
        if leg==0:
            b.segment(f'{cond} AND {pending}',fa[1],fp[1],fa[0],fp[0],color,'DOT')
            cond+=f' AND IF({pending},0,1)'
        b.segment(cond,fa[leg+1],fp[leg+1],fa[leg],fp[leg],color,'LINETHICK2')
    for wave in range(1,8):
        possibilities=[v for v in (3,5,7) if wave<=v]
        price=put(f'WP{wave}','+'.join(f'IF({length}={v},{fp[v-wave]},0)'for v in possibilities))
        age=put(f'WA{wave}','+'.join(f'IF({length}={v},{fa[v-wave]},0)'for v in possibilities))
        y=f'{price}+{direction}*{1 if wave%2 else -1}*PAD*{lane}'
        b.text(f'{on} AND {length}>={wave} AND IF({pending} AND {length}={wave},0,1)',age,y,f'{label}{wave}',color,font=11)
        if wave in (3,5,7):b.text(f'{on} AND {pending} AND {length}={wave}',age,y,f'{label}{wave}?',color,font=11)
    for wave in (2,4,6):
        valid_lengths=[v for v in (3,5,7)if wave<v]
        ret=put(f'RET{wave}','+'.join(f'IF({length}={v},ABS({fp[v-wave]}-{fp[v-wave+1]})/MAX(ABS({fp[v-wave+1]}-{fp[v-wave+2]}),0.0000001)*100,0)'for v in valid_lengths))
        age=key+f'WA{wave}';price=key+f'WP{wave}';y=f'{price}-{direction}*PAD*({lane}+1)'
        b.text(f'{on} AND {length}>{wave}',age,y,f'{label}{wave}回调%',color,2)
        b.number(f'{on} AND {length}>{wave}',age,y,ret,color)
    for j,q in enumerate(qs):
        is_first=f'({length}=3 AND {j}=0) OR ({length}=5 AND {j}=1) OR ({length}=7 AND {j}=2)'
        qa=put(f'QA{j}',f'IF({is_first},{q["ca"]},{q["wa"]})')
        qn=put(f'QN{j}',f'IF({is_first},{q["cn"]},{q["wn"]})')
        qshow=put(f'QSHOW{j}',f'{on} AND SHOWQ AND {length}>={2*j+3}')
        qpending=put(f'QWAIT{j}',f'{pending} AND {j}=0')
        end=put(f'QE{j}',f'IF({qpending},{oe},{qa})')
        number=put(f'QNUMBER{j}',f'IF({qpending},{q["clock"]}-{oe},{qn})')
        mid=put(f'QMID{j}',f'INTPART(({q["clock"]}+{end})/2)')
        b.segment(qshow,q['clock'],q['root'],end,q['root'],color)
        for size in (3,5,7):
            ordinal=(size-1)//2-j
            if ordinal>0:
                b.text(f'{qshow} AND {length}={size}',mid,f'{q["root"]}+{direction}*PAD*0.7',f'{label}Q{ordinal}',color)
        b.number(qshow,mid,q['root'],number,color,0)
        b.text(f'{qshow} AND {qpending}',end,f'{q["root"]}+{direction}*PAD*1.4','≥进行中',color)
        b.text(f'{qshow} AND {q["hidden"]}',q['clock'],f'{q["root"]}+{direction}*PAD*1.4','隐形Q',color)
        # Historical single-leg adjustments may be marked on each selected
        # degree. Do not call a multi-turn correction "simple" or replace a
        # triangle's e with its most extreme a.
        # Equal-price repeats of the root are not an internal adjustment.
        # Require an alternating return turn after a real opposite turn
        # inside this adjustment; the ending extreme itself is excluded.
        high_return=f'({direction}=1 AND GHC AND VX+GLT+GN<{fa[2*j+2]} AND VX+GLT+GN>{q["counter_age"]})'
        low_return=f'({direction}=(0-1) AND GLC AND VX+GHT+GN<{fa[2*j+2]} AND VX+GHT+GN>{q["counter_age"]})'
        inner=frozen(f'QINNER{j}',f'COUNT(({high_return} OR {low_return}) AND VX+GN<{fa[2*j+2]} AND VX+GN>{q["counter_age"]},BC)')
        covered=frozen(f'QCOVER{j}',f'COUNT((GHC OR GLC) AND VX+GN>={q["clock"]},BC)>0')
        simple=put(f'QSIMPLE{j}',f'{qshow} AND SHOWC AND {inner}=0 AND {covered} AND {q["cok"]}')
        b.text(simple,q['counter_age'],f'{q["counter"]}-{direction}*PAD*({lane}+2.5)',f'{label}A·简单调整',color,2)
        if key in ('E1','E2','E3'):
            snap=q['snapshot_age']
            def shape_match(size):
                return f'REF(GK{size},{snap}) AND ABS(REF(GP{size},{snap})-{q["root"]})<=BEPS AND ABS(REF(GA{size},{snap})+{snap}+GN-{q["clock"]})<=GN'
            match=f'REF(GTYPE,{snap})=(0-{direction}) AND ABS(REF(GP0,{snap})-{q["counter"]})<=BEPS'
            shape=frozen(f'QSHAPE{j}',f'IF({match},IF({shape_match(5)},REF(GAC5,{snap}),IF({shape_match(3)},REF(GAC3,{snap}),0)),0)')
            q['shape_code']=shape
            y=f'{q["counter"]}-{direction}*PAD*({lane}+2.5)'
            compound=f'{qshow} AND SHOWC AND {inner}>0'
            b.text(f'{compound} AND {shape}>0',q['counter_age'],y,f'{label}AD',color,2)
            b.number(f'{compound} AND {shape}>0',q['counter_age'],y,shape,color,0)
            b.text(f'{compound} AND {shape}=0',q['counter_age'],y,f'{label}调整待确认',color,2)
    caption_y=f'{fp[0]}+{direction}*PAD*({lane}+2)'
    b.text(f'{length}=5 AND {five} AND {rel5}=0',fa[0],caption_y,'同级5·结构解释',color,2)
    b.text(f'{length}=5 AND {five} AND {rel5}=1',fa[0],caption_y,'扩张5·待第三Q',color)
    b.text(f'{length}=5 AND {five} AND {rel5}=(0-1)',fa[0],caption_y,'收缩5·待第三Q',color)
    b.text(f'{length}=3 AND {three} AND {mode}=0',fa[0],caption_y,'3浪·待耦合',color)
    b.text(f'{pending5}',fa[0],caption_y,'5?·未破浪3',color)
    b.text(f'{pending3} AND {length}=3',fa[0],caption_y,'3?·第一Q进行中',color)
    b.text(f'{seven} AND {rel71}=1 AND {rel72}>=0',fa[0],caption_y,'扩张7·结构解释',color)
    b.text(f'{seven} AND {rel71}=(0-1) AND {rel72}<=0',fa[0],caption_y,'收缩7·结构解释',color)
    b.text(f'{seven} AND {rel71}=1 AND {rel72}=(0-1)',fa[0],caption_y,'钻石7·结构解释',color)
    b.text(f'{seven} AND {rel71}=(0-1) AND {rel72}=1',fa[0],caption_y,'X7·结构解释',color)
    b.text(f'{mode}=1',fa[0],caption_y,'同级合并·3浪解释',color,2)
    b.text(f'{mode}>=2 AND IF({pending},0,1)',fa[0],caption_y,'高度合并·3浪解释',color,2)
    b.text(f'{mode}>=2 AND {pending}',fa[0],caption_y,'高度合并·待收盘Q',color,2)
    h1=put('PROJH',f'IF({length}=7,ABS({fp[6]}-{fp[7]}),IF({length}=5,ABS({fp[4]}-{fp[5]}),ABS({fp[2]}-{fp[3]})))')
    proof=put('PROOF',f'{on} AND IF({length}=7,{qs[2]["cok"]},IF({length}=5,{qs[1]["cok"]},{qs[0]["cok"]}))')
    if key!='MW':
        end_watch=f'SHOWT AND {on} AND {proof} AND {length}>=5 AND IF({pending},0,1)'
        y=f'{fp[0]}-{direction}*PAD*({lane}+1.2)'
        b.text(end_watch,fa[0],y,'段末·观察',color,2)
        b.number(f'{end_watch} AND {oe}>0',fa[0],y,fp[0],color,2)
    return dict(key=key,on=on,length=length,direction=direction,p=fp,a=fa,qs=qs,h1=h1,run=run,pending=pending,proof=proof)


def layers():
    b=Builder()
    b.lines.append('{ 当前前缀的书本结构图层：第一Q收盘，后续Q影线；候选与进行中分开 }')
    # A positive BARPOS window explicitly spans the loaded prefix. The
    # native host's zero-window extrema can be 0 inside this snapshot,
    # making 1 an invalid "above every price" fallback for LLV masks.
    b.frozen('BSAFEHI','MAX(ABS(HHV(H,BC)),ABS(LLV(L,BC)))+1')
    b.put('BSAFELO','0-BSAFEHI')
    b.frozen('BEPS','MAX(ABS(C)*0.0000000001,0.0000001)')
    b.frozen('BPAD','PAD')
    from book_corrections import correction_math,drawing as correction_drawing
    correction=correction_math(b)
    groups=[]
    groups.append(group(b,'MW','B','BN','WORKAGE','0','WORKSTART','C','WORKVALID','主',
                        'COLORYELLOW',2,True,correction))
    main=groups[0]
    active=b.put('OWN_ACTIVE',f'{main["run"]} OR (PARENTPHASE=1 AND {main["direction"]}=WD)')
    current_start=b.put('OWN_CURRENTAGE',f'IF({main["on"]},IF({active},{main["a"][1]},{main["a"][0]}),BA0+BN)')
    current_price=b.put('OWN_CURRENTPRICE',f'IF({main["on"]},IF({active},{main["p"][1]},{main["p"][0]}),BP0)')
    previous_start=b.put('OWN_PREVSTART',f'IF({main["on"]},IF({active},{main["a"][2]},{main["a"][1]}),BA1+BN)')
    previous_end=b.put('OWN_PREVEND',f'IF({main["on"]},IF({active},{main["a"][1]},{main["a"][0]}),BA0+BN)')
    previous_p1=b.put('OWN_PREVP1',f'IF({main["on"]},IF({active},{main["p"][2]},{main["p"][1]}),BP1)')
    previous_p2=b.put('OWN_PREVP2',f'IF({main["on"]},IF({active},{main["p"][1]},{main["p"][0]}),BP0)')
    # Degree is established by owned time windows and actual Q coupling,
    # not by rejecting any pullback shorter than PN confirmation bars.
    # Start child searches with the finest observed extrema, including
    # first-equal/two-bar Qs. Each child's Q/merge rules still apply.
    groups.append(group(b,'SW','G','GN',current_start,'0',current_price,'C',f'({main["on"]} OR BK0) AND SHOWSUB','子',
                        'COLORCYAN',0.6,correction=correction))
    groups.append(group(b,'PW','G','GN',previous_start,previous_end,previous_p1,previous_p2,f'({main["on"]} OR BK1) AND SHOWSUB','子前',
                        'COLORLIBLUE',0.6,correction=correction))
    live,previous=groups[1:]
    gp1=b.put('OWN_GP1',f'IF({live["on"]},{live["p"][1]},{previous["p"][1]})')
    gp2=b.put('OWN_GP2',f'IF({live["on"]},{live["p"][0]},{previous["p"][0]})')
    ga1=b.put('OWN_GA1',f'IF({live["on"]},{live["a"][1]},{previous["a"][1]})')
    ga2=b.put('OWN_GA2',f'IF({live["on"]},{live["a"][0]},{previous["a"][0]})')
    groups.append(group(b,'GW','G','GN',ga1,ga2,gp1,gp2,f'({live["on"]} OR {previous["on"]}) AND SHOWGRAND','孙',
                        'COLORLIGREEN',1.1))
    # Earlier completed impulses of the selected main structure need
    # their own child details, even while a later main leg is running.
    for index,(older,newer)in enumerate(((3,2),(5,4),(7,6)),1):
        key=f'E{index}'
        start=b.put(key+'OWNERSTART',f'IF({main["on"]},{main["a"][older]},IF(BTYPE=WD,BA{older}+BN,BA{older-1}+BN))')
        end=b.put(key+'OWNEREND',f'IF({main["on"]},{main["a"][newer]},IF(BTYPE=WD,BA{newer}+BN,BA{newer-1}+BN))')
        first_price=b.put(key+'OWNERFIRST',f'IF({main["on"]},{main["p"][older]},IF(BTYPE=WD,BP{older},BP{older-1}))')
        last_price=b.put(key+'OWNERLAST',f'IF({main["on"]},{main["p"][newer]},IF(BTYPE=WD,BP{newer},BP{newer-1}))')
        gate=f'SHOWSUB AND '+('SHOWGRAND AND 'if index==3 else'')+f'WD<>0 AND IF({main["on"]},{main["length"]}>={older},IF(BTYPE=WD,BK{older},BK{older-1})) AND WD*({last_price}-{first_price})>BEPS'
        groups.append(group(b,key,'G','GN',start,end,first_price,last_price,gate,f'早{index}子','COLORLIBLUE',0.6))
    # SW/PW are retained as mathematical degree/H1 evidence only. The
    # visible blue structure is now one continuous P owner, never two
    # disjoint latest-eight-turn fragments. This also removes S entirely.
    b.lines=[line for line in b.lines if not any('{ OWNER:'+key+' }' in line for key in ('SW','PW','GW','E3'))]
    b.owner='corrections';correction_drawing(b,correction)
    from book_lines import draw as line_tools
    b.owner='tools';line_tools(b,groups[0])
    from book_prices import draw as price_plan
    b.owner='prices';plan=price_plan(b,groups[:4])
    from p_complete import draw as complete_p
    complete_p(b,plan)
    # Optional current correction only. It starts at this P's actual end,
    # and cannot substitute a disconnected S fragment for the full P.
    group(b,'SC','G','GN','PCSTOP','0','PCLAST','C',
          'PCON AND SHOWGRAND AND (PRPHASE=2 OR PRPHASE=3)',
          '回调','COLORCYAN',0.6,correction=correction)
    from candle_patterns import turning_labels
    b.owner='reversals';turning_labels(b)
    b.owner='prices'
    b.draw(f'价格尺方向:{plan["direction"]},NODRAW;')
    b.text(f'SHOWBOOK AND {plan["source"]}>0 AND IF({groups[0]["on"]},0,1) AND ({plan["target_on"]} OR {plan["fib_on"]})',
           '0','C+PAD*5','子级价格尺·候选','COLORWHITE',2)
    return b.lines,groups
