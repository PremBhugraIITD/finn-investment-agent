"""Pull mode: primary-document ingestion, grounded retrieval, deterministic financial maths,
and a single strong-model (Tier 2) synthesis whose every claim must carry a resolvable citation.

Allowed evidence: annual reports, company-issued concall transcripts, exchange-filed quarterly results.
Never used: news, broker notes, research reports, Screener's own tables or summaries, model world knowledge.
"""
from __future__ import annotations

import re
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from .. import cache, config
from ..models.schemas import (
    AskOutput, DeepOutput, FinnError, FinSeries, GuidanceExtraction,
)
from . import classifier, documents, guidance as G, llm, market_data, nse, screener

ALL_MODULES = ["business", "financial", "trajectory", "balance_sheet", "cash_quality", "narrative", "guidance", "bull_bear"]
TITLES = {
    "business": "Business snapshot", "financial": "Financial snapshot", "trajectory": "Trajectory",
    "balance_sheet": "Balance sheet", "cash_quality": "Cash quality", "narrative": "Narrative vs. numbers",
    "guidance": "Guidance", "bull_bear": "Bull vs. bear",
}
RESULT_RX = re.compile(r"financial result|results for the|quarter ended|quarter and (half|nine)|unaudited", re.I)
RESULT_NEG = re.compile(r"newspaper|publication|intimation|prior|schedule|trading window|earnings call|conference call|"
                        r"transcript|audio|presentation|investor meet", re.I)

# ------------------------------------------------------------------ prompts
GUIDANCE_SYSTEM = """You read an earnings-call transcript of an Indian listed company and extract what MANAGEMENT said about the future.
Extract guidance/outlook statements by management (not analysts) on: revenue growth, EBITDA margin, PAT growth, capex, and other headline KPIs.
Rules:
- explicit=true only for a specific numeric target or range ("EBITDA margin of 22-24%", "double-digit growth" -> low=10, high=null, "capex of Rs 500 crore"). Otherwise explicit=false (qualitative commentary on growth/demand/margins; leave low/high null).
- metric: revenue_growth_yoy, ebitda_margin, pat_growth_yoy or capex_inr_cr when the statement clearly is that metric on a reported basis; else 'other'. Constant-currency or segment-only statements are 'other'.
- period_type: the period the statement targets, relative to this call: next_quarter, current_fy (the fiscal year in progress, April-March), next_fy, medium_term (3+ years), or unspecified.
- Units: pct for growth/margins (give percent points), inr_cr for capex (convert to crore), else other.
- quote must be copied verbatim from the transcript (<= 45 words); page is the [p.N] marker it appears under.
- Do NOT include statements about results already reported (past actuals); only forward-looking guidance or outlook.
- The metric label must be what the quote literally refers to (e.g. a store-opening target is 'other', not capex). If unsure use 'other' with explicit=false.
- Maximum 12 claims, prioritising numeric guidance, then headline growth commentary. If management gave none, return an empty list.
Use only the transcript text. Do not use outside knowledge."""

FIN_SYSTEM = """You extract a multi-year financial series from excerpts of a company's ANNUAL REPORT.
Return the most recent up to 5 fiscal years available (label like FY26; Indian fiscal years end 31 March). Prefer CONSOLIDATED statements; use standalone only if no consolidated data exist.
Fields (null if not present in the excerpts - never estimate):
revenue = revenue from operations; ebitda only if reported (else null); pbt; pat = profit after tax for the year; total_debt = long-term + short-term borrowings (incl. current maturities; exclude lease liabilities unless included in borrowings); net_worth = total equity; cfo = net cash from operating activities; capex = purchase of property/plant/equipment/intangibles (positive number); receivable_days, inventory_days, payable_days if the report states them.
Report numbers in the report's own unit (set unit to exactly one of 'INR crore', 'INR lakh', 'INR million', 'INR billion'). All years must be in the same unit.
For each year set page = the [p.N] marker of the table row used for revenue/PAT and quote = a short verbatim snippet of that row.
For banks/NBFCs revenue = total income and ebitda/total_debt can be null.
Use only the supplied excerpts."""

DEEP_SYSTEM = """You are the research analyst for a public-markets investment firm. Write a tight, evidence-grounded view of ONE company.
STRICT RULES
1. Use ONLY the FACTS (ids M#) and EXCERPTS (ids C#) provided. No broker notes, no news, no research reports, no outside knowledge of this company. If something is not supported, put it in the section's `gap` field in one short sentence instead of guessing.
2. Every claim must cite at least one source. Cite facts by ref 'M#' (quote ''), excerpts by ref 'C#' with a VERBATIM quote (<= 30 words) copied from that excerpt.
3. Numbers in a claim must match the cited fact or quote exactly. Do not compute new numbers; use the FACTS already computed.
4. Style: investor-grade, concise, no adjectives like 'robust', 'impressive'; each claim <= 35 words; 2-4 claims per section; plain English.
5. bull_bear section: exactly 3 claims with stance 'bull' and 3 with stance 'bear', each a distinct argument. Other sections use stance 'none'.
6. Never compare a figure to a target for a different period or basis. Never say management set a target, or missed/met one, unless a fact line explicitly lists that target. Management statements are facts starting 'said'; delivered quarterly numbers are facts starting 'delivered'.
   Timing: a call labelled Qn discusses quarter Qn's results and looks FORWARD; 'next quarter' means the quarter after Qn, 'H2' means Q3+Q4 and 'H1' means Q1+Q2 of that fiscal year (April-March). A forward statement can only be judged against results of the period it targets; if that period is not in the 'delivered' facts, say it is not yet reported instead of judging it. Never use the results of the quarter being discussed to judge a statement about a later quarter.
   narrative: exactly 3 claims, each pairing one management statement (a 'said' fact) with the delivered numbers that confirm or contradict it (state which, using the exact figures).
   scorecard_takeaways: 3-5 claims on the four-quarter record: what was promised vs delivered, whether targets/tone shifted between calls, and what was never measurable. Cite the 'said' and 'delivered' facts you rely on. Use the accelerated/decelerated wording given in the facts; do not do your own arithmetic or comparisons.
7. Only produce the sections listed in REQUESTED."""

