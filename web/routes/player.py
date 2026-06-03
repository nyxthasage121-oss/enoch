"""player.py — Player-facing pages."""
import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from ..forms import form_int
from ..db import (
    coterie_effective_rating,
    create_character,
    create_claim,
    create_coterie_request,
    create_coterie_single_funder_spend,
    create_spend,
    delete_character,
    set_character_background,
    blank_character_background,
    list_character_backgrounds,
    create_project,
    list_projects_for_character,
    timeskip_rolls_remaining,
    get_active_period,
    get_character,
    get_claim,
    get_coterie_spend,
    get_character_for_player,
    get_coterie,
    get_coterie_for_character,
    get_db,
    get_ledger,
    get_pending_spend_total,
    list_characters,
    list_claims_for_character,
    list_coterie_members,
    list_coterie_spends,
    list_criteria,
    list_hunting_sites,
    list_player_characters,
    list_recent_closed_periods,
    list_spends_for_character,
    list_upcoming_periods,
    sweep_retirements,
    update_character,
    validate_coterie_advance,
    validate_coterie_named_trait,
)
from ..xp_rules import calculate_cost as _calculate_cost
from ..config import settings
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

def _header_safe(s: str) -> str:
    """HTTP headers must be latin-1; replace common Unicode punctuation."""
    return (s.replace('—', '--').replace('–', '-')
             .replace(''', "'").replace(''', "'")
             .replace('"', '"').replace('"', '"')
             .encode('latin-1', errors='replace').decode('latin-1'))


def _toast(response: Response, message: str, kind: str = "success") -> None:
    response.headers["X-Enoch-Toast"]      = _header_safe(message)
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
    return templates.TemplateResponse(request, "player/index.html", _ctx(request))


@router.get("/characters", response_class=HTMLResponse)
async def character_list(
    request: Request,
    user: dict = Depends(require_auth),
):
    with get_db() as conn:
        sweep_retirements(conn)
        all_chars        = list_player_characters(conn, user["id"])
        active_period    = get_active_period(conn)
        upcoming_periods = list_upcoming_periods(conn, limit=2)
        recent_periods   = list_recent_closed_periods(conn, limit=1)
    drafts     = [c for c in all_chars if c.get("is_draft")]
    characters = [c for c in all_chars if not c.get("is_draft")]
    return templates.TemplateResponse(
        request, "player/characters.html",
        _ctx(
            request,
            characters=characters,
            drafts=drafts,
            active_period=active_period,
            upcoming_periods=upcoming_periods,
            recent_periods=recent_periods,
        ),
    )


# ── Hunting sites — player-facing directory ───────────────────────────────────

_BOROUGHS_PLAYER = ["Manhattan", "Brooklyn", "Queens", "The Bronx",
                    "Staten Island", "New Jersey"]


@router.get("/hunting-sites", response_class=HTMLResponse)
async def hunting_sites_directory(
    request: Request,
    borough: str = "",
    min_quality: int = 0,
    character_id: int = 0,
    user: dict = Depends(require_auth),
):
    """List active hunting sites with simple filters. The currently-
    selected character's predator type (if any) decides which DC gets
    highlighted on each site card."""
    from ..db import get_coterie
    with get_db() as conn:
        sites = list_hunting_sites(conn, active_only=True)
        characters = list_player_characters(conn, user["id"])

        # Pick which character drives the "your DC" highlight. Default
        # to the player's only active char, else whichever they passed.
        active_chars = [c for c in characters if c["is_approved"]
                                              and c["status"] == "active"]
        selected = None
        if character_id:
            selected = next((c for c in active_chars
                             if c["id"] == character_id), None)
        elif len(active_chars) == 1:
            selected = active_chars[0]

        # Apply filters
        if borough:
            sites = [s for s in sites if s["borough"] == borough]
        if min_quality:
            sites = [s for s in sites if (s["blood_quality"] or 0) >= min_quality]

        # Enrich with the owning coterie name (one lookup per unique id)
        coterie_names: dict[int, str] = {}
        for s in sites:
            if s["coterie_id"] and s["coterie_id"] not in coterie_names:
                co = get_coterie(conn, s["coterie_id"])
                coterie_names[s["coterie_id"]] = co["name"] if co else ""

        # Chasse only eases feeding at the viewing character's OWN coterie's
        # sites — used to gate the reduced-DC display per card.
        _vc = get_coterie_for_character(conn, selected["id"]) if selected else None
        selected_coterie_id = _vc["id"] if _vc else None

    return templates.TemplateResponse(
        request, "player/hunting_sites.html",
        _ctx(request,
             sites=sites,
             coterie_names=coterie_names,
             active_chars=active_chars,
             selected_char=selected,
             selected_coterie_id=selected_coterie_id,
             boroughs=_BOROUGHS_PLAYER,
             filter_borough=borough,
             filter_min_quality=min_quality),
    )


@router.get("/hunting-sites/{site_id}", response_class=HTMLResponse)
async def hunting_site_detail(
    request: Request,
    site_id: int,
    character_id: int = 0,
    user: dict = Depends(require_auth),
):
    from ..db import get_hunting_site, get_coterie, list_hunts_for_site
    with get_db() as conn:
        site = get_hunting_site(conn, site_id)
        if not site or not site["active"]:
            raise HTTPException(status_code=404)
        characters = list_player_characters(conn, user["id"])
        active_chars = [c for c in characters if c["is_approved"]
                                              and c["status"] == "active"]
        selected = None
        if character_id:
            selected = next((c for c in active_chars
                             if c["id"] == character_id), None)
        elif len(active_chars) == 1:
            selected = active_chars[0]
        owner = get_coterie(conn, site["coterie_id"]) if site["coterie_id"] else None
        hunts = list_hunts_for_site(conn, site_id, limit=10)
        # Chasse only eases feeding for the OWNING coterie's own members —
        # outsiders hunting here get the unreduced (base) DCs.
        viewer_owns_site = False
        if selected and site["coterie_id"]:
            _vc = get_coterie_for_character(conn, selected["id"])
            viewer_owns_site = bool(_vc and _vc["id"] == site["coterie_id"])

    return templates.TemplateResponse(
        request, "player/hunting_site_detail.html",
        _ctx(request, site=site, owner=owner, hunts=hunts,
             active_chars=active_chars, selected_char=selected,
             viewer_owns_site=viewer_owns_site),
    )


@router.post("/hunting-sites/{site_id}/hunt", response_class=HTMLResponse)
async def hunting_site_log_hunt(
    request: Request,
    site_id: int,
    user: dict = Depends(require_auth),
    _: None = Depends(csrf_protect),
):
    """Player logs a hunt at this site for one of their characters."""
    from ..db import get_hunting_site, create_hunt_log, HUNT_OUTCOMES
    form = await request.form()
    char_id = form_int(form.get("character_id"))
    outcome = (form.get("outcome") or "").strip()
    note    = (form.get("note") or "").strip()

    err = None
    if outcome not in HUNT_OUTCOMES:
        err = "Pick an outcome."
    elif char_id <= 0:
        err = "Pick a character."

    if not err:
        with get_db() as conn:
            char = get_character_for_player(conn, char_id, user["id"])
            site = get_hunting_site(conn, site_id)
            if not char or not site or not site["active"]:
                raise HTTPException(status_code=404)
            create_hunt_log(conn, site_id, char_id, outcome, note, source="web")

    request.session["flash"] = [{
        "kind": "error" if err else "success",
        "message": err or "Hunt logged.",
    }]
    return RedirectResponse(
        url=f"/hunting-sites/{site_id}?character_id={char_id}",
        status_code=303,
    )


# V5 sheet constants — single source of truth in web.v5_traits
from ..v5_traits import (
    V5_ATTRIBUTES   as _V5_ATTRIBUTES,
    V5_SKILLS       as _V5_SKILLS,
    V5_DISCIPLINES  as _V5_DISCIPLINES,
    V5_CLAN_INFO    as _V5_CLAN_INFO,
    V5_PREDATOR_INFO as _V5_PREDATOR_INFO,
    V5_SKILL_SPREADS as _V5_SKILL_SPREADS,
    V5_DISCIPLINE_SPREADS as _V5_DISCIPLINE_SPREADS,
    PREDATOR_FREE_DISCIPLINE_DOTS as _PREDATOR_FREE_DISCIPLINE_DOTS,
    CLAN_DISCIPLINES as _CLAN_DISCIPLINES,
    SHEET_TRAIT_KEYS as _SHEET_TRAIT_KEYS,
    SHEET_LIMITS    as _SHEET_LIMITS,
    validate_chargen_raw as _validate_chargen_raw,
)


_CLANS = [
    ("banu-haqim",   "Banu Haqim"),
    ("brujah",       "Brujah"),
    ("gangrel",      "Gangrel"),
    ("hecata",       "Hecata"),
    ("lasombra",     "Lasombra"),
    ("malkavian",    "Malkavian"),
    ("ministry",     "The Ministry"),
    ("nosferatu",    "Nosferatu"),
    ("ravnos",       "Ravnos"),
    ("salubri",      "Salubri"),
    ("toreador",     "Toreador"),
    ("tremere",      "Tremere"),
    ("tzimisce",     "Tzimisce"),
    ("ventrue",      "Ventrue"),
    # ── Outliers (no in-clan disciplines) ──
    ("caitiff",      "Caitiff"),
    ("thin-blood",   "Thin-Blood"),
]

from ..v5_traits import (
    V5_PREDATOR_TYPES as _PREDATOR_TYPES,
    V5_RESTRICTED_PREDATOR_TYPES as _RESTRICTED_PREDATOR_TYPES,
    V5_CLAN_BANE_FLAWS as _V5_CLAN_BANE_FLAWS,
    V5_CLAN_BANE_FLAW_POOLS as _V5_CLAN_BANE_FLAW_POOLS,
    V5_CLAN_BANE_VARIANTS as _V5_CLAN_BANE_VARIANTS,
    bane_severity_for_bp as _bane_severity_for_bp,
    active_clan_bane as _active_clan_bane,
)


def _clan_bane_flaws() -> dict[str, dict]:
    """Per-clan chargen Bane flaws keyed by which Bane is active, e.g.
    {'nosferatu': {'standard': {'name': 'Repulsive', 'dots': 2}}}. JSON-safe
    for the wizard — the player picks standard vs variant at clan selection
    and the active choice drives the auto-granted flaw."""
    out: dict[str, dict] = {}
    for (clan, bane), flaw in _V5_CLAN_BANE_FLAWS.items():
        out.setdefault(clan, {})[bane] = flaw
    return out


def _clan_bane_flaw_pools() -> dict[str, dict]:
    """Per-clan Bane flaw POOLS keyed by active Bane, e.g.
    {'hecata': {'variant': {'options': ['Retainer', 'Haven', 'Resources']}}}.
    The dots distributed equal the character's Bane Severity."""
    out: dict[str, dict] = {}
    for (clan, bane), spec in _V5_CLAN_BANE_FLAW_POOLS.items():
        out.setdefault(clan, {})[bane] = spec
    return out


def _clan_bane_flaw_for(clan: str, choice: str) -> dict | None:
    """The chargen flaw a clan's active Bane grants (None for most)."""
    return _V5_CLAN_BANE_FLAWS.get((clan, choice if choice in ("standard", "variant") else "standard"))


def _clan_bane_flaw_pool_for(clan: str, choice: str) -> dict | None:
    """The chargen flaw-pool a clan's active Bane grants (None for most)."""
    return _V5_CLAN_BANE_FLAW_POOLS.get((clan, choice if choice in ("standard", "variant") else "standard"))


def _available_predator_types() -> list[str]:
    """The predator-type lineup the player wizard renders, after honoring
    the chronicle's component-restriction table. Default-restricted types
    (Blood Leech, Tithe Collector) are hidden unless an 'unlocked' row
    exists; default-allowed types are visible unless a 'banned' row
    exists. See web/db.py::is_component_allowed."""
    from ..db import is_component_allowed
    with get_db() as conn:
        return [
            p for p in _PREDATOR_TYPES
            if is_component_allowed(conn, "predator_type", p,
                                    _RESTRICTED_PREDATOR_TYPES)
        ]

_COVENANTS = [
    # Sect lineup per Steward direction (2026-05): "Unbound" replaced by
    # "Autarkis" to match NYbN chronicle terminology. Existing characters
    # with covenant="Unbound" should be migrated by staff manually.
    "Camarilla", "Anarch Movement", "Autarkis",
    "Hecata Clan", "Sabbat", "None / Unknown",
]


