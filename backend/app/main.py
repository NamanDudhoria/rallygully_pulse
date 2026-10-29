import asyncio
import contextlib
import os
import threading

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import config, scheduler
from .audit import DomainError
from .db import SessionLocal, ensure_schema
from .services import cash
from .timeutil import now_local
from .services import analytics
from .routers import admin, analytics_routes, auth_routes, history_routes, operations, people, store, venues


@contextlib.asynccontextmanager
async def lifespan(_app: FastAPI):
    ensure_schema()
    with SessionLocal() as db:  # cash tracking starts now for any venue that has no account yet
        cash.ensure_accounts(db, now_local())
        db.commit()
    task = asyncio.create_task(scheduler.loop()) if config.RUN_SCHEDULER else None
    stop = threading.Event()
    if config.RUN_SCHEDULER:
        threading.Thread(target=analytics.warm_forever, args=(stop,), daemon=True, name="pulse-warm").start()
        analytics._warm_wake.set()  # warm once at startup
    yield
    stop.set()
    analytics._warm_wake.set()
    if task:
        task.cancel()


app = FastAPI(title="RallyGully Pulse", description="Venue operations & performance intelligence API",
              version="1.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=config.CORS_ORIGINS, allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])


@app.exception_handler(DomainError)
async def domain_error(_req: Request, exc: DomainError):
    return JSONResponse({"detail": exc.message}, status_code=exc.status)


for r in (auth_routes.router, venues.router, operations.router, analytics_routes.router, admin.router,
          people.router, history_routes.router, store.router):
    app.include_router(r)


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.middleware("http")
async def cache_headers(request: Request, call_next):
    """Built files under /assets have a content hash in their name, so browsers may keep them for a year and
    repeat visits load instantly; the page itself is always re-checked so a new release shows straight away."""
    response = await call_next(request)
    path = request.url.path
    if path.startswith("/assets/") and response.status_code == 200:
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    elif not path.startswith("/api/") and response.headers.get("content-type", "").startswith("text/html"):
        response.headers["Cache-Control"] = "no-cache"
    return response


# Serve the built frontend (frontend/dist) when present, so one process runs the whole app.
_dist = os.path.join(os.path.dirname(__file__), "..", "..", "frontend", "dist")
if os.path.isdir(_dist):
    app.mount("/assets", StaticFiles(directory=os.path.join(_dist, "assets")), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        candidate = os.path.normpath(os.path.join(_dist, path))
        if path and candidate.startswith(os.path.normpath(_dist)) and os.path.isfile(candidate):
            return FileResponse(candidate)
        return FileResponse(os.path.join(_dist, "index.html"))
