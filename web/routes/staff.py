"""staff.py — Staff-only pages (roster, approvals, admin)."""
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from ..db import (
    add_coterie_member,
    adjust_xp_manual,
    approve_character,
    approve_claim,
    add_coterie_flaw,
    add_coterie_merit,
    approve_coterie_request,
    start_character_review,
    approve_coterie_spend,
    approve_spend,
    auto_create_next_period_if_due,
    close_period,
    create_criterion,
    create_hunting_site,
    create_period,
    get_active_period,
    get_character,
    get_coterie,
    get_db,
    get_ledger,
    list_all_players,
    list_audit,
    list_characters,
    list_claims_for_character,
    list_coterie_members,
    list_coterie_spends,
    list_coteries,
    list_criteria,
    list_hunting_sites,
    list_pending_claims,
    list_pending_coterie_requests,
    list_pending_coterie_spends,
    list_pending_spends,
    list_claims_history,
    list_coterie_flaws,
    list_coterie_merits,
    list_periods,
    list_recent_closed_periods,
    list_spends_for_character,
    list_spends_history,
    list_upcoming_periods,
    sweep_period_closing_soon,
    delete_character,
    reject_character,
    reject_claim,
    reject_coterie_request,
    remove_coterie_flaw,
    remove_coterie_merit,
    reject_coterie_spend,
    reject_spend,
    remove_coterie_member,
    set_period_active,
    sweep_retirements,
    toggle_hunting_site,
    update_character,
    update_criterion,
    update_hunting_site,
)
from ..deps import csrf_protect, require_permission, require_settings_admin, require_staff
from ..main import _ctx

router = APIRouter(prefix="/staff", tags=["staff"])
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")


def _header_safe(s: str) -> str:
    """HTTP headers must be latin-1; replace common Unicode punctuation."""
    return (s.replace('—', '--').replace('–', '-')
             .replace('‘', "'").replace('’', "'")
             .replace('“', '"').replace('”', '"')
             .encode('latin-1', errors='replace').decode('latin-1'))


def _toast(response, message: str, kind: str = "success") -> None:
    response.headers["X-Enoch-Toast"]      = _header_safe(message)
    response.headers["X-Enoch-Toast-Kind"] = kind


# ── Dashboard ─────────────────────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, user: dict = Depends(require_staff)):
    from ..db import list_characters_near_cap
    with get_db() as conn:
        sweep_retirements(conn)
        sweep_period_closing_soon(conn)
        auto_create_next_period_if_due(conn)
        pending_claims    = list_pending_claims(conn)
        pending_spends    = list_pending_spends(conn)
        all_chars         = list_characters(conn)
        near_cap          = list_characters_near_cap(conn, threshold_xp=30)
        active_period     = get_active_period(conn)
        upcoming_periods  = list_upcoming_periods(conn, limit=3)
        recent_periods    = list_recent_closed_periods(conn, limit=2)
        coterie_requests  = list_pending_coterie_requests(conn)

    pending_chars = [c for c in all_chars if not c["is_approved"] and not c.get("is_draft")]
    active_chars  = [c for c in all_chars if c["status"] == "active"]

    return templates.TemplateResponse(
        request, "staff/dashboard.html",
        _ctx(
            request,
            n_claims=len(pending_claims),
            n_spends=len(pending_spends),
            n_chars=len(pending_chars),
            n_active=len(active_chars),
            n_near_cap=len(near_cap),
            near_cap_list=near_cap[:5],
            n_coterie_reqs=len(coterie_requests),
            active_period=active_period,
            upcoming_periods=upcoming_periods,
            recent_periods=recent_periods,
            user_is_staff=True,
            recent_claims=pending_claims[:5],
        ),
    )


# ── Data export ───────────────────────────────────────────────────────────────

# Tables in the snapshot. Order is arbitrary — JSON keys preserve insertion.
# `bot_outbox` is excluded by design: it's a transient queue, not chronicle data.
_EXPORT_TABLES = (
    "chronicle_settings",
    "player_profiles",
    "characters",
    "play_periods",
    "criteria",
    "xp_claims",
    "spend_requests",
    "ledger_entries",
    "audit_log",
    "coteries",
    "coterie_memberships",
    "coterie_merits",
    "coterie_flaws",
    "coterie_requests",
    "coterie_spends",
    "hunting_sites",
)


