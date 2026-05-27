"""api.py — Bot service token API (called by the Discord bot)."""
from fastapi import APIRouter, Depends, Header, HTTPException

from ..config import settings

router = APIRouter(prefix="/api", tags=["api"])


def _require_bot(authorization: str | None = Header(default=None)):
    """Dependency: reject requests that don't carry the bot service token."""
    if not settings.BOT_SERVICE_TOKEN:
        raise HTTPException(status_code=503, detail="Bot API not configured")
    if authorization != f"Bearer {settings.BOT_SERVICE_TOKEN}":
        raise HTTPException(status_code=401, detail="Invalid service token")


# Endpoints added in Chunk 6 (create_character, approve_claim, etc.)
