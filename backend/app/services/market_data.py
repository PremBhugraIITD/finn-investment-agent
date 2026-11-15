"""End-of-day price and volume via yfinance, with cache -> seed fallback."""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from .. import cache, config
from ..models.schemas import FinnError

SYMBOL_RE = re.compile(r"^[A-Z0-9&-]{1,20}$")


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


def _fetch_live(symbol: str) -> dict[str, Any]:
    import yfinance as yf

    t = yf.Ticker(f"{symbol}.NS")
    h = t.history(period="4mo", auto_adjust=False)
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
    ]
    rows = rows[-70:]
    info: dict[str, Any] = {}
    try:
        i = t.info or {}
        mc = i.get("marketCap")
        info = {
            "name": i.get("longName") or i.get("shortName"),
            "sector": i.get("sector"),
            "industry": i.get("industry"),
            "market_cap_cr": round(mc / 1e7) if mc else None,
        }
    except Exception:
        pass
    return {"symbol": symbol, "fetched_at": cache.iso_now(), "rows": rows, "info": info}


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


def get_eod(symbol: str, force: bool = False, allow_live: bool = True) -> dict[str, Any]:
    symbol = normalize_symbol(symbol)
    name = f"{symbol}.json"
    rec, origin, age = cache.get_layered("market", name)
    fresh = rec is not None and origin == "cache" and age is not None and age < config.MARKET_TTL
    if fresh and not force:
        return _shape(rec, "cache", False)
    if allow_live:
        try:
            live = _fetch_live(symbol)
            if rec and not live["info"].get("name"):
                live["info"] = rec.get("info") or {}
            cache.put_cache("market", name, live)
            return _shape(live, "live", False)
        except FinnError:
            if rec is None:
                raise
        except Exception:
            pass
    if rec is not None:
        return _shape(rec, origin or "cache", True)
    raise FinnError("MARKET_UNAVAILABLE", f"Price data for {symbol} is unavailable right now.", 503)


def get_many(symbols: list[str], force: bool = False) -> dict[str, dict[str, Any]]:
    def one(s: str):
        try:
            return s, get_eod(s, force=force)
        except FinnError as e:
            return s, {"symbol": s, "error": e.code, "source": "none", "is_stale": True}

    with ThreadPoolExecutor(max_workers=6) as ex:
        return dict(ex.map(one, symbols))
