"""FINN API entrypoint."""
from __future__ import annotations

import threading
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import cache, config
from .models.schemas import FinnError
from .services import digest, market_data, nse


def _background_loop() -> None:
    """Keep announcements warm every 5 minutes and prices after the close (cache TTLs make this cheap)."""
    time.sleep(3)
    first = True
    while True:
        try:
            syms = [c["symbol"] for c in digest.portfolio()]
            market_data.get_many(syms)
            for s in syms:
                try:
                    nse.refresh(s, 7 if first else 3)
                except Exception:
                    pass
            digest.build(hours=24 * 7, wait=False)
            first = False
        except Exception:
            pass
        time.sleep(config.NSE_TTL)


@asynccontextmanager
async def lifespan(app: FastAPI):
    threading.Thread(target=_background_loop, daemon=True).start()
    yield


app = FastAPI(title="FINN API", version="1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if config.FRONTEND_ORIGIN == "*" else [o.strip() for o in config.FRONTEND_ORIGIN.split(",")],
    allow_methods=["*"], allow_headers=["*"],
)


@app.exception_handler(FinnError)
async def finn_error_handler(_: Request, exc: FinnError):
    return JSONResponse(status_code=exc.status, content={"error": {"code": exc.code, "message": exc.message}})


# /health and /ready are `async def` on purpose: they run directly on the event loop, so they answer even when
# every worker thread is busy with slow requests, and they do ZERO I/O (no network, no disk).
_SEED_PRESENT = (config.SEED / "portfolio.json").exists()


@app.get("/health")
async def health():
    return {"status": "ok", "time": cache.iso_now()}


@app.get("/ready")
async def ready():
    return {
        "status": "ready",
        "openai_key": bool(config.OPENAI_API_KEY),
        "models": {"tier1": config.FAST_MODEL, "tier2": config.REASONING_MODEL},
        "seed_present": _SEED_PRESENT,
        "market_data": market_data.live_status(),
        "budget": cache.budget_status(),
    }


from .api import push, research  # noqa: E402

app.include_router(push.router)
app.include_router(research.router)

# Single-service deploy: if the frontend has been built, serve it (SPA fallback) from the same origin.
DIST = config.ROOT / "frontend" / "dist"
if (DIST / "index.html").exists():
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles

    if (DIST / "assets").exists():
        app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith(("api/", "health", "ready")):
            return JSONResponse(status_code=404, content={"error": {"code": "NOT_FOUND", "message": "Not found"}})
        f = (DIST / path).resolve()
        if path and f.is_file() and DIST.resolve() in f.parents:
            return FileResponse(f)
        return FileResponse(DIST / "index.html")
