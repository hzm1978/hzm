"""Generate editable formula text for the THS Futures PC formula editor.

No client-specific binary indicator file is fabricated. Analysis and retrospective
drawing are separate sections; the checker enforces that boundary.
"""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent


def standard_k():
    lines = ["{ 标准K：只用此前40根，实体排序前5，实体相等时较近者优先 }",
             "BC:=BARPOS;", "BODY:=ABS(C-O);", "RANGE:=H-L;",
             "UPPER:=H-MAX(O,C);", "LOWER:=MIN(O,C)-L;",
             "KDIST:=IF(C>O,C-L,IF(C<O,H-C,0));",
             "TRANGE:=MAX1(H-L,ABS(H-REF(C,1)),ABS(L-REF(C,1)));",
             "ATRRAW:=MA(TRANGE,14);", "ATRVAL:=IF(ISNULL(ATRRAW),0,ATRRAW);"]
    for i in range(1, 41):
        # Compute per-candle values before looking back: identical historical
        # samples, fewer native REF calls and no duplicated selection masks.
        lines.append(f"E{i:02}:=REF(BODY,{i});")
    # The exported catalog explicitly limits SORT/SORTPOS to 30 data args.
    # The global top five must belong to a group's top five. Merge two sets
    # of 20, then take five out of their ten candidates; ties remain nearest.
    candidates=[]
    for group in range(2):
        values=','.join(f'E{i:02}' for i in range(group*20+1,group*20+21))
        for rank in range(1,6):
            key=f'KS{group}{rank}'
            lines += [f'{key}:=SORT(1,{rank},{values});',
                      f'KA{group}{rank}:=SORTPOS(1,{rank},{values})+{group*20};']
            candidates.append((key,f'KA{group}{rank}'))
    values=','.join(key for key,age in candidates)
    for rank in range(1,6):
        lines.append(f'KSEL{rank}:=SORTPOS(1,{rank},{values});')
        lines.append(f'KAGE{rank}:='+ '+'.join(f'IF(KSEL{rank}={i},1,0)*{age}'
                      for i,(key,age) in enumerate(candidates,1))+';')
    lines.append('SK:=IF(BC>40,('+ '+'.join(f'REF(KDIST,KAGE{rank})' for rank in range(1,6))+')/5,0);')
    lines.extend(["SKOK:=BC>40 AND SK>0;", "PAD:=MAX((HHV(H,160)-LLV(L,160))*0.025,SK*0.25);"])
    return lines


def pivots(prefix, n, depth=6, first_equal=False):
    """Confirmed fractals; retain record extremes within same-kind runs.

    A0..An are ages of confirmation bars, not the earlier extreme bars.
    Actual extreme age = Ai + n. Every dynamic REF is clamped separately.
    """
    p = prefix
    # Fine Q seeds retain the FIRST equal extreme. Requiring the following
    # candle to have a strictly higher low/lower high loses a two-bar Q when
    # a reversal opens at the previous close and shares its extreme.
    high_turn=f'(REF(H,{n})>HHV(H,{n}) OR REF(H,{n})>REF(H,{n}+1))'if first_equal else f'REF(H,{n})>HHV(H,{n})'
    low_turn=f'(REF(L,{n})<LLV(L,{n}) OR REF(L,{n})<REF(L,{n}+1))'if first_equal else f'REF(L,{n})<LLV(L,{n})'
    # The opposite extreme's own candle can have a long wick above/below
    # the entire subsequent bounce. It is not part of that bounce. Keeping
    # it in this record window suppresses real inner turns and their Qs.
    record_extra=''if first_equal else'+1'
    lines = [f"{{ {p}级：右侧{n}根确认；局部转折不是书中浪号 }}",
             f"{p}RH:=BC>2*{n} AND REF(H,{n})=HHV(H,2*{n}+1) AND {high_turn};",
             f"{p}RL:=BC>2*{n} AND REF(L,{n})=LLV(L,2*{n}+1) AND {low_turn};",
             f"{p}HA:={p}RH AND IF({p}RL,0,1);",
             f"{p}LA:={p}RL AND IF({p}RH,0,1);",
             f"{p}HC:={p}HA AND REF(H,{n})>=HHV(REF(H,{n}),MIN(BC,MAX(1,IF(SUM({p}LA,0)>0,BARSLAST({p}LA){record_extra},BC))));",
             f"{p}LC:={p}LA AND REF(L,{n})<=LLV(REF(L,{n}),MIN(BC,MAX(1,IF(SUM({p}HA,0)>0,BARSLAST({p}HA){record_extra},BC))));",
             f"{p}HT:=BARSLAST({p}HC);", f"{p}LT:=BARSLAST({p}LC);",
             f"{p}HS:=SUM({p}HC,0)>0;", f"{p}LS:=SUM({p}LC,0)>0;",
             f"{p}NOLOW:=IF({p}LS=0,1,0);",
             f"{p}NEWH:=IF({p}HT<{p}LT,1,0);",
             f"{p}TYPE:=2*{p}HS*MAX({p}NOLOW,{p}NEWH)-1;",
             f"{p}A0:=MIN(BC-1,IF({p}TYPE=1,{p}HT,{p}LT));",
             f"{p}K0:=({p}HS OR {p}LS) AND REF(IF({p}TYPE=1,{p}HC,{p}LC),{p}A0) AND {p}A0+{n}<BC;"]
    # Do not REF(IF(TYPE,...),age): TYPE must be its current value, rather
    # than the value TYPE happened to have on the historical bar.
    lines[-1] = (f"{p}K0:=({p}HS OR {p}LS) AND "
                 f"IF({p}TYPE=1,REF({p}HC,{p}A0),REF({p}LC,{p}A0)) AND {p}A0+{n}<BC;")
    for i in range(depth):
        if i:
            # At an opposite-kind confirmation, BARSLAST(sought event) > 0.
            h = f"REF({p}HT,{p}A{i-1})"
            l = f"REF({p}LT,{p}A{i-1})"
            delta = f"IF({p}TYPE=1,{l if i % 2 else h},{h if i % 2 else l})"
            lines.append(f"{p}A{i}:=MIN(BC-1,{p}A{i-1}+{delta});")
            evh, evl = (f"{p}LC", f"{p}HC") if i % 2 else (f"{p}HC", f"{p}LC")
            lines.append(f"{p}K{i}:={p}K{i-1} AND {p}A{i}>{p}A{i-1} AND {p}A{i}+{n}<BC AND IF({p}TYPE=1,REF({evh},{p}A{i}),REF({evl},{p}A{i}));")
        high, low = ("L", "H") if i % 2 else ("H", "L")
        lines.append(f"{p}P{i}:=IF({p}TYPE=1,REF({high},MIN(BC-1,{p}A{i}+{n})),REF({low},MIN(BC-1,{p}A{i}+{n})));")
    return lines


