"""player.py — Player-facing pages."""
import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
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
    create_companion,
    list_companions,
    get_companion,
    get_companion_for_player,
    update_companion,
    delete_companion,
    list_familiars,
    get_familiar,
    list_character_familiars,
    bond_familiar,
    unbond_familiar,
    get_character_familiar_for_player,
    set_character_background,
    blank_character_background,
    list_character_backgrounds,
    create_project,
    list_projects_for_character,
    list_projects_for_coterie,
    timeskip_rolls_remaining,
    hunt_downtime,
    list_downtime_actions,
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
from ..deps import csrf_protect, require_auth
from ..main import _ctx
from ..xp_rules import (
    HUMANITY_CONDITIONS,
    RULES,
    SPEND_CATEGORIES,
    validate_humanity_conditions,
    validate_spend,
)
from core.dice import (
    OUTCOME_LABELS,
    apply_specialty,
    blood_surge_bonus,
    build_trait_index,
    probability,
    reroll_indices,
    resolve_pool,
    roll_pool,
    rouse_check,
)
from core.conditions import character_conditions
from core.resonance import roll_resonance

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
        # Distinct areas/zones present — the filter options now come from the
        # data (Area is free text per server), not a fixed list.
        all_areas = sorted({s["borough"] for s in sites if s["borough"]})
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
             boroughs=all_areas,
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
    DISCIPLINE_POWERS as _DISCIPLINE_POWERS,
    MERIT_CATALOG   as _MERIT_CATALOG,
    FLAW_CATALOG    as _FLAW_CATALOG,
    RITUAL_CATALOG  as _RITUAL_CATALOG,
    CEREMONY_CATALOG as _CEREMONY_CATALOG,
    LORESHEET_PICKER as _LORESHEET_PICKER,
    LORESHEET_CATALOG as _LORESHEET_CATALOG,
    LORESHEET_DOT_XP as _LORESHEET_DOT_XP,
    get_loresheet   as _get_loresheet,
    V5_CLAN_INFO    as _V5_CLAN_INFO,
    V5_PREDATOR_INFO as _V5_PREDATOR_INFO,
    V5_SKILL_SPREADS as _V5_SKILL_SPREADS,
    V5_DISCIPLINE_SPREADS as _V5_DISCIPLINE_SPREADS,
    PREDATOR_FREE_DISCIPLINE_DOTS as _PREDATOR_FREE_DISCIPLINE_DOTS,
    CLAN_DISCIPLINES as _CLAN_DISCIPLINES,
    SHEET_TRAIT_KEYS as _SHEET_TRAIT_KEYS,
    SHEET_LIMITS    as _SHEET_LIMITS,
    validate_chargen_raw as _validate_chargen_raw,
    V5_ATTRIBUTE_SPREAD as _V5_ATTRIBUTE_SPREAD,
    MORTAL_TEMPLATES as _MORTAL_TEMPLATES,
    RETAINER_DOTS_TO_TEMPLATE as _RETAINER_DOTS_TO_TEMPLATE,
    validate_retainer_template as _validate_retainer_template,
)

# Make the V5 reference catalogs available to every template render (the chargen
# wizard pickers, and the edit page) without threading them through each call.
templates.env.globals["discipline_powers"] = _DISCIPLINE_POWERS
templates.env.globals["merit_catalog"] = _MERIT_CATALOG
templates.env.globals["flaw_catalog"] = _FLAW_CATALOG
templates.env.globals["ritual_catalog"] = _RITUAL_CATALOG
templates.env.globals["ceremony_catalog"] = _CEREMONY_CATALOG
templates.env.globals["loresheet_picker"] = _LORESHEET_PICKER
templates.env.globals["loresheets_by_id"] = {l["id"]: l for l in _LORESHEET_CATALOG}
templates.env.globals["loresheet_dot_xp"] = _LORESHEET_DOT_XP
# Trait-name autocomplete lists for the XP-spend form, keyed by category type.
templates.env.globals["spend_trait_lists"] = {
    "attribute":  [lbl for _c, _tr in _V5_ATTRIBUTES for _k, lbl in _tr],
    "skill":      [lbl for _c, _tr in _V5_SKILLS for _k, lbl in _tr],
    "discipline": [lbl for _k, lbl in _V5_DISCIPLINES],
    "ritual":     [r["name"] for r in _RITUAL_CATALOG],
    "ceremony":   [c["name"] for c in _CEREMONY_CATALOG],
    "formula":    [p["name"] for p in _DISCIPLINE_POWERS.get("disc_thin_blood_alchemy", [])],
    "advantage":  [m["name"] for m in _MERIT_CATALOG],
}
# Trait name → sheet key, so the spend form can auto-fill Current Dots from the
# character's sheet (attributes / skills / disciplines).
templates.env.globals["spend_trait_keys"] = {
    **{lbl.lower(): k for _c, _tr in _V5_ATTRIBUTES for k, lbl in _tr},
    **{lbl.lower(): k for _c, _tr in _V5_SKILLS for k, lbl in _tr},
    **{lbl.lower(): k for k, lbl in _V5_DISCIPLINES},
}


def _build_spend_trait_ratings() -> dict[str, list[int]]:
    """Valid purchasable dot ratings per advantage (merit/loresheet), keyed by
    lowercased trait name. A single-entry list is a FIXED-rating merit — e.g.
    Cold Dead Hunger ••• → [3] — which the XP-spend form snaps "New Dots" to,
    instead of leaving it at the generic default of 1. Scalable advantages
    (Contacts, loresheets) get their full range so the form can cap them."""
    out: dict[str, list[int]] = {}
    for m in _MERIT_CATALOG:
        nm = (m.get("name") or "").strip().lower()
        cs = sorted({int(c) for c in (m.get("costs") or []) if isinstance(c, (int, float))})
        if nm and cs:
            out[nm] = cs
    for lore in _LORESHEET_CATALOG:
        nm = (lore.get("name") or "").strip().lower()
        if nm and nm not in out:
            mx = max((int(d.get("dot", 0)) for d in (lore.get("dots") or [])), default=5) or 5
            out[nm] = list(range(1, mx + 1))
    return out


templates.env.globals["spend_trait_ratings"] = _build_spend_trait_ratings()
# Outcome labels (one source for the roll result, bot embed, and History tab).
templates.env.globals["outcome_labels"] = OUTCOME_LABELS


# ── Web dice roller (Irad's engine, in the browser) ───────────────────────────
# Trait name → sheet key so a pool like "strength + brawl" resolves from the
# sheet. Mirrors the bot's _TRAIT_INDEX so web and Discord rolls agree.
_WEB_TRAIT_INDEX = build_trait_index(
    [pair for _c, _tr in _V5_ATTRIBUTES for pair in _tr],
    [pair for _c, _tr in _V5_SKILLS for pair in _tr],
    _V5_DISCIPLINES,
)


def _pool_label(parts, total) -> str:
    """Render a pool breakdown like 'Strength 3 + Brawl 2 = 5d'."""
    if not parts:
        return f"{total}d"
    return " + ".join(f"{lbl} {val}" for lbl, val in parts) + f" = {total}d"


def _log_roll_safe(character_id: int, result, label: str | None,
                   *, kind: str = "roll") -> None:
    """Best-effort roll logging (migration 053) — a logging hiccup must never
    fail the roll itself."""
    try:
        from ..db import log_roll
        dice = ",".join(str(d) for d in (result.normal_dice + result.hunger_dice))
        with get_db() as conn:
            log_roll(conn, character_id, kind=kind, pool=result.pool,
                     hunger=result.hunger, difficulty=result.difficulty,
                     successes=result.successes, outcome=result.outcome,
                     label=label, dice=dice)
    except Exception:
        pass


