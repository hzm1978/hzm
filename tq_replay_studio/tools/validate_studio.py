"""Real Qt offline workflow and screenshots; never logs into a trading account."""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QCoreApplication, QEvent, QThreadPool
from tq_workbench.app import make_app
from tq_workbench.main_window import MainWindow
from tq_workbench.storage import Store
from tq_workbench.history_dialog import HistoryDialog


def validate(output):
    output.mkdir(parents=True, exist_ok=True)
    app = make_app()
    def spin(predicate):
        deadline = time.monotonic()+12
        while not predicate() and time.monotonic() < deadline:
            app.processEvents(); threading.Event().wait(.01)
        assert predicate(), "Qt验证超时"
    with tempfile.TemporaryDirectory() as directory:
        window = MainWindow(Path(directory)/"studio.db", auto_connect=False)
        history = None
        try:
            window.resize(1640, 1060); window.show(); window.open_demo()
            window.order("开空"); window.seek_index(window.replay.cursor+3)
            window.save()
            spin(lambda:window.primary._trend_cache is not None)
            assert window.primary._trend_error == ""
            assert window.primary.df.datetime.iloc[-1] == window.replay.current["datetime"]
            assert window.grab().save(str(output/"studio_two.png"))
            window.layout_mode.setCurrentIndex(2)
            app.processEvents()
            assert all(not chart.df.empty for chart in window.all_charts())
            assert window.grab().save(str(output/"studio_four.png"))
            window.tabs.setCurrentWidget(window.statistics)
            app.processEvents()
            assert window.grab().save(str(output/"statistics.png"))
            before = window.replay.cursor; account = window.account.to_dict()
            window.live_action.setChecked(True)
            app.processEvents()
            assert window.live.chart.df.datetime.iloc[-1] > window.replay.current["datetime"]
            assert window.replay.cursor == before and window.account.to_dict() == account
            assert window.live.grab().save(str(output/"live_demo.png"))
            window.open_trading()
            window.trade.mode.setCurrentIndex(2)
            app.processEvents()
            assert not window.trade.send.isEnabled() and window.trade.connection is None
            assert not window.trade.fund_password.text()
            assert window.trade.grab().save(str(output/"broker_setup.png"))
            history = HistoryDialog(window.store.path, "KQ.m@SHFE.ag", "1分钟", (), window)
            history.show(); app.processEvents()
            assert history.grab().save(str(output/"history_setup.png"))
            backup = Path(directory)/"portable.sqlite3"
            window.store.backup(backup)
            state = Store(backup).load_session(window.session_id)
            assert state["cursor"] == window.replay.cursor and state["state"]["account"] == account
            result = dict(status="passed",data="构造演示行情；账户接入截图未登录，无真实资金",
                checks=["真实新主窗口", "单/双/四图因果数据", "趋势计算完成且无错误",
                        "历史模拟成交和统计", "实时看盘不推进训练", "徽商实盘初始锁定",
                        "历史日期加载界面", "工作区一致备份/恢复"])
        finally:
            if history is not None:
                history.reject()
            window.close()
            QThreadPool.globalInstance().waitForDone(15000)
            app.processEvents()
            window.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    (output/"result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2)+"\n")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(); parser.add_argument("--output", type=Path, required=True)
    validate(parser.parse_args().output)