ASK_SYSTEM = """Answer the investor's question about ONE company using ONLY the EXCERPTS (ids C#) provided, all from primary documents (annual report, concall transcripts, exchange-filed results).
Return 1-4 claims. Every claim cites excerpts by ref 'C#' with a VERBATIM quote (<= 30 words) copied from that excerpt. If the excerpts do not answer the question, return one claim saying 'Not found in available primary documents' with no cites.
No outside knowledge, no speculation, no investment advice."""

# ------------------------------------------------------------------ helpers
_norm_rx = re.compile(r"[^a-z0-9]+")


def _norm(s: str) -> str:
    return _norm_rx.sub(" ", (s or "").lower()).strip()


def locate_quote(doc: dict, quote: str, hint: int | None = None) -> int | None:
    nq = _norm(quote)
    if len(nq) < 12:
        return None
    pages = doc.get("pages", [])
    order = []
    if hint:
        order += [p for p in pages if p["n"] == hint]
    order += [p for p in pages if not hint or p["n"] != hint]
    for p in order:
        if nq in _norm(p["text"]):
            return p["n"]
    # tolerate quotes that span a page break: match on the first 70 normalised chars
    head = nq[:70]
    for p in order:
        if head and head in _norm(p["text"]):
            return p["n"]
    return None


def _manifest_get(symbol: str) -> list[dict]:
    v, _, _ = cache.get_layered("docs", f"manifest_{symbol}.json")
    return v or []


def _doc_full(entry: dict) -> dict | None:
    return documents.get_document(entry["url"], entry["symbol"], entry["kind"], entry.get("title", ""),
                                  entry.get("period", ""), allow_fetch=False)


def _ensure_indexed(symbol: str) -> list[dict]:
    man = _manifest_get(symbol)
    for e in man:
        d = _doc_full(e)
        if d:
            documents.index_document(d)
    return man


def _cite(entry: dict, page: int, quote: str = "") -> dict:
    label = {"annual_report": "Annual Report", "transcript": "Concall", "results": "Results filing"}.get(entry["kind"], entry["kind"])
    return {"doc_id": entry["doc_id"], "page": page, "quote": quote, "url": entry["url"],
            "label": f"{label} · {entry.get('period') or entry.get('title', '')}".strip(" ·")}


# ------------------------------------------------------------------ inventory & ingestion
def _results_candidates(recs: list[dict], n_quarters: int = 5) -> list[dict]:
    by_q: dict[int, list[dict]] = {}
    for r in recs:
        if not r.get("attachment"):
            continue
        blob = r["subject"] + " " + r["text"]
        if RESULT_RX.search(blob) and not RESULT_NEG.search(r["subject"]):
            by_q.setdefault(G.qidx_from_iso(r["ts"]), []).append(r)
    picked = []
    for q in sorted(by_q, reverse=True)[:n_quarters]:
        # prefer the formal board outcome / results filing, earliest filed
        cands = sorted(by_q[q], key=lambda r: (not re.search(r"outcome|financial result", r["subject"], re.I), r["ts"]))
        picked.append(cands[0] | {"q": q})
    return picked


def _history(symbol: str, days: int = 500) -> list[dict]:
    cached, seeded = nse._load(symbol)
    if cached.get("covered_days", 0) < days:
        try:
            cached = nse.refresh(symbol, days)
        except Exception:
            pass
    return nse._merge(seeded.get("records", []), cached.get("records", []))


def inventory(symbol: str) -> dict[str, Any]:
    symbol = market_data.normalize_symbol(symbol)
    eod = market_data.get_eod_soft(symbol) or {}  # TICKER_NOT_FOUND still raises; a Yahoo outage must not block research
    try:
        scr = screener.get_documents(symbol)
    except FinnError:
        scr = {"annual_reports": [], "concalls": [], "resolved_url": None, "source": "none"}
    cached, seeded = nse._load(symbol)
    recs = nse._merge(seeded.get("records", []), cached.get("records", []))
    res = _results_candidates(recs)
    result_cached, _, _ = cache.get_layered("research", f"result_{symbol}.json")
    return {
        "symbol": symbol, "name": (eod.get("info") or {}).get("name"), "resolved_url": scr.get("resolved_url"),
        "seeded": bool(_manifest_get(symbol)), "cached_analysis": bool(result_cached),
        "annual_reports": scr.get("annual_reports", [])[:3],
        "concalls": [{"period": c["period"], "transcript": c.get("transcript"), "ppt": c.get("ppt")} for c in scr.get("concalls", [])[:6]],
        "results_filings": [{"title": r["subject"], "ts": r["ts"], "url": r["attachment"]} for r in res],
        "source": scr.get("source", "none"),
    }