def q_intervals(prefix, n):
    p = prefix
    return [f"{{ {p}区间：确认极值后首次收盘越过；仅为收盘Q子集，不代替完整分级 }}",
            f"{p}UH:=REF(H,MIN(BC-1,{p}HT+{n}));",
            f"{p}DL:=REF(L,MIN(BC-1,{p}LT+{n}));",
            f"{p}UB:={p}HS AND {p}LS AND {p}LT<{p}HT AND C>{p}UH;",
            f"{p}DB:={p}HS AND {p}LS AND {p}HT<{p}LT AND C<{p}DL;",
            f"{p}UQ:={p}UB AND COUNT({p}UB,MIN(BC,{p}HT+1))=1;",
            f"{p}DQ:={p}DB AND COUNT({p}DB,MIN(BC,{p}LT+1))=1;",
            f"{p}UN:={p}HT+{n};", f"{p}DN:={p}LT+{n};",
            f"{p}UE:=BARSLAST({p}UQ);", f"{p}DE:=BARSLAST({p}DQ);",
            f"{p}UV:=REF({p}UN,MIN(BC-1,{p}UE));", f"{p}DV:=REF({p}DN,MIN(BC-1,{p}DE));",
            f"{p}UA:=MIN(BC-1,{p}UE+1+REF({p}UE,MIN(BC-1,{p}UE+1)));",
            f"{p}DA:=MIN(BC-1,{p}DE+1+REF({p}DE,MIN(BC-1,{p}DE+1)));",
            f"{p}UP:=REF({p}UN,{p}UA);", f"{p}DP:=REF({p}DN,{p}DA);",
            f"{p}UOK:=SUM({p}UQ,0)>=2 AND {p}UA>{p}UE AND REF({p}UQ,{p}UA);",
            f"{p}DOK:=SUM({p}DQ,0)>=2 AND {p}DA>{p}DE AND REF({p}DQ,{p}DA);"]