def _post_roll_to_discord(char: dict, result, pool_label: str | None,
                          note: str | None) -> bool:
    """Best-effort web→Discord roll post (migration 054). When the chronicle has
    a dice channel configured, enqueue a 'roll_posted' bot_outbox event so Irad
    posts this result's embed there. Returns True if enqueued. Never raises — a
    posting hiccup must not fail the roll itself."""
    try:
        from ..db import enqueue_bot, get_dice_channel_id
        with get_db() as conn:
            channel_id = get_dice_channel_id(conn)
            if not channel_id:
                return False
            enqueue_bot(conn, "roll_posted", {
                "channel_id":        channel_id,
                "character_name":    char.get("name") or "A character",
                "roller_discord_id": char.get("discord_id"),
                "outcome":           result.outcome,
                "outcome_label":     OUTCOME_LABELS.get(result.outcome, result.outcome),
                "is_win":            bool(result.is_win),
                "successes":         result.successes,
                "difficulty":        result.difficulty,
                "margin":            result.margin,
                "pool":              result.pool,
                "hunger":            result.hunger,
                "normal_dice":       list(result.normal_dice),
                "hunger_dice":       list(result.hunger_dice),
                "pool_label":        pool_label,
                "note":              note,
            })
        return True
    except Exception:
        return False


def _roll_kwargs(char, *, result=None, form=None, parts=None, unknown=None,
                 surge_note=None, reroll_note=None, error=None, pool_label=None,
                 odds=None):
    """Context keys the roll partial needs. Excludes `char` so this composes
    with character_detail's existing context (which passes char itself)."""
    sheet = char.get("sheet_json") or {}

    def _picker_traits(pairs, *, only_owned=False):
        rows = []
        for key, label in pairs:
            dots = int(sheet.get(key) or 0)
            if only_owned and dots <= 0:
                continue
            rows.append({"label": label, "dots": dots})
        return rows

    return {
        "roll_result": result,
        "roll_outcome_label": (OUTCOME_LABELS.get(result.outcome) if result else None),
        "roll_can_reroll": bool(result and not reroll_note
                                and any(d < 6 for d in result.normal_dice)),
        "roll_form": form or {"pool": "", "difficulty": 0, "hunger": "",
                              "modifier": 0, "specialty": "", "surge": False},
        "roll_parts": parts or [],
        "roll_unknown": unknown or [],
        "roll_surge_note": surge_note,
        "roll_reroll_note": reroll_note,
        "roll_error": error,
        "roll_pool_label": pool_label,
        "roll_odds": odds,
        "roll_specialties": [s for s in (sheet.get("specialties") or [])
                             if isinstance(s, dict)],
        "conditions": character_conditions(sheet),
        "roll_current_hunger": int(sheet.get("hunger") or 0),
        "roll_picker": {
            "attributes": _picker_traits(
                [(k, l) for _c, tr in _V5_ATTRIBUTES for k, l in tr]),
            "skill_groups": [{"cat": cat, "traits": _picker_traits(tr)}
                             for cat, tr in _V5_SKILLS],
            "disciplines": _picker_traits(list(_V5_DISCIPLINES), only_owned=True),
        },
    }


