"""Exercise real QProcess startup order and the selected-period data path."""
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

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication

from futures_replay.live_service import collect
from futures_replay.realtime_data import RealtimeTarget
from futures_replay.ui.background_live import BackgroundLiveController
from futures_replay.ui.live_chart_dialog import LiveChartDialog
from test_background_live import MultiApi


CHILD = '''import json,sys
cfg=json.loads(sys.stdin.readline())
def send(kind,**kw): print('FRLIVE '+json.dumps(dict(kind=kind,**kw)),flush=True)
if cfg.get('command') or 'username' not in cfg:
 send('error',text='config was not the first line');sys.exit(3)
send('started',count=1,targets=[])
for line in sys.stdin:
 cmd=json.loads(line)
 if cmd.get('command')=='stop': break
 if cmd.get('command')=='view' and cmd.get('enabled'):
  send('preview',tq_symbol=cmd['tq_symbol'],period=cmd['period'],bars=[])
send('stopped')
'''


class LiveStartupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def spin(self, predicate):
        end = time.monotonic()+5
        while not predicate() and time.monotonic() < end:
            self.app.processEvents(); threading.Event().wait(.005)
        self.assertTrue(predicate())

    def client(self):
        controller = BackgroundLiveController(program=sys.executable, arguments=['-u','-c',CHILD])
        controller.start(dict(root='/tmp', mode='current', source='主力连续', symbol='SM',
                              contract='主连', tq_symbol='KQ.m@CZCE.SM'), 'test-user', 'test-secret')
        return controller

    def dispose(self, controller):
        controller.shutdown(); controller.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_view_selected_before_process_started_follows_config_and_uses_latest_period(self):
        controller = self.client(); previews=[]
        controller.previewReady.connect(previews.append)
        try:
            controller.request_view('KQ.m@CZCE.SM','1min')
            controller.request_view('KQ.m@CZCE.SM','1d')
            self.spin(lambda: bool(previews) or controller.process is None)
            self.assertIsNotNone(controller.process,controller.last_status)
            self.assertEqual([p['period'] for p in previews],['1d'])
            self.assertIsNone(controller._payload)
        finally:
            self.dispose(controller)

    def test_closing_a_view_before_process_started_cancels_its_queued_enable(self):
        controller = self.client(); previews=[]; ready=[]
        controller.previewReady.connect(previews.append);controller.targetsReady.connect(ready.append)
        try:
            controller.request_view('KQ.m@CZCE.SM','1d')
            controller.request_view(enabled=False)
            self.spin(lambda: bool(ready) or controller.process is None)
            self.assertIsNotNone(controller.process,controller.last_status)
            self.assertFalse(previews)
            controller.request_view('KQ.m@CZCE.SM','1min')
            self.spin(lambda: bool(previews))
            self.assertEqual([p['period'] for p in previews],['1min'])
        finally:
            self.dispose(controller)

    def test_stop_before_process_started_keeps_config_first_and_exits_cleanly(self):
        controller=self.client()
        try:
            controller.request_view('KQ.m@CZCE.SM','1d')
            controller.stop()
            self.spin(lambda: controller.process is None)
            self.assertNotIn('config was not the first line',controller.last_status)
            self.assertEqual(controller._last_error,'')
        finally:
            self.dispose(controller)

    def test_visible_window_receives_backend_error_and_filters_other_view_statuses(self):
        controller=BackgroundLiveController();view=LiveChartDialog(controller)
        target=RealtimeTarget(Path('/tmp'),'主力连续','SM','主连','KQ.m@CZCE.SM')
        try:
            view.configure(target,'1d')
            controller.statusChanged.emit('认证失败：测试权限错误')
            self.assertIn('认证失败',view.status.text())
            controller.statusChanged.emit('免费行情后台运行中 · 1个品种')
            self.assertIn('认证失败',view.status.text())
            controller.viewStatusChanged.emit(dict(tq_symbol=target.tq_symbol,period='1min',text='wrong period'))
            self.assertNotIn('wrong period',view.status.text())
            controller.viewStatusChanged.emit(dict(tq_symbol=target.tq_symbol,period='1d',text='日线请求超时'))
            self.assertEqual(view.status.text(),'日线请求超时')
            controller.statusChanged.emit('后台实时更新已停止：测试断线')
            self.assertIn('测试断线',view.status.text())
        finally:
            view.close();view.deleteLater();self.dispose(controller)


class SelectedPeriodTests(unittest.TestCase):
    def run_service(self, api_type, period):
        with tempfile.TemporaryDirectory() as directory:
            stopped=threading.Event();api=api_type(stopped);commands=queue.Queue()
            commands.put(dict(command='view',enabled=True,tq_symbol='KQ.m@CZCE.SM',period=period))
            output=io.StringIO()
            config=dict(root=directory,source='主力连续',mode='current',symbol='SM',contract='主连',
                        tq_symbol='KQ.m@CZCE.SM',username='test-user',password='test-secret')
            with patch('futures_replay.live_service.lower_priority'),contextlib.redirect_stdout(output):
                collect(config,api_factory=lambda u,p,l:api,command_queue=commands,stop_event=stopped)
            self.assertNotIn('test-secret',output.getvalue())
            return [json.loads(line[7:]) for line in output.getvalue().splitlines() if line.startswith('FRLIVE ')]

    def test_ready_minute_view_does_not_wait_for_the_separate_daily_calendar(self):
        class SlowDaily(MultiApi):
            def is_serial_ready(self,frame):
                return frame is not self.serials['1d']
        events=self.run_service(SlowDaily,'1min')
        previews=[e for e in events if e['kind']=='preview']
        self.assertTrue(previews)
        self.assertTrue(previews[0]['bars'])
        self.assertFalse(any(e['kind']=='saved' for e in events))

    def test_subscription_error_names_the_selected_period_and_redacts_credentials(self):
        class FailingView(MultiApi):
            def get_kline_serial(self,symbol,seconds,data_length):
                if seconds==300: raise ValueError('permission failure test-secret')
                return super().get_kline_serial(symbol,seconds,data_length)
        events=self.run_service(FailingView,'5min')
        error=next(e for e in events if e['kind']=='view_error')
        self.assertEqual((error['tq_symbol'],error['period']),('KQ.m@CZCE.SM','5min'))
        self.assertIn('permission failure',error['text'])
        self.assertIn('[已隐藏]',error['text'])

    def test_pending_selected_serial_reports_waiting_instead_of_remaining_silent(self):
        class NotReady(MultiApi):
            def is_serial_ready(self,frame):return False
        events=self.run_service(NotReady,'1d')
        waiting=[e for e in events if e['kind']=='view_status' and '正在等待' in e['text']]
        self.assertTrue(waiting)
        self.assertEqual((waiting[0]['tq_symbol'],waiting[0]['period']),('KQ.m@CZCE.SM','1d'))


if __name__=='__main__':unittest.main()