def ingest(symbol: str, progress: Callable[[str, float], None] | None = None, refresh: bool = False) -> list[dict]:
    """Discover, download, extract and index the primary documents. Idempotent (checksum-addressed cache)."""
    symbol = market_data.normalize_symbol(symbol)
    prog = progress or (lambda s, p: None)
    existing = _manifest_get(symbol)
    if existing and not refresh:
        prog("Indexing documents", 0.3)
        _ensure_indexed(symbol)
        return existing
    prog("Finding primary documents", 0.04)
    market_data.get_eod_soft(symbol)
    scr = screener.get_documents(symbol)
    recs = _history(symbol)
    want: list[dict] = []
    seen_q: set[int] = set()
    for c in scr["concalls"]:
        qi = G.qidx_from_period(c["period"])
        if c.get("transcript") and qi is not None and qi not in seen_q:
            seen_q.add(qi)
            want.append({"kind": "transcript", "title": f"Concall transcript · {c['period']}", "period": c["period"],
                         "url": c["transcript"], "q": qi})
        if len(seen_q) >= 4:
            break
    for r in _results_candidates(recs):
        want.append({"kind": "results", "title": r["subject"], "period": G.qlabel(r["q"]), "url": r["attachment"], "q": r["q"]})
    ars = scr["annual_reports"][:2]
    # annual report: take the newest that actually yields text
    for ar in ars:
        want.append({"kind": "annual_report", "title": ar["title"], "period": str(ar["year"]), "url": ar["url"], "q": None})
        break
    total = len(want)
    manifest: list[dict] = []
    lock = threading.Lock()
    counter = {"n": 0}

    def work(w: dict):
        d = documents.get_document(w["url"], symbol, w["kind"], w["title"], w["period"])
        if d and d["status"] == "extraction_incomplete" and w["kind"] == "annual_report" and len(ars) > 1:
            alt = ars[1]
            d = documents.get_document(alt["url"], symbol, "annual_report", alt["title"], str(alt["year"])) or d
            w = w | {"title": alt["title"], "period": str(alt["year"]), "url": alt["url"]}
        if d and d.get("pages"):
            documents.index_document(d)
        with lock:
            counter["n"] += 1
            prog(f"Reading documents ({counter['n']}/{total})", 0.08 + 0.5 * counter["n"] / max(total, 1))
        return w, d

    with ThreadPoolExecutor(max_workers=4) as ex:
        for w, d in ex.map(work, want):
            if d:
                manifest.append({"doc_id": d["doc_id"], "symbol": symbol, "kind": w["kind"], "title": w["title"],
                                 "period": w["period"], "url": w["url"], "q": w.get("q"),
                                 "n_pages": d.get("n_pages", 0), "status": d.get("status", "ok")})
    cache.put_cache("docs", f"manifest_{symbol}.json", manifest)
    return manifest


# ------------------------------------------------------------------ extraction (Tier 1)
def _transcript_text(doc: dict, cap: int = 60000) -> str:
    out, n = [], 0
    for p in doc.get("pages", []):
        t = f"[p.{p['n']}]\n{p['text']}\n"
        if n + len(t) > cap:
            break
        out.append(t)
        n += len(t)
    return "".join(out)


def extract_guidance(entry: dict) -> list[dict]:
    doc = _doc_full(entry)
    if not doc or not doc.get("pages"):
        return []
    text = _transcript_text(doc)
    ext = llm.call("cheap", GuidanceExtraction, GUIDANCE_SYSTEM,
                   f"Call: {entry['title']} (reporting {G.qlabel(entry['q'])})\n\n{text}", tag=f"guid:{doc['doc_id']}", max_tokens=3000)
    if not ext:
        return []
    out = []
    for c in ext.claims:
        page = locate_quote(doc, c.quote, c.page)
        if page is None:   # unverifiable quote -> drop (grounding rule)
            continue
        out.append(c.model_dump() | {"page": page, "q": entry["q"], "doc": entry})
    return out


def actuals_for(manifest: list[dict]) -> tuple[dict[int, dict], dict[int, dict]]:
    """quarter index -> derived ratios, and quarter index -> (card, entry) for citations."""
    best: dict[int, tuple[int, dict, dict]] = {}
    for e in manifest:
        if e["kind"] != "results" or e.get("q") is None:
            continue
        rec = {"id": cache.sha(e["url"], n=16), "symbol": e["symbol"], "company": e["symbol"], "attachment": e["url"],
               "subject": e["title"], "ts": "2000-01-01T00:00:00+05:30", "text": ""}
        card = classifier.extract_results(rec)
        if not card:
            continue
        filled = sum(1 for m in card["metrics"].values() for k in ("current", "prev_quarter", "year_ago") if m[k] is not None)
        if e["q"] not in best or filled > best[e["q"]][0]:
            best[e["q"]] = (filled, card, e)
    # a filing also describes its previous quarter (QoQ column) -> only trust the filed quarter itself
    ratios = {q: G.derive_actuals(c) for q, (_, c, _) in best.items()}
    cards = {q: {"card": c, "entry": e} for q, (_, c, e) in best.items()}
    return ratios, cards


