"""Render and inspect the actual Qt last-leg overlay in three market states."""
import argparse
import json
import os
from pathlib import Path
import sys
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
from PySide6.QtCore import QCoreApplication,QEvent
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication,QLabel,QVBoxLayout,QWidget
from futures_replay.ui.chart_widget import CandleChart
from test_subwave_hierarchy import daily_five
from test_last_leg import sideways_frame


def validate(output):
    output.mkdir(parents=True,exist_ok=True)
    app=QApplication.instance() or QApplication([]);app.setFont(QFont('Noto Sans CJK SC',9))
    cases=[('trend','顺势下行：只画候选止盈',daily_five().iloc[:88].copy(),'target',3),
           ('adjustment','向上调整：前一下跌浪从底向顶画黄金分割',daily_five(),'fibonacci',7),
           ('sideways','横盘调整：只画黄金分割',sideways_frame(),'fibonacci',7)]
    result=[]
    for name,title,frame,kind,count in cases:
        scale=786/(170.05-144.95);offset=3129-144.95*scale
        for column in ('open','high','low','close'):frame[column]=frame[column]*scale+offset
        widget=QWidget();layout=QVBoxLayout(widget);layout.setContentsMargins(0,0,0,0);layout.setSpacing(0)
        label=QLabel(f'   v7 · {title} · 构造行情，非用户实盘数据');label.setFixedHeight(30)
        label.setStyleSheet('background:#192632;color:#dbe3ee');layout.addWidget(label)
        chart=CandleChart();chart.visible_bars=40;chart.show_ma=False;chart.show_volume=False
        chart.set_series(frame,len(frame)-1);chart.set_trend_visible(True);layout.addWidget(chart)
        widget.resize(1350,680);widget.show();app.processEvents()
        image=widget.grab();assert image.save(str(output/f'{name}.png'))
        assert not chart._trend_error,chart._trend_error
        segments=chart._trend_price_segments
        assert len(segments)==count and {p['kind'] for p in segments}=={kind},segments
        assert all(abs(p['y']-p['label_y'])<1e-6 for p in segments)
        result.append(dict(case=name,result='passed',mode=chart._trend_analysis_cached(len(frame)).last_leg.mode,segments=segments))
        widget.close();widget.deleteLater();QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete);app.processEvents()
    (output/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(result='passed',cases=[p['case'] for p in result],data='构造行情，非用户实盘数据'),ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True)
    validate(parser.parse_args().output)
