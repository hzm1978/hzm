"""Local-first views must work without a quote, a running worker or a market tick."""
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd
from PySide6.QtCore import QCoreApplication, QEvent, QTimer
from PySide6.QtWidgets import QApplication

from futures_replay.data_repo import DataRepository
from futures_replay.live_store import period_folder
from futures_replay.realtime_data import RealtimeTarget
from futures_replay.ui.background_live import BackgroundLiveController
from futures_replay.ui.live_chart_dialog import LiveChartDialog
from futures_replay.ui.local_history import load_local_history


def bars(dates, price=4200.):
    dates=pd.DatetimeIndex(dates);close=price+np.arange(len(dates),dtype=float)
    return pd.DataFrame(dict(datetime=dates,open=close-.5,high=close+1,low=close-1,
        close=close,volume=100.,amount=0.,open_interest=1000.,trading_day=dates.strftime('%Y-%m-%d')))


def write_local(root,symbol='IF',period='1d',frame=None):
    target=RealtimeTarget(Path(root),'主力连续',symbol,'主连',f'KQ.m@CFFEX.{symbol}')
    folder=Path(root)/'qihuozldata_parquet'/symbol/'主连';folder.mkdir(parents=True,exist_ok=True)
    if frame is None:frame=bars(pd.bdate_range(end='2026-11-03',periods=80))
    frame.to_parquet(folder/f'{period}.parquet',index=False)
    return target,frame


class LocalHistoryTests(unittest.TestCase):
    def test_daily_preview_uses_native_daily_without_reading_the_minute_library(self):
        with tempfile.TemporaryDirectory() as directory:
            target,frame=write_local(directory);path=Path(directory)/'qihuozldata_parquet/IF/主连/1d.parquet'
            original=path.read_bytes()
            with patch.object(DataRepository,'load_minutes',side_effect=AssertionError('unnecessary minute scan')):
                got=load_local_history(target,'1d',limit=20)
            self.assertEqual(len(got),20)
            self.assertEqual(got.datetime.iloc[-1],frame.datetime.iloc[-1])
            self.assertEqual(path.read_bytes(),original)

    def test_latest_closed_live_shard_is_merged_with_native_history(self):
        with tempfile.TemporaryDirectory() as directory:
            target,_=write_local(directory,frame=bars(['2026-10-30','2026-11-02']))
            path=period_folder(directory,target.source,target.symbol,target.contract,'1d')/'2026.parquet'
            path.parent.mkdir(parents=True);bars(['2026-11-03'],price=4350).to_parquet(path,index=False)
            got=load_local_history(target,'1d')
            self.assertEqual(got.datetime.tolist(),list(pd.to_datetime(['2026-10-30','2026-11-02','2026-11-03'])))
            self.assertEqual(got.close.iloc[-1],4350)

    def test_weekend_and_future_replay_examples_do_not_become_latest_market_bars(self):
        with tempfile.TemporaryDirectory() as directory:
            target,_=write_local(directory,frame=bars(['2026-10-30','2026-10-31','2026-11-02','2026-11-03','2026-11-05']))
            with patch('futures_replay.ui.local_history.shanghai_time',return_value=pd.Timestamp('2026-11-04 08:00')):
                got=load_local_history(target,'1d')
            self.assertEqual(got.datetime.tolist(),list(pd.to_datetime(['2026-10-30','2026-11-02','2026-11-03'])))

    def test_daily_preview_can_be_derived_when_only_local_minutes_exist(self):
        with tempfile.TemporaryDirectory() as directory:
            target,frame=write_local(directory,period='1min',frame=bars(pd.date_range('2026-11-03 14:50',periods=10,freq='min')))
            got=load_local_history(target,'1d')
            self.assertEqual(len(got),1)
            self.assertEqual(got.datetime.iloc[-1],pd.Timestamp('2026-11-03'))
            self.assertEqual(got.close.iloc[-1],frame.close.iloc[-1])

    def test_missing_intraday_period_can_be_derived_from_stored_minutes(self):
        with tempfile.TemporaryDirectory() as directory:
            target,frame=write_local(directory,period='1min',frame=bars(pd.date_range('2026-11-03 14:50',periods=10,freq='min')))
            got=load_local_history(target,'5min')
            self.assertEqual(got.datetime.tolist(),list(pd.to_datetime(['2026-11-03 14:50','2026-11-03 14:55'])))
            self.assertEqual(got.close.iloc[-1],frame.close.iloc[-1])


class LocalFollowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def spin(self,predicate):
        end=time.monotonic()+5
        while not predicate() and time.monotonic()<end:
            self.app.processEvents();threading.Event().wait(.005)
        self.assertTrue(predicate())

    def view(self):
        controller=BackgroundLiveController();view=LiveChartDialog(controller);view.resize(1100,700)
        return controller,view

    def dispose(self,controller,view):
        view.close()
        self.spin(lambda:view.history._active is None)
        controller.shutdown();view.deleteLater();controller.deleteLater()
        QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete);self.app.processEvents()

    def test_closed_market_if_daily_shows_local_latest_at_the_right_without_a_worker(self):
        with tempfile.TemporaryDirectory() as directory:
            target,frame=write_local(directory);controller,view=self.view()
            try:
                view.configure(target,'1d');self.spin(lambda:not view.chart.df.empty)
                view.grab()
                self.assertIsNone(controller.process)
                self.assertEqual(view.chart.df.datetime.iloc[-1],pd.Timestamp('2026-11-03'))
                self.assertEqual(view.chart.current_idx,len(frame)-1)
                self.assertEqual(view.chart._end,len(frame))
                self.assertEqual(view.chart.end_offset,0)
                self.assertTrue(view.chart.follow_latest)
                self.assertGreater(view.chart._index_x(len(frame)-1),view.chart.width()*.85)
                self.assertIsNone(view.chart.partial)
                self.assertIn('本地最新K线',view.status.text())
                self.assertIn('v8.2.0',view.windowTitle())
            finally:self.dispose(controller,view)

    def test_connection_error_does_not_prevent_the_local_history_from_appearing(self):
        with tempfile.TemporaryDirectory() as directory:
            target,_=write_local(directory);controller,view=self.view()
            try:
                view.configure(target,'1d')
                controller.statusChanged.emit('认证失败：构造的连接错误')
                self.spin(lambda:not view.chart.df.empty)
                self.assertIn('认证失败',view.status.text())
                self.assertIn('本地最新K线',view.status.text())
            finally:self.dispose(controller,view)

    def test_empty_or_stale_replies_keep_latest_bars_and_short_live_windows_keep_history(self):
        with tempfile.TemporaryDirectory() as directory:
            target,_=write_local(directory);controller,view=self.view()
            try:
                view.configure(target,'1d');self.spin(lambda:not view.chart.df.empty)
                old=view.chart.df
                view._preview(dict(tq_symbol=target.tq_symbol,period='1d',bars=[]))
                self.assertIs(view.chart.df,old)
                view._preview(dict(tq_symbol=target.tq_symbol,period='1d',
                    bars=bars(['2026-11-02']).to_dict('records'),received_at='2026-11-04 16:00'))
                self.assertIs(view.chart.df,old)
                self.assertIn('保留较新的已有K线',view.status.text())
                view.chart.set_follow_latest(False);view.chart.end_offset=10
                view._request()
                self.assertIs(view.chart.df,old)
                self.assertTrue(view.chart.follow_latest);self.assertEqual(view.chart.end_offset,0)
                self.spin(lambda:view.history._active is None)
                changed=bars(pd.bdate_range(end='2026-11-03',periods=80),price=5000)
                write_local(directory,frame=changed)
                view._request();self.spin(lambda:view.chart.df.close.iloc[-1]==changed.close.iloc[-1])
                view._preview(dict(tq_symbol=target.tq_symbol,period='1d',
                    bars=bars(['2026-11-03'],price=5600).to_dict('records'),received_at='2026-11-04 16:00'))
                self.assertEqual(len(view.chart.df),80)
                self.assertEqual(view.chart.df.close.iloc[-1],5600)
            finally:self.dispose(controller,view)

    def test_late_local_read_cannot_overwrite_a_newer_realtime_preview(self):
        with tempfile.TemporaryDirectory() as directory:
            target,old=write_local(directory);controller,view=self.view();release=threading.Event();started=threading.Event()
            def slow(*args):
                started.set();release.wait(4);return old
            try:
                with patch('futures_replay.ui.local_history.load_local_history',new=slow):
                    view.configure(target,'1d');self.spin(started.is_set)
                    frame=bars(['2026-11-02','2026-11-03','2026-11-04'],price=5500)
                    view._preview(dict(tq_symbol=target.tq_symbol,period='1d',
                        bars=frame.to_dict('records'),received_at='2026-11-04 16:00',quote_time='2026-11-04 15:00'))
                    release.set();self.spin(lambda:view.history._active is None)
                self.assertEqual(view.chart.df.datetime.iloc[-1],pd.Timestamp('2026-11-04'))
                self.assertEqual(view.chart.df.close.iloc[-1],5502)
                self.assertNotIn('本地最新K线',view.status.text())
            finally:release.set();self.dispose(controller,view)

    def test_rapid_period_changes_discard_old_reads_and_conflate_pending_work(self):
        with tempfile.TemporaryDirectory() as directory:
            target,day=write_local(directory);controller,view=self.view();release=threading.Event();started=threading.Event();seen=[]
            minute=bars(['2026-11-03 14:55'],price=4400)
            def read(target,period,*args):
                seen.append(period)
                if period=='1d':started.set();release.wait(4);return day
                return minute
            try:
                with patch('futures_replay.ui.local_history.load_local_history',new=read):
                    view.configure(target,'1d');self.spin(started.is_set)
                    view.period.setCurrentIndex(view.period.findData('1min'))
                    view.period.setCurrentIndex(view.period.findData('5min'))
                    release.set();self.spin(lambda:not view.chart.df.empty and view.chart.df.close.iloc[-1]==4400)
                self.assertEqual(seen,['1d','5min'])
                self.assertEqual(view.chart.df.datetime.iloc[-1],pd.Timestamp('2026-11-03 14:55'))
            finally:release.set();self.dispose(controller,view)

    def test_slow_local_read_does_not_block_gui_timer_or_update_a_closed_view(self):
        with tempfile.TemporaryDirectory() as directory:
            target,day=write_local(directory);controller,view=self.view();release=threading.Event();started=threading.Event();tick=[]
            def read(*args):started.set();release.wait(4);return day
            try:
                with patch('futures_replay.ui.local_history.load_local_history',new=read):
                    view.configure(target,'1d');QTimer.singleShot(0,lambda:tick.append(True))
                    self.spin(lambda:bool(tick) and started.is_set())
                    view.close();release.set();self.spin(lambda:view.history._active is None)
                self.assertTrue(view.chart.df.empty)
            finally:release.set();self.dispose(controller,view)

    def test_contract_change_cannot_receive_the_previous_contracts_local_result(self):
        with tempfile.TemporaryDirectory() as directory:
            target,day=write_local(directory);other,_=write_local(directory,symbol='IH')
            controller,view=self.view();release=threading.Event();started=threading.Event()
            def read(target,period,*args):
                if target.symbol=='IF':started.set();release.wait(4);return day
                return bars(['2026-11-03'],price=2600)
            try:
                with patch('futures_replay.ui.local_history.load_local_history',new=read):
                    view.configure(target,'1d');self.spin(started.is_set)
                    view.configure(other,'1d');release.set()
                    self.spin(lambda:not view.chart.df.empty and view.chart.df.close.iloc[-1]==2600)
                self.assertEqual(view.target.symbol,'IH')
                self.assertIn('IH',view.title.text())
            finally:release.set();self.dispose(controller,view)


if __name__=='__main__':unittest.main()
