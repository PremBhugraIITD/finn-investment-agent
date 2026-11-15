"""File-backed cache with a committed-seed read-through layer.

Lookup order everywhere: runtime cache (data/cache) -> committed seed (data/seed).
Writes are atomic and only ever go to the runtime cache.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import config

_lock = threading.Lock()


def now_ts() -> float:
    return time.time()


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha(*parts: Any, n: int = 24) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(str(p).encode("utf-8", "ignore"))
        h.update(b"\x1f")
    return h.hexdigest()[:n]


def _open_read(path: Path):
    if path.suffix == ".gz":
        return gzip.open(path, "rt", encoding="utf-8")
    return open(path, "r", encoding="utf-8")


def read_json(path: Path) -> Any | None:
    try:
        with _open_read(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError, EOFError):
        return None


def write_json(path: Path, obj: Any) -> None:
    """Atomic write; gzip if the suffix is .gz."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        if path.suffix == ".gz":
            with os.fdopen(fd, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb") as gz:
                gz.write(json.dumps(obj, ensure_ascii=False).encode("utf-8"))
        else:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(obj, f, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def cache_path(sub: str, name: str) -> Path:
    return config.CACHE / sub / name


def seed_path(sub: str, name: str) -> Path:
    return config.SEED / sub / name


def get_layered(sub: str, name: str) -> tuple[Any | None, str | None, float | None]:
    """Return (value, origin, age_seconds). origin is 'cache' or 'seed'."""
    p = cache_path(sub, name)
    v = read_json(p)
    if v is not None:
        return v, "cache", max(0.0, now_ts() - p.stat().st_mtime)
    p = seed_path(sub, name)
    v = read_json(p)
    if v is not None:
        return v, "seed", None
    return None, None, None


def put_cache(sub: str, name: str, obj: Any) -> None:
    write_json(cache_path(sub, name), obj)


# --- daily budget counters (in-process; plan: admin-overridable via env) ---
_budget: dict[str, Any] = {"day": None, "cheap": 0, "deep": 0}


def budget_take(kind: str) -> bool:
    limit = config.MAX_CHEAP_CALLS if kind == "cheap" else config.MAX_DEEP_CALLS
    today = datetime.now(timezone.utc).date().isoformat()
    with _lock:
        if _budget["day"] != today:
            _budget.update(day=today, cheap=0, deep=0)
        if _budget[kind] >= limit:
            return False
        _budget[kind] += 1
        return True


def budget_status() -> dict[str, Any]:
    return {k: _budget[k] for k in ("day", "cheap", "deep")} | {
        "cheap_limit": config.MAX_CHEAP_CALLS,
        "deep_limit": config.MAX_DEEP_CALLS,
    }
