"""Deterministic materiality scoring, thresholds, settings and alert state. No model calls here."""
from __future__ import annotations

import re
from typing import Any

from .. import cache
from ..models.schemas import Settings

BASE = {
    "results": 72, "M&A": 62, "fund_raise": 56, "management_change": 52, "litigation": 52,
    "credit_rating": 42, "capex": 50, "pledging": 48, "dividend": 34, "bonus_split": 36,
    "insider_trading": 28, "press_release": 20, "earnings_call": 14, "investor_meetings": 10,
    "postal_ballot": 8, "agm_egm": 8, "unclassified": 3,
}
COLLAPSE_BY_DEFAULT = {"earnings_call", "investor_meetings", "postal_ballot", "agm_egm", "unclassified"}
SENIOR = re.compile(r"chief executive|\bceo\b|managing director|\bmd\b|chief financial|\bcfo\b|chairman|chairperson|"
                    r"whole[- ]time director|president", re.I)


def score(cls: dict, mcap_cr: float | None, results: dict | None, people: list[dict] | None,
          repeat_in_window: bool) -> tuple[int, list[dict]]:
    cat = cls["category"]
    pts = BASE.get(cat, 3)
    factors = [{"label": f"Type: {cat.replace('_', ' ')}", "points": pts}]

    def add(label: str, p: int):
        nonlocal pts
        if p:
            pts += p
            factors.append({"label": label, "points": p})

    sev = cls.get("severity")
    add("High impact" if sev == "high" else "Medium impact" if sev == "medium" else "", 14 if sev == "high" else 6 if sev == "medium" else 0)
    amt = cls.get("amount_inr_cr")
    if amt and mcap_cr:
        pct = amt / mcap_cr * 100
        if pct >= 5:
            add(f"Size {pct:.1f}% of market cap", 20)
        elif pct >= 1:
            add(f"Size {pct:.1f}% of market cap", 12)
        elif pct >= 0.2:
            add(f"Size {pct:.2f}% of market cap", 6)
    if cat == "credit_rating" and cls.get("direction") == "negative":
        add("Downgrade / negative outlook", 28)
    if cat == "management_change" and people:
        if any(SENIOR.search(p.get("role", "")) for p in people):
            add("Senior role (CEO/CFO/MD/Chair)", 22)
    if cat == "results" and results:
        pat = results["metrics"]["pat"].get("yoy_pct")
        rev = results["metrics"]["revenue"].get("yoy_pct")
        big = max([abs(x) for x in (pat, rev) if x is not None] or [0])
        if big >= 25:
            add(f"Large YoY move ({big:.0f}%)", 12)
        elif big >= 10:
            add(f"YoY move ({big:.0f}%)", 6)
    if cls.get("direction") == "negative" and cat not in ("results", "credit_rating"):
        add("Negative for shareholders", 5)
    if repeat_in_window:
        add("Repeat of same type in window", -8)
    return max(0, min(100, pts)), factors


# ---------------- settings & alert state ----------------
def get_settings() -> Settings:
    v, _, _ = cache.get_layered("research", "settings.json")
    try:
        return Settings.model_validate(v) if v else Settings()
    except Exception:
        return Settings()


def put_settings(s: Settings) -> Settings:
    cache.put_cache("research", "settings.json", s.model_dump())
    return s


def read_state() -> dict[str, Any]:
    return cache.read_json(cache.cache_path("research", "alert_state.json")) or {"read": []}


def mark_read(ids: list[str] | None, all_ids: list[str] | None = None) -> None:
    st = read_state()
    st["read"] = sorted(set(st.get("read", [])) | set(ids or []) | set(all_ids or []))
    cache.put_cache("research", "alert_state.json", st)


def market_alerts(eod: dict[str, dict], names: dict[str, str], s: Settings) -> list[dict]:
    out = []
    for sym, m in eod.items():
        if m.get("error") or m.get("change_pct") is None:
            continue
        date = m.get("last_date")
        mv, vr = m["change_pct"], m.get("volume_ratio")
        if abs(mv) >= s.price_move_pct:
            out.append({
                "id": cache.sha("price", sym, date, n=14), "kind": "price", "symbol": sym, "company": names.get(sym, sym),
                "ts": date, "title": f"{'+' if mv > 0 else ''}{mv:.1f}% on the day",
                "reason": f"Move of {mv:+.1f}% exceeds your ±{s.price_move_pct:g}% threshold",
                "value": mv, "threshold": s.price_move_pct, "history": [h["close"] for h in m.get("history", [])],
                "is_stale": m.get("is_stale", False),
            })
        if vr is not None and vr >= s.volume_ratio:
            out.append({
                "id": cache.sha("vol", sym, date, n=14), "kind": "volume", "symbol": sym, "company": names.get(sym, sym),
                "ts": date, "title": f"Volume {vr:.1f}x the 20-day average",
                "reason": f"Volume ratio {vr:.2f}x exceeds your {s.volume_ratio:g}x threshold",
                "value": vr, "threshold": s.volume_ratio, "history": [h["volume"] for h in m.get("history", [])],
                "is_stale": m.get("is_stale", False),
            })
    return out
