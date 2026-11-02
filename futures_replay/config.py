from __future__ import annotations

from pathlib import Path

APP_NAME = "期货复盘训练系统"
ORG_NAME = "LocalReplayTrainer"
DEFAULT_INITIAL_CAPITAL = 1_000_000.0
DEFAULT_MARGIN_RATE = 0.12
CACHE_ITEMS = 40
MAX_VISIBLE_BARS = 260
MIN_VISIBLE_BARS = 30
DEFAULT_VISIBLE_BARS = 120

PERIODS = ["1min", "5min", "15min", "30min", "1h", "4h", "1d", "1w", "1M"]
PERIOD_SECONDS = {
    "1min": 60,
    "5min": 300,
    "15min": 900,
    "30min": 1800,
    "1h": 3600,
    "4h": 14400,
    "1d": 86400,
}

SOURCE_DIRS = {
    "原始行情": "qihushuju_parquet",
    "主力连续": "qihuozldata_parquet",
    "结算价": "settle_parquet",
}

# Common Chinese futures contract multipliers. Unknown symbols fall back to 10.
CONTRACT_MULTIPLIERS = {
    "AU": 1000,
    "AG": 15,
    "CU": 5,
    "AL": 5,
    "ZN": 5,
    "PB": 5,
    "NI": 1,
    "SN": 1,
    "RB": 10,
    "HC": 10,
    "SS": 5,
    "RU": 10,
    "BU": 10,
    "FU": 10,
    "SC": 1000,
    "LU": 10,
    "NR": 10,
    "SP": 10,
    "EC": 50,
    "IF": 300,
    "IH": 300,
    "IC": 200,
    "IM": 200,
    "T": 10000,
    "TF": 10000,
    "TS": 20000,
    "TL": 10000,
    "M": 10,
    "Y": 10,
    "P": 10,
    "C": 10,
    "CS": 10,
    "A": 10,
    "B": 10,
    "JM": 60,
    "J": 100,
    "I": 100,
    "PP": 5,
    "L": 5,
    "V": 5,
    "EG": 10,
    "EB": 5,
    "PG": 20,
    "MA": 10,
    "TA": 5,
    "FG": 20,
    "SR": 10,
    "CF": 5,
    "OI": 10,
    "RM": 10,
    "SA": 20,
    "UR": 20,
    "AP": 10,
    "CJ": 5,
    "PK": 5,
}


def locate_data_root(start: Path) -> Path | None:
    """Locate either the canonical qihuodata tree or a development sample tree.

    Besides ``qihuodata/``, accept a folder that directly contains instrument
    directories such as ``EC/1min``.  This makes ``EC`` placed beside ``run.py``
    work with a plain ``python run.py`` in VS Code.
    """
    current = start.resolve()
    for _ in range(9):
        candidate = current / "qihuodata"
        if candidate.exists() and candidate.is_dir():
            return candidate
        try:
            direct = any(
                child.is_dir() and (child / "1min").is_dir() and
                ((child / "1d").is_dir() or (child / "数据说明.md").exists())
                for child in current.iterdir()
            )
            if direct:
                return current
        except OSError:
            pass
        if current.parent == current:
            break
        current = current.parent
    return None
