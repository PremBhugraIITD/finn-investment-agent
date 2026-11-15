"""Screener.in is used strictly as a link directory for primary documents.

Only annual reports and raw concall transcripts are taken. Screener's own financial tables and
its AI summaries are never used as evidence.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

import requests
from bs4 import BeautifulSoup

from .. import cache, config
from ..models.schemas import FinnError
from . import guidance as _g
from .market_data import normalize_symbol

_MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}


def _period_key(label: str) -> str:
    """'Oct 2026' -> '2026-10' so periods sort chronologically."""
    m = re.match(r"([A-Za-z]{3})\w*\s+(\d{4})", label.strip())
    if not m or m.group(1).title() not in _MONTHS:
        return "0000-00"
    return f"{m.group(2)}-{_MONTHS[m.group(1).title()]:02d}"


def _parse(html: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")
    name_el = soup.select_one("h1")
    out: dict[str, Any] = {
        "name": name_el.get_text(strip=True) if name_el else None,
        "annual_reports": [],
        "concalls": [],
    }
    for h3 in soup.select("#documents h3"):
        title = h3.get_text(strip=True).lower()
        box = h3.find_parent("div", class_="documents")
        if not box:
            continue
        if "annual report" in title:
            for a in box.select("ul.list-links a"):
                href = a.get("href")
                label = a.get_text(" ", strip=True)
                m = re.search(r"(20\d{2}|19\d{2})", label)
                if href and m:
                    out["annual_reports"].append({"title": label, "year": int(m.group(1)), "url": href})
        elif "concall" in title:
            for li in box.select("ul.list-links > li"):
                lab = li.find("div", class_=re.compile("ink-600"))
                period = lab.get_text(strip=True) if lab else ""
                entry: dict[str, Any] = {"period": period, "period_key": _period_key(period)}
                for a in li.find_all("a", href=True):
                    t = a.get_text(strip=True).lower()
                    if t == "transcript":
                        entry["transcript"] = a["href"]
                    elif t == "ppt":
                        entry["ppt"] = a["href"]
                    elif t in ("rec", "recording"):
                        entry["recording"] = a["href"]
                if entry.get("transcript"):
                    out["concalls"].append(entry)
    out["annual_reports"].sort(key=lambda x: x["year"], reverse=True)
    out["concalls"].sort(key=lambda x: x["period_key"], reverse=True)
    return out


def _fetch(symbol: str) -> dict[str, Any]:
    last = None
    for path in (f"/company/{symbol}/consolidated/", f"/company/{symbol}/"):
        url = "https://www.screener.in" + path
        try:
            r = requests.get(url, headers={"User-Agent": config.USER_AGENT}, timeout=20)
        except requests.RequestException as e:
            last = e
            continue
        if r.status_code == 200:
            parsed = _parse(r.text)
            if parsed["annual_reports"] or parsed["concalls"]:
                parsed.update(symbol=symbol, resolved_url=url, fetched_at=cache.iso_now())
                return parsed
        last = RuntimeError(f"HTTP {r.status_code}")
    raise FinnError("SCREENER_UNAVAILABLE", f"No Screener documents found for {symbol} ({last}).", 404)


def get_documents(symbol: str, force: bool = False) -> dict[str, Any]:
    symbol = normalize_symbol(symbol)
    name = f"screener_{symbol}.json"
    rec, origin, age = cache.get_layered("docs", name)
    if rec and origin == "cache" and age is not None and age < config.SCREENER_TTL and not force:
        return rec | {"source": "cache"}
    try:
        live = _fetch(symbol)
        cache.put_cache("docs", name, live)
        return live | {"source": "live"}
    except FinnError:
        if rec:
            return rec | {"source": origin or "cache", "is_stale": True}
        raise
    except Exception:
        if rec:
            return rec | {"source": origin or "cache", "is_stale": True}
        raise FinnError("SCREENER_UNAVAILABLE", f"Screener is unavailable for {symbol}.", 503)


def select_for_ingest(docs: dict[str, Any]) -> dict[str, list[dict]]:
    """Cap what a live ingest will download: the config.INGEST_TRANSCRIPTS most recent concall
    transcripts (one per fiscal quarter, raw transcripts only) and the latest annual report."""
    transcripts: list[dict] = []
    seen: set[int] = set()
    for c in docs.get("concalls", []):                      # already sorted newest first
        qi = _g.qidx_from_period(c.get("period", ""))
        if c.get("transcript") and qi is not None and qi not in seen:
            seen.add(qi)
            transcripts.append({"period": c["period"], "url": c["transcript"], "q": qi})
            if len(transcripts) >= config.INGEST_TRANSCRIPTS:
                break
    annual = [{"title": a["title"], "year": a["year"], "url": a["url"]}
              for a in docs.get("annual_reports", [])[: config.INGEST_ANNUAL_REPORTS]]
    return {"transcripts": transcripts, "annual_reports": annual}
