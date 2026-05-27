"""player.py — Player-facing pages."""
import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from ..db import (
    add_coterie_member,
    coterie_domain_cost,
    create_claim,
    create_coterie_request,
    create_coterie_spend,
    create_spend,
    get_active_period,
    get_character,
    get_character_for_player,
    get_coterie,
    get_coterie_for_character,
    get_db,
    get_ledger,
    list_characters,
    list_claims_for_character,
    list_coterie_members,
    list_coterie_spends,
    list_criteria,
    list_player_characters,
    list_spends_for_character,
)
from ..deps import csrf_protect, require_auth
from ..main import _ctx
from ..xp_rules import (
    HUMANITY_CONDITIONS,
    RULES,
    SPEND_CATEGORIES,
    validate_humanity_conditions,
    validate_spend,
)

router = APIRouter(tags=["player"])
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _toast(response: Response, message: str, kind: str = "success") -> None:
    response.headers["X-Enoch-Toast"]      = message
    response.headers["X-Enoch-Toast-Kind"] = kind


def _player_criteria(criteria: list[dict]) -> list[dict]:
    """Filter to only categories a player can self-submit."""
    return [c for c in criteria if c["category"] in ("base", "player")]


def _already_claimed(claims: list[dict], period_id: int) -> bool:
    return any(
        c["play_period_id"] == period_id and c["status"] in ("pending", "approved")
        for c in claims
    )


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Landing page — redirects to character list if logged in."""
    if request.session.get("user"):
        return RedirectResponse(url="/characters", status_code=303)
    return templates.TemplateResponse("player/index.html", _ctx(request))


@router.get("/characters", response_class=HTMLResponse)
async def character_list(
    request: Request,
    user: dict = Depends(require_auth),
):
    with get_db() as conn:
        characters = list_player_characters(conn, user["id"])
    return templates.TemplateResponse(
        "player/characters.html",
        _ctx(request, characters=characters),
    )


@router.get("/characters/{character_id}", response_class=HTMLResponse)
async def character_detail(
    request: Request,
    character_id: int,
    tab: str = "overview",
    user: dict = Depends(require_auth),
):
    with get_db() as conn:
        char = get_character_for_player(conn, character_id, user["id"])
        if not char:
            raise HTTPException(status_code=404)

        active_period   = get_active_period(conn)
        all_criteria    = list_criteria(conn, active_only=True)
        claims          = list_claims_for_character(conn, character_id)
        spends          = list_spends_for_character(conn, character_id)
        ledger          = get_ledger(conn, character_id, limit=50)
        coterie         = get_coterie_for_character(conn, character_id)

    p_criteria     = _player_criteria(all_criteria)
    period_claimed = (
        active_period is not None
        and _already_claimed(claims, active_period["id"])
    )

    return templates.TemplateResponse(
        "player/character.html",
        _ctx(
            request,
            char=char,
            active_period=active_period,
            player_criteria=p_criteria,
            already_claimed=period_claimed,
            claims=claims[:10],
            spends=spends[:10],
            ledger=ledger,
            coterie=coterie,
            default_tab=tab,
            spend_categories=SPEND_CATEGORIES,
            spend_rules_json=json.dumps(RULES),
            humanity_conditions=HUMANITY_CONDITIONS,
        ),
    )


@router.post("/characters/{character_id}/claim", response_class=HTMLResponse)
async def submit_claim(
    request: Request,
    character_id: int,
    user: dict = Depends(require_auth),
    _: None = Depends(csrf_protect),
):
    form         = await request.form()
    criteria_ids = [int(x) for x in form.getlist("criteria_ids") if x]
    rp_links     = [x.strip() for x in form.getlist("rp_links") if x.strip()]
    path         = form.get("path", "none")
    helper_note  = (form.get("helper_note") or "").strip() or None

    errors: list[str] = []

    with get_db() as conn:
        char = get_character_for_player(conn, character_id, user["id"])
        if not char:
            raise HTTPException(status_code=404)

        active_period   = get_active_period(conn)
        all_criteria    = list_criteria(conn, active_only=True)
        criteria_map    = {c["id"]: c for c in _player_criteria(all_criteria)}
        claims          = list_claims_for_character(conn, character_id)

        # ── Validation ───────────────────────────────────────────────────────
        if not char.get("is_approved"):
            errors.append("Your character must be approved before claiming XP.")

        if not active_period:
            errors.append("There is no active XP window right now.")
        elif _already_claimed(claims, active_period["id"]):
            errors.append("You have already submitted a claim for this period.")

        if not criteria_ids:
            errors.append("Select at least one criterion.")
        else:
            bad = [cid for cid in criteria_ids if cid not in criteria_map]
            if bad:
                errors.append("One or more selected criteria are invalid.")

        needs_links = any(
            criteria_map[cid]["requires_rp_links"]
            for cid in criteria_ids
            if cid in criteria_map
        )
        if needs_links and not rp_links:
            errors.append("At least one RP link is required for the selected criteria.")

        if path == "helper" and not helper_note:
            errors.append("Helper Activity requires a note explaining your contribution.")

        if errors:
            resp = templates.TemplateResponse(
                "player/partials/claim_section.html",
                _ctx(
                    request,
                    char=char,
                    active_period=active_period,
                    player_criteria=list(criteria_map.values()),
                    already_claimed=False,
                    claim_errors=errors,
                    claim_form={"criteria_ids": criteria_ids, "rp_links": rp_links, "path": path},
                ),
            )
            _toast(resp, "Please fix the errors below.", "error")
            return resp

        # ── Snapshot claimed criteria ─────────────────────────────────────────
        claimed_criteria = [
            {
                "criteria_id":           cid,
                "label":                 criteria_map[cid]["label"],
                "xp_value_at_submission": criteria_map[cid]["xp_value"],
            }
            for cid in criteria_ids
            if cid in criteria_map
        ]

        create_claim(
            conn,
            character_id=character_id,
            play_period_id=active_period["id"],
            claimed_criteria=claimed_criteria,
            rp_links=rp_links,
            path=path,
            helper_note=helper_note,
        )

        # Refresh after insert
        claims     = list_claims_for_character(conn, character_id)
        p_criteria = list(criteria_map.values())

    resp = templates.TemplateResponse(
        "player/partials/claim_section.html",
        _ctx(
            request,
            char=char,
            active_period=active_period,
            player_criteria=p_criteria,
            already_claimed=True,
            claim_success=True,
        ),
    )
    _toast(resp, "Claim submitted — pending staff review.")
    return resp


@router.post("/characters/{character_id}/spend", response_class=HTMLResponse)
async def submit_spend(
    request: Request,
    character_id: int,
    user: dict = Depends(require_auth),
    _: None = Depends(csrf_protect),
):
    form         = await request.form()
    category     = (form.get("category") or "").strip()
    trait_name   = (form.get("trait_name") or "").strip()
    current_dots = int(form.get("current_dots") or 0)
    new_dots     = int(form.get("new_dots") or 1)
    note         = (form.get("note") or "").strip() or None
    hc_checked   = [form.get(f"hc_{i}") == "on" for i in range(len(HUMANITY_CONDITIONS))]

    errors: list[str] = []
    verified_cost = 0

    def _spend_ctx(extra=None):
        return _ctx(
            request,
            char=char,
            spend_categories=SPEND_CATEGORIES,
            spend_rules_json=json.dumps(RULES),
            humanity_conditions=HUMANITY_CONDITIONS,
            spend_errors=errors,
            spend_form={
                "category": category, "trait_name": trait_name,
                "current_dots": current_dots, "new_dots": new_dots,
                "note": note,
            },
            **(extra or {}),
        )

    with get_db() as conn:
        char = get_character_for_player(conn, character_id, user["id"])
        if not char:
            raise HTTPException(status_code=404)

        if not char.get("is_approved"):
            errors.append("Your character must be approved before spending XP.")
        if not trait_name:
            errors.append("Trait name is required.")
        if not category:
            errors.append("Category is required.")

        if category and not errors:
            verified_cost, spend_errors = validate_spend(category, current_dots, new_dots, char)
            errors.extend(spend_errors)

        if category == "Humanity" and not errors:
            ok, hc_err = validate_humanity_conditions(hc_checked)
            if not ok:
                errors.append(hc_err)

        if errors:
            resp = templates.TemplateResponse(
                "player/partials/spend_form.html", _spend_ctx()
            )
            _toast(resp, "Please fix the errors below.", "error")
            return resp

        create_spend(
            conn,
            character_id=character_id,
            category=category,
            trait_name=trait_name,
            current_dots=current_dots,
            new_dots=new_dots,
            verified_cost=verified_cost,
            is_ingrained=(category == "Ingrained Discipline"),
            humanity_conditions=(
                [HUMANITY_CONDITIONS[i] for i, v in enumerate(hc_checked) if v]
                if category == "Humanity" else None
            ),
            note=note,
        )

        # Refresh character for updated XP display
        char = get_character_for_player(conn, character_id, user["id"])

    resp = templates.TemplateResponse(
        "player/partials/spend_form.html",
        _spend_ctx({"spend_success": f"Request submitted — {trait_name} ({category}, {verified_cost} XP)."}),
    )
    _toast(resp, "Spend request submitted — pending staff review.")
    return resp


# ── Coteries ──────────────────────────────────────────────────────────────────

@router.get("/coteries", response_class=HTMLResponse)
async def coteries_list(request: Request, user: dict = Depends(require_auth)):
    """Show the player's coterie (if any), otherwise offer formation request."""
    with get_db() as conn:
        chars   = list_player_characters(conn, user["id"])
        coterie = None
        members = []
        spends  = []
        # Find the first active, approved character that is in a coterie
        for c in chars:
            if c["status"] == "active" and c["is_approved"]:
                coterie = get_coterie_for_character(conn, c["id"])
                if coterie:
                    members = list_coterie_members(conn, coterie["id"])
                    spends  = list_coterie_spends(conn, coterie["id"])
                    break

        # Characters eligible to include in a formation request
        eligible = [
            c for c in chars
            if c["status"] == "active" and c["is_approved"]
        ]
        all_active = list_characters(conn, status="active")
        roster = [c for c in all_active if c["is_approved"]]

    return templates.TemplateResponse(
        "player/coteries.html",
        _ctx(
            request,
            coterie=coterie,
            members=members,
            spends=spends,
            eligible_chars=eligible,
            roster=roster,
        ),
    )


