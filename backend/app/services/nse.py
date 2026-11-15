"""NSE corporate announcements: live fetch with retries, runtime cache, committed seed."""
from __future__ import annotations

import random
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote

import requests

from .. import cache, config
from .market_data import normalize_symbol

IST = timezone(timedelta(hours=5, minutes=30))
BASE = "https://www.nseindia.com"
API = BASE + "/api/corporate-announcements"
HEADERS = {
    "User-Agent": config.USER_AGENT,
    "Accept": "application/json,text/plain,*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": BASE + "/companies-listing/corporate-filings-announcements",
}

_session: requests.Session | None = None
_session_lock = threading.Lock()
_rate_lock = threading.Lock()
_last_call = 0.0
_refreshing: set[str] = set()
_refresh_lock = threading.Lock()


def _bootstrap() -> requests.Session:
    s = requests.Session()
    try:
        s.get(BASE, headers=HEADERS, timeout=12)  # sets cookies (may 403; cookies still issued)
    except requests.RequestException:
        pass
    return s


def _get_session(refresh: bool = False) -> requests.Session:
    global _session
    with _session_lock:
        if _session is None or refresh:
            _session = _bootstrap()
        return _session


def _throttle() -> None:
    global _last_call
    with _rate_lock:
        wait = 1.0 - (time.time() - _last_call)
        if wait > 0:
            time.sleep(wait)
        _last_call = time.time()


def _parse_dt(s: str | None) -> str | None:
    for fmt in ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y"):
        try:
            return datetime.strptime(s or "", fmt).replace(tzinfo=IST).isoformat()
        except ValueError:
            continue
    return None


def _normalize(rec: dict) -> dict | None:
    ts = _parse_dt(rec.get("an_dt") or rec.get("exchdisstime"))
    if not ts:
        return None
    subject = (rec.get("desc") or "").strip()
    att = rec.get("attchmntFile") or None
    sym = rec.get("symbol")
    return {
        "id": cache.sha(sym, ts, subject, att or "", n=16),
        "symbol": sym,
        "company": rec.get("sm_name"),
        "ts": ts,
        "subject": subject,
        "text": (rec.get("attchmntText") or "").strip(),
        "attachment": att,
        "industry": rec.get("smIndustry"),
        "filing_url": f"{BASE}/get-quotes/equity?symbol={quote(sym or '')}",
    }


def _fetch_range(symbol: str, d0: datetime, d1: datetime) -> list[dict]:
    params = {
        "index": "equities",
        "symbol": symbol,
        "from_date": d0.strftime("%d-%m-%Y"),
        "to_date": d1.strftime("%d-%m-%Y"),
    }
    last_err: Exception | None = None
    for attempt in range(3):
        _throttle()
        sess = _get_session(refresh=(attempt == 1))
        try:
            r = sess.get(API, params=params, headers=HEADERS, timeout=15)
            if r.status_code == 200:
                data = r.json()
                if isinstance(data, list):
                    return [n for n in (_normalize(x) for x in data) if n]
                return []
            last_err = RuntimeError(f"NSE {r.status_code}")
        except (requests.RequestException, ValueError) as e:
            last_err = e
        time.sleep((2 ** attempt) + random.random())
    raise RuntimeError(f"NSE unavailable: {last_err}")


def _live_fetch(symbol: str, days: int) -> list[dict]:
    end = datetime.now(IST)
    start = end - timedelta(days=days)
    out: list[dict] = []
    cur = start
    while cur < end:  # bounded chunks of at most 30 days
        nxt = min(cur + timedelta(days=30), end)
        out.extend(_fetch_range(symbol, cur, nxt))
        cur = nxt + timedelta(days=1)
    return out


def _merge(*lists: list[dict]) -> list[dict]:
    seen: dict[str, dict] = {}
    for lst in lists:
        for r in lst or []:
            seen[r["id"]] = r
    return sorted(seen.values(), key=lambda r: r["ts"], reverse=True)


def _load(symbol: str):
    cached = cache.read_json(cache.cache_path("nse", f"{symbol}.json")) or {}
    seeded = cache.read_json(cache.seed_path("nse", f"{symbol}.json")) or {}
    return cached, seeded


def refresh(symbol: str, days: int) -> dict[str, Any]:
    """Blocking live refresh. Raises on failure."""
    symbol = normalize_symbol(symbol)
    cached, seeded = _load(symbol)
    recs = _live_fetch(symbol, days)
    merged = _merge(cached.get("records", []), recs)
    prev_cov = cached.get("covered_days", 0)
    store = {
        "symbol": symbol,
        "fetched_at": cache.iso_now(),
        "covered_days": max(prev_cov, days),
        "records": merged,
    }
    cache.put_cache("nse", f"{symbol}.json", store)
    return store


def _bg_refresh(symbol: str, days: int) -> None:
    with _refresh_lock:
        if symbol in _refreshing:
            return
        _refreshing.add(symbol)

    def run():
        try:
            refresh(symbol, days)
        except Exception:
            pass
        finally:
            with _refresh_lock:
                _refreshing.discard(symbol)

    threading.Thread(target=run, daemon=True).start()


def get_announcements(symbol: str, days: int = 1, wait: bool = False, force: bool = False) -> dict[str, Any]:
    """Resolution order: live NSE -> fresh cache -> stale cache -> committed seed.

    wait=False gives stale-while-revalidate (returns immediately, refreshes in background).
    """
    symbol = normalize_symbol(symbol)
    fetch_days = max(days, 3)
    cached, seeded = _load(symbol)
    age = None
    if cached.get("fetched_at"):
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(cached["fetched_at"])).total_seconds()
    covered = cached.get("covered_days", 0)
    fresh = age is not None and age < config.NSE_TTL and covered >= fetch_days
    status = {"source": "cache", "is_stale": False, "as_of": cached.get("fetched_at"), "refreshing": False}

    if not fresh or force:
        if wait or force or not (cached or seeded):
            try:
                cached = refresh(symbol, fetch_days)
                status.update(source="live", as_of=cached["fetched_at"])
            except Exception:
                status["is_stale"] = True
        else:
            _bg_refresh(symbol, fetch_days)
            status.update(is_stale=age is None or age >= config.NSE_TTL, refreshing=True)

    if status["source"] == "cache" and not cached.get("records"):
        status.update(source="seed", as_of=seeded.get("fetched_at"), is_stale=True)
    recs = _merge(seeded.get("records", []), cached.get("records", []))
    cutoff = (datetime.now(IST) - timedelta(days=days)).isoformat()
    # Seed data is a dated snapshot: if live data is unavailable, anchor the window to the snapshot date.
    if status["source"] == "seed" and seeded.get("fetched_at"):
        snap = datetime.fromisoformat(seeded["fetched_at"]).astimezone(IST)
        cutoff = (snap - timedelta(days=days)).isoformat()
    return {"records": [r for r in recs if r["ts"] >= cutoff], **status}
