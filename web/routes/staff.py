"""staff.py — Staff-only pages (roster, approvals, admin)."""
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from ..db import (
    add_coterie_member,
    approve_character,
    approve_claim,
    approve_coterie_request,
    approve_coterie_spend,
    approve_spend,
    close_period,
    create_criterion,
    create_period,
    get_active_period,
    get_coterie,
    get_db,
    list_characters,
    list_coterie_members,
    list_coterie_spends,
    list_coteries,
    list_criteria,
    list_pending_claims,
    list_pending_coterie_requests,
    list_pending_coterie_spends,
    list_pending_spends,
    list_periods,
    reject_character,
    reject_claim,
    reject_coterie_request,
    reject_coterie_spend,
    reject_spend,
    remove_coterie_member,
    set_period_active,
    update_coterie,
    update_criterion,
)
from ..deps import csrf_protect, require_staff
from ..main import _ctx

router = APIRouter(prefix="/staff", tags=["staff"])
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")


def _toast(response, message: str, kind: str = "success") -> None:
    response.headers["X-Enoch-Toast"]      = message
    response.headers["X-Enoch-Toast-Kind"] = kind


# ── Dashboard ─────────────────────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, user: dict = Depends(require_staff)):
    with get_db() as conn:
        pending_claims    = list_pending_claims(conn)
        pending_spends    = list_pending_spends(conn)
        all_chars         = list_characters(conn)
        active_period     = get_active_period(conn)
        coterie_requests  = list_pending_coterie_requests(conn)

    pending_chars = [c for c in all_chars if not c["is_approved"]]
    active_chars  = [c for c in all_chars if c["status"] == "active"]

    return templates.TemplateResponse(
        "staff/dashboard.html",
        _ctx(
            request,
            n_claims=len(pending_claims),
            n_spends=len(pending_spends),
            n_chars=len(pending_chars),
            n_active=len(active_chars),
            n_coterie_reqs=len(coterie_requests),
            active_period=active_period,
            recent_claims=pending_claims[:5],
        ),
    )


# ── Claims ────────────────────────────────────────────────────────────────────

@router.get("/claims", response_class=HTMLResponse)
async def claims_queue(request: Request, user: dict = Depends(require_staff)):
    with get_db() as conn:
        claims = list_pending_claims(conn)
    return templates.TemplateResponse(
        "staff/claims.html", _ctx(request, claims=claims)
    )


@router.post("/claims/{claim_id}/approve", response_class=HTMLResponse)
async def do_approve_claim(
    request: Request,
    claim_id: int,
    user: dict = Depends(require_staff),
    _: None = Depends(csrf_protect),
):
    err = None
    try:
        with get_db() as conn:
            approve_claim(conn, claim_id, user["id"])
    except ValueError as e:
        err = str(e)

    with get_db() as conn:
        claims = list_pending_claims(conn)

    resp = templates.TemplateResponse(
        "staff/partials/claims_table.html", _ctx(request, claims=claims)
    )
    _toast(resp, err or "Claim approved.", "error" if err else "success")
    return resp


@router.post("/claims/{claim_id}/reject", response_class=HTMLResponse)
async def do_reject_claim(
    request: Request,
    claim_id: int,
    user: dict = Depends(require_staff),
    _: None = Depends(csrf_protect),
):
    form   = await request.form()
    reason = (form.get("reason") or "").strip() or "No reason provided"

    err = None
    try:
        with get_db() as conn:
            reject_claim(conn, claim_id, user["id"], reason)
    except ValueError as e:
        err = str(e)

    with get_db() as conn:
        claims = list_pending_claims(conn)

    resp = templates.TemplateResponse(
        "staff/partials/claims_table.html", _ctx(request, claims=claims)
    )
    _toast(resp, err or "Claim rejected.", "error" if err else "info")
    return resp


# ── Spends ────────────────────────────────────────────────────────────────────

@router.get("/spends", response_class=HTMLResponse)
async def spends_queue(request: Request, user: dict = Depends(require_staff)):
    with get_db() as conn:
        spends = list_pending_spends(conn)
    return templates.TemplateResponse(
        "staff/spends.html", _ctx(request, spends=spends)
    )


