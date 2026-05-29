"""auth.py — Discord OAuth 2.0 + session management."""
import logging
import secrets
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from ..config import settings
from ..db import get_db, upsert_player

log = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

# ── Discord constants ─────────────────────────────────────────────────────────
_DISCORD_API   = "https://discord.com/api/v10"
_DISCORD_CDN   = "https://cdn.discordapp.com"
_OAUTH_URL     = "https://discord.com/oauth2/authorize"
_TOKEN_URL     = f"{_DISCORD_API}/oauth2/token"
_SCOPES        = "identify guilds.members.read"


# ── URL builder ───────────────────────────────────────────────────────────────

def _auth_url(state: str) -> str:
    params = {
        "client_id":     settings.DISCORD_CLIENT_ID,
        "redirect_uri":  settings.DISCORD_REDIRECT_URI,
        "response_type": "code",
        "scope":         _SCOPES,
        "state":         state,
        "prompt":        "none",  # skip re-consent if already authorized
    }
    return f"{_OAUTH_URL}?{urlencode(params)}"


# ── Discord API helpers ───────────────────────────────────────────────────────

async def _exchange_code(code: str) -> dict | None:
    """POST /oauth2/token — exchange authorization code for access token."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                _TOKEN_URL,
                data={
                    "client_id":     settings.DISCORD_CLIENT_ID,
                    "client_secret": settings.DISCORD_CLIENT_SECRET,
                    "grant_type":    "authorization_code",
                    "code":          code,
                    "redirect_uri":  settings.DISCORD_REDIRECT_URI,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        if resp.status_code == 200:
            return resp.json()
        log.warning("Token exchange failed: %s %s", resp.status_code, resp.text[:200])
    except httpx.HTTPError as exc:
        log.error("Discord token exchange error: %s", exc)
    return None


async def _fetch_user(access_token: str) -> dict | None:
    """GET /users/@me — fetch the authenticated Discord user."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{_DISCORD_API}/users/@me",
                headers={"Authorization": f"Bearer {access_token}"},
            )
        if resp.status_code == 200:
            return resp.json()
        log.warning("User fetch failed: %s", resp.status_code)
    except httpx.HTTPError as exc:
        log.error("Discord user fetch error: %s", exc)
    return None


