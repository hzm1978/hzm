"""Offline main-window validation: background current/all and optional view."""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
from PySide6.QtCore import QTimer, QCoreApplication, QEvent
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication,QToolButton

from validate_ui import fixture
from futures_replay.ui.main_window import MainWindow
from futures_replay.ui.background_live import BackgroundLiveController, TqLoginDialog

CHILD = r'''
import json,sys
from pathlib import Path
import numpy as np,pandas as pd
from futures_replay.realtime_data import RealtimeTarget,build_snapshot
from futures_replay.live_store import write_live_snapshot
from futures_replay.live_service import preview_payload
config=json.loads(sys.stdin.readline())
assert config.pop('username')=='offline-test'
assert config.pop('password')=='offline-secret'
def send(kind,**kw): print('FRLIVE '+json.dumps(dict(kind=kind,**kw),ensure_ascii=False),flush=True)
root=Path(config['root'])
day=pd.read_parquet(root/'qihuozldata_parquet/SN/主连/1d.parquet')
last=float(day.close.iloc[-1])
dates=pd.date_range('2026-11-03 10:30',periods=6,freq='min')
curve=last+np.arange(6)*20
minute=pd.DataFrame(dict(datetime=dates,open=curve-10,high=curve+50,low=curve-50,close=curve+10,volume=100.))
day=pd.concat([day,pd.DataFrame([dict(datetime=pd.Timestamp('2026-11-03'),open=last,high=last+200,low=last-50,close=last+110,volume=1000.)])],ignore_index=True)
def native(frame):
 frame=frame.copy();frame['id']=np.arange(len(frame));frame['datetime']=pd.DatetimeIndex(frame.datetime).tz_localize('Asia/Shanghai').as_unit('ns').asi8
 frame['open_oi']=frame['close_oi']=50000.;return frame
serials={'1min':native(minute),'1d':native(day)}
quote=dict(datetime='2026-11-03 10:35:20',last_price=last+110,underlying_symbol='SHFE.sn2612')
snapshot=build_snapshot(serials,quote,'2026-11-03 10:35:20')
target=RealtimeTarget(root,'主力连续','SN','主连','KQ.m@SHFE.sn')
stats=write_live_snapshot(target,snapshot)
send('started',count=1,mode=config['mode'],targets=[dict(source=target.source,symbol=target.symbol,
     contract=target.contract,tq_symbol=target.tq_symbol)])
send('saved',source=target.source,symbol=target.symbol,contract=target.contract,minute_latest=stats['minute_latest'])
for line in sys.stdin:
 command=json.loads(line)
 if command.get('command')=='stop': break
 if command.get('command')=='view' and command.get('enabled'):
  period=command['period']
  if period not in snapshot.frames: period='1min'
  send('preview',tq_symbol=target.tq_symbol,**preview_payload(snapshot,period))
send('stopped')
'''

