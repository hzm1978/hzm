from contextlib import contextmanager
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import pandas as pd
from PySide6.QtCore import QCoreApplication, QEvent, QThreadPool, Qt, QTimer

from tq_workbench.app import make_app
from tq_workbench.bars import records
from tq_workbench.connection import Connection
from tq_workbench.main_window import MainWindow
from tq_workbench.technical.models import Drawing
from test_core import frame

STREAM = """
import json,queue,sys,threading
sys.path.insert(0,'tests')
from test_sdk import FakeApi
from tq_workbench.sdk_worker import serve
config=json.loads(sys.stdin.readline())
commands=queue.Queue();stopped=threading.Event()
def reader():
    for line in sys.stdin:
        message=json.loads(line)
        if message.get('command')=='stop':stopped.set();return
        commands.put(message)
    stopped.set()
threading.Thread(target=reader,daemon=True).start()
serve(config,commands,stopped,api_factory=lambda *args:FakeApi())
"""


class DesktopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = make_app()

    def spin(self, predicate, timeout=8):
        end = time.monotonic()+timeout
        while time.monotonic() < end and not predicate():
            self.app.processEvents(); threading.Event().wait(.005)
        self.assertTrue(predicate())

    @contextmanager
    def window(self, streamed=False):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory)/"studio.db"
            connection = Connection(database, program=sys.executable, arguments=["-u", "-c", STREAM]) if streamed else None
            window = MainWindow(database, connection=connection, auto_connect=False)
            window.trend.setChecked(False); window.show()
            try:
                yield window
            finally:
                window.close()
                self.spin(lambda:not window._tasks)
                QThreadPool.globalInstance().waitForDone(10000)
                self.app.processEvents()
                window.deleteLater()
                if connection:
                    connection.deleteLater()
                QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_real_qprocess_runs_production_collector_and_preserves_replay_during_live_updates(self):
        with self.window(streamed=True) as window:
            window.select_symbol("KQ.m@DCE.jm")
            window.connection.connect_account("offline-user", "offline-pass")
            self.spin(lambda:not window.replay.frame.empty)
            original = window.replay.cursor
            window.order("开多")
            account = window.account.to_dict()
            window.live_action.setChecked(True); window.live.period.setCurrentText("1分钟")
            self.spin(lambda:60 in window.buffers and len(window.live.chart.df)==2)
            self.assertEqual(window.replay.cursor, original)
            self.assertEqual(window.account.to_dict(), account)
            self.assertEqual(window.primary.df.iloc[-1]["datetime"], pd.Timestamp("2026-11-03"))
            self.assertEqual(window.live.chart.df.iloc[-1]["datetime"], pd.Timestamp("2026-11-04 10:00"))
            self.assertEqual(window.store.load_bars("KQ.m@DCE.jm", 60).shape[0], 2)
            self.assertNotIn("offline-pass", window.connection.details())

    def test_selection_reuses_worker_and_ignores_previous_revision(self):
        with self.window(streamed=True) as window:
            window.connection.connect_account("offline-user", "offline-pass")
            self.spin(lambda:not window.replay.frame.empty)
            pid = window.connection.process.processId()
            old_revision = window.connection.revision
            window.select_symbol("KQ.m@DCE.jm")
            self.spin(lambda:len(window.replay.frame)==2 and window.replay.current is not None)
            self.assertEqual(window.connection.process.processId(), pid)
            before = window.buffers[86400].copy()
            malicious = frame(pd.to_datetime(["2099-01-01"]), [9000])
            window.receive(dict(kind="bars", symbol="KQ.m@DCE.jm", seconds=86400,
                                revision=old_revision, bars=records(malicious)))
            pd.testing.assert_frame_equal(window.buffers[86400], before)

    def test_weekly_ui_uses_daily_cursor_and_never_paints_future_week_prices(self):
        with self.window() as window:
            window.open_demo()
            source = frame(pd.to_datetime(["2026-10-30", "2026-11-02", "2026-11-03"]), [100, 110, 9000])
            window.buffers = {86400:source}
            window.period.setCurrentText("周线")
            window.create_training(source, 0)
            window.seek_index(window.replay.day_index(1))
            self.assertEqual(window.replay.current["datetime"], pd.Timestamp("2026-11-02"))
            self.assertLess(window.primary.df.high.max(), 1000)
            self.assertFalse(window.primary.df.iloc[-1]["closed"])

    def test_training_restore_replays_frozen_snapshot_and_restores_orders_and_drawings(self):
        with self.window() as window:
            window.open_demo(); window.order("开多")
            window.primary.drawings = [Drawing("趋势线", 3, 100, 8, 105)]
            window.save()
            identifier = window.session_id; cursor = window.replay.cursor
            before = window.primary.df.copy()
            correction = window.buffers[86400].copy(); correction["volume"] = 999999
            window.store.upsert_bars(window.symbol, 86400, correction)
            state = window.store.load_session(identifier)
            window.create_training(correction, 20)
            window.restore_training(state)
            self.assertEqual(window.replay.cursor, cursor)
            self.assertEqual(window.account.qty, 1)
            self.assertEqual(window.primary.drawings[0].kind, "趋势线")
            pd.testing.assert_frame_equal(window.primary.df, before)

    def test_backwards_after_trade_is_blocked_but_forward_matching_cannot_skip_bars(self):
        with self.window() as window:
            window.open_demo()
            source = frame(pd.date_range("2026-11-02 09:00", periods=5, freq="min"), [100, 100, 90, 110, 120])
            window.create_training(source, 0)
            window.account.contract.slippage_ticks = 0
            window.account.submit_limit("开多", 1, 95, str(window.replay.current["datetime"]))
            window.seek_index(4)
            self.assertEqual(window.account.qty, 1)
            self.assertEqual(window.account.fills[0]["timestamp"], "2026-11-02 09:02:00")
            window.seek_index(0)
            self.assertEqual(window.replay.cursor, 4)
            self.assertIn("不能回退", window.message_label.text())

    def test_going_back_before_activity_resets_matching_clock(self):
        with self.window() as window:
            window.open_demo()
            window.seek_index(window.replay.cursor+3)
            target = window.replay.cursor-2
            window.seek_index(target)
            self.assertEqual(window.account.last_bar, str(window.replay.current["datetime"]))
            self.assertFalse(window.account.has_activity)

    def test_volume_preferences_and_live_native_window_controls(self):
        with self.window() as window:
            window.open_demo(); window.volume.setChecked(False)
            window.live_action.setChecked(True)
            flags = window.live.windowFlags()
            self.assertTrue(flags & Qt.WindowType.WindowMinimizeButtonHint)
            self.assertTrue(flags & Qt.WindowType.WindowMaximizeButtonHint)
            window.live.showMaximized(); self.app.processEvents()
            self.assertTrue(window.live.isMaximized())
            window.live.showMinimized(); self.app.processEvents()
            self.assertTrue(window.live.isMinimized())
            window.live.showNormal()
            window.live.volume.setChecked(False); window.save()
            self.assertFalse(window.primary.show_volume)
            self.assertFalse(window.store.setting("ui")["volume"])
            self.assertFalse(window.store.setting("live_volume"))

    def test_login_timeout_and_split_stderr_preserve_redacted_diagnostics(self):
        with tempfile.TemporaryDirectory() as directory:
            child = """import json,sys,time
cfg=json.loads(sys.stdin.readline())
sys.stderr.write('password='+cfg['password'][:4]);sys.stderr.flush()
time.sleep(.03)
sys.stderr.write(cfg['password'][4:]+' access_token=private-token');sys.stderr.flush()
sys.exit(7)
"""
            connection = Connection(Path(directory)/"db", program=sys.executable, arguments=["-u", "-c", child])
            connection.connect_account("offline-user", "sensitive-password")
            try:
                self.spin(lambda:connection.process is None)
                text = connection.details()
                self.assertIn("代码7", text)
                self.assertNotIn("sensitive-password", text)
                self.assertNotIn("private-token", text)
            finally:
                connection.shutdown(); connection.deleteLater()

    def test_slow_cache_read_keeps_qt_responsive_and_cannot_overwrite_new_market_data(self):
        with self.window() as window:
            window.open_demo()
            old = frame(pd.to_datetime(["2026-11-04"]), [100])
            latest = old.copy(); latest["volume"] = 999
            ticks = []; timer = QTimer(); timer.setInterval(5); timer.timeout.connect(lambda:ticks.append(1)); timer.start()
            def slow(*args, **kwargs):
                threading.Event().wait(.12)
                return old
            try:
                with patch.object(window.store, "load_bars", new=slow):
                    window.load_cache()
                    window.receive(dict(kind="bars", symbol=window.symbol, revision=window.connection.revision,
                        seconds=86400, bars=records(latest), quote_time="2026-11-04 10:00", last_price=101))
                    self.spin(lambda:not window._tasks)
                self.assertGreater(len(ticks), 3)
                self.assertEqual(window.buffers[86400].iloc[-1]["volume"], 999)
            finally:
                timer.stop()

    def test_slow_trend_result_is_discarded_after_the_replay_prefix_changes(self):
        from tq_workbench.chart import analyze_trend
        with self.window() as window:
            window.open_demo()
            def slow(source, **kwargs):
                threading.Event().wait(.08)
                return analyze_trend(source, **kwargs)
            with patch("tq_workbench.chart.analyze_trend", new=slow):
                window.trend.setChecked(True)
                window.render()
                self.spin(lambda:window.primary._working is not None)
                window.seek_index(window.replay.cursor-7)
                self.spin(lambda:window.primary._trend_cache is not None and window.primary._working is None)
                result = window.primary._trend_cache
                self.assertEqual(result.as_of_index, window.replay.cursor)
                self.assertEqual(len(window.primary.df), window.replay.cursor+1)

    def test_history_result_starts_a_new_frozen_training_within_the_requested_range(self):
        with self.window() as window:
            window.open_demo()
            source = frame(pd.bdate_range("2016-01-05", periods=5))
            window.store.upsert_bars("KQ.m@SHFE.ag", 86400, source)
            window.use_api_history(dict(symbol="KQ.m@SHFE.ag", period="日线", seconds=86400,
                                        start="2016-01-05", end="2016-01-06"))
            self.spin(lambda:not window._tasks)
            self.assertEqual(len(window.replay.frame), 2)
            self.assertEqual(window.replay.current["datetime"], pd.Timestamp("2016-01-05"))
            self.assertEqual(len(window.primary.df), 1)


    def test_hung_initializer_timeout_keeps_the_original_reason(self):
        with tempfile.TemporaryDirectory() as directory:
            child = "import json,sys,time\njson.loads(sys.stdin.readline())\ntime.sleep(10)\n"
            connection = Connection(Path(directory)/"db", program=sys.executable, arguments=["-u", "-c", child], timeout_ms=100)
            connection._kill.setInterval(100)
            connection.connect_account("offline-user", "sensitive-password")
            try:
                self.spin(lambda:connection.process is None)
                self.assertIn("连接超过", connection.error)
                self.assertNotIn("Process crashed", connection.error)
            finally:
                connection.shutdown(); connection.deleteLater()

    def test_four_linked_charts_have_independent_periods_and_never_receive_future_bars(self):
        with self.window() as window:
            window.open_demo()
            source = frame(pd.date_range("2026-11-04 09:00", periods=50, freq="min"), [100]*30+[9000]*20)
            window.period.setCurrentText("1分钟")
            window.create_training(source, 19)
            window.layout_mode.setCurrentIndex(2)
            window.high_period.setCurrentText("5分钟")
            window.extra_views[0][1].setCurrentText("15分钟")
            window.extra_views[1][1].setCurrentText("日线")
            self.app.processEvents()
            for chart in window.all_charts():
                self.assertTrue(chart.isVisible())
                self.assertFalse(chart.df.empty)
                self.assertLess(chart.df.high.max(), 1000)
            window._activate(window.extra_views[0][2])
            window.ma_periods.setText("3,8,21")
            window.chart_options()
            self.assertEqual(window.extra_views[0][2].ma_periods, (3, 8, 21))
            self.assertNotEqual(window.primary.ma_periods, (3, 8, 21))
            window.save()
            self.assertEqual(window.store.setting("ui")["chart_layout"], 2)
            self.assertEqual(window.store.setting("ui")["view_periods"], ["5分钟", "15分钟", "日线"])
            state = window.store.load_session(window.session_id)
            window.layout_mode.setCurrentIndex(0)
            window.high_period.setCurrentText("日线")
            window.restore_training(state)
            self.assertEqual(window.layout_mode.currentIndex(), 2)
            self.assertEqual(window.high_period.currentText(), "5分钟")
            self.assertEqual(window.extra_views[0][2].ma_periods, (3, 8, 21))


if __name__ == "__main__":
    unittest.main()