def _parse_dice_csv(raw) -> list[int]:
    """Parse a comma-separated dice string ('10,7,3') into ints (1-10). Ignores
    junk and caps length so a crafted reroll POST can't balloon the pool."""
    out: list[int] = []
    for tok in (raw or "").split(","):
        tok = tok.strip()
        if tok.lstrip("-").isdigit():
            out.append(max(1, min(10, int(tok))))
        if len(out) >= 50:
            break
    return out


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
        "in_memoriam_enabled": bool(s.get("in_memoriam_enabled", 0)),
        "creation_mode":      (s.get("creation_mode") or "guided").lower(),
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
    # Background autosave (save-as-you-go): the wizard posts this on step
    # navigation via fetch. It's always a tolerant draft write and returns the
    # draft_id as JSON instead of redirecting, so the page never reloads.
    autosave         = form.get("autosave") == "1"
    if autosave:
        as_draft = True
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
    else:
        # In Memoriam is now an opt-in the player CHOOSES (migration 040). Honor
        # their pick, but fold it back to standard if the chronicle hasn't
        # enabled the Era Builder; default a missing pick to standard.
        if ancilla_mode == "in_memoriam" and not _chronicle_settings().get("in_memoriam_enabled"):
            ancilla_mode = "standard"
        elif ancilla_mode is None:
            ancilla_mode = "standard"
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
        _form = {k: v for k, v in form.items() if isinstance(v, str)}
        # Re-hydrate the wizard's sheet so a validation error doesn't wipe all
        # the player's progress. The Alpine init reads initialForm.sheet (the
        # draft-resume path); rebuild that object from the flat posted fields.
        try:
            _form["sheet"] = _parse_sheet_from_form(form, base={})
        except Exception:
            pass
        return templates.TemplateResponse(
            request, "player/character_create.html",
            _ctx(request, clans=_CLANS, predator_types=_available_predator_types(),
                 covenants=_COVENANTS,
                 v5_attributes=_V5_ATTRIBUTES, v5_skills=_V5_SKILLS,
                 v5_disciplines=_V5_DISCIPLINES,
                 clan_disciplines=_CLAN_DISCIPLINES,
                 errors=errs,
                 form=_form,
                 **_wizard_extras()),
        )

    if errors:
        if autosave:
            return JSONResponse({"ok": False, "errors": errors})
        return _rerender_wizard(errors)

    # Always parse the sheet from the form — drafts preserve what the
    # player typed even if it's incomplete. Final submission re-uses it.
    sheet = _parse_sheet_from_form(form, base={}) if (require_sheet or as_draft) else {}

    # Seed initial V5 stats based on character archetype + tier. Only
    # apply when the player hasn't already typed something (drafts
    # preserve in-progress values; final submission fills in defaults).
    if character_type == "kindred":
        # Sea-of-Time per-tier starting Blood Potency + Humanity. The wizard
        # only PREVIEWS these (they aren't posted), so the route is the
        # authoritative seed — it must match applyTierBloodDefaults() in
        # character_create.html. Older code seeded a flat BP 1 / Humanity 7 for
        # every tier, which understated Ancilla (BP 2 / Humanity 6) and
        # overstated Thin-blood (BP 0).
        _tier_bp       = {"thinblood": 0, "fledgling": 1, "neonate": 1, "ancilla": 2}
        _tier_humanity = {"thinblood": 7, "fledgling": 7, "neonate": 7, "ancilla": 6}
        if "blood_potency" not in sheet:
            if ancilla_mode == "in_memoriam" and im_generation:
                sheet["blood_potency"] = {"12th": 1, "11th-10th": 2, "9th-8th": 3}.get(im_generation, 1)
            else:
                sheet["blood_potency"] = _tier_bp.get(character_tier, 1)
        if "humanity" not in sheet:
            if ancilla_mode == "in_memoriam":
                # The wizard owns the Oceans-of-Time Humanity math (base 7 minus
                # era + embrace-age losses, RAW floor 4). Trust its posted result
                # — staff verify at approval — and clamp to the legal 4-10 band.
                try:
                    _im_h = int(form.get("im_computed_humanity") or 7)
                except (TypeError, ValueError):
                    _im_h = 7
                sheet["humanity"] = max(4, min(10, _im_h))
            else:
                sheet["humanity"] = _tier_humanity.get(character_tier, 7)
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

    # Revenants get their family Bane at Bane Severity 1 (NYbN) — auto-applied
    # free like a clan Bane (src='revenant_bane'); the full bane text lives in
    # the seeded revenant_families data. Strip stale ones first so re-submits
    # don't stack.
    if character_type == "revenant" and revenant_family and (require_sheet or as_draft):
        _rev_flaws = sheet.setdefault("flaws", [])
        _rev_flaws[:] = [f for f in _rev_flaws
                         if not (isinstance(f, dict) and f.get("src") == "revenant_bane")]
        _rev_flaws.append({"name": f"{revenant_family} Bane", "dots": 1,
                           "src": "revenant_bane"})
        sheet["bane_severity"] = 1

    # V5 RAW chargen validation (Standard ruleset only). The base allocation —
    # attributes + skills before starting-XP buys — must follow the priority
    # spreads. Drafts stay tolerant; only full submissions are gated, and only
    # under the standard ruleset (homebrew runs its own tier budgets).
    if not as_draft and require_sheet:
        _settings = _chronicle_settings()
        _creation_mode = (_settings.get("creation_mode") or "guided").lower()
        _ruleset = (_settings.get("active_ruleset") or "standard").lower()
        # Open mode = no enforcement (players just enter their sheet). In Memoriam
        # characters follow the Era rules instead of the Standard spreads, so they
        # skip this Standard RAW check too (IM validation lives in the Era flow).
        if _creation_mode != "open" and _ruleset == "standard" and ancilla_mode != "in_memoriam":
            from ..db import tier_budget
            _bud = tier_budget(_settings, character_tier)
            # Revenants: resolve the family's Disciplines so the validator can
            # enforce the 2-family + 1-domitor split.
            _fam_discs = None
            if character_type == "revenant" and revenant_family:
                _fam_discs = next(
                    (f.get("disciplines")
                     for f in (_settings.get("revenant_families") or [])
                     if f.get("name") == revenant_family), None)
            raw_errors = _validate_chargen_raw(
                sheet, character_type=character_type,
                clan=clan, predator_type=predator_type,
                family_disciplines=_fam_discs,
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

    # Background autosave: return the draft id as JSON (no redirect) so the
    # wizard keeps editing in place and reuses the same draft next time.
    if autosave:
        return JSONResponse({"ok": True, "draft_id": char["id"]})

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
            # Some traits name a target (Contacts of whom, Folkloric Bane object,
            # …) — keep the player's free-text specifics.
            detail = str(it.get("detail", "")).strip()[:60]
            if detail:
                entry["detail"] = detail
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

    # Loresheets — {id, name, dots}; validated against the catalog. Names are
    # canonicalized from the catalog; unknown ids and duplicates are dropped.
    raw = form.get("loresheets")
    if raw is not None:
        try:
            items = json.loads(raw)
        except (ValueError, TypeError):
            items = None
        if isinstance(items, list):
            cleaned_ls: list[dict] = []
            seen_ls: set[tuple] = set()
            for it in items:
                if not isinstance(it, dict):
                    continue
                lid = str(it.get("id", "")).strip()
                ls = _get_loresheet(lid)
                if not ls:
                    continue
                # A loresheet may appear twice — a creation entry and an
                # XP-bought (src='xp') entry — so dedupe on (id, src), not id.
                src = str(it.get("src", "")).strip()[:20]
                if (lid, src) in seen_ls:
                    continue
                seen_ls.add((lid, src))
                # Loresheet entries are independent picks, each costing its own
                # level (non-cumulative). Validate each selected level against
                # the catalog. Fall back to a legacy {dots:N} rating = levels 1..N.
                valid_levels = {int(d["dot"]) for d in ls.get("dots", [])}
                raw_levels = it.get("levels")
                if raw_levels is None and it.get("dots"):
                    try:
                        raw_levels = list(range(1, int(it["dots"]) + 1))
                    except (ValueError, TypeError):
                        raw_levels = []
                levels = sorted({
                    int(x) for x in (raw_levels or [])
                    if isinstance(x, (int, float)) and int(x) in valid_levels
                })
                if not levels:          # no entries selected → nothing to store
                    continue
                entry = {"id": lid, "name": ls["name"], "levels": levels}
                if src:   # src='xp' → bought with XP, not the creation pool
                    entry["src"] = src
                cleaned_ls.append(entry)
            sheet["loresheets"] = cleaned_ls

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
        _sync_backgrounds_from_sheet(conn, char)
        backgrounds     = list_character_backgrounds(conn, character_id)
        companions      = list_companions(conn, character_id)
        char_familiars  = list_character_familiars(conn, character_id)
        projects        = list_projects_for_character(conn, character_id)
        proj_rolls      = timeskip_rolls_remaining(conn, character_id)
        downtime_hunts  = (list_downtime_actions(conn, character_id, proj_rolls["period_id"], "hunt")
                           if proj_rolls["period_id"] else [])
        from ..db import list_character_rolls, macros_from_sheet, roll_outcome_stats
        rolls           = list_character_rolls(conn, character_id, limit=10)
        roll_stats      = roll_outcome_stats(conn, character_id)
        macros          = macros_from_sheet(char.get("sheet_json") or {})

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
            companions=companions,
            char_familiars=char_familiars,
            projects=projects,
            proj_rolls=proj_rolls,
            downtime_hunts=downtime_hunts,
            rolls=rolls,
            roll_stats=roll_stats,
            macros=macros,
            spend_categories=SPEND_CATEGORIES,
            spend_rules_json=json.dumps(RULES),
            humanity_conditions=HUMANITY_CONDITIONS,
            v5_attributes=_V5_ATTRIBUTES,
            v5_skills=_V5_SKILLS,
            v5_disciplines=_V5_DISCIPLINES,
            active_bane=_active_clan_bane(
                char.get("clan"), (char.get("sheet_json") or {}).get("bane_choice")),
            clan_disciplines=set(_CLAN_DISCIPLINES.get(char["clan"], [])),
            resonance_result=None,
            **_roll_kwargs(char),
        ),
    )


