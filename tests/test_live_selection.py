"""Product following, automatic connection and optional volume in a live window."""
import contextlib
import io
import json
from pathlib import Path
import queue
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import pandas as pd
from PySide6.QtCore import QCoreApplication,QEvent
from PySide6.QtWidgets import QApplication

from futures_replay.data_repo import DataRepository
from futures_replay.live_service import activate_view_target,collect,subscribe_target,target_info
from futures_replay.realtime_data import RealtimeTarget
from futures_replay.ui.background_live import BackgroundLiveController,TqLoginDialog
from futures_replay.ui.main_window import MainWindow
from test_background_live import MultiApi
from test_local_follow import bars,write_local


def descriptor(root,symbol,source='主力连续'):
    exchange='SHFE' if symbol in {'AG','SN'} else 'CFFEX'
    code=symbol.lower() if exchange=='SHFE' else symbol
    return dict(root=str(root),source=source,symbol=symbol,contract='主连',tq_symbol=f'KQ.m@{exchange}.{code}')


class FollowCollectionTests(unittest.TestCase):
    def collect_events(self,root,commands):
        stop=threading.Event();api=MultiApi(stop);output=io.StringIO();created=[]
        cfg=dict(**descriptor(root,'IF'),mode='current',username='offline-user',password='offline-secret')
        work=queue.Queue()
        for cmd in commands:work.put(cmd)
        def factory(*args):created.append(True);return api
        with patch('futures_replay.live_service.lower_priority'),contextlib.redirect_stdout(output):
            collect(cfg,api_factory=factory,command_queue=work,stop_event=stop)
        self.assertEqual(len(created),1)
        self.assertNotIn('offline-secret',output.getvalue())
        return api,[json.loads(line[7:]) for line in output.getvalue().splitlines() if line.startswith('FRLIVE ')]

    def command(self,root,symbol,enabled=True):
        item=descriptor(root,symbol)
        return dict(command='view',enabled=enabled,tq_symbol=item['tq_symbol'],period='1d',target=item)

    def test_current_follow_switches_collection_and_saves_the_new_product_without_login_again(self):
        with tempfile.TemporaryDirectory() as directory:
            api,events=self.collect_events(directory,[self.command(directory,'IH')])
            self.assertEqual({e['symbol'] for e in events if e['kind']=='saved'},{'IH'})
            self.assertTrue(any(e['kind']=='preview' and e['tq_symbol']=='KQ.m@CFFEX.IH' for e in events))
            repo=DataRepository(Path(directory))
            self.assertFalse(repo.load_bars('主力连续','IH','1d','主连').empty)
            self.assertFalse((Path(directory)/'.futures_realtime/主力连续/IF').exists())
            self.assertEqual(set(api.lengths),{10000})

    def test_all_mode_reuses_an_existing_product_and_preserves_other_subscriptions(self):
        with tempfile.TemporaryDirectory() as directory:
            api=MultiApi(threading.Event())
            active=[subscribe_target(api,RealtimeTarget(Path(directory),**{k:v for k,v in descriptor(directory,s).items() if k!='root'})) for s in ('IF','IH')]
            requests=len(api.requests)
            item,changed=activate_view_target(api,active,descriptor(directory,'IH'),'all')
            self.assertFalse(changed);self.assertEqual(len(api.requests),requests)
            self.assertEqual({s['target'].symbol for s in active},{'IF','IH'})
            self.assertEqual(item['target'].symbol,'IH')

    def test_all_mode_keeps_base_library_and_replaces_only_the_extra_visible_subscription(self):
        with tempfile.TemporaryDirectory() as directory:
            api=MultiApi(threading.Event());root=Path(directory)
            base=subscribe_target(api,RealtimeTarget(root/'base','主力连续','IF','主连','KQ.m@CFFEX.IF'))
            active=[base]
            activate_view_target(api,active,descriptor(root/'other','IH'),'all')
            activate_view_target(api,active,descriptor(root/'other','SN'),'all')
            self.assertEqual(len(active),2)
            self.assertIs(active[0],base)
            self.assertEqual(active[1]['target'].symbol,'SN')
            self.assertEqual(active[1]['target'].root,root/'other')

    def test_raw_follow_resolves_actual_main_before_saving_instead_of_the_placeholder(self):
        with tempfile.TemporaryDirectory() as directory:
            api=MultiApi(threading.Event());active=[]
            info=descriptor(directory,'AG','原始行情');info['contract']='当前主力（待解析）'
            item,changed=activate_view_target(api,active,info,'current')
            self.assertTrue(changed)
            self.assertEqual((item['target'].contract,item['target'].tq_symbol),('AG2612','SHFE.ag2612'))
            original=api.get_quote
            def rolled(symbol):
                quote=original(symbol)
                if symbol=='KQ.m@SHFE.ag':quote['underlying_symbol']='SHFE.ag2701'
                return quote
            api.get_quote=rolled
            info.update(contract='AG2612',tq_symbol='SHFE.ag2612')
            current,_=activate_view_target(api,active,info,'current')
            self.assertEqual((current['target'].contract,current['target'].tq_symbol),('AG2701','SHFE.ag2701'))

    def test_rapid_view_changes_only_subscribe_the_last_queued_product(self):
        with tempfile.TemporaryDirectory() as directory:
            api,events=self.collect_events(directory,[self.command(directory,'IH'),self.command(directory,'SN')])
            self.assertNotIn('KQ.m@CFFEX.IH',{s for s,_ in api.requests})
            self.assertEqual({e['symbol'] for e in events if e['kind']=='saved'},{'SN'})

    def test_closing_before_queued_switch_leaves_background_collection_running(self):
        with tempfile.TemporaryDirectory() as directory:
            api,events=self.collect_events(directory,[self.command(directory,'IH'),dict(command='view',enabled=False)])
            self.assertEqual({s for s,_ in api.requests},{'KQ.m@CFFEX.IF'})
            self.assertEqual({e['symbol'] for e in events if e['kind']=='saved'},{'IF'})
            self.assertFalse(any(e['kind']=='preview' for e in events))

    def test_resolved_contracts_are_scoped_to_both_library_and_source(self):
        controller=BackgroundLiveController()
        try:
            with tempfile.TemporaryDirectory() as directory:
                first=descriptor(Path(directory)/'first','IF','原始行情');first['contract']='IF2611'
                second=descriptor(Path(directory)/'second','IF','原始行情');second['contract']='IF2612'
                controller._remember_targets(dict(targets=[first,second,descriptor(Path(directory)/'first','IF')]))
                self.assertEqual(controller.resolved_target(Path(directory)/'first','原始行情','IF').contract,'IF2611')
                self.assertEqual(controller.resolved_target(Path(directory)/'second','原始行情','IF').contract,'IF2612')
                self.assertEqual(controller.resolved_target(Path(directory)/'first','主力连续','IF').contract,'主连')
        finally:controller.deleteLater()