def extract_series(symbol: str, man: list[dict]) -> dict | None:
    ar = next((e for e in man if e["kind"] == "annual_report" and e["status"] == "ok"), None)
    if not ar:
        return None
    doc = _doc_full(ar)
    if not doc:
        return None
    queries = [
        "five year financial highlights summary key financial ratios revenue EBITDA profit",
        "consolidated statement of profit and loss revenue from operations profit before tax profit for the year",
        "consolidated balance sheet borrowings total equity net worth",
        "statement of cash flows net cash generated from operating activities purchase of property plant equipment",
        "debtors turnover inventory days receivable days working capital",
    ]
    seen, chunks = set(), []
    for q in queries:
        for h in documents.search(symbol, q, kinds=["annual_report"], doc_ids=[ar["doc_id"]], limit=2):
            if h["chunk_id"] not in seen:
                seen.add(h["chunk_id"])
                chunks.append(h)
    if not chunks:
        return None
    text = "\n\n".join(f"--- excerpt pages {c['page_start']}-{c['page_end']} ---\n{c['text']}" for c in chunks[:9])
    ser = llm.call("cheap", FinSeries, FIN_SYSTEM, text, tag=f"fin:{ar['doc_id']}", max_tokens=2500)
    if not ser or not ser.years:
        return None
    years = []
    for y in ser.years:
        d = y.model_dump()
        if d.get("quote"):
            p = locate_quote(doc, d["quote"], d.get("page"))
            d["page"] = p or d.get("page")
            d["quote_ok"] = p is not None
        years.append(d)
    years.sort(key=lambda y: y["fy"])
    return {"unit": ser.unit, "basis": ser.basis, "years": years, "entry": ar}


# ------------------------------------------------------------------ deterministic metrics & facts
def _fm(v: float | None, unit: str = "") -> str:
    if v is None:
        return "n/a"
    a = abs(v)
    s = f"{v:,.0f}" if a >= 100 else f"{v:,.1f}"
    return f"{s} {unit}".strip()


def _ratio(a: float | None, b: float | None) -> float | None:
    return None if a is None or not b else a / b * 100


def build_metrics(series: dict | None, cards: dict[int, dict]) -> dict[str, list[dict]]:
    """Section -> list of {label, value, note, cite}. Pure arithmetic."""
    out: dict[str, list[dict]] = {k: [] for k in ALL_MODULES}
    if cards:
        q = max(cards)
        c, e = cards[q]["card"], cards[q]["entry"]
        m = c["metrics"]
        pg = m["revenue"].get("page") or m["pat"].get("page") or 1
        cite = _cite(e, pg)
        a = G.derive_actuals(c)
        out["financial"].append({"label": f"Revenue · {G.qlabel(q)}", "value": f"{_fm(m['revenue']['current'])} {c['unit'].replace('INR ', '')}",
                                 "note": f"YoY {m['revenue']['yoy_pct']:+.1f}%" if m["revenue"]["yoy_pct"] is not None else "", "cite": cite})
        if a["ebitda_margin"] is not None:
            out["financial"].append({"label": "EBITDA margin", "value": f"{a['ebitda_margin']:.1f}%",
                                     "note": f"prev Q {a['ebitda_margin_prev_q']:.1f}%" if a["ebitda_margin_prev_q"] is not None else "", "cite": cite})
        if m["pat"]["current"] is not None:
            out["financial"].append({"label": "PAT", "value": f"{_fm(m['pat']['current'])} {c['unit'].replace('INR ', '')}",
                                     "note": f"YoY {m['pat']['yoy_pct']:+.1f}%" if m["pat"]["yoy_pct"] is not None else "", "cite": cite})
    if not series:
        return out
    ys, unit, e = series["years"], series["unit"].replace("INR ", ""), series["entry"]

    def cite_for(y):
        return _cite(e, y["page"], y.get("quote") or "") if y.get("page") else None
    last = ys[-1]
    if last.get("revenue") is not None:
        out["financial"].append({"label": f"Revenue · {last['fy']}", "value": f"{_fm(last['revenue'])} {unit}", "note": "", "cite": cite_for(last)})
    mg = _ratio(last.get("pat"), last.get("revenue"))
    if mg is not None:
        out["financial"].append({"label": f"PAT margin · {last['fy']}", "value": f"{mg:.1f}%", "note": "", "cite": cite_for(last)})
    em = _ratio(last.get("ebitda"), last.get("revenue"))
    if em is not None:
        out["financial"].append({"label": f"EBITDA margin · {last['fy']}", "value": f"{em:.1f}%", "note": "", "cite": cite_for(last)})
    # trajectory
    rv = [(y["fy"], y["revenue"]) for y in ys if y.get("revenue")]
    if len(rv) >= 2:
        n = len(rv) - 1
        cagr = ((rv[-1][1] / rv[0][1]) ** (1 / n) - 1) * 100
        out["trajectory"].append({"label": (f"Revenue CAGR {rv[0][0]}-{rv[-1][0]}" if n > 1 else f"Revenue growth {rv[0][0]} to {rv[-1][0]}"), "value": f"{cagr:.1f}%",
                                  "note": f"{_fm(rv[0][1])} to {_fm(rv[-1][1])} {unit}", "cite": cite_for(ys[-1])})
    pt = [(y["fy"], y["pat"]) for y in ys if y.get("pat") and y["pat"] > 0]
    if len(pt) >= 2:
        n = len(pt) - 1
        cagr = ((pt[-1][1] / pt[0][1]) ** (1 / n) - 1) * 100
        out["trajectory"].append({"label": (f"PAT CAGR {pt[0][0]}-{pt[-1][0]}" if n > 1 else f"PAT growth {pt[0][0]} to {pt[-1][0]}"), "value": f"{cagr:.1f}%", "note": "", "cite": cite_for(ys[-1])})
    ms = [(y["fy"], _ratio(y.get("ebitda"), y.get("revenue"))) for y in ys]
    ms = [(f, v) for f, v in ms if v is not None]
    if len(ms) >= 2:
        out["trajectory"].append({"label": f"EBITDA margin {ms[0][0]} to {ms[-1][0]}", "value": f"{ms[0][1]:.1f}% to {ms[-1][1]:.1f}%",
                                  "note": f"{ms[-1][1] - ms[0][1]:+.1f} pp", "cite": cite_for(ys[-1])})
    # balance sheet
    if last.get("total_debt") is not None:
        out["balance_sheet"].append({"label": f"Total borrowings · {last['fy']}", "value": f"{_fm(last['total_debt'])} {unit}", "note": "", "cite": cite_for(last)})
    if last.get("net_worth"):
        out["balance_sheet"].append({"label": "Net worth", "value": f"{_fm(last['net_worth'])} {unit}", "note": "", "cite": cite_for(last)})
        if last.get("total_debt") is not None:
            out["balance_sheet"].append({"label": "Debt / equity", "value": f"{last['total_debt'] / last['net_worth']:.2f}x", "note": "", "cite": cite_for(last)})
    for k, lab in (("receivable_days", "Receivable days"), ("inventory_days", "Inventory days"), ("payable_days", "Payable days")):
        if last.get(k) is not None:
            out["balance_sheet"].append({"label": lab, "value": f"{last[k]:.0f}", "note": "", "cite": cite_for(last)})
    # cash quality
    for y in ys[-3:]:
        conv = _ratio(y.get("cfo"), y.get("pat"))
        if conv is not None:
            out["cash_quality"].append({"label": f"CFO / PAT · {y['fy']}", "value": f"{conv:.0f}%",
                                        "note": f"CFO {_fm(y['cfo'])} vs PAT {_fm(y['pat'])} {unit}", "cite": cite_for(y)})
    for y in ys[-2:]:
        if y.get("cfo") is not None and y.get("capex") is not None:
            out["cash_quality"].append({"label": f"Free cash flow · {y['fy']}", "value": f"{_fm(y['cfo'] - y['capex'])} {unit}",
                                        "note": f"CFO {_fm(y['cfo'])} less capex {_fm(y['capex'])}", "cite": cite_for(y)})
    return out