@router.post("/coteries/request", response_class=HTMLResponse)
async def submit_coterie_request(
    request: Request,
    user: dict = Depends(require_auth),
    _: None = Depends(csrf_protect),
):
    form          = await request.form()
    proposed_name = (form.get("proposed_name") or "").strip()
    note          = (form.get("note") or "").strip() or None
    # member_ids: character IDs the player wants in the coterie (JSON array from hidden input)
    raw_ids       = (form.get("member_ids") or "").strip()

    errors: list[str] = []

    if not proposed_name:
        errors.append("A coterie name is required.")

    member_ids: list[int] = []
    if raw_ids:
        try:
            import json as _json
            member_ids = [int(x) for x in _json.loads(raw_ids)]
        except Exception:
            errors.append("Invalid member list — please try again.")

    if errors:
        resp = templates.TemplateResponse(
            "player/partials/coterie_request_form.html",
            _ctx(request, request_errors=errors, form={"proposed_name": proposed_name, "note": note}),
        )
        _toast(resp, "Please fix the errors below.", "error")
        return resp

    with get_db() as conn:
        create_coterie_request(
            conn,
            requested_by=user["id"],
            proposed_name=proposed_name,
            member_ids=member_ids,
            note=note,
        )

    resp = templates.TemplateResponse(
        "player/partials/coterie_request_form.html",
        _ctx(request, request_success=True),
    )
    _toast(resp, "Formation request submitted — staff will review shortly.")
    return resp


