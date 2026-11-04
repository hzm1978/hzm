"""Connection phases, safe diagnostics, native window controls and retries."""
from pathlib import Path
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import patch

import pandas as pd
from PySide6.QtCore import QCoreApplication,QEvent,QTimer,Qt
from PySide6.QtWidgets import QApplication,QPushButton

from futures_replay.realtime_data import create_tq_api
from futures_replay.ui.background_live import BackgroundLiveController,background_python,redact_diagnostic
from futures_replay.ui.live_chart_dialog import LiveChartDialog
from test_local_follow import bars,write_local
from test_live_selection import live_window


class ConnectionUtilityTests(unittest.TestCase):
    def test_windows_windowed_python_uses_the_console_interpreter_for_worker_pipes(self):
        self.assertEqual(background_python('/env/Scripts/pythonw.exe'),'/env/Scripts/python.exe')
        self.assertEqual(background_python('/env/Scripts/PYTHONW.EXE'),'/env/Scripts/python.exe')
        self.assertEqual(background_python(sys.executable),sys.executable)

    def test_diagnostics_hide_known_credentials_bearer_json_tokens_and_jwt(self):
        value='user=private-user password="private-pass" Authorization: Bearer bearer-value access_token=token-value eyJhbGci.payload.signature proxy=http://proxy-user:proxy-pass@example.invalid'
        safe=redact_diagnostic(value,('private-user','private-pass'))
        for secret in ('private-user','private-pass','bearer-value','token-value','eyJhbGci.payload.signature','proxy-user','proxy-pass'):
            self.assertNotIn(secret,safe)
        self.assertIn('[已隐藏]',safe)

    def test_sdk_authentication_progress_distinguishes_success_and_rejection(self):
        for reject in (False,True):
            with self.subTest(reject=reject):
                fake=types.ModuleType('tqsdk');stages=[]
                class Auth:
                    def __init__(self,*args):pass
                    def login(self):
                        if reject:raise ValueError('authentication rejected')
                class Api:
                    def __init__(self,*,auth,**kwargs):auth.login()
                fake.TqAuth=Auth;fake.TqApi=Api;fake.TqSim=object
                with patch.dict(sys.modules,{'tqsdk':fake}):
                    if reject:
                        with self.assertRaisesRegex(ValueError,'rejected'):
                            create_tq_api('private-user','private-pass',object(),progress_cb=lambda stage,text:stages.append(stage))
                    else:
                        create_tq_api('private-user','private-pass',object(),progress_cb=lambda stage,text:stages.append(stage))
                self.assertIn('authenticating',stages)
                self.assertEqual('authenticated' in stages,not reject)


WAIT_CHILD='''import json,sys
cfg=json.loads(sys.stdin.readline())
def send(kind,**kw):print('FRLIVE '+json.dumps(dict(kind=kind,**kw)),flush=True)
send('status',stage='authenticating',text='正在认证天勤账号')
for line in sys.stdin:
 if json.loads(line).get('command')=='stop':break
'''
READY_CHILD=WAIT_CHILD.replace("send('status',stage='authenticating',text='正在认证天勤账号')",
    "send('status',stage='api_ready',text='行情连接已建立')")


class ConnectionGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def spin(self,predicate):
        end=time.monotonic()+6
        while not predicate() and time.monotonic()<end:
            self.app.processEvents();threading.Event().wait(.005)
        self.assertTrue(predicate())

    def start(self,child,timeout=90000):
        controller=BackgroundLiveController(program=sys.executable,arguments=['-u','-c',child],connection_timeout_ms=timeout)
        controller.start(dict(mode='current',root='/tmp',source='主力连续',symbol='JM',contract='主连',
                              tq_symbol='KQ.m@DCE.jm'),'private-user','sensitive-pass')
        return controller

    def dispose(self,controller):
        controller.shutdown();controller.deleteLater()
        QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)

    def test_login_that_never_completes_times_out_and_keeps_the_reason(self):
        controller=self.start(WAIT_CHILD,timeout=200)
        try:
            self.spin(lambda:controller.process is None)
            self.assertIn('实时连接超过',controller._last_error)
            self.assertIn('实时连接超过',controller.last_status)
            self.assertNotIn('sensitive-pass',controller.diagnostic_text())
        finally:self.dispose(controller)

    def test_api_ready_stops_login_timeout_while_history_can_continue_loading(self):
        controller=self.start(READY_CHILD,timeout=500)
        try:
            self.spin(lambda:controller._service_ready)
            elapsed=[];QTimer.singleShot(650,lambda:elapsed.append(True))
            self.spin(lambda:bool(elapsed))
            self.assertTrue(controller.is_running())
            self.assertEqual(controller._last_error,'')
            self.assertIn('行情连接已建立',controller.connection_summary())
        finally:self.dispose(controller)

    def test_forced_stop_of_a_hung_initializer_preserves_the_original_timeout_reason(self):
        child="import json,sys,time\njson.loads(sys.stdin.readline())\ntime.sleep(20)\n"
        controller=self.start(child,timeout=200);controller._guard.setInterval(100)
        try:
            self.spin(lambda:controller.process is None)
            self.assertIn('实时连接超过',controller._last_error)
            self.assertIn('实时连接超过',controller.connection_summary())
            self.assertNotIn('Process crashed',controller._last_error)
        finally:self.dispose(controller)

    def test_worker_stderr_and_exit_reason_survive_with_split_credentials_redacted(self):
        child='''import json,sys,time
cfg=json.loads(sys.stdin.readline())
sys.stderr.write('RuntimeError: password='+cfg['password'][:4]);sys.stderr.flush()
time.sleep(.05)
sys.stderr.write(cfg['password'][4:]+' access_token=private-token');sys.stderr.flush()
sys.exit(7)
'''
        controller=self.start(child)
        try:
            self.spin(lambda:controller.process is None)
            details=controller.diagnostic_text()
            self.assertIn('RuntimeError',details)
            self.assertIn('代码7',details)
            self.assertNotIn('sensitive-pass',details)
            self.assertNotIn('private-token',details)
        finally:self.dispose(controller)

    def test_minimize_maximize_controls_and_restoration_keep_latest_preview(self):
        with tempfile.TemporaryDirectory() as directory:
            target,_=write_local(directory);controller=BackgroundLiveController();view=LiveChartDialog(controller)
            view.trend.setChecked(False)
            try:
                view.configure(target,'1d');self.spin(lambda:not view.chart.df.empty)
                flags=view.windowFlags()
                self.assertTrue(flags & Qt.WindowType.WindowMinimizeButtonHint)
                self.assertTrue(flags & Qt.WindowType.WindowMaximizeButtonHint)
                view.showMaximized();self.app.processEvents();self.assertTrue(view.isMaximized())
                view.showNormal();view.showMinimized();self.app.processEvents();self.assertTrue(view.isMinimized())
                view._preview(dict(tq_symbol=target.tq_symbol,period='1d',bars=bars(['2026-11-04'],price=4400).to_dict('records'),
                    received_at='2026-11-04 10:00',quote_time='2026-11-04 10:00'))
                view.showNormal();self.app.processEvents()
                self.assertEqual(view.chart.df.datetime.iloc[-1],pd.Timestamp('2026-11-04'))
                self.assertEqual(view.chart.df.close.iloc[-1],4400)
            finally:
                view.close();self.spin(lambda:view.history._active is None)
                view.deleteLater();self.dispose(controller)

    def test_reconnect_button_keeps_all_mode_and_original_replay_while_replacing_worker(self):
        with live_window() as (window,clients,saved):
            self.spin(lambda:window.engine is not None)
            window._start_all_live();self.spin(lambda:len(window.live_controller.context.get('targets',[]))==3)
            window.live_view_action.setChecked(True)
            self.spin(lambda:window._live_chart is not None and window._live_chart._received_live)
            live=window._live_chart;old_pid=window.live_controller.process.processId()
            engine=window.engine;stamp=engine.current().timestamp;account=window.account.to_dict()
            live.volume.setChecked(False)
            button=next(b for b in live.findChildren(QPushButton) if b.text()=='重新连接')
            button.click()
            self.spin(lambda:window.live_controller.process is not None and window.live_controller.process.processId()!=old_pid
                      and live._received_live and window.live_controller._collection_started)
            self.assertEqual(window.live_controller.mode,'all')
            self.assertIs(window.engine,engine);self.assertEqual(engine.current().timestamp,stamp)
            self.assertEqual(window.account.to_dict(),account)
            self.assertFalse(live.volume.isChecked())

    def test_known_contract_identity_survives_worker_start_until_server_rechecks_it(self):
        controller=BackgroundLiveController(program=sys.executable,arguments=['-u','-c',READY_CHILD])
        try:
            controller._remember_targets(dict(targets=[dict(root='/tmp/other-library',source='原始行情',
                symbol='JM',contract='JM2612',tq_symbol='DCE.jm2612')]))
            controller.start(dict(mode='current',root='/tmp/base-library',source='主力连续',symbol='JM',
                contract='主连',tq_symbol='KQ.m@DCE.jm'),'private-user','sensitive-pass')
            self.spin(lambda:controller._service_ready)
            self.assertEqual(controller.resolved_target('/tmp/other-library','原始行情','JM').contract,'JM2612')
        finally:self.dispose(controller)


if __name__=='__main__':unittest.main()
