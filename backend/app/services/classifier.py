"""Tier-1 (gpt-4o-mini) classification and extraction for the push side."""
from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from .. import cache, config
from ..models.schemas import (
    ClassifiedBatch, ClassifiedItem, MgmtExtraction, ResultsExtraction,
)
from . import documents, llm

CLASSIFY_SYSTEM = """You classify Indian stock-exchange (NSE) corporate announcements for an investor.
Pick exactly one category per item:
results: quarterly/annual results, board meeting approving them, results decks, shareholder letters, ads confirming results.
M&A: acquisition, merger, demerger, joint venture, investing in or selling a stake in a subsidiary.
fund_raise: rights issue, QIP, preferential allotment, buyback, NCD/commercial paper or other debt raising.
management_change: CEO, CFO, MD, director, KMP or senior management joining, resigning, retiring, appointed.
litigation: lawsuits, regulatory orders, fines, warnings, tax demands, insolvency/restructuring proceedings.
insider_trading: promoter/insider buying or selling own shares (SEBI PIT disclosures).
earnings_call: notice, transcript, audio/recording link of the earnings conference call.
investor_meetings: one-on-one/group investor meetings, attending investor conferences, analyst meet schedules.
credit_rating: a rating agency assigning, upgrading, downgrading, reaffirming a rating.
dividend: cash dividend declared, with record date.
bonus_split: bonus issue or stock split.
capex: approving spend on new plant, facility, capacity expansion.
press_release: general news - order wins/contracts bagged, new products, partnerships, awards, sales numbers, clarifications - not covered above. Order wins are NEVER M&A.
postal_ballot: postal ballot notice or result.
agm_egm: AGM/EGM notice or result.
pledging: promoter pledging, release or change of pledge of shares.
unclassified: routine low-relevance paperwork - ESOP allotments, compliance certificates, newspaper ad copies, ESG reports, statutory/secretarial auditor appointments (NOT management_change), trading window, loss of share certificates, share transfer/dematerialisation windows, listing/trading commencement notices of an already-announced issue.
Choose results only if the filing is about financial results (or their deck/call/letter). A board outcome whose main item is something else (NCD issue, fund raise, dividend only, appointment) takes that other category. A results announcement beats dividend when both appear in the same board outcome.
For each item give: a one-line factual summary (max 22 words, no adjectives like 'strong' unless in the text), amount_inr_cr only if an INR amount is explicitly stated (convert to crore), severity (low/medium/high for the impact on shareholders), direction (positive/negative/neutral for shareholders).
Use only the supplied text. Return every id exactly once."""

RESULTS_SYSTEM = """You extract headline quarterly financials from an Indian company's exchange-filed results.
Return Revenue (revenue from operations), EBITDA, PBT (profit before tax) and PAT (net profit after tax) for: current quarter, immediately previous quarter, and the same quarter a year ago.
Rules:
- Prefer CONSOLIDATED figures; use standalone only if no consolidated table exists and set basis accordingly.
- Report numbers exactly as printed, in the document's own unit. Set unit to exactly one of: 'INR crore', 'INR lakh', 'INR million', 'INR billion'. Do not convert units. Use plain numbers (no commas); negatives as negative numbers.
- PAT = profit for the period attributable to owners if shown, else total profit after tax.
- EBITDA: only if explicitly reported; else compute PBT + finance costs + depreciation and amortisation ONLY if all three are present and set derived=true; else null.
- For banks/NBFCs, Revenue = total income; EBITDA is null.
- Never guess. If a value is not in the text return null. page is the [p.N] marker the value came from; quote is a short verbatim snippet (max 25 words) supporting the current-quarter figure.
Use only the supplied text."""

