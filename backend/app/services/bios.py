"""Incoming-executive bio enrichment (push-side; outside web context is allowed here).

Order: filing text -> public professional-profile search snippet (LinkedIn when publicly indexed).
Never bypasses login walls; failure degrades to the filing-sourced bio.
"""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import requests
from bs4 import BeautifulSoup

from .. import cache, config
from ..models.schemas import BioDraft
from . import llm

BIO_SYSTEM = """Write a short professional bio (2-3 lines, max 55 words) of an executive who is joining an Indian listed company.
Use ONLY facts present in the supplied FILING and WEB SNIPPETS. Do not add anything from your own knowledge.
If the text has little information, say so plainly and keep it short. List previous_roles as 'Role, Organisation' strings found in the text.
confidence: high if the filing and web snippets agree on identity and background, medium if only one source gives background, low otherwise."""


def _search(name: str, company: str, role: str) -> list[dict[str, str]]:
    q = f'"{name}" {company} {role} linkedin'
    try:
        r = requests.post("https://html.duckduckgo.com/html/", data={"q": q},
                          headers={"User-Agent": config.USER_AGENT}, timeout=10)
        if r.status_code != 200:
            return []
    except requests.RequestException:
        return []
    soup = BeautifulSoup(r.text, "lxml")
    out = []
    for res in soup.select(".result")[:6]:
        a = res.select_one("a.result__a")
        sn = res.select_one(".result__snippet")
        if not a:
            continue
        href = a.get("href", "")
        if "uddg=" in href:
            href = unquote(parse_qs(urlparse(href).query).get("uddg", [href])[0])
        out.append({"url": href, "title": a.get_text(" ", strip=True),
                    "snippet": sn.get_text(" ", strip=True) if sn else ""})
    linked = [o for o in out if "linkedin.com/in/" in o["url"]]
    return (linked[:2] or out[:1]) if out else []


def enrich(person: dict[str, Any], company: str) -> dict[str, Any]:
    """Return a bio card for one incoming person."""
    key = f"bio_{cache.sha(person['name'].lower(), company.lower(), n=16)}.json"
    v, _, age = cache.get_layered("llm", key)
    if v and (age is None or age < 30 * 86400):
        return v
    filing_bg = person.get("background_in_filing")
    hits = _search(person["name"], company, person["role"])
    # keep only hits that actually mention the person's surname (identity guard)
    surname = person["name"].split()[-1].lower()
    hits = [h for h in hits if surname in (h["title"] + h["snippet"]).lower()]
    card: dict[str, Any] = {
        "name": person["name"], "role": person["role"],
        "bio": filing_bg, "previous_roles": [], "confidence": "low" if filing_bg else None,
        "sources": [{"label": "Exchange filing", "url": person.get("source_url")}],
        "external_profile": bool(hits),
    }
    if not filing_bg and not hits:
        card["bio"] = None
        card["note"] = "External profile unavailable"
        cache.put_cache("llm", key, card)
        return card
    user = f"PERSON: {person['name']}, {person['role']} at {company}\nFILING: {filing_bg or 'none'}\nWEB SNIPPETS:\n" + \
        "\n".join(f"- {h['title']}: {h['snippet']}" for h in hits)
    draft = llm.call("cheap", BioDraft, BIO_SYSTEM, user, tag="bio", max_tokens=400)
    if draft:
        card.update(bio=draft.bio, previous_roles=draft.previous_roles, confidence=draft.confidence)
    for h in hits:
        card["sources"].append({"label": "LinkedIn" if "linkedin.com" in h["url"] else "Web", "url": h["url"]})
    if not hits:
        card["note"] = "External profile unavailable"
    cache.put_cache("llm", key, card)
    return card
