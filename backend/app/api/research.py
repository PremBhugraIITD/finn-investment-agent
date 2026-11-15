"""Pull-mode routes."""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from ..models.schemas import FinnError
from ..services import digest, market_data, research

router = APIRouter(prefix="/api/v1/research")


class AnalyzeBody(BaseModel):
    modules: list[str] | None = None
    question: str | None = None
    refresh: bool = False


class AskBody(BaseModel):
    question: str


@router.get("/universe")
def universe():
    return {
        "suggested": digest.research_universe(),
        "portfolio": [{"symbol": c["symbol"], "name": c["name"]} for c in digest.portfolio()],
    }


@router.get("/jobs/{job_id}")
def job(job_id: str):
    j = research.JOBS.get(job_id)
    if not j:
        raise FinnError("JOB_NOT_FOUND", "Unknown research job.", 404)
    return j


@router.get("/{symbol}/documents")
def documents_inventory(symbol: str):
    return research.inventory(symbol)


@router.post("/{symbol}/ingest")
def ingest(symbol: str):
    jid = research.start_job(symbol, ["business"])
    return {"job_id": jid}


@router.post("/{symbol}/analyze")
def analyze(symbol: str, body: AnalyzeBody):
    return {"job_id": research.start_job(symbol, body.modules, body.refresh)}


@router.get("/{symbol}/result")
def result(symbol: str):
    sym = market_data.normalize_symbol(symbol)
    from .. import cache
    v, origin, _ = cache.get_layered("research", f"result_{sym}.json")
    if not v:
        raise FinnError("NO_RESULT", "No analysis yet for this company.", 404)
    return v | {"source": origin}


@router.post("/{symbol}/ask")
def ask(symbol: str, body: AskBody):
    return research.ask(symbol, body.question)