@router.post("/characters/{character_id}/roll", response_class=HTMLResponse)
async def roll_dice(
    request: Request,
    character_id: int,
    user: dict = Depends(require_auth),
    _: None = Depends(csrf_protect),
):
    """Web V5 roller — reuses the shared engine. Read-only except that a Blood
    Surge persists its Rouse's Hunger gain to the sheet, like the bot does."""
    form       = await request.form()
    pool_expr  = (form.get("pool") or "").strip()
    difficulty = form_int(form.get("difficulty"))
    hunger_raw = (form.get("hunger") or "").strip()
    specialty  = (form.get("specialty") or "").strip() or None
    surge      = form.get("surge") == "on"

    with get_db() as conn:
        char = get_character_for_player(conn, character_id, user["id"])
        if not char:
            raise HTTPException(status_code=404)
    sheet = char.get("sheet_json") or {}
    # A saved macro fills pool / difficulty / Hunger / Surge for this roll.
    macro_name = (form.get("macro") or "").strip()
    if macro_name:
        from ..db import get_macro
        m = get_macro(sheet, macro_name)
        if m:
            pool_expr, difficulty, hunger_raw, surge = (
                m["pool"], m["difficulty"], m["hunger"], m["surge"])
    modifier = form_int(form.get("modifier"))

    form_state = {"pool": pool_expr, "difficulty": difficulty, "hunger": hunger_raw,
                  "modifier": modifier, "specialty": specialty or "", "surge": surge}

    if not pool_expr:
        return templates.TemplateResponse(
            request, "player/partials/roll_form.html",
            _ctx(request, char=char, **_roll_kwargs(
                char, form=form_state,
                error="Enter a pool — a number (5) or traits like 'strength + brawl'.")),
        )

    if pool_expr.isdigit():
        # A flat number needs no breakdown — _pool_label renders just "Nd".
        total, parts, unknown = int(pool_expr), [], []
    else:
        total, parts, unknown = resolve_pool(pool_expr, sheet, _WEB_TRAIT_INDEX)

    total, parts, unknown = apply_specialty(total, parts, unknown, specialty,
                                            sheet.get("specialties"))
    if modifier:
        total = max(0, total + modifier)
        parts = parts + [("Modifier", modifier)]

    eff_hunger = int(hunger_raw) if hunger_raw.isdigit() else int(sheet.get("hunger") or 0)

    surge_note = None
    if surge:
        bonus = blood_surge_bonus(sheet.get("blood_potency", 0))
        total += bonus
        parts = parts + [("Blood Surge", bonus)]
        rolls, gained = rouse_check(1)
        new_hunger = min(5, eff_hunger + gained)
        if gained:
            # The Surge's Rouse raised Hunger — persist it, like the bot does.
            from ..db import apply_character_state_delta
            with get_db() as conn:
                st = apply_character_state_delta(conn, character_id, hunger=gained)
            if st:
                new_hunger = st["hunger"]
        eff_hunger = new_hunger   # reflect the Rouse in this roll's Hunger dice
        rouse_txt = (f"+{gained} Hunger → {new_hunger}/5 (saved to your sheet)"
                     if gained else "no Hunger gained")
        surge_note = (f"+{bonus} {'die' if bonus == 1 else 'dice'} · Rouse "
                      + " · ".join(str(d) for d in rolls) + f" → {rouse_txt}")

    result = roll_pool(total, eff_hunger, difficulty)
    pool_label = _pool_label(parts, result.pool)
    _log_roll_safe(character_id, result, pool_label, kind="roll")
    # Optional web→Discord post (migration 054) — only when opted in AND the
    # chronicle has a dice channel set; best-effort, never fails the roll.
    posted = (form.get("post_discord") == "on"
              and _post_roll_to_discord(char, result, pool_label, surge_note))
    return templates.TemplateResponse(
        request, "player/partials/roll_form.html",
        _ctx(request, char=char, roll_posted=posted, **_roll_kwargs(
            char, result=result, form=form_state, parts=parts, unknown=unknown,
            surge_note=surge_note, pool_label=pool_label)),
    )


@router.post("/characters/{character_id}/roll/reroll", response_class=HTMLResponse)
async def reroll_dice(
    request: Request,
    character_id: int,
    user: dict = Depends(require_auth),
    _: None = Depends(csrf_protect),
):
    """Willpower reroll of the regular failures from a prior web roll. The dice
    ride in on hidden fields (the roller is stateless). v1 is read-only — it
    flags the -1 Superficial Willpower cost rather than writing it."""
    form       = await request.form()
    normal     = _parse_dice_csv(form.get("normal"))
    hunger     = _parse_dice_csv(form.get("hunger"))
    difficulty = form_int(form.get("difficulty"))
    pool_label = (form.get("pool_label") or "").strip() or None
    pool_expr  = (form.get("pool") or "").strip()
    indices_raw = (form.get("indices") or "").strip()
    indices = ([int(x) for x in indices_raw.split(",") if x.strip().isdigit()]
               if indices_raw else None)

    with get_db() as conn:
        char = get_character_for_player(conn, character_id, user["id"])
        if not char:
            raise HTTPException(status_code=404)

    result, n = reroll_indices(normal, hunger, difficulty, indices)
    _log_roll_safe(character_id, result, pool_label, kind="reroll")
    note = (f"Willpower reroll — rerolled {n} {'die' if n == 1 else 'dice'} "
            "(costs 1 Superficial Willpower — mark it on your sheet).")
    form_state = {"pool": pool_expr, "difficulty": difficulty, "hunger": "",
                  "specialty": "", "surge": False}
    return templates.TemplateResponse(
        request, "player/partials/roll_form.html",
        _ctx(request, char=char, **_roll_kwargs(
            char, result=result, form=form_state, reroll_note=note,
            pool_label=pool_label)),
    )


@router.post("/characters/{character_id}/roll/odds", response_class=HTMLResponse)
async def roll_odds(
    request: Request,
    character_id: int,
    user: dict = Depends(require_auth),
    _: None = Depends(csrf_protect),
):
    """Probability preview for the current pool — simulates the odds without
    rolling or writing anything to the sheet."""
    form       = await request.form()
    pool_expr  = (form.get("pool") or "").strip()
    difficulty = form_int(form.get("difficulty"))
    hunger_raw = (form.get("hunger") or "").strip()
    specialty  = (form.get("specialty") or "").strip() or None
    surge      = form.get("surge") == "on"

    with get_db() as conn:
        char = get_character_for_player(conn, character_id, user["id"])
        if not char:
            raise HTTPException(status_code=404)
    sheet = char.get("sheet_json") or {}
    modifier = form_int(form.get("modifier"))
    form_state = {"pool": pool_expr, "difficulty": difficulty, "hunger": hunger_raw,
                  "modifier": modifier, "specialty": specialty or "", "surge": surge}

    if not pool_expr:
        return templates.TemplateResponse(
            request, "player/partials/roll_form.html",
            _ctx(request, char=char, **_roll_kwargs(
                char, form=form_state, error="Enter a pool to preview its odds.")),
        )

    if pool_expr.isdigit():
        total, parts, unknown = int(pool_expr), [], []
    else:
        total, parts, unknown = resolve_pool(pool_expr, sheet, _WEB_TRAIT_INDEX)
    total, parts, unknown = apply_specialty(total, parts, unknown, specialty,
                                            sheet.get("specialties"))
    if modifier:
        total = max(0, total + modifier)
        parts = parts + [("Modifier", modifier)]
    if surge:
        bonus = blood_surge_bonus(sheet.get("blood_potency", 0))
        total += bonus
        parts = parts + [("Blood Surge", bonus)]
    eff_hunger = int(hunger_raw) if hunger_raw.isdigit() else int(sheet.get("hunger") or 0)

    odds = probability(total, eff_hunger, difficulty)
    pool_label = _pool_label(parts, odds["pool"])
    # Record the odds check in history, marked as kind='odds' (not a real roll).
    try:
        from ..db import log_roll
        with get_db() as conn:
            log_roll(conn, character_id, kind="odds", pool=odds["pool"],
                     hunger=odds["hunger"], difficulty=odds["difficulty"],
                     successes=round(odds["p_success"] * 100), outcome="odds",
                     label=pool_label)
    except Exception:
        pass
    return templates.TemplateResponse(
        request, "player/partials/roll_form.html",
        _ctx(request, char=char, **_roll_kwargs(
            char, form=form_state, parts=parts, unknown=unknown,
            pool_label=pool_label, odds=odds)),
    )


@router.post("/characters/{character_id}/resonance", response_class=HTMLResponse)
async def roll_resonance_route(
    request: Request,
    character_id: int,
    user: dict = Depends(require_auth),
    _: None = Depends(csrf_protect),
):
    """Generate a random V5 blood Resonance + Temperament. Stateless — owner-
    gated for consistency with the other Roll-tab tools."""
    from ..db import get_resonance_mode
    with get_db() as conn:
        char = get_character_for_player(conn, character_id, user["id"])
        if not char:
            raise HTTPException(status_code=404)
        mode = get_resonance_mode(conn)
    return templates.TemplateResponse(
        request, "player/partials/resonance_card.html",
        _ctx(request, char=char, resonance_result=roll_resonance(mode)),
    )


