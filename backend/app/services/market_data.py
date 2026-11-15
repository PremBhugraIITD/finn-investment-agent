"""End-of-day price and volume via yfinance, with cache -> seed fallback.

Cloud IPs (e.g. Render) are often rate-limited by Yahoo (HTTP 429 / 401 Invalid Crumb). So the request
path is built to never wait on Yahoo for long:

* all stale tickers are fetched concurrently and share ONE total deadline (config.MARKET_LIVE_BUDGET_S);
* anything not back in time is served instantly from the runtime cache or the committed seed (flagged stale);
* the slow work keeps running in the background and, if it succeeds, is written to the cache by a callback;
* any provider failure trips a circuit breaker so the next requests skip Yahoo entirely (no repeated 6s waits)
  until it expires, then a single batch probes again;
* retries are disabled and per-request timeouts are short; the slow `Ticker.info` call is never on the
  request path (names / market caps come from cache or seed).
"""
from __future__ import annotations

import re
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor, wait
from typing import Any

from .. import cache, config
from ..models.schemas import FinnError

SYMBOL_RE = re.compile(r"^[A-Z0-9&-]{1,20}$")

HISTORY_TIMEOUT_S = 4          # per Yahoo request (yfinance default is 10-30s)
_pool = ThreadPoolExecutor(max_workers=24, thread_name_prefix="yf")   # 18 portfolio + headroom
_lock = threading.Lock()
_inflight: dict[str, Future] = {}
_breaker: dict[str, Any] = {"until": 0.0, "reason": None}
_info_cooldown: dict[str, float] = {}
_yf_configured = False


# ---------------------------------------------------------------- basics
def normalize_symbol(raw: str) -> str:
    s = (raw or "").strip().upper()
    for suf in (".NS", ".BO"):
        if s.endswith(suf):
            s = s[: -len(suf)]
    if not SYMBOL_RE.match(s):
        raise FinnError("INVALID_TICKER", f"'{raw}' is not a valid NSE symbol.", 422)
    return s


def cap_band(mcap_cr: float | None) -> str | None:
    if not mcap_cr:
        return None
    if mcap_cr >= 80_000:
        return "Large"
    if mcap_cr >= 25_000:
        return "Mid"
    return "Small"


def compute(rows: list[dict]) -> dict[str, Any]:
    """Pure arithmetic: daily move and volume ratio against the prior 20 sessions."""
    if len(rows) < 2:
        return {"last_close": rows[-1]["close"] if rows else None}
    last, prev = rows[-1], rows[-2]
    base = [r["volume"] for r in rows[-21:-1] if r.get("volume")]
    avg20 = sum(base) / len(base) if base else None
    return {
        "last_close": last["close"],
        "prev_close": prev["close"],
        "change_pct": round((last["close"] / prev["close"] - 1) * 100, 2) if prev["close"] else None,
        "volume": last["volume"],
        "avg_volume_20d": round(avg20) if avg20 else None,
        "volume_ratio": round(last["volume"] / avg20, 2) if avg20 else None,
        "last_date": last["date"],
    }


# ---------------------------------------------------------------- circuit breaker
def live_blocked() -> bool:
    return time.time() < _breaker["until"]


def live_status() -> dict[str, Any]:
    """Local-only status (no I/O) for /ready."""
    left = max(0.0, _breaker["until"] - time.time())
    return {"live_blocked": left > 0, "retry_in_s": round(left), "reason": _breaker["reason"] if left > 0 else None}


def _trip(reason: str, seconds: float) -> None:
    with _lock:
        _breaker["until"] = max(_breaker["until"], time.time() + seconds)
        _breaker["reason"] = reason


# ---------------------------------------------------------------- live fetch (runs in the pool)
def _configure_yf() -> None:
    """Disable yfinance's internal retry/backoff once; failures must surface fast."""
    global _yf_configured
    if _yf_configured:
        return
    try:
        import yfinance as yf
        yf.config.network.retries = 0
    except Exception:
        pass
    _yf_configured = True


def _fetch_live(symbol: str) -> dict[str, Any]:
    _configure_yf()
    import yfinance as yf

    t = yf.Ticker(f"{symbol}.NS")
    h = t.history(period="4mo", auto_adjust=False, timeout=HISTORY_TIMEOUT_S)
    if h is None or h.empty:
        raise FinnError("TICKER_NOT_FOUND", f"No NSE price data found for {symbol}.", 404)
    h = h.dropna(subset=["Close", "Volume"])
    rows = [
        {
            "date": idx.strftime("%Y-%m-%d"),
            "open": round(float(r["Open"]), 2),
            "high": round(float(r["High"]), 2),
            "low": round(float(r["Low"]), 2),
            "close": round(float(r["Close"]), 2),
            "volume": int(r["Volume"]),
        }
        for idx, r in h.iterrows()
    ][-70:]
    prev, _, _ = cache.get_layered("market", f"{symbol}.json")
    info = (prev or {}).get("info") or {}      # reuse cached/seeded profile; never call .info here
    return {"symbol": symbol, "fetched_at": cache.iso_now(), "rows": rows, "info": info}


