from __future__ import annotations

import argparse
import os
import sys
import traceback
from pathlib import Path


def install_exception_hook():
    def hook(tp, value, tb):
        text = "".join(traceback.format_exception(tp, value, tb))
        try:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.critical(None, "未捕获异常", text[-1500:])
        except Exception:
            print(text, file=sys.stderr)
    sys.excepthook = hook


def run_gui(data_root: Path | None = None):
    from PySide6.QtWidgets import QApplication
    from .ui.main_window import MainWindow
    app = QApplication(sys.argv)
    app.setApplicationName("期货复盘训练系统")
    install_exception_hook()
    w = MainWindow(data_root)
    w.show()
    return app.exec()


def run_selftest():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    import numpy as np
    import pandas as pd
    from PySide6.QtWidgets import QApplication
    from .replay import ReplayEngine
    from .trading import TradingAccount
    from .ui.chart_widget import CandleChart
    from .models import Side
    app = QApplication.instance() or QApplication([])
    t = pd.date_range("2026-01-01 09:00", periods=60, freq="min")
    base = 100 + np.cumsum(np.random.default_rng(1).normal(0, .2, len(t)))
    mins = pd.DataFrame({"datetime":t,"open":base,"high":base+.2,"low":base-.2,"close":base+.05,"volume":100,"trading_day":"2026-01-01"})
    bars = mins.set_index("datetime").resample("5min").agg(open=("open","first"),high=("high","max"),low=("low","min"),close=("close","last"),volume=("volume","sum"),trading_day=("trading_day","last")).dropna().reset_index()
    eng=ReplayEngine(bars,"5min",mins)
    for _ in range(10): eng.step(1)
    acc=TradingAccount(); acc.open_market("RB",Side.LONG,1,100); assert acc.position and acc.position.qty==1
    chart=CandleChart(); chart.resize(900,500); chart.set_wave_visible(True); s=eng.current(); chart.set_series(bars,s.bar_index,s.partial,s.synthetic); chart.show(); app.processEvents()
    out=Path("selftest.png").resolve(); chart.grab().save(str(out)); print(f"SELFTEST OK: {out}")
    return 0


def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--data-root",type=Path); ap.add_argument("--selftest",action="store_true"); args=ap.parse_args()
    if args.selftest: return run_selftest()
    return run_gui(args.data_root)


if __name__ == "__main__":
    raise SystemExit(main())
