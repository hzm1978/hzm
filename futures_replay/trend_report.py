from __future__ import annotations

from dataclasses import asdict
from html import escape
import json


BOOK_URL = "https://github.com/hzm1978/hzm/releases/download/book/3.pdf"
BOOK_SHA256 = "f7c93443bc5df0a2b849a718c817fe20b554bf86d7d4ea0af417b75f0fde16ea"


def _table(headers, rows):
    cells = lambda xs, tag: "".join(f"<{tag}>{escape(str(x))}</{tag}>" for x in xs)
    return "<table border='1' cellspacing='0' cellpadding='5'><tr>" + cells(headers, "th") + "</tr>" + "".join("<tr>" + cells(row, "td") + "</tr>" for row in rows) + "</table>"


def rules_html():
    return f"""<h2>趋势交易法 · 第三版</h2>
    <p>依据 <a href='{BOOK_URL}'>3.pdf</a>，731页。正文书页+55=PDF页。
    程序给出一种可核验的结构解释；书页610允许不违反数浪法则的多种数法。</p>
    <ul>
    <li>书页140–193：Qit按实际K线根数计时；第一Q收盘越前高/低，后续Q可按影线突破。
    内/外、隐形、失败候选分开；dif用于两Q均≤10，其他用δ。</li>
    <li>书页200–203、216–295：前40根中选实体较大的5根，平均阳线收盘至低点、阴线高点至收盘距离。
    当前K不参与自身阈值。锤子影线&gt;1.618实体；十字星长影≥另一侧2倍；
    启明星/黄昏星末收盘须越过第一根开盘，非标准形态等待确认。</li>
    <li>书页296–395：趋势线锚间不穿价，第二锚位于最高/最低之前；失败5的78.6%回归是例外。
    拐点线来自对边外延线平移；主趋势和导航分级。突破根加后两根形成延时水平，之后收盘越过才确认。</li>
    <li>书页396–427：简单调整A取结束极值，abc取c，三角/abcde取e；e可以不是全区间极值。
    主A和导航A分开记录，破位需收盘。</li>
    <li>书页428–611：区间确定同级5浪、扩张/收缩/钻石/X7浪候选；先内后外、同级合并、
    高度1.618合并、升级/降级；允许浪2超过浪1起点。主结构完整并不代表所有子结构完整。</li>
    <li>书页504–557：简单、标准/收缩/强势abc、收缩/扩张三角、趋势/横向/不规则abcde。
    内部结构按更细转折给出，无法分级时明确待定。</li>
    <li>书页558–561：Tp/Ta的Ta从后浪末价水平回投到前浪同价处计算；完整结构或62%–78.6%区域启用，三角时禁用。</li>
    </ul>
    <h3>程序约定</h3><p>默认微转折右侧1根收盘确认、最短Q为2根，以免臆测单根K内高低先后。
    十字实体≤全幅10%、小K及横向容差、快速收回≤2根、强势筛选和距极值2ATR均为程序参数，
    并非原书给出的精确常数。dif的±9按升降级外推。影线消息过滤需要人工/更细数据证据。</p>
    <p>候选、确认时刻、失效分别保存；尚未收盘K不参与正式判定。目标和时间只是参考计算，
    不生成未来K线。书中的胜率未作为本程序实测胜率，也不据此自动下单。</p>"""