@router.post("/spends/{spend_id}/approve", response_class=HTMLResponse)
async def do_approve_spend(
    request: Request,
    spend_id: int,
    user: dict = Depends(require_staff),
    _: None = Depends(csrf_protect),
):
    err = None
    try:
        with get_db() as conn:
            approve_spend(conn, spend_id, user["id"])
    except ValueError as e:
        err = str(e)

    with get_db() as conn:
        spends = list_pending_spends(conn)

    resp = templates.TemplateResponse(
        "staff/partials/spends_table.html", _ctx(request, spends=spends)
    )
    _toast(resp, err or "Spend approved.", "error" if err else "success")
    return resp


@router.post("/spends/{spend_id}/reject", response_class=HTMLResponse)
async def do_reject_spend(
    request: Request,
    spend_id: int,
    user: dict = Depends(require_staff),
    _: None = Depends(csrf_protect),
):
    form   = await request.form()
    reason = (form.get("reason") or "").strip() or "No reason provided"

    err = None
    try:
        with get_db() as conn:
            reject_spend(conn, spend_id, user["id"], reason)
    except ValueError as e:
        err = str(e)

    with get_db() as conn:
        spends = list_pending_spends(conn)

    resp = templates.TemplateResponse(
        "staff/partials/spends_table.html", _ctx(request, spends=spends)
    )
    _toast(resp, err or "Spend rejected.", "error" if err else "info")
    return resp


# ── Characters ────────────────────────────────────────────────────────────────

@router.get("/characters", response_class=HTMLResponse)
async def roster(request: Request, user: dict = Depends(require_staff)):
    with get_db() as conn:
        all_chars = list_characters(conn)

    pending = [c for c in all_chars if not c["is_approved"]]
    active  = [c for c in all_chars if c["status"] == "active"]
    retired = [c for c in all_chars if c["status"] == "retired"]
    dead    = [c for c in all_chars if c["status"] == "dead"]

    return templates.TemplateResponse(
        "staff/characters.html",
        _ctx(request, pending=pending, active=active, retired=retired, dead=dead),
    )


@router.post("/characters/{character_id}/approve", response_class=HTMLResponse)
async def do_approve_char(
    request: Request,
    character_id: int,
    user: dict = Depends(require_staff),
    _: None = Depends(csrf_protect),
):
    err = None
    try:
        with get_db() as conn:
            approve_character(conn, character_id, user["id"])
    except (ValueError, Exception) as e:
        err = str(e)

    with get_db() as conn:
        all_chars = list_characters(conn)

    pending = [c for c in all_chars if not c["is_approved"]]
    resp = templates.TemplateResponse(
        "staff/partials/pending_chars_table.html", _ctx(request, pending=pending)
    )
    _toast(resp, err or "Character approved.", "error" if err else "success")
    return resp


@router.post("/characters/{character_id}/reject", response_class=HTMLResponse)
async def do_reject_char(
    request: Request,
    character_id: int,
    user: dict = Depends(require_staff),
    _: None = Depends(csrf_protect),
):
    form   = await request.form()
    reason = (form.get("reason") or "").strip() or "No reason provided"

    err = None
    try:
        with get_db() as conn:
            reject_character(conn, character_id, user["id"], reason)
    except (ValueError, Exception) as e:
        err = str(e)

    with get_db() as conn:
        all_chars = list_characters(conn)

    pending = [c for c in all_chars if not c["is_approved"]]
    resp = templates.TemplateResponse(
        "staff/partials/pending_chars_table.html", _ctx(request, pending=pending)
    )
    _toast(resp, err or "Character returned to player.", "error" if err else "info")
    return resp


# ── Criteria ──────────────────────────────────────────────────────────────────

@router.get("/criteria", response_class=HTMLResponse)
async def criteria_admin(request: Request, user: dict = Depends(require_staff)):
    with get_db() as conn:
        criteria = list_criteria(conn, active_only=False)
    return templates.TemplateResponse(
        "staff/criteria.html", _ctx(request, criteria=criteria)
    )


