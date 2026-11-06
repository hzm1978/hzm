"""Deliver comment-free, BOM-free formula text and an ASCII label profile.

The mathematical model retains developer comments. They are never copied to
the client. Phase boundaries and the Chinese legend live in a JSON sidecar.
"""
from pathlib import Path
import hashlib
import json
import re
import unicodedata

from check_formula import Parser,FormulaError,TOKEN,check
from compatibility import render

ROOT=Path(__file__).resolve().parent
OUTPUTS={'工作方向':'XWD','末段状态':'XPH','方向模式':'XMODE','标准K':'XSTD',
         '工作回调百分比':'XRET','价格尺方向':'XRDIR','行情类型':'XREG','价格尺状态':'XRPH',
         '均线':'XMA','根数':'XCNT','检查':'XTEST'}
MESSAGES={
    '3?·第一Q进行中':'3? Q1 OPEN','3浪·待耦合':'3 Q WAIT','5?·未破浪3':'5? NO3BREAK',
    'A?·待收盘确认':'A? WAIT','A?候选·非确认分界':'A? WATCH','A?候选失效':'A? FAIL','A失守':'A BREAK',
    'Q升级候选':'Q UP?','Q同级':'Q EQ','Q扩张':'Q EXP','Q收缩':'Q CON','Q降级候选':'Q DN?',
    'X7·结构解释':'7 X','abc候选':'ADJ ABC?','≥进行中':'OPEN >=','不规则abcde':'ADJ ABCDE IR',
    '创新极值·延时过滤':'NEW EXT:DELAY OFF','同级5·结构解释':'5 EQ','同级H1依据不足':'H1 NA',
    '同级合并·3浪解释':'3 MERGE Q','复杂abc·1-3-1候选':'ADJ 1-3-1?',
    '子级价格尺·候选':'SUB PLAN?','工作Q收破':'MQ CLOSE','工作方向待确认：可设置DIR':'DIR? SET DIR',
    '延时收破·观察':'DELAY BREAK?','延时线·待收盘':'DELAY WAIT','强势abc':'ADJ ABC S',
    '扩张5·待第三Q':'5 EXP:Q3?','扩张7·结构解释':'7 EXP','扩张三角':'ADJ TRI E','扩张三角候选':'ADJ TRI E?',
    '收缩5·待第三Q':'5 CON:Q3?','收缩7·结构解释':'7 CON','收缩abc':'ADJ ABC C','收缩abc候选':'ADJ ABC C?',
    '收缩三角':'ADJ TRI C','收缩三角候选':'ADJ TRI C?','本组候选目标已触及':'TARGETS HIT',
    '标准K样本不足或全为零':'STD NA','标准abc':'ADJ ABC','标准收破·直接观察':'STD BREAK?',
    '横向abcde':'ADJ ABCDE H','简单调整':'ADJ SIMPLE','细Q收破':'SQ CLOSE','细腿回调%':'S RET%',
    '缺少同级H1依据：暂不投射目标':'H1 NA','调整A·收盘确认':'A OK','调整结束点失效':'ADJ END FAIL',
    '调整进行中·未确认结束':'ADJ OPEN','趋势abcde':'ADJ ABCDE T','钻石7·结构解释':'7 DIA','隐形Q':'Q HIDDEN',
    '高度合并·3浪解释':'3 MERGE H','高度合并·待收盘Q':'MERGE H:Q?',
    '函数兼容检查':'FUNC CHECK','左侧文字':'TEXT LEFT','段末·观察':'LEG END?',
}
REVERSALS={
    '鬼门关':'RV01','十字星确认':'RV02','急停加速':'RV03','补缺影线':'RV04','魂断蓝桥':'RV05',
    '启明/黄昏星':'RV06','启明星':'RV06B','黄昏星':'RV06S','海底捞月':'RV07','三影线':'RV08',
    '抱线':'RV09','阳抱阴':'RV09B','阴抱阳':'RV09S','锤子线':'RV10','锤子':'RV10B','倒锤子':'RV10S',
    '方向十字':'RV11','看涨十字':'RV11B','看跌十字':'RV11S','跳空虚拟K':'RV12',
    '阳阴阳/阴阳阴':'RV13','阳阴阳':'RV13B','阴阳阴':'RV13S',
}


def ascii_label(text):
    if text.isascii():return text
    if text in MESSAGES:return MESSAGES[text]
    group=re.fullmatch(r'(早[123]子|子前|回调|主|子|孙)(.*)',text)
    if group:
        prefix={'主':'M','子':'S','子前':'P','孙':'G','回调':'S','早1子':'E1.','早2子':'E2.','早3子':'E3.'}[group[1]]
        tail=group[2]
        if re.fullmatch(r'[1-7]\??',tail):
            if group[1]=='回调':return prefix+tail
            return tail if group[1]=='主'else '['+tail+']'if group[1]=='孙'else '('+tail+')'
        if re.fullmatch(r'Q[1-3]',tail):return prefix+tail
        if re.fullmatch(r'[246]回调%',tail):return prefix+tail[0]+' RET%'
        if tail=='A·简单调整':return prefix+'A SIMPLE'
        if tail=='AD':return prefix+'AD'
        if tail=='调整待确认':return prefix+'ADJ?'
    if re.fullmatch(r'[0-9.]+H1候选',text):return text.replace('候选','?')
    for prefix,tag in [('转折观察·','OBS '),('关键位·','KEY ')]:
        if text.startswith(prefix):return tag+REVERSALS[text[len(prefix):]]
    if text.endswith('·观察')and text[:-3]in REVERSALS:return REVERSALS[text[:-3]]+'?'
    raise FormulaError('No reviewed ASCII legend for '+repr(text))