@router.get("/admin/export.json")
async def export_snapshot(
    request: Request,
    user: dict = Depends(require_staff),
):
    """Stream a full JSON snapshot of all chronicle data — characters,
    claims, spends, periods, coteries, ledger, audit log. Bot outbox
    (transient queue) and session data are excluded. Writes an audit
    row recording the export."""
    from datetime import datetime, timezone
    import json as _json
    from fastapi.responses import Response
    from ..db import write_audit

    now = datetime.now(timezone.utc)
    payload: dict = {
        "schema_version": 1,
        "exported_at":    now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "exported_by":    user["id"],
        "tables":         {},
    }

    with get_db() as conn:
        for table in _EXPORT_TABLES:
            rows = conn.execute(f"SELECT * FROM {table}").fetchall()
            payload["tables"][table] = rows
        write_audit(conn, user["id"], "export_snapshot", "system", None,
                    after={"tables": len(_EXPORT_TABLES),
                           "row_count": sum(len(v) for v in payload["tables"].values())})

    body = _json.dumps(payload, indent=2, default=str, ensure_ascii=False)
    fname = f"enoch-export-{now.strftime('%Y-%m-%d')}.json"
    return Response(
        content=body,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


# ── Global character search ───────────────────────────────────────────────────

@router.get("/search", response_class=HTMLResponse)
async def character_search(
    request: Request,
    q: str = "",
    user: dict = Depends(require_staff),
):
    """HTMX-friendly search: returns up to 10 character results matching
    the query across name / clan / player. Empty `q` returns an empty
    result panel (so the dropdown collapses naturally)."""
    q = (q or "").strip()
    matches: list[dict] = []
    if len(q) >= 2:
        with get_db() as conn:
            like = f"%{q.lower()}%"
            matches = conn.execute(
                """
                SELECT c.id, c.name, c.clan, c.status, c.is_approved,
                       p.username AS player_username, c.discord_id
                FROM characters c
                LEFT JOIN player_profiles p ON p.discord_id = c.discord_id
                WHERE LOWER(c.name)        LIKE ?
                   OR LOWER(c.clan)        LIKE ?
                   OR LOWER(p.username)    LIKE ?
                   OR LOWER(c.discord_id)  LIKE ?
                ORDER BY
                    CASE WHEN LOWER(c.name) = ? THEN 0
                         WHEN LOWER(c.name) LIKE ? THEN 1
                         ELSE 2 END,
                    c.name
                LIMIT 10
                """,
                (like, like, like, like, q.lower(), f"{q.lower()}%"),
            ).fetchall()
    return templates.TemplateResponse(
        request, "staff/partials/search_results.html",
        _ctx(request, matches=matches, q=q),
    )


# ── Claims ────────────────────────────────────────────────────────────────────

@router.get("/claims", response_class=HTMLResponse)
async def claims_queue(
    request: Request,
    status: str = "pending",
    period_id: int = 0,
    user: dict = Depends(require_staff),
):
    """Unified claims view. Defaults to status='pending' so the pre-
    consolidation workflow is unchanged; pass status=all/approved/
    rejected to switch to history mode. Only pending rows render the
    inline approve/reject buttons."""
    status_filter = (status or "").strip().lower() or "pending"
    # "all" is a UI label — the DB layer just wants None to skip filtering.
    db_status = None if status_filter == "all" else status_filter
    with get_db() as conn:
        claims  = list_claims_history(
            conn,
            status=db_status,
            period_id=period_id or None,
        )
        periods = list_periods(conn, limit=30)
    return templates.TemplateResponse(
        request, "staff/claims.html",
        _ctx(request, claims=claims, periods=periods,
             filter_status=status_filter, filter_period_id=period_id),
    )


@router.post("/claims/{claim_id}/approve", response_class=HTMLResponse)
async def do_approve_claim(
    request: Request,
    claim_id: int,
    user: dict = Depends(require_permission("approve_claim")),
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
        request, "staff/partials/claims_table.html", _ctx(request, claims=claims)
    )
    _toast(resp, err or "Claim approved.", "error" if err else "success")
    return resp


@router.post("/claims/{claim_id}/reject", response_class=HTMLResponse)
async def do_reject_claim(
    request: Request,
    claim_id: int,
    user: dict = Depends(require_permission("approve_claim")),
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
        request, "staff/partials/claims_table.html", _ctx(request, claims=claims)
    )
    _toast(resp, err or "Claim rejected.", "error" if err else "info")
    return resp


# Backwards-compat: legacy /staff/claims/history URLs land on the
# consolidated page with the same filters preserved.
@router.get("/claims/history", response_class=HTMLResponse)
async def claims_history(
    request: Request,
    status: str = "",
    period_id: int = 0,
    user: dict = Depends(require_staff),
):
    qs = []
    if status:    qs.append(f"status={status}")
    if period_id: qs.append(f"period_id={period_id}")
    suffix = ("?" + "&".join(qs)) if qs else "?status=all"
    return RedirectResponse(url=f"/staff/claims{suffix}", status_code=307)


# ── Spends ────────────────────────────────────────────────────────────────────

@router.get("/spends", response_class=HTMLResponse)
async def spends_queue(
    request: Request,
    status: str = "pending",
    category: str = "",
    user: dict = Depends(require_staff),
):
    """Unified spends view — mirrors the claims consolidation. Defaults
    to 'pending' so the existing triage workflow is unchanged."""
    from ..xp_rules import revalidate_spend
    status_filter = (status or "").strip().lower() or "pending"
    db_status = None if status_filter == "all" else status_filter
    with get_db() as conn:
        spends = list_spends_history(
            conn,
            status=db_status,
            category=category or None,
        )
    # Attach a fresh-from-rules cost recalculation per spend so the
    # template can render "player submitted X / system says Y" diff
    # badges. Stored verified_cost stays authoritative.
    for s in spends:
        s["revalidation"] = revalidate_spend(s)
    return templates.TemplateResponse(
        request, "staff/spends.html",
        _ctx(request, spends=spends,
             filter_status=status_filter, filter_category=category),
    )


# Backwards-compat alias for /staff/spends/history.
@router.get("/spends/history", response_class=HTMLResponse)
async def spends_history(
    request: Request,
    status: str = "",
    category: str = "",
    user: dict = Depends(require_staff),
):
    qs = []
    if status:   qs.append(f"status={status}")
    if category: qs.append(f"category={category}")
    suffix = ("?" + "&".join(qs)) if qs else "?status=all"
    return RedirectResponse(url=f"/staff/spends{suffix}", status_code=307)


@router.post("/spends/{spend_id}/approve", response_class=HTMLResponse)
async def do_approve_spend(
    request: Request,
    spend_id: int,
    user: dict = Depends(require_permission("approve_spend")),
    _: None = Depends(csrf_protect),
):
    err = None
    try:
        with get_db() as conn:
            approve_spend(conn, spend_id, user["id"])
    except ValueError as e:
        err = str(e)

    from ..xp_rules import revalidate_spend
    with get_db() as conn:
        spends = list_pending_spends(conn)
    for s in spends:
        s["revalidation"] = revalidate_spend(s)

    resp = templates.TemplateResponse(
        request, "staff/partials/spends_table.html", _ctx(request, spends=spends)
    )
    _toast(resp, err or "Spend approved.", "error" if err else "success")
    return resp


@router.post("/spends/{spend_id}/reject", response_class=HTMLResponse)
async def do_reject_spend(
    request: Request,
    spend_id: int,
    user: dict = Depends(require_permission("approve_spend")),
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

    from ..xp_rules import revalidate_spend
    with get_db() as conn:
        spends = list_pending_spends(conn)
    for s in spends:
        s["revalidation"] = revalidate_spend(s)

    resp = templates.TemplateResponse(
        request, "staff/partials/spends_table.html", _ctx(request, spends=spends)
    )
    _toast(resp, err or "Spend rejected.", "error" if err else "info")
    return resp


# ── Characters ────────────────────────────────────────────────────────────────

@router.get("/characters/{character_id}", response_class=HTMLResponse)
async def char_detail(
    request: Request,
    character_id: int,
    user: dict = Depends(require_staff),
):
    from ..v5_traits import (
        V5_ATTRIBUTES, V5_SKILLS, V5_DISCIPLINES, CLAN_DISCIPLINES,
    )
    with get_db() as conn:
        char   = get_character(conn, character_id)
        if not char:
            raise HTTPException(status_code=404)
        claims = list_claims_for_character(conn, character_id)
        spends = list_spends_for_character(conn, character_id)
        ledger = get_ledger(conn, character_id, limit=50)
    return templates.TemplateResponse(
        request, "staff/character_detail.html",
        _ctx(request, char=char, claims=claims, spends=spends, ledger=ledger,
             v5_attributes=V5_ATTRIBUTES, v5_skills=V5_SKILLS,
             v5_disciplines=V5_DISCIPLINES,
             clan_disciplines=set(CLAN_DISCIPLINES.get(char["clan"], []))),
    )


@router.post("/characters/{character_id}/st-notes", response_class=HTMLResponse)
async def char_st_notes(
    request: Request,
    character_id: int,
    user: dict = Depends(require_permission("edit_character")),
    _: None = Depends(csrf_protect),
):
    """Staff-only working notes on a character. Replaces the whole
    st_notes field on each save — there's no history, this is a scratchpad."""
    with get_db() as conn:
        char = get_character(conn, character_id)
    if not char:
        raise HTTPException(status_code=404)

    form = await request.form()
    st_notes = (form.get("st_notes") or "").strip() or None

    with get_db() as conn:
        update_character(conn, character_id, st_notes=st_notes)

    request.session["flash"] = [{"kind": "success", "message": "ST notes saved."}]
    return RedirectResponse(url=f"/staff/characters/{character_id}", status_code=303)


@router.post("/characters/{character_id}/edit", response_class=HTMLResponse)
async def char_edit(
    request: Request,
    character_id: int,
    user: dict = Depends(require_permission("edit_character")),
    _: None = Depends(csrf_protect),
):
    with get_db() as conn:
        char = get_character(conn, character_id)
    if not char:
        raise HTTPException(status_code=404)

    form          = await request.form()
    name          = (form.get("name") or "").strip() or char["name"]
    clan          = (form.get("clan") or "").strip() or char["clan"]
    predator_type = (form.get("predator_type") or "").strip() or None
    concept       = (form.get("concept") or "").strip() or None
    sire          = (form.get("sire") or "").strip() or None
    covenant      = (form.get("covenant") or "").strip() or None
    profile_blurb = (form.get("profile_blurb") or "").strip() or None
    has_ingrained = form.get("has_ingrained_flaw") == "on"

    err = None
    try:
        with get_db() as conn:
            update_character(
                conn, character_id,
                name=name, clan=clan, predator_type=predator_type,
                concept=concept, sire=sire, covenant=covenant,
                profile_blurb=profile_blurb, has_ingrained_flaw=has_ingrained,
            )
    except Exception as e:
        err = str(e)

    with get_db() as conn:
        char   = get_character(conn, character_id)
        claims = list_claims_for_character(conn, character_id)
        spends = list_spends_for_character(conn, character_id)
        ledger = get_ledger(conn, character_id, limit=50)

    resp = templates.TemplateResponse(
        request, "staff/character_detail.html",
        _ctx(request, char=char, claims=claims, spends=spends, ledger=ledger,
             edit_error=err, edit_success=(not err)),
    )
    _toast(resp, err or "Character updated.", "error" if err else "success")
    return resp


_INACTIVE_THRESHOLD_DAYS = 28   # 4 weeks


@router.get("/characters", response_class=HTMLResponse)
async def roster(request: Request, user: dict = Depends(require_staff)):
    from datetime import datetime, timezone
    with get_db() as conn:
        all_chars = list_characters(conn)

    # Flag active characters that haven't earned/spent XP recently. We
    # ignore pending/retired/dead — those are expected to be quiet.
    now = datetime.now(timezone.utc)
    for c in all_chars:
        ts = c.get("last_activity_at")
        if c["status"] == "active" and c["is_approved"]:
            if not ts:
                # Approved but never any XP activity — show as inactive
                # only if approved more than 4 weeks ago.
                approved = c.get("approved_at")
                ref = approved or c["created_at"]
                c["inactive_days"] = (now - datetime.strptime(ref[:19],
                                       "%Y-%m-%dT%H:%M:%S").replace(
                                       tzinfo=timezone.utc)).days
            else:
                c["inactive_days"] = (now - datetime.strptime(ts[:19],
                                       "%Y-%m-%dT%H:%M:%S").replace(
                                       tzinfo=timezone.utc)).days
            c["is_inactive"] = c["inactive_days"] >= _INACTIVE_THRESHOLD_DAYS
        else:
            c["is_inactive"] = False
            c["inactive_days"] = 0

    # Drafts (player still filling the short-form sheet, or wizard not
    # yet submitted) are invisible to staff until the player presses
    # Submit for Review. Filter them out of every roster bucket.
    all_chars = [c for c in all_chars if not c.get("is_draft")]
    pending = [c for c in all_chars if not c["is_approved"]]
    active  = [c for c in all_chars if c["status"] == "active"]
    retired = [c for c in all_chars if c["status"] == "retired"]
    dead    = [c for c in all_chars if c["status"] == "dead"]

    return templates.TemplateResponse(
        request, "staff/characters.html",
        _ctx(request, pending=pending, active=active, retired=retired, dead=dead),
    )


@router.post("/characters/{character_id}/start-review", response_class=HTMLResponse)
async def do_start_review(
    request: Request,
    character_id: int,
    user: dict = Depends(require_permission("approve_character")),
    _: None = Depends(csrf_protect),
):
    err = None
    try:
        with get_db() as conn:
            row = start_character_review(conn, character_id, user["id"])
            if row is None:
                err = "Character not found."
    except Exception as e:
        err = str(e)

    with get_db() as conn:
        all_chars = list_characters(conn)
    pending = [c for c in all_chars if not c["is_approved"] and not c.get("is_draft")]
    resp = templates.TemplateResponse(
        request, "staff/partials/pending_chars_table.html",
        _ctx(request, pending=pending),
    )
    _toast(resp, err or "Review started — player sheet is now locked.",
           "error" if err else "info")
    return resp


@router.post("/characters/{character_id}/approve", response_class=HTMLResponse)
async def do_approve_char(
    request: Request,
    character_id: int,
    user: dict = Depends(require_permission("approve_character")),
    _: None = Depends(csrf_protect),
):
    err = None
    try:
        with get_db() as conn:
            approve_character(conn, character_id, user["id"])
    except (ValueError, Exception) as e:
        err = str(e)

    # Plain form post (no HX-Request header) — staff hit Approve from the
    # detail page, not the roster's HTMX swap. Redirect back to the now-
    # approved detail page rather than dumping the roster partial here.
    if not request.headers.get("hx-request"):
        request.session["flash"] = [{
            "kind": "error" if err else "success",
            "message": err or "Character approved.",
        }]
        return RedirectResponse(url=f"/staff/characters/{character_id}", status_code=303)

    with get_db() as conn:
        all_chars = list_characters(conn)

    pending = [c for c in all_chars if not c["is_approved"] and not c.get("is_draft")]
    resp = templates.TemplateResponse(
        request, "staff/partials/pending_chars_table.html", _ctx(request, pending=pending)
    )
    _toast(resp, err or "Character approved.", "error" if err else "success")
    return resp


@router.post("/characters/{character_id}/reject", response_class=HTMLResponse)
async def do_reject_char(
    request: Request,
    character_id: int,
    user: dict = Depends(require_permission("reject_character")),
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

    # Plain form post — staff hit Return from the detail page. Redirect
    # so they see the returned-state UI rather than the roster partial.
    if not request.headers.get("hx-request"):
        request.session["flash"] = [{
            "kind": "error" if err else "info",
            "message": err or "Character returned to player.",
        }]
        return RedirectResponse(url=f"/staff/characters/{character_id}", status_code=303)

    with get_db() as conn:
        all_chars = list_characters(conn)

    pending = [c for c in all_chars if not c["is_approved"] and not c.get("is_draft")]
    resp = templates.TemplateResponse(
        request, "staff/partials/pending_chars_table.html", _ctx(request, pending=pending)
    )
    _toast(resp, err or "Character returned to player.", "error" if err else "info")
    return resp


@router.post("/characters/{character_id}/delete", response_class=HTMLResponse)
async def do_delete_char(
    request: Request,
    character_id: int,
    user: dict = Depends(require_permission("delete_character")),
    _: None = Depends(csrf_protect),
):
    with get_db() as conn:
        char = get_character(conn, character_id)
        if not char:
            raise HTTPException(status_code=404)
        name = char["name"]
        delete_character(conn, character_id)

    request.session["flash"] = [{"kind": "info", "message": f"\"{name}\" has been permanently deleted."}]
    return RedirectResponse(url="/staff/characters", status_code=303)


@router.post("/characters/{character_id}/retire", response_class=HTMLResponse)
async def do_retire_char(
    request: Request,
    character_id: int,
    user: dict = Depends(require_permission("edit_character")),
    _: None = Depends(csrf_protect),
):
    """Manual retire — flips status to 'retired'. The auto-retire
    sweep handles cap+time-elapsed automatically; this route is for
    when staff needs to take action sooner (player request, dropped
    chronicle, etc.).
    Also suspends any coterie contributions the retiring character
    made — Steward rule, mirrors the inactivity sweep."""
    from ..db import write_audit, suspend_member_contributions
    with get_db() as conn:
        char = get_character(conn, character_id)
        if not char:
            raise HTTPException(status_code=404)
        before = char["status"]
        update_character(conn, character_id, status="retired")
        write_audit(conn, user["id"], "manual_retire", "character", character_id,
                    before={"status": before}, after={"status": "retired"})
        affected = suspend_member_contributions(conn, character_id, actor_id=user["id"])
    msg = f"\"{char['name']}\" retired."
    if affected:
        msg += f" Suspended their contributions to {len(affected)} coterie/coteries."
    request.session["flash"] = [{"kind": "info", "message": msg}]
    return RedirectResponse(url=f"/staff/characters/{character_id}", status_code=303)


@router.post("/characters/{character_id}/unretire", response_class=HTMLResponse)
async def do_unretire_char(
    request: Request,
    character_id: int,
    user: dict = Depends(require_permission("edit_character")),
    _: None = Depends(csrf_protect),
):
    """Reverse a retirement. Flips status to 'active', clears the
    retirement_eligible_at marker so the next sweep doesn't re-fire,
    and reactivates any suspended coterie contributions."""
    from ..db import write_audit, unsuspend_member_contributions
    with get_db() as conn:
        char = get_character(conn, character_id)
        if not char:
            raise HTTPException(status_code=404)
        if char["status"] not in ("retired", "dead"):
            request.session["flash"] = [{"kind": "error",
                "message": f"\"{char['name']}\" isn't retired."}]
            return RedirectResponse(url=f"/staff/characters/{character_id}", status_code=303)
        before = char["status"]
        update_character(conn, character_id, status="active",
                         retirement_eligible_at=None)
        write_audit(conn, user["id"], "manual_unretire", "character", character_id,
                    before={"status": before}, after={"status": "active"})
        affected = unsuspend_member_contributions(conn, character_id, actor_id=user["id"])
    msg = f"\"{char['name']}\" is back on the active roster."
    if affected:
        msg += f" Reactivated their contributions to {len(affected)} coterie/coteries."
    request.session["flash"] = [{"kind": "info", "message": msg}]
    return RedirectResponse(url=f"/staff/characters/{character_id}", status_code=303)


# Recognised character lifecycle statuses. 'inactive' is the new
# reversible soft-hide; 'retired' / 'dead' carry IC finality.
_CHAR_LIFECYCLE = ("active", "inactive", "retired", "dead", "pending")


@router.post("/characters/{character_id}/set-status", response_class=HTMLResponse)
async def do_set_char_status(
    request: Request,
    character_id: int,
    user: dict = Depends(require_permission("edit_character")),
    _: None = Depends(csrf_protect),
):
    """Set a character's lifecycle status. Used by the Tools tab to
    flip between active / inactive (soft-hide) without going through
    the heavier retire/unretire pair, which carry their own audit
    verbs and IC flavor."""
    from ..db import write_audit
    form = await request.form()
    status = (form.get("status") or "").strip().lower()
    if status not in _CHAR_LIFECYCLE:
        request.session["flash"] = [{"kind": "error",
            "message": f"Unknown status: {status!r}"}]
        return RedirectResponse(url=f"/staff/characters/{character_id}", status_code=303)
    from ..db import (
        suspend_member_contributions,
        unsuspend_member_contributions,
    )
    with get_db() as conn:
        char = get_character(conn, character_id)
        if not char:
            raise HTTPException(status_code=404)
        before = char["status"]
        if before == status:
            request.session["flash"] = [{"kind": "info",
                "message": f"\"{char['name']}\" is already {status}."}]
            return RedirectResponse(url=f"/staff/characters/{character_id}", status_code=303)
        update_character(conn, character_id, status=status)
        write_audit(conn, user["id"], "set_status", "character", character_id,
                    before={"status": before}, after={"status": status})

        # Per Steward rule: a member who goes inactive has their coterie
        # contributions suspended; coming back to active reactivates them.
        # Retired/dead also suspend (more decisive than inactive but the
        # downstream effect is identical — those dots stop counting).
        suspend_msg = ""
        if status in ("inactive", "retired", "dead") and before not in ("inactive", "retired", "dead"):
            affected = suspend_member_contributions(
                conn, character_id, actor_id=user["id"],
            )
            if affected:
                suspend_msg = (f" {len(affected)} coterie/coteries had their "
                               f"contributions suspended.")
        elif status == "active" and before in ("inactive", "retired", "dead"):
            affected = unsuspend_member_contributions(
                conn, character_id, actor_id=user["id"],
            )
            if affected:
                suspend_msg = (f" {len(affected)} coterie/coteries had their "
                               f"contributions reactivated.")

    request.session["flash"] = [{"kind": "info",
        "message": f"\"{char['name']}\" is now {status}.{suspend_msg}"}]
    return RedirectResponse(url=f"/staff/characters/{character_id}", status_code=303)


@router.post("/characters/{character_id}/toggle-lock", response_class=HTMLResponse)
async def do_toggle_lock(
    request: Request,
    character_id: int,
    user: dict = Depends(require_permission("edit_character")),
    _: None = Depends(csrf_protect),
):
    """Toggle profile_locked. When locked, the player's edit page
    refuses to save profile-tab fields — keeps an approved IC blurb
    from drifting after staff signed off on it."""
    from ..db import write_audit
    with get_db() as conn:
        char = get_character(conn, character_id)
        if not char:
            raise HTTPException(status_code=404)
        new_val = 0 if char.get("profile_locked") else 1
        update_character(conn, character_id, profile_locked=new_val)
        write_audit(conn, user["id"], "toggle_profile_lock", "character", character_id,
                    after={"profile_locked": bool(new_val)})
    msg = "Profile locked — player can no longer edit." if new_val else "Profile unlocked."
    request.session["flash"] = [{"kind": "info", "message": msg}]
    return RedirectResponse(url=f"/staff/characters/{character_id}", status_code=303)


@router.post("/characters/{character_id}/set-ingrained", response_class=HTMLResponse)
async def do_set_ingrained(
    request: Request,
    character_id: int,
    user: dict = Depends(require_permission("edit_character")),
    _: None = Depends(csrf_protect),
):
    """Staff grant: mark a character as having the Ingrained Discipline
    Flaw + record WHICH discipline carries it. Submit with discipline=''
    to clear both the flag and the recorded discipline."""
    from ..db import write_audit
    form = await request.form()
    raw = (form.get("discipline") or "").strip()[:60] or None
    with get_db() as conn:
        char = get_character(conn, character_id)
        if not char:
            raise HTTPException(status_code=404)
        update_character(conn, character_id,
                         has_ingrained_flaw=1 if raw else 0,
                         ingrained_discipline=raw)
        write_audit(conn, user["id"], "set_ingrained_discipline",
                    "character", character_id,
                    after={"ingrained_discipline": raw,
                           "has_ingrained_flaw": bool(raw)})
    msg = (f"Ingrained Discipline Flaw set on {raw}." if raw
           else "Ingrained Discipline Flaw cleared.")
    request.session["flash"] = [{"kind": "success", "message": msg}]
    return RedirectResponse(url=f"/staff/characters/{character_id}", status_code=303)


# ── Bulk pending-character actions ───────────────────────────────────────────

async def _bulk_action_ids(request: Request) -> list[int]:
    """Read repeated character_ids from the form. FastAPI's getlist
    returns strings; we coerce + de-dupe + drop garbage entries here."""
    form = await request.form()
    raw = form.getlist("character_ids") if hasattr(form, "getlist") else []
    seen: set[int] = set()
    for v in raw:
        try:
            seen.add(int(v))
        except (TypeError, ValueError):
            continue
    return sorted(seen)


@router.post("/queue/bulk-approve", response_class=HTMLResponse)
async def do_bulk_approve(
    request: Request,
    user: dict = Depends(require_permission("approve_character")),
    _: None = Depends(csrf_protect),
):
    ids = await _bulk_action_ids(request)
    approved, errors = 0, []
    with get_db() as conn:
        for char_id in ids:
            try:
                approve_character(conn, char_id, user["id"])
                approved += 1
            except Exception as e:  # noqa: BLE001 — surface any per-row failure
                errors.append(f"#{char_id}: {e}")

    flash: list[dict] = []
    if approved:
        flash.append({"kind": "success",
                      "message": f"Approved {approved} character{'s' if approved != 1 else ''}."})
    for err in errors:
        flash.append({"kind": "error", "message": err})
    if not approved and not errors:
        flash.append({"kind": "info", "message": "No characters selected."})
    request.session["flash"] = flash
    return RedirectResponse(url="/staff/characters", status_code=303)


@router.post("/queue/bulk-start-review", response_class=HTMLResponse)
async def do_bulk_start_review(
    request: Request,
    user: dict = Depends(require_permission("approve_character")),
    _: None = Depends(csrf_protect),
):
    ids = await _bulk_action_ids(request)
    started, skipped, errors = 0, 0, []
    with get_db() as conn:
        for char_id in ids:
            try:
                row = start_character_review(conn, char_id, user["id"])
                if row is None:
                    skipped += 1
                else:
                    started += 1
            except Exception as e:  # noqa: BLE001
                errors.append(f"#{char_id}: {e}")

    flash: list[dict] = []
    if started:
        flash.append({"kind": "info",
                      "message": f"Review started on {started} character{'s' if started != 1 else ''}; player sheets locked."})
    if skipped:
        flash.append({"kind": "info",
                      "message": f"{skipped} skipped (already under review or missing)."})
    for err in errors:
        flash.append({"kind": "error", "message": err})
    if not (started or skipped or errors):
        flash.append({"kind": "info", "message": "No characters selected."})
    request.session["flash"] = flash
    return RedirectResponse(url="/staff/characters", status_code=303)


# ── Criteria ──────────────────────────────────────────────────────────────────

@router.get("/criteria", response_class=HTMLResponse)
async def criteria_admin(request: Request, user: dict = Depends(require_staff)):
    with get_db() as conn:
        criteria = list_criteria(conn, active_only=False)
    return templates.TemplateResponse(
        request, "staff/criteria.html", _ctx(request, criteria=criteria)
    )


@router.post("/criteria", response_class=HTMLResponse)
async def create_criterion_route(
    request: Request,
    user: dict = Depends(require_permission("manage_criteria")),
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
        request, "staff/criteria.html", _ctx(request, criteria=criteria, create_error=err)
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
    user: dict = Depends(require_permission("manage_criteria")),
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
        request, "staff/partials/criteria_table.html", _ctx(request, criteria=criteria)
    )
    _toast(resp, f"Criterion {'deactivated' if c['active'] else 'activated'}.", "info")
    return resp


@router.post("/criteria/{criteria_id}/update", response_class=HTMLResponse)
async def update_criterion_route(
    request: Request,
    criteria_id: int,
    user: dict = Depends(require_permission("manage_criteria")),
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
        request, "staff/partials/criteria_table.html", _ctx(request, criteria=criteria)
    )
    _toast(resp, "Criterion updated." if label else "No changes saved.", "success" if label else "info")
    return resp


# ── Periods ───────────────────────────────────────────────────────────────────

@router.get("/periods", response_class=HTMLResponse)
async def periods_admin(request: Request, user: dict = Depends(require_staff)):
    """Folded into /staff/admin#periods 2026-05 per Steward direction.
    Keep this URL as a redirect so deep-links + muscle memory still land
    in the right place; remove once analytics show no one hits it."""
    return RedirectResponse(url="/staff/admin#periods", status_code=307)


@router.post("/periods", response_class=HTMLResponse)
async def create_period_route(
    request: Request,
    user: dict = Depends(require_permission("manage_period")),
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

    # Post-merge (2026-05): always bounce back to the Admin → Periods tab.
    # Flash carries the error or success message across the redirect.
    request.session["flash"] = [{
        "kind": "error" if err else "success",
        "message": err or f"Period '{label}' created.",
    }]
    return RedirectResponse(url="/staff/admin#periods", status_code=303)


@router.post("/periods/{period_id}/activate", response_class=HTMLResponse)
async def activate_period(
    request: Request,
    period_id: int,
    user: dict = Depends(require_permission("manage_period")),
    _: None = Depends(csrf_protect),
):
    with get_db() as conn:
        set_period_active(conn, period_id)
        periods       = list_periods(conn, limit=30)
        active_period = get_active_period(conn)

    resp = templates.TemplateResponse(
        request, "staff/partials/periods_table.html",
        _ctx(request, periods=periods, active_period=active_period),
    )
    _toast(resp, "Period activated — XP window is now open.")
    return resp


@router.post("/periods/{period_id}/close", response_class=HTMLResponse)
async def close_period_route(
    request: Request,
    period_id: int,
    user: dict = Depends(require_permission("manage_period")),
    _: None = Depends(csrf_protect),
):
    with get_db() as conn:
        close_period(conn, period_id)
        periods       = list_periods(conn, limit=30)
        active_period = get_active_period(conn)

    resp = templates.TemplateResponse(
        request, "staff/partials/periods_table.html",
        _ctx(request, periods=periods, active_period=active_period),
    )
    _toast(resp, "Period closed.", "info")
    return resp


@router.post("/periods/auto-create-toggle", response_class=HTMLResponse)
async def toggle_auto_create_periods(
    request: Request,
    user: dict = Depends(require_permission("manage_period")),
    _: None = Depends(csrf_protect),
):
    """Flip the chronicle-wide automatic period-generation toggle. When on,
    the system infers cadence from recent periods and keeps exactly one
    period on deck (see db.auto_create_next_period_if_due). Enabling it also
    attempts an immediate stamp so staff get instant feedback if one's due."""
    from ..db import upsert_settings
    form = await request.form()
    enabled = 1 if form.get("enabled") == "on" else 0
    with get_db() as conn:
        upsert_settings(conn, actor_id=user["id"],
                        auto_create_periods_enabled=enabled)
        created = auto_create_next_period_if_due(conn) if enabled else None
    msg = ("Automatic period generation enabled." if enabled
           else "Automatic period generation disabled.")
    if created:
        msg += f" Stamped \"{created['label']}\"."
    request.session["flash"] = [{"kind": "success", "message": msg}]
    return RedirectResponse(url="/staff/admin#periods", status_code=303)


@router.post("/periods/schedules", response_class=HTMLResponse)
async def create_schedule_route(
    request: Request,
    user: dict = Depends(require_permission("manage_period")),
    _: None = Depends(csrf_protect),
):
    """Save a reusable schedule template. Used by the bulk-generate
    feature on /staff/periods."""
    from ..db import create_period_schedule
    form          = await request.form()
    name          = (form.get("name") or "").strip()
    label_pattern = (form.get("label_pattern") or "Night {n}").strip()
    period_type   = (form.get("period_type") or "night").strip()
    phase         = (form.get("phase") or "full").strip()
    anchor_at     = (form.get("anchor_at") or "").strip()
    try:
        cadence_days   = int(form.get("cadence_days") or 14)
        duration_hours = int(form.get("duration_hours") or 48)
    except ValueError:
        cadence_days, duration_hours = 0, 0

    err = None
    if not name or not anchor_at:
        err = "Name and anchor date are required."
    elif cadence_days < 1 or duration_hours < 1:
        err = "Cadence and duration must be positive integers."
    else:
        # Normalize datetime-local to UTC ISO (datetime-local sends
        # 'YYYY-MM-DDTHH:MM' without seconds or timezone)
        if "T" in anchor_at and not anchor_at.endswith("Z") and "+" not in anchor_at:
            anchor_at = anchor_at + ":00Z" if anchor_at.count(":") == 1 else anchor_at + "Z"
        try:
            with get_db() as conn:
                create_period_schedule(
                    conn,
                    name=name, label_pattern=label_pattern,
                    period_type=period_type, phase=phase,
                    cadence_days=cadence_days, duration_hours=duration_hours,
                    anchor_at=anchor_at, created_by=user["id"],
                )
        except ValueError as e:
            err = str(e)

    if err:
        request.session["flash"] = [{"kind": "error", "message": err}]
    else:
        request.session["flash"] = [{"kind": "success",
                                     "message": f"Schedule \"{name}\" saved."}]
    return RedirectResponse(url="/staff/admin#periods", status_code=303)


@router.post("/periods/schedules/{schedule_id}/stamp", response_class=HTMLResponse)
async def stamp_schedule_route(
    request: Request,
    schedule_id: int,
    user: dict = Depends(require_permission("manage_period")),
    _: None = Depends(csrf_protect),
):
    """Generate the next N periods from a saved schedule."""
    from ..db import stamp_periods_from_schedule
    form = await request.form()
    try:
        count = int(form.get("count") or 1)
    except ValueError:
        count = 0

    if count < 1:
        request.session["flash"] = [{"kind": "error",
            "message": "Count must be at least 1."}]
        return RedirectResponse(url="/staff/admin#periods", status_code=303)

    try:
        with get_db() as conn:
            result = stamp_periods_from_schedule(conn, schedule_id, count, user["id"])
    except ValueError as e:
        request.session["flash"] = [{"kind": "error", "message": str(e)}]
        return RedirectResponse(url="/staff/admin#periods", status_code=303)

    msg = f"Stamped {result['created']} period(s)."
    if result["skipped"]:
        msg += f" {result['skipped']} skipped (already exist)."
    request.session["flash"] = [{"kind": "success", "message": msg}]
    return RedirectResponse(url="/staff/admin#periods", status_code=303)


@router.post("/periods/schedules/{schedule_id}/delete", response_class=HTMLResponse)
async def delete_schedule_route(
    request: Request,
    schedule_id: int,
    user: dict = Depends(require_permission("manage_period")),
    _: None = Depends(csrf_protect),
):
    from ..db import delete_period_schedule, get_period_schedule
    with get_db() as conn:
        sched = get_period_schedule(conn, schedule_id)
        if sched:
            delete_period_schedule(conn, schedule_id, actor_id=user["id"])
    request.session["flash"] = [{"kind": "info",
                                 "message": f"Schedule \"{sched['name']}\" deleted."}
                                if sched else
                                {"kind": "error", "message": "Schedule not found."}]
    return RedirectResponse(url="/staff/admin#periods", status_code=303)


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
    return templates.TemplateResponse(request, "staff/coteries.html", _ctx(request, **ctx))


@router.post("/coteries/requests/{request_id}/approve", response_class=HTMLResponse)
async def approve_coterie_req(
    request: Request,
    request_id: int,
    user: dict = Depends(require_permission("manage_coterie")),
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
        request, "staff/partials/coterie_requests_table.html", _ctx(request, **ctx)
    )
    _toast(resp, err or "Coterie formed and activated.", "error" if err else "success")
    return resp


@router.post("/coteries/requests/{request_id}/reject", response_class=HTMLResponse)
async def reject_coterie_req(
    request: Request,
    request_id: int,
    user: dict = Depends(require_permission("manage_coterie")),
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
        request, "staff/partials/coterie_requests_table.html", _ctx(request, **ctx)
    )
    _toast(resp, err or "Formation request rejected.", "error" if err else "info")
    return resp


@router.post("/coteries/spends/{spend_id}/approve", response_class=HTMLResponse)
async def approve_co_spend(
    request: Request,
    spend_id: int,
    user: dict = Depends(require_permission("approve_spend")),
    _: None = Depends(csrf_protect),
):
    form  = await request.form()
    notes = (form.get("notes") or "").strip() or None
    err = None
    try:
        with get_db() as conn:
            approve_coterie_spend(conn, spend_id, user["id"], notes=notes)
    except ValueError as e:
        err = str(e)
    with get_db() as conn:
        ctx = _coterie_ctx(conn)
    resp = templates.TemplateResponse(
        request, "staff/partials/coterie_spends_table.html", _ctx(request, **ctx)
    )
    _toast(resp, err or "Coterie spend approved.", "error" if err else "success")
    return resp


@router.post("/coteries/spends/{spend_id}/commit-all", response_class=HTMLResponse)
async def commit_all_co_spend(
    request: Request,
    spend_id: int,
    user: dict = Depends(require_permission("manage_coterie")),
    _: None = Depends(csrf_protect),
):
    """Staff shortcut: commit every uncommitted member in one go.
    Members who can't afford it are skipped (their count surfaces
    in the toast so staff knows to follow up)."""
    from ..db import commit_all_coterie_contributions
    err, summary = None, None
    try:
        with get_db() as conn:
            summary = commit_all_coterie_contributions(conn, spend_id, user["id"])
    except ValueError as e:
        err = str(e)
    with get_db() as conn:
        ctx = _coterie_ctx(conn)
    resp = templates.TemplateResponse(
        request, "staff/partials/coterie_spends_table.html", _ctx(request, **ctx)
    )
    if err:
        _toast(resp, err, "error")
    elif summary:
        msg = f"Committed {summary['committed_now']} member(s)."
        if summary["skipped"]:
            msg += f" {len(summary['skipped'])} skipped (insufficient XP or missing)."
        if summary["all_committed"]:
            msg += " Spend is now Funded — ready for approval."
        _toast(resp, msg, "success" if summary["all_committed"] else "info")
    return resp


@router.post("/coteries/spends/{spend_id}/reject", response_class=HTMLResponse)
async def reject_co_spend(
    request: Request,
    spend_id: int,
    user: dict = Depends(require_permission("approve_spend")),
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
        request, "staff/partials/coterie_spends_table.html", _ctx(request, **ctx)
    )
    _toast(resp, err or "Domain upgrade rejected.", "error" if err else "info")
    return resp


@router.post("/coteries/{coterie_id}/members/{character_id}/remove",
             response_class=HTMLResponse)
async def remove_coterie_member_route(
    request: Request,
    coterie_id: int,
    character_id: int,
    user: dict = Depends(require_permission("manage_coterie")),
    _: None = Depends(csrf_protect),
):
    with get_db() as conn:
        remove_coterie_member(conn, coterie_id, character_id)
        members = list_coterie_members(conn, coterie_id)
        coterie = get_coterie(conn, coterie_id)
    resp = templates.TemplateResponse(
        request, "staff/partials/coterie_members_table.html",
        _ctx(request, coterie=coterie, members=members),
    )
    _toast(resp, "Member removed.", "info")
    return resp


# NB: must come BEFORE /coteries/{coterie_id} so FastAPI doesn't try
# to coerce "search-chars" into an int.
@router.get("/coteries/search-chars", response_class=HTMLResponse)
async def search_chars_for_coterie(
    request: Request,
    user: dict = Depends(require_permission("manage_coterie")),
):
    """HTMX search endpoint for the staff coterie member picker.
    Matches character name OR player username (case-insensitive)
    and returns a small results dropdown the staff can click. Used
    so staff can add members by hunting for either side of the
    char/player relationship without having to look up Discord IDs."""
    q = (request.query_params.get("q") or "").strip()
    if len(q) < 2:
        return HTMLResponse(content="", status_code=200)
    like = f"%{q}%"
    with get_db() as conn:
        rows = conn.execute("""
            SELECT c.id, c.name, c.clan, c.status, c.discord_id,
                   pp.username AS player_username
            FROM characters c
            LEFT JOIN player_profiles pp ON pp.discord_id = c.discord_id
            WHERE (LOWER(c.name) LIKE LOWER(?)
                   OR LOWER(COALESCE(pp.username, '')) LIKE LOWER(?))
              AND c.is_approved = 1
              AND c.status IN ('active', 'inactive')
            ORDER BY c.name COLLATE NOCASE
            LIMIT 20
        """, (like, like)).fetchall()
    return templates.TemplateResponse(
        request, "staff/partials/coterie_member_search.html",
        _ctx(request, results=rows, query=q),
    )


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
        merits  = list_coterie_merits(conn, coterie_id)
        flaws   = list_coterie_flaws(conn, coterie_id)
    return templates.TemplateResponse(
        request, "staff/coterie_manage.html",
        _ctx(request, coterie=coterie, members=members, spends=spends,
             merits=merits, flaws=flaws),
    )


# ── Coterie merits + flaws — staff CRUD ───────────────────────────────────────

@router.post("/coteries/{coterie_id}/merits/add", response_class=HTMLResponse)
async def coterie_merit_add(
    request: Request,
    coterie_id: int,
    user: dict = Depends(require_permission("manage_coterie")),
    _: None = Depends(csrf_protect),
):
    form = await request.form()
    err = None
    try:
        character_id = int(form.get("character_id") or 0)
        merit_name   = (form.get("merit_name") or "").strip()
        dots         = max(1, min(5, int(form.get("dots") or 1)))
        merit_type   = form.get("merit_type") or "purchased"
        if not merit_name or not character_id:
            err = "Character and merit name are required."
        if not err:
            with get_db() as conn:
                add_coterie_merit(conn, coterie_id, character_id, merit_name,
                                  dots, merit_type, actor_id=user["id"])
    except ValueError as e:
        err = str(e)

    with get_db() as conn:
        merits  = list_coterie_merits(conn, coterie_id)
        members = list_coterie_members(conn, coterie_id)
    resp = templates.TemplateResponse(
        request, "staff/partials/coterie_merits_table.html",
        _ctx(request, coterie={"id": coterie_id}, merits=merits, members=members),
    )
    _toast(resp, err or "Merit added.", "error" if err else "success")
    return resp


@router.post("/coteries/{coterie_id}/merits/{merit_id}/remove",
             response_class=HTMLResponse)
async def coterie_merit_remove(
    request: Request,
    coterie_id: int,
    merit_id: int,
    user: dict = Depends(require_permission("manage_coterie")),
    _: None = Depends(csrf_protect),
):
    with get_db() as conn:
        remove_coterie_merit(conn, merit_id, actor_id=user["id"])
        merits  = list_coterie_merits(conn, coterie_id)
        members = list_coterie_members(conn, coterie_id)
    resp = templates.TemplateResponse(
        request, "staff/partials/coterie_merits_table.html",
        _ctx(request, coterie={"id": coterie_id}, merits=merits, members=members),
    )
    _toast(resp, "Merit removed.")
    return resp


@router.post("/coteries/{coterie_id}/flaws/add", response_class=HTMLResponse)
async def coterie_flaw_add(
    request: Request,
    coterie_id: int,
    user: dict = Depends(require_permission("manage_coterie")),
    _: None = Depends(csrf_protect),
):
    form = await request.form()
    err = None
    try:
        flaw_name      = (form.get("flaw_name") or "").strip()
        dots           = max(1, min(5, int(form.get("dots") or 1)))
        creation_grant = max(0, int(form.get("creation_grant") or 0))
        if not flaw_name:
            err = "Flaw name is required."
        if not err:
            with get_db() as conn:
                add_coterie_flaw(conn, coterie_id, flaw_name, dots,
                                 creation_grant, actor_id=user["id"])
    except ValueError as e:
        err = str(e)

    with get_db() as conn:
        flaws = list_coterie_flaws(conn, coterie_id)
    resp = templates.TemplateResponse(
        request, "staff/partials/coterie_flaws_table.html",
        _ctx(request, coterie={"id": coterie_id}, flaws=flaws),
    )
    _toast(resp, err or "Flaw added.", "error" if err else "success")
    return resp


@router.post("/coteries/{coterie_id}/flaws/{flaw_id}/remove",
             response_class=HTMLResponse)
async def coterie_flaw_remove(
    request: Request,
    coterie_id: int,
    flaw_id: int,
    user: dict = Depends(require_permission("manage_coterie")),
    _: None = Depends(csrf_protect),
):
    with get_db() as conn:
        remove_coterie_flaw(conn, flaw_id, actor_id=user["id"])
        flaws = list_coterie_flaws(conn, coterie_id)
    resp = templates.TemplateResponse(
        request, "staff/partials/coterie_flaws_table.html",
        _ctx(request, coterie={"id": coterie_id}, flaws=flaws),
    )
    _toast(resp, "Flaw removed.")
    return resp


@router.post("/coteries", response_class=HTMLResponse)
async def create_coterie_route(
    request: Request,
    user: dict = Depends(require_permission("manage_coterie")),
    _: None = Depends(csrf_protect),
):
    """Staff-side coterie creation. Bypasses the player formation
    request flow — staff often need to seed a coterie before its
    members are ready to submit one themselves (or because the
    players asked them to handle the paperwork)."""
    from ..db import create_coterie
    form     = await request.form()
    name     = (form.get("name") or "").strip()
    chasse   = int(form.get("chasse") or 1)
    lien     = int(form.get("lien") or 0)
    portillon = int(form.get("portillon") or 0)
    role_id  = (form.get("discord_role_id") or "").strip() or None

    if not name:
        request.session["flash"] = [{"kind": "error",
            "message": "Coterie name is required."}]
        return RedirectResponse(url="/staff/coteries", status_code=303)

    try:
        with get_db() as conn:
            co = create_coterie(
                conn, name=name,
                chasse=max(1, min(5, chasse)),
                lien=max(0, min(5, lien)),
                portillon=max(0, min(5, portillon)),
                discord_role_id=role_id,
            )
            from ..db import write_audit
            write_audit(conn, user["id"], "create_coterie", "coterie", co["id"],
                        after={"name": name, "by": "staff"})
        request.session["flash"] = [{"kind": "success",
            "message": f"Coterie \"{name}\" created."}]
        return RedirectResponse(url=f"/staff/coteries/{co['id']}", status_code=303)
    except Exception as e:
        request.session["flash"] = [{"kind": "error", "message": str(e)}]
        return RedirectResponse(url="/staff/coteries", status_code=303)


@router.post("/coteries/{coterie_id}/members/add", response_class=HTMLResponse)
async def add_coterie_member_route(
    request: Request,
    coterie_id: int,
    user: dict = Depends(require_permission("manage_coterie")),
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
        request, "staff/partials/coterie_members_table.html",
        _ctx(request, coterie=coterie, members=members),
    )
    _toast(resp, err or "Member added.", "error" if err else "success")
    return resp


# ── Admin ─────────────────────────────────────────────────────────────────────

def _admin_ctx_extras() -> dict:
    """Shared context loader for staff/admin.html.
    Every route that re-renders the admin page (settings save, XP adjust,
    role assignment, etc.) must funnel through this so the template never
    sees an Undefined for chronicle_settings / criteria / players.

    The 2026-05 admin merge folded /staff/periods and /staff/sites into
    tabs here, so this loader now also assembles the periods + schedules
    + sites + coteries-for-picker datasets each of those tabs needs."""
    from ..db import (
        get_settings, list_restrictions, list_periods, list_period_schedules,
        list_hunting_sites,
    )
    from ..v5_traits import V5_PREDATOR_TYPES
    with get_db() as conn:
        restrictions = list_restrictions(conn)
    # Flatten restrictions into a {(type, id): mode} dict the template can
    # check with a single Jinja lookup, plus an `unlocked_predator_ids`
    # set for the predator-type checkbox section.
    rdict = {(r["component_type"], r["component_id"]): r["mode"]
             for r in restrictions}
    unlocked_predator_ids = {
        cid for (ctype, cid), mode in rdict.items()
        if ctype == "predator_type" and mode == "unlocked"
    }
    with get_db() as conn:
        return {
            "players":               list_all_players(conn),
            "all_chars":             list_characters(conn),
            "chronicle_settings":    dict(get_settings(conn) or {}),
            "criteria":              list_criteria(conn, active_only=False),
            "restrictions":          restrictions,
            "restriction_lookup":    rdict,
            "unlocked_predator_ids": unlocked_predator_ids,
            # Periods tab
            "periods":               list_periods(conn, limit=30),
            "active_period":         get_active_period(conn),
            "schedules":             list_period_schedules(conn, active_only=False),
            # Hunting Sites tab
            "sites":                 list_hunting_sites(conn, active_only=False),
            "coteries":              _all_coteries_for_picker(conn),
            "predator_types":        V5_PREDATOR_TYPES,
            "boroughs":              _BOROUGHS,
        }


@router.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request, user: dict = Depends(require_staff)):
    """Admin home — tabbed view consolidating chronicle settings, the
    player roster, and the XP criteria editor under one URL. The tab
    can be deep-linked via the URL fragment (#criteria etc.)."""
    return templates.TemplateResponse(
        request, "staff/admin.html",
        _ctx(request, **_admin_ctx_extras()),
    )


@router.post("/admin/roles/{discord_id}/set", response_class=HTMLResponse)
async def set_role_route(
    request: Request,
    discord_id: str,
    user: dict = Depends(require_permission("manage_roles")),
    _: None = Depends(csrf_protect),
):
    """Assign or clear a staff role for a player. Only roles defined in
    STAFF_ROLES are accepted; pass role='' (or omit) to revoke."""
    from ..db import STAFF_ROLES, set_staff_role
    form = await request.form()
    role = (form.get("role") or "").strip().lower() or None
    if role is not None and role not in STAFF_ROLES:
        request.session["flash"] = [{"kind": "error",
            "message": f"Unknown role: {role!r}"}]
        return RedirectResponse(url="/staff/admin#staff", status_code=303)
    try:
        with get_db() as conn:
            set_staff_role(conn, discord_id, role, actor_id=user["id"])
    except ValueError as e:
        request.session["flash"] = [{"kind": "error", "message": str(e)}]
        return RedirectResponse(url="/staff/admin#staff", status_code=303)

    msg = "Role cleared." if role is None else f"Role set to {role}."
    request.session["flash"] = [{"kind": "success", "message": msg}]
    return RedirectResponse(url="/staff/admin#staff", status_code=303)


@router.post("/admin/settings-admin/{discord_id}/set", response_class=HTMLResponse)
async def set_settings_admin_route(
    request: Request,
    discord_id: str,
    user: dict = Depends(require_settings_admin),
    _: None = Depends(csrf_protect),
):
    """Grant or revoke the settings_admin flag on a player (Pattern 5).
    Only existing settings admins can grant or revoke the flag — locks
    out a self-promotion path. The lead_st backfill in migration 024
    means there's always at least one admin to start the chain."""
    from ..db import set_settings_admin
    form = await request.form()
    enabled = form.get("enabled") == "on"
    try:
        with get_db() as conn:
            set_settings_admin(conn, discord_id, enabled, actor_id=user["id"])
            conn.commit()
    except ValueError as e:
        request.session["flash"] = [{"kind": "error", "message": str(e)}]
        return RedirectResponse(url="/staff/admin#staff", status_code=303)

    msg = "Settings admin granted." if enabled else "Settings admin revoked."
    request.session["flash"] = [{"kind": "success", "message": msg}]
    return RedirectResponse(url="/staff/admin#staff", status_code=303)


@router.post("/admin/settings", response_class=HTMLResponse)
async def admin_settings_save(
    request: Request,
    user: dict = Depends(require_settings_admin),
    _: None = Depends(csrf_protect),
):
    """Update chronicle-wide toggles from the admin page. Handles every
    setting in one shot: sheet-on-create, homebrew rules, revenants.

    Gated by require_settings_admin (Pattern 5) so a co_st can't flip
    XP rules without explicit grant — even if they have manage_settings
    permission. Lead STs get the grant automatically via migration 024."""
    from ..db import upsert_settings

    form = await request.form()

    # Ruleset selector — normalize + validate. Falls back to 'standard'
    # if the form somehow sends an unknown value. For back-compat with
    # the legacy use_homebrew_rules checkbox, if it's posted without an
    # explicit active_ruleset, infer 'homebrew'.
    from ..db import RULESETS
    active_ruleset = (form.get("active_ruleset") or "").strip().lower()
    if not active_ruleset and form.get("use_homebrew_rules") == "on":
        active_ruleset = "homebrew"
    if not active_ruleset:
        active_ruleset = "standard"
    if active_ruleset not in RULESETS:
        active_ruleset = "standard"

    payload: dict = {
        "require_sheet_on_create":   1 if form.get("require_sheet_on_create") == "on" else 0,
        # Old binary flag stays in sync with the new selector so anything
        # still reading it doesn't break.
        "use_homebrew_rules":        1 if active_ruleset == "homebrew" else 0,
        "active_ruleset":            active_ruleset,
        # Legacy homebrew budgets — kept for back-compat with the old
        # single-budget UI; superseded by homebrew_tier_budgets below.
        "homebrew_starting_xp":      max(0, int(form.get("homebrew_starting_xp") or 75)),
        "homebrew_merit_budget":     max(0, int(form.get("homebrew_merit_budget") or 7)),
        "homebrew_advantage_budget": max(0, int(form.get("homebrew_advantage_budget") or 2)),
        "homebrew_background_budget":max(0, int(form.get("homebrew_background_budget") or 5)),
        "homebrew_flaw_cap":         max(0, int(form.get("homebrew_flaw_cap") or 2)),
        "revenants_enabled":         1 if form.get("revenants_enabled") == "on" else 0,
    }

    # Restricted predator types unlock list — Steward opt-in per
    # chronicle for normally-banned predator types like Blood Leech and
    # Tithe Collector. Stored in chronicle_restrictions (migration 022).
    # Form posts one checkbox per restricted name (e.g.
    # `unlock_predator_Blood Leech` = "on" if unlocked).
    from ..v5_traits import V5_RESTRICTED_PREDATOR_TYPES
    from ..db import set_restriction, clear_restriction
    with get_db() as conn:
        for name in V5_RESTRICTED_PREDATOR_TYPES:
            if form.get(f"unlock_predator_{name}") == "on":
                set_restriction(conn, "predator_type", name, "unlocked",
                                reason="Staff unlock via admin UI",
                                updated_by=user["id"])
            else:
                clear_restriction(conn, "predator_type", name, "unlocked",
                                  actor_id=user["id"])
        conn.commit()

    # Per-tier homebrew budgets — form fields are tier_<key>_<field>
    # (e.g. tier_mortal_xp, tier_ancilla_mab, tier_neonate_flaw_cap).
    # Only stored when the ruleset isn't 'standard'; the admin form
    # hides the table under standard ruleset.
    #
    # Per Steward UX change (2026-05): the form posts a single "mab"
    # field per tier (combined Merits + Advantages + Backgrounds pool).
    # We split it back to the three legacy fields here so the wizard
    # sidebar and the cost validator code can keep using the existing
    # three-bucket schema. Equal three-way split is the simplest model
    # — players can later move dots between buckets within their pool.
    def _split_mab(total: int) -> tuple[int, int, int]:
        merits = total // 3
        advantages = total // 3
        backgrounds = total - merits - advantages
        return merits, advantages, backgrounds

    tier_budgets: dict[str, dict] = {}
    # NB: must list every tier the admin form renders (see admin.html +
    # db._TIER_DEFAULTS). 'fledgling' was previously omitted here, which
    # silently dropped any Fledgling budget override the staff entered.
    for tier in ("mortal", "ghoul", "revenant", "fledgling", "thinblood", "neonate", "ancilla"):
        bucket: dict = {}
        # XP override
        raw_xp = form.get(f"tier_{tier}_xp")
        if raw_xp:
            try: bucket["xp"] = max(0, int(raw_xp))
            except ValueError: pass
        # Combined M/A/B pool
        raw_mab = form.get(f"tier_{tier}_mab")
        if raw_mab:
            try:
                total = max(0, int(raw_mab))
                m, a, b = _split_mab(total)
                bucket["merits"] = m
                bucket["advantages"] = a
                bucket["backgrounds"] = b
                # Stash original so re-rendering shows the staff-entered total
                # verbatim rather than the post-split sum (which could differ
                # by ±1 due to integer rounding).
                bucket["merits_advantages_backgrounds"] = total
            except ValueError:
                pass
        # Flaw cap override
        raw_fc = form.get(f"tier_{tier}_flaw_cap")
        if raw_fc:
            try: bucket["flaw_cap"] = max(0, int(raw_fc))
            except ValueError: pass

        if bucket:
            tier_budgets[tier] = bucket
    if tier_budgets:
        payload["homebrew_tier_budgets"] = tier_budgets

    # Revenant family list — posted one per line as `name | parent_clan`.
    # Rich data (disciplines/bane/compulsion) lives in the seed JSON; we
    # preserve it across admin edits by merging by name when the family
    # already exists in the current list.
    from ..db import get_settings
    with get_db() as conn:
        existing = (get_settings(conn) or {}).get("revenant_families") or []
    existing_by_name = {f.get("name"): f for f in existing if isinstance(f, dict)}

    raw_families = (form.get("revenant_families") or "").strip()
    families: list[dict] = []
    for line in raw_families.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split("|", 1)]
        name        = parts[0][:60]
        parent_clan = (parts[1][:40] if len(parts) > 1 and parts[1] else None)
        if not name:
            continue
        if name in existing_by_name:
            # Preserve disciplines/bane/compulsion data
            merged = dict(existing_by_name[name])
            merged["name"] = name
            if parent_clan is not None:
                merged["parent_clan"] = parent_clan
            families.append(merged)
        else:
            families.append({"name": name, "parent_clan": parent_clan})
    payload["revenant_families"] = families

    with get_db() as conn:
        upsert_settings(conn, actor_id=user["id"], **payload)

    request.session["flash"] = [{
        "kind": "success",
        "message": "Chronicle settings saved.",
    }]
    return RedirectResponse(url="/staff/admin", status_code=303)


# Manual ledger action types — porting NYbN's four distinct verbs so the
# ledger row reads cleanly (grant_xp / remove_xp / refund_spend / add_spend)
# instead of a single signed `delta`. The DB still stores them via
# adjust_xp_manual (signed delta + note prefix), but the SIGN is derived
# from the action so staff can't typo a refund as a deduction.
_LEDGER_ACTIONS = {
    "grant_xp":     {"sign":  1, "target": "total", "label": "Grant XP",     "prefix": "Grant"},
    "remove_xp":    {"sign": -1, "target": "total", "label": "Remove XP",    "prefix": "Remove"},
    "refund_spend": {"sign":  1, "target": "spent", "label": "Refund Spend", "prefix": "Refund"},
    "add_spend":    {"sign": -1, "target": "spent", "label": "Add Spend",    "prefix": "Spend"},
}


@router.post("/characters/{character_id}/adjust-xp", response_class=HTMLResponse)
async def adjust_character_xp(
    request: Request,
    character_id: int,
    user: dict = Depends(require_permission("adjust_xp")),
    _: None = Depends(csrf_protect),
):
    """Inline XP adjustment from the staff character detail page. Accepts
    either the new structured form (action + amount) or the legacy
    signed-delta form for back-compat."""
    form  = await request.form()
    note  = (form.get("note") or "").strip()
    action = (form.get("action") or "").strip().lower()

    # Resolve sign + amount from either action+amount (new) or delta (legacy).
    if action in _LEDGER_ACTIONS:
        try:
            amount = abs(int(form.get("amount") or 0))
        except ValueError:
            amount = 0
        spec   = _LEDGER_ACTIONS[action]
        delta  = spec["sign"] * amount
        target = spec["target"]   # grant/remove -> earned total; refund/add-spend -> spent
        if note:
            note = f"{spec['prefix']}: {note}"
        else:
            note = spec["label"]
    else:
        # Legacy path — staff posts a signed delta directly. Used by
        # the admin-level "/admin/adjust-xp" form which still works
        # the old way. Legacy deltas always move the earned total.
        target = "total"
        try:
            delta = int(form.get("delta") or 0)
        except ValueError:
            delta = 0

    flash_kind = "success"
    flash_msg  = None
    if delta == 0:
        flash_kind, flash_msg = "error", "Amount must be a positive integer."
    elif not note:
        flash_kind, flash_msg = "error", "Note is required."
    else:
        try:
            with get_db() as conn:
                adjust_xp_manual(conn, character_id, delta, note, user["id"], target=target)
            flash_msg = f"Adjusted XP by {delta:+d}."
        except ValueError as e:
            flash_kind, flash_msg = "error", str(e)

    request.session["flash"] = [{"kind": flash_kind, "message": flash_msg}]
    return RedirectResponse(url=f"/staff/characters/{character_id}", status_code=303)


@router.post("/admin/adjust-xp", response_class=HTMLResponse)
async def admin_adjust_xp(
    request: Request,
    user: dict = Depends(require_permission("adjust_xp")),
    _: None = Depends(csrf_protect),
):
    form         = await request.form()
    character_id = int(form.get("character_id") or 0)
    delta        = int(form.get("delta") or 0)
    note         = (form.get("note") or "").strip()

    err  = None
    char = None
    if not character_id:
        err = "Character is required."
    elif delta == 0:
        err = "Delta cannot be zero."
    elif not note:
        err = "Note is required."
    else:
        try:
            with get_db() as conn:
                char = adjust_xp_manual(conn, character_id, delta, note, user["id"])
        except ValueError as e:
            err = str(e)

    resp = templates.TemplateResponse(
        request, "staff/admin.html",
        _ctx(request, adjust_err=err, adjust_ok=char, **_admin_ctx_extras()),
    )
    if err:
        _toast(resp, err, "error")
    else:
        name = char["name"] if char else "Character"
        sign = "+" if delta > 0 else ""
        _toast(resp, f"{name}: {sign}{delta} XP — {note[:40]}", "success")
    return resp


# ── Audit Log ─────────────────────────────────────────────────────────────────

# Known audit actions — used to populate the staff filter dropdown
_AUDIT_ACTIONS = [
    "approve_character", "reject_character",
    "approve_claim", "reject_claim",
    "approve_spend", "reject_spend",
    "approve_coterie_request", "reject_coterie_request",
    "approve_coterie_spend", "reject_coterie_spend",
    "adjust_xp", "auto_retire",
]


@router.get("/audit", response_class=HTMLResponse)
async def audit_log(request: Request, user: dict = Depends(require_staff)):
    target_type = request.query_params.get("type") or None
    actor_id    = request.query_params.get("actor") or None
    action      = request.query_params.get("action") or None
    limit       = min(int(request.query_params.get("limit") or 100), 500)

    with get_db() as conn:
        entries = list_audit(
            conn, target_type=target_type, actor_id=actor_id,
            action=action, limit=limit,
        )

    return templates.TemplateResponse(
        request, "staff/audit.html",
        _ctx(request, entries=entries,
             filter_type=target_type, filter_actor=actor_id,
             filter_action=action, known_actions=_AUDIT_ACTIONS),
    )


# ── Hunting Sites ─────────────────────────────────────────────────────────────

_BOROUGHS = ["Manhattan", "Brooklyn", "Queens", "The Bronx",
             "Staten Island", "New Jersey"]


def _parse_predator_dcs(form) -> dict:
    """Extract per-predator-type DCs from a sites edit/create form. The
    form posts keys like `dc_Alleycat`, `dc_Bagger`, etc. Empty values
    are dropped — only populated entries are stored.
    Per Steward direction (2026-05): values must be in 1-5 (0 was
    previously allowed but a DC of 0 has no in-game meaning)."""
    from ..v5_traits import V5_PREDATOR_TYPES
    out: dict[str, int] = {}
    for p in V5_PREDATOR_TYPES:
        raw = form.get(f"dc_{p}")
        if raw is None or raw == "":
            continue
        try:
            n = int(raw)
        except ValueError:
            continue
        if 1 <= n <= 5:
            out[p] = n
    return out


def _all_coteries_for_picker(conn) -> list[dict]:
    """List of coteries shown in the staff site coterie picker.
    Includes any status except 'rejected' / 'disbanded' so a test coterie
    that's still in 'pending' or 'active' can be picked. Previously this
    only showed 'active' which made it impossible to assign a site to a
    coterie that was still going through approval."""
    return conn.execute("""
        SELECT id, name, status
        FROM coteries
        WHERE status NOT IN ('rejected', 'disbanded')
        ORDER BY name COLLATE NOCASE
    """).fetchall()


@router.get("/sites", response_class=HTMLResponse)
async def sites_admin(request: Request, user: dict = Depends(require_staff)):
    """Folded into /staff/admin#sites 2026-05 per Steward direction.
    Keep this URL as a redirect so deep-links + muscle memory still land
    in the right place; remove once analytics show no one hits it."""
    return RedirectResponse(url="/staff/admin#sites", status_code=307)


@router.post("/sites", response_class=HTMLResponse)
async def create_site_route(
    request: Request,
    user: dict = Depends(require_permission("manage_site")),
    _: None = Depends(csrf_protect),
):
    form          = await request.form()
    name          = (form.get("name") or "").strip()
    borough       = (form.get("borough") or "").strip()
    description   = (form.get("description") or "").strip()
    sect_control  = (form.get("sect_control") or "").strip() or None
    coterie_raw   = form.get("coterie_id") or ""
    coterie_id    = int(coterie_raw) if coterie_raw.isdigit() and int(coterie_raw) > 0 else None
    predator_dcs  = _parse_predator_dcs(form)

    err = None
    if not name or not borough:
        err = "Name and area are required."
    elif not predator_dcs:
        # Per Steward direction: DCs are no longer optional — at least
        # one predator type must have a 1-5 DC set. Otherwise the site
        # is useless to players for hunt logging.
        err = "At least one Predator-Type Difficulty (1-5) must be set."
    else:
        try:
            with get_db() as conn:
                create_hunting_site(
                    conn,
                    name=name, borough=borough, description=description,
                    predator_dcs=predator_dcs,
                    coterie_id=coterie_id,
                    sect_control=sect_control,
                )
        except Exception as e:
            err = str(e)

    # Post-merge (2026-05): bounce back to Admin → Hunting Sites tab.
    request.session["flash"] = [{
        "kind": "error" if err else "success",
        "message": err or f"Site '{name}' created.",
    }]
    return RedirectResponse(url="/staff/admin#sites", status_code=303)


@router.post("/sites/{site_id}/toggle", response_class=HTMLResponse)
async def toggle_site_route(
    request: Request,
    site_id: int,
    user: dict = Depends(require_permission("manage_site")),
    _: None = Depends(csrf_protect),
):
    with get_db() as conn:
        site = toggle_hunting_site(conn, site_id, actor_id=user["id"])
        sites = list_hunting_sites(conn, active_only=False)

    resp = templates.TemplateResponse(
        request, "staff/partials/sites_table.html", _ctx(request, sites=sites)
    )
    status = "activated" if site and site["active"] else "deactivated"
    _toast(resp, f"Site {status}.", "success" if site and site["active"] else "info")
    return resp


@router.post("/sites/{site_id}/edit", response_class=HTMLResponse)
async def edit_site_route(
    request: Request,
    site_id: int,
    user: dict = Depends(require_permission("manage_site")),
    _: None = Depends(csrf_protect),
):
    from ..v5_traits import V5_PREDATOR_TYPES
    form          = await request.form()
    name          = (form.get("name") or "").strip()
    borough       = (form.get("borough") or "").strip()
    description   = (form.get("description") or "").strip()
    sect_control  = (form.get("sect_control") or "").strip() or None
    coterie_raw   = form.get("coterie_id") or ""
    coterie_id    = int(coterie_raw) if coterie_raw.isdigit() and int(coterie_raw) > 0 else None
    predator_dcs  = _parse_predator_dcs(form)

    err = None
    if not name or not borough:
        err = "Name and area are required."
    elif not predator_dcs:
        err = "At least one Predator-Type Difficulty (1-5) must be set."
    else:
        try:
            with get_db() as conn:
                update_hunting_site(
                    conn, site_id,
                    name=name, borough=borough, description=description,
                    predator_dcs=predator_dcs,
                    coterie_id=coterie_id,
                    sect_control=sect_control,
                )
        except Exception as e:
            err = str(e)

    with get_db() as conn:
        sites    = list_hunting_sites(conn, active_only=False)
        coteries = _all_coteries_for_picker(conn)

    resp = templates.TemplateResponse(
        request, "staff/partials/sites_table.html",
        _ctx(request, sites=sites, coteries=coteries,
             predator_types=V5_PREDATOR_TYPES, boroughs=_BOROUGHS),
    )
    _toast(resp, err or "Site updated.", "error" if err else "success")
    return resp


# ── Chronicle Map ────────────────────────────────────────────────────────────

@router.get("/map", response_class=HTMLResponse)
async def map_admin(request: Request, user: dict = Depends(require_staff)):
    """Staff map editor — every layer (public + staff-only) with the
    layer-management sidebar + import controls. Sites + coteries are
    passed so the feature-edit form can offer cross-reference dropdowns."""
    from ..db import list_map_layers, list_map_features
    with get_db() as conn:
        layers = list_map_layers(conn, include_staff_only=True, active_only=False)
        layers_with_counts = []
        for layer in layers:
            feats = list_map_features(conn, layer_id=layer["id"], include_hidden=True)
            row = dict(layer)
            row["feature_count"] = len(feats)
            layers_with_counts.append(row)
        sites    = list_hunting_sites(conn, active_only=False)
        coteries = list_coteries(conn, status="active")
    return templates.TemplateResponse(
        request, "staff/map.html",
        _ctx(request, layers=layers_with_counts, sites=sites, coteries=coteries),
    )


@router.get("/map/data.json")
async def map_data_admin(request: Request, user: dict = Depends(require_staff)):
    """Full unfiltered map payload — every layer, every feature
    including hidden ones. The staff Leaflet page reads this."""
    from ..db import list_map_layers, list_map_features
    from fastapi.responses import JSONResponse

    with get_db() as conn:
        layers = list_map_layers(conn, include_staff_only=True, active_only=False)
        payload_layers = []
        for layer in layers:
            features = list_map_features(conn, layer_id=layer["id"], include_hidden=True)
            payload_layers.append({
                "id":          layer["id"],
                "name":        layer["name"],
                "description": layer.get("description"),
                "color":       layer["color"],
                "visibility":  layer["visibility"],
                "active":      bool(layer["active"]),
                "sort_order":  layer["sort_order"],
                "features":    [
                    {
                        "id":           f["id"],
                        "label":        f["label"],
                        "description":  f.get("description"),
                        "tag":          f.get("tag"),
                        "feature_type": f["feature_type"],
                        "geometry":     f.get("geometry"),
                        "is_hidden":    bool(f.get("is_hidden")),
                        "coterie_id":   f.get("coterie_id"),
                        "site_id":      f.get("site_id"),
                    }
                    for f in features
                ],
            })
    return JSONResponse(content={"layers": payload_layers})


@router.post("/map/layers", response_class=HTMLResponse)
async def map_layer_create(
    request: Request,
    user: dict = Depends(require_permission("manage_map")),
    _: None = Depends(csrf_protect),
):
    """Create a new empty layer. Features come later via import."""
    from ..db import create_map_layer
    form        = await request.form()
    name        = (form.get("name") or "").strip()
    description = (form.get("description") or "").strip() or None
    color       = (form.get("color") or "#8B1A1A").strip()
    visibility  = (form.get("visibility") or "public").strip().lower()

    if not name:
        request.session["flash"] = [{"kind": "error", "message": "Layer name is required."}]
        return RedirectResponse(url="/staff/map", status_code=303)

    try:
        with get_db() as conn:
            create_map_layer(conn, name=name, description=description,
                             color=color, visibility=visibility,
                             created_by=user["id"])
    except ValueError as e:
        request.session["flash"] = [{"kind": "error", "message": str(e)}]
        return RedirectResponse(url="/staff/map", status_code=303)

    request.session["flash"] = [{"kind": "success",
                                 "message": f"Layer \"{name}\" created."}]
    return RedirectResponse(url="/staff/map", status_code=303)


@router.post("/map/layers/{layer_id}/edit", response_class=HTMLResponse)
async def map_layer_edit(
    request: Request,
    layer_id: int,
    user: dict = Depends(require_permission("manage_map")),
    _: None = Depends(csrf_protect),
):
    """Update a layer's metadata. Only submitted form fields overwrite."""
    from ..db import update_map_layer
    form    = await request.form()
    updates: dict = {}
    for key in ("name", "description", "color", "visibility"):
        val = form.get(key)
        if val is not None:
            updates[key] = val.strip() or None
    if "active" in form:
        updates["active"] = 1 if form.get("active") == "1" else 0

    try:
        with get_db() as conn:
            update_map_layer(conn, layer_id, actor_id=user["id"], **updates)
    except ValueError as e:
        request.session["flash"] = [{"kind": "error", "message": str(e)}]
        return RedirectResponse(url="/staff/map", status_code=303)

    request.session["flash"] = [{"kind": "success", "message": "Layer updated."}]
    return RedirectResponse(url="/staff/map", status_code=303)


@router.post("/map/layers/{layer_id}/delete", response_class=HTMLResponse)
async def map_layer_delete(
    request: Request,
    layer_id: int,
    user: dict = Depends(require_permission("manage_map")),
    _: None = Depends(csrf_protect),
):
    """Hard-delete a layer and every feature inside it."""
    from ..db import delete_map_layer, get_map_layer
    with get_db() as conn:
        layer = get_map_layer(conn, layer_id)
        if not layer:
            raise HTTPException(status_code=404)
        delete_map_layer(conn, layer_id, actor_id=user["id"])
    request.session["flash"] = [{"kind": "info",
                                 "message": f"Layer \"{layer['name']}\" deleted."}]
    return RedirectResponse(url="/staff/map", status_code=303)


_MAP_IMPORT_MAX_BYTES = 5 * 1024 * 1024  # 5 MB


async def _read_map_payload(form) -> tuple[bytes, str, str | None]:
    """Shared file/paste reader. Returns (bytes, filename, error)."""
    upload = form.get("file")
    pasted = form.get("payload") or ""
    if upload is not None and hasattr(upload, "filename") and upload.filename:
        body = await upload.read()
        return body, upload.filename.lower(), None
    if pasted.strip():
        return pasted.encode("utf-8"), "", None
    return b"", "", "Choose a file or paste GeoJSON/KML to import."


def _run_map_import(conn, layer_id: int, body_bytes: bytes, filename: str,
                    label_field: str | None = None,
                    tag_field: str | None = None,
                    description_field: str | None = None) -> dict:
    """Format-detect (KML vs GeoJSON) and run the matching importer.
    Raises ValueError on bad payload; returns the same {inserted,
    skipped, errors} dict as the underlying importers."""
    import json as _json
    from ..db import import_geojson, import_kml

    if len(body_bytes) > _MAP_IMPORT_MAX_BYTES:
        raise ValueError("Import too large (max 5 MB).")

    text = body_bytes.decode("utf-8", errors="replace")
    is_kml = filename.endswith(".kml") or text.lstrip().startswith("<")
    if is_kml:
        return import_kml(conn, layer_id, text)

    try:
        payload = _json.loads(text)
    except _json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON: {e}") from e
    return import_geojson(
        conn, layer_id, payload,
        label_field=label_field, tag_field=tag_field,
        description_field=description_field,
    )


@router.post("/map/quick-import", response_class=HTMLResponse)
async def map_quick_import(
    request: Request,
    user: dict = Depends(require_permission("manage_map")),
    _: None = Depends(csrf_protect),
):
    """One-step create-layer-and-import. Staff names a new layer and
    uploads a GeoJSON/KML in the same form — typically how the empty-
    state "Import Your First Layer" flow is used."""
    from ..db import create_map_layer, delete_map_layer

    form        = await request.form()
    name        = (form.get("name") or "").strip()
    description = (form.get("description") or "").strip() or None
    color       = (form.get("color") or "#8B1A1A").strip()
    visibility  = (form.get("visibility") or "public").strip().lower()
    label_field       = (form.get("label_field") or "").strip() or None
    tag_field         = (form.get("tag_field") or "").strip() or None
    description_field = (form.get("description_field") or "").strip() or None

    if not name:
        request.session["flash"] = [{"kind": "error",
            "message": "Layer name is required."}]
        return RedirectResponse(url="/staff/map", status_code=303)

    body_bytes, filename, err = await _read_map_payload(form)
    if err:
        request.session["flash"] = [{"kind": "error", "message": err}]
        return RedirectResponse(url="/staff/map", status_code=303)

    # Create the layer first so even a parse failure leaves the row
    # behind for the user to retry against. If they want to nuke it
    # they hit Delete on the sidebar.
    try:
        with get_db() as conn:
            layer = create_map_layer(conn, name=name, description=description,
                                     color=color, visibility=visibility,
                                     created_by=user["id"])
    except ValueError as e:
        request.session["flash"] = [{"kind": "error", "message": str(e)}]
        return RedirectResponse(url="/staff/map", status_code=303)

    try:
        with get_db() as conn:
            result = _run_map_import(
                conn, layer["id"], body_bytes, filename,
                label_field=label_field, tag_field=tag_field,
                description_field=description_field,
            )
    except ValueError as e:
        # Roll back the empty layer if the import payload was bad — no
        # point in leaving a useless shell around.
        with get_db() as conn:
            delete_map_layer(conn, layer["id"])
        request.session["flash"] = [{"kind": "error", "message": str(e)}]
        return RedirectResponse(url="/staff/map", status_code=303)

    msg = f"Created \"{name}\" with {result['inserted']} feature(s)."
    if result["skipped"]:
        msg += f" {result['skipped']} skipped."
    flash: list[dict] = [{"kind": "success" if result["inserted"] else "info",
                          "message": msg}]
    for err in result.get("errors", [])[:5]:
        flash.append({"kind": "error", "message": err})
    request.session["flash"] = flash
    return RedirectResponse(url="/staff/map", status_code=303)


@router.post("/map/layers/{layer_id}/import", response_class=HTMLResponse)
async def map_layer_import(
    request: Request,
    layer_id: int,
    user: dict = Depends(require_permission("manage_map")),
    _: None = Depends(csrf_protect),
):
    """Import GeoJSON or KML into an existing layer. Accepts either an
    uploaded file (form field 'file') or pasted text (form field
    'payload'). Format auto-detects from filename extension first,
    then content sniffing as a fallback."""
    from ..db import get_map_layer

    with get_db() as conn:
        layer = get_map_layer(conn, layer_id)
    if not layer:
        raise HTTPException(status_code=404)

    form = await request.form()
    label_field       = (form.get("label_field") or "").strip() or None
    tag_field         = (form.get("tag_field") or "").strip() or None
    description_field = (form.get("description_field") or "").strip() or None

    body_bytes, filename, err = await _read_map_payload(form)
    if err:
        request.session["flash"] = [{"kind": "error", "message": err}]
        return RedirectResponse(url="/staff/map", status_code=303)

    try:
        with get_db() as conn:
            result = _run_map_import(
                conn, layer_id, body_bytes, filename,
                label_field=label_field, tag_field=tag_field,
                description_field=description_field,
            )
    except ValueError as e:
        request.session["flash"] = [{"kind": "error", "message": str(e)}]
        return RedirectResponse(url="/staff/map", status_code=303)

    msg = f"Imported {result['inserted']} feature(s) into \"{layer['name']}\"."
    if result["skipped"]:
        msg += f" {result['skipped']} skipped."
    flash: list[dict] = [{"kind": "success" if result["inserted"] else "info",
                          "message": msg}]
    for err in result.get("errors", [])[:5]:
        flash.append({"kind": "error", "message": err})
    request.session["flash"] = flash
    return RedirectResponse(url="/staff/map", status_code=303)


@router.post("/map/features", response_class=HTMLResponse)
async def map_feature_create_route(
    request: Request,
    user: dict = Depends(require_permission("manage_map")),
    _: None = Depends(csrf_protect),
):
    """Drop a single Point feature on a layer — used by the staff
    'click on the map to pin' workflow. Polygons + lines still come
    in via GeoJSON/KML import (too much UI surface for hand-drawing)."""
    from ..db import create_map_feature, get_map_layer
    form = await request.form()
    layer_raw = (form.get("layer_id") or "").strip()
    if not layer_raw.isdigit():
        request.session["flash"] = [{"kind": "error", "message": "Layer is required."}]
        return RedirectResponse(url="/staff/map", status_code=303)
    layer_id = int(layer_raw)
    label = (form.get("label") or "").strip()[:120]
    tag   = (form.get("tag") or "").strip()[:60] or None
    description = (form.get("description") or "").strip() or None
    try:
        lat = float(form.get("lat") or "")
        lng = float(form.get("lng") or "")
    except (TypeError, ValueError):
        request.session["flash"] = [{"kind": "error",
            "message": "Latitude and longitude are required."}]
        return RedirectResponse(url="/staff/map", status_code=303)
    is_hidden = form.get("is_hidden") == "1"

    with get_db() as conn:
        if get_map_layer(conn, layer_id) is None:
            raise HTTPException(status_code=404)
        # GeoJSON Point is [lng, lat], not [lat, lng].
        create_map_feature(
            conn, layer_id=layer_id, label=label, tag=tag,
            description=description, feature_type="point",
            geometry={"type": "Point", "coordinates": [lng, lat]},
            is_hidden=is_hidden, actor_id=user["id"],
        )
    request.session["flash"] = [{"kind": "success",
                                 "message": f"Pin \"{label or 'unnamed'}\" added."}]
    return RedirectResponse(url="/staff/map", status_code=303)


@router.post("/map/features/{feature_id}/edit", response_class=HTMLResponse)
async def map_feature_edit(
    request: Request,
    feature_id: int,
    user: dict = Depends(require_permission("manage_map")),
    _: None = Depends(csrf_protect),
):
    """Quick edit for an individual feature — label, tag, description,
    hidden flag, and optional cross-references to a coterie or hunting
    site. Geometry changes happen via re-import."""
    from ..db import update_map_feature, get_map_feature
    form = await request.form()
    updates = {}
    for key in ("label", "description", "tag"):
        val = form.get(key)
        if val is not None:
            updates[key] = val.strip() or None
    if "is_hidden" in form:
        updates["is_hidden"] = form.get("is_hidden") == "1"
    # Cross-refs — empty string means "unlink".
    for key in ("site_id", "coterie_id"):
        raw = form.get(key)
        if raw is not None:
            raw = raw.strip()
            updates[key] = int(raw) if raw.isdigit() and int(raw) > 0 else None
    with get_db() as conn:
        feat = get_map_feature(conn, feature_id)
        if not feat:
            raise HTTPException(status_code=404)
        update_map_feature(conn, feature_id, actor_id=user["id"], **updates)
    request.session["flash"] = [{"kind": "success", "message": "Feature updated."}]
    return RedirectResponse(url="/staff/map", status_code=303)


@router.post("/map/features/{feature_id}/delete", response_class=HTMLResponse)
async def map_feature_delete(
    request: Request,
    feature_id: int,
    user: dict = Depends(require_permission("manage_map")),
    _: None = Depends(csrf_protect),
):
    from ..db import delete_map_feature, get_map_feature
    with get_db() as conn:
        feat = get_map_feature(conn, feature_id)
        if not feat:
            raise HTTPException(status_code=404)
        delete_map_feature(conn, feature_id, actor_id=user["id"])
    request.session["flash"] = [{"kind": "info", "message": "Feature deleted."}]
    return RedirectResponse(url="/staff/map", status_code=303)