# ------------------------------------------------------------------ scorecard
def build_scorecard(man: list[dict], actual_ratios: dict[int, dict], cards: dict[int, dict], series: dict | None = None) -> dict[str, Any] | None:
    calls = sorted([e for e in man if e["kind"] == "transcript" and e.get("q") is not None], key=lambda e: e["q"])[-4:]
    if not calls:
        return None
    with ThreadPoolExecutor(max_workers=4) as ex:
        extracted = list(ex.map(extract_guidance, calls))
    rows, commentary, gaps = [], [], []
    annual = G.annual_actuals(series["years"]) if series else {}
    latest_results_q = max(cards) if cards else None
    if latest_results_q is not None and calls[-1]["q"] < latest_results_q - 1:
        gaps.append(f"Latest transcript on Screener is for {G.qlabel(calls[-1]['q'])}; results are available through {G.qlabel(latest_results_q)}.")
    qs = [e["q"] for e in calls]
    if len(calls) < 4 or any(b - a != 1 for a, b in zip(qs, qs[1:])):
        gaps.append("Screener does not list four consecutive quarterly transcripts for this company; the most recent available calls are shown.")
    for entry, claims in zip(calls, extracted):
        cq = entry["q"]
        if not claims:
            gaps.append(f"No usable guidance extracted from the {G.qlabel(cq)} call transcript.")
        numeric = [c for c in claims if c["explicit"] and c["metric"] != "other"]
        for c in numeric[:5]:
            r = G.compare(c, cq, actual_ratios, annual)
            tq = None
            actual_cite = None
            if r["status"] != "not yet measurable":
                m = re.search(r"Q\d FY\d\d", r["delivered"])
                key = r["metric_key"]
                tgt = next((q for q in cards if G.qlabel(q) == r["target"]), None)
                if tgt is None and r["target"].startswith("FY") and series and "annual report" in r["delivered"]:
                    yr = next((y for y in series["years"] if y["fy"] == r["target"]), None)
                    if yr and yr.get("page"):
                        actual_cite = _cite(series["entry"], yr["page"], yr.get("quote") or "")
                elif tgt is None and r["target"].startswith("FY"):
                    tgt = max((q for q in cards if G.fylabel(G.fy_of(q)) == r["target"]), default=None)
                if tgt is not None:
                    ce, cc = cards[tgt]["entry"], cards[tgt]["card"]["metrics"]
                    pm = cc["revenue"] if key == "revenue_growth_yoy" else cc["pat"] if key == "pat_growth_yoy" else cc["ebitda"]
                    actual_cite = _cite(ce, pm.get("page") or 1, pm.get("quote") or "")
            rows.append({"quarter": G.qlabel(cq), "metric": c["description"], "promised": r["promised"], "target": r["target"],
                         "delivered": r["delivered"], "delta": r["delta"], "status": r["status"], "note": r["note"],
                         "guidance_cite": _cite(entry, c["page"], c["quote"]), "actual_cite": actual_cite})
        other = [c for c in claims if not (c["explicit"] and c["metric"] != "other")][:2]
        for c in other:
            rows.append({"quarter": G.qlabel(cq), "metric": c["description"],
                         "promised": G._promised(c) if c["explicit"] else "qualitative", "target": "", "delivered": "–",
                         "delta": "–", "status": "not yet measurable",
                         "note": "Commentary / KPI not measurable from quarterly results", "guidance_cite": _cite(entry, c["page"], c["quote"]),
                         "actual_cite": None})
    delivered = []
    for q in sorted(actual_ratios)[-5:]:
        a, ce = actual_ratios[q], cards[q]
        pg = ce["card"]["metrics"]["revenue"].get("page") or 1
        delivered.append({"quarter": G.qlabel(q), "revenue_yoy": a["revenue_growth_yoy"], "ebitda_margin": a["ebitda_margin"],
                          "pat_yoy": a["pat_growth_yoy"], "cite": _cite(ce["entry"], pg)})
    return {"quarters": [G.qlabel(e["q"]) for e in calls], "delivered": delivered, "rows": rows, "takeaways": [], "gaps": gaps,
            "claims": [c for cs in extracted for c in cs]}


