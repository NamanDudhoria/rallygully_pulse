import asyncio
import contextlib
import os

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import config, scheduler
from .audit import DomainError
from .db import Base, engine
from .routers import admin, analytics_routes, auth_routes, operations, venues


@contextlib.asynccontextmanager
async def lifespan(_app: FastAPI):
    Base.metadata.create_all(engine)
    task = asyncio.create_task(scheduler.loop()) if config.RUN_SCHEDULER else None
    yield
    if task:
        task.cancel()


app = FastAPI(title="RallyGully Pulse", description="Venue operations & performance intelligence API",
              version="1.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=config.CORS_ORIGINS, allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])


@app.exception_handler(DomainError)
async def domain_error(_req: Request, exc: DomainError):
    return JSONResponse({"detail": exc.message}, status_code=exc.status)


for r in (auth_routes.router, venues.router, operations.router, analytics_routes.router, admin.router):
    app.include_router(r)


@app.get("/api/health")
def health():
    return {"status": "ok"}


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