MGMT_SYSTEM = """You extract management/director changes from an Indian listed company's exchange filing.
List every person named as joining/appointed or departing/resigning/retiring. For each: name, role/designation, action (joining = newly appointed to the company or to a new role; departing = resigning/retiring/ceasing; other = re-appointment to the same role, change in designation or no real change), effective_date as stated, and background_in_filing = the professional background written in the filing (education, prior employers/roles) in up to 40 words, or null if the filing gives none. page = the [p.N] marker.
Use only the supplied text."""

# NSE announcement types where the exchange subject alone is ambiguous: read the filing's first page.
AMBIGUOUS = re.compile(r"outcome of board|general updates|^updates$|financial result|press release|"
                       r"change in directors|change in management|reg\.? ?30|integrated filing", re.I)


def _filing_text(rec: dict, max_chars: int = 1400) -> str:
    if not rec.get("attachment"):
        return ""
    doc = documents.get_document(rec["attachment"], rec["symbol"], "filing", rec["subject"][:80], rec["ts"][:10])
    if not doc or not doc.get("pages"):
        return ""
    return " ".join(p["text"] for p in doc["pages"][:2])[:max_chars]


def _cls_key(rec: dict) -> str:
    return f"cls_{rec['id']}_{config.PROMPT_VERSION}.json"


def classify(records: list[dict]) -> dict[str, dict[str, Any]]:
    """Return {announcement_id: classification}. Cached per announcement; only misses hit the model."""
    out: dict[str, dict] = {}
    misses: list[dict] = []
    for r in records:
        v, _, _ = cache.get_layered("llm", _cls_key(r))
        if v:
            out[r["id"]] = v
        else:
            misses.append(r)
    if not misses:
        return out

    def prep(r: dict) -> dict:
        txt = r["text"]
        if AMBIGUOUS.search(r["subject"]) or len(txt) < 80:
            extra = _filing_text(r)
            if extra:
                txt = (txt + " | FILING: " + extra)[:1800]
        return {"id": r["id"], "nse_type": r["subject"], "text": txt}

    with ThreadPoolExecutor(max_workers=6) as ex:
        prepared = list(ex.map(prep, misses))

    def run_batch(batch: list[dict]) -> list[ClassifiedItem]:
        user = "\n".join(json.dumps(b, ensure_ascii=False) for b in batch)
        res = llm.call("cheap", ClassifiedBatch, CLASSIFY_SYSTEM, user, tag="classify", max_tokens=2500)
        return res.items if res else []

    batches = [prepared[i:i + 10] for i in range(0, len(prepared), 10)]
    with ThreadPoolExecutor(max_workers=3) as ex:
        results = list(ex.map(run_batch, batches))
    by_id = {it.id: it for items in results for it in items}
    for p, r in zip(prepared, misses):
        it = by_id.get(r["id"])
        if it is None:
            out[r["id"]] = fallback_classification(r)  # not cached: retried next time
            continue
        d = it.model_dump()
        cache.put_cache("llm", _cls_key(r), d)
        out[r["id"]] = d
    return out


def fallback_classification(r: dict) -> dict[str, Any]:
    s = (r["subject"] + " " + r["text"]).lower()
    rules = [
        ("results", r"financial result|outcome of board|quarter ended"),
        ("dividend", r"dividend"), ("management_change", r"change in director|appoint|resign|cessation"),
        ("credit_rating", r"credit rating|rating"), ("earnings_call", r"earnings call|con\. call|conference call"),
        ("investor_meetings", r"investor meet|analyst"), ("M&A", r"acquisition|amalgamation|merger|demerger"),
        ("fund_raise", r"fund raising|buyback|qip|rights issue|preferential"),
        ("agm_egm", r"\bagm\b|\begm\b|annual general"), ("postal_ballot", r"postal ballot"),
        ("insider_trading", r"insider|sast|pit"), ("pledging", r"pledge|encumbrance"),
        ("press_release", r"press release"), ("litigation", r"litigation|order|penalty|fine"),
        ("capex", r"capex|capacity expansion|new plant"),
    ]
    cat = next((c for c, rx in rules if re.search(rx, s)), "unclassified")
    return {"id": r["id"], "category": cat, "confidence": 0.3, "summary": (r["text"] or r["subject"])[:140],
            "amount_inr_cr": None, "severity": "low", "direction": "neutral", "fallback": True}