# ------------------------------------------------------------------ Tier 2 synthesis
def _excerpt_cite(ch: dict, doc_entry: dict, quote: str) -> dict | None:
    """Resolve a model quote against the supplied chunk; return a cite with the true page, or None."""
    nq = _norm(quote)
    if len(nq) < 12:
        return None
    parts = re.split(r"\[p\.(\d+)\]", ch["text"])
    # parts: [pre, page, text, page, text, ...]
    for i in range(1, len(parts) - 1, 2):
        if nq in _norm(parts[i + 1]):
            return _cite(doc_entry, int(parts[i]), quote)
    if nq in _norm(ch["text"]):
        return _cite(doc_entry, ch["page_start"], quote)
    return None


def _gather_excerpts(symbol: str, man: list[dict], modules: list[str]) -> list[dict]:
    ar = next((e for e in man if e["kind"] == "annual_report"), None)
    trs = sorted([e for e in man if e["kind"] == "transcript"], key=lambda e: e.get("q") or 0, reverse=True)
    plan: list[tuple[str, list[str], str | None, int]] = []
    ar_ids = [ar["doc_id"]] if ar else []
    tr_ids = [t["doc_id"] for t in trs[:2]]
    if "business" in modules:
        plan.append(("business overview segments products customers revenue mix", ar_ids, "annual_report", 2))
    if {"financial", "trajectory"} & set(modules):
        plan.append(("management discussion analysis financial performance revenue growth profit", ar_ids, "annual_report", 1))
    if "balance_sheet" in modules:
        plan.append(("borrowings debt contingent liabilities related party transactions auditor qualification", ar_ids, "annual_report", 2))
    if "cash_quality" in modules:
        plan.append(("cash flow operating activities working capital receivables inventory", ar_ids, "annual_report", 1))
    if {"narrative", "guidance", "bull_bear"} & set(modules):
        plan.append(("outlook guidance demand margin growth expect confident", tr_ids, "transcript", 3))
    if "bull_bear" in modules:
        plan.append(("risks headwinds competition challenges pressure concerns", tr_ids + ar_ids, None, 2))
    out, seen = [], set()
    for q, ids, kind, n in plan:
        if not ids:
            continue
        for h in documents.search(symbol, q, kinds=[kind] if kind else None, doc_ids=ids, limit=n):
            if h["chunk_id"] not in seen:
                seen.add(h["chunk_id"])
                out.append(h)
    return out[:13]


