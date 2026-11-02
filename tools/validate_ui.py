"""Offline real-main-window check using generated Parquet, never live accounts.

Run from the package root: python tools/validate_ui.py --output <directory>
"""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd
from PySide6.QtCore import QDate, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from futures_replay.ui.main_window import MainWindow
from futures_replay.ui.trend_dialog import TrendIndicatorDialog


def fixture(root):
    dates = pd.bdate_range("2025-06-02", "2026-11-02")
    rng = np.random.default_rng(20261102)
    x = np.arange(len(dates))
    close = 305000 + 90*x + 12000*np.sin(x/20) + 4000*np.sin(x/4) + rng.normal(0, 300, len(x))
    opening = np.r_[close[0]-100, close[:-1]]
    data = pd.DataFrame({"datetime": dates, "trading_day": dates.strftime("%Y-%m-%d"),
                         "open": opening, "high": np.maximum(opening, close) + rng.uniform(100, 800, len(x)),
                         "low": np.minimum(opening, close) - rng.uniform(100, 800, len(x)),
                         "close": close, "volume": rng.integers(10000, 40000, len(x)), "open_interest": 50000., "amount": 0.})
    continuous = root / "qihuozldata_parquet" / "SN" / "主连"
    raw = root / "qihushuju_parquet" / "SN"
    continuous.mkdir(parents=True)
    (raw / "1d").mkdir(parents=True)
    (raw / "1min").mkdir()
    (raw / "数据说明.md").write_text("# SN 沪锡\n交易所：SHFE\n", encoding="utf-8")
    data.to_parquet(continuous / "1d.parquet", index=False)
    data.to_parquet(raw / "1d" / "sn2611.parquet", index=False)
    return data


def validate(output):
    output.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    app.setFont(QFont("Noto Sans CJK SC", 10))
    errors, saved = [], []
    old_argv = list(sys.argv)
    with tempfile.TemporaryDirectory(prefix="futures-v4-ui-") as directory:
        root = Path(directory)
        data = fixture(root)
        sys.argv[0] = str(root / "run.py")
        with patch("futures_replay.ui.main_window.load_state", return_value={}), \
             patch("futures_replay.ui.main_window.load_replay_heads", return_value={}), \
             patch("futures_replay.ui.main_window.load_main_list_sort", return_value={}), \
             patch("futures_replay.ui.main_window.save_main_list_sort"), \
             patch("futures_replay.ui.main_window.save_state", side_effect=lambda state: saved.append(state)), \
             patch.object(MainWindow, "_error", lambda self, error: errors.append(str(error))):
            window = MainWindow(root)
            window.resize(1500, 1050)
            window.source_combo.setCurrentText("主力连续")
            for panel in window.panels:
                panel.period.set_period("1d", intraday=False)
                panel.trend.setChecked(True)
                panel.wave.setChecked(False)
                panel.ma_enabled.setChecked(False)
                panel.volume.setChecked(False)
                panel.apply_options()
            window.show(); app.processEvents()
            assert window.engine is not None, errors
            window.engine.jump_bar(len(window.engine.bars)-1, to_end_of_bar=True)
            window._capture_current_session(force=True)
            window._refresh_all_panels(); app.processEvents()
            panel = window.panels[0]
            analysis = panel.chart._trend_analysis_cached(len(panel.chart.df))
            assert analysis is not None and analysis.intervals and analysis.structure
            assert analysis.boundaries and analysis.standards and analysis.levels
            assert not panel.chart.show_wave
            assert not data.datetime.dt.strftime("%Y-%m-%d").eq("2026-10-31").any()
            assert window.grab().save(str(output / "main_daily.png"))
            # Exercise the actual toolbar button and a real modal dialog.
            def apply_dialog():
                dialog = app.activeModalWidget()
                assert isinstance(dialog, TrendIndicatorDialog)
                dialog.toggles["show_navigation"].setChecked(True)
                dialog.numbers["confirmation_bars"].setValue(2)
                assert dialog.grab().save(str(output / "indicator_details.png"))
                dialog._apply()
            QTimer.singleShot(0, apply_dialog)
            panel.trend_details.click(); app.processEvents()
            assert panel.chart.trend_settings["show_navigation"]
            assert panel.chart.trend_settings["confirmation_bars"] == 2
            window._save_now()
            assert saved[-1]["ui"]["panels"][0]["trend_settings"]["confirmation_bars"] == 2
            saved_settings = dict(panel.chart.trend_settings)
            panel.chart.set_trend_settings({})
            window.state = saved[-1]
            window._restore_ui(); app.processEvents()
            assert window.panels[0].chart.trend_settings == saved_settings
            panel = window.panels[0]
            for period in ("1d", "1w", "1M"):
                panel.period.set_period(period, intraday=False); app.processEvents()
                window.date_edit.setDate(QDate(2026, 10, 30)); app.processEvents()
                assert window.engine.current().trading_day == "2026-10-30"
                window.btn_next_day.click(); app.processEvents()
                assert window.engine.current().trading_day == "2026-11-02", period
                window.btn_prev_day.click(); app.processEvents()
                assert window.engine.current().trading_day == "2026-10-30", period
            window.grab().save(str(output / "main_monthly_navigation.png"))
            window.close(); app.processEvents()
            assert not errors, errors
        sys.argv[:] = old_argv
    result = {"result": "passed", "pandas": pd.__version__,
              "checks": ["真实Parquet加载主窗口", "第三版趋势图层", "实际指标明细按钮与模态应用", "设置保存与恢复", "10.30→11.2日/周/月单日加减", "10.31无伪日K"],
              "data": "生成行情，仅功能验证，非实际SN报价"}
    (output / "validation.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    validate(parser.parse_args().output)
