from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QPen
from ..trend_last_leg import price_text


def price_segments(c, p, rows, color, occupied, anchor, kind):
    """Place every numeric caption ON its true price line; vary X, never Y."""
    fm = p.fontMetrics()
    for text, price in rows:
        y = c._price_y(price)
        if not c._plot_rect.top()+fm.height()/2 < y < c._plot_rect.bottom()-fm.height()/2:
            continue
        w = min(fm.horizontalAdvance(text)+8, c._plot_rect.width()-8)
        right, left = c._plot_rect.right()-4, c._plot_rect.left()+4
        starts = list(reversed([left+i*(w+8) for i in range(max(1,int((right-left)/(w+8))))]))
        starts.insert(0,right-w)
        rects = [QRectF(x,y-fm.height()/2-1,w,fm.height()+2) for x in starts]
        def cost(rect):
            return sum(rect.intersected(old).width()*rect.intersected(old).height() for old in occupied)
        rect = min(rects,key=cost)
        x1 = min(c._index_x(max(c._start,anchor)),rect.left()-4)
        p.setPen(QPen(color,1,Qt.PenStyle.DashLine))
        p.drawLine(QPointF(x1,y),QPointF(right,y))
        p.fillRect(rect,QColor(18,21,26,235));p.setPen(color)
        p.drawText(rect.adjusted(4,0,-2,0),Qt.AlignmentFlag.AlignVCenter,text)
        occupied.append(rect.adjusted(-3,-2,3,2))
        c._trend_price_segments.append(dict(kind=kind,text=text,price=price,y=y,
            x1=x1,x2=right,label_y=rect.center().y()))


