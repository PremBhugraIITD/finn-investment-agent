"""Deterministic guidance-vs-delivery logic. All arithmetic and period matching lives here, not in a model.

Quarter indexing uses the Indian fiscal year (April-March): quarter ending Jun = Q1, Sep = Q2, Dec = Q3, Mar = Q4.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any

# month an event happened -> the quarter-end month/year offset it most likely reports
_REPORTED = {1: (12, -1), 2: (12, -1), 3: (12, -1), 4: (3, 0), 5: (3, 0), 6: (3, 0),
             7: (6, 0), 8: (6, 0), 9: (6, 0), 10: (9, 0), 11: (9, 0), 12: (9, 0)}


def qidx(year: int, month: int) -> int:
    """Index of the quarter reported by a call/filing made in (year, month)."""
    qm, dy = _REPORTED[month]
    qy = year + dy
    q, fy = {6: (1, qy + 1), 9: (2, qy + 1), 12: (3, qy + 1), 3: (4, qy)}[qm]
    return fy * 4 + (q - 1)


def qlabel(i: int) -> str:
    fy, q = divmod(i, 4)
    return f"Q{q + 1} FY{str(fy)[-2:]}"


def fylabel(fy: int) -> str:
    return f"FY{str(fy)[-2:]}"


def qidx_from_iso(ts: str) -> int:
    d = datetime.fromisoformat(ts)
    return qidx(d.year, d.month)


def qidx_from_period(period: str) -> int | None:
    m = re.match(r"([A-Za-z]{3})\w*\s+(\d{4})", period.strip())
    if not m:
        return None
    mon = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"].index(m.group(1).lower()) + 1
    return qidx(int(m.group(2)), mon)


def fy_of(i: int) -> int:
    return i // 4


def derive_actuals(card: dict) -> dict[str, float | None]:
    """From a results card (current / prev / year-ago values) derive comparable ratios."""
    m = card["metrics"]
    rev, ebitda, pbt, pat = (m[k] for k in ("revenue", "ebitda", "pbt", "pat"))

    def margin(x, r):
        return round(x / r * 100, 2) if x is not None and r else None
    return {
        "revenue_growth_yoy": rev.get("yoy_pct"),
        "pat_growth_yoy": pat.get("yoy_pct"),
        "pbt_growth_yoy": pbt.get("yoy_pct"),
        "ebitda_margin": margin(ebitda["current"], rev["current"]),
        "ebitda_margin_prev_q": margin(ebitda["prev_quarter"], rev["prev_quarter"]),
        "pat_margin": margin(pat["current"], rev["current"]),
    }


MEASURABLE = {"revenue_growth_yoy", "ebitda_margin", "pat_growth_yoy"}
TOL = {"revenue_growth_yoy": 2.0, "pat_growth_yoy": 3.0, "ebitda_margin": 1.0}


def _fmt(metric: str, v: float | None) -> str:
    if v is None:
        return "n/a"
    return f"{v:.1f}%" if metric in MEASURABLE else f"{v:g}"


def annual_actuals(years: list[dict]) -> dict[int, dict[str, float | None]]:
    """Fiscal-year ratios from annual-report figures (consecutive years only for growth)."""
    by = {}
    for y in years:
        m = re.match(r"FY(\d{2})", y.get("fy", ""))
        if m:
            by[2000 + int(m.group(1))] = y
    out: dict[int, dict[str, float | None]] = {}
    for fy, y in by.items():
        prev = by.get(fy - 1)

        def g(k):
            return round((y[k] / prev[k] - 1) * 100, 2) if prev and y.get(k) and prev.get(k) else None
        out[fy] = {
            "revenue_growth_yoy": g("revenue"), "pat_growth_yoy": g("pat"),
            "ebitda_margin": round(y["ebitda"] / y["revenue"] * 100, 2) if y.get("ebitda") and y.get("revenue") else None,
        }
    return out


def _promised(c: dict) -> str:
    lo, hi = c.get("low"), c.get("high")
    unit = "%" if (c["unit"] == "pct" and c["metric"] != "other") else (" cr" if c["metric"] == "capex_inr_cr" else "")
    if lo is not None and hi is not None and lo != hi:
        return f"{lo:g}-{hi:g}{unit}"
    if lo is not None and hi is None:
        return f">= {lo:g}{unit}"
    if hi is not None and lo is None:
        return f"<= {hi:g}{unit}"
    v = lo if lo is not None else hi
    return f"{v:g}{unit}" if v is not None else "qualitative"


def compare(claim: dict, call_q: int, actuals: dict[int, dict[str, float | None]],
            annual: dict[int, dict[str, float | None]] | None = None) -> dict[str, Any]:
    """Match one numeric guidance claim to delivered actuals. Never compares mismatched periods or bases."""
    metric = claim["metric"]
    row = {"promised": _promised(claim), "metric_key": metric, "delivered": "–", "delta": "–", "status": "not yet measurable",
           "target": "", "note": ""}
    pt = claim["period_type"]
    if metric not in MEASURABLE or claim["unit"] != "pct" or (claim.get("low") is None and claim.get("high") is None):
        row["note"] = "Actual is not in the quarterly results filings" if metric not in MEASURABLE else "No numeric target"
        row["target"] = {"next_quarter": qlabel(call_q + 1), "current_fy": fylabel(fy_of(call_q + 1)),
                         "next_fy": fylabel(fy_of(call_q + 1) + 1)}.get(pt, "unspecified")
        return row
    if pt == "next_quarter":
        t = call_q + 1
        row["target"] = qlabel(t)
        val = (actuals.get(t) or {}).get(metric)
        basis = qlabel(t)
    elif pt in ("current_fy", "next_fy"):
        fy = fy_of(call_q + 1) + (1 if pt == "next_fy" else 0)
        row["target"] = fylabel(fy)
        ann = (annual or {}).get(fy, {}).get(metric)
        if ann is not None:
            val, basis = ann, f"{fylabel(fy)} annual report"
            return _judge(row, claim, metric, val, basis)
        qs = [fy * 4 + k for k in range(4)]
        vals = [(actuals.get(q) or {}).get(metric) for q in qs]
        have = [v for v in vals if v is not None]
        if len(have) < 4:
            row["note"] = f"{len(have)} of 4 quarters of {fylabel(fy)} reported" if have else f"{fylabel(fy)} not reported yet"
            if not have:
                return row
        val = sum(have) / len(have)
        basis = f"{fylabel(fy)} avg of {len(have)}Q"
    else:
        row["target"] = "unspecified"
        row["note"] = "No dated target"
        return row
    if val is None:
        row["note"] = row["note"] or f"{row['target']} results not available"
        return row
    return _judge(row, claim, metric, val, basis)


def _judge(row: dict, claim: dict, metric: str, val: float, basis: str) -> dict:
    lo, hi, tol = claim.get("low"), claim.get("high"), TOL[metric]
    row["delivered"] = f"{_fmt(metric, val)} ({basis})"
    ref = lo if lo is not None else hi
    if lo is not None and val >= lo and (hi is None or val <= hi + 0.0):
        status = "met"
    elif hi is not None and val > hi:
        status = "met"      # beat the top of the range
    elif lo is not None and val >= lo - tol:
        status = "partially met"
    elif hi is not None and lo is None and val <= hi:
        status = "met"
    else:
        status = "missed"
    row["status"] = status
    row["delta"] = f"{val - ref:+.1f} pp vs {'low end' if lo is not None else 'cap'}"
    return row