@router.post("/criteria", response_class=HTMLResponse)
async def create_criterion_route(
    request: Request,
    user: dict = Depends(require_staff),
    _: None = Depends(csrf_protect),
):
    form        = await request.form()
    label       = (form.get("label") or "").strip()
    xp_value    = int(form.get("xp_value") or 1)
    category    = form.get("category") or "player"
    description = (form.get("description") or "").strip()
    req_links   = form.get("requires_rp_links") == "on"
    req_note    = form.get("requires_text_note") == "on"
    sort_order  = int(form.get("sort_order") or 0)

    err = None
    if not label:
        err = "Label is required."
    else:
        try:
            with get_db() as conn:
                create_criterion(
                    conn, label=label, xp_value=xp_value,
                    category=category, description=description,
                    requires_rp_links=req_links, requires_text_note=req_note,
                    sort_order=sort_order,
                )
        except Exception as e:
            err = str(e)

    with get_db() as conn:
        criteria = list_criteria(conn, active_only=False)

    resp = templates.TemplateResponse(
        "staff/criteria.html", _ctx(request, criteria=criteria, create_error=err)
    )
    if not err:
        _toast(resp, f"Criterion '{label}' created.")
    else:
        _toast(resp, err, "error")
    return resp


@router.post("/criteria/{criteria_id}/toggle", response_class=HTMLResponse)
async def toggle_criterion(
    request: Request,
    criteria_id: int,
    user: dict = Depends(require_staff),
    _: None = Depends(csrf_protect),
):
    with get_db() as conn:
        from ..db import get_criterion
        c = get_criterion(conn, criteria_id)
        if not c:
            raise HTTPException(status_code=404)
        update_criterion(conn, criteria_id, active=0 if c["active"] else 1)
        criteria = list_criteria(conn, active_only=False)

    resp = templates.TemplateResponse(
        "staff/partials/criteria_table.html", _ctx(request, criteria=criteria)
    )
    _toast(resp, f"Criterion {'deactivated' if c['active'] else 'activated'}.", "info")
    return resp


@router.post("/criteria/{criteria_id}/update", response_class=HTMLResponse)
async def update_criterion_route(
    request: Request,
    criteria_id: int,
    user: dict = Depends(require_staff),
    _: None = Depends(csrf_protect),
):
    form        = await request.form()
    label       = (form.get("label") or "").strip()
    xp_value    = int(form.get("xp_value") or 1)
    description = (form.get("description") or "").strip()
    req_links   = form.get("requires_rp_links") == "on"
    req_note    = form.get("requires_text_note") == "on"
    sort_order  = int(form.get("sort_order") or 0)

    if label:
        with get_db() as conn:
            update_criterion(
                conn, criteria_id,
                label=label, xp_value=xp_value,
                description=description,
                requires_rp_links=int(req_links),
                requires_text_note=int(req_note),
                sort_order=sort_order,
            )
            criteria = list_criteria(conn, active_only=False)
    else:
        with get_db() as conn:
            criteria = list_criteria(conn, active_only=False)

    resp = templates.TemplateResponse(
        "staff/partials/criteria_table.html", _ctx(request, criteria=criteria)
    )
    _toast(resp, "Criterion updated." if label else "No changes saved.", "success" if label else "info")
    return resp


# ── Periods ───────────────────────────────────────────────────────────────────

@router.get("/periods", response_class=HTMLResponse)
async def periods_admin(request: Request, user: dict = Depends(require_staff)):
    with get_db() as conn:
        periods       = list_periods(conn, limit=30)
        active_period = get_active_period(conn)
    return templates.TemplateResponse(
        "staff/periods.html",
        _ctx(request, periods=periods, active_period=active_period),
    )