def _macros_ctx(request: Request, char: dict, *, error: str | None = None):
    from ..db import macros_from_sheet
    return _ctx(request, char=char,
                macros=macros_from_sheet(char.get("sheet_json") or {}),
                macro_error=error)


@router.post("/characters/{character_id}/macros", response_class=HTMLResponse)
async def save_macro_route(
    request: Request,
    character_id: int,
    user: dict = Depends(require_auth),
    _: None = Depends(csrf_protect),
):
    """Create or update a rich roll macro on the character's sheet."""
    from ..db import save_character_macro
    form  = await request.form()
    name  = (form.get("name") or "").strip()
    pool  = (form.get("pool") or "").strip()
    error = None
    with get_db() as conn:
        char = get_character_for_player(conn, character_id, user["id"])
        if not char:
            raise HTTPException(status_code=404)
        if not name or not pool:
            error = "Both a name and a pool are required."
        elif save_character_macro(
                conn, character_id, name, pool=pool,
                difficulty=form_int(form.get("difficulty")),
                hunger=(form.get("hunger") or "").strip(),
                surge=form.get("surge") == "on",
                comment=(form.get("comment") or "").strip()) is None:
            error = "Macro limit reached (25)."
        char = get_character_for_player(conn, character_id, user["id"])
    return templates.TemplateResponse(
        request, "player/partials/macros_card.html",
        _macros_ctx(request, char, error=error))


@router.post("/characters/{character_id}/macros/delete", response_class=HTMLResponse)
async def delete_macro_route(
    request: Request,
    character_id: int,
    user: dict = Depends(require_auth),
    _: None = Depends(csrf_protect),
):
    """Delete a roll macro from the character's sheet."""
    from ..db import delete_character_macro
    form = await request.form()
    name = (form.get("name") or "").strip()
    with get_db() as conn:
        char = get_character_for_player(conn, character_id, user["id"])
        if not char:
            raise HTTPException(status_code=404)
        if name:
            delete_character_macro(conn, character_id, name)
        char = get_character_for_player(conn, character_id, user["id"])
    return templates.TemplateResponse(
        request, "player/partials/macros_card.html",
        _macros_ctx(request, char))


def _find_draft_claim(claims: list[dict], period_id: int) -> dict | None:
    """Return the current open draft claim for this period, if any."""
    for c in claims:
        if c["play_period_id"] == period_id and c["status"] == "draft":
            return c
    return None


# ── Background blanking ───────────────────────────────────────────────────────

def _sheet_backgrounds(char: dict) -> list[dict]:
    """The character's backgrounds drawn from their sheet — the `backgrounds`
    array plus any merits/advantages the catalog classifies as a Background
    (Haven, Resources, Herd, Mentor, …). Deduped by name; highest dots win."""
    sheet = (char.get("sheet_json") or {})
    bg_names = {
        (m.get("name") or "").strip().lower()
        for m in _MERIT_CATALOG if m.get("kind") == "background"
    }
    agg: dict[str, dict] = {}
    for list_key in ("backgrounds", "advantages", "merits"):
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
            # Backgrounds array = always a background; merits/advantages only
            # when the catalog classifies the name as one.
            if list_key != "backgrounds" and name.lower() not in bg_names:
                continue
            key = name.lower()
            if key not in agg or dots > agg[key]["dots"]:
                agg[key] = {"name": name, "dots": dots}
    return list(agg.values())


def _sync_backgrounds_from_sheet(conn, char: dict) -> None:
    """Upsert the character's blankable backgrounds into the blanking table so
    they appear automatically. Sources: the sheet's backgrounds, plus each named
    Retainer/Mawla companion — a companion claims dots from (and suppresses) the
    generic 'Retainer'/'Mawla' background, so a named 'Marcus ●●' is what blanks
    rather than a faceless 'Retainer ●●'. Non-destructive — set_character_background
    preserves any active blank when the dots change."""
    claimed = {"retainer": 0, "mawla": 0}
    comp_rows: list[dict] = []
    for c in list_companions(conn, char["id"]):
        dots = int(c.get("dots") or 0)
        claimed[c["kind"]] = claimed.get(c["kind"], 0) + dots
        if dots > 0:
            comp_rows.append({"name": c["name"], "dots": dots})

    merged: list[dict] = []
    for bg in _sheet_backgrounds(char):
        low = bg["name"].strip().lower()
        if low in ("retainer", "mawla"):
            # Named companions claim dots; emit the remainder so the table
            # reflects the suppression (0 dots removes the generic row).
            merged.append({"name": bg["name"],
                           "dots": max(0, bg["dots"] - claimed.get(low, 0))})
        else:
            merged.append(bg)
    merged.extend(comp_rows)

    for bg in merged:
        try:
            set_character_background(conn, char["id"], bg["name"], bg["dots"],
                                     "system:sheet-sync")
        except ValueError:
            pass


def _backgrounds_partial(request: Request, char: dict, conn, *,
                         notice: str | None = None, error: str | None = None):
    """Re-render the backgrounds card for an HTMX swap."""
    _sync_backgrounds_from_sheet(conn, char)
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


# ── Companions (Retainers & Mawlas) ───────────────────────────────────────────

_COMPANION_TRAIT_KEYS = (
    {k for _c, _tr in _V5_ATTRIBUTES for k, _ in _tr}
    | {k for _c, _tr in _V5_SKILLS for k, _ in _tr}
    | {k for k, _ in _V5_DISCIPLINES}
)


def _sanitize_companion_sheet(raw: dict) -> dict:
    """Keep only known trait keys (clamped 0–5) + specialties / merits / flaws
    from a client-posted companion sheet — never trust the client's JSON whole."""
    sheet: dict = {"specialties": []}
    if not isinstance(raw, dict):
        return sheet
    for k in _COMPANION_TRAIT_KEYS:
        try:
            v = int(raw.get(k, 0) or 0)
        except (TypeError, ValueError):
            v = 0
        if v > 0:
            sheet[k] = max(0, min(5, v))
    specs = []
    for s in (raw.get("specialties") or []):
        if isinstance(s, dict) and str(s.get("name") or "").strip():
            specs.append({"skill": str(s.get("skill") or ""),
                          "name": str(s.get("name")).strip()})
    sheet["specialties"] = specs
    for lk in ("merits", "flaws"):
        items = []
        for it in (raw.get(lk) or []):
            if isinstance(it, dict) and str(it.get("name") or "").strip():
                try:
                    d = int(it.get("dots") or 0)
                except (TypeError, ValueError):
                    d = 0
                items.append({"name": str(it["name"]).strip(), "dots": max(0, min(5, d))})
        if items:
            sheet[lk] = items
    return sheet