def synthesize(symbol: str, name: str, man: list[dict], modules: list[str], metrics: dict[str, list[dict]],
               score: dict | None) -> tuple[dict[str, dict], list[dict], list[str]]:
    """Returns (sections_by_id: {claims, gap}, takeaways, gaps)."""
    by_doc = {e["doc_id"]: e for e in man}
    excerpts = _gather_excerpts(symbol, man, modules)
    facts: list[dict] = []   # {id, text, cite}
    for sec, ms in metrics.items():
        if sec not in modules and sec not in ("financial",):
            continue
        for m in ms:
            facts.append({"id": f"M{len(facts) + 1}", "text": f"{m['label']}: {m['value']}" + (f" ({m['note']})" if m["note"] else ""),
                          "cite": m.get("cite")})
    if score:
        nums = [r for r in score["rows"] if r["promised"] != "qualitative"]
        facts.append({"id": f"M{len(facts) + 1}", "cite": None,
                      "text": f"Across the {len(score['quarters'])} calls ({', '.join(score['quarters'])}) {len(nums)} numeric guidance items were extracted; "
                              f"{sum(1 for r in nums if r['status'] != 'not yet measurable')} could be compared with delivered results."})
        if score.get("delivered"):
            facts.append({"id": f"M{len(facts) + 1}", "cite": None,
                          "text": f"Latest reported quarter in the results filings: {score['delivered'][-1]['quarter']}. Quarters after it are not yet reported."})
        prev_d = None
        for d in score.get("delivered", []):
            trend = ""
            if prev_d and d["revenue_yoy"] is not None and prev_d["revenue_yoy"] is not None:
                dp = d["revenue_yoy"] - prev_d["revenue_yoy"]
                trend = f" [revenue growth {'accelerated' if dp > 0 else 'decelerated' if dp < 0 else 'unchanged'} by {abs(dp):.1f} pp vs {prev_d['quarter']}]"
            prev_d = d

            def f(v, suf="%"):
                return "n/a" if v is None else f"{v:+.1f}{suf}" if suf == "%" else f"{v:.1f}{suf}"
            facts.append({"id": f"M{len(facts) + 1}", "cite": d["cite"],
                          "text": f"{d['quarter']} delivered: revenue {f(d['revenue_yoy'])} YoY, EBITDA margin {f(d['ebitda_margin'], '%') if d['ebitda_margin'] is not None else 'n/a'}, PAT {f(d['pat_yoy'])} YoY{trend}"})
        for r in score["rows"]:
            if r["promised"] == "qualitative":
                facts.append({"id": f"M{len(facts) + 1}", "cite": r["guidance_cite"],
                              "text": f"{r['quarter']} call said: \"{r['metric']}\""})
        for r in score["rows"]:
            if r["status"] != "not yet measurable":
                facts.append({"id": f"M{len(facts) + 1}", "cite": r["guidance_cite"],
                              "text": f"{r['quarter']} call promised {r['metric']} = {r['promised']} for {r['target']}; delivered {r['delivered']}; "
                                      f"{r['delta']}; status {r['status']}"})
        for r in score["rows"]:
            if r["status"] == "not yet measurable" and r["promised"] != "qualitative":
                facts.append({"id": f"M{len(facts) + 1}", "cite": r["guidance_cite"],
                              "text": f"{r['quarter']} call guided {r['metric']} = {r['promised']} (target {r['target'] or 'n/a'}); not yet measurable: {r['note']}"})
    body = [f"COMPANY: {name} ({symbol})", "REQUESTED: " + ", ".join(m for m in modules if m != "guidance"), "", "FACTS (computed in code from primary documents):"]
    body += [f"{f['id']}: {f['text']}" for f in facts] or ["(none)"]
    body += ["", "EXCERPTS (primary documents):"]
    for i, ch in enumerate(excerpts, 1):
        e = by_doc.get(ch["doc_id"], {})
        body.append(f"[C{i}] {e.get('title', '')} ({e.get('period', '')}), pages {ch['page_start']}-{ch['page_end']}\n{ch['text']}")
    gaps: list[str] = []
    if not llm.available():
        return {}, [], ["The reasoning model is unavailable, so only computed figures are shown."]
    out = llm.call("deep", DeepOutput, DEEP_SYSTEM, "\n".join(body), tag="deep", max_tokens=4000)
    if out is None:
        return {}, [], ["The reasoning model did not return a result; showing computed figures only."]
    fact_by_id = {f["id"]: f for f in facts}
    ex_by_id = {f"C{i}": ch for i, ch in enumerate(excerpts, 1)}

    def validate(dc) -> dict | None:
        cites = []
        for c in dc.cites:
            if c.ref in fact_by_id and fact_by_id[c.ref].get("cite"):
                cites.append(fact_by_id[c.ref]["cite"])
            elif c.ref in ex_by_id:
                ch = ex_by_id[c.ref]
                cc = _excerpt_cite(ch, by_doc.get(ch["doc_id"], {"kind": ch["kind"], "doc_id": ch["doc_id"], "url": "", "period": ""}), c.quote)
                if cc:
                    cites.append(cc)
        uniq, seen_c = [], set()
        for c in cites:
            k = (c["doc_id"], c["page"], c["quote"][:40])
            if k not in seen_c:
                seen_c.add(k)
                uniq.append(c)
        if not uniq:
            return None
        text = re.sub(r"^\s*(bull|bear)\s*[:\-]\s*", "", dc.text, flags=re.I)
        return {"text": text, "stance": None if dc.stance == "none" else dc.stance, "cites": uniq}

    sections: dict[str, dict] = {}
    dropped = 0
    for s in out.sections:
        claims = []
        for c in s.claims:
            v = validate(c)
            if v:
                claims.append(v)
            else:
                dropped += 1
        sections[s.id] = {"claims": claims, "gap": s.gap}
    take = [v for v in (validate(c) for c in out.scorecard_takeaways) if v]
    if dropped:
        gaps.append(f"{dropped} generated statement(s) were removed because their citation could not be verified.")
    return sections, take, gaps


# ------------------------------------------------------------------ orchestration
def _guidance_section(score: dict | None) -> dict:
    claims = []
    if score:
        qs = sorted({x["q"] for x in score["claims"]}, reverse=True)[:2]
        for q in qs:
            pool = sorted([c for c in score["claims"] if c["q"] == q], key=lambda c: (not c["explicit"], c["metric"] == "other"))[:3]
            for c in pool:
                claims.append({"text": f"{G.qlabel(q)} call: {c['description']}" + (f" ({G._promised(c)}, {c['period_type'].replace('_', ' ')})" if c["explicit"] else ""),
                               "stance": None, "cites": [_cite(c["doc"], c["page"], c["quote"])]})
    return {"claims": claims, "gap": None if claims else "No management guidance could be extracted from the available transcripts."}


