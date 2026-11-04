from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from PySide6.QtWidgets import QMessageBox

from tq_workbench.app import make_app
from tq_workbench.connection import Connection
import test_desktop

TRADE_STREAM = """
import json,queue,sys,threading
sys.path.insert(0,'tests')
from test_trade_gate import TradingApi
from tq_workbench.trade_worker import serve_trade
config=json.loads(sys.stdin.readline())
commands=queue.Queue();stopped=threading.Event()
def reader():
    for line in sys.stdin:
        command=json.loads(line)
        if command.get('command')=='stop':stopped.set();return
        commands.put(command)
    stopped.set()
threading.Thread(target=reader,daemon=True).start()
serve_trade(config,commands,stopped,api_factory=lambda *args:TradingApi())
"""


class TradeDesktopTests(unittest.TestCase):
    spin = test_desktop.DesktopTests.spin
    window = test_desktop.DesktopTests.window

    @classmethod
    def setUpClass(cls):
        cls.app = make_app()

    def connect(self, window, mode):
        window.open_demo()
        window.select_symbol("KQ.m@DCE.jm")
        window.connection._credentials = ("offline-user", "offline-pass")
        window.open_trading()
        trade = window.trade
        trade.mode.setCurrentIndex(mode)
        trade.fund_account.setText("offline-fund")
        trade.fund_password.setText("private-fund-password")
        def factory(database, parent=None, **kwargs):
            return Connection(database, parent, arguments=["-u", "-c", TRADE_STREAM],
                              configuration=kwargs["configuration"])
        with patch("tq_workbench.trade_dialog.Connection", new=factory):
            trade.connect_account()
        self.spin(lambda:bool(trade.latest) and bool(trade.actual.text()))
        return trade

    def test_live_ui_is_locked_until_explicit_arm_and_cancelled_review_does_not_send(self):
        with self.window() as window:
            trade = self.connect(window, 2)
            self.assertFalse(trade.send.isEnabled())
            self.assertFalse(trade.arm.isChecked())
            with patch("tq_workbench.trade_dialog.QInputDialog.getText", return_value=("启用实盘", True)):
                trade.arm.setChecked(True)
            self.spin(lambda:trade.send.isEnabled())
            with patch("tq_workbench.trade_dialog.QMessageBox.question", return_value=QMessageBox.StandardButton.No):
                trade.prepare()
                self.spin(lambda:not trade._preview_pending)
            self.assertEqual(trade.latest["orders"], [])
            self.assertNotIn("private-fund-password", trade.connection.details())
            trade.close()
            self.spin(lambda:not trade.latest.get("armed"))

    def test_confirmed_live_review_routes_only_one_exact_order_and_allows_cancel(self):
        with self.window() as window:
            trade = self.connect(window, 2)
            with patch("tq_workbench.trade_dialog.QInputDialog.getText", return_value=("启用实盘", True)):
                trade.arm.setChecked(True)
            self.spin(lambda:trade.send.isEnabled())
            with patch("tq_workbench.trade_dialog.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes):
                trade.prepare()
                self.spin(lambda:len(trade.latest["orders"])==1)
            order = trade.latest["orders"][0]
            self.assertEqual(order["instrument_id"], "jm2612")
            self.assertEqual(order["direction"], "BUY")
            self.assertEqual(order["volume_orign"], 1)
            self.assertEqual(order["limit_price"], 100)
            trade.arm.setChecked(False)
            trade.connection._send(dict(command="cancel", order_id=order["order_id"]))
            self.spin(lambda:trade.latest["orders"][0]["status"]=="FINISHED")
            self.assertEqual(len(trade.latest["orders"]), 1)

    def test_real_time_simulation_orders_are_separate_from_historical_training(self):
        with self.window() as window:
            trade = self.connect(window, 0)
            account = window.account.to_dict()
            self.assertTrue(trade.send.isEnabled())
            trade.prepare()
            self.spin(lambda:len(trade.latest["orders"])==1)
            self.assertEqual(trade.latest["mode"], "sim")
            self.assertEqual(window.account.to_dict(), account)
            self.assertFalse(trade.arm.isVisible())


if __name__ == "__main__":
    unittest.main()
