"""SQLite WAL cache and separate, immutable training snapshots."""
from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import uuid
from tempfile import NamedTemporaryFile

import pandas as pd

from .bars import COLUMNS, empty_bars, now_beijing, records, validate_frame


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS bars(
                    symbol TEXT, seconds INTEGER, datetime TEXT, open REAL, high REAL,
                    low REAL, close REAL, volume REAL, open_interest REAL, closed INTEGER, trading_day TEXT,
                    PRIMARY KEY(symbol, seconds, datetime));
                CREATE TABLE IF NOT EXISTS catalog(symbol TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS sessions(
                    id TEXT PRIMARY KEY, name TEXT, symbol TEXT, period TEXT,
                    cursor INTEGER, snapshot TEXT NOT NULL, state TEXT NOT NULL, updated TEXT);
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        try:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA busy_timeout=5000")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def upsert_bars(self, symbol, seconds, frame):
        frame = validate_frame(frame)
        if frame.empty:
            return 0
        payload = [(symbol, seconds, row["datetime"], *[row[key] for key in COLUMNS[1:]])
                   for row in records(frame)]
        with self.connect() as db:
            db.executemany("INSERT OR REPLACE INTO bars VALUES(?,?,?,?,?,?,?,?,?,?,?)", payload)
        return len(payload)

    def load_bars(self, symbol, seconds, limit=100000, start=None, end=None):
        condition = "symbol=? AND seconds=?"
        params = [symbol, int(seconds)]
        if start:
            condition += " AND trading_day>=?"
            params.append(str(start))
        if end:
            condition += " AND trading_day<=?"
            params.append(str(end))
        params.append(int(limit))
        with self.connect() as db:
            rows = db.execute(f"""SELECT datetime,open,high,low,close,volume,open_interest,closed,trading_day
                FROM (SELECT * FROM bars WHERE {condition} ORDER BY datetime DESC LIMIT ?)
                ORDER BY datetime""", params).fetchall()
        return validate_frame(pd.DataFrame(rows, columns=COLUMNS)) if rows else empty_bars()

    def save_catalog(self, instruments):
        with self.connect() as db:
            db.executemany("INSERT OR REPLACE INTO catalog VALUES(?,?)",
                           [(x["symbol"], json.dumps(x, ensure_ascii=False)) for x in instruments])

    def load_catalog(self):
        with self.connect() as db:
            return [json.loads(row[0]) for row in db.execute("SELECT payload FROM catalog ORDER BY symbol")]

    def setting(self, key, default=None):
        with self.connect() as db:
            row = db.execute("SELECT payload FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_setting(self, key, value):
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO settings VALUES(?,?)",
                       (key, json.dumps(value, ensure_ascii=False, allow_nan=False)))

    def new_session(self, symbol, period, frame, cursor, state, name=""):
        identifier = uuid.uuid4().hex
        name = name or f"{symbol} · {now_beijing():%m-%d %H:%M}"
        with self.connect() as db:
            db.execute("INSERT INTO sessions VALUES(?,?,?,?,?,?,?,?)",
                (identifier, name, symbol, period, cursor, json.dumps(records(frame), ensure_ascii=False),
                 json.dumps(state, ensure_ascii=False, allow_nan=False), str(now_beijing())))
        return identifier

    def save_session(self, identifier, cursor, state):
        with self.connect() as db:
            db.execute("UPDATE sessions SET cursor=?,state=?,updated=? WHERE id=?",
                       (cursor, json.dumps(state, ensure_ascii=False, allow_nan=False),
                        str(now_beijing()), identifier))

    def list_sessions(self):
        with self.connect() as db:
            rows = db.execute("SELECT id,name,symbol,period,cursor,updated FROM sessions ORDER BY updated DESC LIMIT 200").fetchall()
        return [dict(zip(("id", "name", "symbol", "period", "cursor", "updated"), row)) for row in rows]

    def load_session(self, identifier):
        with self.connect() as db:
            row = db.execute("SELECT symbol,period,cursor,snapshot,state,name FROM sessions WHERE id=?", (identifier,)).fetchone()
        if row is None:
            raise ValueError("训练记录不存在")
        return dict(id=identifier, symbol=row[0], period=row[1], cursor=row[2],
                    frame=validate_frame(pd.DataFrame(json.loads(row[3]))),
                    state=json.loads(row[4]), name=row[5])

    def backup(self, destination):
        destination = Path(destination)
        if destination.resolve() == self.path.resolve():
            raise ValueError("备份文件不能覆盖当前工作区")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(dir=destination.parent, suffix=".tmp", delete=False) as file:
            temporary = Path(file.name)
        try:
            with self.connect() as source:
                target = sqlite3.connect(temporary)
                try:
                    source.backup(target)
                    target.commit()
                finally:
                    target.close()
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)