def validate(output):
    output.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    app.setFont(QFont("Noto Sans CJK SC", 10))
    errors, clients = [], []
    old_argv = sys.argv[:]
    def controller_factory(parent):
        value = BackgroundLiveController(parent, program=sys.executable,
            arguments=["-X", "utf8", "-u", "-c", CHILD])
        clients.append(value)
        return value
    def spin(predicate, seconds=12):
        end = time.monotonic()+seconds
        while not predicate() and time.monotonic() < end:
            app.processEvents(); threading.Event().wait(.01)
        assert predicate(), errors or "后台联调超时"
    with tempfile.TemporaryDirectory(prefix="futures-v6-background-") as directory:
        root = Path(directory); fixture(root)
        sys.argv[0] = str(root/"run.py")
        with patch("futures_replay.ui.main_window.load_state", return_value={}), \
             patch("futures_replay.ui.main_window.load_replay_heads", return_value={}), \
             patch("futures_replay.ui.main_window.load_main_list_sort", return_value={}), \
             patch("futures_replay.ui.main_window.save_state"), \
             patch("futures_replay.ui.main_window.save_replay_heads"), \
             patch("futures_replay.ui.main_window.save_main_list_sort"), \
             patch("futures_replay.ui.main_window.BackgroundLiveController", side_effect=controller_factory), \
             patch("futures_replay.ui.main_window.component_issue", return_value=""), \
             patch("futures_replay.ui.background_live.LocalLoginStore",
                   **{"return_value.available": False, "return_value.load.return_value": None}), \
             patch.object(MainWindow, "_error", lambda self, exc: errors.append(str(exc))):
            window = MainWindow(root); window.resize(1500, 1000)
            window.source_combo.setCurrentText("主力连续")
            window.panels[0].period.set_period("1d"); window.panels[0].apply_options()
            window.show(); app.processEvents()
            assert window.engine is not None, errors
            window.engine.jump_bar(120, to_end_of_bar=True); window._refresh_all_panels()
            before, engine_id, account = window.engine.current(), id(window.engine), window.account.to_dict()
            def login():
                dialog = app.activeModalWidget()
                if not isinstance(dialog, TqLoginDialog):
                    errors.append("未打开免费行情登录窗口")
                    if dialog: dialog.reject()
                    return
                dialog.username.setText("offline-test")
                dialog.password.setText("offline-secret")
                dialog.accept()
            QTimer.singleShot(20, login)
            direct = window.findChild(QToolButton,'free_realtime_button')
            assert direct is not None
            direct.click()
            # Exercise opening the view while QProcess is still Starting.
            # The old protocol sent this view command ahead of login config.
            window.live_view_action.setChecked(True)
            spin(lambda: ("主力连续", "SN") in window._live_dirty)
            assert window.live_controller.is_running()
            assert id(window.engine) == engine_id and window.engine.current().timestamp == before.timestamp
            assert window.account.to_dict() == account
            assert '2026-11-03 10:34:00' in window.live_status_label.toolTip()
            assert window.live_controller.process.processId() != os.getpid()
            window.live_view_action.setChecked(True)
            spin(lambda: window._live_chart is not None and window._live_chart._received_live
                 and not window._live_chart.chart.df.empty)
            live = window._live_chart
            live.status.setText("【构造行情联调，非实盘登录】 " + live.status.text())
            assert live.grab().save(str(output/"realtime_dialog.png"))
            assert not live.isModal()
            window.live_view_action.setChecked(False)
            assert not live.isVisible() and window.live_controller.is_running()
            window.live_view_action.setChecked(True)
            spin(lambda: live._received_live and not live.chart.df.empty)
            window.realtime_action.trigger()
            spin(lambda: window.live_controller.process is None)
            assert live.isVisible() and '已停止' in live.status.text()
            window.live_view_action.setChecked(False)
            window._live_dirty.clear()
            with patch('futures_replay.ui.main_window.load_saved_login',return_value=('offline-test','offline-secret')):
                window.realtime_all_action.trigger()
            spin(lambda: ("主力连续", "SN") in window._live_dirty)
            assert window.live_controller.mode == "all"
            assert id(window.engine) == engine_id and window.engine.current().timestamp == before.timestamp
            assert window.grab().save(str(output/"main_background.png"))
            assert list((root/".futures_realtime").rglob("*.parquet"))
            window.close()
            spin(lambda: window.live_controller.process is None)
            assert not errors, errors
            window.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            app.processEvents()
    sys.argv[:] = old_argv
    result = {"result": "passed", "pandas": pd.__version__, "checks": [
        "可见免费实时按钮及原当前/全部菜单", "本地账号登录及记住后直接启动", "独立后台进程", "真实增量分片保存与固定保存时间",
        "前台复盘引擎和进度不变", "模拟账户不变", "启动时立即打开实时图的协议顺序", "按需非模态实时图",
        "隐藏实时图后继续采集", "停止后窗口保留并提示状态", "退出时停止子进程"],
        "data": "构造行情、模拟服务端；未使用真实天勤账号"}
    (output/"result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    validate(parser.parse_args().output)