async def _fetch_member(access_token: str, guild_id: int) -> dict | None:
    """GET /users/@me/guilds/{guild_id}/member — fetch guild membership + roles."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{_DISCORD_API}/users/@me/guilds/{guild_id}/member",
                headers={"Authorization": f"Bearer {access_token}"},
            )
        if resp.status_code == 200:
            return resp.json()
        if resp.status_code == 404:
            return None  # not in guild
        log.warning("Member fetch failed: %s", resp.status_code)
    except httpx.HTTPError as exc:
        log.error("Discord member fetch error: %s", exc)
    return None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _avatar_url(user_id: str, avatar_hash: str | None) -> str:
    if avatar_hash:
        ext = "gif" if avatar_hash.startswith("a_") else "webp"
        return f"{_DISCORD_CDN}/avatars/{user_id}/{avatar_hash}.{ext}?size=128"
    # Default avatar — index based on user ID (new Discord system)
    default_idx = (int(user_id) >> 22) % 6
    return f"{_DISCORD_CDN}/embed/avatars/{default_idx}.png"


def _flash(request: Request, message: str, kind: str = "info") -> None:
    """Queue a flash message for the next page render."""
    request.session.setdefault("flash", []).append({"message": message, "kind": kind})


def _not_configured_redirect() -> RedirectResponse:
    """Redirect to home when OAuth env vars are not set (e.g. bare local dev)."""
    return RedirectResponse(url="/")


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/login")
async def login(request: Request):
    """Redirect to Discord OAuth consent screen."""
    if not settings.DISCORD_CLIENT_ID or not settings.DISCORD_CLIENT_SECRET:
        return _not_configured_redirect()

    # One-time random state token — stored in session for CSRF validation
    state = secrets.token_urlsafe(32)
    request.session["oauth_state"] = state

    return RedirectResponse(url=_auth_url(state))


@router.get("/callback")
async def callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
):
    """Handle Discord OAuth callback: exchange code, check guild, set session."""

    # ── User denied or Discord error ─────────────────────────────────────────
    if error:
        if error == "access_denied":
            _flash(request, "Login cancelled.", "warning")
        else:
            log.warning("OAuth error from Discord: %s — %s", error, error_description)
            _flash(request, "Discord returned an error. Please try again.", "error")
        return RedirectResponse(url="/auth/login", status_code=303)

    if not code:
        _flash(request, "No authorization code received.", "error")
        return RedirectResponse(url="/auth/login", status_code=303)

    # ── CSRF state validation ─────────────────────────────────────────────────
    stored_state = request.session.pop("oauth_state", None)
    if not stored_state or not secrets.compare_digest(
        (state or "").encode(), stored_state.encode()
    ):
        _flash(request, "Invalid OAuth state — possible CSRF. Please try again.", "error")
        return RedirectResponse(url="/auth/login", status_code=303)

    # ── Exchange code for access token ────────────────────────────────────────
    token_data = await _exchange_code(code)
    if not token_data or "access_token" not in token_data:
        _flash(request, "Could not authenticate with Discord. Please try again.", "error")
        return RedirectResponse(url="/auth/login", status_code=303)

    access_token = token_data["access_token"]

    # ── Fetch Discord user ────────────────────────────────────────────────────
    discord_user = await _fetch_user(access_token)
    if not discord_user:
        _flash(request, "Could not retrieve your Discord profile.", "error")
        return RedirectResponse(url="/auth/login", status_code=303)

    user_id  = discord_user["id"]
    username = (
        discord_user.get("global_name")
        or discord_user.get("username", "")
    )

    # ── Guild membership check ────────────────────────────────────────────────
    is_staff = False
    if settings.DISCORD_GUILD_ID:
        member = await _fetch_member(access_token, settings.DISCORD_GUILD_ID)

        if member is None:
            _flash(
                request,
                "You must be a member of the NYbN Discord server to log in.",
                "error",
            )
            return RedirectResponse(url="/auth/login", status_code=303)

        # Prefer server nickname as display name
        if member.get("nick"):
            username = member["nick"]

        # Staff role check — any matching role grants staff access
        if settings.STAFF_ROLE_IDS:
            member_roles = {int(r) for r in member.get("roles", [])}
            is_staff     = bool(member_roles & set(settings.STAFF_ROLE_IDS))

    # ── Upsert player profile ─────────────────────────────────────────────────
    try:
        with get_db() as conn:
            upsert_player(conn, user_id, username)
    except Exception:
        log.exception("Failed to upsert player profile for %s", user_id)
        # Non-fatal — session still gets set below

    # ── Write session ─────────────────────────────────────────────────────────
    request.session["user"] = {
        "id":         user_id,
        "username":   username,
        "avatar_url": _avatar_url(user_id, discord_user.get("avatar")),
    }
    request.session["is_staff"] = is_staff
    request.session["_csrf"]    = secrets.token_urlsafe(32)

    # Pull the assigned Enoch staff role (if any) into the session so
    # require_permission can gate without hitting the DB on every request.
    if is_staff:
        # NB: get_db is already imported at module top — do NOT re-import it
        # here. A function-local `import get_db` would make the name local to
        # this whole function and turn the earlier upsert call (above) into an
        # UnboundLocalError, silently killing the player-profile upsert.
        from ..db import get_staff_role
        with get_db() as conn:
            role = get_staff_role(conn, user_id)
        request.session["staff_role"] = role or ""

    log.info("Login: %s (%s) staff=%s role=%s", username, user_id, is_staff,
             request.session.get("staff_role") or "—")

    # ── Redirect back to where they were going ────────────────────────────────
    next_url = request.session.pop("login_next", "/")
    # Safety: only allow relative redirects to prevent open redirector
    if not next_url.startswith("/") or next_url.startswith("//"):
        next_url = "/"

    return RedirectResponse(url=next_url, status_code=303)


@router.get("/logout")
async def logout(request: Request):
    """Clear session and redirect to login."""
    request.session.clear()
    return RedirectResponse(url="/auth/login", status_code=303)
