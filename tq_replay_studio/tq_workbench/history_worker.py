"""Documented TqBacktest history playback, separate from the real-time process."""
from __future__ import annotations

import asyncio
from datetime import date
import json
import sys
import threading
import time

import pandas as pd

from .bars import finite, from_sdk, records, validate_frame
from .sdk_worker import create_api, emit, redact
from .storage import Store


def create_history_api(config, loop, progress):
    from tqsdk import TqBacktest
    backtest = TqBacktest(start_dt=date.fromisoformat(config["start"]),
                        end_dt=date.fromisoformat(config["end"]))
    return create_api(config["username"], config["password"], loop, progress, backtest=backtest)


class HistoryCollector:
    def __init__(self, store, symbol, seconds, start, end, publish=emit):
        self.store, self.symbol, self.seconds = store, symbol, seconds
        self.start, self.end = start, end
        self.publish = publish
        self.pending = {}
        self.count = 0
        self.first = self.last = None
        self.last_flush = 0.

    def accept(self, frame, *, force=False):
        selected = frame.loc[frame["closed"] & frame["trading_day"].between(self.start, self.end)]
        for row in records(selected):
            stamp = row["datetime"]
            self.pending[stamp] = row
            if self.last is None or stamp > self.last:
                self.count += 1
                self.last = stamp
            if self.first is None or stamp < self.first:
                self.first = stamp
        if force or len(self.pending) >= 300 or time.monotonic()-self.last_flush > .75:
            self.flush()

    def flush(self):
        if self.pending:
            self.store.upsert_bars(self.symbol, self.seconds, validate_frame(pd.DataFrame(self.pending.values())))
            self.pending.clear()
        self.last_flush = time.monotonic()
        self.publish("progress", symbol=self.symbol, seconds=self.seconds, count=self.count,
                     first=self.first, last=self.last, start=self.start, end=self.end)


def collect_history(config, stopped, *, api_factory=create_history_api, publish=emit, finished_exception=None):
    if finished_exception is None:
        from tqsdk import BacktestFinished
        finished_exception = BacktestFinished
    if config["start"] > config["end"]:
        raise ValueError("开始日期不能晚于结束日期")
    loop = asyncio.new_event_loop(); asyncio.set_event_loop(loop)
    api = None
    collector = HistoryCollector(Store(config["database"]), config["symbol"], int(config["seconds"]),
                                 config["start"], config["end"], publish)
    secrets = config["username"], config["password"]
    serial = quote = None
    try:
        api = api_factory(config, loop, lambda stage,text:publish("status", stage=stage, text=text))
        publish("status", stage="ready", text="天勤历史回放连接已建立")
        quote = api.get_quote(config["symbol"])
        seconds = int(config["seconds"])
        # A short rolling window is sufficient: TqBacktest progressively
        # supplies every historical bar, so this is not the latest-10000 mode.
        serial = api.get_kline_serial(config["symbol"], seconds, data_length=300)
        daily = serial if seconds == 86400 else api.get_kline_serial(config["symbol"], 86400, data_length=300)
        last_seen = None
        while not stopped.is_set():
            api.wait_update(deadline=time.time()+.2)
            if not api.is_serial_ready(serial):
                continue
            stamp = int(serial.iloc[-1]["datetime"])
            if stamp == last_seen:
                continue
            first = last_seen is None
            last_seen = stamp
            virtual_now = pd.to_datetime(quote.get("datetime", ""), errors="coerce")
            if pd.isna(virtual_now):
                continue
            calendar = from_sdk(daily, 86400, quote, received_at=virtual_now)["datetime"] if api.is_serial_ready(daily) else ()
            rows = serial if first else serial.tail(3)
            frame = from_sdk(rows, seconds, quote, received_at=virtual_now, calendar=calendar)
            collector.accept(frame)
    except finished_exception:
        if serial is not None and api.is_serial_ready(serial):
            # The public engine has reached the requested end trading day.
            # Commit the last observed completed bar without fabricating bars.
            boundary = pd.Timestamp(config["end"])+pd.Timedelta(hours=20, minutes=59)
            frame = from_sdk(serial.tail(3), int(config["seconds"]), quote, received_at=boundary)
            frame["closed"] = True
            collector.accept(frame, force=True)
    except Exception as exc:
        publish("error", text=redact(f"{type(exc).__name__}: {exc}", secrets))
        return
    finally:
        collector.flush()
        if api is not None:
            try:
                api.close()
            except Exception:
                pass
        loop.close()
    publish("complete", symbol=config["symbol"], seconds=int(config["seconds"]),
            start=config["start"], end=config["end"], count=collector.count,
            first=collector.first, last=collector.last, cancelled=stopped.is_set(),
            multiplier=finite((quote or {}).get("volume_multiple"), 10),
            tick=finite((quote or {}).get("price_tick"), 1))


def main():
    line = sys.stdin.readline()
    if not line:
        return
    config = json.loads(line)
    stopped = threading.Event()
    def reader():
        for line in sys.stdin:
            try:
                if json.loads(line).get("command") == "stop":
                    stopped.set(); return
            except ValueError:
                continue
        stopped.set()
    threading.Thread(target=reader, daemon=True).start()
    collect_history(config, stopped)


if __name__ == "__main__":
    main()