def _companions_ctx(request: Request, char: dict, conn, *,
                    create_errors=None, form=None, edit_companion=None):
    """Render the Retainers & Mawlas management page."""
    return templates.TemplateResponse(
        request, "player/companions.html",
        _ctx(
            request,
            char=char,
            companions=list_companions(conn, char["id"]),
            edit_companion=edit_companion,
            v5_attributes=_V5_ATTRIBUTES,
            v5_skills=_V5_SKILLS,
            v5_disciplines=_V5_DISCIPLINES,
            clan_disciplines=sorted(_CLAN_DISCIPLINES.get(char["clan"], [])),
            mortal_templates=_MORTAL_TEMPLATES,
            retainer_dots_to_template=_RETAINER_DOTS_TO_TEMPLATE,
            # Mawla (Kindred) builder data
            clans=_CLANS,
            attribute_spread=list(_V5_ATTRIBUTE_SPREAD),
            skill_spreads=_V5_SKILL_SPREADS,
            discipline_spreads=_V5_DISCIPLINE_SPREADS,
            all_clan_disciplines={s: sorted(_CLAN_DISCIPLINES.get(s, []))
                                  for s, _ in _CLANS},
            create_errors=create_errors or [],
            form=form or {},
        ),
    )


@router.get("/characters/{character_id}/companions", response_class=HTMLResponse)
async def companions_page(
    request: Request,
    character_id: int,
    edit: int = 0,
    user: dict = Depends(require_auth),
):
    with get_db() as conn:
        char = get_character_for_player(conn, character_id, user["id"])
        if not char:
            raise HTTPException(status_code=404)
        edit_companion = None
        if edit:
            cand = get_companion(conn, edit)
            # Only a retainer that belongs to this character is editable.
            if (cand and cand["parent_character_id"] == character_id
                    and cand["kind"] == "retainer"):
                edit_companion = cand
        return _companions_ctx(request, char, conn, edit_companion=edit_companion)


@router.post("/characters/{character_id}/companions")
async def companion_create(
    request: Request,
    character_id: int,
    user: dict = Depends(require_auth),
    _: None = Depends(csrf_protect),
):
    form = await request.form()
    kind        = (form.get("kind") or "retainer").strip().lower()
    name        = (form.get("name") or "").strip()
    concept     = (form.get("concept") or "").strip() or None
    description = (form.get("description") or "").strip() or None
    try:
        raw_sheet = json.loads(form.get("sheet_json") or "{}")
    except (ValueError, TypeError):
        raw_sheet = {}
    sheet = _sanitize_companion_sheet(raw_sheet)

    with get_db() as conn:
        char = get_character_for_player(conn, character_id, user["id"])
        if not char:
            raise HTTPException(status_code=404)
        errors: list[str] = []
        if not name:
            errors.append("Give your companion a name.")
        if kind == "retainer":
            dots     = max(1, min(3, form_int(form.get("dots"), 2)))
            template = _RETAINER_DOTS_TO_TEMPLATE.get(dots, "weak")
            is_ghoul = form.get("is_ghoul") == "on"
            errors += _validate_retainer_template(sheet, template, is_ghoul=is_ghoul)
            if not errors:
                create_companion(
                    conn, parent_character_id=character_id, kind="retainer",
                    name=name, dots=dots, template=template, is_ghoul=is_ghoul,
                    clan=(char["clan"] if is_ghoul else None),
                    concept=concept, description=description, sheet_json=sheet)
                # Surface the new retainer in the Blanking Backgrounds card.
                _sync_backgrounds_from_sheet(conn, char)
                conn.commit()
                return RedirectResponse(
                    url=f"/characters/{character_id}/companions", status_code=303)
        elif kind == "mawla":
            # On hold — elders create differently and the builder isn't final.
            # The page shows a "coming soon" panel; validate_mawla_kindred() and
            # its builder data stay ready for when we re-enable this.
            errors.append("Mawla creation is coming soon.")
        else:
            errors.append("Unknown companion type.")
        return _companions_ctx(request, char, conn, create_errors=errors, form=form)


@router.post("/companions/{companion_id}/edit")
async def companion_edit(
    request: Request,
    companion_id: int,
    user: dict = Depends(require_auth),
    _: None = Depends(csrf_protect),
):
    form = await request.form()
    name        = (form.get("name") or "").strip()
    concept     = (form.get("concept") or "").strip() or None
    description = (form.get("description") or "").strip() or None
    try:
        raw_sheet = json.loads(form.get("sheet_json") or "{}")
    except (ValueError, TypeError):
        raw_sheet = {}
    sheet = _sanitize_companion_sheet(raw_sheet)

    with get_db() as conn:
        comp = get_companion_for_player(conn, companion_id, user["id"])
        if not comp:
            raise HTTPException(status_code=404)
        parent_id = comp["parent_character_id"]
        char = get_character(conn, parent_id)
        errors: list[str] = []
        if not name:
            errors.append("Give your companion a name.")
        if comp["kind"] == "retainer":
            dots     = max(1, min(3, form_int(form.get("dots"), comp["dots"])))
            template = _RETAINER_DOTS_TO_TEMPLATE.get(dots, "weak")
            is_ghoul = form.get("is_ghoul") == "on"
            errors += _validate_retainer_template(sheet, template, is_ghoul=is_ghoul)
            if not errors:
                # On rename, drop the old blanking row before the re-sync adds
                # the new-named one.
                if comp["name"] != name:
                    try:
                        set_character_background(conn, parent_id, comp["name"], 0,
                                                 "system:companion-renamed")
                    except ValueError:
                        pass
                update_companion(
                    conn, companion_id, name=name, dots=dots, template=template,
                    is_ghoul=is_ghoul, clan=(char["clan"] if is_ghoul else None),
                    concept=concept, description=description, sheet_json=sheet)
                _sync_backgrounds_from_sheet(conn, char)
                conn.commit()
                return RedirectResponse(
                    url=f"/characters/{parent_id}/companions", status_code=303)
        else:
            errors.append("This companion can't be edited yet.")
        return _companions_ctx(request, char, conn, create_errors=errors,
                               edit_companion=get_companion(conn, companion_id))


@router.post("/companions/{companion_id}/delete")
async def companion_delete(
    request: Request,
    companion_id: int,
    user: dict = Depends(require_auth),
    _: None = Depends(csrf_protect),
):
    with get_db() as conn:
        comp = get_companion_for_player(conn, companion_id, user["id"])
        if not comp:
            raise HTTPException(status_code=404)
        parent_id = comp["parent_character_id"]
        # Drop its blanking row, then re-sync so any generic Retainer/Mawla dots
        # it had claimed come back.
        try:
            set_character_background(conn, parent_id, comp["name"], 0,
                                     "system:companion-removed")
        except ValueError:
            pass
        delete_companion(conn, companion_id)
        char = get_character(conn, parent_id)
        if char:
            _sync_backgrounds_from_sheet(conn, char)
        conn.commit()
    return RedirectResponse(url=f"/characters/{parent_id}/companions", status_code=303)


# ── Familiars (Animalism • Bond Famulus) ──────────────────────────────────────

def _char_animalism(char: dict) -> int:
    try:
        return int((char.get("sheet_json") or {}).get("disc_animalism") or 0)
    except (TypeError, ValueError):
        return 0


def _familiars_ctx(request: Request, char: dict, conn, *, bond_error=None):
    """Render the Familiars page — bestiary catalog + this character's bonds."""
    return templates.TemplateResponse(
        request, "player/familiars.html",
        _ctx(
            request,
            char=char,
            catalog=list_familiars(conn),
            bonded=list_character_familiars(conn, char["id"]),
            can_bond=_char_animalism(char) >= 1,
            bond_error=bond_error,
        ),
    )


@router.get("/characters/{character_id}/familiars", response_class=HTMLResponse)
async def familiars_page(
    request: Request,
    character_id: int,
    user: dict = Depends(require_auth),
):
    with get_db() as conn:
        char = get_character_for_player(conn, character_id, user["id"])
        if not char:
            raise HTTPException(status_code=404)
        return _familiars_ctx(request, char, conn)