@router.post("/periods", response_class=HTMLResponse)
async def create_period_route(
    request: Request,
    user: dict = Depends(require_staff),
    _: None = Depends(csrf_protect),
):
    form        = await request.form()
    label       = (form.get("label") or "").strip()
    period_type = form.get("period_type") or "night"
    phase       = form.get("phase") or "full"
    opens_at    = (form.get("opens_at") or "").strip()
    closes_at   = (form.get("closes_at") or "").strip()

    err = None
    if not label or not opens_at or not closes_at:
        err = "Label, opens-at, and closes-at are all required."
    else:
        try:
            with get_db() as conn:
                create_period(
                    conn,
                    label=label, period_type=period_type, phase=phase,
                    opens_at=opens_at, closes_at=closes_at,
                    created_by=user["id"],
                )
        except Exception as e:
            err = str(e)

    with get_db() as conn:
        periods       = list_periods(conn, limit=30)
        active_period = get_active_period(conn)

    resp = templates.TemplateResponse(
        "staff/periods.html",
        _ctx(request, periods=periods, active_period=active_period, create_error=err),
    )
    if not err:
        _toast(resp, f"Period '{label}' created.")
    else:
        _toast(resp, err, "error")
    return resp


@router.post("/periods/{period_id}/activate", response_class=HTMLResponse)
async def activate_period(
    request: Request,
    period_id: int,
    user: dict = Depends(require_staff),
    _: None = Depends(csrf_protect),
):
    with get_db() as conn:
        set_period_active(conn, period_id)
        periods       = list_periods(conn, limit=30)
        active_period = get_active_period(conn)

    resp = templates.TemplateResponse(
        "staff/partials/periods_table.html",
        _ctx(request, periods=periods, active_period=active_period),
    )
    _toast(resp, "Period activated — XP window is now open.")
    return resp


@router.post("/periods/{period_id}/close", response_class=HTMLResponse)
async def close_period_route(
    request: Request,
    period_id: int,
    user: dict = Depends(require_staff),
    _: None = Depends(csrf_protect),
):
    with get_db() as conn:
        close_period(conn, period_id)
        periods       = list_periods(conn, limit=30)
        active_period = get_active_period(conn)

    resp = templates.TemplateResponse(
        "staff/partials/periods_table.html",
        _ctx(request, periods=periods, active_period=active_period),
    )
    _toast(resp, "Period closed.", "info")
    return resp


# ── Coteries ──────────────────────────────────────────────────────────────────

def _coterie_ctx(conn) -> dict:
    coteries  = list_coteries(conn, status="active")
    requests  = list_pending_coterie_requests(conn)
    co_spends = list_pending_coterie_spends(conn)
    return {"coteries": coteries, "requests": requests, "co_spends": co_spends}


@router.get("/coteries", response_class=HTMLResponse)
async def coteries_admin(request: Request, user: dict = Depends(require_staff)):
    with get_db() as conn:
        ctx = _coterie_ctx(conn)
    return templates.TemplateResponse("staff/coteries.html", _ctx(request, **ctx))


@router.post("/coteries/requests/{request_id}/approve", response_class=HTMLResponse)
async def approve_coterie_req(
    request: Request,
    request_id: int,
    user: dict = Depends(require_staff),
    _: None = Depends(csrf_protect),
):
    err = None
    try:
        with get_db() as conn:
            approve_coterie_request(conn, request_id, user["id"])
    except ValueError as e:
        err = str(e)
    with get_db() as conn:
        ctx = _coterie_ctx(conn)
    resp = templates.TemplateResponse(
        "staff/partials/coterie_requests_table.html", _ctx(request, **ctx)
    )
    _toast(resp, err or "Coterie formed and activated.", "error" if err else "success")
    return resp


@router.post("/coteries/requests/{request_id}/reject", response_class=HTMLResponse)
async def reject_coterie_req(
    request: Request,
    request_id: int,
    user: dict = Depends(require_staff),
    _: None = Depends(csrf_protect),
):
    form   = await request.form()
    reason = (form.get("reason") or "").strip() or "No reason provided"
    err = None
    try:
        with get_db() as conn:
            reject_coterie_request(conn, request_id, user["id"], reason)
    except ValueError as e:
        err = str(e)
    with get_db() as conn:
        ctx = _coterie_ctx(conn)
    resp = templates.TemplateResponse(
        "staff/partials/coterie_requests_table.html", _ctx(request, **ctx)
    )
    _toast(resp, err or "Formation request rejected.", "error" if err else "info")
    return resp