def _chronicle_settings() -> dict:
    """All chronicle-wide settings, used to drive the wizard branching."""
    from ..db import get_settings
    with get_db() as conn:
        return dict(get_settings(conn) or {})


def _is_sheet_required() -> bool:
    return bool(_chronicle_settings().get("require_sheet_on_create", 1))


def _char_cap() -> int:
    """Per-player character cap (0 = unlimited)."""
    try:
        return int(_chronicle_settings().get("max_chars_per_player", 2) or 0)
    except (TypeError, ValueError):
        return 0


def _player_at_cap(discord_id: str) -> int:
    """Return the cap value if the player is at/over it (truthy), else 0.
    Counts active + pending characters; drafts / retired / dead don't count."""
    cap = _char_cap()
    if not cap:
        return 0
    from ..db import count_active_player_characters
    with get_db() as conn:
        have = count_active_player_characters(conn, str(discord_id))
    return cap if have >= cap else 0


def _wizard_extras() -> dict:
    """Settings the wizard JS needs at render time.

    The new ruleset selector controls which budget table the wizard
    enforces. We pre-compute every tier's budget so the Alpine state
    can swap budgets when the player toggles character type / tier
    without another HTTP round-trip."""
    from ..db import tier_budget
    s = _chronicle_settings()
    ruleset = (s.get("active_ruleset") or "standard").lower()
    # Pre-resolve a budget per tier so the wizard can switch live.
    tier_budgets = {
        tier: tier_budget(s, tier)
        for tier in ("mortal", "ghoul", "revenant", "fledgling", "thinblood", "neonate", "ancilla")
    }
    # Default tier shown in the wizard is neonate — that's the budget
    # the sidebar initializes against on first render.
    default = tier_budgets["neonate"]
    return {
        "require_sheet":      bool(s.get("require_sheet_on_create", 1)),
        "revenants_enabled":  bool(s.get("revenants_enabled", 0)),
        "revenant_families":  s.get("revenant_families") or [],
        "clan_info":          _V5_CLAN_INFO,
        # Per-clan chargen Bane flaws keyed by choice (e.g. Nosferatu standard
        # → Repulsive ••, free) + the variant Bane name/effect the player can
        # pick at clan selection.
        "clan_bane_flaws":    _clan_bane_flaws(),
        "clan_bane_flaw_pools": _clan_bane_flaw_pools(),
        "clan_bane_variants": _V5_CLAN_BANE_VARIANTS,
        "predator_info":      _V5_PREDATOR_INFO,
        # Label lookups so the wizard's predator-grant pickers can render
        # human names for skill_*/disc_* keys without re-deriving them in JS.
        "skill_labels":       {k: lbl for _, traits in _V5_SKILLS for k, lbl in traits},
        "disc_labels":        {k: lbl for k, lbl in _V5_DISCIPLINES},
        # Chargen spreads — the wizard's skill/discipline trackers read these.
        "skill_spreads":      _V5_SKILL_SPREADS,
        "discipline_spreads": _V5_DISCIPLINE_SPREADS,
        "predator_free_disc_dots": _PREDATOR_FREE_DISCIPLINE_DOTS,
        "active_ruleset":     ruleset,
        "tier_budgets":       tier_budgets,
        # Default budget shape (neonate) — Alpine swaps in tier_budgets
        # when the player picks a different tier mid-wizard.
        "budgets": {
            "starting_xp":  default["xp"],
            "merits":       default["merits"],
            "advantages":   default["advantages"],
            "backgrounds":  default["backgrounds"],
            "flaw_cap":     default["flaw_cap"],
            "homebrew":     ruleset == "homebrew",
            "ruleset":      ruleset,
        },
    }


@router.get("/characters/new", response_class=HTMLResponse)
async def character_new(request: Request, user: dict = Depends(require_auth)):
    capped = _player_at_cap(str(user["id"]))
    if capped:
        request.session["flash"] = [{
            "kind": "error",
            "message": f"You've reached the {capped}-character limit for this chronicle.",
        }]
        return RedirectResponse(url="/characters", status_code=303)
    return templates.TemplateResponse(
        request, "player/character_create.html",
        _ctx(request, clans=_CLANS, predator_types=_available_predator_types(),
             covenants=_COVENANTS,
             v5_attributes=_V5_ATTRIBUTES, v5_skills=_V5_SKILLS,
             v5_disciplines=_V5_DISCIPLINES,
             clan_disciplines=_CLAN_DISCIPLINES,
             errors=[], form={},
             **_wizard_extras()),
    )