@router.post("/characters/{character_id}/familiars/bond")
async def familiar_bond_route(
    request: Request,
    character_id: int,
    user: dict = Depends(require_auth),
    _: None = Depends(csrf_protect),
):
    form = await request.form()
    name  = (form.get("name") or "").strip()
    notes = (form.get("notes") or "").strip() or None
    fam_id = form_int(form.get("familiar_id"), 0)
    with get_db() as conn:
        char = get_character_for_player(conn, character_id, user["id"])
        if not char:
            raise HTTPException(status_code=404)
        error = None
        if _char_animalism(char) < 1:
            error = "Bonding a famulus requires Animalism • (Bond Famulus)."
        elif not name:
            error = "Give your famulus a name."
        elif not fam_id or not get_familiar(conn, fam_id):
            error = "Choose an animal to bond."
        if error:
            return _familiars_ctx(request, char, conn, bond_error=error)
        bond_familiar(conn, character_id=character_id, familiar_id=fam_id,
                      name=name, notes=notes)
        conn.commit()
    return RedirectResponse(
        url=f"/characters/{character_id}/familiars", status_code=303)


@router.post("/familiars/bonds/{bond_id}/unbond")
async def familiar_unbond_route(
    request: Request,
    bond_id: int,
    user: dict = Depends(require_auth),
    _: None = Depends(csrf_protect),
):
    with get_db() as conn:
        bond = get_character_familiar_for_player(conn, bond_id, user["id"])
        if not bond:
            raise HTTPException(status_code=404)
        parent_id = bond["character_id"]
        unbond_familiar(conn, bond_id)
        conn.commit()
    return RedirectResponse(
        url=f"/characters/{parent_id}/familiars", status_code=303)


# ── Projects (downtime endeavours) ────────────────────────────────────────────

