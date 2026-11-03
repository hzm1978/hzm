from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QPen


def reference_table(c, p, rows, color, occupied, top, start, end):
    fm = p.fontMetrics()
    row_h = fm.height()+3
    width = min(210, max(140, max(fm.horizontalAdvance(row) for row in rows)+12))
    width = min(width, c._plot_rect.width()-8)
    height = row_h*len(rows)+8
    left, right = c._plot_rect.left()+4, c._plot_rect.right()-width-4
    bottom = c._plot_rect.bottom()-height-4
    boxes = [QRectF(right, top, width, height), QRectF(left, bottom, width, height),
             QRectF(right, bottom, width, height), QRectF(left, top, width, height)]
    def cost(rect):
        total = sum(rect.intersected(old).width()*rect.intersected(old).height() for old in occupied)
        for i in range(start, end):
            row = c.partial if i == c.current_idx and c.partial is not None else c.df.iloc[i]
            candle = QRectF(c._index_x(i)-2, c._price_y(float(row["high"])), 4,
                            max(1, c._price_y(float(row["low"]))-c._price_y(float(row["high"]))))
            overlap = rect.intersected(candle)
            total += overlap.width()*overlap.height()
        return total
    panel = min(boxes, key=cost)
    p.fillRect(panel, QColor(16, 20, 28, 238))
    p.setPen(QPen(color, .8)); p.drawRect(panel)
    for i, text in enumerate(rows):
        p.setPen(color if i == 0 else QColor(210, 220, 235))
        row_rect = QRectF(panel.left()+6, panel.top()+4+i*row_h, panel.width()-12, row_h)
        p.drawText(row_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   fm.elidedText(text, Qt.TextElideMode.ElideRight, int(row_rect.width())))
    occupied.append(panel.adjusted(-3, -3, 3, 3))


