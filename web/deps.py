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

    token = (
        request.headers.get("X-CSRF-Token")
        or request.query_params.get("_csrf", "")
    )

    if not secrets.compare_digest(token.encode(), expected.encode()):
        raise HTTPException(status_code=403, detail="CSRF token mismatch")
