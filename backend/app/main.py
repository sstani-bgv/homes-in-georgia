import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app.api import api_router
from app.config import REPO_ROOT, settings
from app.db import SessionLocal
from app.services import users
from app.services.errors import DomainError

log = logging.getLogger("app")
DIST = REPO_ROOT / "frontend" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        with SessionLocal() as db:
            for u in users.ensure_bootstrap(db):
                log.warning("bootstrap superadmin ensured: id=%s login=%s tg_id=%s", u.id, u.login, u.tg_id)
    except Exception:  # noqa: BLE001  (DB not migrated yet: the API still starts, /health shows it)
        log.exception("bootstrap failed")
    yield


if settings.APP_ENV != "dev" and (len(settings.SECRET_KEY) < 32 or "change-me" in settings.SECRET_KEY):
    raise RuntimeError("SECRET_KEY is missing or weak: set a random string of 32+ chars in .env")

app = FastAPI(title="Homes in Georgia API", version="1.0", lifespan=lifespan)
# nothing in front compresses: JSON lists and the JS bundle shrink ~5x, which matters on slow mobile links
app.add_middleware(GZipMiddleware, minimum_size=1000)
if settings.DEV_CORS:
    app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
                       allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


@app.exception_handler(DomainError)
async def domain_error(_: Request, e: DomainError):
    return JSONResponse({"detail": e.message, "code": e.code}, status_code=e.status)


@app.exception_handler(RequestValidationError)
async def validation_error(_: Request, e: RequestValidationError):
    return JSONResponse({"detail": "validation error", "code": "validation", "errors": e.errors()}, status_code=422)


app.include_router(api_router, prefix="/api")


@app.get("/health")
@app.get("/api/health")
def health():
    try:
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
        return {"ok": True, "db": True}
    except Exception:  # noqa: BLE001
        return JSONResponse({"ok": False, "db": False}, status_code=503)


@app.get("/map", include_in_schema=False)
def client_map():  # the bot's Mini App map (public, no data)
    return FileResponse(REPO_ROOT / "bot" / "webapp" / "index.html", media_type="text/html")


@app.get("/journey", include_in_schema=False)
def journey():
    return FileResponse(REPO_ROOT / "bot" / "docs" / "user-journey" / "index.html", media_type="text/html")


if (DIST / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")


@app.get("/{path:path}", include_in_schema=False)
def spa(path: str):
    if path == "api" or path.startswith("api/"):
        return JSONResponse({"detail": "not found", "code": "not_found"}, status_code=404)
    if DIST.is_dir():
        f = (DIST / path).resolve()
        if path and f.is_file() and f.is_relative_to(DIST.resolve()):
            return FileResponse(f)
        index = DIST / "index.html"
        if index.is_file():
            # revalidated on every load: after a deploy the browser picks the new hashed bundle, not a cached old app
            return FileResponse(index, media_type="text/html", headers={"Cache-Control": "no-cache"})
    return JSONResponse({"detail": "frontend is not built (frontend/dist)", "code": "not_found"}, status_code=404)
