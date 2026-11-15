"""Quick smoke check against a running FINN API (local or deployed).

Usage:  python smoke_check.py [BASE_URL]     (default http://localhost:8000)
Prints a pass/fail line per step and exits non-zero on any failure. Not a test suite.
"""
from __future__ import annotations

import sys
import time

import requests

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000").rstrip("/")
failures = 0


def step(name: str, fn):
    global failures
    t = time.time()
    try:
        detail = fn()
        print(f"PASS  {name}  ({time.time() - t:.1f}s) {detail or ''}")
    except Exception as e:  # noqa: BLE001
        failures += 1
        print(f"FAIL  {name}  -> {e}")


def get(path, **kw):
    r = requests.get(BASE + path, timeout=kw.pop("timeout", 90), **kw)
    r.raise_for_status()
    return r.json()


def health():
    # First request may hit a Render free-tier cold start (up to ~60-100s); give it 120s, then the rest are fast.
    t0 = time.time()
    assert get("/health", timeout=120)["status"] == "ok"
    cold = time.time() - t0
    r = get("/ready", timeout=30)
    return f"first /health {cold:.1f}s, models={r['models']} openai_key={r['openai_key']} market_data={r.get('market_data')}"


def portfolio():
    d = get("/api/v1/portfolio")
    assert d["count"] == 18, f"expected 18 companies, got {d['count']}"
    missing = [c["symbol"] for c in d["companies"] if c["last_close"] is None]
    assert not missing, f"no price for {missing}"
    return f"18 companies, as_of={d['as_of'][:19]}"


def digest():
    a = get("/api/v1/digest?window=24h")
    b = get("/api/v1/digest?window=7d")
    assert b["total"] >= a["total"] and b["total"] > 0, "7d window should contain filings"
    return f"24h={a['total']} 7d={b['total']} counts={b['counts']} status={b['source_status']}"


def results_card():
    d = get("/api/v1/digest?hours=720")
    cards = [i for i in d["items"] if i["results"]]
    assert cards, "no results card in the 30-day window"
    m = cards[0]["results"]["metrics"]
    for k in ("revenue", "ebitda", "pbt", "pat"):
        assert k in m
    return f"{len(cards)} results cards, e.g. {cards[0]['symbol']} {cards[0]['results']['quarter']}"


def mgmt():
    d = get("/api/v1/digest?hours=720")
    items = [i for i in d["items"] if i["category"] == "management_change"]
    assert items, "no management-change item in 30-day window"
    with_bio = [i for i in items if i["bios"]]
    return f"{len(items)} mgmt-change items, {len(with_bio)} with bio cards (others are re-appointments/departures)"


def settings_and_alerts():
    orig = get("/api/v1/settings")
    try:
        loose = requests.put(BASE + "/api/v1/settings", json={**orig, "price_move_pct": 1.0, "volume_ratio": 1.0, "materiality": 40}, timeout=60).json()
        a1 = get("/api/v1/alerts")
        tight = requests.put(BASE + "/api/v1/settings", json={**orig, "price_move_pct": 10.0, "volume_ratio": 5.0, "materiality": 95}, timeout=60).json()
        a2 = get("/api/v1/alerts")
        assert len(a1["alerts"]) > len(a2["alerts"]), "loosening thresholds should raise alert count"
        return f"loose={len(a1['alerts'])} tight={len(a2['alerts'])}"
    finally:
        requests.put(BASE + "/api/v1/settings", json=orig, timeout=60)


def research(sym: str, expect_scorecard: bool, timeout: float = 60):
    """Start a research job and poll it. If it runs long, print live progress so a slow free-tier box is
    distinguishable from a stuck job."""
    jid = requests.post(f"{BASE}/api/v1/research/{sym}/analyze", json={"modules": None}, timeout=60).json()["job_id"]
    t = time.time()
    last_print, last_stage = 0.0, None
    j = {"status": "queued", "stage": "?", "progress": 0}
    while time.time() - t < timeout:
        j = get(f"/api/v1/research/jobs/{jid}")
        if j["status"] in ("complete", "failed"):
            break
        el = time.time() - t
        if el > 20 and (j["stage"] != last_stage or el - last_print > 20):   # taking longer than expected
            print(f"      ... {sym} still {j['status']} after {el:.0f}s: {j['progress']:.0%} - {j['stage']}")
            last_print, last_stage = el, j["stage"]
        time.sleep(2)
    if j["status"] not in ("complete", "failed"):
        raise AssertionError(f"{sym} job still {j['status']} after {timeout:.0f}s at {j['progress']:.0%} ({j['stage']})")
    assert j["status"] == "complete", j.get("error") or j["status"]
    r = j["result"]
    cites = [c for s in r["sections"] for cl in s["claims"] for c in cl["cites"]]
    assert cites, "no cited claims"
    assert all(c["doc_id"] and c["page"] and c["url"] for c in cites), "unresolvable citation"
    docs = {d["doc_id"] for d in r["documents"]}
    assert all(c["doc_id"] in docs for c in cites), "citation points outside the primary-document set"
    if expect_scorecard:
        sc = r["scorecard"]
        assert sc and len(sc["quarters"]) >= 1 and sc["rows"], "scorecard missing"
        return f"{len(r['sections'])} sections, {len(cites)} citations, scorecard quarters={sc['quarters']} source={r['source']}"
    return f"{len(r['sections'])} sections, {len(cites)} citations, source={r['source']}"


if __name__ == "__main__":
    print(f"Smoke check against {BASE}")
    step("health/ready", health)
    step("portfolio (18 stocks with prices)", portfolio)
    step("digest 24h and 7d", digest)
    step("results card (Revenue/EBITDA/PBT/PAT with QoQ/YoY)", results_card)
    step("management-change items and bio cards", mgmt)
    step("settings change moves alerts", settings_and_alerts)
    step("research KPITTECH (seeded) + four-quarter scorecard", lambda: research("KPITTECH", True, timeout=60))
    step("research ASIANPAINT (unseeded ticker, live ingest)", lambda: research("ASIANPAINT", True, timeout=150))
    print("\nALL PASSED" if not failures else f"\n{failures} step(s) FAILED")
    sys.exit(1 if failures else 0)