def core():
    lines = ["{ 趋势交易·期货通PC 1.2.1；按用户导出函数库生成，结构解释需结合确认时刻 }",
             "{ PN细/BN工作转折；DIR=0自动，1上升，-1下降 }",
             "{ KIND=0自动，1顺势，2调整，3横盘；参数详见README }",
             "{ MANF=1人工前浪：MFROM起价、MTO止价、MFAGE起点距今 }",
             "{ MH1>0人工H1：MBASE调整末价、MORG浪1起价、MAGE距今 }",
             "{ STRONG=1允许强势候选；SPAN为价格线最大显示根数 }",
             "PN:=2;", "BN:=7;", "GN:=1;", "DIR:=0;", "KIND:=0;",
             "MANF:=0;", "MFROM:=0;", "MTO:=0;", "MFAGE:=90;",
             "MH1:=0;", "MBASE:=0;", "MORG:=0;", "MAGE:=0;",
             "STRONG:=0;", "SPAN:=90;", "SHOWQ:=1;", "SHOWK:=1;",
             "SHOWF:=1;", "SHOWT:=1;", "SHOWC:=1;", "DETAIL:=0;",
             "SHOWBOOK:=1;", "SHOWSUB:=1;", "SHOWGRAND:=1;", "BOOKBARS:=600;"]
    lines += standard_k()
    lines += pivots("S", "PN",8) + pivots("B", "BN",12) + pivots("G","GN",8,first_equal=True)
    from book_corrections import adjustment_seed_formulas
    seeds=adjustment_seed_formulas()
    lines += seeds+[re.sub(r'\bG(?=[A-Z0-9])','S',line)for line in seeds]
    lines += q_intervals("S", "PN") + q_intervals("B", "BN")
    lines += ["{ 工作方向观察：同侧双点或先前已知工作极值收盘突破；旧方向破位后不强行沿用 }",
              "BH0:=IF(BTYPE=1,BP0,BP1);", "BH1:=IF(BTYPE=1,BP2,BP3);",
              "BL0:=IF(BTYPE=-1,BP0,BP1);", "BL1:=IF(BTYPE=-1,BP2,BP3);",
              "DS:=BK3 AND ((BH0>BH1 AND BL0>BL1) OR (BH0<BH1 AND BL0<BL1));",
              "DSV:=IF(BH0>BH1,1,-1);",
              "DSCHANGE:=DS AND (REF(DS,1)=0 OR DSV<>REF(DSV,1));",
              "BREAKUP:=REF(BK1,1) AND C>REF(BH0,1) AND REF(C,1)<=REF(BH0,1);",
              "BREAKDN:=REF(BK1,1) AND C<REF(BL0,1) AND REF(C,1)>=REF(BL0,1);",
              "DEVENT:=DSCHANGE OR BREAKUP OR BREAKDN;",
              "DVALUE:=IF(BREAKUP,1,IF(BREAKDN,-1,DSV));",
              "DSA:=MIN(BC-1,BARSLAST(DEVENT));",
              "WD:=IF(DIR=1,1,IF(DIR=-1,-1,IF(SUM(DEVENT,0)>0,REF(DVALUE,DSA),0)));",
              "LEG:=IF(STYPE=1,IF(C>SP0,1,-1),IF(C<SP0,-1,1));",
              "PARENTLEG:=IF(BTYPE=1,IF(C>BP0,1,-1),IF(C<BP0,-1,1));",
              "SIDE:=BK3 AND MAX(BH0,BH1)-MIN(BL0,BL1)>0 AND ABS(BH0-BH1)+ABS(BL0-BL1)<(MAX(BH0,BH1)-MIN(BL0,BL1))*0.25;",
              "PHASE:=IF(KIND>0,KIND,IF(SIDE,3,IF(WD=0,0,IF(LEG=WD,1,2))));",
              "{ 工作方向最近一次切换时锁定起端；其后小回调不缩短工作段 }",
              "WCHANGE:=WD<>0 AND (WD<>REF(WD,1) OR (BK3 AND REF(BK3,1)=0));",
              "WAGE:=MIN(BC-1,BARSLAST(WCHANGE));",
              "HAGE0:=IF(BTYPE=1,BA0,BA1)+BN;", "HAGE1:=IF(BTYPE=1,BA2,BA3)+BN;",
              "LAGE0:=IF(BTYPE=-1,BA0,BA1)+BN;", "LAGE1:=IF(BTYPE=-1,BA2,BA3)+BN;",
              "ORIGIN:=IF(WD=1,MIN(BL0,BL1),MAX(BH0,BH1));",
              "ORIGAGE:=IF(WD=1,IF(BL0<=BL1,LAGE0,LAGE1),IF(BH0>=BH1,HAGE0,HAGE1));",
              "WORKSTART:=REF(ORIGIN,WAGE);",
              "WORKAGE:=MIN(BC-1,WAGE+REF(ORIGAGE,WAGE));",
              "WORKEND:=IF(WD=1,HHV(H,WAGE+1),LLV(L,WAGE+1));",
              "WORKVALID:=SUM(WCHANGE,0)>0 AND REF(BK3,WAGE) AND WORKAGE>WAGE;",
              "WORKTIPRAW:=IF(WD=1,HHVBARS(H,WAGE+1),LLVBARS(L,WAGE+1));",
              "WORKTIPAGE:=IF(ISNULL(WORKTIPRAW),0,WORKTIPRAW);",
              "BOOKTIPTURN:=WORKVALID AND WD<>0 AND WORKTIPAGE>=PN AND IF(WD=1,HHV(H,PN)<WORKEND,LLV(L,PN)>WORKEND);",
              "PARENTPHASE:=IF(KIND>0,KIND,IF(SIDE,3,IF(WD=0,0,IF(BOOKTIPTURN,2,IF(PARENTLEG=WD,1,2)))));",
              "{ 逆势或横盘时：工作段末端0%，锁定起端100%，不以内部小腿替换 }",
              "ALIGN:=BTYPE=WD;",
              "FEND:=IF(MANF=1,MTO,IF(PHASE=3,BP0,WORKEND));",
              "FSTART:=IF(MANF=1,MFROM,IF(PHASE=3,BP1,WORKSTART));",
              "FAGE:=MIN(BC-1,MAX(1,IF(MANF=1,MFAGE,IF(PHASE=3,BA1+BN,WORKAGE))));",
              "FH:=ABS(FSTART-FEND);",
              "FDIR:=IF(PHASE=3,IF(FEND>FSTART,1,-1),WD);",
              "FVALID:=(WD<>0 OR PHASE=3) AND (IF(MANF=1,MFAGE>=1 AND MFAGE<BC,IF(PHASE=3,BK1,WORKVALID))) AND FDIR*(FEND-FSTART)>0 AND FH>0;",
              "F0:=FEND;", "F236:=FEND+(FSTART-FEND)*0.236;",
              "F382:=FEND+(FSTART-FEND)*0.382;", "F500:=FEND+(FSTART-FEND)*0.5;",
              "F618:=FEND+(FSTART-FEND)*0.618;", "F786:=FEND+(FSTART-FEND)*0.786;", "F100:=FSTART;",
              "FON:=SHOWF AND FVALID AND (PHASE=2 OR PHASE=3);",
              "RET:=IF(FVALID,FDIR*(FEND-C)/MAX(FH,0.0000001)*100,0);",
              "{ 细级最近完成腿的回调；只称细腿，不编造书中子1至子5浪号 }",
              "SH:=ABS(SP0-SP1);", "SRET:=IF(SK1 AND SH>0,ABS(C-SP0)/MAX(SH,0.0000001)*100,0);",
              "{ Q比较：小Q按dif，其余按比值；共享边界同级优先 }",
              "QNEW:=IF(WD=1,BUV,BDV);", "QOLD:=IF(WD=1,BUP,BDP);",
              "QOK:=IF(WD=1,BUOK,BDOK) AND WD<>0;",
              "QDIF:=QNEW-QOLD;", "QDEL:=QNEW/MAX(QOLD,1);",
              "QSM:=QNEW<=10 AND QOLD<=10;",
              "QREL:=IF(QSM,IF(ABS(QDIF)<=2,0,IF(QDIF>5,2,IF(QDIF>=3,1,IF(QDIF<0-5,0-2,0-1)))),IF(QDEL>=0.828 AND QDEL<=1.208,0,IF(QDEL<0.382,0-2,IF(QDEL<0.828,0-1,IF(QDEL<=2.618,1,2)))));",
              "{ H1只是候选：局部五转折不等于书中完成五浪；可输入人工核定的同级H1 }",
              "HQAGE:=IF(WD=1,BUE,BDE);", "HQCOUNT:=IF(WD=1,SUM(BUQ,0),SUM(BDQ,0));",
              "HQA:=IF(ALIGN,BA3,BA2)+BN;", "HQB:=IF(ALIGN,BA2,BA1)+BN;",
              "HQFIT:=HQCOUNT>0 AND HQAGE<HQA AND HQAGE>=HQB;",
              "HOK:=BK5 AND WD<>0 AND WORKVALID AND HQFIT AND IF(ALIGN,BA5,BA4)+BN<=WORKAGE AND IF(ALIGN,WD*(BP2-BP4)>0,WD*(BP1-BP3)>0);",
              "AUTOH:=IF(ALIGN,ABS(BP4-BP5),ABS(BP3-BP4));",
              "AUTOBASE:=IF(ALIGN,BP1,BP0);", "AUTOORG:=IF(ALIGN,BP5,BP4);",
              "AUTOAGE:=IF(ALIGN,BA1,BA0)+BN;",
              "MANUAL:=MH1>0 AND MAGE>=1;",
              "H1:=IF(MANUAL,MH1,AUTOH);", "TBASE:=IF(MANUAL,MBASE,AUTOBASE);",
              "TORG:=IF(MANUAL,MORG,AUTOORG);", "TAGE:=MIN(BC-1,IF(MANUAL,MAGE,AUTOAGE));",
              "TON:=SHOWT AND PHASE=1 AND WD<>0 AND ((MANUAL AND MAGE<BC) OR (MH1=0 AND HOK)) AND H1>0;",
              "{ 起点根内先后未知；命中统计排除起点本根 }",
              "TEXTREME:=IF(WD=1,HHV(H,MAX(1,TAGE)),LLV(L,MAX(1,TAGE)));",
              "T1:=TBASE+WD*H1*1.208;", "T2:=TBASE+WD*H1*1.618;", "T3:=TBASE+WD*H1*2;",
              "T4:=TBASE+WD*H1*2.382;", "T5:=TBASE+WD*H1*2.618;", "T6:=TBASE+WD*H1*3;",
              "T7:=TORG+WD*H1*3.236;", "T8:=TORG+WD*H1*4.236;"]
    for i in range(1, 9):
        gate = "STRONG AND " if i in (4, 5, 6) else ""
        lines.append(f"T{i}OK:=TON AND {gate}WD*(T{i}-TEXTREME)>0;")
    for i in range(1, 9):
        comparisons = [f"(T{j}OK AND (WD*T{j}<WD*T{i}" + (f" OR T{j}=T{i}" if j < i else "") + "))"
                       for j in range(1, 9) if j != i]
        lines.append(f"T{i}R:=1+" + "+".join(comparisons) + ";")
    for rank in range(1, 4):
        # Target prices are finite (clamped, observed anchors). Select with a
        # numerical 0/1 coefficient and a primitive rank comparison.
        lines.append(f"TT{rank}:=" + "+".join(f"IF(T{i}R={rank},T{i}OK,0)*T{i}" for i in range(1, 9)) + ";")
        lines.append(f"TT{rank}OK:=" + " OR ".join(f"(T{i}OK AND T{i}R={rank})" for i in range(1, 9)) + ";")
    lines += ["{ 调整仅标候选：三/五腿与几何收敛不证明书中的内部完整分级 }",
              "CSIGN:=0-WD;", "COK:=BK3 AND WD<>0 AND BTYPE=CSIGN AND (PHASE=2 OR PHASE=3);",
              "ALEG:=ABS(BP2-BP3);", "BLEG:=ABS(BP1-BP2);", "CLEG:=ABS(BP0-BP1);",
              "ABC:=COK AND ALEG>0 AND WD*(BP1-BP3)<=0 AND CSIGN*(BP0-BP2)>0;",
              "ABCS:=COK AND ALEG>0 AND WD*(BP1-BP3)<=0 AND CSIGN*(BP0-BP2)<=0;",
              "C5:=BK5 AND WD<>0 AND BTYPE=CSIGN AND (PHASE=2 OR PHASE=3);",
              "SHRINK:=C5 AND ABS(BP4-BP5)>ABS(BP3-BP4) AND ABS(BP3-BP4)>ABS(BP2-BP3) AND ABS(BP2-BP3)>ABS(BP1-BP2) AND ABS(BP1-BP2)>ABS(BP0-BP1);",
              "EXPAND:=C5 AND ABS(BP4-BP5)<ABS(BP3-BP4) AND ABS(BP3-BP4)<ABS(BP2-BP3) AND ABS(BP2-BP3)<ABS(BP1-BP2) AND ABS(BP1-BP2)<ABS(BP0-BP1);",
              "ACAND:=BK1 AND WD<>0 AND BTYPE=CSIGN AND (ABC OR ABCS OR SHRINK OR EXPAND);",
              "AV:=BP0;", "AFAIL:=ACAND AND WD*(C-AV)<0;",
              "{ 关键回调价位必须在上一根已知；形态完成根不借用后来出现的结构 }",
              "PREPOS:=REF(FVALID,1) AND (ABS(REF(RET,1)-40)<=5 OR ABS(REF(RET,1)-62)<=5 OR ABS(REF(RET,1)-78.6)<=5);",
              "NEARPOS:=PREPOS AND (WD=REF(WD,1));",
              "HAMMER:=BODY>RANGE*0.1 AND LOWER>BODY*1.618 AND LOWER>UPPER;",
              "INVHAM:=BODY>RANGE*0.1 AND UPPER>BODY*1.618 AND UPPER>LOWER;",
              "DOJI:=RANGE>0 AND BODY<=RANGE*0.1;",
              "BULLENG:=C>O AND REF(C,1)<REF(O,1) AND C>=REF(O,1) AND O<=REF(C,1);",
              "BEARENG:=C<O AND REF(C,1)>REF(O,1) AND C<=REF(O,1) AND O>=REF(C,1);",
              "MORNING:=BC>2 AND REF(C,2)<REF(O,2) AND REF(BODY,2)>=REF(RANGE,2)*0.45 AND REF(BODY,1)<=REF(BODY,2)*0.5 AND REF(MAX(O,C),1)<=(REF(O,2)+REF(C,2))/2 AND C>O AND C>REF(O,2);",
              "EVENING:=BC>2 AND REF(C,2)>REF(O,2) AND REF(BODY,2)>=REF(RANGE,2)*0.45 AND REF(BODY,1)<=REF(BODY,2)*0.5 AND REF(MIN(O,C),1)>=(REF(O,2)+REF(C,2))/2 AND C<O AND C<REF(O,2);",
              "BULLDO:=DOJI AND LOWER>UPPER*2;", "BEARDO:=DOJI AND UPPER>LOWER*2;",
              "BULLSTD:=SKOK AND C>O AND C-L>SK;", "BEARSTD:=SKOK AND C<O AND H-C>SK;",
              "BULLPAT:=BPCODE>0;", "BEARPAT:=SPCODE>0;",
              "BULLKEY:=NEARPOS AND WD=1 AND BPQUAL AND BULLPAT;",
              "BEARKEY:=NEARPOS AND WD=-1 AND SPQUAL AND BEARPAT;",
              "{ 对边两极值斜率平移参考：尚未验证全部锚间包络，不叫已确认拐点线 }",
              "HSLOPE:=(BH0-BH1)/MAX(1,HAGE1-HAGE0);",
              "LSLOPE:=(BL0-BL1)/MAX(1,LAGE1-LAGE0);",
              "TRENDV:=IF(WD=1,BL0+LSLOPE*LAGE0,BH0+HSLOPE*HAGE0);",
              "PIVOTV:=IF(WD=1,BL0+HSLOPE*LAGE0,BH0+LSLOPE*HAGE0);",
              "LINEOK:=BK3 AND WD<>0;", "{ END_ANALYSIS：以下未来引用只用于当前图形显示，绝不参与上面的计算 }"]
    # RANGE is a built-in formula function in several THS editions. Avoid
    # borrowing candlestick helper names that might collide with native names.
    renamed = {"BODY": "KBODY", "RANGE": "KRANGE", "UPPER": "KUWICK", "LOWER": "KLWICK"}
    from candle_patterns import formulas as candle_formulas
    insert=next(i for i,line in enumerate(lines)if line.startswith('BULLPAT:='))
    lines[insert:insert]=candle_formulas()
    from book_corrections import context_formulas
    lines[-1:-1]=context_formulas()
    return [re.sub(r"\b(?:BODY|RANGE|UPPER|LOWER)\b", lambda m: renamed[m.group()], s)
            for s in lines]