@router.post("/coteries/spends/{spend_id}/approve", response_class=HTMLResponse)
async def approve_co_spend(
    request: Request,
    spend_id: int,
    user: dict = Depends(require_staff),
    _: None = Depends(csrf_protect),
):
    err = None
    try:
        with get_db() as conn:
            approve_coterie_spend(conn, spend_id, user["id"])
    except ValueError as e:
        err = str(e)
    with get_db() as conn:
        ctx = _coterie_ctx(conn)
    resp = templates.TemplateResponse(
        "staff/partials/coterie_spends_table.html", _ctx(request, **ctx)
    )
    _toast(resp, err or "Domain upgrade approved.", "error" if err else "success")
    return resp


@router.post("/coteries/spends/{spend_id}/reject", response_class=HTMLResponse)
async def reject_co_spend(
    request: Request,
    spend_id: int,
    user: dict = Depends(require_staff),
    _: None = Depends(csrf_protect),
):
    form   = await request.form()
    reason = (form.get("reason") or "").strip() or "No reason provided"
    err = None
    try:
        with get_db() as conn:
            reject_coterie_spend(conn, spend_id, user["id"], reason)
    except ValueError as e:
        err = str(e)
    with get_db() as conn:
        ctx = _coterie_ctx(conn)
    resp = templates.TemplateResponse(
        "staff/partials/coterie_spends_table.html", _ctx(request, **ctx)
    )
    _toast(resp, err or "Domain upgrade rejected.", "error" if err else "info")
    return resp


@router.post("/coteries/{coterie_id}/members/{character_id}/remove",
             response_class=HTMLResponse)
async def remove_coterie_member_route(
    request: Request,
    coterie_id: int,
    character_id: int,
    user: dict = Depends(require_staff),
    _: None = Depends(csrf_protect),
):
    with get_db() as conn:
        remove_coterie_member(conn, coterie_id, character_id)
        members = list_coterie_members(conn, coterie_id)
        coterie = get_coterie(conn, coterie_id)
    resp = templates.TemplateResponse(
        "staff/partials/coterie_members_table.html",
        _ctx(request, coterie=coterie, members=members),
    )
    _toast(resp, "Member removed.", "info")
    return resp


@router.get("/coteries/{coterie_id}", response_class=HTMLResponse)
async def coterie_manage_page(
    request: Request,
    coterie_id: int,
    user: dict = Depends(require_staff),
):
    with get_db() as conn:
        coterie = get_coterie(conn, coterie_id)
        if not coterie:
            raise HTTPException(status_code=404)
        members = list_coterie_members(conn, coterie_id)
        spends  = list_coterie_spends(conn, coterie_id)
    return templates.TemplateResponse(
        "staff/coterie_manage.html",
        _ctx(request, coterie=coterie, members=members, spends=spends),
    )


@router.post("/coteries/{coterie_id}/members/add", response_class=HTMLResponse)
async def add_coterie_member_route(
    request: Request,
    coterie_id: int,
    user: dict = Depends(require_staff),
    _: None = Depends(csrf_protect),
):
    form         = await request.form()
    character_id = int(form.get("character_id") or 0)
    role         = form.get("role") or "member"
    err = None
    if not character_id:
        err = "Character ID is required."
    else:
        try:
            with get_db() as conn:
                add_coterie_member(conn, coterie_id, character_id, role)
        except Exception as e:
            err = str(e)
    with get_db() as conn:
        members = list_coterie_members(conn, coterie_id)
        coterie = get_coterie(conn, coterie_id)
    resp = templates.TemplateResponse(
        "staff/partials/coterie_members_table.html",
        _ctx(request, coterie=coterie, members=members),
    )
    _toast(resp, err or "Member added.", "error" if err else "success")
    return resp
