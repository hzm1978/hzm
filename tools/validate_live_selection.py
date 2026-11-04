"""Drive actual Qt selectors and the production worker with an offline SDK."""
import argparse
import json
import os
from pathlib import Path
import sys
import threading
import time

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
import pandas as pd
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication
from futures_replay import __version__
from futures_replay.data_repo import DataRepository
from test_live_selection import live_window


def validate(output):
    output.mkdir(parents=True,exist_ok=True)
    app=QApplication.instance() or QApplication([]);app.setFont(QFont('Noto Sans CJK SC',10))
    def spin(predicate):
        end=time.monotonic()+12
        while not predicate() and time.monotonic()<end:
            app.processEvents();threading.Event().wait(.01)
        assert predicate(),'实时联动验证超时'
    def capture(live,name,label):
        live.title.setText(f'【v{__version__} 构造行情·模拟服务端】{label} · '+live.title.text().split('】')[-1])
        live.resize(1250,800);app.processEvents()
        assert live.grab().save(str(output/name))
    with live_window() as (window,clients,saved):
        spin(lambda:window.engine is not None)
        window.engine.jump_bar(5,to_end_of_bar=True);window._refresh_all_panels()
        engine=window.engine;before=engine.current().timestamp;account=window.account.to_dict()
        # No update button: opening the view must establish the connection.
        window.live_view_action.setChecked(True)
        spin(lambda:window._live_chart is not None and window._live_chart._received_live)
        live=window._live_chart;pid=window.live_controller.process.processId()
        assert pid and len(clients)==1
        assert live.chart.df.datetime.iloc[-1]==pd.Timestamp('2026-11-03')
        assert window.engine is engine and engine.current().timestamp==before
        assert window.account.to_dict()==account
        capture(live,'automatic.png','打开即连接并加载最新')
        live.volume.setChecked(False)
        live.period.setCurrentIndex(live.period.findData('5min'))
        window.symbol_combo.setCurrentIndex(window.symbol_combo.findData('IH'))
        spin(lambda:live.target.symbol=='IH' and live._received_live)
        assert live.period.currentData()=='5min' and not live.chart.show_volume
        ih_time=window.engine.current().timestamp
        spin(lambda:('主力连续','IH') in window._live_dirty)
        assert window.engine.current().timestamp==ih_time
        live.period.setCurrentIndex(live.period.findData('1d'))
        spin(lambda:live._received_live)
        assert 'VOLUME' not in live.chart._active_subpanels()
        capture(live,'combo_volume_hidden.png','顶部下拉切到IH，成交量隐藏')
        row=window._rebuild_main_list_rows()['SN']
        window.main_list.cellClicked.emit(row,1)
        spin(lambda:live.target.symbol=='SN' and live._received_live)
        spin(lambda:('主力连续','SN') in window._live_dirty)
        assert window.symbol=='SN' and live.period.currentData()=='1d' and not live.volume.isChecked()
        assert window.live_controller.process.processId()==pid
        previous=live.chart.df.copy()
        window.live_controller.previewReady.emit(dict(tq_symbol='KQ.m@CFFEX.IH',period='1d',bars=[{'close':999999}]))
        pd.testing.assert_frame_equal(live.chart.df,previous)
        live.volume.setChecked(True);assert live.chart.show_volume
        capture(live,'list_volume_visible.png','主连列表切到SN，成交量显示')
        fresh=DataRepository(window.repo.root)
        for symbol in ('IH','SN'):
            assert not fresh.load_bars('主力连续',symbol,'1d','主连').empty
        live.volume.setChecked(False);window._save_now()
        assert saved[-1]['ui']['live_volume'] is False
        window.live_view_action.setChecked(False);window.live_view_action.setChecked(True)
        spin(lambda:live._received_live)
        assert not live.volume.isChecked() and window.live_controller.process.processId()==pid
        result=dict(result='passed',version=__version__,pandas=pd.__version__,
            data='构造IF/IH/SN行情；生产子进程使用模拟SDK，未实际联网',
            checks=['打开窗口自动连接','顶部下拉切品种','主连列表跨交易所切品种','切换不重新登录或重启进程',
                '新选择的品种保存到本地','实时窗口周期独立保留','成交量可隐藏/显示/保存并恢复',
                '迟到的旧品种预览不覆盖新图','原回放不跳到最新，模拟账户不变'])
    (output/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True)
    validate(parser.parse_args().output)
