"""SDK-only subprocess. No SDK entity, auth or order object reaches the GUI.

Protocol: one configuration JSON line, then watch/stop JSON lines on stdin.
Only documented public market-data methods are called.
"""
from __future__ import annotations

import asyncio
import importlib.metadata
import json
import queue
import re
import sys
import threading
import time
import pandas as pd

from .bars import finite, from_sdk, now_beijing, records
from .storage import Store

PREFIX = "TQSTUDIO "
HISTORY_LIMIT = 10000
EXCHANGES = ["SHFE", "DCE", "CZCE", "CFFEX", "INE", "GFEX"]


def redact(value, secrets=()):
    text = str(value)
    for secret in sorted({str(x) for x in secrets if x}, key=len, reverse=True):
        text = text.replace(secret, "[已隐藏]")
        text = text.replace(json.dumps(secret, ensure_ascii=True)[1:-1], "[已隐藏]")
    text = re.sub(r"(?i)(Bearer\s+)[A-Za-z0-9._~+/=-]+", r"\1[已隐藏]", text)
    text = re.sub(r"(?i)(https?://)[^/@\s]+:[^/@\s]+@", r"\1[已隐藏]@", text)
    text = re.sub(r'''(?i)(["']?(?:password|passwd|access_token|refresh_token|token|client_secret)["']?\s*[:=]\s*)(?:"[^"]*"|'[^']*'|[^\s,;}]+)''', r"\1[已隐藏]", text)
    return re.sub(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b", "[已隐藏]", text)


def emit(kind, **kwargs):
    print(PREFIX+json.dumps(dict(kind=kind, **kwargs), ensure_ascii=False, allow_nan=False), flush=True)


def create_api(username, password, loop, progress, *, backtest=None, account=None):
    from tqsdk import TqApi, TqAuth, TqSim
    class Auth(TqAuth):
        def login(self):
            progress("authenticating", "正在认证天勤账号")
            result = super().login()
            progress("authenticated", "认证通过，正在连接行情服务")
            return result
    progress("connecting", "正在初始化行情连接")
    return TqApi(account=TqSim() if account is None else account,
                 auth=Auth(username, password), loop=loop, backtest=backtest,
                 debug=False, web_gui=False, disable_print=True)


def read_catalog(api):
    symbols = list(api.query_quotes(ins_class=["CONT", "FUTURE"], exchange_id=EXCHANGES, expired=False))
    if not symbols:
        return []
    info = api.query_symbol_info(symbols)
    output = []
    for row in info.to_dict("records"):
        output.append(dict(symbol=str(row["instrument_id"]),
            name=str(row.get("instrument_name") or row["instrument_id"]),
            exchange=str(row.get("exchange_id", "")), category=str(row.get("ins_class", "")),
            multiplier=finite(row.get("volume_multiple"), 10), tick=finite(row.get("price_tick"), 1),
            underlying=str(row.get("underlying_symbol") or "")))
    return output


def drain(commands):
    latest = None
    while True:
        try:
            command = commands.get_nowait()
        except queue.Empty:
            return latest
        if command.get("command") == "watch":
            latest = command


class MarketPump:
    """One active watch, finite SDK subscription cache, immutable output batches."""
    def __init__(self, api, store, publish=emit):
        self.api, self.store, self.publish = api, store, publish
        self.cache = {}
        self.active = None
        self.last_send = 0.
        self.fingerprints = {}
        self.full_sent = set()

    def watch(self, request):
        symbol, revision = request["symbol"], request["revision"]
        durations = sorted({86400, *[int(x) for x in request["seconds"]]})
        if not symbol or any(x <= 0 or x > 86400 for x in durations):
            raise ValueError("合约或周期无效")
        self.publish("status", stage="subscribing", text=f"正在订阅 {symbol}")
        quote = self.api.get_quote(symbol)
        streams = {}
        for seconds in durations:
            key = symbol, seconds
            if key not in self.cache:
                self.publish("status", stage="subscribing", text=f"正在加载 {symbol} / {seconds}秒最近最多10000根")
                self.cache[key] = self.api.get_kline_serial(symbol, seconds, data_length=HISTORY_LIMIT)
            streams[seconds] = self.cache[key]
        self.active = dict(symbol=symbol, revision=revision, quote=quote, streams=streams)
        self.full_sent.clear(); self.fingerprints.clear(); self.last_send = 0.
        self.publish("status", stage="watching", text=f"订阅已建立：{symbol}；等待实际K线")

    def tick(self, clock=None):
        if self.active is None:
            return
        clock = time.monotonic() if clock is None else clock
        if clock-self.last_send < .75:
            return
        self.last_send = clock
        active = self.active
        quote = active["quote"]
        calendar = ()
        daily = active["streams"].get(86400)
        if daily is not None and self.api.is_serial_ready(daily):
            calendar = from_sdk(daily, 86400, quote)["datetime"].tolist()
        for seconds, serial in active["streams"].items():
            if not self.api.is_serial_ready(serial):
                continue
            frame = from_sdk(serial, seconds, quote, calendar=calendar)
            if frame.empty:
                continue
            symbol = active["symbol"]
            hashes = pd.util.hash_pandas_object(frame, index=False).to_numpy(copy=True)
            fingerprint = pd.Series(hashes, index=pd.DatetimeIndex(frame["datetime"]))
            previous = self.fingerprints.get(seconds)
            changed = (pd.Series(True, index=fingerprint.index) if previous is None else
                       fingerprint.ne(previous.reindex(fingerprint.index)))
            if not changed.any():
                continue
            full = seconds not in self.full_sent
            batch = frame if full else frame.loc[changed.to_numpy()].copy()
            self.store.upsert_bars(symbol, seconds, batch)
            self.publish("bars", symbol=symbol, seconds=seconds, revision=active["revision"],
                bars=records(batch), full=full, available=len(frame),
                quote_time=str(quote.get("datetime", "") or ""), last_price=finite(quote.get("last_price")),
                underlying=str(quote.get("underlying_symbol", "") or ""),
                multiplier=finite(quote.get("volume_multiple")), tick=finite(quote.get("price_tick")),
                received_at=str(now_beijing()))
            self.fingerprints[seconds] = fingerprint
            self.full_sent.add(seconds)
        self.publish("heartbeat", symbol=active["symbol"], revision=active["revision"],
                     quote_time=str(quote.get("datetime", "") or ""),
                     last_price=finite(quote.get("last_price")), received_at=str(now_beijing()))


def serve(config, commands, stopped, *, api_factory=create_api, publish=emit):
    username, password = config["username"], config["password"]
    secrets = username, password
    store = Store(config["database"])
    request = config.get("watch")
    first_connection = True
    while not stopped.is_set():
        api = None
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            api = api_factory(username, password, loop, lambda stage, text:publish("status", stage=stage, text=text))
            publish("status", stage="ready", text="行情连接已建立")
            if first_connection:
                publish("status", stage="catalog", text="正在获取交易所合约目录")
                try:
                    catalog = read_catalog(api)
                    store.save_catalog(catalog)
                    publish("catalog", instruments=catalog)
                except Exception as exc:
                    publish("status", stage="catalog", text="合约目录暂未取得："+redact(exc, secrets))
                first_connection = False
            pump = MarketPump(api, store, publish)
            latest = drain(commands)
            request = latest or request
            if request:
                pump.watch(request)
            while not stopped.is_set():
                latest = drain(commands)
                if latest is not None:
                    request = latest
                    # The public SDK has no kline unsubscribe. Recreate a bounded
                    # market connection after enough different subscriptions.
                    if len(pump.cache) >= 16 and any((request["symbol"], int(s)) not in pump.cache for s in request["seconds"]):
                        publish("status", stage="connecting", text="正在整理旧订阅并重新连接行情")
                        break
                    try:
                        pump.watch(request)
                    except Exception as exc:
                        publish("view_error", revision=request["revision"], text=redact(exc, secrets))
                api.wait_update(deadline=time.time()+.2)
                pump.tick()
        except Exception as exc:
            publish("error", text=redact(f"{type(exc).__name__}: {exc}", secrets))
            return
        finally:
            if api is not None:
                try:
                    api.close()
                except Exception:
                    pass
            loop.close()


def main():
    line = sys.stdin.readline()
    if not line:
        return
    config = json.loads(line)
    emit("hello", python=sys.executable, sdk=importlib.metadata.version("tqsdk"))
    commands, stopped = queue.Queue(), threading.Event()
    def reader():
        for line in sys.stdin:
            try:
                command = json.loads(line)
            except ValueError:
                continue
            if command.get("command") == "stop":
                stopped.set()
                return
            commands.put(command)
        stopped.set()
    threading.Thread(target=reader, daemon=True).start()
    serve(config, commands, stopped)


if __name__ == "__main__":
    main()