def paint_trend_overlay(chart, painter, start, end):
    chart._trend_price_segments = []
    a = chart._trend_analysis_cached(end)
    if a is None:
        if getattr(chart, "_trend_error", ""):
            painter.save()
            painter.setPen(QColor(255, 150, 125))
            painter.drawText(QRectF(chart._plot_rect.left()+8, chart._plot_rect.top()+5,
                                   chart._plot_rect.width()-16, 30),
                             Qt.AlignmentFlag.AlignLeft, "趋势分析暂不可用，请打开指标明细查看原因")
            painter.restore()
        return
    c, p, settings = chart, painter, a.settings
    p.save()
    p.setClipRect(c._plot_rect)
    font = p.font()
    font.setPointSize(max(8, font.pointSize() - 1))
    p.setFont(font)
    up = a.direction == "up"
    main = QColor(65, 220, 155) if up else QColor(250, 125, 115)
    if a.direction not in {"up", "down"}:
        main = QColor(195, 200, 210)
    blue, yellow, orange = QColor(95, 205, 245), QColor(245, 200, 85), QColor(255, 180, 80)
    header_y = max(c._plot_rect.top() + 16, float(c._ma_legend_bottom) + 15)
    metrics = p.fontMetrics()
    width = max(40, int(c._plot_rect.width() - 16))
    child = a.subwaves[0] if settings.show_subwaves and a.subwaves else None
    compact = bool(child and width < 760)
    summary = ""
    if child:
        summary = "  ".join(f"q{i+1}={x.bars}根" for i, x in enumerate(child.intervals))
        if child.pending_interval:
            summary += f"  q2≥{child.pending_interval.bars}根·进行中"
        if child.retracements:
            summary += " | " + child.retracements[-1].text
        summary += f" | 当前回调 {child.pullback_percent:.1f}%"
    leg = a.last_leg
    mode = ("末段/级别待确认" if not leg or leg.mode == "pending" else
            "横盘调整·黄金分割" if leg.market == "sideways" else
            ("下行" if leg and leg.leg_direction < 0 else "上行")+
            ("主浪候选·止盈线" if leg and leg.mode == "targets" else "调整·黄金分割"))
    title = mode + " | 趋势交易法 · 第三版 | " + (child.label if compact else a.status)
    p.setPen(main)
    p.drawText(QRectF(c._plot_rect.left() + 7, header_y - 13, width, 17),
               Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
               metrics.elidedText(title, Qt.TextElideMode.ElideRight, width))
    details = a.line_degree + " | " + a.active_tool
    if a.structure:
        details += " | " + "  ".join(f"Q{i+1}={x.bars}根" for i, x in enumerate(a.structure.intervals))
    if a.key_level is not None:
        details += f" | {a.key_level_kind} {a.key_level:.6g}"
    if a.pullback_percent is not None:
        details += f" | 父级回调 {a.pullback_percent:.1f}%"
    if compact:
        details = summary
    p.setPen(QColor(185, 195, 208))
    p.drawText(QRectF(c._plot_rect.left() + 7, header_y + 5, width, 17),
               Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
               metrics.elidedText(details, Qt.TextElideMode.ElideRight, width))
    if child and not compact:
        summary = child.label + " | " + summary
        p.setPen(blue)
        p.drawText(QRectF(c._plot_rect.left() + 7, header_y + 23, width, 17),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   metrics.elidedText(summary, Qt.TextElideMode.ElideRight, width))
    reserved = 36 + (18 if child and not compact else 0) + (18 if c.synthetic else 0)
    occupied = [QRectF(c._plot_rect.left(), header_y - 15, c._plot_rect.width(), reserved)]
    tags = []

    def tag(index, price, text, color, above, priority=40):
        y = c._price_y(price)
        if start <= index < end and c._plot_rect.top() <= y <= c._plot_rect.bottom():
            tags.append((priority, c._index_x(index), y, text, color, above))

    def line(points, offset, color, style, label, stop=None):
        if not points:
            return
        left, right = points
        x1, x2 = max(start, left.index), end - 1
        if stop is not None:
            x2 = min(x2, stop)
        if x2 < x1:
            return
        y1 = c._price_y(c._trend_line_price(left, right, x1) + offset)
        y2 = c._price_y(c._trend_line_price(left, right, x2) + offset)
        p.setPen(QPen(color, 1.5, style))
        p.drawLine(QPointF(c._index_x(x1), y1), QPointF(c._index_x(x2), y2))
        if c._plot_rect.top() <= y2 <= c._plot_rect.bottom():
            tags.append((20, c._index_x(x2)-60, y2, label, color, not up))

    last = a.last_leg
    show_reverse = bool(last and last.mode == "fibonacci" and settings.show_reverse_fibonacci and last.fibonacci)
    if show_reverse:
        ruler = last.fibonacci
        rows = [(f"{ratio:.3f}  {price_text(level.price)}",level.price)
                for ratio,level in zip((0,.191,.382,.5,.618,1),ruler.levels)]
        price_segments(c,p,rows,yellow,occupied,last.origin.index,"fibonacci")
    elif last and last.mode == "targets" and settings.show_main_wave_targets:
        rows = [(f"止盈 {t.ratio:g}H1{' 全趋势' if t.kind == 'whole' else ''}"
                 f"{' ·强势' if t.kind == 'main' and t.ratio > 2 else ''}  {price_text(t.price)}",t.price)
                for t in last.targets]
        price_segments(c,p,rows,orange,occupied,last.origin.index,"target")

    if settings.show_navigation:
        p.setPen(QPen(QColor(155, 165, 185, 75), .8, Qt.PenStyle.DotLine))
        navigation = a.navigation_pivots[-100:]
        for left, right in zip(navigation, navigation[1:]):
            if left.index < end and right.index >= start:
                p.drawLine(QPointF(c._index_x(left.index), c._price_y(left.price)),
                           QPointF(c._index_x(right.index), c._price_y(right.price)))
    if settings.show_waves:
        p.setPen(QPen(QColor(245, 200, 85, 210), 1.6))
        for left, right in zip(a.pivots, a.pivots[1:]):
            if left.index < end and right.index >= start:
                p.drawLine(QPointF(c._index_x(left.index), c._price_y(left.price)),
                           QPointF(c._index_x(right.index), c._price_y(right.price)))
        if a.pivots:
            last_pivot = a.pivots[-1]
            tip_index = min(c.current_idx, end - 1)
            if tip_index > last_pivot.index:
                tip = float(c.partial["close"]) if c.partial is not None and tip_index == c.current_idx else float(c.df.iloc[tip_index]["close"])
                p.setPen(QPen(QColor(245, 200, 85, 130), 1, Qt.PenStyle.DashLine))
                p.drawLine(QPointF(c._index_x(last_pivot.index), c._price_y(last_pivot.price)),
                           QPointF(c._index_x(tip_index), c._price_y(tip)))
        for label in a.wave_labels:
            if not child or not child.points[0].index <= label.index <= child.points[-1].index:
                tag(label.index, label.price, label.text, yellow, label.direction < 0, 30)
    if settings.show_subwaves:
        for series in a.subwaves:
            color = QColor(95, 205, 245)
            p.setPen(QPen(color, 1.8))
            for left, right in zip(series.points, series.points[1:]):
                if left.index < end and right.index >= start:
                    p.drawLine(QPointF(c._index_x(left.index), c._price_y(left.price)),
                               QPointF(c._index_x(right.index), c._price_y(right.price)))
            for label in series.labels:
                tag(label.index, label.price, label.text, color, label.direction < 0, 8)
            # The current bar's final OHLC in df is intentionally never read.
            # A new extreme in the supplied partial candle is only a preview.
            if c.partial is not None and c.current_idx < end and c.current_idx > series.points[-1].index:
                tip = series.points[-1]
                price = float(c.partial["high" if series.direction > 0 else "low"])
                if series.direction * (price-tip.price) > 0:
                    anchor = series.points[-2]
                    p.setPen(QPen(color, 1.2, Qt.PenStyle.DashLine))
                    p.drawLine(QPointF(c._index_x(anchor.index), c._price_y(anchor.price)),
                               QPointF(c._index_x(c.current_idx), c._price_y(price)))
                    tag(c.current_idx, price, f"子{len(series.points)-1}延伸?·未收盘", color,
                        series.direction > 0, 8)
    if settings.show_corrections:
        for pattern in a.corrections[-10:]:
            if pattern.end_index < start or pattern.start_index >= end:
                continue
            color = QColor(95, 190, 235, 180) if pattern.state == "candidate" else QColor(95, 210, 185, 170)
            if pattern.state == "opposite_break":
                color = QColor(235, 130, 120, 150)
            p.setPen(QPen(color, 1, Qt.PenStyle.DashLine))
            for (x1, y1), (x2, y2) in pattern.lines:
                p.drawLine(QPointF(c._index_x(x1), c._price_y(y1)), QPointF(c._index_x(x2), c._price_y(y2)))
            # Complete A and critical reversals get first choice of label space.
            endpoint = pattern.end_point or pattern.markers[-1]
            caption = pattern.label.replace("·收盘确认结束", "") if pattern.label.startswith("子") else pattern.label
            tag(endpoint.index, endpoint.price, caption, color, pattern.direction < 0, 70)
            markers = () if len(pattern.markers) == 2 else zip(pattern.markers[1:], pattern.marker_labels[1:])
            for point, text in markers:
                tag(point.index, point.price, text, color, point.kind == "H", 75)
    if settings.show_trendline:
        color = QColor(250, 120, 105) if a.trendline_broken else main
        line(a.trendline, 0, color, Qt.PenStyle.DashLine if a.trendline_broken else Qt.PenStyle.SolidLine,
             "趋势线 · " + a.line_degree)
        if settings.show_subwaves:
            for series in a.subwaves:
                line(series.trendline, 0, blue, Qt.PenStyle.DashLine, "子趋势线",
                     None if series is child else series.points[-1].index)
        if a.trendline:
            for point, name in zip(a.trendline, ("趋势锚1", "趋势锚2")):
                tag(point.index, point.price, name, color, point.kind == "H", 65)
    if settings.show_pivot_line:
        line(a.external_line, 0, QColor(165, 180, 200, 125), Qt.PenStyle.DotLine, "对边外延线")
        line(a.external_line, float(a.pivot_line_offset or 0),
             QColor(250, 130, 115) if a.pivot_line_broken else blue,
             Qt.PenStyle.DashDotLine, "拐点线")
        if a.pivot_line_anchor:
            point = a.pivot_line_anchor
            tag(point.index, point.price, "结构平移点", blue, point.kind == "H", 65)
        if a.navigation_line and (settings.show_navigation or a.active_tool.startswith("导航")):
            line(a.navigation_line, float(a.navigation_offset or 0), QColor(170, 150, 245),
                 Qt.PenStyle.DashDotLine, "导航拐点线")
        if settings.show_subwaves:
            for series in a.subwaves:
                line(series.external_line, float(series.pivot_line_offset or 0), QColor(150, 170, 245),
                     Qt.PenStyle.DashDotLine, "子拐点线",
                     None if series is child else series.points[-1].index)

    if settings.show_intervals:
        items = [(f"Q{i+1}", item) for i, item in enumerate(a.structure.intervals)][-5:] if a.structure else []
        if settings.show_navigation and a.structure:
            items += [("q", x) for x in a.structure.navigation[-8:]]
        if settings.show_subwaves:
            for series in a.subwaves:
                items += [(f"子q{i+1}", item) for i, item in enumerate(series.intervals)]
                if series.pending_interval:
                    items.append(("子q2", series.pending_interval))
        for name, item in items:
            if item.end_index < start or item.start_index >= end:
                continue
            color = blue if name.startswith("子") else yellow if name.startswith("Q") else QColor(135, 165, 195, 150)
            offset_y = -15 if item.direction > 0 else 15
            y = c._price_y(item.level) + offset_y
            x1, x2 = c._index_x(max(start, item.start_index)), c._index_x(min(end - 1, item.end_index))
            p.setPen(QPen(color, 1, Qt.PenStyle.DotLine))
            p.drawLine(QPointF(x1, y), QPointF(x2, y))
            for x in (x1, x2):
                p.drawLine(QPointF(x, y - 3), QPointF(x, y + 3))
            text = (f"{name}≥{item.bars}根·进行中" if item.state == "running" else
                    f"{name}={item.bars}根" + ("·隐形" if item.hidden else ""))
            tags.append((3 if name.startswith("子") else 35, (x1 + x2) / 2, y, text, color, item.direction > 0))
        if a.failed_interval:
            item = a.failed_interval
            tag(item.end_index, item.level, f"失败Q候选 {item.bars}根", orange, item.direction > 0, 35)

    if settings.show_boundaries:
        main_points = [x for x in a.boundaries if x.degree == "main"]
        nav_points = [x for x in a.boundaries if x.degree == "navigation"]
        child_points = [point for series in a.subwaves for point in series.boundaries] if settings.show_subwaves else []
        shown = main_points[-3:] + (nav_points[-3:] if settings.show_navigation else []) + child_points
        for point in shown:
            color = orange if point.degree == "main" else blue
            if start <= point.index < end and c._plot_rect.top() <= c._price_y(point.price) <= c._plot_rect.bottom():
                x, y = c._index_x(point.index), c._price_y(point.price)
                p.setPen(QPen(color, 1))
                p.setBrush(QBrush(color))
                p.drawRect(QRectF(x - 3, y - 3, 6, 6))
                p.setBrush(Qt.BrushStyle.NoBrush)
                tag(point.index, point.price, point.label + ("?" if point.state == "candidate" else ""), color, not up, 5)
            if point.state == "confirmed" and ((main_points and point == main_points[-1]) or
                                                (settings.show_navigation and nav_points and point == nav_points[-1]) or
                                                (child and child.boundaries and point == child.boundaries[-1])):
                y = c._price_y(point.price)
                if not c._plot_rect.top() <= y <= c._plot_rect.bottom():
                    continue
                x = c._index_x(max(start, point.index))
                p.setPen(QPen(color, 1, Qt.PenStyle.DashLine if point.broken_at is not None else Qt.PenStyle.DotLine))
                p.drawLine(QPointF(x, y), QPointF(c._plot_rect.right(), y))
                label = f"{point.label} {point.price:.6g}" + (" ·收盘破" if point.broken_at is not None else "")
                tags.append((5, c._plot_rect.right() - 80, y, label, color,
                             point.direction > 0))

    # The last leg has exactly one ruler. Legacy retracement/target tables
    # must not reintroduce the other ruler when older settings are enabled.
    if settings.show_levels and (not child or settings.show_navigation):
        for level in a.levels:
            if level.kind in {"retracement","target","subwave_retracement"} or level.confirmed_at > a.as_of_index:
                continue
            y = c._price_y(level.price)
            if c._plot_rect.top()+40 < y < c._plot_rect.bottom()-5:
                color = QColor(215,150,240,190)
                p.setPen(QPen(color,.9,Qt.PenStyle.DashLine))
                p.drawLine(QPointF(c._index_x(max(start,level.start_index)),y),QPointF(c._plot_rect.right(),y))
                tags.append((25,c._plot_rect.right()-85,y,f"{level.label} {price_text(level.price)}",color,True))

    if settings.show_historical_arrivals:
        seen = set()
        for group in a.wave_projections:
            if group.degree == "子" and not settings.show_subwaves:
                continue
            for item in group.historical_arrivals:
                key = item.index
                if key not in seen:
                    # One exact reached price per completed main-wave endpoint.
                    tag(item.index,item.price,price_text(item.price),orange,item.direction < 0,16)
                    seen.add(key)

    if settings.show_targets and last and last.mode == "targets" and a.angle_slope is not None and a.angle_anchor is not None:
        anchor = a.angle_anchor
        x1, x2 = max(start, anchor.index), end - 1
        value = lambda x: anchor.price + a.angle_slope * (x - anchor.index)
        if x1 < x2:
            p.setPen(QPen(QColor(205, 165, 240, 150), 1, Qt.PenStyle.DashLine))
            p.drawLine(QPointF(c._index_x(x1), c._price_y(value(x1))),
                       QPointF(c._index_x(x2), c._price_y(value(x2))))
            tags.append((40, c._plot_rect.right() - 100, c._price_y(value(x2)), "PSTD角度参考", QColor(205, 165, 240), True))

    if settings.show_ratios:
        if settings.show_subwaves:
            for series in a.subwaves:
                for item in series.retracements:
                    tag(item.index, item.price, item.text, QColor(85, 220, 230), item.direction < 0, 6)
        for item in a.retracements:
            tag(item.index, item.price, item.text, QColor(85, 220, 230), item.direction < 0, 10)
    if settings.show_candles:
        observed_turns = set()
        for item in a.turn_observations:
            if compact and item.context in observed_turns:
                continue
            observed_turns.add(item.context)
            caption = item.text.replace("转折观察·","转折·").replace("·位置待确认","·观察")
            tag(item.index,item.price,caption,QColor(220,195,145),item.direction < 0,12)
        for item in a.reversals:
            if not start <= item.index < end:
                continue
            color = QColor(95, 230, 170) if item.direction > 0 else QColor(255, 145, 125)
            x, y = c._index_x(item.index), c._price_y(item.price)
            p.setPen(QPen(color, 1.5))
            p.setBrush(QBrush(color))
            p.drawEllipse(QPointF(x, y), 3.3, 3.3)
            p.setBrush(Qt.BrushStyle.NoBrush)
            context = "·后确认" if item.confirmed_at > item.index else ""
            tag(item.index, item.price, item.text + context, color, item.direction < 0,
                0 if item.index >= a.as_of_index-2 else 18)
        for item in a.standards[-20:]:
            if item.standard and start <= item.index < end:
                x, y = c._index_x(item.index), c._price_y(float(c.df.iloc[item.index]["close"]))
                p.setPen(QPen(QColor(220, 225, 235), 1))
                p.drawLine(QPointF(x - 3, y), QPointF(x + 3, y))

    # Priority prevents an 'a' or a repetitive geometry caption from displacing
    # a key reversal, A point or measured percentage.
    for _, x, y, text, color, above in sorted(tags, key=lambda t: t[0]):
        c._draw_wave_tag(p, x, y, text, color, above=above, anchor_is_y=True, occupied=occupied)
    p.restore()
