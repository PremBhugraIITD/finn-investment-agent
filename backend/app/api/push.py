"""Push-mode routes: portfolio, market, digest, alerts, settings, refresh."""
from __future__ import annotations

import json
import re
import time
from typing import Any

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from .. import cache, config
from ..models.schemas import FinnError, Settings
from ..services import alerts, digest, market_data, nse

router = APIRouter(prefix="/api/v1")

_memo: dict[str, tuple[float, Any]] = {}


def _memoize(key: str, ttl: float, fn):
    hit = _memo.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    val = fn()
    _memo[key] = (time.time(), val)
    return val


def parse_window(window: str, hours: int | None) -> int:
    if hours:
        return max(1, min(hours, 24 * 45))
    m = re.fullmatch(r"(\d+)([hd])", window or "24h")
    if not m:
        raise FinnError("BAD_WINDOW", "window must look like 6h, 24h, 3d, 7d or pass hours=", 422)
    n = int(m.group(1)) * (24 if m.group(2) == "d" else 1)
    return max(1, min(n, 24 * 45))


@router.get("/portfolio")
def get_portfolio():
    comps = digest.portfolio()
    eod = market_data.get_many([c["symbol"] for c in comps])
    last = _memo.get("digest:168:all")
    events: dict[str, dict] = {}
    if last:
        for it in last[1]["items"]:
            if it["tier"] == "high" and it["symbol"] not in events:
                events[it["symbol"]] = {"category": it["category"], "summary": it["summary"], "ts": it["ts"],
                                        "materiality": it["materiality"]}
    out = []
    for c in comps:
        m = eod.get(c["symbol"], {})
        info = m.get("info") or {}
        out.append({
            "symbol": c["symbol"], "name": info.get("name") or c["name"], "label": c["name"],
            "sector": c["sector"], "cap_band": m.get("cap_band") or c["cap_band"],
            "market_cap_cr": info.get("market_cap_cr"), "last_close": m.get("last_close"),
            "change_pct": m.get("change_pct"), "volume_ratio": m.get("volume_ratio"),
            "volume": m.get("volume"), "last_date": m.get("last_date"),
            "spark": [h["close"] for h in m.get("history", [])],
            "source": m.get("source"), "is_stale": m.get("is_stale", False), "latest_event": events.get(c["symbol"]),
        })
    return {"companies": out, "as_of": cache.iso_now(), "count": len(out)}


@router.get("/market/{symbol}")
def get_market(symbol: str):
    return market_data.get_eod(symbol)


@router.get("/digest")
def get_digest(window: str = "24h", hours: int | None = None, symbols: str | None = None):
    h = parse_window(window, hours)
    syms = [s.strip().upper() for s in symbols.split(",")] if symbols else None
    s = alerts.get_settings()
    key = f"digest:{h}:{','.join(sorted(syms)) if syms else 'all'}:{s.materiality}:{','.join(s.categories)}"
    return _memoize(key, 45, lambda: digest.build(hours=h, symbols=syms, settings=s))


@router.get("/alerts")
def get_alerts(days: int = 7):
    s = alerts.get_settings()
    days = max(1, min(days, 30))
    comps = {c["symbol"]: c["name"] for c in digest.portfolio()}
    d = _memoize(f"digest:{days * 24}:alerts:{s.materiality}:{','.join(s.categories)}", 45,
                 lambda: digest.build(hours=days * 24, settings=s))
    eod = market_data.get_many(list(comps))
    out = alerts.market_alerts(eod, comps, s)
    for it in d["items"]:
        if it["materiality"] >= s.materiality and it["category"] in s.categories:
            out.append({
                "id": cache.sha("ann", it["id"], n=14), "kind": "announcement", "symbol": it["symbol"],
                "company": it["company"], "ts": it["ts"], "title": it["summary"],
                "reason": f"{it['category'].replace('_', ' ').title()} scored {it['materiality']}, above your {s.materiality} threshold",
                "category": it["category"], "materiality": it["materiality"], "item": it,
                "is_stale": d["is_stale"],
            })
    read = set(alerts.read_state().get("read", []))
    for a in out:
        a["read"] = a["id"] in read
    out.sort(key=lambda a: (a["read"], str(a["ts"])), reverse=False)
    out.sort(key=lambda a: str(a["ts"]), reverse=True)
    out.sort(key=lambda a: a["read"])
    return {"alerts": out, "unread": sum(1 for a in out if not a["read"]), "settings": s.model_dump(),
            "as_of": cache.iso_now(), "source_status": d["source_status"], "is_stale": d["is_stale"]}


class ReadBody(BaseModel):
    ids: list[str] = []
    all: bool = False


@router.post("/alerts/read")
def read_alerts(body: ReadBody):
    ids = body.ids
    if body.all:
        ids = [a["id"] for a in get_alerts()["alerts"]]
    alerts.mark_read(ids)
    return {"ok": True}


@router.get("/settings")
def get_settings():
    return alerts.get_settings()


@router.put("/settings")
def put_settings(s: Settings):
    unknown = [c for c in s.categories if c not in config.CATEGORIES]
    if unknown:
        raise FinnError("BAD_CATEGORY", f"Unknown categories: {unknown}", 422)
    s.price_move_pct = max(0.5, min(s.price_move_pct, 25))
    s.volume_ratio = max(1.0, min(s.volume_ratio, 20))
    s.materiality = max(0, min(s.materiality, 100))
    _memo.clear()
    return alerts.put_settings(s)


@router.post("/refresh")
def refresh(x_refresh_secret: str | None = Header(default=None), days: int = 3):
    if not config.REFRESH_SECRET or x_refresh_secret != config.REFRESH_SECRET:
        raise HTTPException(status_code=401, detail="Invalid refresh secret")
    syms = [c["symbol"] for c in digest.portfolio()]
    market_data.get_many(syms, force=True)
    ok = 0
    for s in syms:
        try:
            nse.refresh(s, days)
            ok += 1
        except Exception:
            pass
    _memo.clear()
    return {"nse_refreshed": ok, "of": len(syms), "as_of": cache.iso_now()}
