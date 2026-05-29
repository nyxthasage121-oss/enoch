"""deps.py — Reusable FastAPI dependencies.

Usage:
    from fastapi import Depends
    from ..deps import require_auth, require_staff, csrf_protect

    @router.post("/thing", dependencies=[Depends(csrf_protect)])
    async def my_route(request: Request, user: dict = Depends(require_auth)): ...
"""
import secrets

from fastapi import Depends, HTTPException, Request


# ── Custom exceptions ─────────────────────────────────────────────────────────

class LoginRequired(Exception):
    """Raised by require_auth when no session user exists.
    Handled in main.py — redirects to /auth/login (browser) or
    sends HX-Redirect header (HTMX).
    """
    def __init__(self, next_url: str = "/"):
        self.next_url = next_url


# ── Session readers ───────────────────────────────────────────────────────────

def get_current_user(request: Request) -> dict | None:
    return request.session.get("user")


def get_is_staff(request: Request) -> bool:
    return bool(request.session.get("is_staff", False))


# ── Auth guards ───────────────────────────────────────────────────────────────

def require_auth(request: Request) -> dict:
    """
    Dependency: raise LoginRequired if not authenticated.
    Handled in main.py: browser → 303 redirect; HTMX → HX-Redirect header.
    Stores the original path so /auth/callback can return the user there.
    """
    user = request.session.get("user")
    if not user:
        raise LoginRequired(next_url=str(request.url.path))
    return user


def require_staff(request: Request, user: dict = Depends(require_auth)) -> dict:
    """
    Dependency: 403 if authenticated but not staff.
    Implies require_auth — raises LoginRequired if not logged in at all.
    """
    if not request.session.get("is_staff", False):
        raise HTTPException(status_code=403, detail="Staff access required")
    return user


def _current_staff_role(request: Request) -> str | None:
    """Read the viewer's assigned staff role from the DB — the source of
    truth the admin UI writes. Deliberately NOT cached in the session: a
    role change or revoke must take effect on the very next request, not at
    re-login. (Sessions here are client-side signed cookies, so we couldn't
    invalidate another user's cached copy from the server anyway.) The
    Discord-role-driven `is_staff` flag is separate and still refreshes at
    login by design. Returns None if no role is assigned."""
    user = request.session.get("user") or {}
    discord_id = user.get("id")
    if not discord_id:
        return None
    # Imported here to dodge a circular import at module load.
    from .db import get_db, get_staff_role
    with get_db() as conn:
        return get_staff_role(conn, str(discord_id))


def require_permission(permission: str):
    """Dependency factory: 403 unless the viewer's staff role grants
    the named permission. Implies require_staff. Use it on routes that
    should be gated beyond the basic Discord-role staff check —
    e.g. role management and chronicle settings."""
    def _checker(request: Request, user: dict = Depends(require_staff)) -> dict:
        from .db import staff_role_has_permission
        role = _current_staff_role(request)
        if not staff_role_has_permission(role, permission):
            raise HTTPException(
                status_code=403,
                detail=f"Your staff role does not grant '{permission}' permission.",
            )
        return user
    return _checker


def is_settings_admin(request: Request, user: dict) -> bool:
    """Resolver for the settings-admin gate (migration 024):
        1. ENOCH_SETTINGS_ADMIN_IDS env var (comma-separated discord ids)
           — emergency / bootstrap override.
        2. player_profiles.settings_admin = 1 (read live from the DB).
        Otherwise: False.

    Read straight from the DB on every call — never cached in the session —
    so revoking the flag via the admin UI takes effect on the next request
    instead of lingering until the affected user happens to re-log in. These
    checks only run on low-traffic staff config routes, so the per-call
    lookup is cheap and correctness wins over shaving a query."""
    import os
    env_ids = (os.environ.get("ENOCH_SETTINGS_ADMIN_IDS") or "")
    if str(user.get("id", "")) in {i.strip() for i in env_ids.split(",") if i.strip()}:
        return True
    from .db import get_db, get_player
    with get_db() as conn:
        prof = get_player(conn, str(user["id"]))
    return bool(prof.get("settings_admin")) if prof else False


def require_settings_admin(
    request: Request,
    user: dict = Depends(require_staff),
) -> dict:
    """Gate beyond require_staff for chronicle-wide configuration
    (XP rules, tier budgets, ruleset selection). Modeled on MCbN's
    SETTINGS_ADMIN_DISCORD_IDS pattern — even a lead_st must hold
    settings_admin to flip rule constants. Default-true for any
    pre-existing lead_st via migration 024's backfill."""
    if not is_settings_admin(request, user):
        raise HTTPException(
            status_code=403,
            detail="Settings admin access required. Ask a lead ST to grant the role.",
        )
    return user


# ── CSRF protection ───────────────────────────────────────────────────────────

async def csrf_protect(request: Request) -> None:
    """
    Dependency: reject mutating requests without a valid CSRF token.
    HTMX attaches the token via X-CSRF-Token header (wired in codex.js).
    """
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return

    expected = request.session.get("_csrf")
    if not expected:
        raise HTTPException(status_code=403, detail="No CSRF token in session")

    token = request.headers.get("X-CSRF-Token") or request.query_params.get("_csrf", "")
    if not token:
        # Plain HTML form POST — token lives in the body
        content_type = request.headers.get("content-type", "")
        if "form" in content_type:
            form = await request.form()
            token = form.get("_csrf", "")

    if not secrets.compare_digest(token.encode(), expected.encode()):
        raise HTTPException(status_code=403, detail="CSRF token mismatch")