@router.post("/characters/new", response_class=HTMLResponse)
async def character_create(
    request: Request,
    user: dict = Depends(require_auth),
    _: None = Depends(csrf_protect),
):
    form = await request.form()
    name             = (form.get("name") or "").strip()
    clan             = (form.get("clan") or "").strip()
    predator_type    = (form.get("predator_type") or "").strip() or None
    concept          = (form.get("concept") or "").strip() or None
    sire             = (form.get("sire") or "").strip() or None
    covenant         = (form.get("covenant") or "").strip() or None
    profile_blurb    = (form.get("profile_blurb") or "").strip() or None
    submission_notes = (form.get("submission_notes") or "").strip() or None
    has_ingrained    = form.get("has_ingrained_flaw") == "on"
    character_type   = (form.get("character_type") or "kindred").strip().lower()
    # Normalize character_type up front so downstream tier-nulling and
    # ancilla-mode logic see the canonical value.
    if character_type not in {"kindred", "mortal", "ghoul", "revenant"}:
        character_type = "kindred"
    revenant_family  = (form.get("revenant_family") or "").strip() or None
    ghoul_regnant    = (form.get("ghoul_regnant") or "").strip() or None
    as_draft         = form.get("as_draft") == "1"
    draft_id_raw     = (form.get("draft_id") or "").strip()
    draft_id         = int(draft_id_raw) if draft_id_raw.isdigit() else 0
    require_sheet    = _is_sheet_required()

    # Phase 4: ancilla tier + In Memoriam
    # Kindred tiers per V5 Sea of Time. Elder is staff-seeded only.
    # Anything outside the recognised list coerces to fledgling (the
    # safest "no XP bonus" fallback).
    character_tier   = (form.get("character_tier") or "neonate").strip().lower()
    if character_tier not in {"fledgling", "thinblood", "neonate", "ancilla"}:
        character_tier = "neonate"
    # Tier only applies to Kindred. For non-Kindred we leave the column
    # at its 'neonate' DB default rather than NULL (the column is NOT
    # NULL since migration 010) — templates check character_type before
    # reading the tier, so the placeholder is harmless.
    ancilla_mode_raw = (form.get("ancilla_mode") or "").strip().lower()
    ancilla_mode     = ancilla_mode_raw if ancilla_mode_raw in {"standard", "in_memoriam"} else None
    if character_type != "kindred" or character_tier != "ancilla":
        ancilla_mode = None
    # Chronicle-level ruleset overrides the per-character choice for
    # Ancilla. When the chronicle runs In Memoriam, every Ancilla goes
    # through the era builder regardless of what the form posted.
    if character_tier == "ancilla":
        _chronicle_ruleset = (_chronicle_settings().get("active_ruleset") or "standard").lower()
        if _chronicle_ruleset == "in_memoriam":
            ancilla_mode = "in_memoriam"
    im_generation        = (form.get("im_generation") or "").strip() or None
    im_discipline_spread = (form.get("im_discipline_spread") or "").strip() or None
    try:
        in_memoriam = json.loads(form.get("in_memoriam") or "{}")
        if not isinstance(in_memoriam, dict):
            in_memoriam = {}
    except (ValueError, TypeError):
        in_memoriam = {}
    # Only persist these when relevant
    if ancilla_mode != "in_memoriam":
        in_memoriam = {}
        im_generation = None
        im_discipline_spread = None

    # Phase 5: V5 chargen metadata
    ambition       = (form.get("ambition")     or "").strip() or None
    desire         = (form.get("desire")       or "").strip() or None
    profession     = (form.get("profession")   or "").strip() or None
    pronouns       = (form.get("pronouns")     or "").strip() or None
    backstory      = (form.get("backstory")    or "").strip() or None
    try:
        true_age = form_int(form.get("true_age")) or None
    except ValueError:
        true_age = None
    try:
        apparent_age = form_int(form.get("apparent_age")) or None
    except ValueError:
        apparent_age = None

    errors: list[str] = []
    if not name:
        errors.append("Character name is required.")
    elif len(name) > 80:
        errors.append("Name must be 80 characters or fewer.")

    # Drafts are tolerant — we only require a name. Everything else can be
    # filled in across multiple sessions. Full validation only runs when
    # the player explicitly submits.
    if not as_draft:
        # Clan required for Kindred only. Mortals have none, Ghouls inherit
        # from their regnant, Revenants from their family.
        if character_type == "kindred":
            if not clan or clan not in {c[0] for c in _CLANS}:
                errors.append("Please select a valid clan.")
        else:
            clan = clan if (clan and clan in {c[0] for c in _CLANS}) else ""
        if character_type == "revenant" and not revenant_family:
            errors.append("Please select a revenant family.")
        if predator_type and predator_type not in _PREDATOR_TYPES:
            errors.append("Please select a valid predator type.")

        # Touchstones: V5 chargen requires 2-3. Only enforce on full-sheet
        # final submissions; short-form submits skip the Soul step entirely.
        if require_sheet:
            try:
                touchstones = json.loads(form.get("touchstones") or "[]")
            except (ValueError, TypeError):
                touchstones = []
            # The wizard stores touchstones as {name, conviction} dicts;
            # smoke tests sometimes post plain strings. Count any entry
            # whose name (or string body) is non-empty.
            def _counts(t):
                if isinstance(t, dict):
                    return bool(str(t.get("name") or "").strip())
                if isinstance(t, str):
                    return bool(t.strip())
                return False
            touch_count = len([t for t in touchstones if _counts(t)])
            if touch_count < 2:
                errors.append("Please provide at least 2 touchstones.")
            elif touch_count > 3:
                errors.append("Maximum 3 touchstones allowed at creation.")
    else:
        # On drafts, sanitize clan to a known value but don't error.
        if clan and clan not in {c[0] for c in _CLANS}:
            clan = ""

    # Per-player character cap — block a non-draft submission that would push
    # the player past their active+pending limit (drafts don't count, and a
    # draft being finalized isn't counted yet).
    if not as_draft:
        capped = _player_at_cap(str(user["id"]))
        if capped:
            errors.append(f"You've reached the {capped}-character limit for this chronicle.")

    def _rerender_wizard(errs):
        # Keep only string fields — the profile_image UploadFile is not
        # JSON-serializable and would 500 the wizard's `initialForm | tojson`.
        return templates.TemplateResponse(
            request, "player/character_create.html",
            _ctx(request, clans=_CLANS, predator_types=_available_predator_types(),
                 covenants=_COVENANTS,
                 v5_attributes=_V5_ATTRIBUTES, v5_skills=_V5_SKILLS,
                 v5_disciplines=_V5_DISCIPLINES,
                 clan_disciplines=_CLAN_DISCIPLINES,
                 errors=errs,
                 form={k: v for k, v in form.items() if isinstance(v, str)},
                 **_wizard_extras()),
        )

    if errors:
        return _rerender_wizard(errors)

    # Always parse the sheet from the form — drafts preserve what the
    # player typed even if it's incomplete. Final submission re-uses it.
    sheet = _parse_sheet_from_form(form, base={}) if (require_sheet or as_draft) else {}

    # Seed initial V5 stats based on character archetype + tier. Only
    # apply when the player hasn't already typed something (drafts
    # preserve in-progress values; final submission fills in defaults).
    if character_type == "kindred":
        if "blood_potency" not in sheet:
            if ancilla_mode == "in_memoriam" and im_generation:
                sheet["blood_potency"] = {"12th": 1, "11th-10th": 2, "9th-8th": 3}.get(im_generation, 1)
            else:
                sheet["blood_potency"] = 1
        if "humanity" not in sheet:
            base_humanity = 7
            if ancilla_mode == "in_memoriam" and isinstance(in_memoriam, dict):
                loss = sum(int(e.get("humanity_loss") or 0) for e in (in_memoriam.get("eras") or []) if isinstance(e, dict))
                # Pull embrace-age loss too
                age_id = in_memoriam.get("embrace_age")
                age_loss = {"up_to_100": 0, "up_to_150": 1, "over_150": 2}.get(age_id, 0)
                sheet["humanity"] = max(0, base_humanity - loss - age_loss)
            else:
                sheet["humanity"] = base_humanity
        if "hunger" not in sheet:
            sheet["hunger"] = 1
    elif character_type == "ghoul" or character_type == "revenant":
        sheet.setdefault("humanity", 7)
        # Ghouls + Revenants don't have Hunger or independent BP
        sheet.pop("hunger", None)
    elif character_type == "mortal":
        sheet.setdefault("humanity", 7)
        sheet.pop("hunger", None)
        sheet.pop("blood_potency", None)

    # Apply the predator type's flat Humanity / Blood Potency grants on top of
    # the seeded base. The wizard previews this, but humanity/BP are seeded
    # server-side (not posted), so persist the delta here to match.
    if character_type == "kindred" and predator_type in _V5_PREDATOR_INFO:
        for g in _V5_PREDATOR_INFO[predator_type].get("grants", []):
            if g.get("kind") == "delta":
                trait = g.get("trait")
                if trait in ("humanity", "blood_potency") and trait in sheet:
                    sheet[trait] = max(0, min(10, int(sheet[trait]) + int(g.get("delta", 0))))

    # Apply the clan's active-Bane chargen flaw (e.g. Nosferatu standard →
    # Repulsive ••) server-side too, so it lands even if the form omits it.
    # Free — tagged src='clan_bane' so the budget + sheets treat it as
    # auto-granted. The player picks standard vs variant at clan selection;
    # the Nosferatu variant (Infestation) grants no flaw.
    _bane_choice = (form.get("bane_choice") or "standard").strip()
    if _bane_choice not in ("standard", "variant"):
        _bane_choice = "standard"
    if require_sheet or as_draft:
        sheet["bane_choice"] = _bane_choice
        # Stash the active variant Bane's name so the sheets can show it
        # without re-deriving from the reference data.
        if _bane_choice == "variant" and clan in _V5_CLAN_BANE_VARIANTS:
            sheet["bane_variant_name"] = _V5_CLAN_BANE_VARIANTS[clan]["name"]
        else:
            sheet.pop("bane_variant_name", None)
        _bane_flaw = _clan_bane_flaw_for(clan, _bane_choice)
        _flaws = sheet.setdefault("flaws", [])
        # Strip any stale clan-Bane flaw, then re-apply the active one (if the
        # chosen Bane grants one) — mirrors the wizard's resolveClanBane so a
        # switch to a flawless variant (e.g. Nosferatu Infestation) clears it.
        _flaws[:] = [f for f in _flaws
                     if not (isinstance(f, dict) and f.get("src") == "clan_bane")]
        if _bane_flaw:
            _flaws.append({"name": _bane_flaw["name"], "dots": _bane_flaw["dots"],
                           "src": "clan_bane"})
        # Pool bane (e.g. Hecata Decay): distribute Bane Severity Flaw dots
        # among the named Flaws — free, auto-applied. Honors the player's
        # allocation and auto-fills any unspent dots into the first option so
        # the effect always lands.
        _pool = _clan_bane_flaw_pool_for(clan, _bane_choice)
        if _pool:
            _sev = _bane_severity_for_bp(sheet.get("blood_potency"))
            try:
                _alloc = json.loads(form.get("bane_flaw_pool") or "{}")
            except (ValueError, TypeError):
                _alloc = {}
            _alloc = _alloc if isinstance(_alloc, dict) else {}
            _cleaned_pool: dict[str, int] = {}
            _total = 0
            for _name in _pool["options"]:
                try:
                    _d = max(0, int(_alloc.get(_name, 0)))
                except (TypeError, ValueError):
                    _d = 0
                _d = min(_d, max(0, _sev - _total))   # don't exceed the pool
                if _d > 0:
                    _cleaned_pool[_name] = _d
                    _total += _d
            if _total < _sev and _pool["options"]:    # auto-fill the remainder
                _first = _pool["options"][0]
                _cleaned_pool[_first] = _cleaned_pool.get(_first, 0) + (_sev - _total)
            for _name, _d in _cleaned_pool.items():
                _flaws.append({"name": _name, "dots": min(5, _d), "src": "clan_bane"})
            sheet["bane_flaw_pool"] = _cleaned_pool
        else:
            sheet.pop("bane_flaw_pool", None)

    # V5 RAW chargen validation (Standard ruleset only). The base allocation —
    # attributes + skills before starting-XP buys — must follow the priority
    # spreads. Drafts stay tolerant; only full submissions are gated, and only
    # under the standard ruleset (homebrew runs its own tier budgets).
    if not as_draft and require_sheet:
        _settings = _chronicle_settings()
        _ruleset = (_settings.get("active_ruleset") or "standard").lower()
        if _ruleset == "standard":
            from ..db import tier_budget
            _bud = tier_budget(_settings, character_tier)
            raw_errors = _validate_chargen_raw(
                sheet, character_type=character_type,
                clan=clan, predator_type=predator_type,
                advantage_pool=_bud["merits"] + _bud["advantages"] + _bud["backgrounds"],
                flaw_cap=_bud["flaw_cap"],
            )
            if raw_errors:
                return _rerender_wizard(raw_errors)

    # Short-form Submit moves the character past the wizard but keeps it in
    # the draft state so the player can keep editing the sheet on the detail
    # page. `post_wizard` (a real column, migration 026) flags this so the
    # roster's resume link routes to the detail page, not back into the
    # wizard. This used to be an `_post_wizard` sentinel inside sheet_json —
    # routing state doesn't belong in the character blob.
    post_wizard = 1 if (not require_sheet and not as_draft) else 0

    from ..db import update_character
    with get_db() as conn:
        # If this submission carries a draft_id, update that row in place
        # (after verifying ownership). Otherwise create a new character.
        existing = None
        if draft_id:
            existing = get_character_for_player(conn, draft_id, user["id"])
            if not existing or not existing.get("is_draft"):
                # Stale or stolen draft id — fall back to create new.
                existing = None

        if existing:
            update_character(
                conn, existing["id"],
                name=name, clan=clan, predator_type=predator_type,
                concept=concept, sire=sire, covenant=covenant,
                sheet_json=sheet, has_ingrained_flaw=has_ingrained,
                character_type=character_type,
                revenant_family=revenant_family, ghoul_regnant=ghoul_regnant,
                character_tier=character_tier, ancilla_mode=ancilla_mode,
                im_generation=im_generation,
                im_discipline_spread=im_discipline_spread,
                in_memoriam=in_memoriam,
                ambition=ambition, desire=desire, profession=profession,
                true_age=true_age, apparent_age=apparent_age,
                pronouns=pronouns, backstory=backstory,
                # Short-form chronicles stage submissions as drafts so
                # the player can fill the external sheet on the detail
                # page before staff sees it in the review queue. The
                # detail-page "Submit for Review" button flips this.
                is_draft=1 if (as_draft or not require_sheet) else 0,
                submission_notes=submission_notes,
                post_wizard=post_wizard,
            )
            if profile_blurb:
                update_character(conn, existing["id"], profile_blurb=profile_blurb)
            char = {"id": existing["id"]}
        else:
            char = create_character(
                conn,
                discord_id=user["id"],
                name=name, clan=clan, predator_type=predator_type,
                concept=concept, sire=sire, covenant=covenant,
                sheet_json=sheet, has_ingrained_flaw=has_ingrained,
                character_type=character_type,
                revenant_family=revenant_family, ghoul_regnant=ghoul_regnant,
            )
            post_fields = {
                "character_tier":        character_tier,
                "ancilla_mode":          ancilla_mode,
                "im_generation":         im_generation,
                "im_discipline_spread":  im_discipline_spread,
                "in_memoriam":           in_memoriam,
                "ambition":              ambition,
                "desire":                desire,
                "profession":            profession,
                "true_age":              true_age,
                "apparent_age":          apparent_age,
                "pronouns":              pronouns,
                "backstory":             backstory,
            }
            if profile_blurb:
                post_fields["profile_blurb"] = profile_blurb
            if submission_notes:
                post_fields["submission_notes"] = submission_notes
            # Short-form chronicles stage even non-draft submissions as
            # drafts so the player can fill the external sheet on the
            # detail page before the character lands in the staff queue.
            if as_draft or not require_sheet:
                post_fields["is_draft"] = 1
            post_fields["post_wizard"] = post_wizard
            update_character(conn, char["id"], **post_fields)

    # Optional profile image — wizard only. If the player attached a file,
    # save it now that we have a character_id. Failures are non-fatal: the
    # character is already saved, we just flash an error and proceed.
    upload = form.get("profile_image")
    if upload is not None and hasattr(upload, "filename") and upload.filename:
        url, image_error = await _persist_uploaded_image(upload, char["id"])
        if url:
            with get_db() as conn:
                update_character(conn, char["id"], profile_image_url=url)
        elif image_error:
            request.session.setdefault("flash", []).append({
                "kind": "error",
                "message": f"Profile image not saved: {image_error}",
            })

    # Draft branch: send the player back to their roster with a soft
    # "saved" flash. They can resume editing from there.
    if as_draft:
        request.session["flash"] = [{
            "kind": "info",
            "message": f"Draft saved as {name}. Resume from your roster anytime.",
        }]
        return RedirectResponse(url="/characters", status_code=303)

    # When sheet wasn't collected during creation, drop the player on
    # the Sheet tab. The character is staged as a draft (above) so they
    # can keep editing freely; the explicit "Submit for Review" button
    # on the detail page is what finally sends it to staff.
    if not require_sheet:
        request.session["flash"] = [{
            "kind": "info",
            "message": f"{name} saved. Fill in your sheet here, then press Submit for Review when you're ready.",
        }]
        return RedirectResponse(url=f"/characters/{char['id']}?tab=sheet",
                                status_code=303)
    # Full-wizard submission — make it obvious the form went through.
    request.session["flash"] = [{
        "kind": "success",
        "message": f"{name} submitted for staff review. You'll be notified when staff acts on it.",
    }]
    return RedirectResponse(url=f"/characters/{char['id']}", status_code=303)


@router.get("/characters/{character_id}/resume-draft", response_class=HTMLResponse)
async def character_resume_draft(
    request: Request,
    character_id: int,
    user: dict = Depends(require_auth),
):
    """Re-open the creation wizard with all the saved draft data loaded.
    Owner-only; refuses for non-draft characters."""
    with get_db() as conn:
        char = get_character_for_player(conn, character_id, user["id"])
    if not char or not char.get("is_draft"):
        raise HTTPException(status_code=404)

    sheet = dict(char.get("sheet_json") or {})

    # Flatten the character + sheet into the form-shaped dict the wizard
    # expects so `initialForm` populates Alpine state on load.
    form = {
        "draft_id":         str(character_id),
        "name":             char.get("name") or "",
        "clan":             char.get("clan") or "",
        "predator_type":    char.get("predator_type") or "",
        "covenant":         char.get("covenant") or "",
        "sire":             char.get("sire") or "",
        "concept":          char.get("concept") or "",
        "profile_blurb":    char.get("profile_blurb") or "",
        "submission_notes": char.get("submission_notes") or "",
        "has_ingrained_flaw": "on" if char.get("has_ingrained_flaw") else "",
        "character_type":   char.get("character_type") or "kindred",
        "revenant_family":  char.get("revenant_family") or "",
        "ghoul_regnant":    char.get("ghoul_regnant") or "",
        "character_tier":   char.get("character_tier") or "neonate",
        "ancilla_mode":     char.get("ancilla_mode") or "",
        "in_memoriam":      char.get("in_memoriam") or {},
        # Phase 5 metadata
        "ambition":         char.get("ambition") or "",
        "desire":           char.get("desire") or "",
        "profession":       char.get("profession") or "",
        "true_age":         char.get("true_age"),
        "apparent_age":     char.get("apparent_age"),
        "pronouns":         char.get("pronouns") or "",
        "backstory":        char.get("backstory") or "",
        # Chargen build picks — the wizard reads these at the TOP level of
        # initialForm (not nested in sheet), so surface them here or the
        # predator-grant pickers / spread selections come back blank and the
        # player has to re-choose everything on the Hunt step.
        "predator_choices":  sheet.get("predator_choices") or {},
        "skill_spread":      sheet.get("skill_spread") or "",
        "discipline_spread": sheet.get("discipline_spread") or "",
        "bane_choice":       sheet.get("bane_choice") or "standard",
        "bane_flaw_pool":    sheet.get("bane_flaw_pool") or {},
        # Sheet data — wizard reads these from initialForm.sheet
        "sheet":            sheet,
    }
    return templates.TemplateResponse(
        request, "player/character_create.html",
        _ctx(request, clans=_CLANS, predator_types=_available_predator_types(),
             covenants=_COVENANTS,
             v5_attributes=_V5_ATTRIBUTES, v5_skills=_V5_SKILLS,
             v5_disciplines=_V5_DISCIPLINES,
             clan_disciplines=_CLAN_DISCIPLINES,
             errors=[], form=form,
             **_wizard_extras()),
    )