def legacy_drawing():
    lines = ["{ 回标转折：必须等PN/BN根后才知道；历史连线会重绘，不作为当时的交易信号 }",
             "VX:=REFX1(BC,100000)-BC;",
             "VRECENT:=DETAIL OR VX<=SPAN;",
             "VSCOPE:=DETAIL OR VX<=REFX1(SA5+PN,100000);",
             "VSH:=FILTER(BACKSET(SHC,PN+1),PN);", "VSL:=FILTER(BACKSET(SLC,PN+1),PN);",
             "VBH:=FILTER(BACKSET(BHC,BN+1),BN);", "VBL:=FILTER(BACKSET(BLC,BN+1),BN);",
             "POLYLINE(VSH OR VSL,IF(VSH,H,L),COLORCYAN),LINETHICK1;",
             "POLYLINE(VBH OR VBL,IF(VBH,H,L),COLORYELLOW),LINETHICK2;",
             "DRAWNUMBER1(VBH AND VRECENT,H+PAD,H,2),COLORYELLOW;",
             "DRAWNUMBER1(VBL AND VRECENT,L-PAD,L,2),COLORYELLOW;",
             "VQAGE:=REFX1(IF(WD=1,BUE,BDE),100000);",
             "VQN:=REFX1(IF(WD=1,BUV,BDV),100000);",
             "VQP:=REFX1(IF(WD=1,REF(BUH,BUE),REF(BDL,BDE)),100000);",
             "VQON:=REFX1(SHOWQ AND WD<>0 AND IF(WD=1,SUM(BUQ,0)>0,SUM(BDQ,0)>0),100000);",
             "POLYLINE(VQON AND (VX=VQAGE OR VX=VQAGE+VQN),VQP,COLORWHITE);",
             "DRAWTEXT(DETAIL AND SHOWQ AND SUQ,H+PAD*2,'细Q收破'),COLORWHITE;",
             "DRAWNUMBER1(SHOWQ AND SUQ AND VSCOPE AND IF(BUQ,0,1),H+PAD*2,SUN,0),COLORCYAN;",
             "DRAWTEXT(DETAIL AND SHOWQ AND SDQ,L-PAD*2,'细Q收破'),COLORWHITE;",
             "DRAWNUMBER1(SHOWQ AND SDQ AND VSCOPE AND IF(BDQ,0,1),L-PAD*2,SDN,0),COLORCYAN;",
             "DRAWTEXT(DETAIL AND SHOWQ AND BUQ,H+PAD*3,'工作Q收破'),COLORYELLOW;",
             "DRAWNUMBER1(SHOWQ AND BUQ AND VRECENT,H+PAD*2,BUN,0),COLORWHITE;",
             "DRAWTEXT(DETAIL AND SHOWQ AND BDQ,L-PAD*3,'工作Q收破'),COLORYELLOW;",
             "DRAWNUMBER1(SHOWQ AND BDQ AND VRECENT,L-PAD*2,BDN,0),COLORWHITE;",
             "VKBUY:=BULLKEY AND (DETAIL OR FILTER(BULLKEY,2));",
             "VKSELL:=BEARKEY AND (DETAIL OR FILTER(BEARKEY,2));",
             "DRAWTEXT(SHOWK AND ISLASTBAR AND HAMMER,L-PAD,'锤子·观察'),COLORWHITE;",
             "DRAWTEXT(SHOWK AND ISLASTBAR AND INVHAM,H+PAD,'倒锤子·观察'),COLORWHITE;",
             "DRAWTEXT(SHOWK AND ISLASTBAR AND BULLENG,L-PAD*2,'阳抱阴·观察'),COLORRED;",
             "DRAWTEXT(SHOWK AND ISLASTBAR AND BEARENG,H+PAD*2,'阴抱阳·观察'),COLORGREEN;",
             "DRAWTEXT(SHOWK AND ISLASTBAR AND MORNING,L-PAD*3,'启明星·观察'),COLORRED;",
             "DRAWTEXT(SHOWK AND ISLASTBAR AND EVENING,H+PAD*3,'黄昏星·观察'),COLORGREEN;",
             "DRAWTEXT(SHOWK AND ISLASTBAR AND BULLDO,L-PAD,'看涨十字·观察'),COLORWHITE;",
             "DRAWTEXT(SHOWK AND ISLASTBAR AND BEARDO,H+PAD,'看跌十字·观察'),COLORWHITE;",
             "DRAWTEXT(SHOWC AND ISLASTBAR AND ABC AND IF(SHRINK OR EXPAND,0,1),AV,'abc候选'),COLORCYAN;",
             "DRAWTEXT(SHOWC AND ISLASTBAR AND ABCS AND IF(SHRINK OR EXPAND,0,1),AV,'收缩abc候选'),COLORCYAN;",
             "DRAWTEXT(SHOWC AND ISLASTBAR AND SHRINK,AV+PAD,'收缩三角候选'),COLORCYAN;",
             "DRAWTEXT(SHOWC AND ISLASTBAR AND EXPAND,AV+PAD,'扩张三角候选'),COLORCYAN;",
             "DRAWTEXT(SHOWC AND ISLASTBAR AND ACAND AND IF(AFAIL,0,1),AV-PAD,'A?候选·非确认分界'),COLORMAGENTA;",
             "DRAWTEXT(SHOWC AND ISLASTBAR AND AFAIL,AV-PAD,'A?候选失效'),COLORMAGENTA;",
             "{ REFX1末值冻结只画最后一套短水平线；不混入历史信号 }",
             "VLEN:=REFX1(MIN(SPAN,FAGE),100000);",
             "VF:=REFX1(FON,100000) AND VX<=VLEN;",
             "VTLEN:=REFX1(MIN(BC-1,MIN(SPAN,MAX(40,TAGE))),100000);",
             "VT:=REFX1(TON,100000) AND VX<=VTLEN;",
             "VSA:=REFX1(SA0+PN,100000);", "VBA:=REFX1(BA0+BN,100000);",
             "POLYLINE(REFX1(SK0,100000) AND (VX=VSA OR ISLASTBAR),IF(ISLASTBAR,C,REFX1(SP0,100000)),COLORCYAN);",
             "POLYLINE(REFX1(BK0,100000) AND (VX=VBA OR ISLASTBAR),IF(ISLASTBAR,C,REFX1(BP0,100000)),COLORYELLOW);",
             "{ 只画当前锚点的直线参考段；条件关闭时不产生0价或空值算术 }",
             "VCL:=REFX1(SHOWC AND LINEOK AND SHOWBOOK=0,100000);",
             "VCS:=REFX1(IF(WD=1,LSLOPE,HSLOPE),100000);",
             "VPS:=REFX1(IF(WD=1,HSLOPE,LSLOPE),100000);",
             "VCA:=REFX1(MIN(SPAN,IF(WD=1,LAGE1,HAGE1)),100000);",
             "VCV:=REFX1(TRENDV,100000);", "VPV:=REFX1(PIVOTV,100000);",
             "POLYLINE(VCL AND (VX=VCA OR ISLASTBAR),VCV-VCS*VX,COLORYELLOW);",
             "POLYLINE(VCL AND (VX=VCA OR ISLASTBAR),VPV-VPS*VX,COLORMAGENTA);"]
    for key, text in [("0", "0%"), ("236", "23.6%"), ("382", "38.2%"), ("500", "50%"), ("618", "61.8%"), ("786", "78.6%"), ("100", "100%")]:
        lines.append(f"VF{key}:=REFX1(F{key},100000);")
        lines.append(f"POLYLINE(VF AND (VX=VLEN OR ISLASTBAR),VF{key},COLORFFCC00);")
        lines.append(f"DRAWTEXT(VF AND VX=MIN(10,VLEN),VF{key},'{text}'),COLORFFCC00;")
        lines.append(f"DRAWNUMBER1(VF AND VX=MIN(40,VLEN),VF{key},VF{key},2),COLORFFCC00;")
    ratios = [1.208, 1.618, 2, 2.382, 2.618, 3, 3.236, 4.236]
    for rank in range(1, 4):
        lines += [f"VTT{rank}:=REFX1(TT{rank},100000);",
                  f"VTT{rank}ON:=REFX1(TT{rank}OK,100000);",
                  f"POLYLINE(VT AND VTT{rank}ON AND (VX=VTLEN OR ISLASTBAR),VTT{rank},COLORFF9900);"]
    for i, ratio in enumerate(ratios, 1):
        lines += [f"VT{i}:=REFX1(T{i},100000);",
                  f"VT{i}ON:=VT AND REFX1(T{i}OK AND T{i}R<=3,100000);",
                  f"DRAWTEXT(VT{i}ON AND VX=MIN(12,VTLEN),VT{i},'{ratio:g}H1候选'),COLORFF9900;",
                  f"DRAWNUMBER1(VT{i}ON AND VX=MIN(35,VTLEN),VT{i},VT{i},2),COLORFF9900;"]
    lines += ["DRAWTEXT(ISLASTBAR AND WD=0,C,'工作方向待确认：可设置DIR'),COLORWHITE;",
              "DRAWTEXT(ISLASTBAR AND PHASE=1 AND SHOWT AND IF(TON,0,1),C,'缺少同级H1依据：暂不投射目标'),COLORWHITE;",
              "DRAWTEXT(ISLASTBAR AND IF(SKOK,0,1),C,'标准K样本不足或全为零'),COLORWHITE;",
              "工作方向:WD,NODRAW;", "末段状态:IF(SHOWBOOK,PARENTPHASE,PHASE),NODRAW;", "方向模式:DIR,NODRAW;", "标准K:SK,NODRAW;",
              "DRAWTEXT(DETAIL AND ISLASTBAR AND SK1,L-PAD*3,'细腿回调%'),COLORCYAN;",
              "DRAWNUMBER1(DETAIL AND ISLASTBAR AND SK1,L-PAD*4,SRET,1),COLORCYAN;",
              "DRAWTEXT(SHOWQ AND (DETAIL OR VX=VQAGE) AND (BUQ OR BDQ) AND QOK AND QREL=0,C,'Q同级'),COLORYELLOW;",
              "DRAWTEXT(SHOWQ AND (DETAIL OR VX=VQAGE) AND (BUQ OR BDQ) AND QOK AND QREL=1,C,'Q扩张'),COLORYELLOW;",
              "DRAWTEXT(SHOWQ AND (DETAIL OR VX=VQAGE) AND (BUQ OR BDQ) AND QOK AND QREL=0-1,C,'Q收缩'),COLORYELLOW;",
              "DRAWTEXT(SHOWQ AND (DETAIL OR VX=VQAGE) AND (BUQ OR BDQ) AND QOK AND QREL=2,C,'Q升级候选'),COLORYELLOW;",
              "DRAWTEXT(SHOWQ AND (DETAIL OR VX=VQAGE) AND (BUQ OR BDQ) AND QOK AND QREL=0-2,C,'Q降级候选'),COLORYELLOW;"]
    legacy_only=('abc候选','收缩abc候选','收缩三角候选','扩张三角候选','A?候选',
                 '锤子·观察','倒锤子·观察','阳抱阴·观察','阴抱阳·观察','启明星·观察',
                 '黄昏星·观察','看涨十字·观察','看跌十字·观察','缺少同级H1依据')
    for i,line in enumerate(lines):
        if line.startswith('VF:='):
            # Standard book mode has exactly ONE price plan. The legacy
            # ruler is retained only for explicit development comparison.
            lines[i]=line.replace('REFX1(FON,100000)','REFX1(FON AND SHOWBOOK=0,100000)')
        elif line.startswith('VT:='):
            lines[i]=line.replace('REFX1(TON,100000)','REFX1(TON AND SHOWBOOK=0,100000)')
        elif 'SHOWQ AND' in line:
            lines[i]=line.replace('SHOWQ AND','SHOWQ AND (SHOWBOOK=0 OR DETAIL) AND')
        elif any(text in line for text in legacy_only) and line.startswith('DRAWTEXT('):
            lines[i]=line.replace('DRAWTEXT(','DRAWTEXT(SHOWBOOK=0 AND ',1)
    # A single named RV observation supplies the pattern. KEY is a separate
    # two-root badge for standard size + key position, avoiding a second
    # overlapping copy of every pattern name.
    for flag,price,color in (('VKBUY','L-PAD*3','COLORRED'),('VKSELL','H+PAD*3','COLORGREEN')):
        near='GLS AND GTYPE=(0-1) AND GLT<=3'if flag=='VKBUY'else'GHS AND GTYPE=1 AND GHT<=3'
        lines.append(f"KTEXT(SHOWK AND {flag} AND {near} AND VX<=REFX1(MIN(WORKAGE,BOOKBARS),100000),0,{price},0,{color},'KEY'),FONTSIZE9;")
    # Raw fractals are reference geometry; qualified book waves retain the
    # coloured, thicker strokes. This separates the two interpretations.
    refs=[]
    for line in lines:
        if line.startswith(('POLYLINE(VSH OR VSL','POLYLINE(VBH OR VBL','POLYLINE(REFX1(SK0','POLYLINE(REFX1(BK0')):
            line=line.replace('POLYLINE(VSH OR VSL,','POLYLINE((VSH OR VSL),').replace('POLYLINE(VBH OR VBL,','POLYLINE((VBH OR VBL),')
            refs.append(line.replace('POLYLINE(','POLYLINE(SHOWBOOK=0 AND ',1))
            match=re.match(r'(.+),COLOR(?:CYAN|YELLOW)\)(?:,LINETHICK[12])?;',line)
            if not match:raise ValueError('unknown reference-polyline template')
            if not line.startswith(('POLYLINE((VSH','POLYLINE(REFX1(SK0')):
                refs.append(match[1].replace('POLYLINE(','POLYLINE(SHOWBOOK AND ',1)+',COLORGRAY),DOT;')
        else:refs.append(line)
    lines=refs
    for i,line in enumerate(lines):
        if line.startswith(('POLYLINE(','DRAWTEXT(','DRAWNUMBER1(','KTEXT('))or ',NODRAW;'in line:
            owner='prices'if 'COLORFFCC00'in line or 'COLORFF9900'in line else'key_reversals'if '关键位·'in line else'overview'
            lines[i]=line+' { OWNER:'+owner+' }'
    from book_layers import layers
    book,groups=layers()
    return lines+book


