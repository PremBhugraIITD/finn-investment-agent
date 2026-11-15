"""Central configuration. All secrets come from the git-ignored .env."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")

DATA = Path(os.getenv("FINN_DATA_DIR", ROOT / "data"))
SEED = DATA / "seed"
CACHE = Path(os.getenv("FINN_CACHE_DIR", DATA / "cache"))

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
FAST_MODEL = os.getenv("OPENAI_FAST_MODEL", "gpt-4o-mini")
REASONING_MODEL = os.getenv("OPENAI_REASONING_MODEL", "gpt-4o")
REFRESH_SECRET = os.getenv("REFRESH_SECRET", "")
FRONTEND_ORIGIN = os.getenv("FRONTEND_ORIGIN", "*")

# Budgets (per process, per UTC day)
MAX_CHEAP_CALLS = int(os.getenv("MAX_CHEAP_CALLS", "400"))
MAX_DEEP_CALLS = int(os.getenv("MAX_DEEP_CALLS", "30"))

# Cache TTLs (seconds)
NSE_TTL = 300
MARKET_TTL = 6 * 3600
# Total wall-clock budget for a live Yahoo refresh on the request path; then serve cache/seed immediately.
MARKET_LIVE_BUDGET_S = float(os.getenv("MARKET_LIVE_BUDGET_S", "6"))
INFO_TTL = 24 * 3600
SCREENER_TTL = 24 * 3600

# Live document ingestion limits (keep a cold, unseeded ticker inside ~20s and a 512 MB instance).
MAX_PDF_PAGES = int(os.getenv("MAX_PDF_PAGES", "40"))        # pypdf extracts at most the first N pages of any PDF
MAX_PDF_BYTES = int(os.getenv("MAX_PDF_BYTES", str(50_000_000)))
INGEST_TRANSCRIPTS = 4                                        # most recent concall transcripts
INGEST_ANNUAL_REPORTS = 1                                     # latest annual report only
NSE_HISTORY_BUDGET_S = float(os.getenv("NSE_HISTORY_BUDGET_S", "8"))

PROMPT_VERSION = "v2"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

CATEGORIES = [
    "results", "M&A", "fund_raise", "management_change", "litigation",
    "insider_trading", "earnings_call", "investor_meetings", "credit_rating",
    "dividend", "bonus_split", "capex", "press_release", "postal_ballot",
    "agm_egm", "pledging", "unclassified",
]

for _p in (CACHE, CACHE / "nse", CACHE / "market", CACHE / "llm", CACHE / "docs", CACHE / "research"):
    _p.mkdir(parents=True, exist_ok=True)