def analysis_html(analysis, frame=None):
    if analysis is None:
        return "<p>当前没有可分析的已收盘K线。</p>"
    a = analysis
    states = {"confirmed": "已确认", "candidate": "候选", "breakout": "收盘确认结束", "opposite_break": "结束点失效"}
    patterns = {"simple": "简单", "abc": "标准abc", "abc_contracting": "收缩abc", "abc_strong": "强势abc",
                "triangle": "收缩三角", "expanding_triangle": "扩张三角", "abcde_trending": "趋势abcde",
                "abcde_sideways": "横向abcde", "abcde_irregular": "不规则abcde", "compound_pending": "复合调整·待分级"}

    def when(index):
        if index is None:
            return "—"
        label = f"#{index}"
        if frame is not None and "datetime" in frame and 0 <= index < len(frame):
            label += " " + str(frame.iloc[index]["datetime"])
        return label

    h = f"<h2>{escape(a.status)}</h2><p>截至 {escape(when(a.as_of_index))} 的已收盘资料；依据《趋势交易法》第三版 3.pdf。</p>"
    h += f"<p>当前线的级别：{escape(a.line_degree)}；使用工具：{escape(a.active_tool)}。</p>"
    if a.structure:
        h += "<h3>区间与级别</h3>" + _table(
            ["级别", "开始", "突破", "Q根数", "第一Q收盘时刻", "关系", "备注"],
            [("主Q"+str(i+1), when(p.start_index), when(p.end_index), p.bars, when(p.first_confirmed_at), p.relation or "首区间", "隐形" if p.hidden else "外区间") for i, p in enumerate(a.structure.intervals)] +
            [("导航q", when(p.start_index), when(p.end_index), p.bars, when(p.first_confirmed_at), p.relation, "内区间" if p.parent_start is not None else "降级/合并") for p in a.structure.navigation])
        h += "<p>" + "<br>".join(escape(r) for r in a.structure.rules) + "</p>"
    h += "<h3>分界点 A（与趋势线锚点分开）</h3>" + _table(
        ["A", "结构结束", "价位", "调整类型", "可确认时刻", "收盘破位时刻", "状态"],
        [(p.label, when(p.index), f"{p.price:.6g}", patterns.get(p.pattern, p.pattern), when(p.confirmed_at), when(p.broken_at), states.get(p.state, p.state)) for p in a.boundaries])
    h += "<h3>线的构造</h3>" + _table(["线", "首锚", "次锚", "当前值/平移"], [
        ("趋势线", when(a.trendline[0].index) if a.trendline else "—", when(a.trendline[1].index) if a.trendline else "—", a.trendline_value),
        ("对边外延线", when(a.external_line[0].index) if a.external_line else "—", when(a.external_line[1].index) if a.external_line else "—", "—"),
        ("拐点线", when(a.pivot_line_anchor.index) if a.pivot_line_anchor else "—", "对边外延线平移", a.pivot_line_value)])
    if a.navigation_line:
        h += "<p>导航拐点线：" + escape(when(a.navigation_line[0].index)) + " → " + escape(when(a.navigation_line[1].index)) + f"；平移差值 {a.navigation_offset:.6g}。</p>"
    h += "<h3>回调百分比</h3>" + _table(["调整结束", "相对主浪", "回调", "可确认时刻"],
        [(when(p.index), when(p.start_index), f"{p.percent:.3f}%", when(p.confirmed_at)) for p in a.retracements])
    h += "<h3>位置参考（目标与估计不生成未来K线）</h3>" + _table(["位置", "价位", "已知时刻"],
        [(p.label, f"{p.price:.6g}", when(p.confirmed_at)) for p in a.levels])
    if a.break_decision:
        d = a.break_decision
        h += f"<h3>突破 / 延时</h3><p>{escape(d.label)}<br>{escape(d.reason)}<br>突破：{escape(when(d.break_index))}；延时线成立：{escape(when(d.delay_ready_at))}；确认：{escape(when(d.confirmed_at))}；水平：{d.delay_level}</p>"
    h += "<h3>调整浪</h3>" + _table(["形态", "结构结束点", "子结构", "确认时刻", "依据"],
        [(p.label, when(p.end_point.index) if p.end_point else "待定", p.structure, when(p.confirmed_at), p.evidence) for p in a.corrections])
    h += "<h3>关键位置反转</h3>" + _table(["形态", "实际K线", "位置", "可确认时刻", "依据"],
        [(p.text, when(p.index), p.context, when(p.confirmed_at), p.evidence) for p in a.reversals])
    h += "<h3>最近形态观察（未通过位置/标准条件的不算反转信号）</h3>" + _table(["形态", "时刻", "标准/后续确认", "依据"],
        [(p.text, when(p.index), "具备" if p.qualified else "待确认", p.evidence) for p in a.candle_patterns[-40:]])
    h += "<h3>标准K计算</h3>" + _table(["K线", "方向距离", "前样本门槛", "标准K", "取样K序号"],
        [(when(p.index), f"{p.strength:.6g}", f"{p.threshold:.6g}" if p.threshold is not None else "样本不足", p.standard, ", ".join(map(str, p.sample_indices))) for p in a.standards[-12:]])
    if a.timing:
        h += "<h3>周期参考</h3><p>" + "<br>".join(escape(t) for t in a.timing) + "</p>"
    if a.failed_interval:
        p = a.failed_interval
        h += f"<p>失败Q候选：{p.bars}根；{escape(p.relation)}。尚未突破前极值，不计入已完成主Q。</p>"
    return h


def export_analysis(analysis):
    return json.dumps({"source": {"file": "3.pdf", "edition": 3, "sha256": BOOK_SHA256, "url": BOOK_URL},
                       "analysis": asdict(analysis) if analysis else None}, ensure_ascii=False, indent=2, allow_nan=False)