@router.post("/characters/{character_id}/about", response_class=HTMLResponse)
async def character_about_save(
    request: Request,
    character_id: int,
    user: dict = Depends(require_auth),
    _: None = Depends(csrf_protect),
):
    """Save the inline "About My Character" panel — the identity + narrative
    fields relocated out of chargen. Mirrors /edit's gating: concept/sire/
    covenant stay editable; the IC profile fields freeze once staff sets
    profile_locked; everything locks while the character is under review."""
    with get_db() as conn:
        char = get_character_for_player(conn, character_id, user["id"])
    if not char:
        raise HTTPException(status_code=404)
    if not char.get("is_approved") and char.get("review_started_at"):
        request.session["flash"] = [{"kind": "error",
            "message": "Staff is reviewing this character — edits are locked until they finish."}]
        return RedirectResponse(url=f"/characters/{character_id}", status_code=303)

    form = await request.form()
    updates: dict = dict(
        concept=(form.get("concept") or "").strip() or None,
        sire=(form.get("sire") or "").strip() or None,
        covenant=(form.get("covenant") or "").strip() or None,
    )
    if not char.get("profile_locked"):
        updates.update(
            profile_blurb=(form.get("profile_blurb") or "").strip() or None,
            pronouns=(form.get("pronouns") or "").strip()[:60] or None,
            profession=(form.get("profession") or "").strip()[:80] or None,
            backstory=(form.get("backstory") or "").strip() or None,
            ambition=(form.get("ambition") or "").strip() or None,
            desire=(form.get("desire") or "").strip() or None,
            true_age=form_int(form.get("true_age")) or None,
            apparent_age=form_int(form.get("apparent_age")) or None,
        )
    with get_db() as conn:
        update_character(conn, character_id, **updates)
    request.session["flash"] = [{"kind": "success", "message": "Character details updated."}]
    return RedirectResponse(url=f"/characters/{character_id}", status_code=303)


@router.get("/characters/{character_id}/edit", response_class=HTMLResponse)
async def character_edit(
    request: Request,
    character_id: int,
    user: dict = Depends(require_auth),
):
    with get_db() as conn:
        char = get_character_for_player(conn, character_id, user["id"])
    if not char:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        request, "player/character_edit.html",
        _ctx(request, char=char, clans=_CLANS, predator_types=_available_predator_types(),
             covenants=_COVENANTS, errors=[]),
    )


@router.post("/characters/{character_id}/edit", response_class=HTMLResponse)
async def character_edit_post(
    request: Request,
    character_id: int,
    user: dict = Depends(require_auth),
    _: None = Depends(csrf_protect),
):
    with get_db() as conn:
        char = get_character_for_player(conn, character_id, user["id"])
    if not char:
        raise HTTPException(status_code=404)

    # Lock: while staff has this pending character under review, freeze ALL
    # player edits (identity + profile), mirroring the /sheet lock — so the
    # character can't change out from under the reviewer. Edits are free
    # again before review starts and after approval (gated on is_approved).
    if not char.get("is_approved") and char.get("review_started_at"):
        request.session["flash"] = [{"kind": "error",
            "message": "Staff is reviewing this character — edits are locked until they finish."}]
        return RedirectResponse(url=f"/characters/{character_id}", status_code=303)

    form          = await request.form()
    concept       = (form.get("concept") or "").strip() or None
    sire          = (form.get("sire") or "").strip() or None
    covenant      = (form.get("covenant") or "").strip() or None
    predator_type = (form.get("predator_type") or "").strip() or None
    profile_blurb = (form.get("profile_blurb") or "").strip() or None
    # Structured profile fields — re-editable from /edit so players
    # can update IC details after chargen.
    pronouns      = (form.get("pronouns") or "").strip()[:60] or None
    profession    = (form.get("profession") or "").strip()[:80] or None
    backstory     = (form.get("backstory") or "").strip() or None
    ambition      = (form.get("ambition") or "").strip() or None
    desire        = (form.get("desire") or "").strip() or None
    try:
        true_age      = form_int(form.get("true_age")) or None
    except ValueError:
        true_age = None
    try:
        apparent_age  = form_int(form.get("apparent_age")) or None
    except ValueError:
        apparent_age = None

    errors: list[str] = []
    updates: dict = dict(
        concept=concept, sire=sire, covenant=covenant,
        predator_type=predator_type,
    )

    # Profile + IC fields are gated by profile_locked. Locked = staff
    # froze the IC profile after approval; identity fields (concept /
    # sire / covenant / predator) stay editable but blurb + structured
    # IC details freeze.
    if not char.get("profile_locked"):
        updates.update(
            profile_blurb=profile_blurb,
            pronouns=pronouns, profession=profession, backstory=backstory,
            ambition=ambition, desire=desire,
            true_age=true_age, apparent_age=apparent_age,
        )

    if not char["is_approved"]:
        name  = (form.get("name") or "").strip()
        clan  = (form.get("clan") or "").strip()
        has_ingrained = form.get("has_ingrained_flaw") == "on"

        if not name:
            errors.append("Character name is required.")
        elif len(name) > 80:
            errors.append("Name must be 80 characters or fewer.")
        if not clan or clan not in {c[0] for c in _CLANS}:
            errors.append("Please select a valid clan.")
        if predator_type and predator_type not in _PREDATOR_TYPES:
            errors.append("Please select a valid predator type.")

        if not errors:
            updates["name"] = name
            updates["clan"] = clan
            updates["has_ingrained_flaw"] = has_ingrained
            # Clear rejection so it goes back to staff review
            if char.get("rejection_reason"):
                updates["rejection_reason"] = None
                updates["rejected_at"]      = None

    if errors:
        with get_db() as conn:
            char = get_character_for_player(conn, character_id, user["id"])
        return templates.TemplateResponse(
            request, "player/character_edit.html",
            _ctx(request, char=char, clans=_CLANS, predator_types=_available_predator_types(),
                 covenants=_COVENANTS, errors=errors),
        )

    with get_db() as conn:
        update_character(conn, character_id, **updates)

    return RedirectResponse(url=f"/characters/{character_id}", status_code=303)


# ── Profile image upload ───────────────────────────────────────────────────────

_ALLOWED_IMAGE_TYPES = {
    "image/jpeg": "jpg",
    "image/png":  "png",
    "image/webp": "webp",
    "image/gif":  "gif",
}
_MAX_IMAGE_BYTES = 2 * 1024 * 1024  # 2 MB
_UPLOADS_DIR = Path(__file__).parent.parent / "static" / "uploads"


def _safe_image_return(raw, character_id: int) -> str:
    """Validate a posted `next` return path for the image routes. Only the
    character's own sheet or edit page are allowed; anything else (external
    URLs, other characters, protocol-relative) falls back to the sheet."""
    allowed = {f"/characters/{character_id}", f"/characters/{character_id}/edit"}
    dest = str(raw or "").strip()
    return dest if dest in allowed else f"/characters/{character_id}"


async def _persist_uploaded_image(upload, character_id: int) -> tuple[str | None, str | None]:
    """Validate and write an uploaded image to /static/uploads/.

    Returns (url, error). On success, the character row should be updated
    with the returned url. On failure, error is a user-facing message and
    the file is not written.
    """
    if upload is None or not hasattr(upload, "filename") or not upload.filename:
        return None, "Please choose a file to upload."
    ext = _ALLOWED_IMAGE_TYPES.get(upload.content_type or "")
    if not ext:
        return None, "Unsupported file type — use JPG, PNG, WebP, or GIF."
    data = await upload.read()
    if len(data) > _MAX_IMAGE_BYTES:
        return None, f"File is too large ({len(data) // 1024} KB) — max 2 MB."

    _UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    # Remove any previous image (regardless of extension) for this character.
    for old in _UPLOADS_DIR.glob(f"character_{character_id}.*"):
        try:
            old.unlink()
        except OSError:
            pass

    target = _UPLOADS_DIR / f"character_{character_id}.{ext}"
    target.write_bytes(data)
    return f"/static/uploads/character_{character_id}.{ext}", None


@router.post("/characters/{character_id}/image", response_class=HTMLResponse)
async def character_image_upload(
    request: Request,
    character_id: int,
    user: dict = Depends(require_auth),
    _: None = Depends(csrf_protect),
):
    with get_db() as conn:
        char = get_character_for_player(conn, character_id, user["id"])
    if not char:
        raise HTTPException(status_code=404)

    form = await request.form()
    # Return to whichever page launched the upload (the sheet's About panel
    # or the full edit page) so the player sees the result in place instead
    # of being bounced to /edit. Falls back to the sheet.
    dest = _safe_image_return(form.get("next"), character_id)
    upload = form.get("image")
    url, error = await _persist_uploaded_image(upload, character_id)
    if error:
        request.session["flash"] = [{"kind": "error", "message": error}]
        return RedirectResponse(url=dest, status_code=303)

    with get_db() as conn:
        update_character(conn, character_id, profile_image_url=url)

    request.session["flash"] = [{"kind": "success", "message": "Profile image updated."}]
    return RedirectResponse(url=dest, status_code=303)


@router.post("/characters/{character_id}/image/delete", response_class=HTMLResponse)
async def character_image_delete(
    request: Request,
    character_id: int,
    user: dict = Depends(require_auth),
    _: None = Depends(csrf_protect),
):
    with get_db() as conn:
        char = get_character_for_player(conn, character_id, user["id"])
    if not char:
        raise HTTPException(status_code=404)

    form = await request.form()
    dest = _safe_image_return(form.get("next"), character_id)
    for old in _UPLOADS_DIR.glob(f"character_{character_id}.*"):
        try:
            old.unlink()
        except OSError:
            pass

    with get_db() as conn:
        update_character(conn, character_id, profile_image_url=None)

    request.session["flash"] = [{"kind": "info", "message": "Profile image removed."}]
    return RedirectResponse(url=dest, status_code=303)


# ── Sheet payload parsing (shared by create + edit) ──────────────────────────

_RATED_LIST_KEYS = {
    "merits":      "dots",
    "advantages":  "dots",
    "backgrounds": "dots",
    "flaws":       "dots",
    "rituals":     "level",
    "ceremonies":  "level",
    "formulae":    "level",
}
_FREE_LIST_KEYS = ("convictions",)  # touchstones are paired now — handled below
_DAMAGE_KEYS = (
    "damage_health_sup", "damage_health_agg",
    "damage_willpower_sup", "damage_willpower_agg",
)


