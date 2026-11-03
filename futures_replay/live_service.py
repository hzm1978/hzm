from __future__ import annotations

"""Low-priority market-data subprocess. JSON commands arrive over stdin only.

No usernames/passwords are written to disk or placed in process arguments.
Only get_quote/get_kline_serial/wait_update are used; no paid download API.
"""

import asyncio
import json
import math
import os
from pathlib import Path
import queue
import re
import sys
import threading
import time

import pandas as pd

from .data_repo import DataRepository
from .edb_update import make_edb_contract_symbol, make_edb_main_symbol
from .live_store import write_live_snapshot
from .realtime_data import (RealtimeTarget, LIVE_PERIODS, LIVE_HISTORY_BARS, build_snapshot,
                             chart_frame, shanghai_time, create_tq_api, day_close_seconds)


PREFIX = "FRLIVE "


def safe_error(exc, secrets=()):
    message = f"{type(exc).__name__}: {exc}"
    for secret in secrets:
        if secret:
            message = message.replace(secret, "[已隐藏]")
    return message[:500]


def emit(kind, **values):
    print(PREFIX+json.dumps({"kind": kind, **values}, ensure_ascii=False), flush=True)


def tq_contract_symbol(exchange, contract):
    value = make_edb_contract_symbol(exchange, contract)
    if value.startswith("CZCE."):
        value = re.sub(r"^CZCE\.([A-Z]+)\d(\d{3})$", r"CZCE.\1\2", value)
    return value


def lower_priority():
    try:
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes
            kernel = ctypes.windll.kernel32
            kernel.GetCurrentProcess.restype = wintypes.HANDLE
            kernel.SetPriorityClass.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            kernel.SetPriorityClass(kernel.GetCurrentProcess(), 0x00100000)
        else:
            os.nice(10)
    except (OSError, AttributeError):
        pass


def resolve_targets(api, config):
    root, source = Path(config["root"]), config["source"]
    if config["mode"] == "current":
        if source == "原始行情" and config.get("main_symbol"):
            main = api.get_quote(config["main_symbol"])
            symbol = str(main.get("underlying_symbol", "") or "")
            if not symbol:
                raise ValueError("尚未返回当前实际主力合约，请稍后重试")
            local = DataRepository(root).list_contracts(config["symbol"],"1d",source)
            exchange = symbol.split(".")[0]
            contract = next((s for s in local if tq_contract_symbol(exchange,s) == symbol),symbol.split(".")[-1].upper())
            return [RealtimeTarget(root,source,config["symbol"],contract,symbol)]
        return [RealtimeTarget(root, source, config["symbol"], config["contract"], config["tq_symbol"])]
    repo = DataRepository(root)
    targets = []
    for meta in repo.list_instruments(source):
        try:
            main = make_edb_main_symbol(meta.exchange, meta.symbol)
            if source == "主力连续":
                sequences = repo.list_contracts(meta.symbol, "1d", source)
                valid = [s for s in sequences if not any(k in s.lower() for k in ("复权", "adjust", "指数", "index"))]
                contract = next((s for s in valid if s == "主连"), valid[0] if valid else "主连")
                target_symbol = main
            else:
                quote = api.get_quote(main)
                target_symbol = str(quote.get("underlying_symbol", "") or "")
                if not target_symbol:
                    raise ValueError("尚未返回活动合约")
                name = target_symbol.split(".")[-1]
                local = repo.list_contracts(meta.symbol, "1d", source)
                contract = next((s for s in local if tq_contract_symbol(meta.exchange, s) == target_symbol), name.upper())
            targets.append(RealtimeTarget(root, source, meta.symbol, contract, target_symbol))
        except Exception:
            emit("status", text=f"暂未订阅 {meta.symbol}，继续处理其他品种")
    return targets


def preview_payload(snapshot, period):
    frame, partial = chart_frame(snapshot, period)
    # One selected chart only; no arrays for all other products enter the GUI.
    records = json.loads(frame.to_json(orient="records", date_format="iso"))
    return {"period": period, "bars": records, "forming": partial is not None,
            "received_at": str(snapshot.received_at),
            "quote_time": str(snapshot.quote_time) if snapshot.quote_time is not None else None,
            "last_price": snapshot.last_price, "underlying": snapshot.underlying,
            "day_close_seconds": snapshot.day_close_seconds}


