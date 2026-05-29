"""main.py — FastAPI application entry point."""
import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from .config import settings
from .db import (
    get_db, run_migrations, sweep_retirements,
    sweep_period_closing_soon, auto_create_next_period_if_due,
)
from .deps import LoginRequired

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(name)s  %(message)s")

BASE_DIR = Path(__file__).parent


# ── Lifespan ─────────────────────────────────────────────────────

async def _daily_retirement_sweep() -> None:
    """Background task: scan for retirement-eligible characters once a day."""
    # Settle delay so we don't sweep before migrations complete.
    await asyncio.sleep(60)
    while True:
        try:
            with get_db() as conn:
                retired = sweep_retirements(conn)
            if retired:
                log.info("Auto-retired %d character(s) on daily sweep", len(retired))
        except Exception:
            log.exception("Auto-retirement sweep failed")
        await asyncio.sleep(24 * 60 * 60)  # 24h


async def _hourly_period_closing_sweep() -> None:
    """Background task: check every hour for periods closing within 24h
    and enqueue closing-soon announcements. Idempotent — a flag column
    on each row prevents repeat fires."""
    await asyncio.sleep(90)  # settle past migrations + retirement sweep
    while True:
        try:
            with get_db() as conn:
                notified = sweep_period_closing_soon(conn)
                created  = auto_create_next_period_if_due(conn)
            if notified:
                log.info("Enqueued period_closing_soon for %d period(s)", len(notified))
            if created:
                log.info("Auto-created next period %r (id=%s)",
                         created["label"], created["id"])
        except Exception:
            log.exception("Period-closing sweep failed")
        await asyncio.sleep(60 * 60)  # 1h


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Running database migrations…")
    run_migrations()
    log.info("Migrations complete.")
    retirement_task = asyncio.create_task(_daily_retirement_sweep())
    closing_task    = asyncio.create_task(_hourly_period_closing_sweep())
    try:
        yield
    finally:
        retirement_task.cancel()
        closing_task.cancel()


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


_STAFF_ROLE_LABELS = {
    "lead_st":  "Lead ST",
    "co_st":    "Co-ST",
    "reviewer": "Reviewer",
    "helper":   "Helper",
}


# ── Asset cache-busting ──────────────────────────────────────────
# Static assets are served at fixed paths, so browsers cache them hard.
# `static_url` appends a short content hash as ?v= so a *changed* CSS/JS
# file gets a fresh URL (forcing a re-fetch) while unchanged files stay
# cacheable. The hash is memoized on (path, mtime) so a file is only
# re-read when it actually changes on disk.
_ASSET_HASHES: dict[tuple[str, float], str] = {}


def static_url(rel_path: str) -> str:
    """`/static/<rel_path>` with a content-hash cache-buster. Falls back to
    the bare path if the file is missing."""
    full = BASE_DIR / "static" / rel_path
    try:
        mtime = full.stat().st_mtime
    except OSError:
        return f"/static/{rel_path}"
    key = (rel_path, mtime)
    fp = _ASSET_HASHES.get(key)
    if fp is None:
        import hashlib
        fp = hashlib.md5(full.read_bytes()).hexdigest()[:10]
        _ASSET_HASHES[key] = fp
    return f"/static/{rel_path}?v={fp}"


