"""auth.py — Discord OAuth + session management."""
from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/login")
async def login(request: Request):
    # TODO (Chunk 3): redirect to Discord OAuth URL
    return RedirectResponse(url="/")


@router.get("/callback")
async def callback(request: Request, code: str | None = None):
    # TODO (Chunk 3): exchange code, fetch guilds/roles, set session
    return RedirectResponse(url="/")


@router.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/auth/login")
