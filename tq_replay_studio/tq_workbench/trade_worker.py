"""TqSim / TqKq / explicitly unlocked TqAccount trading subprocess."""
import asyncio
import hashlib
import json
import queue
import sys
import threading
import time

from .bars import finite
from .sdk_worker import create_api, emit, redact
from .trade_gate import TradeGate, TradePolicy


def create_trade_api(config, loop, progress):
    from tqsdk import TqAccount, TqKq, TqSim
    mode = config["mode"]
    if mode == "live":
        if not config.get("broker_account") or not config.get("broker_password"):
            raise ValueError("请输入期货资金账号和交易密码")
        account = TqAccount(config.get("broker", "H徽商期货"), config["broker_account"], config["broker_password"])
    elif mode == "kq":
        account = TqKq()
    elif mode == "sim":
        account = TqSim(init_balance=float(config.get("capital", 1_000_000)))
    else:
        raise ValueError("未知账户类型")
    return create_api(config["username"], config["password"], loop, progress, account=account)


def clean_entity(entity, keys):
    result = {}
    for key in keys:
        value = entity.get(key)
        if isinstance(value, (int, float)):
            result[key] = finite(value)
        else:
            result[key] = str(value or "")
    return result


def snapshot(api, gate, revision):
    account = clean_entity(api.get_account(), ("balance", "available", "float_profit", "position_profit",
        "margin", "risk_ratio", "commission", "close_profit"))
    positions = []
    for symbol, position in api.get_position().items():
        row = clean_entity(position, ("pos_long", "pos_short", "pos_long_today", "pos_long_his",
            "pos_short_today", "pos_short_his", "open_price_long", "open_price_short", "float_profit", "margin"))
        if finite(row.get("pos_long"), 0) or finite(row.get("pos_short"), 0):
            positions.append(dict(symbol=symbol, **row))
    orders = [clean_entity(order, ("order_id", "exchange_id", "instrument_id", "direction", "offset",
        "volume_orign", "volume_left", "limit_price", "status", "last_msg", "insert_date_time"))
        for order in api.get_order().values()]
    trades = [clean_entity(trade, ("trade_id", "order_id", "exchange_id", "instrument_id",
        "direction", "offset", "volume", "price", "trade_date_time")) for trade in api.get_trade().values()]
    quote = clean_entity(gate.quote, ("datetime", "last_price", "bid_price1", "ask_price1", "bid_volume1",
        "ask_volume1", "price_tick", "volume_multiple", "upper_limit", "lower_limit"))
    return dict(mode=gate.mode, armed=gate.armed, symbol=gate.symbol, revision=revision,
                account=account, positions=positions, orders=orders[-300:], trades=trades[-300:], quote=quote)


def serve_trade(config, commands, stopped, *, api_factory=create_trade_api, publish=emit):
    mode = config.get("mode", "sim")
    secrets = tuple(config.get(key, "") for key in ("username", "password", "broker_account", "broker_password"))
    loop = asyncio.new_event_loop(); asyncio.set_event_loop(loop)
    api = None
    try:
        api = api_factory(config, loop, lambda stage,text:publish("status", stage=stage, text=text))
        gate = TradeGate(api, mode, TradePolicy(**config.get("policy", {})))
        # Merely logging in never enables a live send.
        publish("status", stage="ready", text="实盘已登录 · 下单锁定" if mode == "live" else "实时模拟账户已连接")
        request = config.get("watch") or {}
        revision = request.get("revision", 0)
        if request.get("symbol"):
            gate.watch(request["symbol"])
        last_emit, last_signature = 0., ""
        while not stopped.is_set():
            while not stopped.is_set():
                try:
                    command = commands.get_nowait()
                except queue.Empty:
                    break
                try:
                    kind = command.get("command")
                    if kind == "watch":
                        gate.watch(command["symbol"]); revision = command.get("revision", revision)
                    elif kind == "arm":
                        gate.arm(command.get("enabled"))
                        publish("armed", enabled=gate.armed, mode=mode)
                    elif kind == "prepare":
                        publish("review", **gate.prepare(command["order"]))
                    elif kind == "submit":
                        order = gate.submit(command.get("token", ""))
                        publish("order_sent", order_id=str(order.get("order_id", "")), mode=mode)
                    elif kind == "cancel":
                        gate.cancel(command["order_id"])
                        publish("cancel_sent", order_id=command["order_id"])
                except Exception as exc:
                    publish("command_error", text=redact(f"{type(exc).__name__}: {exc}", secrets))
            api.wait_update(deadline=time.time()+.2)
            now = time.monotonic()
            if now-last_emit >= .5:
                result = snapshot(api, gate, revision)
                signature = hashlib.blake2b(json.dumps(result, sort_keys=True, allow_nan=False).encode(), digest_size=12).hexdigest()
                if signature != last_signature:
                    publish("trade_snapshot", **result)
                    last_signature = signature
                last_emit = now
    except Exception as exc:
        publish("error", text=redact(f"{type(exc).__name__}: {exc}", secrets))
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
    commands, stopped = queue.Queue(), threading.Event()
    def reader():
        for line in sys.stdin:
            try:
                command = json.loads(line)
            except ValueError:
                continue
            if command.get("command") == "stop":
                stopped.set(); return
            commands.put(command)
        stopped.set()
    threading.Thread(target=reader, daemon=True).start()
    serve_trade(config, commands, stopped)


if __name__ == "__main__":
    main()
