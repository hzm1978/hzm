import argparse
from pathlib import Path
import sys
import tempfile
import time

from PySide6.QtCore import QThreadPool
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from . import APP_NAME
from .main_window import MainWindow
from .paths import data_directory
from .style import STYLE


def make_app():
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("TqReplayStudio")
    app.setStyle("Fusion")
    app.setFont(QFont("Microsoft YaHei UI" if sys.platform == "win32" else "Noto Sans CJK SC", 10))
    app.setStyleSheet(STYLE)
    return app


def selftest(output):
    output.mkdir(parents=True, exist_ok=True)
    app = make_app()
    with tempfile.TemporaryDirectory() as directory:
        window = MainWindow(Path(directory)/"studio.sqlite3", auto_connect=False)
        window.show(); window.open_demo()
        end = time.monotonic()+6
        while time.monotonic() < end:
            app.processEvents()
            if window.primary._trend_cache is not None:
                break
            time.sleep(.01)
        before = window.replay.cursor
        window.seek_index(before+1); app.processEvents()
        assert window.replay.cursor == before+1
        assert len(window.primary.df) == window.replay.cursor+1
        window.order("开多")
        assert window.account.qty == 1
        window.save()
        saved = window.store.load_session(window.session_id)
        assert saved["state"]["account"]["qty"] == 1
        assert window.grab().save(str(output/"studio.png"))
        window.live_action.setChecked(True); app.processEvents()
        assert window.live.chart.df.datetime.iloc[-1] == window.buffers[86400].datetime.iloc[-1]
        assert window.replay.cursor == before+1
        assert window.live.grab().save(str(output/"live.png"))
        window.close()
        QThreadPool.globalInstance().waitForDone(10000)
        app.processEvents()
    print("SELFTEST OK: new desktop, demo, replay, paper order, persistence and independent live window")
    return 0


def main():
    parser = argparse.ArgumentParser(description=APP_NAME)
    parser.add_argument("--database", type=Path, help="自定义本机SQLite数据库路径")
    parser.add_argument("--demo", action="store_true", help="使用明确标记的构造演示数据")
    parser.add_argument("--selftest", action="store_true", help="离线真实Qt自检")
    parser.add_argument("--output", type=Path, default=Path("validation"))
    args = parser.parse_args()
    if args.selftest:
        return selftest(args.output)
    app = make_app()
    window = MainWindow(args.database or data_directory()/"studio.sqlite3", auto_connect=not args.demo)
    window.show()
    if args.demo:
        window.open_demo()
    result = app.exec()
    QThreadPool.globalInstance().waitForDone(10000)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