def _ctx(request: Request, **extra) -> dict:
    """Base template context — injected into every render call."""
    user = request.session.get("user")
    flash = request.session.pop("flash", [])
    raw_role = request.session.get("staff_role") or ""
    return {
        "request": request,
        "current_user": user,
        "is_staff": request.session.get("is_staff", False),
        "staff_role": raw_role,
        "staff_role_label": _STAFF_ROLE_LABELS.get(raw_role, ""),
        "csrf_token": request.session.get("_csrf", ""),
        "flash_messages": flash,
        "dev_preview": settings.DEV_PREVIEW,
        "static_url": static_url,
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

    @app.get("/_dev/login", response_class=HTMLResponse)
    async def _dev_login(request: Request):
        """Dev login chooser — never ships to production."""
        html = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Dev Login — Enoch</title>
  <link rel="stylesheet" href="/static/css/tailwind.css">
  <link rel="stylesheet" href="/static/css/codex.css">
  <link href="https://fonts.googleapis.com/css2?family=Cinzel:wght@400;600&family=EB+Garamond:ital,wght@0,400;1,400&display=swap" rel="stylesheet">
</head>
<body class="min-h-screen flex items-center justify-center px-4">
  <div class="gilded p-10 max-w-sm w-full text-center">
    <p class="font-cinzel text-[0.6rem] tracking-[0.4em] uppercase mb-2" style="color:#7a5e29;">Development</p>
    <h1 class="font-cinzel text-2xl text-bone-200 tracking-widest mb-1">Dev Login</h1>
    <p class="font-garamond text-bone-600 text-sm mb-8">ENOCH_DEV_PREVIEW=1 is active</p>

    <div class="space-y-3">
      <a href="/_dev/seed"
         class="btn-send font-cinzel tracking-widest text-xs w-full flex items-center justify-center gap-2 py-3">
        <svg class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2.5">
          <path stroke-linecap="square" d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z"/>
        </svg>
        Login as Staff (DevStaff)
      </a>

      <a href="/_dev/seed_data"
         class="btn-ghost font-cinzel tracking-widest text-xs w-full flex items-center justify-center gap-2 py-2.5">
        Seed Test Character + Login as Staff
      </a>

      <a href="/_dev/player"
         class="btn-ghost font-cinzel tracking-widest text-xs w-full flex items-center justify-center gap-2 py-2.5">
        Login as Player (TestPlayer)
      </a>
    </div>

    <div style="border-top:1px solid #2a1f22;" class="mt-8 pt-6">
      <p class="font-garamond text-bone-700 text-xs leading-relaxed">
        Staff: DevStaff · is_staff=true<br>
        Player: TestPlayer · character: Valeria Morano (Brujah)
      </p>
    </div>
  </div>
</body>
</html>"""
        return HTMLResponse(content=html)

    @app.get("/_dev/seed")
    async def _dev_seed(request: Request):
        """Inject a mock staff session — never ships to production.
        Dev staff get the lead_st role by default so every endpoint
        gated by require_permission lights up under the dev preview."""
        from .db import get_db, upsert_player, set_staff_role, set_settings_admin
        request.session["user"] = {
            "id": "999999999999999999",
            "username": "DevStaff",
            "avatar": None,
        }
        request.session["is_staff"]       = True
        request.session["staff_role"]     = "lead_st"
        request.session["settings_admin"] = True
        request.session["_csrf"]          = "dev-csrf-token"
        # Persist the role so DB-side lookups (audit, role admin UI) see it.
        with get_db() as conn:
            upsert_player(conn, discord_id="999999999999999999", username="DevStaff")
            try:
                set_staff_role(conn, "999999999999999999", "lead_st", actor_id="dev-seed")
                set_settings_admin(conn, "999999999999999999", True, actor_id="dev-seed")
            except ValueError:
                pass
            conn.commit()
        return RedirectResponse(url="/staff", status_code=307)

    @app.get("/_dev/seed_data")
    async def _dev_seed_data(request: Request):
        """Create a test player + character directly — never ships to production."""
        from .db import get_db, upsert_player, create_character, approve_character
        DEV_PLAYER_ID = "111111111111111111"
        DEV_CHAR_NAME = "Valeria Morano"
        with get_db() as conn:
            upsert_player(conn, discord_id=DEV_PLAYER_ID, username="TestPlayer")
            existing = conn.execute(
                "SELECT id FROM characters WHERE discord_id=? AND name=? LIMIT 1",
                (DEV_PLAYER_ID, DEV_CHAR_NAME),
            ).fetchone()
            if not existing:
                char = create_character(
                    conn,
                    discord_id=DEV_PLAYER_ID,
                    name=DEV_CHAR_NAME,
                    clan="brujah",
                    predator_type="Siren",
                    concept="Former NYC DA turned revolutionary",
                    sire="Alejandro Cruz",
                )
                approve_character(conn, char["id"], reviewer_id=DEV_PLAYER_ID)
        return RedirectResponse(url="/_dev/login", status_code=307)

    @app.get("/_dev/player")
    async def _dev_player(request: Request):
        """Switch session to TestPlayer (non-staff) — never ships to production."""
        request.session["user"] = {
            "id": "111111111111111111",
            "username": "TestPlayer",
            "avatar": None,
        }
        request.session["is_staff"]   = False
        request.session["_csrf"]      = "dev-csrf-token"
        return RedirectResponse(url="/characters", status_code=307)

    log.warning("⚠  ENOCH_DEV_PREVIEW=1 — OAuth bypass is active. Never use in production.")


# ── Routers ──────────────────────────────────────────────────────

from .routes import auth, player, staff, api  # noqa: E402

app.include_router(auth.router)
app.include_router(player.router)
app.include_router(staff.router)
app.include_router(api.router)