def drawing():
    # The old model remains callable for independent core/book regressions;
    # it is not evaluated as part of the new product drawing path.
    historical=['VX:=REFX1(BC,100000)-BC;',
                'BSAFEHI:=REFX1(IF(ISLASTBAR,MAX(ABS(HHV(H,BC)),ABS(LLV(L,BC)))+1,0),100000);',
                'BSAFELO:=0-BSAFEHI;',
                'BEPS:=REFX1(MAX(ABS(C)*0.0000000001,0.0000001),100000);',
                'BPAD:=REFX1(PAD,100000);']
    from interval_layers import layers
    visible,_=layers()
    return historical+visible


def legacy_formula_source():
    from compatibility import normalize
    return normalize('\n'.join(core()+legacy_drawing()))


def legacy_formula_names():
    from compatibility import variable_names
    return variable_names('\n'.join(core()+legacy_drawing()))


def raw_probe():
    return """{ 新建主图；本文件逐项检查本包用到的关键绘图函数，不能当趋势指标使用 }
X1:=REF(C,1);
X2:=HHV(H,5);
X3:=LLV(L,5);
X4:=BARSLAST(C>O);
X5:=SUM(C>O,0);
X6:=COUNT(C>O,5);
X7:=FILTER(BACKSET(C>O,2),1);
X8:=REFX1(C,100000);
X9:=BARPOS;
X10:=SORT(1,1,C,O,H,L);
X11:=SORTPOS(0,1,C,O,H,L);
X12:=VALUEWHEN(C>O,C);
X13:=IF(ISNULL(X12),C,X12);
X14:=BARSSINCEN(C>O,MAX(1,BARPOS));
X15:=IF(ISNULL(X14),0,X14);
均线:MA(C,5),COLORWHITE;
DRAWLINE(BARPOS=6,X2,ISLASTBAR,X2,0),COLORCYAN;
DRAWLINE(C>O,L,C>O,H,0),COLORYELLOW;
POLYLINE(C>O,C,COLORCYAN);
DRAWLINE3(BARPOS=DATACOUNT-1,X13,MIN(9,MAX(0,BARPOS-1)),ISLASTBAR,X13,0,0),COLORLIBLUE,LINETHICK2;
DRAWTEXT(ISLASTBAR,C,'函数兼容检查'),COLORWHITE;
KTEXT(ISLASTBAR,0,C,0,COLORWHITE,'左侧文字');
DRAWNUMBER1(ISLASTBAR,C,C,2),COLORYELLOW;
DRAWSHIFTNUMBER(ISLASTBAR AND BARPOS>5,H,X10,2,COLORYELLOW,0,5),ALIGN0,FONTSIZE9;
根数:X9,NODRAW;
"""


