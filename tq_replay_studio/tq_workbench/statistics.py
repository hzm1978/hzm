import numpy as np
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget


class EquityPlot(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.values = []; self.setMinimumHeight(150)

    def set_values(self, values):
        self.values = list(values)[-2000:]; self.update()

    def paintEvent(self, event):
        p = QPainter(self); p.fillRect(self.rect(), QColor("#111b29"))
        if not self.values:
            p.setPen(QColor("#99b2d0")); p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "推进K线后显示权益曲线")
            return
        rect = self.rect().adjusted(10, 23, -10, -10)
        low, high = min(self.values), max(self.values)
        span = max(high-low, abs(high)*.001, 1)
        points = [QPointF(rect.left()+index*rect.width()/max(1, len(self.values)-1),
                         rect.bottom()-(value-low)/span*rect.height()) for index, value in enumerate(self.values)]
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor("#65b4ff"), 1.5)); p.drawPolyline(QPolygonF(points))
        p.setPen(QColor("#a6bdd8")); p.drawText(10, 16, f"权益 {self.values[-1]:,.2f}   区间 {low:,.2f}—{high:,.2f}")


class StatisticsPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        self.summary = QLabel(); self.summary.setWordWrap(True); layout.addWidget(self.summary)
        self.plot = EquityPlot(); layout.addWidget(self.plot); layout.addStretch()

    def refresh(self, account, mark):
        current = account.metrics(mark)["equity"]
        values = [account.initial]+[x["equity"] for x in account.equity_curve]+[current]
        series = np.asarray(values)
        peaks = np.maximum.accumulate(series)
        drawdown = np.maximum(0, (peaks-series)/np.maximum(peaks, 1e-9))
        closes = [x for x in account.fills if x["action"].startswith("平")]
        wins = sum(x["pnl"] > 2*account.contract.fee_per_lot*x["qty"] for x in closes)
        self.summary.setText(
            f"净收益 {current-account.initial:+,.2f}\n收益率 {(current/account.initial-1)*100:+.2f}%\n"
            f"最大回撤 {float(drawdown.max())*100:.2f}%\n平仓成交 {len(closes)} 笔\n"
            f"平仓成交胜率 {wins/max(1, len(closes))*100:.1f}%\n"
            f"累计手续费 {sum(x['fee'] for x in account.fills):,.2f}")
        self.plot.set_values(values)