def _parse_sheet_from_form(form, base: dict | None = None) -> dict:
    """Build a sheet_json dict from a posted form. Used by both the
    initial creation wizard and the per-character sheet save."""
    sheet = dict(base or {})

    # Numeric traits (attributes + skills + disciplines + humanity/BP/hunger)
    for key in _SHEET_TRAIT_KEYS:
        raw = form.get(key)
        if raw is None or raw == "":
            sheet.pop(key, None)
            continue
        try:
            val = int(raw)
        except ValueError:
            continue
        max_dots = _SHEET_LIMITS.get(key, 5)
        sheet[key] = max(0, min(max_dots, val))

    # Rated lists: merits/flaws/rituals/ceremonies/formulae
    for list_key, rating_field in _RATED_LIST_KEYS.items():
        raw = form.get(list_key)
        if raw is None:
            continue
        try:
            items = json.loads(raw)
        except (ValueError, TypeError):
            continue
        if not isinstance(items, list):
            continue
        cleaned: list[dict] = []
        for it in items:
            if not isinstance(it, dict):
                continue
            name = str(it.get("name", "")).strip()[:80]
            if not name:
                continue
            try:
                rating = int(it.get(rating_field, 0))
            except (ValueError, TypeError):
                rating = 0
            entry = {"name": name, rating_field: max(0, min(5, rating))}
            # Preserve provenance (e.g. src='predator') so resume-draft can
            # tell auto-granted entries from player-added ones and reconcile
            # them instead of duplicating.
            src = str(it.get("src", "")).strip()[:20]
            if src:
                entry["src"] = src
            cleaned.append(entry)
        sheet[list_key] = cleaned

    # Discipline powers — {discipline: 'disc_auspex', name: 'Heightened Senses', level: 1}
    raw = form.get("powers")
    if raw is not None:
        try:
            items = json.loads(raw)
        except (ValueError, TypeError):
            items = None
        if isinstance(items, list):
            valid_disc = {key for key, _ in _V5_DISCIPLINES}
            cleaned_pow: list[dict] = []
            for it in items:
                if not isinstance(it, dict):
                    continue
                disc = str(it.get("discipline", "")).strip()
                name = str(it.get("name", "")).strip()[:80]
                if not disc or not name or disc not in valid_disc:
                    continue
                try:
                    level = int(it.get("level", 1))
                except (ValueError, TypeError):
                    level = 1
                cleaned_pow.append({"discipline": disc, "name": name,
                                    "level": max(1, min(5, level))})
            sheet["powers"] = cleaned_pow

    # Free-text lists: touchstones, convictions
    for list_key in _FREE_LIST_KEYS:
        raw = form.get(list_key)
        if raw is None:
            continue
        try:
            items = json.loads(raw)
        except (ValueError, TypeError):
            continue
        if not isinstance(items, list):
            continue
        cleaned_free: list[str] = []
        for it in items:
            s = str(it).strip()[:120]
            if s:
                cleaned_free.append(s)
        sheet[list_key] = cleaned_free

    # Damage tracks (only relevant on edit; wizard skips these)
    for dmg_key in _DAMAGE_KEYS:
        raw_val = form.get(dmg_key)
        if raw_val is None or raw_val == "":
            sheet.pop(dmg_key, None)
            continue
        try:
            n = int(raw_val)
        except ValueError:
            continue
        sheet[dmg_key] = max(0, min(15, n))

    # Touchstones now paired with Convictions: each entry is
    # {name: <touchstone>, conviction: <text>}. Backward-compat: also
    # accept plain strings and treat them as touchstone-only entries.
    raw = form.get("touchstones")
    if raw is not None:
        try:
            items = json.loads(raw)
        except (ValueError, TypeError):
            items = None
        if isinstance(items, list):
            cleaned_t: list[dict] = []
            for it in items:
                if isinstance(it, dict):
                    name = str(it.get("name", "")).strip()[:120]
                    conv = str(it.get("conviction", "")).strip()[:200]
                    if name or conv:
                        cleaned_t.append({"name": name, "conviction": conv})
                elif isinstance(it, str):
                    s = it.strip()[:120]
                    if s:
                        cleaned_t.append({"name": s, "conviction": ""})
            sheet["touchstones"] = cleaned_t

    # Specialties: pairs of {skill: skill_key, name: str}
    raw = form.get("specialties")
    if raw is not None:
        try:
            items = json.loads(raw)
        except (ValueError, TypeError):
            items = None
        if isinstance(items, list):
            valid_skills = {key for _, traits in _V5_SKILLS for key, _ in traits}
            cleaned_spec: list[dict] = []
            for it in items:
                if not isinstance(it, dict):
                    continue
                skill = str(it.get("skill", "")).strip()
                name  = str(it.get("name", "")).strip()[:80]
                if not skill or not name or skill not in valid_skills:
                    continue
                spec = {"skill": skill, "name": name}
                src = str(it.get("src", "")).strip()[:20]
                if src:
                    spec["src"] = src
                cleaned_spec.append(spec)
            sheet["specialties"] = cleaned_spec

    # Chargen build metadata: the spreads the player followed + the resolved
    # predator picks (provenance — the granted traits themselves already live
    # in specialties/backgrounds/flaws/disc_* via the blocks above).
    skill_spread = (form.get("skill_spread") or "").strip()[:40]
    if skill_spread:
        sheet["skill_spread"] = skill_spread
    disc_spread = (form.get("discipline_spread") or "").strip()[:40]
    if disc_spread:
        sheet["discipline_spread"] = disc_spread
    raw = form.get("predator_choices")
    if raw:
        try:
            pc = json.loads(raw)
            if isinstance(pc, dict):
                sheet["predator_choices"] = pc
        except (ValueError, TypeError):
            pass

    # Starting-XP allocator: the bought dots are already folded into the trait
    # values above; keep the purchase ledger + totals for staff review.
    raw = form.get("xp_buys")
    if raw:
        try:
            buys = json.loads(raw)
            if isinstance(buys, list):
                sheet["xp_buys"] = buys
        except (ValueError, TypeError):
            pass
    xp_spent = form_int(form.get("xp_spent"))
    if xp_spent:
        sheet["xp_spent"] = xp_spent
    xp_pool = form_int(form.get("xp_pool"))
    if xp_pool:
        sheet["starting_xp_pool"] = xp_pool

    return sheet


# ── Character sheet save ──────────────────────────────────────────────────────

@router.post("/characters/{character_id}/sheet", response_class=HTMLResponse)
async def character_sheet_save(
    request: Request,
    character_id: int,
    user: dict = Depends(require_auth),
    _: None = Depends(csrf_protect),
):
    """Save attributes / skills / single-value traits to character.sheet_json.
    Blocked while staff has the character under review."""
    with get_db() as conn:
        char = get_character_for_player(conn, character_id, user["id"])
    if not char:
        raise HTTPException(status_code=404)

    # Lock: if staff has opened the review on this pending character,
    # the player can't keep editing until the review concludes.
    if not char.get("is_approved") and char.get("review_started_at"):
        request.session["flash"] = [{"kind": "error",
            "message": "Staff is reviewing this character — edits are locked until they finish."}]
        return RedirectResponse(url=f"/characters/{character_id}?tab=sheet",
                                status_code=303)

    form = await request.form()
    sheet = _parse_sheet_from_form(form, base=char.get("sheet_json") or {})

    with get_db() as conn:
        update_character(conn, character_id, sheet_json=sheet)

    request.session["flash"] = [{"kind": "success", "message": "Sheet saved."}]
    return RedirectResponse(url=f"/characters/{character_id}?tab=sheet", status_code=303)


# ── IC Profile (public to all logged-in players) ──────────────────────────────

@router.get("/profiles/{character_id}", response_class=HTMLResponse)
async def ic_profile(
    request: Request,
    character_id: int,
    user: dict = Depends(require_auth),
):
    """Read-only IC profile, visible to any logged-in player."""
    with get_db() as conn:
        char = get_character(conn, character_id)
        if not char or not char["is_approved"]:
            raise HTTPException(status_code=404)
        coterie = get_coterie_for_character(conn, character_id)
    return templates.TemplateResponse(
        request, "player/ic_profile.html",
        _ctx(request, char=char, coterie=coterie, is_owner=(char["discord_id"] == user["id"])),
    )


@router.post("/characters/{character_id}/withdraw", response_class=HTMLResponse)
async def character_withdraw(
    request: Request,
    character_id: int,
    user: dict = Depends(require_auth),
    _: None = Depends(csrf_protect),
):
    """Player withdraws a pending (unapproved) character — hard delete."""
    with get_db() as conn:
        char = get_character_for_player(conn, character_id, user["id"])
        if not char:
            raise HTTPException(status_code=404)
        if char["is_approved"]:
            raise HTTPException(status_code=400, detail="Cannot withdraw an approved character")
        name = char["name"]
        delete_character(conn, character_id)

    request.session["flash"] = [{"kind": "info", "message": f"\"{name}\" has been withdrawn."}]
    return RedirectResponse(url="/characters", status_code=303)


@router.post("/characters/{character_id}/submit-for-review", response_class=HTMLResponse)
async def character_submit_for_review(
    request: Request,
    character_id: int,
    user: dict = Depends(require_auth),
    _: None = Depends(csrf_protect),
):
    """Flip a draft character into the staff review queue.

    Short-form chargen stages submissions as drafts so the player can
    fill the external sheet on the detail page before staff sees it.
    This route is the explicit "I'm done — review me" signal that lands
    the character in the staff pending list."""
    with get_db() as conn:
        char = get_character_for_player(conn, character_id, user["id"])
        if not char:
            raise HTTPException(status_code=404)
        if char["is_approved"]:
            raise HTTPException(status_code=400, detail="Already approved")
        if not char.get("is_draft"):
            # Already in the staff queue — no-op with a soft flash so the
            # double-click doesn't error out the player.
            request.session["flash"] = [{
                "kind": "info",
                "message": f"{char['name']} is already with staff for review.",
            }]
            return RedirectResponse(url=f"/characters/{character_id}",
                                    status_code=303)
        update_character(conn, character_id, is_draft=0)

    request.session["flash"] = [{
        "kind": "success",
        "message": f"{char['name']} submitted for staff review. You'll be notified when staff acts on it.",
    }]
    return RedirectResponse(url=f"/characters/{character_id}", status_code=303)


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
        backgrounds     = list_character_backgrounds(conn, character_id)
        projects        = list_projects_for_character(conn, character_id)
        proj_rolls      = timeskip_rolls_remaining(conn, character_id)

    p_criteria     = _player_criteria(all_criteria)
    period_claimed = (
        active_period is not None
        and _already_claimed(claims, active_period["id"])
    )
    draft_claim = (
        _find_draft_claim(claims, active_period["id"])
        if active_period else None
    )

    return templates.TemplateResponse(
        request, "player/character.html",
        _ctx(
            request,
            char=char,
            active_period=active_period,
            player_criteria=p_criteria,
            already_claimed=period_claimed,
            draft_claim=draft_claim,
            # Non-draft claims only for the recent history panel.
            claims=[c for c in claims if c["status"] != "draft"][:10],
            spends=spends[:10],
            ledger=ledger,
            coterie=coterie,
            default_tab=tab,
            backgrounds=backgrounds,
            projects=projects,
            proj_rolls=proj_rolls,
            spend_categories=SPEND_CATEGORIES,
            spend_rules_json=json.dumps(RULES),
            humanity_conditions=HUMANITY_CONDITIONS,
            v5_attributes=_V5_ATTRIBUTES,
            v5_skills=_V5_SKILLS,
            v5_disciplines=_V5_DISCIPLINES,
            active_bane=_active_clan_bane(
                char.get("clan"), (char.get("sheet_json") or {}).get("bane_choice")),
            clan_disciplines=set(_CLAN_DISCIPLINES.get(char["clan"], [])),
        ),
    )


def _find_draft_claim(claims: list[dict], period_id: int) -> dict | None:
    """Return the current open draft claim for this period, if any."""
    for c in claims:
        if c["play_period_id"] == period_id and c["status"] == "draft":
            return c
    return None


# ── Background blanking ───────────────────────────────────────────────────────

def _backgrounds_partial(request: Request, char: dict, conn, *,
                         notice: str | None = None, error: str | None = None):
    """Re-render the backgrounds card for an HTMX swap."""
    return templates.TemplateResponse(
        request, "player/partials/backgrounds.html",
        _ctx(
            request,
            char=char,
            backgrounds=list_character_backgrounds(conn, char["id"]),
            active_period=get_active_period(conn),
            bg_notice=notice,
            bg_error=error,
        ),
    )


@router.post("/characters/{character_id}/backgrounds/set", response_class=HTMLResponse)
async def set_background(
    request: Request,
    character_id: int,
    user: dict = Depends(require_auth),
    _: None = Depends(csrf_protect),
):
    form = await request.form()
    name = (form.get("background_name") or "").strip()
    dots = max(0, min(10, form_int(form.get("dots_total"), 0)))
    actor = f"player:{user.get('username') or user['id']}"
    notice = error = None
    with get_db() as conn:
        char = get_character_for_player(conn, character_id, user["id"])
        if not char:
            raise HTTPException(status_code=404)
        try:
            res = set_character_background(conn, character_id, name, dots, actor)
            notice = (f"Removed {res['name']}." if res["deleted"]
                      else f"Saved {res['name']} at {dots} dot(s).")
        except ValueError as exc:
            error = str(exc)
        return _backgrounds_partial(request, char, conn, notice=notice, error=error)


@router.post("/characters/{character_id}/backgrounds/blank", response_class=HTMLResponse)
async def blank_background(
    request: Request,
    character_id: int,
    user: dict = Depends(require_auth),
    _: None = Depends(csrf_protect),
):
    form = await request.form()
    name = (form.get("background_name") or "").strip()
    dots = form_int(form.get("dots"), 1)
    actor = f"player:{user.get('username') or user['id']}"
    notice = error = None
    with get_db() as conn:
        char = get_character_for_player(conn, character_id, user["id"])
        if not char:
            raise HTTPException(status_code=404)
        try:
            res = blank_character_background(conn, character_id, name, dots, actor)
            notice = (f"Blanked {res['blanked_now']} dot(s) of {res['name']} — "
                      "restores when the next night opens.")
        except ValueError as exc:
            error = str(exc)
        return _backgrounds_partial(request, char, conn, notice=notice, error=error)


