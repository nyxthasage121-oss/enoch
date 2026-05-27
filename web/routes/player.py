"""player.py — Player-facing pages."""
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path

from ..main import _ctx

router = APIRouter(tags=["player"])
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    # TODO (Chunk 4): redirect to my characters or login
    return templates.TemplateResponse(
        "player/index.html", _ctx(request)
    )