def collect(config, api_factory=None, *, command_queue=None, stop_event=None):
    lower_priority()
    commands = command_queue or queue.Queue()
    stopped = stop_event or threading.Event()
    loop = asyncio.new_event_loop(); asyncio.set_event_loop(loop)
    api, view, last_preview = None, None, 0.
    secrets = [str(config.get("password", "")), str(config.get("username", ""))]
    try:
        if stopped.is_set():
            return
        if api_factory is None:
            api_factory = create_tq_api
        emit("status", text="正在登录免费实时行情…")
        api = api_factory(config.pop("username"), config.pop("password"), loop)
        targets = resolve_targets(api, config)
        if not targets:
            raise ValueError("当前数据源未找到可订阅的品种，请检查品种交易所信息")
        active, deferred = [], []
        for i, target in enumerate(targets):
            if stopped.is_set():
                break
            while not commands.empty():
                command = commands.get_nowait()
                if command.get("command") == "stop":
                    stopped.set()
                else:
                    deferred.append(command)
            if stopped.is_set():
                break
            try:
                quote = api.get_quote(target.tq_symbol)
                serials = {key: api.get_kline_serial(target.tq_symbol, LIVE_PERIODS[key],
                    data_length=LIVE_HISTORY_BARS) for key in ("1min", "1d")}
                active.append({"target": target, "quote": quote, "serials": serials,
                               "signature": None, "fingerprints": {}, "next_check": 0.})
                emit("status", text=f"后台订阅 {i+1}/{len(targets)}：{target.symbol}")
            except Exception:
                emit("status", text=f"{target.symbol}订阅未完成，其他品种继续更新")
        for command in deferred:
            commands.put(command)
        if not active and not stopped.is_set():
            raise ValueError("未建立有效订阅，请检查网络及当前合约")
        emit("started", count=len(active), mode=config["mode"], targets=[dict(
            source=x["target"].source,symbol=x["target"].symbol,contract=x["target"].contract,
            tq_symbol=x["target"].tq_symbol) for x in active])
        cursor, last_health = 0, 0.
        while not stopped.is_set():
            while not commands.empty():
                command = commands.get_nowait()
                if command.get("command") == "stop":
                    stopped.set(); break
                if command.get("command") == "view":
                    try:
                        if not command.get("enabled", True):
                            view = None; continue
                        symbol, period = command["tq_symbol"], command.get("period", "1min")
                        if period not in LIVE_PERIODS:
                            period = "1d"
                        source = next((item for item in active if item["target"].tq_symbol == symbol), None)
                        quote = source["quote"] if source else api.get_quote(symbol)
                        daily = source["serials"]["1d"] if source else api.get_kline_serial(symbol, 86400, data_length=LIVE_HISTORY_BARS)
                        native = (source["serials"][period] if source and period in source["serials"]
                                  else api.get_kline_serial(symbol, LIVE_PERIODS[period], data_length=LIVE_HISTORY_BARS))
                        view = {"symbol": symbol, "period": period, "serials": {"1d": daily, period: native}, "quote": quote}
                    except Exception:
                        emit("view_error", text="当前合约的实时图尚未就绪，后台采集继续运行")
            if stopped.is_set():
                break
            api.wait_update(deadline=time.time()+.25)
            now = time.monotonic()
            if view and now-last_preview >= 1:
                try:
                    if all(api.is_serial_ready(frame) for frame in view["serials"].values()):
                        snap = build_snapshot(view["serials"], view["quote"])
                        emit("preview", tq_symbol=view["symbol"], **preview_payload(snap, view["period"]))
                except Exception:
                    emit("view_error", text="实时图暂未就绪，后台更新继续运行")
                last_preview = now
            if now-last_health > 10:
                emit("status", text=f"免费行情后台运行中 · {len(active)}个品种；复盘进度保持不变")
                last_health = now
            if active:
                item = active[cursor % len(active)]; cursor += 1
                if now >= item["next_check"]:
                    item["next_check"] = now+2
                    if all(api.is_serial_ready(frame) for frame in item["serials"].values()):
                        minute = item["serials"]["1min"]
                        daily_frame = item["serials"]["1d"]
                        if minute.empty or daily_frame.empty:
                            continue
                        try:
                            stamps = float(minute.iloc[-1]["datetime"]), float(daily_frame.iloc[-1]["datetime"])
                            if not all(math.isfinite(value) and 0 < value < pd.Timestamp.max.value for value in stamps):
                                continue
                        except (TypeError, ValueError):
                            continue
                        last = minute.iloc[-1]
                        boundary = shanghai_time().tz_localize("Asia/Shanghai").value
                        k = -1 if float(last["datetime"])+60e9 <= boundary else -2
                        if k == -2 and len(minute) < 2:
                            continue
                        completed = minute.iloc[k]
                        daily = item["serials"]["1d"].iloc[-1]
                        day_end = pd.Timestamp(int(daily["datetime"]), unit="ns", tz="UTC").tz_convert("Asia/Shanghai").tz_localize(None).normalize()
                        day_end += pd.Timedelta(seconds=day_close_seconds(item["quote"].get("trading_time", {})))
                        day_closed = shanghai_time() >= day_end
                        day_signature = tuple(daily.get(key) for key in ("open", "high", "low", "close", "volume")) if day_closed else ()
                        signature = tuple(completed.get(key) for key in ("datetime", "open", "high", "low", "close", "volume")) + (daily.get("datetime"), day_closed, day_signature)
                        if signature != item["signature"]:
                            try:
                                snapshot = build_snapshot(item["serials"], item["quote"])
                                ids = pd.to_numeric(minute["id"], errors="coerce")
                                first_id = int(ids.loc[ids.ge(0)].iloc[0]) if ids.ge(0).any() else 0
                                stats = write_live_snapshot(item["target"], snapshot, item["fingerprints"], first_minute_id=first_id)
                                item["signature"] = signature
                                if stats["changed"]:
                                    target = item["target"]
                                    emit("saved", source=target.source, symbol=target.symbol, contract=target.contract,
                                         minute_latest=stats["minute_latest"], daily_latest=stats["daily_latest"])
                            except Exception as exc:
                                emit("status", text=f"{item['target'].symbol}保存未完成：{safe_error(exc,secrets)}；稍后重试")
    except Exception as exc:
        message = str(exc)
        for secret in secrets:
            if secret:
                message = message.replace(secret, "[已隐藏]")
        emit("error", text=message[:800])
    finally:
        if api is not None:
            try:
                api.close()
            except Exception:
                pass
        if not loop.is_closed():
            loop.close()
        asyncio.set_event_loop(None)
        emit("stopped")


def main():
    line = sys.stdin.readline()
    if not line:
        return
    config = json.loads(line)
    commands, stopped = queue.Queue(), threading.Event()
    def reader():
        for line in sys.stdin:
            try:
                command = json.loads(line)
                if command.get("command") == "stop":
                    stopped.set()
                commands.put(command)
            except (ValueError, TypeError):
                pass
        stopped.set()
    threading.Thread(target=reader, daemon=True).start()
    collect(config, command_queue=commands, stop_event=stopped)


if __name__ == "__main__":
    main()