# ── Projects (downtime endeavours) ────────────────────────────────────────────

def _projects_partial(request: Request, char: dict, conn, *,
                      notice: str | None = None, error: str | None = None):
    """Re-render the projects card for an HTMX swap."""
    return templates.TemplateResponse(
        request, "player/partials/projects.html",
        _ctx(
            request,
            char=char,
            projects=list_projects_for_character(conn, char["id"]),
            proj_rolls=timeskip_rolls_remaining(conn, char["id"]),
            proj_notice=notice,
            proj_error=error,
        ),
    )


@router.post("/characters/{character_id}/projects/propose", response_class=HTMLResponse)
async def propose_project(
    request: Request,
    character_id: int,
    user: dict = Depends(require_auth),
    _: None = Depends(csrf_protect),
):
    form = await request.form()
    title = (form.get("title") or "").strip()
    description = (form.get("description") or "").strip()
    notice = error = None
    with get_db() as conn:
        char = get_character_for_player(conn, character_id, user["id"])
        if not char:
            raise HTTPException(status_code=404)
        if not char.get("is_approved"):
            error = "Your character must be approved before proposing projects."
        else:
            try:
                create_project(conn, character_id, title, description,
                               proposed_by=user["id"])
                notice = "Project proposed — staff will review it."
            except ValueError as exc:
                error = str(exc)
        return _projects_partial(request, char, conn, notice=notice, error=error)


