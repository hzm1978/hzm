"""Inspect real Qt rendering of complex parent legs and smaller countertrends."""
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
from futures_replay.trend_report import analysis_html
from test_trend_hierarchy_context import complex_decline


def validate(output):
    output.mkdir(parents=True,exist_ok=True)
    app=QApplication.instance() or QApplication([]);app.setFont(QFont('Noto Sans CJK SC',9))
    cases=[('structure','父级下降：末段上调；长主浪内保留子、孙结构',complex_decline(),190,-1),
           ('earlier_leg','较早主跌段放大：区间、回调、A点与反转观察',complex_decline().iloc[:126],65,-1),
           ('mirror','上下对称：父级上升、末段下调',complex_decline(reflect=True),190,1)]
    results=[]
    for name,title,frame,visible,direction in cases:
        widget=QWidget();layout=QVBoxLayout(widget);layout.setContentsMargins(0,0,0,0);layout.setSpacing(0)
        label=QLabel(f'   v8 · {title} · 构造回归行情，非用户原始行情');label.setFixedHeight(30)
        label.setStyleSheet('color:#dbe4ef;background:#192632');layout.addWidget(label)
        chart=CandleChart();chart.visible_bars=visible;chart.show_ma=False;chart.show_volume=False
        chart.set_series(frame,len(frame)-1);chart.set_trend_visible(True);layout.addWidget(chart)
        drawn=[];original=chart._draw_wave_tag
        def trace(*args,**kwargs):
            rect=original(*args,**kwargs)
            if rect is not None:drawn.append(args[3])
            return rect
        chart._draw_wave_tag=trace
        widget.resize(1600,850);widget.show();app.processEvents()
        picture=widget.grab();assert picture.save(str(output/f'{name}.png'))
        analysis=chart._trend_analysis_cached(len(frame))
        assert not chart._trend_error,chart._trend_error
        assert analysis.last_leg.direction==direction and analysis.last_leg.mode=='fibonacci'
        segments=chart._trend_price_segments
        assert len(segments)==7 and {x['kind'] for x in segments}=={'fibonacci'}
        assert all(abs(x['y']-x['label_y'])<1e-8 for x in segments)
        anchor=chart._index_x(analysis.last_leg.fibonacci.start.index)
        assert all(x['x1']<=anchor+1 for x in segments)
        assert any('孙' in text for text in drawn),drawn
        if name=='structure':
            assert analysis.last_leg.degree=='主'
            assert {s.owner for s in analysis.subwaves if s.depth==1 and not s.alternative} >= {(71,120),(133,186)}
            assert any('78.6%' in row['text'] for row in segments)
        (output/f'{name}.html').write_text(analysis_html(analysis,frame),encoding='utf-8')
        results.append(dict(case=name,result='passed',context=analysis.last_leg.context_key,
            working_Q=analysis.last_leg.working_intervals,segments=segments,
            groups=[dict(depth=s.depth,owner=s.owner,points=[p.index for p in s.points],
                Q=[q.bars for q in s.intervals],alternative=s.alternative) for s in analysis.subwaves]))
        del chart.__dict__['_draw_wave_tag']
        widget.close();widget.deleteLater();QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete);app.processEvents()
    (output/'result.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(result='passed',cases=[r['case'] for r in results],data='构造回归行情，非用户原始行情'),ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True)
    validate(parser.parse_args().output)