def paint_trend_overlay(chart, painter, start, end):
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
    title = "趋势交易法 · 第三版 | " + (child.label if compact else a.status)
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

    projection = next((g for g in a.wave_projections if not g.historical
                       and (settings.show_subwaves or g.degree == "主")), None)
    show_reverse = bool(settings.show_reverse_fibonacci and projection and projection.reverse)
    if show_reverse:
        ruler = projection.reverse
        x1 = c._index_x(max(start, ruler.start.index))
        x2 = c._plot_rect.right()
        ys = [c._price_y(level.price) for level in ruler.levels]
        for i, (y1, y2) in enumerate(zip(ys, ys[1:])):
            p.fillRect(QRectF(x1, min(y1, y2), max(0, x2-x1), abs(y2-y1)),
                       QColor(195, 150, 30, 12 if i % 2 else 22))
        p.setPen(QPen(QColor(240, 205, 75, 180), .8, Qt.PenStyle.DotLine))
        visible = []
        for level, y in zip(ruler.levels, ys):
            if c._plot_rect.top()+36 < y < c._plot_rect.bottom()-5:
                p.drawLine(QPointF(x1, y), QPointF(x2, y))
                visible.append((level, y))
        # Price captions must stay beside their real level. The general tag
        # placer can move crowded captions far away, which misstates the ruler.
        ordered_y = sorted(y for _, y in visible)
        if all(b-a >= metrics.height()+2 for a, b in zip(ordered_y, ordered_y[1:])):
            for level, y in visible:
                text = f"{level.label} ({level.price:.6g})"
                w = metrics.horizontalAdvance(text)+8
                rect = QRectF(min(x1+4, c._plot_rect.right()-w-2), y+2,
                              w, metrics.height()+2)
                if rect.bottom() > c._plot_rect.bottom():
                    rect.moveBottom(y-2)
                p.fillRect(rect, QColor(20, 20, 15, 225))
                p.setPen(yellow)
                p.drawText(rect.adjusted(4, 0, -2, 0), Qt.AlignmentFlag.AlignVCenter, text)
                occupied.append(rect.adjusted(-2, -2, 2, 2))
        elif visible:
            reference_table(c, p, ["前一回调 · 反向黄金分割"] +
                            [f"{level.label}  {level.price:.6g}" for level, _ in visible],
                            yellow, occupied, header_y+(25 if compact else 43), start, end)

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
            last = a.pivots[-1]
            tip_index = min(c.current_idx, end - 1)
            if tip_index > last.index:
                tip = float(c.partial["close"]) if c.partial is not None and tip_index == c.current_idx else float(c.df.iloc[tip_index]["close"])
                p.setPen(QPen(QColor(245, 200, 85, 130), 1, Qt.PenStyle.DashLine))
                p.drawLine(QPointF(c._index_x(last.index), c._price_y(last.price)),
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

    seen_levels = set()
    displayed_levels = [x for x in a.levels if not (child or show_reverse) or x.kind != "retracement"]
    if child and not show_reverse:
        displayed_levels += list(child.levels)
    for level in displayed_levels:
        if level.kind == "target" and not settings.show_targets:
            continue
        if level.kind != "target" and not settings.show_levels:
            continue
        rounded = round(level.price, 8)
        if rounded in seen_levels or level.confirmed_at > a.as_of_index:
            continue
        seen_levels.add(rounded)
        y = c._price_y(level.price)
        if not c._plot_rect.top() + 40 < y < c._plot_rect.bottom() - 5:
            continue
        color = QColor(215, 150, 240, 190) if level.kind == "delay" else QColor(180, 190, 210, 130)
        p.setPen(QPen(color, .9, Qt.PenStyle.DashLine))
        x = c._index_x(max(start, level.start_index))
        p.drawLine(QPointF(x, y), QPointF(c._plot_rect.right(), y))
        if level.kind != "subwave_retracement":
            tags.append((25, c._plot_rect.right() - 85, y, f"{level.label} {level.price:.6g}", color, True))

    if child and settings.show_levels and not show_reverse:
        # A fixed ruler table avoids moving a price caption several rows away
        # from its true horizontal level when the latest leg is short.
        row_height = metrics.height() + 3
        panel_width = min(180, max(130, int(c._plot_rect.width() * .33)))
        panel_height = row_height * 4 + 8
        left, right = c._plot_rect.left()+4, c._plot_rect.right()-panel_width-4
        top = header_y+(25 if compact else 43)
        bottom = c._plot_rect.bottom()-panel_height-4
        candidates = [QRectF(left, bottom, panel_width, panel_height),
                      QRectF(right, top, panel_width, panel_height),
                      QRectF(left, top, panel_width, panel_height),
                      QRectF(right, bottom, panel_width, panel_height)]
        def cost(rect):
            total = sum(rect.intersected(old).width()*rect.intersected(old).height() for old in occupied)
            for i in range(start, end):
                row = c.partial if i == c.current_idx and c.partial is not None else c.df.iloc[i]
                candle = QRectF(c._index_x(i)-2, c._price_y(float(row["high"])), 4,
                                max(1, c._price_y(float(row["low"]))-c._price_y(float(row["high"]))))
                overlap = rect.intersected(candle)
                total += overlap.width()*overlap.height()
            return total
        panel = min(candidates, key=cost)
        p.fillRect(panel, QColor(16, 20, 28, 235))
        p.setPen(QPen(QColor(95, 205, 245, 100), .8))
        p.drawRect(panel)
        for i, text in enumerate([f"子{len(child.points)-1}回调参考价"] +
                [f"{ratio:g}%   {level.price:.6g}" for ratio, level in zip((40, 62, 78.6), child.levels)]):
            p.setPen(blue if i == 0 else QColor(205, 220, 235))
            p.drawText(panel.adjusted(6, 4+i*row_height, -4, -panel.height()+4+(i+1)*row_height),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text)
        occupied.append(panel.adjusted(-3, -3, 3, 3))

    if settings.show_main_wave_targets and projection:
        active = [t for t in projection.targets if t.wave == projection.wave and t.kind == "main"]
        displayed = active if c._plot_rect.height() >= 400 else (sorted(active, key=lambda t: t.hit_index is not None)[:3])
        rows = [f"{projection.degree}{projection.wave}候选止盈 · H1={abs(projection.first_tip.price-projection.first_origin.price):.6g}"]
        rows += [f"{t.ratio:g}H1  {t.price:.6g}" + ("·强势" if t.ratio > 2 else "") + (" ✓" if t.hit_index is not None else "") for t in displayed]
        if c._plot_rect.height() >= 400:
            rows += [f"全 {t.ratio:g}H1  {t.price:.6g}" + (" ✓" if t.hit_index is not None else "")
                     for t in projection.targets if t.kind == "whole"]
        if len(displayed) < len(active):
            rows.append("扩展 / 全趋势价位见明细")
        reference_table(c, p, rows, orange, occupied, header_y+(25 if compact else 43), start, end)
        for target in active:
            y = c._price_y(target.price)
            if c._plot_rect.top()+40 < y < c._plot_rect.bottom()-4:
                p.setPen(QPen(QColor(255, 180, 80, 130), .9, Qt.PenStyle.DashLine))
                p.drawLine(QPointF(c._index_x(max(start, target.origin.index)), y), QPointF(c._plot_rect.right(), y))
                if target.hit_index is not None:
                    tag(target.hit_index, target.price, f"{target.ratio:g}H1已达", orange,
                        projection.direction > 0, 17)

    if settings.show_historical_arrivals:
        for group in a.wave_projections:
            if group.degree == "子" and not settings.show_subwaves:
                continue
            for item in group.historical_arrivals:
                tag(item.index, item.price, item.text, orange, item.direction < 0, 16)

    if settings.show_targets and a.angle_slope is not None and a.angle_anchor is not None:
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