def analyze(symbol: str, modules: list[str] | None, progress: Callable[[str, float], None] | None = None,
            refresh: bool = False) -> dict[str, Any]:
    symbol = market_data.normalize_symbol(symbol)
    prog = progress or (lambda s, p: None)
    modules = [m for m in (modules or ALL_MODULES) if m in ALL_MODULES] or ALL_MODULES
    full, origin, _ = cache.get_layered("research", f"result_{symbol}.json")
    if full and not refresh:
        r = dict(full)
        r["source"] = origin or "cache"
        if set(modules) != set(ALL_MODULES):
            r["sections"] = [s for s in r["sections"] if s["id"] in modules]
            r["modules"] = modules
        prog("Ready", 1.0)
        return r
    man = ingest(symbol, prog, refresh=refresh)
    if not man:
        raise FinnError("NO_PRIMARY_DOCUMENTS", f"No primary documents (annual report, transcripts, results) found for {symbol}.", 404)
    prog("Extracting guidance and results", 0.62)
    eod = market_data.get_eod_soft(symbol) or {}
    name = (eod.get("info") or {}).get("name") or symbol
    ratios, cards = actuals_for(man)
    series = extract_series(symbol, man)
    prog("Building four-quarter scorecard", 0.74)
    score = build_scorecard(man, ratios, cards, series) if {"narrative", "guidance"} & set(modules) else None
    metrics = build_metrics(series, cards)
    prog("Reasoning over evidence", 0.84)
    sections_llm, takeaways, gaps = synthesize(symbol, name, man, modules, metrics, score)
    if score:
        score["takeaways"] = takeaways
    sections = []
    for m in modules:
        if m == "guidance":
            sec = _guidance_section(score)
        else:
            sec = sections_llm.get(m, {"claims": [], "gap": None})
        gap = sec.get("gap")
        if not sec["claims"] and not metrics.get(m) and not gap:
            gap = "Not found in available primary documents."
        sections.append({"id": m, "title": TITLES[m], "claims": sec["claims"], "metrics": metrics.get(m, []), "gap": gap})
    if series is None:
        gaps.append("Annual report text could not be read; multi-year figures are unavailable.")
    clean_score = None
    if score:
        clean_score = {k: v for k, v in score.items() if k != "claims"}
    result = {
        "symbol": symbol, "name": name, "as_of": cache.iso_now(), "source": "live",
        "models": {"tier1": config.FAST_MODEL, "tier2": config.REASONING_MODEL},
        "documents": [{k: e[k] for k in ("doc_id", "kind", "title", "period", "url", "n_pages", "status")} for e in man],
        "modules": modules, "sections": sections, "scorecard": clean_score, "gaps": gaps,
    }
    if set(modules) == set(ALL_MODULES) and any(s["claims"] for s in sections):
        cache.put_cache("research", f"result_{symbol}.json", result)
    prog("Done", 1.0)
    return result


def run_full(symbol: str) -> dict:
    r = analyze(symbol, ALL_MODULES, progress=lambda s, p: print(f"[{symbol}] {p:.0%} {s}"), refresh=True)
    print(symbol, "sections", [(s["id"], len(s["claims"]), len(s["metrics"])) for s in r["sections"]], "gaps", r["gaps"])
    return r


def ask(symbol: str, question: str) -> dict[str, Any]:
    symbol = market_data.normalize_symbol(symbol)
    man = _ensure_indexed(symbol)
    if not man:
        raise FinnError("NOT_INGESTED", "Run research on this company first so its documents are indexed.", 409)
    hits = documents.search(symbol, question, limit=6)
    if not hits:
        return {"answer": "Not found in available primary documents", "claims": []}
    by_doc = {e["doc_id"]: e for e in man}
    body = [f"QUESTION: {question}", "", "EXCERPTS:"]
    for i, ch in enumerate(hits, 1):
        e = by_doc.get(ch["doc_id"], {})
        body.append(f"[C{i}] {e.get('title', '')} ({e.get('period', '')}), pages {ch['page_start']}-{ch['page_end']}\n{ch['text']}")
    out = llm.call("deep", AskOutput, ASK_SYSTEM, "\n".join(body), tag="ask", max_tokens=1200)
    if out is None:
        raise FinnError("MODEL_UNAVAILABLE", "The reasoning model is unavailable right now.", 503)
    ex_by_id = {f"C{i}": ch for i, ch in enumerate(hits, 1)}
    claims = []
    for dc in out.claims:
        cites = []
        for c in dc.cites:
            ch = ex_by_id.get(c.ref)
            if ch:
                cc = _excerpt_cite(ch, by_doc.get(ch["doc_id"], {"kind": ch["kind"], "doc_id": ch["doc_id"], "url": "", "period": ""}), c.quote)
                if cc:
                    cites.append(cc)
        if cites or "not found" in dc.text.lower():
            claims.append({"text": dc.text, "stance": None, "cites": cites})
    return {"answer": "", "claims": claims or [{"text": "Not found in available primary documents", "stance": None, "cites": []}]}


# ------------------------------------------------------------------ jobs
JOBS: dict[str, dict[str, Any]] = {}


def start_job(symbol: str, modules: list[str] | None, refresh: bool = False) -> str:
    symbol = market_data.normalize_symbol(symbol)
    market_data.get_eod_soft(symbol)  # fail fast on invalid tickers, but tolerate a Yahoo outage
    jid = uuid.uuid4().hex[:12]
    JOBS[jid] = {"id": jid, "symbol": symbol, "status": "queued", "stage": "Queued", "progress": 0.0, "error": None, "result": None}

    def run():
        job = JOBS[jid]
        job["status"] = "running"

        def prog(stage: str, p: float):
            job["stage"], job["progress"] = stage, round(p, 2)
        try:
            job["result"] = analyze(symbol, modules, prog, refresh)
            job["status"], job["progress"], job["stage"] = "complete", 1.0, "Done"
        except FinnError as e:
            job["status"], job["error"] = "failed", e.message
        except Exception as e:  # pragma: no cover
            job["status"], job["error"] = "failed", f"Unexpected error: {str(e)[:160]}"

    threading.Thread(target=run, daemon=True).start()
    return jid