@router.post("/characters/{character_id}/claim", response_class=HTMLResponse)
async def submit_claim(
    request: Request,
    character_id: int,
    user: dict = Depends(require_auth),
    _: None = Depends(csrf_protect),
):
    from ..db import update_draft_claim
    form         = await request.form()
    criteria_ids = [n for x in form.getlist("criteria_ids") if (n := form_int(x)) > 0]
    rp_links     = [x.strip() for x in form.getlist("rp_links") if x.strip()]
    path         = form.get("path", "none")
    helper_note  = (form.get("helper_note") or "").strip() or None
    as_draft     = form.get("as_draft") == "1"
    draft_id_raw = (form.get("draft_id") or "").strip()
    draft_id     = int(draft_id_raw) if draft_id_raw.isdigit() else 0

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
        # Drafts are lenient — we only need an approved character and an
        # active period. Skip every other check so players can save
        # work-in-progress entries even if they're still figuring out
        # criteria / RP links.
        if not char.get("is_approved"):
            errors.append("Your character must be approved before claiming XP.")

        if not active_period:
            errors.append("There is no active XP window right now.")
        elif not as_draft and _already_claimed(claims, active_period["id"]):
            errors.append("You have already submitted a claim for this period.")

        if not as_draft:
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
            existing_draft = _find_draft_claim(claims, active_period["id"]) if active_period else None
            resp = templates.TemplateResponse(
                request, "player/partials/claim_section.html",
                _ctx(
                    request,
                    char=char,
                    active_period=active_period,
                    player_criteria=list(criteria_map.values()),
                    already_claimed=False,
                    claim_errors=errors,
                    claim_form={"criteria_ids": criteria_ids, "rp_links": rp_links, "path": path},
                    draft_claim=existing_draft,
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

        # If the form carried a draft_id, decide whether to update it or
        # ignore it (stale / non-draft / not theirs). Otherwise create.
        existing_draft = None
        if draft_id:
            for c in claims:
                if c["id"] == draft_id and c["character_id"] == character_id and c["status"] == "draft":
                    existing_draft = c
                    break

        if existing_draft:
            update_draft_claim(
                conn, existing_draft["id"],
                claimed_criteria=claimed_criteria,
                rp_links=rp_links,
                path=path,
                helper_note=helper_note,
                submit_now=not as_draft,
            )
        else:
            # Detect staff/helper double-dip: if this player has another
            # character with a non-rejected staff/helper claim for the
            # same period, flag the new one so staff sees the conflict.
            staff_claim_conflict = False
            if path in ("staff", "helper") and not as_draft:
                row = conn.execute(
                    """
                    SELECT 1
                    FROM xp_claims xc
                    JOIN characters c2 ON c2.id = xc.character_id
                    WHERE c2.discord_id = ?
                      AND c2.id        != ?
                      AND xc.play_period_id = ?
                      AND xc.path     IN ('staff', 'helper')
                      AND xc.status   != 'rejected'
                    LIMIT 1
                    """,
                    (user["id"], character_id, active_period["id"]),
                ).fetchone()
                staff_claim_conflict = bool(row)

            create_claim(
                conn,
                character_id=character_id,
                play_period_id=active_period["id"],
                claimed_criteria=claimed_criteria,
                rp_links=rp_links,
                path=path,
                helper_note=helper_note,
                is_draft=as_draft,
                staff_claim_conflict=staff_claim_conflict,
            )

        # Refresh after insert/update
        claims     = list_claims_for_character(conn, character_id)
        p_criteria = list(criteria_map.values())

    # Draft save → return the form pre-filled with the saved draft so the
    # player can keep going. Submission → confirmation panel.
    if as_draft:
        saved = _find_draft_claim(claims, active_period["id"])
        resp = templates.TemplateResponse(
            request, "player/partials/claim_section.html",
            _ctx(
                request,
                char=char,
                active_period=active_period,
                player_criteria=p_criteria,
                already_claimed=False,
                draft_claim=saved,
                draft_saved=True,
            ),
        )
        _toast(resp, "Draft saved — finish and submit when ready.")
        return resp

    resp = templates.TemplateResponse(
        request, "player/partials/claim_section.html",
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


@router.post("/characters/{character_id}/claim/{claim_id}/discard", response_class=HTMLResponse)
async def discard_claim_draft(
    request: Request,
    character_id: int,
    claim_id: int,
    user: dict = Depends(require_auth),
    _: None = Depends(csrf_protect),
):
    """Throw away an in-progress claim draft. Only the claim's owner
    can do this, and only for status='draft' — approved/pending claims
    must go through staff."""
    from ..db import discard_draft_claim
    with get_db() as conn:
        char = get_character_for_player(conn, character_id, user["id"])
        if not char:
            raise HTTPException(status_code=404)

        # Verify the claim belongs to this character before deleting.
        claim = get_claim(conn, claim_id)
        if claim is None or claim["character_id"] != character_id:
            raise HTTPException(status_code=404)

        discard_draft_claim(conn, claim_id)

        active_period = get_active_period(conn)
        all_criteria  = list_criteria(conn, active_only=True)
        p_criteria    = _player_criteria(all_criteria)

    resp = templates.TemplateResponse(
        request, "player/partials/claim_section.html",
        _ctx(
            request,
            char=char,
            active_period=active_period,
            player_criteria=p_criteria,
            already_claimed=False,
        ),
    )
    _toast(resp, "Draft discarded.", "info")
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
    current_dots = form_int(form.get("current_dots"))
    new_dots     = form_int(form.get("new_dots"), 1)
    note         = (form.get("note") or "").strip() or None
    hc_checked   = [form.get(f"hc_{i}") == "on" for i in range(len(HUMANITY_CONDITIONS))]
    # Optional explicit depends_on (migration 023). If absent we'll
    # auto-detect a chain inside the DB block below.
    depends_on_raw = (form.get("depends_on") or "").strip()
    depends_on    = int(depends_on_raw) if depends_on_raw.isdigit() else None

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
            # Account for already-pending spends — they don't deduct xp_spent until
            # approval, so without this a player could queue spends totalling more
            # than their available XP and one would silently fail at approval time.
            pending_total = get_pending_spend_total(conn, character_id)
            effective_char = dict(char)
            effective_char["xp_available"] = max(0, char["xp_available"] - pending_total)
            verified_cost, spend_errors = validate_spend(category, current_dots, new_dots, effective_char)
            if spend_errors and pending_total > 0:
                # Make the error explain WHY available is less than they expect
                spend_errors = [
                    e + f" (you have {char['xp_available']} XP earned, {pending_total} already pending review)"
                    if e.startswith("Insufficient XP") else e
                    for e in spend_errors
                ]
            errors.extend(spend_errors)

        if category == "Humanity" and not errors:
            ok, hc_err = validate_humanity_conditions(hc_checked)
            if not ok:
                errors.append(hc_err)

        if errors:
            resp = templates.TemplateResponse(
                request, "player/partials/spend_form.html", _spend_ctx()
            )
            _toast(resp, "Please fix the errors below.", "error")
            return resp

        # Auto-detect a dependency chain: if the player didn't pass
        # depends_on but there's already a pending spend for the same
        # (category, trait_name) on this character whose new_dots matches
        # this submission's current_dots, treat this as a chained spend.
        # Lets "Dominate 1→2" + "Dominate 2→3" submitted together resolve
        # in order without staff sequencing.
        if depends_on is None:
            parent = conn.execute(
                "SELECT id, new_dots FROM spend_requests "
                "WHERE character_id=? AND category=? AND trait_name=? "
                "AND status='pending' AND new_dots=? "
                "ORDER BY id DESC LIMIT 1",
                (character_id, category, trait_name, current_dots),
            ).fetchone()
            if parent:
                depends_on = parent["id"]

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
            depends_on=depends_on,
        )

        # Refresh character for updated XP display
        char = get_character_for_player(conn, character_id, user["id"])

    resp = templates.TemplateResponse(
        request, "player/partials/spend_form.html",
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
        sites = list_hunting_sites(conn)

    return templates.TemplateResponse(
        request, "player/coteries.html",
        _ctx(
            request,
            coterie=coterie,
            members=members,
            spends=spends,
            eligible_chars=eligible,
            roster=roster,
            hunting_sites=sites,
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
    members_acquainted = form.get("members_acquainted") == "on"
    requested_site_id  = form_int(form.get("requested_site_id")) or None
    # member_ids: character IDs the player wants in the coterie (JSON array from hidden input)
    raw_ids       = (form.get("member_ids") or "").strip()

    errors: list[str] = []

    if not proposed_name:
        errors.append("A coterie name is required.")
    if not members_acquainted:
        errors.append("Please confirm your characters know and have met each other.")

    member_ids: list[int] = []
    if raw_ids:
        try:
            import json as _json
            member_ids = [int(x) for x in _json.loads(raw_ids)]
        except Exception:
            errors.append("Invalid member list — please try again.")

    if len(member_ids) > settings.COTERIE_MAX_MEMBERS:
        errors.append(
            f"A coterie can have at most {settings.COTERIE_MAX_MEMBERS} members."
        )

    if errors:
        with get_db() as conn:
            sites = list_hunting_sites(conn)
        resp = templates.TemplateResponse(
            request, "player/partials/coterie_request_form.html",
            _ctx(request, request_errors=errors,
                 form={"proposed_name": proposed_name, "note": note},
                 hunting_sites=sites),
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
            members_acquainted=members_acquainted,
            requested_site_id=requested_site_id,
        )

    resp = templates.TemplateResponse(
        request, "player/partials/coterie_request_form.html",
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
        ctx = _coterie_detail_ctx(conn, coterie_id, viewer_discord_id=user["id"])
        # Membership gate — only a member can view the detail page.
        if not ctx["viewer_member_chars"]:
            raise HTTPException(status_code=403, detail="Not a member of this coterie")

    return templates.TemplateResponse(
        request, "player/coterie_detail.html",
        _ctx(request, **ctx),
    )


def _coterie_detail_ctx(conn, coterie_id: int, viewer_discord_id: str | None = None) -> dict:
    """Shared context for player coterie detail responses. Computes
    single-funder advance costs, pulls members + spends + contributions,
    and (when given a viewer) flags which member chars the viewer owns so
    the template can show advance/donate/cancel buttons."""
    from ..db import (
        list_coterie_contributions, COTERIE_NAMED_TRAIT_CAP,
        member_free_dots_used, CREATION_FREE_DOTS_PER_MEMBER,
        coterie_free_budget,
    )
    coterie = get_coterie(conn, coterie_id)
    members = list_coterie_members(conn, coterie_id)
    spends  = list_coterie_spends(conn, coterie_id)

    # Free creation dots per member — only meaningful while the coterie is
    # still 'forming'. Surfaced for ALL members so a single assembler can
    # allocate on everyone's behalf (how coteries are built in practice).
    coterie_free_dots: list[dict] = []
    if coterie and coterie["creation_state"] == "forming":
        for m in members:
            used = member_free_dots_used(conn, coterie_id, m["character_id"])
            coterie_free_dots.append({
                "char_id":   m["character_id"],
                "char_name": m["character_name"],
                "used":      used,
                "left":      max(0, CREATION_FREE_DOTS_PER_MEMBER - used),
            })

    # Coterie creation budget (forming only): 2/member base + flaw bonus.
    free_budget = coterie_free_budget(conn, coterie_id) if (
        coterie and coterie["creation_state"] == "forming") else None

    # Single-funder advance costs — what one member pays personally for
    # +1 Chasse/Lien/Portillon. Uses the "Advantage" rule (flat 3/dot)
    # because the Steward rules say C/L/P are treated the same as any
    # merit/background/advantage.
    advance_costs: dict[str, dict] = {}
    if coterie:
        for trait, current_val in [("chasse",    coterie["chasse"]),
                                    ("lien",      coterie["lien"]),
                                    ("portillon", coterie["portillon"])]:
            next_dot = current_val + 1
            if next_dot > 5:
                continue
            cost, err = _calculate_cost("Advantage", current_val, next_dot)
            blocked = err
            if trait == "portillon" and next_dot > coterie["chasse"]:
                blocked = f"Portillon can't exceed Chasse ({coterie['chasse']})."
            advance_costs[trait] = {
                "next_dot": next_dot,
                "cost": cost,
                "blocked": blocked,
            }

    # Active contributions, grouped for display ("who paid for what?")
    active_contribs = list_coterie_contributions(
        conn, coterie_id, status="active",
    ) if coterie else []

    # Pull all contributions (including suspended/removed) for the staff
    # audit view; the player UI just shows active.
    all_contribs = list_coterie_contributions(
        conn, coterie_id, status=None,
    ) if coterie else []

    coterie_flaws = [c for c in active_contribs if c["target_kind"] == "flaw"]

    # Viewer's member chars — for "commit my share" / "cancel" buttons,
    # and to surface their donatable merits.
    viewer_member_chars: list[dict] = []
    viewer_donatable: list[dict] = []
    if viewer_discord_id:
        member_char_ids = {m["character_id"] for m in members}
        for c in list_player_characters(conn, viewer_discord_id):
            if c["id"] in member_char_ids:
                viewer_member_chars.append(c)
                full = get_character(conn, c["id"])
                sheet = (full or {}).get("sheet_json") or {}
                # Collect every merit/background/advantage on the sheet
                # that has >=1 dot. Show even shared ones (Steward UX
                # decision — players can re-donate to a second coterie).
                for list_key, kind_label in [
                    ("advantages",  "advantage"),
                    ("merits",      "merit"),
                    ("backgrounds", "background"),
                ]:
                    for entry in (sheet.get(list_key) or []):
                        if not isinstance(entry, dict):
                            continue
                        name = str(entry.get("name", "")).strip()
                        try:
                            dots = int(entry.get("dots", 0))
                        except (TypeError, ValueError):
                            dots = 0
                        if not name or dots < 1:
                            continue
                        already = coterie_id in (
                            entry.get("shared_with_coteries") or []
                        )
                        viewer_donatable.append({
                            "char_id":      c["id"],
                            "char_name":    c["name"],
                            "list_key":     list_key,
                            "kind":         "merit" if list_key == "merits"
                                            else ("background" if list_key == "backgrounds"
                                                  else "merit"),
                            "name":         name,
                            "dots":         dots,
                            "already_shared": already,
                        })

    return {"coterie": coterie, "members": members, "spends": spends,
            "advance_costs": advance_costs,
            "active_contribs": active_contribs,
            "all_contribs": all_contribs,
            "named_trait_cap": COTERIE_NAMED_TRAIT_CAP,
            "viewer_member_chars": viewer_member_chars,
            "viewer_donatable": viewer_donatable,
            "coterie_free_dots": coterie_free_dots,
            "free_dots_per_member": CREATION_FREE_DOTS_PER_MEMBER,
            "free_budget": free_budget,
            "coterie_flaws": coterie_flaws}


def _resolve_member_char(player_chars: list[dict], members: list[dict],
                         requested_id: int | None) -> dict | None:
    """Pick the character a player is acting as inside this coterie.
    Honors an explicit character_id selection if it's valid; otherwise
    auto-picks the player's first coterie-member character."""
    member_char_ids = {m["character_id"] for m in members}
    if requested_id:
        for c in player_chars:
            if c["id"] == requested_id and c["id"] in member_char_ids:
                return c
    # Fallback: first member char this player owns.
    for c in player_chars:
        if c["id"] in member_char_ids:
            return c
    return None


@router.post("/coteries/{coterie_id}/spends/{spend_id}/cancel", response_class=HTMLResponse)
async def cancel_coterie_spend_route(
    request: Request,
    coterie_id: int,
    spend_id: int,
    user: dict = Depends(require_auth),
    _: None = Depends(csrf_protect),
):
    """The initiator can withdraw their proposal as long as staff
    hasn't approved it yet. Other members can't cancel someone else's
    proposal — staff handles those cases via reject."""
    from ..db import cancel_coterie_spend as _cancel
    with get_db() as conn:
        coterie = get_coterie(conn, coterie_id)
        if not coterie:
            raise HTTPException(status_code=404)
        player_chars = list_player_characters(conn, user["id"])

        spend = get_coterie_spend(conn, spend_id)
        if spend is None or spend["coterie_id"] != coterie_id:
            raise HTTPException(status_code=404)

        # Must be the initiator's owner
        initiator_id = int(spend["initiated_by"]) if (spend.get("initiated_by") and str(spend["initiated_by"]).isdigit()) else None
        if initiator_id is None or not any(c["id"] == initiator_id for c in player_chars):
            raise HTTPException(status_code=403, detail="Only the proposer can cancel.")

        flash_kind, flash_msg = "info", "Proposal cancelled."
        try:
            _cancel(conn, spend_id, initiator_id)
        except ValueError as e:
            flash_kind, flash_msg = "error", str(e)

        ctx = _coterie_detail_ctx(conn, coterie_id, viewer_discord_id=user["id"])

    resp = templates.TemplateResponse(
        request, "player/coterie_detail.html",
        _ctx(request, **ctx),
    )
    _toast(resp, flash_msg, flash_kind)
    return resp


# ── New single-funder coterie spends (advance / personal-XP / donate) ────────

# Per Steward direction (2026-05): C/L/P advancement, coterie merits, and
# donations are all funded by ONE member each rather than the equal-split
# group-buy of the legacy domain spend flow. These three routes share a
# helper for the response — they all re-render the player coterie detail
# page with a flash toast.

def _coterie_flash_response(request, conn, coterie_id, user_id,
                            flash_msg, flash_kind="success"):
    """Re-render the player coterie detail with a flash toast.
    Caches `_coterie_detail_ctx` so callers don't have to know the
    template's full context shape."""
    ctx = _coterie_detail_ctx(conn, coterie_id, viewer_discord_id=user_id)
    resp = templates.TemplateResponse(
        request, "player/coterie_detail.html",
        _ctx(request, **ctx),
    )
    _toast(resp, flash_msg, flash_kind)
    return resp


@router.post("/coteries/{coterie_id}/free-dots", response_class=HTMLResponse)
async def submit_coterie_free_dots(
    request: Request,
    coterie_id: int,
    user: dict = Depends(require_auth),
    _: None = Depends(csrf_protect),
):
    """Allocate free creation dots on a coterie trait — Domain (chasse/lien/
    portillon) or a named merit/background. No XP. Any member may assemble the
    sheet, allocating on behalf of any member (capped 2/member), so one person
    can build it all; only valid while the coterie is 'forming'."""
    from ..db import commit_free_creation_dots
    form        = await request.form()
    char_id     = form_int(form.get("character_id"))
    target_kind = (form.get("target_kind") or "").strip().lower()
    target_name = (form.get("target_name") or "").strip() or None
    dots        = form_int(form.get("dots"), 1, lo=1, hi=2)

    with get_db() as conn:
        coterie = get_coterie(conn, coterie_id)
        if not coterie:
            raise HTTPException(status_code=404)
        members    = list_coterie_members(conn, coterie_id)
        member_ids = {m["character_id"] for m in members}
        owned      = {c["id"] for c in list_player_characters(conn, user["id"])}
        # The actor must own a member of this coterie to assemble its sheet.
        if not (member_ids & owned):
            raise HTTPException(status_code=403)
        # Dots are attributed to the chosen member (any member — one assembler
        # can fill in everyone's). Default to the actor's own member char.
        if char_id not in member_ids:
            char_id = next(iter(member_ids & owned))

        flash_kind, flash_msg = "success", "Free dots allocated."
        try:
            commit_free_creation_dots(
                conn, coterie_id=coterie_id, character_id=char_id,
                target_kind=target_kind, target_name=target_name, dots=dots,
            )
            label = target_name or target_kind.title()
            flash_msg = f"{dots} free dot(s) → {label}."
        except ValueError as e:
            flash_kind, flash_msg = "error", str(e)

        ctx = _coterie_detail_ctx(conn, coterie_id, viewer_discord_id=user["id"])

    resp = templates.TemplateResponse(
        request, "player/coterie_detail.html", _ctx(request, **ctx),
    )
    _toast(resp, flash_msg, flash_kind)
    return resp


@router.post("/coteries/{coterie_id}/flaws", response_class=HTMLResponse)
async def submit_coterie_flaw(
    request: Request,
    coterie_id: int,
    user: dict = Depends(require_auth),
    _: None = Depends(csrf_protect),
):
    """Add a coterie flaw while forming (any member). Each flaw dot grants +1
    Advantage/Background creation dot, capped at 4 flaw dots total."""
    from ..db import commit_coterie_flaw
    form      = await request.form()
    flaw_name = (form.get("flaw_name") or "").strip()
    dots      = form_int(form.get("dots"), 1, lo=1, hi=4)

    with get_db() as conn:
        coterie = get_coterie(conn, coterie_id)
        if not coterie:
            raise HTTPException(status_code=404)
        members    = list_coterie_members(conn, coterie_id)
        member_ids = {m["character_id"] for m in members}
        owned      = {c["id"] for c in list_player_characters(conn, user["id"])}
        if not (member_ids & owned):
            raise HTTPException(status_code=403)

        flash_kind, flash_msg = "success", "Flaw added."
        try:
            commit_coterie_flaw(conn, coterie_id=coterie_id, flaw_name=flaw_name, dots=dots)
            flash_msg = f"Flaw “{flaw_name}” added — +{dots} Advantage/Background dot(s)."
        except ValueError as e:
            flash_kind, flash_msg = "error", str(e)
        ctx = _coterie_detail_ctx(conn, coterie_id, viewer_discord_id=user["id"])

    resp = templates.TemplateResponse(
        request, "player/coterie_detail.html", _ctx(request, **ctx),
    )
    _toast(resp, flash_msg, flash_kind)
    return resp


@router.post("/coteries/{coterie_id}/creation/{contribution_id}/remove", response_class=HTMLResponse)
async def remove_coterie_creation_entry(
    request: Request,
    coterie_id: int,
    contribution_id: int,
    user: dict = Depends(require_auth),
    _: None = Depends(csrf_protect),
):
    """Undo a free-dot or flaw allocation while the coterie is forming (any
    member). Only creation contributions can be removed this way."""
    from ..db import set_contribution_status
    with get_db() as conn:
        coterie = get_coterie(conn, coterie_id)
        if not coterie:
            raise HTTPException(status_code=404)
        members    = list_coterie_members(conn, coterie_id)
        member_ids = {m["character_id"] for m in members}
        owned      = {c["id"] for c in list_player_characters(conn, user["id"])}
        if not (member_ids & owned):
            raise HTTPException(status_code=403)

        flash_kind, flash_msg = "success", "Allocation removed."
        row = conn.execute(
            "SELECT coterie_id, contribution_type FROM coterie_contributions WHERE id=?",
            (contribution_id,),
        ).fetchone()
        if coterie["creation_state"] != "forming":
            flash_kind, flash_msg = "error", "Edits are locked once the sheet is submitted."
        elif (not row or row["coterie_id"] != coterie_id
              or row["contribution_type"] not in ("creation_free", "flaw_bonus")):
            flash_kind, flash_msg = "error", "That isn't a removable creation entry."
        else:
            set_contribution_status(conn, contribution_id, "removed", actor_id=user["id"])
        ctx = _coterie_detail_ctx(conn, coterie_id, viewer_discord_id=user["id"])

    resp = templates.TemplateResponse(
        request, "player/coterie_detail.html", _ctx(request, **ctx),
    )
    _toast(resp, flash_msg, flash_kind)
    return resp


@router.post("/coteries/{coterie_id}/submit-sheet", response_class=HTMLResponse)
async def submit_coterie_sheet_route(
    request: Request,
    coterie_id: int,
    user: dict = Depends(require_auth),
    _: None = Depends(csrf_protect),
):
    """Submit the assembled coterie sheet for staff sign-off (forming →
    submitted). Any member may submit (the group has agreed in their cubby)."""
    from ..db import submit_coterie_sheet
    with get_db() as conn:
        coterie = get_coterie(conn, coterie_id)
        if not coterie:
            raise HTTPException(status_code=404)
        members    = list_coterie_members(conn, coterie_id)
        member_ids = {m["character_id"] for m in members}
        owned      = {c["id"] for c in list_player_characters(conn, user["id"])}
        if not (member_ids & owned):
            raise HTTPException(status_code=403)
        flash_kind, flash_msg = "success", "Coterie sheet submitted for staff sign-off."
        try:
            submit_coterie_sheet(conn, coterie_id, user["id"])
        except ValueError as e:
            flash_kind, flash_msg = "error", str(e)
        ctx = _coterie_detail_ctx(conn, coterie_id, viewer_discord_id=user["id"])
    resp = templates.TemplateResponse(
        request, "player/coterie_detail.html", _ctx(request, **ctx),
    )
    _toast(resp, flash_msg, flash_kind)
    return resp


@router.post("/coteries/{coterie_id}/advance", response_class=HTMLResponse)
async def submit_coterie_advance(
    request: Request,
    coterie_id: int,
    user: dict = Depends(require_auth),
    _: None = Depends(csrf_protect),
):
    """One member pays personal XP to bump Chasse / Lien / Portillon by 1.
    Soft-warns staff at approval time if another advance for the same
    rating already exists in the current active period (so the Steward
    can override the once-per-time-skip policy when they want)."""
    form          = await request.form()
    target_kind   = (form.get("target_kind") or "").strip().lower()
    funder_raw    = (form.get("funded_by_character_id") or "").strip()
    funder_id     = int(funder_raw) if funder_raw.isdigit() else 0
    justification = (form.get("justification") or "").strip() or None

    with get_db() as conn:
        coterie = get_coterie(conn, coterie_id)
        if not coterie:
            raise HTTPException(status_code=404)

        members      = list_coterie_members(conn, coterie_id)
        player_chars = list_player_characters(conn, user["id"])
        funder       = _resolve_member_char(player_chars, members, funder_id)
        if funder is None:
            return _coterie_flash_response(
                request, conn, coterie_id, user["id"],
                "Pick one of your characters who is a member of this coterie.",
                "error",
            )

        ok, err = validate_coterie_advance(conn, coterie_id, target_kind, 1)
        if not ok:
            return _coterie_flash_response(
                request, conn, coterie_id, user["id"], err or "Invalid advance.", "error",
            )

        # Cost: same curve as the matching Advantage rule — flat 3 XP/dot.
        # Pulled from xp_costs.json via calculate_cost() so a Steward edit
        # to that file propagates without code changes.
        current = coterie_effective_rating(conn, coterie_id, target_kind)
        cost, cost_err = _calculate_cost("Advantage", current, current + 1)
        if cost_err:
            return _coterie_flash_response(
                request, conn, coterie_id, user["id"], cost_err, "error",
            )

        # Period-pin for the soft-duplicate check.
        active_period = get_active_period(conn)
        period_id = active_period["id"] if active_period else None

        try:
            create_coterie_single_funder_spend(
                conn,
                coterie_id=coterie_id,
                funded_by_character_id=funder["id"],
                contribution_type="timeskip_advance",
                target_kind=target_kind,
                target_name=None,
                current_dots=current,
                new_dots=current + 1,
                xp_cost=cost,
                period_id=period_id,
                justification=justification,
            )
        except ValueError as e:
            return _coterie_flash_response(
                request, conn, coterie_id, user["id"], str(e), "error",
            )

        # Soft warning if there's already an advance for this rating in
        # this period. The just-created row IS in the result set — only
        # warn when there's a SECOND one (i.e. count > 1). Staff sees the
        # same flag on the approval card so they can override the
        # once-per-time-skip rule when the RP justifies it.
        warn = ""
        if period_id:
            count = conn.execute(
                "SELECT COUNT(*) AS n FROM coterie_spends "
                "WHERE coterie_id=? AND period_id=? AND trait_name=? "
                "AND contribution_type='timeskip_advance' "
                "AND status IN ('pending','funded','approved')",
                (coterie_id, period_id, target_kind),
            ).fetchone()["n"] or 0
            if count > 1:
                warn = (f" Heads-up: another {target_kind.title()} advance "
                        f"already exists this period — staff will review.")

        return _coterie_flash_response(
            request, conn, coterie_id, user["id"],
            f"{target_kind.title()} +1 requested ({cost} XP from {funder['name']}). "
            f"Waiting on staff approval.{warn}",
        )


@router.post("/coteries/{coterie_id}/buy-trait", response_class=HTMLResponse)
async def submit_coterie_buy_trait(
    request: Request,
    coterie_id: int,
    user: dict = Depends(require_auth),
    _: None = Depends(csrf_protect),
):
    """One member spends personal XP on a NAMED coterie trait — a merit
    (e.g. \"Multilevel Lorekeeping\") or background (e.g. \"Haven\").
    Cap is 3 dots total per named item across all contributors."""
    form          = await request.form()
    target_kind   = (form.get("target_kind") or "merit").strip().lower()
    target_name   = (form.get("target_name") or "").strip()
    funder_raw    = (form.get("funded_by_character_id") or "").strip()
    funder_id     = int(funder_raw) if funder_raw.isdigit() else 0
    try:
        delta = max(1, int(form.get("dots") or 1))
    except ValueError:
        delta = 1
    justification = (form.get("justification") or "").strip() or None

    with get_db() as conn:
        coterie = get_coterie(conn, coterie_id)
        if not coterie:
            raise HTTPException(status_code=404)

        members      = list_coterie_members(conn, coterie_id)
        player_chars = list_player_characters(conn, user["id"])
        funder       = _resolve_member_char(player_chars, members, funder_id)
        if funder is None:
            return _coterie_flash_response(
                request, conn, coterie_id, user["id"],
                "Pick one of your characters who is a member of this coterie.",
                "error",
            )

        if target_kind not in ("merit", "background"):
            return _coterie_flash_response(
                request, conn, coterie_id, user["id"],
                "Trait kind must be merit or background.", "error",
            )

        ok, err = validate_coterie_named_trait(
            conn, coterie_id, target_kind, target_name, delta,
        )
        if not ok:
            return _coterie_flash_response(
                request, conn, coterie_id, user["id"],
                err or "Invalid trait.", "error",
            )

        current = coterie_effective_rating(conn, coterie_id, target_kind, target_name)
        cost, cost_err = _calculate_cost("Advantage", current, current + delta)
        if cost_err:
            return _coterie_flash_response(
                request, conn, coterie_id, user["id"], cost_err, "error",
            )

        try:
            create_coterie_single_funder_spend(
                conn,
                coterie_id=coterie_id,
                funded_by_character_id=funder["id"],
                contribution_type="paid_xp",
                target_kind=target_kind,
                target_name=target_name,
                current_dots=current,
                new_dots=current + delta,
                xp_cost=cost,
                justification=justification,
            )
        except ValueError as e:
            return _coterie_flash_response(
                request, conn, coterie_id, user["id"], str(e), "error",
            )

        return _coterie_flash_response(
            request, conn, coterie_id, user["id"],
            f"\"{target_name}\" +{delta} (now {current + delta}/3) requested. "
            f"{cost} XP from {funder['name']} pending staff approval.",
        )


@router.post("/coteries/{coterie_id}/donate", response_class=HTMLResponse)
async def submit_coterie_donate(
    request: Request,
    coterie_id: int,
    user: dict = Depends(require_auth),
    _: None = Depends(csrf_protect),
):
    """Member transfers an existing sheet merit/background to the coterie
    \"as shared\". No XP cost. On approval the sheet entry gets a
    `shared_with_coteries=[coterie_id]` flag — the player keeps the trait
    on their sheet, and the coterie gains the same dots."""
    form        = await request.form()
    target_kind = (form.get("target_kind") or "merit").strip().lower()
    target_name = (form.get("target_name") or "").strip()
    funder_raw  = (form.get("funded_by_character_id") or "").strip()
    funder_id   = int(funder_raw) if funder_raw.isdigit() else 0
    try:
        dots = max(1, int(form.get("dots") or 1))
    except ValueError:
        dots = 1
    justification = (form.get("justification") or "").strip() or None

    with get_db() as conn:
        coterie = get_coterie(conn, coterie_id)
        if not coterie:
            raise HTTPException(status_code=404)

        members      = list_coterie_members(conn, coterie_id)
        player_chars = list_player_characters(conn, user["id"])
        funder       = _resolve_member_char(player_chars, members, funder_id)
        if funder is None:
            return _coterie_flash_response(
                request, conn, coterie_id, user["id"],
                "Pick one of your characters who is a member of this coterie.",
                "error",
            )

        if target_kind not in ("merit", "background"):
            return _coterie_flash_response(
                request, conn, coterie_id, user["id"],
                "Donation target must be a merit or background.", "error",
            )
        if not target_name:
            return _coterie_flash_response(
                request, conn, coterie_id, user["id"],
                "Pick the merit / background to donate.", "error",
            )

        # Verify the funder actually has this trait at the claimed dots
        # on their sheet — donations can't fabricate dots out of thin air.
        char = get_character(conn, funder["id"])
        sheet = (char or {}).get("sheet_json") or {}
        has_it = False
        for list_key in ("advantages", "merits", "backgrounds"):
            for entry in (sheet.get(list_key) or []):
                if (isinstance(entry, dict)
                        and str(entry.get("name", "")).casefold() == target_name.casefold()
                        and int(entry.get("dots", 0)) >= dots):
                    has_it = True
                    break
            if has_it:
                break
        if not has_it:
            return _coterie_flash_response(
                request, conn, coterie_id, user["id"],
                f"{funder['name']} doesn't have \"{target_name}\" at {dots}+ on their sheet.",
                "error",
            )

        # The donation still respects the 3-dot coterie cap.
        ok, err = validate_coterie_named_trait(
            conn, coterie_id, target_kind, target_name, dots,
        )
        if not ok:
            return _coterie_flash_response(
                request, conn, coterie_id, user["id"], err or "Invalid donation.", "error",
            )

        try:
            create_coterie_single_funder_spend(
                conn,
                coterie_id=coterie_id,
                funded_by_character_id=funder["id"],
                contribution_type="donated",
                target_kind=target_kind,
                target_name=target_name,
                current_dots=0,
                new_dots=dots,
                xp_cost=0,
                justification=justification or f"Donated from {funder['name']}'s sheet.",
            )
        except ValueError as e:
            return _coterie_flash_response(
                request, conn, coterie_id, user["id"], str(e), "error",
            )

        return _coterie_flash_response(
            request, conn, coterie_id, user["id"],
            f"\"{target_name}\" ({dots} dots) donation queued. Pending staff approval.",
        )


# ── Chronicle Map ────────────────────────────────────────────────────────────

@router.get("/map", response_class=HTMLResponse)
async def map_view(request: Request, user: dict = Depends(require_auth)):
    """Player-facing chronicle map. Shows every layer marked
    visibility='public' plus the non-hidden features inside them. The
    Leaflet JS fetches geometry asynchronously from /map/data.json."""
    return templates.TemplateResponse(
        request, "player/map.html", _ctx(request),
    )


@router.get("/map/data.json")
async def map_data_player(request: Request, user: dict = Depends(require_auth)):
    """Return the JSON payload the player map consumes — public layers
    only, with hidden features filtered out. Staff hit /staff/map/data.json
    for the unfiltered view."""
    from ..db import list_map_layers, list_map_features
    from fastapi.responses import JSONResponse

    with get_db() as conn:
        layers = list_map_layers(conn, include_staff_only=False, active_only=True)
        payload_layers = []
        for layer in layers:
            features = list_map_features(conn, layer_id=layer["id"], include_hidden=False)
            payload_layers.append({
                "id":          layer["id"],
                "name":        layer["name"],
                "description": layer.get("description"),
                "color":       layer["color"],
                "sort_order":  layer["sort_order"],
                "features":    [
                    {
                        "id":           f["id"],
                        "label":        f["label"],
                        "description":  f.get("description"),
                        "tag":          f.get("tag"),
                        "feature_type": f["feature_type"],
                        "geometry":     f.get("geometry"),
                    }
                    for f in features
                ],
            })
    return JSONResponse(content={"layers": payload_layers})
