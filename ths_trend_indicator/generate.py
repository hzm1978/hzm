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
             "UPPER:=H-MAX(O,C);", "LOWER:=MIN(O,C)-L;"]
    for i in range(1, 41):
        lines.append(f"E{i:02}:=ABS(REF(C,{i})-REF(O,{i}));")
        lines.append(f"D{i:02}:=IF(REF(C,{i})>=REF(O,{i}),REF(C,{i})-REF(L,{i}),REF(H,{i})-REF(C,{i}));")
    for i in range(1, 41):
        terms = [f"(E{j:02}{'>=' if j < i else '>'}E{i:02})"
                 for j in range(1, 41) if j != i]
        lines.append(f"R{i:02}:=1+" + "+".join(terms) + ";")
    lines.append("SK:=IF(BC>40,(" + "+".join(f"IF(R{i:02}<=5,D{i:02},0)" for i in range(1, 41)) + ")/5,0);")
    lines.extend(["SKOK:=BC>40 AND SK>0;", "PAD:=MAX((HHV(H,40)-LLV(L,40))*0.018,SK*0.12);"])
    return lines


def pivots(prefix, n, depth=6):
    """Confirmed fractals; retain record extremes within same-kind runs.

    A0..An are ages of confirmation bars, not the earlier extreme bars.
    Actual extreme age = Ai + n. Every dynamic REF is clamped separately.
    """
    p = prefix
    lines = [f"{{ {p}级：右侧{n}根确认；局部转折不是书中浪号 }}",
             f"{p}RH:=BC>2*{n} AND REF(H,{n})=HHV(H,2*{n}+1) AND REF(H,{n})>HHV(H,{n});",
             f"{p}RL:=BC>2*{n} AND REF(L,{n})=LLV(L,2*{n}+1) AND REF(L,{n})<LLV(L,{n});",
             f"{p}HA:={p}RH AND IF({p}RL,0,1);",
             f"{p}LA:={p}RL AND IF({p}RH,0,1);",
             f"{p}HC:={p}HA AND REF(H,{n})>=HHV(REF(H,{n}),MIN(BC,MAX(1,IF(SUM({p}LA,0)>0,BARSLAST({p}LA)+1,BC))));",
             f"{p}LC:={p}LA AND REF(L,{n})<=LLV(REF(L,{n}),MIN(BC,MAX(1,IF(SUM({p}HA,0)>0,BARSLAST({p}HA)+1,BC))));",
             f"{p}HT:=BARSLAST({p}HC);", f"{p}LT:=BARSLAST({p}LC);",
             f"{p}HS:=SUM({p}HC,0)>0;", f"{p}LS:=SUM({p}LC,0)>0;",
             f"{p}TYPE:=IF({p}HS AND ({p}LS=0 OR {p}HT<{p}LT),1,-1);",
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
    lines = ["{ 趋势交易·期货通PC结构观察版 1.0.2；请先阅读README和验证边界 }",
             "{ PN细转折；BN工作转折；DIR=0自动，1上升，-1下降 }",
             "{ KIND=0自动，1顺势，2调整，3横盘；手动设置需自己确认级别 }",
             "{ MANF=1时用MFROM/MTO作前主浪起止价，MFAGE为起点距今根数 }",
             "{ MH1=0自动候选；大于0时用MBASE/MORG/MAGE指定投射锚点 }",
             "{ STRONG=1才允许强势目标；SPAN为末端价位线最多显示根数 }",
             "PN:=2;", "BN:=7;", "DIR:=0;", "KIND:=0;",
             "MANF:=0;", "MFROM:=0;", "MTO:=0;", "MFAGE:=90;",
             "MH1:=0;", "MBASE:=0;", "MORG:=0;", "MAGE:=0;",
             "STRONG:=0;", "SPAN:=90;", "SHOWQ:=1;", "SHOWK:=1;",
             "SHOWF:=1;", "SHOWT:=1;", "SHOWC:=1;"]
    lines += standard_k()
    lines += pivots("S", "PN") + pivots("B", "BN")
    lines += q_intervals("S", "PN") + q_intervals("B", "BN")
    lines += ["{ 工作方向：高低点同时提高或降低才更新；小级别反转不直接替换工作方向 }",
              "BH0:=IF(BTYPE=1,BP0,BP1);", "BH1:=IF(BTYPE=1,BP2,BP3);",
              "BL0:=IF(BTYPE=-1,BP0,BP1);", "BL1:=IF(BTYPE=-1,BP2,BP3);",
              "DS:=BK3 AND ((BH0>BH1 AND BL0>BL1) OR (BH0<BH1 AND BL0<BL1));",
              "DSV:=IF(BH0>BH1,1,-1);", "DSA:=MIN(BC-1,BARSLAST(DS));",
              "WD:=IF(DIR=1,1,IF(DIR=-1,-1,IF(SUM(DS,0)>0,REF(DSV,DSA),0)));",
              "LEG:=IF(STYPE=1,IF(C>SP0,1,-1),IF(C<SP0,-1,1));",
              "SIDE:=BK3 AND MAX(BH0,BH1)-MIN(BL0,BL1)>0 AND ABS(BH0-BH1)+ABS(BL0-BL1)<(MAX(BH0,BH1)-MIN(BL0,BL1))*0.25;",
              "PHASE:=IF(KIND>0,KIND,IF(SIDE,3,IF(WD=0,0,IF(LEG=WD,1,2))));",
              "{ 最近完整工作同向腿：末端0%，前端100%；原始端价计算后才显示小数 }",
              "ALIGN:=BTYPE=WD;",
              "FEND:=IF(MANF=1,MTO,IF(ALIGN,BP0,BP1));",
              "FSTART:=IF(MANF=1,MFROM,IF(ALIGN,BP1,BP2));",
              "FAGE:=MIN(BC-1,MAX(1,IF(MANF=1,MFAGE,IF(ALIGN,BA1,BA2)+BN)));",
              "FH:=ABS(FSTART-FEND);",
              "FVALID:=WD<>0 AND (IF(MANF=1,MFAGE>=1 AND MFAGE<BC,BK2)) AND WD*(FEND-FSTART)>0 AND FH>0;",
              "F0:=FEND;", "F236:=FEND+(FSTART-FEND)*0.236;",
              "F382:=FEND+(FSTART-FEND)*0.382;", "F500:=FEND+(FSTART-FEND)*0.5;",
              "F618:=FEND+(FSTART-FEND)*0.618;", "F786:=FEND+(FSTART-FEND)*0.786;", "F100:=FSTART;",
              "FON:=SHOWF AND FVALID AND (PHASE=2 OR PHASE=3);",
              "RET:=IF(FVALID,WD*(FEND-C)/MAX(FH,0.0000001)*100,0);",
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
              "HOK:=BK5 AND WD<>0 AND HQFIT AND IF(ALIGN,WD*(BP2-BP4)>0,WD*(BP1-BP3)>0);",
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
        lines.append(f"TT{rank}:=" + "+".join(f"IF(T{i}OK AND T{i}R={rank},T{i},0)" for i in range(1, 9)) + ";")
        lines.append(f"TT{rank}OK:=" + " OR ".join(f"(T{i}OK AND T{i}R={rank})" for i in range(1, 9)) + ";")
    lines += ["{ 调整仅标候选：三/五腿与几何收敛不证明书中的内部完整分级 }",
              "CSIGN:=0-WD;", "COK:=BK3 AND WD<>0 AND BTYPE=CSIGN;",
              "ALEG:=ABS(BP2-BP3);", "BLEG:=ABS(BP1-BP2);", "CLEG:=ABS(BP0-BP1);",
              "ABC:=COK AND ALEG>0 AND WD*(BP1-BP3)<=0 AND CSIGN*(BP0-BP2)>0;",
              "ABCS:=COK AND ALEG>0 AND WD*(BP1-BP3)<=0 AND CSIGN*(BP0-BP2)<=0;",
              "C5:=BK5 AND WD<>0 AND BTYPE=CSIGN;",
              "SHRINK:=C5 AND ABS(BP4-BP5)>ABS(BP3-BP4) AND ABS(BP3-BP4)>ABS(BP2-BP3) AND ABS(BP2-BP3)>ABS(BP1-BP2) AND ABS(BP1-BP2)>ABS(BP0-BP1);",
              "EXPAND:=C5 AND ABS(BP4-BP5)<ABS(BP3-BP4) AND ABS(BP3-BP4)<ABS(BP2-BP3) AND ABS(BP2-BP3)<ABS(BP1-BP2) AND ABS(BP1-BP2)<ABS(BP0-BP1);",
              "ACAND:=BK1 AND WD<>0 AND BTYPE=CSIGN AND (ABC OR ABCS OR SHRINK OR EXPAND);",
              "AV:=BP0;", "AFAIL:=ACAND AND WD*(C-AV)<0;",
              "{ 关键回调价位必须在上一根已知；形态完成根不借用后来出现的结构 }",
              "PREPOS:=REF(FVALID,1) AND (ABS(REF(RET,1)-40)<=5 OR ABS(REF(RET,1)-62)<=5 OR ABS(REF(RET,1)-78.6)<=5);",
              "NEARPOS:=PREPOS AND (WD=REF(WD,1));",
              "HAMMER:=BODY>0 AND LOWER>BODY*1.618 AND LOWER>UPPER*2;",
              "INVHAM:=BODY>0 AND UPPER>BODY*1.618 AND UPPER>LOWER*2;",
              "DOJI:=RANGE>0 AND BODY<=RANGE*0.1;",
              "BULLENG:=C>O AND REF(C,1)<REF(O,1) AND C>=REF(O,1) AND O<=REF(C,1);",
              "BEARENG:=C<O AND REF(C,1)>REF(O,1) AND C<=REF(O,1) AND O>=REF(C,1);",
              "MORNING:=BC>2 AND REF(C,2)<REF(O,2) AND REF(BODY,1)<REF(BODY,2)*0.5 AND C>O AND C>REF(O,2);",
              "EVENING:=BC>2 AND REF(C,2)>REF(O,2) AND REF(BODY,1)<REF(BODY,2)*0.5 AND C<O AND C<REF(O,2);",
              "BULLDO:=DOJI AND LOWER>UPPER*2;", "BEARDO:=DOJI AND UPPER>LOWER*2;",
              "BULLSTD:=SKOK AND C>O AND C-L>SK;", "BEARSTD:=SKOK AND C<O AND H-C>SK;",
              "BULLPAT:=BULLENG OR MORNING OR HAMMER OR BULLDO;",
              "BEARPAT:=BEARENG OR EVENING OR INVHAM OR BEARDO;",
              "BULLKEY:=NEARPOS AND WD=1 AND BULLSTD AND BULLPAT;",
              "BEARKEY:=NEARPOS AND WD=-1 AND BEARSTD AND BEARPAT;",
              "{ 对边两极值斜率平移参考：尚未验证全部锚间包络，不叫已确认拐点线 }",
              "HAGE0:=IF(BTYPE=1,BA0,BA1)+BN;", "HAGE1:=IF(BTYPE=1,BA2,BA3)+BN;",
              "LAGE0:=IF(BTYPE=-1,BA0,BA1)+BN;", "LAGE1:=IF(BTYPE=-1,BA2,BA3)+BN;",
              "HSLOPE:=(BH0-BH1)/MAX(1,HAGE1-HAGE0);",
              "LSLOPE:=(BL0-BL1)/MAX(1,LAGE1-LAGE0);",
              "TRENDV:=IF(WD=1,BL0+LSLOPE*LAGE0,BH0+HSLOPE*HAGE0);",
              "PIVOTV:=IF(WD=1,BH0+LSLOPE*HAGE0,BL0+HSLOPE*LAGE0);",
              "LINEOK:=BK3 AND WD<>0;", "{ END_ANALYSIS：以下未来引用只用于当前图形显示，绝不参与上面的计算 }"]
    # RANGE is a built-in formula function in several THS editions. Avoid
    # borrowing candlestick helper names that might collide with native names.
    renamed = {"BODY": "KBODY", "RANGE": "KRANGE", "UPPER": "KUWICK", "LOWER": "KLWICK"}
    return [re.sub(r"\b(?:BODY|RANGE|UPPER|LOWER)\b", lambda m: renamed[m.group()], s)
            for s in lines]


def drawing():
    lines = ["{ 回标转折：必须等PN/BN根后才知道；历史连线会重绘，不作为当时的交易信号 }",
             "VSH:=FILTER(BACKSET(SHC,PN+1),PN);", "VSL:=FILTER(BACKSET(SLC,PN+1),PN);",
             "VBH:=FILTER(BACKSET(BHC,BN+1),BN);", "VBL:=FILTER(BACKSET(BLC,BN+1),BN);",
             "DRAWLINE(VSH,H,VSL,L,0),COLORCYAN,LINETHICK1;",
             "DRAWLINE(VSL,L,VSH,H,0),COLORCYAN,LINETHICK1;",
             "DRAWLINE(VBH,H,VBL,L,0),COLORYELLOW,LINETHICK2;",
             "DRAWLINE(VBL,L,VBH,H,0),COLORYELLOW,LINETHICK2;",
             "DRAWNUMBER1(VBH,H+PAD,H,2),COLORYELLOW;",
             "DRAWNUMBER1(VBL,L-PAD,L,2),COLORYELLOW;",
             "DRAWLINE(SHOWQ AND VSH,H,SHOWQ AND SUQ,SUH,0),COLORWHITE;",
             "DRAWLINE(SHOWQ AND VSL,L,SHOWQ AND SDQ,SDL,0),COLORWHITE;",
             "DRAWTEXT(SHOWQ AND SUQ,H+PAD*2,'细Q收破'),COLORWHITE;",
             "DRAWNUMBER1(SHOWQ AND SUQ,H+PAD*3,SUN,0),COLORWHITE;",
             "DRAWTEXT(SHOWQ AND SDQ,L-PAD*2,'细Q收破'),COLORWHITE;",
             "DRAWNUMBER1(SHOWQ AND SDQ,L-PAD*3,SDN,0),COLORWHITE;",
             "DRAWTEXT(SHOWQ AND BUQ,H+PAD*4,'工作Q收破'),COLORYELLOW;",
             "DRAWNUMBER1(SHOWQ AND BUQ,H+PAD*5,BUN,0),COLORYELLOW;",
             "DRAWTEXT(SHOWQ AND BDQ,L-PAD*4,'工作Q收破'),COLORYELLOW;",
             "DRAWNUMBER1(SHOWQ AND BDQ,L-PAD*5,BDN,0),COLORYELLOW;",
             "DRAWTEXT(SHOWK AND BULLKEY,L-PAD*4,'关键位置看涨·候选'),COLORRED;",
             "DRAWTEXT(SHOWK AND BEARKEY,H+PAD*4,'关键位置看跌·候选'),COLORGREEN;",
             "DRAWTEXT(SHOWK AND ISLASTBAR AND HAMMER,L-PAD,'锤子·观察'),COLORWHITE;",
             "DRAWTEXT(SHOWK AND ISLASTBAR AND INVHAM,H+PAD,'倒锤子·观察'),COLORWHITE;",
             "DRAWTEXT(SHOWK AND ISLASTBAR AND BULLENG,L-PAD*2,'阳抱阴·观察'),COLORRED;",
             "DRAWTEXT(SHOWK AND ISLASTBAR AND BEARENG,H+PAD*2,'阴抱阳·观察'),COLORGREEN;",
             "DRAWTEXT(SHOWK AND ISLASTBAR AND MORNING,L-PAD*3,'启明星·观察'),COLORRED;",
             "DRAWTEXT(SHOWK AND ISLASTBAR AND EVENING,H+PAD*3,'黄昏星·观察'),COLORGREEN;",
             "DRAWTEXT(SHOWK AND ISLASTBAR AND BULLDO,L-PAD,'看涨十字·观察'),COLORWHITE;",
             "DRAWTEXT(SHOWK AND ISLASTBAR AND BEARDO,H+PAD,'看跌十字·观察'),COLORWHITE;",
             "DRAWTEXT(SHOWC AND ISLASTBAR AND ABC,AV,'abc候选·内部待分级'),COLORCYAN;",
             "DRAWTEXT(SHOWC AND ISLASTBAR AND ABCS,AV,'收缩abc候选·内部待分级'),COLORCYAN;",
             "DRAWTEXT(SHOWC AND ISLASTBAR AND SHRINK,AV+PAD,'收缩三角候选'),COLORCYAN;",
             "DRAWTEXT(SHOWC AND ISLASTBAR AND EXPAND,AV+PAD,'扩张三角候选'),COLORCYAN;",
             "DRAWTEXT(SHOWC AND ISLASTBAR AND ACAND AND IF(AFAIL,0,1),AV-PAD,'A?候选·非确认分界'),COLORMAGENTA;",
             "DRAWTEXT(SHOWC AND ISLASTBAR AND AFAIL,AV-PAD,'A?候选失效'),COLORMAGENTA;",
             "{ REFX1末值冻结只画最后一套短水平线；不混入历史信号 }",
             "VX:=REFX1(BC,100000)-BC;", "VLEN:=REFX1(MIN(SPAN,FAGE),100000);",
             "VF:=REFX1(FON,100000) AND VX<=VLEN;",
             "VT:=REFX1(TON,100000) AND VX<=REFX1(MIN(SPAN,TAGE),100000);",
             "VSA:=REFX1(SA0+PN,100000);", "VBA:=REFX1(BA0+BN,100000);",
             "DRAWLINE(REFX1(SK0,100000) AND VX=VSA,REFX1(SP0,100000),ISLASTBAR,C,0),COLORCYAN;",
             "DRAWLINE(REFX1(BK0,100000) AND VX=VBA,REFX1(BP0,100000),ISLASTBAR,C,0),COLORYELLOW;",
             "趋势参考:IF(SHOWC AND LINEOK,TRENDV,DRAWNULL),COLORYELLOW;",
             "拐点平移参考:IF(SHOWC AND LINEOK,PIVOTV,DRAWNULL),COLORMAGENTA;"]
    for key, text in [("0", "0%"), ("236", "23.6%"), ("382", "38.2%"), ("500", "50%"), ("618", "61.8%"), ("786", "78.6%"), ("100", "100%")]:
        lines.append(f"VF{key}:=REFX1(F{key},100000);")
        lines.append(f"回调{key}:IF(VF,VF{key},DRAWNULL),COLORFFCC00;")
        lines.append(f"DRAWTEXT(VF AND VX=MIN(12,VLEN),VF{key},'{text}'),COLORFFCC00;")
        lines.append(f"DRAWNUMBER1(ISLASTBAR AND FON,F{key},F{key},2),COLORFFCC00;")
    ratios = [1.208, 1.618, 2, 2.382, 2.618, 3, 3.236, 4.236]
    for rank in range(1, 4):
        lines += [f"VTT{rank}:=REFX1(TT{rank},100000);",
                  f"目标{rank}:IF(VT AND REFX1(TT{rank}OK,100000),VTT{rank},DRAWNULL),COLORFF9900;"]
    for i, ratio in enumerate(ratios, 1):
        lines += [f"VT{i}:=REFX1(T{i},100000);",
                  f"VT{i}ON:=VT AND REFX1(T{i}OK AND T{i}R<=3,100000);",
                  f"DRAWTEXT(VT{i}ON AND VX=MIN(12,REFX1(MIN(SPAN,TAGE),100000)),VT{i},'{ratio:g}H1候选'),COLORFF9900;",
                  f"DRAWNUMBER1(ISLASTBAR AND T{i}OK AND T{i}R<=3,T{i},T{i},2),COLORFF9900;"]
    lines += ["DRAWTEXT(ISLASTBAR AND WD=0,C,'工作方向待确认：可设置DIR'),COLORWHITE;",
              "DRAWTEXT(ISLASTBAR AND PHASE=1 AND SHOWT AND IF(TON,0,1),C,'缺少同级H1依据：暂不投射目标'),COLORWHITE;",
              "DRAWTEXT(ISLASTBAR AND IF(SKOK,0,1),C,'标准K样本不足或全为零'),COLORWHITE;",
              "工作方向:WD,NODRAW;", "末段状态:PHASE,NODRAW;", "标准K:SK,NODRAW;",
              "工作回调百分比:RET,NODRAW;",
              "DRAWTEXT(ISLASTBAR AND SK1,L-PAD*7,'细腿回调%'),COLORCYAN;",
              "DRAWNUMBER1(ISLASTBAR AND SK1,L-PAD*8,SRET,1),COLORCYAN;",
              "DRAWTEXT(SHOWQ AND (BUQ OR BDQ) AND QOK AND QREL=0,C,'Q同级'),COLORYELLOW;",
              "DRAWTEXT(SHOWQ AND (BUQ OR BDQ) AND QOK AND QREL=1,C,'Q扩张'),COLORYELLOW;",
              "DRAWTEXT(SHOWQ AND (BUQ OR BDQ) AND QOK AND QREL=0-1,C,'Q收缩'),COLORYELLOW;",
              "DRAWTEXT(SHOWQ AND (BUQ OR BDQ) AND QOK AND QREL=2,C,'Q升级候选'),COLORYELLOW;",
              "DRAWTEXT(SHOWQ AND (BUQ OR BDQ) AND QOK AND QREL=0-2,C,'Q降级候选'),COLORYELLOW;"]
    return lines


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
均线:MA(C,5),COLORWHITE;
上沿:IF(BARPOS>5,X2,DRAWNULL),COLORCYAN;
DRAWLINE(C>O,L,C<O,H,0),COLORYELLOW;
DRAWTEXT(ISLASTBAR,C,'函数兼容检查'),COLORWHITE;
DRAWNUMBER1(ISLASTBAR,C,C,2),COLORYELLOW;
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


def build():
    path = ROOT / "formulas"
    path.mkdir(exist_ok=True)
    full = formula_source()
    samples = {"趋势交易_PC主图": full, "函数兼容检查": probe()}
    for name, text in samples.items():
        (path / f"{name}_UTF8.txt").write_bytes(text.replace("\n", "\r\n").encode("utf-8-sig"))
        (path / f"{name}_GB18030.txt").write_bytes(text.replace("\n", "\r\n").encode("gb18030"))
    return samples


if __name__ == "__main__":
    for name, text in build().items():
        print(name, len(text.splitlines()), "lines;", len(text.encode("gb18030")), "GB18030 bytes")