def _projects_partial(request: Request, char: dict, conn, *,
                      notice: str | None = None, error: str | None = None):
    """Re-render the projects card for an HTMX swap."""
    rolls = timeskip_rolls_remaining(conn, char["id"])
    downtime = (list_downtime_actions(conn, char["id"], rolls["period_id"], "hunt")
                if rolls["period_id"] else [])
    return templates.TemplateResponse(
        request, "player/partials/projects.html",
        _ctx(
            request,
            char=char,
            projects=list_projects_for_character(conn, char["id"]),
            proj_rolls=rolls,
            downtime_hunts=downtime,
            coterie=get_coterie_for_character(conn, char["id"]),
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
        from ..db import projects_enabled
        if not projects_enabled(conn):
            error = "Projects are turned off for this chronicle."
        elif not char.get("is_approved"):
            error = "Your character must be approved before proposing projects."
        else:
            try:
                create_project(conn, character_id, title, description,
                               proposed_by=user["id"])
                notice = "Project proposed — staff will review it."
            except ValueError as exc:
                error = str(exc)
        return _projects_partial(request, char, conn, notice=notice, error=error)


@router.post("/characters/{character_id}/downtime/hunt", response_class=HTMLResponse)
async def hunt_downtime_route(
    request: Request,
    character_id: int,
    user: dict = Depends(require_auth),
    _: None = Depends(csrf_protect),
):
    """Spend one timeskip roll to hunt (generic — outcome is ST/bot resolved)."""
    notice = error = None
    with get_db() as conn:
        char = get_character_for_player(conn, character_id, user["id"])
        if not char:
            raise HTTPException(status_code=404)
        from ..db import projects_enabled
        if not projects_enabled(conn):
            error = "Projects are turned off for this chronicle."
        elif not char.get("is_approved"):
            error = "Your character must be approved first."
        else:
            res = hunt_downtime(conn, character_id)
            if res["ok"]:
                notice = (f"Spent a roll to hunt — {res['remaining']} "
                          f"roll{'s' if res['remaining'] != 1 else ''} left this timeskip.")
            else:
                error = res["error"]
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

        # Blood Sorcery Rituals / Oblivion Ceremonies require the discipline —
        # you can't learn them without at least one dot of the power.
        _sheet = char.get("sheet_json") or {}
        if category == "Blood Sorcery Ritual" and int(_sheet.get("disc_blood_sorcery") or 0) < 1:
            errors.append("Your character needs at least 1 dot of Blood Sorcery to learn rituals.")
        elif category == "Oblivion Ceremony" and int(_sheet.get("disc_oblivion") or 0) < 1:
            errors.append("Your character needs at least 1 dot of Oblivion to learn ceremonies.")

        if category and not errors:
            # Account for already-pending spends — they don't deduct xp_spent until
            # approval, so without this a player could queue spends totalling more
            # than their available XP and one would silently fail at approval time.
            pending_total = get_pending_spend_total(conn, character_id)
            effective_char = dict(char)
            effective_char["xp_available"] = max(0, char["xp_available"] - pending_total)
            verified_cost, spend_errors = validate_spend(category, current_dots, new_dots, effective_char)
            # V5: the first Level-1 Blood Sorcery ritual / Oblivion ceremony is
            # free — it comes with the discipline. Zero its cost and drop any
            # "insufficient XP" error, gated on having none yet (sheet + pending).
            if category in ("Blood Sorcery Ritual", "Oblivion Ceremony") and new_dots == 1 and verified_cost > 0:
                _lk = "rituals" if category == "Blood Sorcery Ritual" else "ceremonies"
                _have_rit = (char.get("sheet_json") or {}).get(_lk) or []
                _pending_free = conn.execute(
                    "SELECT 1 FROM spend_requests WHERE character_id=? AND category=? "
                    "AND status='pending' AND verified_cost=0 LIMIT 1",
                    (character_id, category),
                ).fetchone()
                if not _have_rit and not _pending_free:
                    verified_cost = 0
                    spend_errors = [e for e in spend_errors if not e.startswith("Insufficient XP")]
                    note = (note + " " if note else "") + "[free first one — V5]"
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

        # Merits/backgrounds the coterie holds, summed per trait — an
        # at-a-glance list for the summary card (active contributions only).
        coterie_merits: list[dict] = []
        if coterie:
            from ..db import list_coterie_contributions
            _agg: dict[tuple, int] = {}
            for _co in list_coterie_contributions(conn, coterie["id"], status="active"):
                if _co["target_kind"] in ("merit", "background"):
                    _k = (_co["target_kind"], _co["target_name"])
                    _agg[_k] = _agg.get(_k, 0) + int(_co["dots"] or 0)
            coterie_merits = [
                {"kind": k[0], "name": k[1], "dots": v}
                for k, v in sorted(_agg.items(), key=lambda x: (-x[1], x[0][1]))
            ]

        from ..db import coterie_max_members
        coterie_member_cap = coterie_max_members(conn)

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
            coterie_merits=coterie_merits,
            coterie_member_cap=coterie_member_cap,
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

    from ..db import coterie_max_members
    with get_db() as conn:
        _cap = coterie_max_members(conn)
    if len(member_ids) > _cap:
        errors.append(
            f"A coterie can have at most {_cap} members."
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
        coterie_free_budget, list_coterie_shared_backgrounds,
        get_active_period,
    )
    coterie = get_coterie(conn, coterie_id)
    members = list_coterie_members(conn, coterie_id)
    spends  = list_coterie_spends(conn, coterie_id)

    # Shared-background pool: donated backgrounds any member can blank for the
    # night (migration 039). Total per name derives from active 'donated'
    # contributions; the blank state is per active play period.
    shared_backgrounds = list_coterie_shared_backgrounds(conn, coterie_id) if coterie else []
    active_period = get_active_period(conn)

    # Coterie projects (Phase D): any member proposes + rolls; rolls (via the
    # bot's /project roll) spend each member's own per-character timeskip budget.
    coterie_projects = list_projects_for_coterie(conn, coterie_id) if coterie else []

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

    # The viewer's own remaining project-roll budget (rolls on coterie projects
    # spend it). Shown on the coterie projects panel.
    viewer_proj_rolls = (
        timeskip_rolls_remaining(conn, viewer_member_chars[0]["id"])
        if viewer_member_chars else None
    )

    # Leader controls — the coterie leader may PROPOSE new members (staff
    # approve). Flag whether the viewer leads, and which active+approved
    # characters are eligible to add (not already members).
    viewer_char_ids = {c["id"] for c in viewer_member_chars}
    viewer_is_leader = any(
        m["role"] == "leader" and m["character_id"] in viewer_char_ids
        for m in members
    )
    eligible_new_members: list[dict] = []
    if viewer_is_leader:
        member_char_ids = {m["character_id"] for m in members}
        _active = list(list_characters(conn, status="active"))
        # One character per player: exclude anyone whose owning player already
        # has a character in the coterie (add_coterie_member would reject it).
        member_owner_ids = {
            c["discord_id"] for c in _active
            if c["id"] in member_char_ids and c.get("discord_id")
        }
        for c in _active:
            if (c.get("is_approved") and c["id"] not in member_char_ids
                    and c.get("discord_id") not in member_owner_ids):
                eligible_new_members.append(
                    {"id": c["id"], "name": c["name"], "clan": c.get("clan")}
                )

    return {"coterie": coterie, "members": members, "spends": spends,
            "advance_costs": advance_costs,
            "active_contribs": active_contribs,
            "all_contribs": all_contribs,
            "named_trait_cap": COTERIE_NAMED_TRAIT_CAP,
            "viewer_member_chars": viewer_member_chars,
            "viewer_donatable": viewer_donatable,
            "viewer_is_leader": viewer_is_leader,
            "eligible_new_members": eligible_new_members,
            "coterie_free_dots": coterie_free_dots,
            "free_dots_per_member": CREATION_FREE_DOTS_PER_MEMBER,
            "free_budget": free_budget,
            "coterie_flaws": coterie_flaws,
            "shared_backgrounds": shared_backgrounds,
            "coterie_projects": coterie_projects,
            "viewer_proj_rolls": viewer_proj_rolls,
            "active_period": active_period}


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


@router.post("/coteries/{coterie_id}/backgrounds/blank", response_class=HTMLResponse)
async def blank_coterie_background_route(
    request: Request,
    coterie_id: int,
    user: dict = Depends(require_auth),
    _: None = Depends(csrf_protect),
):
    """Any coterie member can blank dots of a shared (donated) background for
    the current night. Blanked dots leave the pool for the whole coterie until
    the next play period opens (same release engine as per-character blanking)."""
    from ..db import blank_coterie_background as _blank
    form = await request.form()
    name = (form.get("name") or "").strip()
    try:
        dots = int(form.get("dots") or 0)
    except (TypeError, ValueError):
        dots = 0

    with get_db() as conn:
        coterie = get_coterie(conn, coterie_id)
        if not coterie:
            raise HTTPException(status_code=404)
        # Membership gate — must own a character in this coterie.
        player_chars = list_player_characters(conn, user["id"])
        members = list_coterie_members(conn, coterie_id)
        actor = _resolve_member_char(player_chars, members, None)
        if actor is None:
            raise HTTPException(status_code=403, detail="Not a member of this coterie")

        flash_kind, flash_msg = "success", ""
        try:
            res = _blank(conn, coterie_id, name, dots, blanked_by=actor["id"])
            flash_msg = (
                f"Blanked {res['blanked_now']} dot(s) of {res['name']} for "
                f"{res['period_label']} — {res['available']} left to the coterie tonight."
            )
        except ValueError as e:
            flash_kind, flash_msg = "error", str(e)

        return _coterie_flash_response(request, conn, coterie_id, user["id"],
                                       flash_msg, flash_kind)


@router.post("/coteries/{coterie_id}/members/propose", response_class=HTMLResponse)
async def propose_coterie_member(
    request: Request,
    coterie_id: int,
    user: dict = Depends(require_auth),
    _: None = Depends(csrf_protect),
):
    """A coterie LEADER proposes adding a character; staff approve before the
    character actually joins (mirrors coterie formation requests)."""
    from ..db import create_coterie_member_request, has_pending_member_request
    form = await request.form()
    character_id = form_int(form.get("character_id"))
    note = (form.get("note") or "").strip() or None

    with get_db() as conn:
        coterie = get_coterie(conn, coterie_id)
        if not coterie:
            raise HTTPException(status_code=404)
        ctx = _coterie_detail_ctx(conn, coterie_id, viewer_discord_id=user["id"])
        if not ctx["viewer_member_chars"]:
            raise HTTPException(status_code=403, detail="Not a member of this coterie")

        msg, kind = None, "success"
        if not ctx["viewer_is_leader"]:
            msg, kind = "Only the coterie leader can propose new members.", "error"
        elif not character_id:
            msg, kind = "Pick a character to propose.", "error"
        else:
            target = get_character(conn, character_id)
            member_ids = {m["character_id"] for m in ctx["members"]}
            if not target or target.get("status") != "active" or not target.get("is_approved"):
                msg, kind = "That isn't an active, approved character.", "error"
            elif character_id in member_ids:
                msg, kind = "That character is already in the coterie.", "error"
            elif has_pending_member_request(conn, coterie_id, character_id):
                msg, kind = "There's already a pending request for that character.", "error"
            else:
                create_coterie_member_request(conn, coterie_id, character_id,
                                              user["id"], note)
                msg = f"Proposed {target['name']} — staff will review and approve."
        return _coterie_flash_response(request, conn, coterie_id, user["id"], msg, kind)


@router.post("/coteries/{coterie_id}/projects/propose", response_class=HTMLResponse)
async def propose_coterie_project(
    request: Request,
    coterie_id: int,
    user: dict = Depends(require_auth),
    _: None = Depends(csrf_protect),
):
    """Any coterie member proposes a coterie project (staff still approve it).
    The proposing member's character is recorded as the proposer; the coterie
    owns it. Once active, any member rolls it via the bot's /project roll."""
    form = await request.form()
    title = (form.get("title") or "").strip()
    description = (form.get("description") or "").strip()
    with get_db() as conn:
        coterie = get_coterie(conn, coterie_id)
        if not coterie:
            raise HTTPException(status_code=404)
        player_chars = list_player_characters(conn, user["id"])
        members = list_coterie_members(conn, coterie_id)
        actor = _resolve_member_char(player_chars, members, None)
        if actor is None:
            raise HTTPException(status_code=403, detail="Not a member of this coterie")

        from ..db import projects_enabled
        flash_kind, flash_msg = "success", ""
        if not projects_enabled(conn):
            flash_kind, flash_msg = "error", "Projects are turned off for this chronicle."
        elif not actor.get("is_approved"):
            flash_kind, flash_msg = "error", "Your character must be approved first."
        else:
            try:
                create_project(conn, actor["id"], title, description,
                               proposed_by=user["id"], coterie_id=coterie_id)
                flash_msg = "Coterie project proposed — staff will review it."
            except ValueError as e:
                flash_kind, flash_msg = "error", str(e)

        return _coterie_flash_response(request, conn, coterie_id, user["id"],
                                       flash_msg, flash_kind)


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