def clean_export(model,ascii_only=False):
    """Only rename display text/outputs. Numerical ASTs remain unchanged."""
    marker=model.find('{ END_ANALYSIS')
    phase=len(Parser(model[:marker]).parse())if marker>=0 else None
    legends={};outputs={};lines=[]
    def rewrite(node):
        if node[0]=='string'and ascii_only:
            converted=ascii_label(node[1]);legends[node[1]]=converted
            return ('string',converted)
        if node[0]=='identifier'and ascii_only:return ('identifier',OUTPUTS.get(node[1],node[1]))
        if node[0]=='call':return ('call',node[1],[rewrite(arg)for arg in node[2]])
        if node[0]=='binary':return ('binary',node[1],rewrite(node[2]),rewrite(node[3]))
        if node[0]=='unary':return ('unary',node[1],rewrite(node[2]))
        return node
    for statement in Parser(model).parse():
        name=OUTPUTS.get(statement.name,statement.name)if ascii_only else statement.name
        if statement.name and name!=statement.name:outputs[statement.name]=name
        head=(name+(':'if statement.output else':='))if name else''
        tail=','+','.join(statement.styles)if statement.styles else''
        lines.append(head+render(rewrite(statement.expression))+tail+';')
    text='\n'.join(lines)+'\n'
    profile={'analysis_end_line':phase,'ascii_only':ascii_only,'bom':False,'comments':False,
             'model_normalized_utf8_sha256':hashlib.sha256(model.encode()).hexdigest(),
             'labels':legends,'outputs':outputs}
    check_delivered(text,profile)
    return text,profile


def check_delivered(source,profile):
    # Do not silently decode using utf-8-sig here: that hid a BOM in 1.1.0.
    for i,char in enumerate(source):
        if char not in '\r\n\t'and (ord(char)<32 or unicodedata.category(char)=='Cf'or char=='\u00a0'):
            raise FormulaError(f'Hidden/control character U+{ord(char):04X} at offset {i}')
    if profile.get('ascii_only')and not source.isascii():raise FormulaError('Primary paste file must be ASCII')
    # The user's catalog documents // and /*...*/. Curly braces are model
    # comments accepted by the development parser, not a native guarantee.
    for token in TOKEN.finditer(source):
        if token.group().startswith('{'):raise FormulaError('Unsupported native brace comment')
    if profile.get('comments')is False:
        for token in TOKEN.finditer(source):
            if token.group().startswith(('//','/*')):raise FormulaError('Paste file must contain no comments')
    if not source or source[0].isspace():raise FormulaError('Paste file cannot start with a blank line')
    phase=profile.get('analysis_end_line')
    return check(source,enforce_boundary=phase is not None,analysis_end_line=phase)


def load_primary():
    """Load the complete developer model; installation uses the install list."""
    profile=json.loads((ROOT/'导出配置.json').read_text(encoding='utf-8'))
    name=profile['primary'];file_profile=profile['files'][name]
    data=(ROOT/'formulas'/name).read_bytes()
    if hashlib.sha256(data).hexdigest()!=file_profile['sha256']:raise FormulaError('Primary source differs from export profile')
    source=data.decode('ascii').replace('\r\n','\n')
    return source,file_profile


def write_legend(profile):
    from candle_patterns import PATTERNS
    from book_corrections import ADJUSTMENT_TYPES
    rows=['# 图例与英文代码',
          '', 'M为最新主级结构，P为其中最后一个主浪的次级结构，S仅为当前最后回调。',
          '白线为实际Q区间跨度；Q数字不计起始根。RET%相对于同级前一推进腿。',
          '主浪端点旁的数值单位H1，是已观察推进高度除以本组第一推进高度，保留三位小数。价格尺端点另标真实价格。',
          '', '| 英文标注 | 含义 |','|---|---|',
          '| M1、P1、S1… | 经Q归属与合并后的同级浪号，?表示尚未确认完成 |',
          '| 5 EQ | 两个同级Q耦合的5浪 |',
          '| 7 EXP / CON / DIA / X | 扩张 / 收缩 / 钻石 / X形7浪 |',
          '| 5 EXP:Q3? / 5 CON:Q3? | 两Q扩张/收缩，等待第三Q；尚不判7浪 |',
          '| Q EXTENSION? | 搜索或候选数量受限，或存在待归属Q，不能视为完整已确认结构 |',
          '| H1 ? / H1 BASIS? | 同级第一Q或H1依据不足，不编造端点倍数/目标 |',
          '| 1.208H1?、1.618H1?等 | 尚未触及的未来候选止盈位，与实际端点倍数区分 |',
          '| A SIMPLE / COMPLEX? / ADJ? | 简单调整 / 多转折证据但组合未定 / 证据不足 |',
          '| ABC / ABC CON / ABC STR | 标准 / 收缩 / 强势abc |',
          '| TRI CON / TRI EXP | 收缩 / 扩张三角形，实际末点e |',
          '| ABCDE TREND / FLAT / IRREG | 趋势 / 横向 / 不规则abcde |',
          '| M/P/S RV01…13 | 归属于该层转折附近、完成根上的K线形态观察 |',
          '', '同一个区间已有英文分类时不追加重复的中文分类。紫红色字母a…e用于该区间内部调整。',
          '', '| RV代码 | K线形态 |','|---|---|']
    rows.extend('| RV'+str(k).zfill(2)+' | '+v+' |'for k,v in PATTERNS.items())
    rows+=['', 'RV是形态观察，不等于未来转折必然成立；有多种形态时按规则优先显示一个，未识别到不硬标。',
           '', '| 标题字段 | 中文含义 |','|---|---|']
    rows.extend('| '+v+' | '+k+' |'for k,v in profile['outputs'].items())
    (ROOT/'docs'/'ASCII图例对照.md').write_text('\n'.join(rows)+'\n',encoding='utf-8')