@router.get("/coteries/{coterie_id}", response_class=HTMLResponse)
async def coterie_detail(
    request: Request,
    coterie_id: int,
    user: dict = Depends(require_auth),
):
    with get_db() as conn:
        coterie = get_coterie(conn, coterie_id)
        if not coterie:
            raise HTTPException(status_code=404)

        members = list_coterie_members(conn, coterie_id)
        spends  = list_coterie_spends(conn, coterie_id)

        # Verify caller is a member
        player_chars = list_player_characters(conn, user["id"])
        member_char_ids = {m["character_id"] for m in members}
        is_member = any(c["id"] in member_char_ids for c in player_chars)
        if not is_member:
            raise HTTPException(status_code=403, detail="Not a member of this coterie")

    # Compute upgrade costs for display
    n = len(members)
    upgrade_costs = {}
    if n > 0:
        for trait, current_val in [("chasse", coterie["chasse"]),
                                    ("lien",   coterie["lien"]),
                                    ("portillon", coterie["portillon"])]:
            next_dot = current_val + 1
            if next_dot <= 5:
                total, per = coterie_domain_cost(next_dot, n)
                upgrade_costs[trait] = {"next_dot": next_dot, "total": total, "per_member": per}

    return templates.TemplateResponse(
        "player/coterie_detail.html",
        _ctx(
            request,
            coterie=coterie,
            members=members,
            spends=spends,
            upgrade_costs=upgrade_costs,
        ),
    )


