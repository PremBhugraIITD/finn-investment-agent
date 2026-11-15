"""Windowed digest assembly: announcements -> classification -> enrichment -> materiality ranking."""
from __future__ import annotations

import json
import math
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import Any

from .. import cache, config
from ..models.schemas import Settings
from . import alerts, bios, classifier, market_data, nse


def portfolio() -> list[dict]:
    return json.loads((config.SEED / "portfolio.json").read_text())["companies"]


def research_universe() -> list[dict]:
    return json.loads((config.SEED / "research_universe.json").read_text())["companies"]


def _enrich_item(rec: dict, cls: dict) -> tuple[dict | None, list[dict], list[dict]]:
    results, people, bio_cards = None, [], []
    if cls["category"] == "results":
        results = classifier.extract_results(rec)
    elif cls["category"] == "management_change":
        people = classifier.extract_mgmt(rec)
        for p in people:
            if p.get("action") == "joining":
                bio_cards.append(bios.enrich(p, rec.get("company") or rec["symbol"]))
    return results, people, bio_cards


def build(hours: int = 24, symbols: list[str] | None = None, settings: Settings | None = None,
          wait: bool = False) -> dict[str, Any]:
    settings = settings or alerts.get_settings()
    pf = {c["symbol"]: c for c in portfolio()}
    symbols = [s for s in (symbols or list(pf)) if s in pf] or list(pf)
    days = max(1, math.ceil(hours / 24))

    def fetch(s: str):
        return s, nse.get_announcements(s, days=days, wait=wait)

    with ThreadPoolExecutor(max_workers=4) as ex:
        fetched = dict(ex.map(fetch, symbols))

    snapshot_ts = None
    sources = Counter()
    recs: list[dict] = []
    for s, r in fetched.items():
        sources[r["source"]] += 1
        recs += r["records"]
        if r["source"] == "seed":
            snapshot_ts = r.get("as_of")
    # window anchor: now when live/cache data; the seed snapshot time when we only have the seed
    anchor = datetime.fromisoformat(snapshot_ts) if (snapshot_ts and sources.get("seed") and not sources.get("live")
                                                     and not sources.get("cache")) else datetime.now().astimezone()
    cutoff = anchor - timedelta(hours=hours)
    recs = [r for r in recs if datetime.fromisoformat(r["ts"]) >= cutoff]

    cls = classifier.classify(recs)
    eod = market_data.get_many(symbols)
    seen: Counter = Counter()
    items: list[dict] = []

    def build_item(rec: dict):
        c = cls[rec["id"]]
        results, people, bio_cards = _enrich_item(rec, c)
        return rec, c, results, people, bio_cards

    with ThreadPoolExecutor(max_workers=6) as ex:
        enriched = list(ex.map(build_item, recs))

    for rec, c, results, people, bio_cards in sorted(enriched, key=lambda x: x[0]["ts"]):
        key = (rec["symbol"], c["category"])
        mcap = (eod.get(rec["symbol"]) or {}).get("info", {}).get("market_cap_cr")
        sc, factors = alerts.score(c, mcap, results, people, seen[key] > 0)
        seen[key] += 1
        in_focus = c["category"] in settings.categories
        routine = (c["category"] in alerts.COLLAPSE_BY_DEFAULT and not in_focus and sc < settings.materiality) or sc < 25
        items.append({
            "id": rec["id"], "symbol": rec["symbol"], "company": pf[rec["symbol"]]["name"],
            "ts": rec["ts"], "category": c["category"], "confidence": c.get("confidence"),
            "summary": c["summary"], "subject": rec["subject"], "attachment": rec.get("attachment"),
            "direction": c.get("direction"), "materiality": sc, "factors": factors,
            "tier": "routine" if routine else ("high" if sc >= settings.materiality else "normal"),
            "results": results, "people": people, "bios": bio_cards,
        })
    _dedupe_results(items)
    items.sort(key=lambda i: i["ts"], reverse=True)
    items.sort(key=lambda i: (i["tier"] == "routine", -i["materiality"]))
    status = {
        "window_hours": hours, "as_of": cache.iso_now(),
        "source_status": dict(sources),
        "is_stale": any(r["is_stale"] for r in fetched.values()),
        "snapshot_as_of": snapshot_ts, "refreshing": any(r.get("refreshing") for r in fetched.values()),
        "llm_available": bool(config.OPENAI_API_KEY),
    }
    counts = Counter(i["tier"] for i in items)
    return {"items": items, "counts": dict(counts), "total": len(items), **status}


def _dedupe_results(items: list[dict]) -> None:
    """Several filings carry the same quarter's results (board outcome, media release, deck).
    Keep the headline-numbers card only on the most complete one per company."""
    by_sym: dict[str, list[dict]] = {}
    for it in items:
        if it["results"]:
            by_sym.setdefault(it["symbol"], []).append(it)
    for lst in by_sym.values():
        if len(lst) < 2:
            continue

        def filled(i):
            return sum(1 for m in i["results"]["metrics"].values() for k in ("current", "prev_quarter", "year_ago") if m[k] is not None)
        best = max(lst, key=lambda i: (filled(i), i["results"]["basis"] == "consolidated"))
        for i in lst:
            if i is not best:
                i["results"] = None


def warm(symbols: list[str] | None = None, days: int = 7) -> None:
    """Fetch + classify + enrich a window so first user load is instant (and seed capture)."""
    build(hours=days * 24, symbols=symbols, wait=True)