def _refresh_info_bg(symbol: str) -> None:
    """Fill name/market cap for tickers that have none. Slow (needs a crumb), so background-only,
    skipped while the breaker is open, and rate-limited per symbol."""
    if live_blocked() or time.time() < _info_cooldown.get(symbol, 0):
        return
    _info_cooldown[symbol] = time.time() + 3600

    def run():
        try:
            import yfinance as yf
            i = yf.Ticker(f"{symbol}.NS").info or {}
            mc = i.get("marketCap")
            info = {"name": i.get("longName") or i.get("shortName"), "sector": i.get("sector"),
                    "industry": i.get("industry"), "market_cap_cr": round(mc / 1e7) if mc else None}
            rec = cache.read_json(cache.cache_path("market", f"{symbol}.json"))
            if rec and info.get("name"):
                rec["info"] = info
                cache.put_cache("market", f"{symbol}.json", rec)
        except Exception:
            pass

    threading.Thread(target=run, daemon=True).start()


def _on_done(symbol: str, fut: Future) -> None:
    """Runs whenever a live fetch finishes, even after the request already got its fallback."""
    try:
        rec = fut.result()
    except Exception:
        return
    cache.put_cache("market", f"{symbol}.json", rec)
    if live_blocked():       # a success proves Yahoo is answering again
        with _lock:
            _breaker["until"] = 0.0
    if not (rec.get("info") or {}).get("name"):
        _refresh_info_bg(symbol)


def _submit(symbol: str) -> Future:
    with _lock:
        f = _inflight.get(symbol)
        if f is not None and not f.done():
            return f           # concurrent callers share one in-flight fetch
        f = _pool.submit(_fetch_live, symbol)
        _inflight[symbol] = f
    f.add_done_callback(lambda fut, s=symbol: _on_done(s, fut))
    return f


# ---------------------------------------------------------------- resolution
def _shape(rec: dict, origin: str, stale: bool) -> dict[str, Any]:
    out = {
        "symbol": rec["symbol"],
        "source": origin,
        "is_stale": stale,
        "as_of": rec.get("fetched_at"),
        "info": rec.get("info") or {},
        "history": rec["rows"][-30:],
    }
    out.update(compute(rec["rows"]))
    out["cap_band"] = cap_band(out["info"].get("market_cap_cr"))
    return out


def _resolve(symbols: list[str], force: bool, allow_live: bool, budget: float | None) -> dict[str, Any]:
    """symbol -> shaped dict, or FinnError. Never blocks longer than `budget` seconds in total."""
    budget = config.MARKET_LIVE_BUDGET_S if budget is None else budget
    out: dict[str, Any] = {}
    loaded: dict[str, tuple] = {}
    need: list[str] = []
    for s in symbols:
        rec, origin, age = cache.get_layered("market", f"{s}.json")
        loaded[s] = (rec, origin)
        if rec is not None and origin == "cache" and age is not None and age < config.MARKET_TTL and not force:
            out[s] = _shape(rec, "cache", False)
        else:
            need.append(s)

    blocked_before = live_blocked()
    futs: dict[str, Future] = {}
    if need and allow_live and not blocked_before:
        futs = {s: _submit(s) for s in need}
        wait(list(futs.values()), timeout=budget)       # ONE shared deadline for the whole batch

    ok = failed = 0
    for s in need:
        rec, origin = loaded[s]
        f = futs.get(s)
        err: Exception | None = None
        if f is not None and f.done():
            try:
                out[s] = _shape(f.result(), "live", False)
                ok += 1
                continue
            except Exception as e:  # noqa: BLE001
                err = e
        elif f is not None:
            err = TimeoutError("live fetch exceeded budget")
        # --- fallback: runtime cache, then committed seed (flagged stale)
        unknown_ticker = isinstance(err, FinnError) and err.code == "TICKER_NOT_FOUND" and rec is None
        if f is not None and not unknown_ticker:
            failed += 1                                  # provider failure (429/401/crumb/timeout/empty)
        if rec is not None:
            out[s] = _shape(rec, origin or "cache", True)
        elif unknown_ticker and not blocked_before:
            out[s] = err
        else:
            out[s] = FinnError("MARKET_UNAVAILABLE", f"Price data for {s} is unavailable right now.", 503)
    if failed:
        # nothing worked -> back off long; partial failure -> short back-off
        _trip("provider errors or timeout", 300 if ok == 0 else 60)
    return out


def get_eod(symbol: str, force: bool = False, allow_live: bool = True, budget: float | None = None) -> dict[str, Any]:
    symbol = normalize_symbol(symbol)
    res = _resolve([symbol], force, allow_live, budget)[symbol]
    if isinstance(res, FinnError):
        raise res
    return res


def get_eod_soft(symbol: str) -> dict[str, Any] | None:
    """Like get_eod, but a Yahoo outage returns None instead of failing. Genuinely invalid or unknown
    tickers still raise. Used where price data is nice-to-have (research name lookup)."""
    try:
        return get_eod(symbol)
    except FinnError as e:
        if e.code in ("MARKET_UNAVAILABLE",):
            return None
        raise


def get_many(symbols: list[str], force: bool = False) -> dict[str, dict[str, Any]]:
    """Concurrent, deadline-bounded. Returns an entry per symbol; failures become {'error': code}."""
    syms = [normalize_symbol(s) for s in symbols]
    res = _resolve(syms, force, True, None)
    return {
        s: (v if not isinstance(v, FinnError) else {"symbol": s, "error": v.code, "source": "none", "is_stale": True})
        for s, v in res.items()
    }
