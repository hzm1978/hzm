"""Real Qt window controls, bounded login waiting and redacted diagnostics."""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
from PySide6.QtCore import QCoreApplication,QEvent,QTimer,Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication,QDialog,QPlainTextEdit
from futures_replay import __version__
from futures_replay.realtime_data import RealtimeTarget
from futures_replay.ui.background_live import BackgroundLiveController
from futures_replay.ui.live_chart_dialog import LiveChartDialog
from test_local_follow import write_local

CHILD='''import json,sys
cfg=json.loads(sys.stdin.readline())
def send(kind,**kw):print('FRLIVE '+json.dumps(dict(kind=kind,**kw)),flush=True)
send('hello',python=sys.executable,python_version=sys.version.split()[0],sdk='mock',pandas='mock')
send('status',stage='authenticating',text='正在认证天勤账号（构造超时测试）')
for line in sys.stdin:
 if json.loads(line).get('command')=='stop':break
'''


def validate(output):
    output.mkdir(parents=True,exist_ok=True)
    app=QApplication.instance() or QApplication([]);app.setFont(QFont('Noto Sans CJK SC',10))
    def spin(predicate):
        end=time.monotonic()+8
        while not predicate() and time.monotonic()<end:
            app.processEvents();threading.Event().wait(.01)
        assert predicate(),'连接状态验证超时'
    controller=BackgroundLiveController(program=sys.executable,arguments=['-u','-c',CHILD],connection_timeout_ms=2500)
    view=LiveChartDialog(controller);view.trend.setChecked(False);view.volume.setChecked(False)
    try:
        with tempfile.TemporaryDirectory() as directory:
            write_local(directory,symbol='JM')
            target=RealtimeTarget(Path(directory),'主力连续','JM','主连','KQ.m@DCE.jm')
            view.configure(target,'1d');view.resize(1250,800)
            controller.start(dict(root=directory,source=target.source,mode='current',symbol=target.symbol,
                contract=target.contract,tq_symbol=target.tq_symbol),'offline-user','offline-secret')
            spin(lambda:not view.chart.df.empty and '认证天勤账号' in view.connection.text())
            view.title.setText(f'【v{__version__} 构造行情：连接超时缩短为2.5秒，仅演示状态】 '+view.title.text())
            flags=view.windowFlags()
            assert flags & Qt.WindowType.WindowMinimizeButtonHint
            assert flags & Qt.WindowType.WindowMaximizeButtonHint
            view.showMaximized();app.processEvents();assert view.isMaximized()
            view.showNormal();view.showMinimized();app.processEvents();assert view.isMinimized()
            view.showNormal();view.resize(1250,800);app.processEvents()
            assert view.grab().save(str(output/'login_stage.png'))
            spin(lambda:controller.process is None)
            assert '实时连接超过' in view.connection.text() and not view.chart.df.empty
            assert view.grab().save(str(output/'login_timeout.png'))
            errors=[]
            def inspect():
                dialog=app.activeModalWidget()
                if not isinstance(dialog,QDialog):errors.append('诊断窗口未打开');return
                try:
                    text=dialog.findChild(QPlainTextEdit).toPlainText()
                    assert 'offline-secret' not in text and '实时连接超过' in text
                    assert dialog.grab().save(str(output/'connection_details.png'))
                except Exception as exc:errors.append(str(exc))
                finally:dialog.reject()
            QTimer.singleShot(50,inspect);view._connection_details();assert not errors,errors
            result=dict(result='passed',version=__version__,data='构造行情、模拟登录停滞，未实际联网',
                checks=['最小化/最大化提示标志','最大化/最小化/还原状态','显示真实登录阶段及等待秒数',
                '超时结束等待并保留K线','连接详情可查看且凭据脱敏'])
            view.close();spin(lambda:view.history._active is None)
    finally:
        view.close();controller.shutdown();view.deleteLater();controller.deleteLater()
        QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete);app.processEvents()
    (output/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True)
    validate(parser.parse_args().output)
