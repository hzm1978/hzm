"""Third-edition reversal shapes, with size qualification kept separate."""

PATTERNS={1:'鬼门关',2:'十字星确认',3:'急停加速',4:'补缺影线',5:'魂断蓝桥',
          6:'启明/黄昏星',7:'海底捞月',8:'三影线',9:'抱线',10:'锤子线',
          11:'方向十字',12:'跳空虚拟K',13:'阳阴阳/阴阳阴'}


def pattern_text(code,bull):
    return {6:('启明星','黄昏星'),9:('阳抱阴','阴抱阳'),10:('锤子','倒锤子'),
            11:('看涨十字','看跌十字'),13:('阳阴阳','阴阳阴')}.get(code,(PATTERNS[code],PATTERNS[code]))[0 if bull else 1]


def formulas():
    out=['{ 第三版反转形态；几何观察、标准大小、关键位置分别保存 }',
         'KEPS:=MAX(KRANGE*0.03,ABS(C)*0.0000000001);',
         'BULLSTD2:=REF(SKOK,1) AND C-LLV(L,2)>REF(SK,1);',
         'BEARSTD2:=REF(SKOK,1) AND HHV(H,2)-C>REF(SK,1);',
         'BULLSTD3:=REF(SKOK,2) AND C-LLV(L,3)>REF(SK,2);',
         'BEARSTD3:=REF(SKOK,2) AND HHV(H,3)-C>REF(SK,2);',
         'BCOVER:=BC>1 AND C>O AND REF(KUWICK,1)>REF(KEPS,1) AND O<=REF(MAX(O,C),1)+KEPS AND C>=REF(H,1);',
         'SCOVER:=BC>1 AND C<O AND REF(KLWICK,1)>REF(KEPS,1) AND O>=REF(MIN(O,C),1)-KEPS AND C<=REF(L,1);',
         'BBRIDGE:=BC>1 AND REF(C,1)>REF(O,1) AND C>O AND REF(H-C,1)<=REF(KEPS,1) AND O-L<=KEPS AND O>=REF(C,1);',
         'SBRIDGE:=BC>1 AND REF(C,1)<REF(O,1) AND C<O AND REF(C-L,1)<=REF(KEPS,1) AND H-O<=KEPS AND O<=REF(C,1);',
         'BDCONF:=BC>1 AND REF(DOJI,1) AND BULLSTD AND C>REF(H,1);',
         'SDCONF:=BC>1 AND REF(DOJI,1) AND BEARSTD AND C<REF(L,1);',
         'BGAP:=BC>1 AND SKOK AND O-REF(C,1)>SK AND C>REF(C,1);',
         'SGAP:=BC>1 AND SKOK AND REF(C,1)-O>SK AND C<REF(C,1);',
         'BTHREE:=BC>2 AND LLV(KLWICK-MAX(KBODY,KRANGE*0.05)*1.618,3)>0;',
         'STHREE:=BC>2 AND LLV(KUWICK-MAX(KBODY,KRANGE*0.05)*1.618,3)>0;',
         'BYNY:=BC>2 AND C>O AND REF(C,2)>REF(O,2) AND REF(C,1)<REF(O,1) AND C>=REF(C,2) AND REF(KBODY,1)<=REF(KBODY,2)*1.208;',
         'SYNY:=BC>2 AND C<O AND REF(C,2)<REF(O,2) AND REF(C,1)>REF(O,1) AND C<=REF(C,2) AND REF(KBODY,1)<=REF(KBODY,2)*1.208;']
    for direction,tag in ((1,'B'),(-1,'S')):
        std='BULLSTD'if direction>0 else'BEARSTD'
        opposite='BEARSTD'if direction>0 else'BULLSTD'
        out.append(f'{tag}HALT:=BC>2 AND {std} AND REF({std},2) AND REF(KBODY,1)<=REF(KBODY,2)*0.382 AND {direction}*(C-REF(C,2))>0;')
        ghosts=[];reclaims=[]
        for gap in range(2,6):
            name=f'{tag}GHOST{gap}'
            out.append(f'{name}:=BC>{gap} AND {std} AND REF({opposite},{gap}) AND HHV(REF(KBODY,1),{gap-1})<=REF(KBODY,{gap})*0.382 AND {direction}*(C-REF(O,{gap}))>0;')
            ghosts.append(name)
        for ago in range(3):
            name=f'{tag}RECLAIM{ago}'
            fn,price,rel=('LLV','L','<')if direction>0 else('HHV','H','>')
            level=f'REF({fn}({price},5),{ago+1})'
            out.append(f'{name}:=BC>={ago+6} AND REF({price},{ago}){rel}{level} AND {direction}*(C-{level})>0 AND {direction}*(C-O)>0;')
            reclaims.append(name)
        out += [f'{tag}GHOST:='+' OR '.join(ghosts)+';',f'{tag}RECLAIM:='+' OR '.join(reclaims)+';']
    for side in ('B','S'):
        choices=[(side+'GHOST',1),(side+'DCONF',2),(side+'HALT',3),(side+'COVER',4),
                 (side+'BRIDGE',5),('MORNING'if side=='B'else'EVENING',6),
                 (side+'RECLAIM',7),(side+'THREE',8),('BULLENG'if side=='B'else'BEARENG',9),
                 ('HAMMER'if side=='B'else'INVHAM',10),('BULLDO'if side=='B'else'BEARDO',11),
                 (side+'GAP',12),(side+'YNY',13)]
        expr='0'
        for cond,code in reversed(choices):expr=f'IF({cond},{code},{expr})'
        out.append(f'{side}PCODE:={expr};')
        # Multi-bar shapes use the merged distance from the FIRST reference
        # window. A large current candle alone is not the multi-bar standard.
        std1='BULLSTD'if side=='B'else'BEARSTD'
        std2='BULLSTD2'if side=='B'else'BEARSTD2'
        std3='BULLSTD3'if side=='B'else'BEARSTD3'
        out.append(f'{side}PQUAL:=IF({side}PCODE=1 OR {side}PCODE=2 OR {side}PCODE=3,{std1},IF({side}PCODE>=4 AND {side}PCODE<=5,{std2},IF({side}PCODE=6 OR {side}PCODE=8 OR {side}PCODE=13,{std3},{std1})));')
    return out


def turning_labels(builder):
    b=builder
    scope=b.frozen('TPATSCOPE','MIN(MAX(WORKAGE,PCSTART),BOOKBARS)')
    recent=b.frozen('TPATRECENT','SA1+PN+3')
    for bull in (True,False):
        code='BPCODE'if bull else'SPCODE';age='GLT'if bull else'GHT'
        valid='GLS'if bull else'GHS';kind=-1 if bull else 1
        # Historical trend legs keep their observed reversal patterns.
        # The current correction is quieter: only its latest turns remain.
        # Draw at pattern completion, never on an earlier pivot candle.
        key='VKBUY'if bull else'VKSELL'
        near=f'{valid} AND GTYPE={kind} AND {age}<=3'
        gate=b.put('TPATBUY'if bull else'TPATSELL',f'SHOWK AND VX<={scope} AND (({key} AND {near}) OR FILTER((PARENTPHASE=1 OR VX<={recent} OR (PCON AND VX<=PCSTART AND VX>=MAX(0,PCSTOP-3))) AND {near} AND {code}>0,3))')
        for number,text in PATTERNS.items():
            price='L-PAD*2'if bull else'H+PAD*2'
            b.draw(f"KTEXT({gate} AND {code}={number},0,{price},2,COLORWHITE,'转折观察·{text}'),FONTSIZE9;")
