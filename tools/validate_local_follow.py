"""Real Qt proof of local-first, right-aligned history without starting an SDK."""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
import pandas as pd
from PySide6.QtCore import QCoreApplication,QEvent
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication
from futures_replay.ui.main_window import MainWindow
from test_local_follow import bars,write_local


def validate(output):
    output.mkdir(parents=True,exist_ok=True)
    app=QApplication.instance() or QApplication([]);app.setFont(QFont('Noto Sans CJK SC',10))
    errors=[];old_argv=sys.argv[:]
    def spin(predicate):
        end=time.monotonic()+10
        while not predicate() and time.monotonic()<end:
            app.processEvents();threading.Event().wait(.01)
        assert predicate(),errors or '本地最新K线窗口超时'
    with tempfile.TemporaryDirectory(prefix='local-follow-') as directory:
        root=Path(directory);target,day=write_local(root)
        raw=root/'qihushuju_parquet/IF'
        (raw/'1min').mkdir(parents=True);(raw/'1d').mkdir()
        (raw/'数据说明.md').write_text('# IF 沪深300指数期货\n交易所：CFFEX\n',encoding='utf-8')
        sys.argv[0]=str(root/'run.py')
        with patch('futures_replay.ui.main_window.load_state',return_value={}), \
             patch('futures_replay.ui.main_window.load_replay_heads',return_value={}), \
             patch('futures_replay.ui.main_window.load_main_list_sort',return_value={}), \
             patch('futures_replay.ui.main_window.save_state'), \
             patch('futures_replay.ui.main_window.save_replay_heads'), \
             patch('futures_replay.ui.main_window.save_main_list_sort'), \
             patch.object(MainWindow,'_error',lambda self,error:errors.append(str(error))):
            window=MainWindow(root);window.resize(1400,950)
            window.source_combo.setCurrentText('主力连续')
            window.panels[0].period.set_period('1d');window.panels[0].apply_options()
            window.show();app.processEvents();assert window.engine is not None,errors
            window.engine.jump_bar(5,to_end_of_bar=True);window._refresh_all_panels()
            before=window.engine.current();engine_id=id(window.engine);account=window.account.to_dict()
            assert window.live_controller.process is None
            # Open without logging in or starting a background process.
            window.live_view_action.setChecked(True)
            spin(lambda:window._live_chart is not None and not window._live_chart.chart.df.empty)
            live=window._live_chart;live.title.setText('【v8.2.0 构造行情：无后台/休市显示验证】 '+live.title.text())
            live.resize(1250,800);app.processEvents();live.grab().save(str(output/'local_daily.png'))
            assert live.chart.df.datetime.iloc[-1]==day.datetime.iloc[-1]
            assert live.chart.current_idx==len(day)-1
            assert live.chart._end==len(day) and live.chart.end_offset==0 and live.chart.follow_latest
            assert live.chart._index_x(len(day)-1)>live.chart.width()*.85
            assert 'v8.2.0' in live.windowTitle() and '本地最新K线' in live.status.text()
            latest_day=str(live.chart.df.datetime.iloc[-1])
            window.live_controller.statusChanged.emit('连接未完成（构造错误测试）；已有K线保留')
            app.processEvents();assert not live.chart.df.empty
            assert '连接未完成' in live.status.text() and '本地最新K线' in live.status.text()
            live.grab().save(str(output/'local_error.png'))
            minute=bars(pd.date_range('2026-11-03 14:56',periods=4,freq='min'))
            write_local(root,period='1min',frame=minute)
            live.period.setCurrentIndex(live.period.findData('1min'))
            spin(lambda:not live.chart.df.empty and live.chart.df.datetime.iloc[-1]==minute.datetime.iloc[-1])
            live.grab().save(str(output/'local_minute.png'))
            assert live.chart.end_offset==0 and live.chart.follow_latest
            assert live.chart._end==len(minute) and live.chart.partial is None
            assert id(window.engine)==engine_id and window.engine.current().timestamp==before.timestamp
            assert window.account.to_dict()==account and window.live_controller.process is None
            latest_minute=str(live.chart.df.datetime.iloc[-1])
            spin(lambda:live.history._active is None)
            window.close();window.deleteLater();QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)
            app.processEvents();assert not errors,errors
    sys.argv[:]=old_argv
    result=dict(result='passed',pandas=pd.__version__,data='构造IF行情；未实际联网、未启动后台进程',
        latest_day=latest_day,latest_minute=latest_minute,checks=['无后台也能打开','本地最新K线位于最右端',
        '连接失败保留K线并显示原因','切换周期读取本地最新','原复盘引擎/时刻/账户不变','实时窗口显示v8.2.0'])
    (output/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True)
    validate(parser.parse_args().output)