# The real production collector runs inside a real QProcess with an offline
# in-memory SDK substitute. No TqAuth, external service or real credentials.
STREAM_CHILD=r'''
import re,time
import numpy as np,pandas as pd
import futures_replay.live_service as service
class Api:
 def __init__(self):self.frames={}
 def product(self,symbol):return re.match('[A-Za-z]+',symbol.rsplit('.',1)[-1]).group().upper()
 def get_quote(self,symbol):
  product=self.product(symbol);exchange=symbol.split('@')[-1].split('.')[0]
  code=product if exchange in ('CFFEX','CZCE') else product.lower()
  return dict(datetime='2026-11-03 15:00:00',last_price=dict(IF=4200,IH=2600,SN=300000).get(product,5000)+80,
      underlying_symbol=exchange+'.'+code+'2612',trading_time={'day':[['09:00:00','15:00:00']]})
 def get_kline_serial(self,symbol,seconds,data_length):
  key=symbol,seconds
  if key not in self.frames:
   product=self.product(symbol);base=dict(IF=4200,IH=2600,SN=300000).get(product,5000)
   dates=pd.bdate_range(end='2026-11-03',periods=80) if seconds==86400 else pd.date_range('2026-11-03 10:30',periods=6,freq=pd.Timedelta(seconds=seconds))
   close=base+np.arange(len(dates),dtype=float)
   self.frames[key]=pd.DataFrame(dict(id=np.arange(len(dates)),datetime=dates.tz_localize('Asia/Shanghai').as_unit('ns').asi8,
       open=close-.5,high=close+1,low=close-1,close=close,volume=100.,open_oi=5000.,close_oi=5000.))
  return self.frames[key]
 def is_serial_ready(self,frame):return True
 def wait_update(self,deadline):time.sleep(.025);return True
 def close(self):pass
service.create_tq_api=lambda username,password,loop:Api()
service.main()
'''


