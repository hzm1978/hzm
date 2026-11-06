"""Whole owned P trace, independent of whether an H1/count was established.

Walk forward from the owner's actual start. An adjustment keeps its entire
counter excursion until the original trend resumes; its first local pivot is
not automatically its end. The ruler and P share the very same owner anchors.
All searches are retrospective, bounded and evaluated at the current prefix.
"""

MAX_Q = 5


def draw(b, plan):
    b.owner = 'P_complete'
    p = lambda s, e: b.put('PC' + s, e)
    f = lambda s, e: b.frozen('PC' + s, e)
    # In a correction/range the authoritative scope is the existing ruler.
    # During an impulse use the current main owner and its actual extreme.
    # If the parent/count is not established, the last observed B swing is
    # still drawable. Absence of H1 only withholds price projections.
    fib = p('USEFIB', f'{plan["phase"]}=2 OR {plan["phase"]}=3')
    active = p('ACTIVE', f'IF({fib},0,{plan["phase"]}=1 AND (MWON OR WORKVALID))')
    start = f('START', f'IF({fib},{plan["fib_age"]},IF({active},IF(MWON,OWN_CURRENTAGE,IF(BTYPE=WD,BA1+BN,BA0+BN)),BA1+BN))')
    first = f('FIRST', f'IF({fib},{plan["fib_start"]},IF({active},IF(MWON,OWN_CURRENTPRICE,IF(BTYPE=WD,BP1,BP0)),BP1))')
    raw_end = f('ENDRAW', f'IF({fib},{plan["fib_end_age"]},IF({active},0,BA0+BN))')
    direction = f('DIR', f'IF({fib},SIGN({plan["fib_end"]}-{first}),IF({active},WD,SIGN(BP0-{first})))')
    end_mask = p('ENDMASK', f'VX<{start} AND VX>={raw_end}')
    tip = b.extreme('PCTIPZ', end_mask, f'{direction}*IF({direction}=1,H,L)', True)
    last = f('LAST', f'IF({fib},{plan["fib_end"]},IF({active},{direction}*{tip},BP0))')
    found_end, has_end = b.first('PCENDSEARCH', f'VX<={start} AND VX>={raw_end} AND ABS(IF({direction}=1,H,L)-{last})<=BEPS')
    end = f('STOP', f'IF({fib} AND MANF=0,{raw_end},IF({has_end},{found_end},{raw_end}))')
    gate = f('ON', f'SHOWBOOK AND SHOWSUB AND {direction}<>0 AND {start}>{end} AND {start}<DATACOUNT AND {direction}*({last}-{first})>BEPS AND IF({fib},PRFVALID,BK1 OR {active}) AND IF({fib} AND MANF=1,{has_end},1)')
    # Never relabel a clipped tail as a complete wave.
    clipped = p('CLIPPED', f'{start}>BOOKBARS')
    cursor = p('CURSOR0', f'MIN({start},BOOKBARS)')
    prev_price = first
    prior_root = first
    ordinal = p('ORD0', '0')
    vertices = []
    qs = []
    for i in range(MAX_Q):
        tag = f'PCQ{i}'
        put = lambda s, e: b.put(tag + s, e)
        frozen = lambda s, e: b.frozen(tag + s, e)
        # Start from the earliest trend-side turn, rather than the latest
        # eight pivots. The confirmation candle itself is not the root.
        event = put('EVENT', f'IF({direction}=1,GHC,GLC) AND VX+GN<{cursor} AND VX+GN>{end} AND {direction}*(REF(IF({direction}=1,H,L),GN)-{prior_root})>BEPS')
        raw_root, has_root = b.first(tag + 'ROOTRAW', event)
        root_age = put('ROOTAGE', f'{raw_root}+GN')
        root = frozen('ROOT', f'REF(IF({direction}=1,H,L),MIN(DATACOUNT-1,{root_age}))')
        # The first accepted Q needs a close break. Later Qs may use wicks.
        crossing = put('BREAKMASK', f'VX<{root_age} AND VX>={end} AND {direction}*(IF({ordinal}=0,C,IF({direction}=1,H,L))-{root})>BEPS')
        break_age, has_break = b.first(tag + 'BREAK', crossing)
        boundary = put('BOUNDARY', f'IF({has_break},{break_age},{end})')
        # The whole correction, excluding the root/break candles, determines
        # the counter end. Internal a/b turns cannot replace the later c/e.
        band = put('BAND', f'VX<{root_age} AND VX>{boundary}')
        counter_z = b.extreme(tag + 'COUNTERZ', band, f'{direction}*IF({direction}=1,L,H)')
        extreme_counter = put('EXTCOUNTER', f'{direction}*{counter_z}')
        extreme_age, has_counter = b.first(tag + 'EXTAGE', f'{band} AND ABS(IF({direction}=1,L,H)-{extreme_counter})<=BEPS')
        # Inspect the last counter-side snapshot BEFORE continuation. A
        # matching c/e is the adjustment end even if a was more extreme.
        snap = frozen('SNAP', f'MIN(DATACOUNT-1,{boundary}+IF(REF(GTYPE,{boundary})=(0-{direction}),0,REF(GA1,{boundary})))')
        match_end = f'REF(GTYPE,{snap})=(0-{direction}) AND REF(GA0,{snap})+{snap}+GN<{root_age} AND REF(GA0,{snap})+{snap}+GN>{boundary}'
        def match(size):
            return f'REF(GK{size},{snap}) AND ABS(REF(GP{size},{snap})-{root})<=BEPS AND ABS(REF(GA{size},{snap})+{snap}+GN-{root_age})<=GN'
        code = frozen('SHAPE', f'IF({match_end},IF({match(5)},REF(GAC5,{snap}),IF({match(3)},REF(GAC3,{snap}),IF({match(1)},1,0))),0)')
        counter = frozen('COUNTER', f'IF({code}>0,REF(GP0,{snap}),{extreme_counter})')
        counter_age = frozen('COUNTERAGE', f'IF({code}>0,REF(GA0,{snap})+{snap}+GN,{extreme_age})')
        n = put('N', f'{root_age}-{boundary}')
        valid = put('VALID', f'{gate} AND {has_root} AND {has_counter} AND {n}>=2 AND {direction}*({root}-{prev_price})>BEPS AND {direction}*({root}-{counter})>BEPS')
        complete = put('COMPLETE', f'{valid} AND {has_break}')
        index = put('INDEX', f'{ordinal}+IF({valid},1,0)')
        ret = put('RET', f'ABS({counter}-{root})/MAX(ABS({root}-{prev_price}),0.0000001)*100')
        root_no = put('ROOTNO', f'{ordinal}*2+1')
        counter_no = put('COUNTERNO', f'{ordinal}*2+2')
        mid = put('MID', f'INTPART(({root_age}+{boundary})/2)')
        y = f'{root}+{direction}*BPAD*0.8'
        b.segment(valid, root_age, root, boundary, root, 'COLORLIBLUE')
        b.text(valid, mid, y, 'P Q', 'COLORLIBLUE', 2)
        b.number(valid, mid, y, n, 'COLORLIBLUE', 0)
        b.text(f'{valid} AND IF({has_break},0,1)', boundary, y, 'OPEN >=', 'COLORLIBLUE', 2)
        b.number(valid, root_age, f'{root}+{direction}*BPAD*0.5', root_no, 'COLORLIBLUE', 0, 11)
        b.number(valid, root_age, f'{root}+{direction}*BPAD*1.7', root, 'COLORLIBLUE', 2)
        b.number(valid, counter_age, f'{counter}-{direction}*BPAD*0.6', counter_no, 'COLORLIBLUE', 0, 11)
        b.text(valid, counter_age, f'{counter}-{direction}*BPAD*1.8', 'RET%', 'COLORLIBLUE', 2)
        b.number(valid, counter_age, f'{counter}-{direction}*BPAD*1.8', ret, 'COLORLIBLUE')
        b.text(f'{valid} AND SHOWC', counter_age, f'{counter}-{direction}*BPAD*3', 'P AD', 'COLORMAGENTA', 2)
        b.number(f'{valid} AND SHOWC', counter_age, f'{counter}-{direction}*BPAD*3', code, 'COLORMAGENTA', 0)
        vertices += [(valid, root_age, root), (valid, counter_age, counter)]
        qs.append(dict(valid=valid, complete=complete, root=root, root_age=root_age,
                       counter=counter, counter_age=counter_age, break_age=boundary,
                       length=n, retracement=ret, shape=code, ordinal=index,snapshot=snap))
        # Every attempted root advances. A one-bar inner Q is consumed but
        # does not become a new numbered main-level adjustment.
        cursor = put('NEXT', f'IF({has_root} AND {has_break},{boundary},{end})')
        prior_root = put('PRIORROOT', f'IF({has_root},{root},{prior_root})')
        prev_price = put('PREVPRICE', f'IF({valid},{counter},{prev_price})')
        ordinal = index
    # A single connected polyline includes both exact owner endpoints.
    # It therefore remains visible even if no Q/count/H1 can be established.
    # If the detail bound is exceeded, continue the full observed outline
    # with confirmed PN turns and label the limit instead of hiding a tail.
    tail = p('TAIL', f'VX>={end} AND VX<{cursor} AND (VSH OR VSL)')
    mask = p('MASK', f'VX={start} OR VX={end} OR {tail} OR ' + ' OR '.join(f'({ok} AND (VX={a}))' for ok,a,_ in vertices))
    price = p('PRICE', f'IF(VX={start},{first},IF(VX={end},{last},IF({tail},IF(VSH,H,L),' + '+'.join(f'IF({ok} AND VX={a},{v},0)' for ok,a,v in vertices) + ')))')
    b.draw(f'POLYLINE({gate} AND {mask},{price},COLORLIBLUE),LINETHICK2;')
    gh=p('VHIGH','FILTER(BACKSET(GHC,GN+1),GN)')
    gl=p('VLOW','FILTER(BACKSET(GLC,GN+1),GN)')
    for q in qs:
        b.draw(f'POLYLINE(SHOWC AND {q["valid"]} AND {q["shape"]}>1 AND VX<={q["root_age"]} AND VX>={q["counter_age"]} AND ({gh} OR {gl}),IF({gh},H,L),COLORMAGENTA),LINETHICK1;')
    b.text(gate, start, f'{first}-{direction}*BPAD', 'P START', 'COLORLIBLUE', 2)
    b.number(gate, start, f'{first}-{direction}*BPAD', first, 'COLORLIBLUE', 2)
    b.text(gate, f'INTPART(({start}+{end})/2)', f'{first}-{direction}*BPAD*2.2', 'P LEN', 'COLORLIBLUE', 2)
    b.number(gate, f'INTPART(({start}+{end})/2)', f'{first}-{direction}*BPAD*2.2', f'{start}-{end}', 'COLORLIBLUE', 0)
    b.text(f'{gate} AND IF({active},0,1)', end, f'{last}+{direction}*BPAD*2.8', 'P END', 'COLORLIBLUE', 2)
    b.text(f'{gate} AND {active}', end, f'{last}+{direction}*BPAD*2.8', 'P TIP?', 'COLORLIBLUE', 2)
    b.number(gate, end, f'{last}+{direction}*BPAD*2.8', last, 'COLORLIBLUE', 2)
    b.number(gate, end, f'{last}+{direction}*BPAD*0.6', f'{ordinal}*2+1', 'COLORLIBLUE', 0, 11)
    overflow=f('OVERFLOW',f'COUNT(IF({direction}=1,GHC,GLC) AND VX+GN<{cursor} AND VX+GN>{end},BC)>0')
    b.text(f'{gate} AND ({clipped} OR {overflow})', end, f'{last}-{direction}*BPAD*5', 'P DETAIL LIMIT', 'COLORWHITE', 2)
    return dict(on=gate, start=start, end=end, first=first, last=last,
                direction=direction, active=active, qs=qs, count=ordinal,
                detail_limit=overflow)