def formula_source():
    from compatibility import normalize
    return normalize("\n".join(core()+drawing()))


def formula_names():
    from compatibility import variable_names
    return variable_names("\n".join(core()+drawing()))


def probe():
    from compatibility import normalize
    return normalize(raw_probe())


def basic_probe():
    # Exact four-line source reported as compiling and rendering by the user.
    return "QFLAG:=COUNT(C>O,1);\nQOTHER:=COUNT(C<O,1);\nQSIGN:=MAX(QFLAG,QOTHER)+QFLAG-QOTHER-1;\n检查:QSIGN;\n"


def build():
    import json,hashlib
    from export_native import clean_export,write_legend
    path = ROOT / "formulas"
    path.mkdir(exist_ok=True)
    full = formula_source()
    models={"趋势交易_PC主图":full,"函数兼容检查":probe(),"基础函数兼容检查":basic_probe()}
    samples={};profiles={}
    for name,model in models.items():
        text,profile=clean_export(model)
        samples[name]=text
        for suffix,encoding in [('UTF8','utf-8'),('GB18030','gb18030')]:
            filename=f'{name}_{suffix}.txt';data=text.replace('\n','\r\n').encode(encoding)
            (path/filename).write_bytes(data)
            profiles[filename]=profile|{'encoding':encoding,'sha256':hashlib.sha256(data).hexdigest(),'role':'development_comparison'}
    ascii_text,ascii_profile=clean_export(full,ascii_only=True)
    data=ascii_text.replace('\n','\r\n').encode('ascii')
    (path/'EWTN_ASCII.txt').write_bytes(data)
    profiles['EWTN_ASCII.txt']=ascii_profile|{'encoding':'ascii','sha256':hashlib.sha256(data).hexdigest(),'role':'full_model_reference'}
    # The content service rejects >80000 characters as "sensitive words".
    # Installation overlays retain every drawing exactly once, with copied
    # dependencies and identical editable settings in each independent file.
    from native_layers import generate as installation_layers
    layers,layer_report=installation_layers('\n'.join(core()+drawing()))
    for obsolete in (path/'install').glob('EWT[0-9][0-9].txt'):
        if obsolete.name not in layers:obsolete.unlink()
    install=[]
    for filename,(layer_data,layer_profile)in layers.items():
        relative='install/'+filename
        (path/'install').mkdir(exist_ok=True)
        (path/relative).write_bytes(layer_data)
        profiles[relative]=layer_profile
        install.append(relative)
    (ROOT/'导出配置.json').write_text(json.dumps({'release':'1.2.1','primary':'EWTN_ASCII.txt','primary_role':'full_model_reference',
                                               'install':install,'p_focus_install':install[:2],
                                               'recommended_install':install[:3],'optional_install':[],
                                               'installation_layers':layer_report,'files':profiles},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    write_legend(ascii_profile)
    (ROOT/"变量映射.json").write_text(json.dumps(formula_names(), ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    from interval_layers import layers
    (ROOT/"结构字段.json").write_text(json.dumps(layers()[1], ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    return samples


if __name__ == "__main__":
    for name, text in build().items():
        print(name, len(text.splitlines()), "lines;", len(text.encode("gb18030")), "GB18030 bytes")