@router.post("/coteries/{coterie_id}/spend", response_class=HTMLResponse)
async def submit_coterie_spend(
    request: Request,
    coterie_id: int,
    user: dict = Depends(require_auth),
    _: None = Depends(csrf_protect),
):
    form       = await request.form()
    trait_name = (form.get("trait_name") or "").strip()

    with get_db() as conn:
        coterie = get_coterie(conn, coterie_id)
        if not coterie:
            raise HTTPException(status_code=404)

        members = list_coterie_members(conn, coterie_id)
        member_char_ids = {m["character_id"] for m in members}
        player_chars = list_player_characters(conn, user["id"])

        if not any(c["id"] in member_char_ids for c in player_chars):
            raise HTTPException(status_code=403)

        errors: list[str] = []
        if trait_name not in ("chasse", "lien", "portillon"):
            errors.append("Invalid domain trait.")

        current_val = coterie.get(trait_name, 0) if not errors else 0
        new_dots    = current_val + 1 if not errors else 0

        if not errors and new_dots > 5:
            errors.append(f"{trait_name.title()} is already at maximum (5).")

        pending_spends = list_coterie_spends(conn, coterie_id)
        already_pending = any(
            s["status"] == "pending" and s["trait_name"] == trait_name
            for s in pending_spends
        )
        if not errors and already_pending:
            errors.append(f"There is already a pending {trait_name} upgrade request.")

        if not errors:
            n = len(members)
            _, per_cost = coterie_domain_cost(new_dots, n)
            # Check all members have enough XP
            short = []
            for m in members:
                char = get_character(conn, m["character_id"])
                if char and char["xp_available"] < per_cost:
                    short.append(f"{char['name']} ({char['xp_available']} XP available)")
            if short:
                errors.append(
                    f"Insufficient XP — {per_cost} XP needed per member. "
                    "Short: " + ", ".join(short)
                )

        if errors:
            spends = list_coterie_spends(conn, coterie_id)
            n = len(members)
            upgrade_costs = {}
            for t, cv in [("chasse", coterie["chasse"]),
                           ("lien",   coterie["lien"]),
                           ("portillon", coterie["portillon"])]:
                nd = cv + 1
                if nd <= 5:
                    total, per = coterie_domain_cost(nd, n)
                    upgrade_costs[t] = {"next_dot": nd, "total": total, "per_member": per}
            resp = templates.TemplateResponse(
                "player/coterie_detail.html",
                _ctx(request, coterie=coterie, members=members,
                     spends=spends, upgrade_costs=upgrade_costs,
                     spend_errors=errors),
            )
            _toast(resp, "Please fix the errors below.", "error")
            return resp

        contributions = {str(m["character_id"]): per_cost for m in members}
        create_coterie_spend(
            conn,
            coterie_id=coterie_id,
            trait_name=trait_name,
            current_dots=current_val,
            new_dots=new_dots,
            contributions=contributions,
        )
        spends = list_coterie_spends(conn, coterie_id)

    resp = templates.TemplateResponse(
        "player/coterie_detail.html",
        _ctx(
            request, coterie=coterie, members=members, spends=spends,
            upgrade_costs={},
            spend_success=f"{trait_name.title()} upgrade submitted for staff review.",
        ),
    )
    _toast(resp, f"{trait_name.title()} upgrade request submitted.")
    return resp