# ---------------- results ----------------
_RESULT_PAGE_HINT = re.compile(r"revenue from operations|total income|profit before tax|profit for the (period|quarter)|"
                               r"net profit|statement of (consolidated|standalone)? ?(unaudited|audited)? ?financial results", re.I)


def _select_pages(doc: dict, max_chars: int = 15000) -> str:
    pages = doc.get("pages", [])
    scored = sorted(pages[:25], key=lambda p: -len(_RESULT_PAGE_HINT.findall(p["text"])))
    keep = sorted([p for p in scored if _RESULT_PAGE_HINT.search(p["text"])][:5], key=lambda p: p["n"]) or pages[:4]
    txt = "\n".join(f"[p.{p['n']}]\n{p['text']}" for p in keep)
    return txt[:max_chars]


def _pct(cur: float | None, base: float | None) -> float | None:
    if cur is None or base is None or base == 0:
        return None
    return round((cur - base) / abs(base) * 100, 1)


def extract_results(rec: dict) -> dict[str, Any] | None:
    key = f"res_{rec['id']}_{config.PROMPT_VERSION}.json"
    v, _, _ = cache.get_layered("llm", key)
    if v:
        return v if v.get("ok") else None
    if not rec.get("attachment"):
        return None
    doc = documents.get_document(rec["attachment"], rec["symbol"], "results", rec["subject"][:80], rec["ts"][:10])
    if not doc or not doc.get("pages"):
        return None
    text = _select_pages(doc)
    ext = llm.call("cheap", ResultsExtraction, RESULTS_SYSTEM, f"Company: {rec.get('company')}\n\n{text}",
                   tag="results", max_tokens=1800)
    if ext is None:
        return None
    card = {"ok": False}
    metrics = {}
    for name in ("revenue", "ebitda", "pbt", "pat"):
        f = getattr(ext, name)
        metrics[name] = {
            "current": f.current, "prev_quarter": f.prev_quarter, "year_ago": f.year_ago,
            "qoq_pct": _pct(f.current, f.prev_quarter), "yoy_pct": _pct(f.current, f.year_ago),
            "derived": f.derived, "page": f.page, "quote": f.quote,
        }
    if metrics["revenue"]["current"] is not None or metrics["pat"]["current"] is not None:
        card = {"ok": True, "quarter": ext.quarter_label, "unit": ext.unit, "basis": ext.basis,
                "metrics": metrics, "source_url": rec["attachment"], "doc_id": doc["doc_id"]}
    cache.put_cache("llm", key, card)
    return card if card["ok"] else None


# ---------------- management change ----------------
def extract_mgmt(rec: dict) -> list[dict[str, Any]]:
    key = f"mgmt_{rec['id']}_{config.PROMPT_VERSION}.json"
    v, _, _ = cache.get_layered("llm", key)
    if v is not None:
        return v.get("people", [])
    if not rec.get("attachment"):
        return []
    doc = documents.get_document(rec["attachment"], rec["symbol"], "filing", rec["subject"][:80], rec["ts"][:10])
    if not doc or not doc.get("pages"):
        return []
    text = "\n".join(f"[p.{p['n']}]\n{p['text']}" for p in doc["pages"][:4])[:9000]
    ext = llm.call("cheap", MgmtExtraction, MGMT_SYSTEM, f"Company: {rec.get('company')}\n\n{text}",
                   tag="mgmt", max_tokens=1200)
    if ext is None:
        return []
    people = [p.model_dump() | {"source_url": rec["attachment"]} for p in ext.people]
    cache.put_cache("llm", key, {"people": people})
    return people