def product_fixture(root):
    for symbol,exchange,price in [('IF','CFFEX',4200),('IH','CFFEX',2600),('SN','SHFE',300000)]:
        write_local(root,symbol,frame=bars(pd.bdate_range(end='2026-11-02',periods=80),price))
        raw=root/'qihushuju_parquet'/symbol
        (raw/'1min').mkdir(parents=True);(raw/'1d').mkdir()
        (raw/'数据说明.md').write_text(f'# {symbol} 构造品种\n交易所：{exchange}\n',encoding='utf-8')


@contextlib.contextmanager
def live_window(state=None):
    old_argv=sys.argv[:];clients=[];saved=[]
    app=QApplication.instance() or QApplication([])
    with tempfile.TemporaryDirectory() as directory:
        root=Path(directory);product_fixture(root);sys.argv[0]=str(root/'run.py')
        def factory(parent):
            value=BackgroundLiveController(parent,program=sys.executable,arguments=['-X','utf8','-u','-c',STREAM_CHILD])
            clients.append(value);return value
        with patch('futures_replay.ui.main_window.load_state',return_value=state or {}), \
             patch('futures_replay.ui.main_window.load_replay_heads',return_value={}), \
             patch('futures_replay.ui.main_window.load_main_list_sort',return_value={}), \
             patch('futures_replay.ui.main_window.save_state',side_effect=saved.append), \
             patch('futures_replay.ui.main_window.save_replay_heads'), \
             patch('futures_replay.ui.main_window.save_main_list_sort'), \
             patch('futures_replay.ui.main_window.component_issue',return_value=''), \
             patch('futures_replay.ui.background_live.LocalLoginStore',
                   **{'return_value.available':False,'return_value.load.return_value':None}), \
             patch('futures_replay.ui.main_window.load_saved_login',return_value=('offline-user','offline-secret')), \
             patch('futures_replay.ui.main_window.BackgroundLiveController',side_effect=factory):
            window=MainWindow(root);window.resize(1400,900)
            window.source_combo.setCurrentText('主力连续')
            window.panels[0].period.set_period('1d');window.panels[0].apply_options()
            window.show();app.processEvents()
            try:yield window,clients,saved
            finally:
                window.close();window.deleteLater()
                QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete);app.processEvents()
    sys.argv[:]=old_argv


class MainWindowFollowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def spin(self,predicate):
        end=time.monotonic()+8
        while not predicate() and time.monotonic()<end:
            self.app.processEvents();threading.Event().wait(.005)
        self.assertTrue(predicate())

    def test_open_auto_connects_saved_login_without_moving_replay_and_volume_choice_survives_reopen(self):
        with live_window() as (window,clients,saved):
            self.spin(lambda:window.engine is not None)
            window.engine.jump_bar(5,to_end_of_bar=True);window._refresh_all_panels()
            engine=window.engine;before=engine.current().timestamp;account=window.account.to_dict()
            window.live_view_action.setChecked(True)
            self.spin(lambda:window._live_chart is not None and window._live_chart._received_live)
            live=window._live_chart;pid=window.live_controller.process.processId()
            self.assertEqual(live.chart.df.datetime.iloc[-1],pd.Timestamp('2026-11-03'))
            self.assertIs(window.engine,engine);self.assertEqual(engine.current().timestamp,before)
            self.assertEqual(window.account.to_dict(),account)
            self.assertEqual(len(clients),1)
            live.volume.setChecked(False);live.grab()
            self.assertNotIn('VOLUME',live.chart._active_subpanels())
            window._save_now();self.assertFalse(saved[-1]['ui']['live_volume'])
            window.live_view_action.setChecked(False);window.live_view_action.setChecked(True)
            self.spin(lambda:live._received_live)
            self.assertFalse(live.volume.isChecked());self.assertFalse(live.chart.show_volume)
            self.assertEqual(window.live_controller.process.processId(),pid)

    def test_top_combo_and_main_list_follow_product_keep_live_period_and_save_new_targets(self):
        with live_window() as (window,clients,saved):
            self.spin(lambda:window.engine is not None)
            window.live_view_action.setChecked(True)
            self.spin(lambda:window._live_chart is not None and window._live_chart._received_live)
            live=window._live_chart;pid=window.live_controller.process.processId()
            live.volume.setChecked(False);live.period.setCurrentIndex(live.period.findData('5min'))
            window.symbol_combo.setCurrentIndex(window.symbol_combo.findData('IH'))
            self.spin(lambda:live.target.symbol=='IH' and live._received_live)
            self.assertEqual(live.period.currentData(),'5min');self.assertFalse(live.chart.show_volume)
            before=window.engine.current().timestamp
            self.spin(lambda:('主力连续','IH') in window._live_dirty)
            self.assertEqual(window.engine.current().timestamp,before)
            row=window._rebuild_main_list_rows()['SN'];window.main_list.cellClicked.emit(row,1)
            self.spin(lambda:live.target.symbol=='SN' and live._received_live)
            self.spin(lambda:('主力连续','SN') in window._live_dirty)
            self.assertEqual(window.symbol,'SN');self.assertEqual(live.period.currentData(),'5min')
            self.assertFalse(live.volume.isChecked())
            self.assertEqual(window.live_controller.process.processId(),pid)
            fresh=DataRepository(window.repo.root)
            self.assertFalse(fresh.load_bars('主力连续','IH','1d','主连').empty)
            self.assertFalse(fresh.load_bars('主力连续','SN','1d','主连').empty)

    def test_opening_and_switching_while_all_mode_runs_does_not_replace_that_process(self):
        with live_window() as (window,clients,saved):
            self.spin(lambda:window.engine is not None)
            window._start_all_live()
            self.spin(lambda:len(window.live_controller.context.get('targets',[]))==3)
            pid=window.live_controller.process.processId()
            window.live_view_action.setChecked(True)
            self.spin(lambda:window._live_chart is not None and window._live_chart._received_live)
            window.symbol_combo.setCurrentIndex(window.symbol_combo.findData('IH'))
            self.spin(lambda:window._live_chart.target.symbol=='IH' and window._live_chart._received_live)
            self.assertEqual(window.live_controller.mode,'all')
            self.assertEqual(window.live_controller.process.processId(),pid)
            self.spin(lambda:len(window._live_dirty)==3)

    def test_volume_hidden_preference_restores_in_a_new_window(self):
        with live_window({'ui':{'live_volume':False}}) as (window,clients,saved):
            self.spin(lambda:window.engine is not None)
            window.live_view_action.setChecked(True)
            self.spin(lambda:window._live_chart is not None and window._live_chart._received_live)
            self.assertFalse(window._live_chart.volume.isChecked())
            self.assertFalse(window._live_chart.chart.show_volume)

    def test_cancelling_initial_login_keeps_local_bars_and_product_changes_do_not_prompt_again(self):
        with live_window() as (window,clients,saved):
            self.spin(lambda:window.engine is not None)
            with patch('futures_replay.ui.main_window.load_saved_login',return_value=None) as login, \
                 patch.object(TqLoginDialog,'exec',return_value=TqLoginDialog.DialogCode.Rejected) as prompt:
                window.live_view_action.setChecked(True)
                self.spin(lambda:prompt.called and not window._live_chart.chart.df.empty)
                self.assertIsNone(window.live_controller.process)
                self.assertIn('尚未登录',window._live_chart.status.text())
                window.symbol_combo.setCurrentIndex(window.symbol_combo.findData('IH'))
                self.spin(lambda:window._live_chart.target.symbol=='IH' and not window._live_chart.chart.df.empty)
                self.assertEqual(login.call_count,1)
                self.assertEqual(prompt.call_count,1)


if __name__=='__main__':unittest.main()
