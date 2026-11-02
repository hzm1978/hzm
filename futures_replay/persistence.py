from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from .config import APP_NAME, ORG_NAME


def _settings():
    from PySide6.QtCore import QSettings
    return QSettings(ORG_NAME, APP_NAME)


def _portable_state_file() -> Path:
    """A visible fallback next to run.py/executable.

    QSettings remains the primary store, but keeping the same state in a small
    JSON file makes session restoration robust when Python/Qt installations or
    Windows registry views change.
    """
    try:
        entry = Path(sys.argv[0]).resolve()
        base = entry.parent
    except Exception:
        base = Path.cwd()
    return base / "futures_replay_session.json"




def _portable_main_list_sort_file() -> Path:
    """Small independent preference file for the main-continuous watch-list sort."""
    return _portable_state_file().with_name("futures_replay_main_list_sort.json")


def _portable_replay_heads_file() -> Path:
    """Independent replay-head checkpoint file.

    Replay locations are intentionally persisted separately from the larger UI
    state.  A serialization problem in an unrelated widget must never make a
    played product lose its last K-line.
    """
    return _portable_state_file().with_name("futures_replay_heads.json")


def _json_default(obj):
    """Best-effort conversion for Qt/numpy/pandas-like scalar values."""
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, (set, tuple)):
        return list(obj)
    if isinstance(obj, (bytes, bytearray)):
        return bytes(obj).hex()
    value = getattr(obj, "value", None)
    if value is not None and value is not obj:
        try:
            json.dumps(value)
            return value
        except Exception:
            pass
    item = getattr(obj, "item", None)
    if callable(item):
        try:
            return item()
        except Exception:
            pass
    iso = getattr(obj, "isoformat", None)
    if callable(iso):
        try:
            return iso()
        except Exception:
            pass
    return str(obj)


def _atomic_write_json(path: Path, payload: Any) -> None:
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=_json_default)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def load_state() -> dict[str, Any]:
    # Prefer the portable file when present because it is the exact state from
    # the last run of this copy of the trainer.  Fall back to QSettings for
    # backward compatibility with earlier versions.
    path = _portable_state_file()
    try:
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    try:
        raw = _settings().value("state", "{}")
        data = json.loads(raw) if raw else {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_state(state: dict[str, Any]) -> None:
    text = json.dumps(state, ensure_ascii=False, separators=(",", ":"), default=_json_default)
    # Keep the legacy QSettings copy.
    try:
        s = _settings()
        s.setValue("state", text)
        s.sync()
    except Exception:
        pass

    # Also write atomically beside run.py/exe.
    path = _portable_state_file()
    try:
        _atomic_write_json(path, state)
    except Exception:
        # QSettings may still have succeeded, so session saving should never
        # prevent the application from closing.
        pass


def save_replay_heads(sessions: dict[str, Any]) -> None:
    """Atomically checkpoint per-product replay positions only."""
    payload = {
        "saved_at_ns": time.time_ns(),
        "sessions": sessions if isinstance(sessions, dict) else {},
    }
    _atomic_write_json(_portable_replay_heads_file(), payload)


def load_replay_heads() -> dict[str, Any]:
    path = _portable_replay_heads_file()
    try:
        if not path.exists():
            return {}
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return {}
        sessions = payload.get("sessions", {})
        return sessions if isinstance(sessions, dict) else {}
    except Exception:
        return {}


def clear_replay_heads() -> None:
    try:
        _portable_replay_heads_file().unlink(missing_ok=True)
    except Exception:
        pass



def save_main_list_sort(column: int, order: str, symbol_order=None) -> None:
    """Persist watch-list sort and its exact visible symbol order.

    The exact order is needed as a stable tie-breaker.  On restart, rows that
    were ``播放中`` are intentionally not auto-resumed and become ``已播放``;
    saving only the status column/direction would therefore lose the user's
    previous ordering among equal-status rows.
    """
    try:
        col = max(0, int(column))
    except Exception:
        col = 0
    raw = str(order or "asc").strip().lower()
    direction = "desc" if raw in {"desc", "descending", "1"} else "asc"
    cleaned = []
    if isinstance(symbol_order, (list, tuple)):
        seen = set()
        for value in symbol_order:
            sym = str(value or "").strip()
            if sym and sym not in seen:
                seen.add(sym)
                cleaned.append(sym)
    _atomic_write_json(_portable_main_list_sort_file(), {
        "saved_at_ns": time.time_ns(),
        "sort_column": col,
        "sort_order": direction,
        "symbol_order": cleaned,
    })


def load_main_list_sort() -> dict[str, Any]:
    path = _portable_main_list_sort_file()
    try:
        if not path.exists():
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def clear_main_list_sort() -> None:
    try:
        _portable_main_list_sort_file().unlink(missing_ok=True)
    except Exception:
        pass


def clear_state() -> None:
    try:
        s = _settings()
        s.clear()
        s.sync()
    except Exception:
        pass
    try:
        _portable_state_file().unlink(missing_ok=True)
    except Exception:
        pass
    clear_replay_heads()
    clear_main_list_sort()
