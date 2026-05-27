"""main.py — FastAPI application entry point."""
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from .config import settings
from .db import run_migrations
from .deps import LoginRequired

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(name)s  %(message)s")

BASE_DIR = Path(__file__).parent


# ── Lifespan ─────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Running database migrations…")
    run_migrations()
    log.info("Migrations complete.")
    yield


# ── App ──────────────────────────────────────────────────────────

app = FastAPI(
    title="Enoch — NYbN XP Tracker",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
)

app.add_middleware(
    SessionMiddleware,
    secret_key=settings.SESSION_SECRET,
    session_cookie="enoch_session",
    max_age=60 * 60 * 24 * 14,   # 14 days
    same_site="lax",
    https_only=settings.HTTPS_ONLY,
)


# ── Static files + templates ─────────────────────────────────────

app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")

templates = Jinja2Templates(directory=BASE_DIR / "templates")


def _ctx(request: Request, **extra) -> dict:
    """Base template context — injected into every render call."""
    user = request.session.get("user")
    flash = request.session.pop("flash", [])
    return {
        "request": request,
        "current_user": user,
        "is_staff": request.session.get("is_staff", False),
        "csrf_token": request.session.get("_csrf", ""),
        "flash_messages": flash,
        **extra,
    }


# ── Auth redirect handler ────────────────────────────────────────

@app.exception_handler(LoginRequired)
async def handle_login_required(request: Request, exc: LoginRequired):
    from fastapi.responses import Response as _Resp
    request.session["login_next"] = exc.next_url
    # HTMX requests can't follow a 303 — send HX-Redirect instead
    if request.headers.get("HX-Request"):
        return _Resp(status_code=200, headers={"HX-Redirect": "/auth/login"})
    from fastapi.responses import RedirectResponse as _Redir
    return _Redir(url="/auth/login", status_code=303)


# ── Error handlers ───────────────────────────────────────────────

@app.exception_handler(404)
async def not_found(request: Request, exc):
    return templates.TemplateResponse(
        request, "errors/404.html", _ctx(request), status_code=404
    )


@app.exception_handler(403)
async def forbidden(request: Request, exc):
    return templates.TemplateResponse(
        request, "errors/403.html", _ctx(request), status_code=403
    )


@app.exception_handler(500)
async def server_error(request: Request, exc):
    log.exception("Unhandled server error")
    return templates.TemplateResponse(
        request, "errors/500.html", _ctx(request), status_code=500
    )


# ── Dev preview gate ─────────────────────────────────────────────

if settings.DEV_PREVIEW:
    from fastapi.responses import RedirectResponse

    @app.get("/_dev/seed")
    async def _dev_seed(request: Request):
        """Inject a mock staff session — never ships to production."""
        request.session["user"] = {
            "id": "999999999999999999",
            "username": "DevStaff",
            "avatar": None,
        }
        request.session["is_staff"]   = True
        request.session["_csrf"]      = "dev-csrf-token"
        return RedirectResponse(url="/", status_code=307)

    log.warning("⚠  ENOCH_DEV_PREVIEW=1 — OAuth bypass is active. Never use in production.")


# ── Routers ──────────────────────────────────────────────────────

from .routes import auth, player, staff, api  # noqa: E402

app.include_router(auth.router)
app.include_router(player.router)
app.include_router(staff.router)
app.include_router(api.router)
