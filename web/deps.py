"""deps.py — Reusable FastAPI dependencies.

Import these into route handlers to enforce auth, staff access, and CSRF.

Usage:
    from fastapi import Depends
    from ..deps import require_auth, require_staff, csrf_protect

    @router.post("/something", dependencies=[Depends(csrf_protect), Depends(require_auth)])
    async def my_route(request: Request): ...
"""
import secrets

from fastapi import Depends, HTTPException, Request


# ── Session readers ───────────────────────────────────────────────────────────

def get_current_user(request: Request) -> dict | None:
    """Return the session user dict, or None if not logged in."""
    return request.session.get("user")


def get_is_staff(request: Request) -> bool:
    return bool(request.session.get("is_staff", False))


# ── Auth guards ───────────────────────────────────────────────────────────────

def require_auth(request: Request) -> dict:
    """
    Dependency: redirect to login if not authenticated.

    Stores the original path in session so the callback can return the user
    to where they were trying to go.
    """
    user = request.session.get("user")
    if not user:
        request.session["login_next"] = str(request.url.path)
        raise HTTPException(
            status_code=303,
            headers={"Location": "/auth/login"},
            detail="Authentication required",
        )
    return user


def require_staff(request: Request, user: dict = Depends(require_auth)) -> dict:
    """
    Dependency: 403 if authenticated but not staff.
    Implies require_auth (login redirect if not authenticated).
    """
    if not request.session.get("is_staff", False):
        raise HTTPException(status_code=403, detail="Staff access required")
    return user


# ── CSRF protection ───────────────────────────────────────────────────────────

async def csrf_protect(request: Request) -> None:
    """
    Dependency: reject mutating requests without a valid CSRF token.

    HTMX attaches the token via X-CSRF-Token header (wired in codex.js).
    For SSR form fallbacks, _csrf in query params is also accepted.
    """
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return

    expected = request.session.get("_csrf")
    if not expected:
        raise HTTPException(status_code=403, detail="No CSRF token in session")

    # Header first (HTMX), then query param (SSR form fallback)
    token = (
        request.headers.get("X-CSRF-Token")
        or request.query_params.get("_csrf", "")
    )

    if not secrets.compare_digest(token.encode(), expected.encode()):
        raise HTTPException(status_code=403, detail="CSRF token mismatch")
