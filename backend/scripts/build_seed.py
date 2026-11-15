"""Capture timestamped fallback data into data/seed so demos never break.

Usage (from backend/):
  python -m scripts.build_seed market nse        # no LLM cost
  python -m scripts.build_seed push              # classify + results/mgmt extraction for seeded window
  python -m scripts.build_seed research [SYMBOL] # ingest + full analysis for the 4 ad-hoc stocks
  python -m scripts.build_seed sync              # copy runtime LLM/doc caches into data/seed
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

from app import cache, config
from app.services import market_data, nse


def portfolio_symbols() -> list[str]:
    return [c["symbol"] for c in json.loads((config.SEED / "portfolio.json").read_text())["companies"]]


def research_symbols() -> list[str]:
    return [c["symbol"] for c in json.loads((config.SEED / "research_universe.json").read_text())["companies"]]


def seed_market() -> None:
    for s in portfolio_symbols() + research_symbols():
        try:
            m = market_data.get_eod(s, force=True)
            rec = cache.read_json(cache.cache_path("market", f"{s}.json"))
            cache.write_json(cache.seed_path("market", f"{s}.json"), rec)
            print("market", s, m["last_close"], m["change_pct"])
        except Exception as e:
            print("market FAIL", s, e)


def seed_nse(days: int = 45) -> None:
    for s in portfolio_symbols():
        try:
            store = nse.refresh(s, days)
            cache.write_json(cache.seed_path("nse", f"{s}.json"), store)
            print("nse", s, len(store["records"]))
        except Exception as e:
            print("nse FAIL", s, e)


def sync() -> None:
    """Copy runtime LLM/doc/research caches into data/seed (committed)."""
    for sub in ("llm", "docs", "research", "nse"):
        src, dst = config.CACHE / sub, config.SEED / sub
        dst.mkdir(parents=True, exist_ok=True)
        n = 0
        for f in src.glob("*"):
            if f.is_file():
                shutil.copy2(f, dst / f.name)
                n += 1
        print("synced", sub, n)


if __name__ == "__main__":
    args = sys.argv[1:] or ["market", "nse"]
    if "market" in args:
        seed_market()
    if "nse" in args:
        seed_nse()
    if "push" in args:
        from app.services import digest
        digest.warm(portfolio_symbols(), days=30)
    if "research" in args:
        from app.services import research
        only = [a for a in args if a.isupper() and a not in ("market", "nse")]
        for s in only or research_symbols():
            research.run_full(s)
    if "sync" in args:
        sync()
