"""V5 trait reference data.

Single source of truth for the V5 sheet structure — imported by both the
player route (for the editor) and the staff route (for the read-only sheet
display). Keep player.py + staff.py in sync via this module.
"""
import json
from pathlib import Path

V5_ATTRIBUTES: list[tuple[str, list[tuple[str, str]]]] = [
    ("Physical", [
        ("attr_strength",     "Strength"),
        ("attr_dexterity",    "Dexterity"),
        ("attr_stamina",      "Stamina"),
    ]),
    ("Social", [
        ("attr_charisma",     "Charisma"),
        ("attr_manipulation", "Manipulation"),
        ("attr_composure",    "Composure"),
    ]),
    ("Mental", [
        ("attr_intelligence", "Intelligence"),
        ("attr_wits",         "Wits"),
        ("attr_resolve",      "Resolve"),
    ]),
]

V5_SKILLS: list[tuple[str, list[tuple[str, str]]]] = [
    ("Physical", [
        ("skill_athletics",     "Athletics"),
        ("skill_brawl",         "Brawl"),
        ("skill_craft",         "Craft"),
        ("skill_drive",         "Drive"),
        ("skill_firearms",      "Firearms"),
        ("skill_larceny",       "Larceny"),
        ("skill_melee",         "Melee"),
        ("skill_stealth",       "Stealth"),
        ("skill_survival",      "Survival"),
    ]),
    ("Social", [
        ("skill_animal_ken",    "Animal Ken"),
        ("skill_etiquette",     "Etiquette"),
        ("skill_insight",       "Insight"),
        ("skill_intimidation",  "Intimidation"),
        ("skill_leadership",    "Leadership"),
        ("skill_performance",   "Performance"),
        ("skill_persuasion",    "Persuasion"),
        ("skill_streetwise",    "Streetwise"),
        ("skill_subterfuge",    "Subterfuge"),
    ]),
    ("Mental", [
        ("skill_academics",     "Academics"),
        ("skill_awareness",     "Awareness"),
        ("skill_finance",       "Finance"),
        ("skill_investigation", "Investigation"),
        ("skill_medicine",      "Medicine"),
        ("skill_occult",        "Occult"),
        ("skill_politics",      "Politics"),
        ("skill_science",       "Science"),
        ("skill_technology",    "Technology"),
    ]),
]

V5_DISCIPLINES: list[tuple[str, str]] = [
    ("disc_animalism",          "Animalism"),
    ("disc_auspex",             "Auspex"),
    ("disc_blood_sorcery",      "Blood Sorcery"),
    ("disc_celerity",           "Celerity"),
    ("disc_dominate",           "Dominate"),
    ("disc_fortitude",          "Fortitude"),
    ("disc_obfuscate",          "Obfuscate"),
    ("disc_oblivion",           "Oblivion"),
    ("disc_potence",            "Potence"),
    ("disc_presence",           "Presence"),
    ("disc_protean",            "Protean"),
    ("disc_thin_blood_alchemy", "Thin-Blood Alchemy"),
]

# In-clan discipline map. Caitiff and thin-bloods have no in-clan disciplines.
CLAN_DISCIPLINES: dict[str, list[str]] = {
    "banu-haqim": ["disc_blood_sorcery", "disc_celerity",      "disc_obfuscate"],
    "brujah":     ["disc_celerity",      "disc_potence",       "disc_presence"],
    "gangrel":    ["disc_animalism",     "disc_fortitude",     "disc_protean"],
    "hecata":     ["disc_auspex",        "disc_fortitude",     "disc_oblivion"],
    "lasombra":   ["disc_dominate",      "disc_oblivion",      "disc_potence"],
    "malkavian":  ["disc_auspex",        "disc_dominate",      "disc_obfuscate"],
    "ministry":   ["disc_obfuscate",     "disc_presence",      "disc_protean"],
    "nosferatu":  ["disc_animalism",     "disc_obfuscate",     "disc_potence"],
    "ravnos":     ["disc_animalism",     "disc_obfuscate",     "disc_presence"],
    "salubri":    ["disc_auspex",        "disc_dominate",      "disc_fortitude"],
    "toreador":   ["disc_auspex",        "disc_celerity",      "disc_presence"],
    "tremere":    ["disc_auspex",        "disc_blood_sorcery", "disc_dominate"],
    "tzimisce":   ["disc_animalism",     "disc_dominate",      "disc_protean"],
    "ventrue":    ["disc_dominate",      "disc_fortitude",     "disc_presence"],
}

# Discipline powers catalog — V5-generic, lifted from the friend's data set
# (2026-06-17). Keyed by the disc_* keys above; each power is
# {name, level, summary, dice_pool, rouse_checks, amalgam?[{discipline, level}]}.
# Loaded once at import, mirroring packages/rules/xp_costs.json.
_DISCIPLINE_POWERS_PATH = Path(__file__).parent.parent / "packages" / "rules" / "discipline_powers.json"
DISCIPLINE_POWERS: dict[str, list[dict]] = json.loads(
    _DISCIPLINE_POWERS_PATH.read_text(encoding="utf-8")
)


def discipline_powers(disc_key: str, max_level: int | None = None) -> list[dict]:
    """Powers for a discipline, optionally capped at max_level (the character's
    current dots in it). Returns [] for an unknown key."""
    powers = DISCIPLINE_POWERS.get(disc_key, [])
    if max_level is not None:
        powers = [p for p in powers if p["level"] <= max_level]
    return powers


# Merits & Flaws catalog — V5-generic, lifted from the friend's data set
# (2026-06-17). Two flat lists; each entry is {name, costs:[allowed dot values],
# summary, category, advanced, restriction?, excludes?}. Loaded once at import.
_MERITS_FLAWS_PATH = Path(__file__).parent.parent / "packages" / "rules" / "merits_flaws.json"
MERITS_FLAWS: dict[str, list[dict]] = json.loads(
    _MERITS_FLAWS_PATH.read_text(encoding="utf-8")
)
MERIT_CATALOG: list[dict] = MERITS_FLAWS.get("merits", [])
FLAW_CATALOG:  list[dict] = MERITS_FLAWS.get("flaws", [])

# Split the positive catalog into merits vs backgrounds so the chargen Legacy
# step can show two focused pickers. Background categories follow the friend's
# authoritative classification (MeritsAndFlawsPicker BG title sets); everything
# else is a merit. Loresheets are treated as backgrounds for budget purposes.
_BACKGROUND_CATEGORIES = {"Haven", "Resources", "Kindred", "Mortals", "Fame", "Influence"}
for _m in MERIT_CATALOG:
    _m["kind"] = "background" if _m.get("category") in _BACKGROUND_CATEGORIES else "merit"

# Blood Sorcery Rituals + Oblivion Ceremonies catalog — V5-generic, lifted from
# the friend's data set (2026-06-17). Each entry is {name, level, summary,
# dice_pool, rouse_checks, required_time?, ingredients?, prerequisite_powers?}.
_RITUALS_PATH = Path(__file__).parent.parent / "packages" / "rules" / "rituals_ceremonies.json"
_RITUALS_CEREMONIES: dict[str, list[dict]] = json.loads(
    _RITUALS_PATH.read_text(encoding="utf-8")
)
RITUAL_CATALOG:   list[dict] = _RITUALS_CEREMONIES.get("rituals", [])
CEREMONY_CATALOG: list[dict] = _RITUALS_CEREMONIES.get("ceremonies", [])

# Loresheets catalog — V5-generic, lifted from the friend's data set
# (2026-06-17). First-class loresheets: each is {id, name, source,
# requires_st_permission, clan_restriction?, dots:[{dot, name, description,
# clan_restriction?}]}, dots 1–5 each granting that level's benefit (cost = dot×3
# XP). The full catalog (with long descriptions) is for server-side display;
# LORESHEET_PICKER is a trimmed projection embedded in the chargen wizard.
_LORESHEETS_PATH = Path(__file__).parent.parent / "packages" / "rules" / "loresheets.json"
LORESHEET_CATALOG: list[dict] = json.loads(_LORESHEETS_PATH.read_text(encoding="utf-8"))
_LORESHEET_BY_ID: dict[str, dict] = {l["id"]: l for l in LORESHEET_CATALOG}
LORESHEET_PICKER: list[dict] = [
    {k: v for k, v in {
        "id":   l["id"],
        "name": l["name"],
        "source": l.get("source", ""),
        "requires_st_permission": bool(l.get("requires_st_permission")),
        "clan_restriction": l.get("clan_restriction"),
        "dots": [{"dot": d["dot"], "name": d["name"]} for d in l.get("dots", [])],
    }.items() if v is not None}
    for l in LORESHEET_CATALOG
]


def get_loresheet(loresheet_id: str) -> dict | None:
    """Full loresheet (with per-dot descriptions) by id, or None."""
    return _LORESHEET_BY_ID.get(loresheet_id)


LORESHEET_DOT_XP = 3   # XP per loresheet dot (V5: dot × 3)


# Flat allow-list of single-value sheet keys. Free-form lists (merits/flaws/
# rituals/ceremonies/formulae) are handled separately by the save route.
SHEET_TRAIT_KEYS: set[str] = (
    {key for _, traits in (V5_ATTRIBUTES + V5_SKILLS) for key, _ in traits}
    | {key for key, _ in V5_DISCIPLINES}
    | {"humanity", "blood_potency", "hunger"}
)

# Most traits cap at 5 dots. Humanity goes to 10.
SHEET_LIMITS: dict[str, int] = {"humanity": 10, "blood_potency": 5, "hunger": 5}


# V5 predator types — kept here so both player creation forms and the
# staff hunting-sites editor draw from the same canonical list.
# Predator type lineup tuned for NYbN per Steward direction (2026-05):
#   - Ferryman, Hitcher, Smuggler — removed (not in use for the chronicle)
#   - Tithe Collector — added (In Memoriam supplement)
#   - Pursuer, Roadside Killer, Trapdoor — added (Players Guide / LStRR)
#   - Blood Leech (Core Rulebook) + Tithe Collector — flagged as
#     restricted via V5_RESTRICTED_PREDATOR_TYPES below. The wizard hides
#     restricted types from the picker unless the chronicle has unlocked
#     them via a chronicle_restrictions row (migration 022). The legacy
#     chronicle_settings.unlocked_predator_types JSON column is kept for
#     backwards compatibility but no longer authoritative — see
#     web/db.py::is_component_allowed.
V5_PREDATOR_TYPES: list[str] = [
    "Alleycat", "Bagger", "Blood Leech", "Cleaver", "Consensualist",
    "Extortionist", "Farmer", "Graverobber", "Grim Reaper", "Montero",
    "Osiris", "Pursuer", "Roadside Killer", "Sandman", "Scene Queen",
    "Siren", "Tithe Collector", "Trapdoor",
]

# Predator types that are usually banned in chronicles and require staff
# opt-in. The wizard filters these out unless a chronicle_restrictions
# row with mode='unlocked' exists for the name.
V5_RESTRICTED_PREDATOR_TYPES: tuple[str, ...] = (
    "Blood Leech",
    "Tithe Collector",
)

# Predator types valid as a HUNTING-SITE favored predator. The restricted
# types don't represent a mortal hunting profile — Blood Leech feeds on other
# vampires, Tithe Collector bends the Hunger economy — so they're excluded
# from the staff site editor, matching the chargen picker (which hides
# restricted types unless the chronicle explicitly unlocks them).
V5_SITE_PREDATOR_TYPES: list[str] = [
    p for p in V5_PREDATOR_TYPES if p not in V5_RESTRICTED_PREDATOR_TYPES
]


# ── Clan reference: Bane + Compulsion names ──────────────────────────────────
# Concise paraphrased summaries of each clan's signature weakness (Bane) and
# the Compulsion their Beast trends toward when Hunger strikes. Use these as
# at-a-glance reminders during chargen — the player still consults the
# sourcebook for the full mechanical text.
V5_CLAN_INFO: dict[str, dict[str, str]] = {
    "banu-haqim": {
        "name": "Banu Haqim",
        "bane": "Diablerist's curse — drinking vampire blood is addictive; resisting requires a Hunger test against intoxication.",
        "compulsion": "Judgment — driven to punish those who break their personal code; bite anyone who transgresses or take a 2-die penalty.",
    },
    "brujah": {
        "name": "Brujah",
        "bane": "Volatile temper — penalty equal to Bane Severity on rolls to resist fury frenzy.",
        "compulsion": "Rebellion — must defy the most recent order, request, or expectation until they've pushed back; 2-die penalty otherwise.",
    },
    "caitiff": {
        "name": "Caitiff",
        "bane": "Clanless stigma — no in-clan Disciplines; every Discipline costs the out-of-clan rate. Vampires who recognize Caitiffs often look down on them.",
        "compulsion": "No fixed Compulsion — staff and player negotiate one at chargen.",
    },
    "gangrel": {
        "name": "Gangrel",
        "bane": "Beastly features — after each frenzy, gain an animal trait that imposes a penalty until next sunset.",
        "compulsion": "Feral Impulses — regress to instinct; lose dice on Social/Mental pools and prefer physical solutions for one scene.",
    },
    "hecata": {
        "name": "Hecata",
        "bane": "Painful Kiss — the Kiss is excruciating instead of pleasurable; victims fight, scream, or panic unless restrained.",
        "compulsion": "Morbidity — fixate on death; must dwell on or invoke endings to take meaningful action.",
    },
    "lasombra": {
        "name": "Lasombra",
        "bane": "Distorted reflection — no clear reflection in mirrors and recording devices distort their image and voice.",
        "compulsion": "Ruthlessness — every plan must include the most direct path to power, even if cruel; 2-die penalty on rolls that don't.",
    },
    "malkavian": {
        "name": "Malkavian",
        "bane": "Fractured Perspective — sensory glitches and intrusive thoughts; penalty on rolls relying on stable perception.",
        "compulsion": "Delusion — perceptions warp; take a 2-die penalty to Dexterity, Manipulation, Composure, and Wits rolls for the scene.",
    },
    "ministry": {
        "name": "The Ministry",
        "bane": "Cold Blood — sunlight, fire, and faith damage them more readily; Aggravated damage scales with Bane Severity.",
        "compulsion": "Transgression — tempt someone (or yourself) into breaking a rule, taboo, or vow before the scene ends.",
    },
    "nosferatu": {
        "name": "Nosferatu",
        "bane": "Repulsiveness — physically grotesque; cannot pass for human and take a penalty equal to Bane Severity on Social rolls aimed at making a positive impression.",
        "compulsion": "Cryptophilia — must learn a secret before the scene ends; 2-die penalty until they unearth one.",
    },
    "ravnos": {
        "name": "Ravnos",
        "bane": "Doomed — slumbering in the same haven more than once a week ignites the Blood; roll Bane Severity dice for Aggravated damage per success.",
        "compulsion": "Tempting Fate — must take the most reckless option available; 2-die penalty until they court real danger.",
    },
    "salubri": {
        "name": "Salubri",
        "bane": "Hunted Blood — drinking from a Salubri compels other Kindred to seek them out; their vitae draws diablerists.",
        "compulsion": "Affective Empathy — drawn to another character's distress; must act on their feelings or take a penalty until they intervene.",
    },
    "thin-blood": {
        "name": "Thin-Blood",
        "bane": "Weak Vitae — many traditional Disciplines unavailable; rely on Thin-Blood Alchemy instead. Specific weaknesses vary per character.",
        "compulsion": "No fixed Compulsion — Thin-Bloods don't roll Hunger frenzy in the standard way.",
    },
    "toreador": {
        "name": "Toreador",
        "bane": "Aesthetic Failure — surroundings that fall short of their standard impose a penalty equal to Bane Severity on Discipline rolls.",
        "compulsion": "Obsession — fixate on a person, object, or sensation; can't take attention off it until the scene ends or it leaves their senses.",
    },
    "tremere": {
        "name": "Tremere",
        "bane": "Deficient Bond — their Blood cannot create permanent Bonds normally; each step takes additional drinks equal to Bane Severity.",
        "compulsion": "Perfectionism — repeat actions until they get them \"right\"; 2-die penalty on dice pools until a critical win or scene end.",
    },
    "tzimisce": {
        "name": "Tzimisce",
        "bane": "Grounded — must rest with at least two handfuls of soil tied to their identity (homeland, haven, etc.) or take Aggravated damage per night.",
        "compulsion": "Covetousness — must claim something nearby as their own (person, place, idea) before scene's end; 2-die penalty otherwise.",
    },
    "ventrue": {
        "name": "Ventrue",
        "bane": "Rarefied Tastes — can only feed from a narrow preference (specific bloodline, profession, emotion); other blood is rejected.",
        "compulsion": "Arrogance — must impose their will; insist someone obey or follow before the scene ends, otherwise take a 2-die penalty.",
    },
}


# ── Clan Bane chargen flaws ─────────────────────────────────────────────────
# A few clan Banes manifest at character creation as an auto-granted, FREE flaw
# (it does not count against the flaw budget). Nosferatu's Repulsiveness is the
# canonical case — every Nosferatu carries the Repulsive flaw (2 dots). Keyed by
# (clan slug, active bane: 'standard' | 'variant'). Most Banes are runtime
# mechanical effects and grant no chargen flaw, so they're simply absent here.
V5_CLAN_BANE_FLAWS: dict[tuple[str, str], dict] = {
    ("nosferatu", "standard"): {"name": "Repulsive", "dots": 2},
    # Nosferatu's variant Bane (Infestation) replaces the appearance Bane, so a
    # Nosferatu on the variant is "not necessarily deformed" → no Repulsive flaw.
}


# Clan Banes that grant a POOL of chargen Flaw dots scaled by Bane Severity,
# distributed among named Flaws (vs the single fixed flaw above). The Hecata
# variant "Decay" rots their holdings: gain Flaw dots equal to Bane Severity
# split among Retainer / Haven / Resources. Keyed by (clan, active bane); the
# dots are free (src='clan_bane') and computed from the character's Blood
# Potency at runtime.
V5_CLAN_BANE_FLAW_POOLS: dict[tuple[str, str], dict] = {
    ("hecata", "variant"): {
        "options": ["Retainer", "Haven", "Resources"],
    },
}


# V5 Blood Potency → Bane Severity (Corebook p.216). Used to size Bane-scaled
# chargen effects like the Hecata Decay flaw pool.
def bane_severity_for_bp(bp) -> int:
    try:
        bp = int(bp or 0)
    except (TypeError, ValueError):
        bp = 0
    if bp <= 0:
        return 0
    # V5 Corebook p.216: 0 at BP 0, else ceil(BP / 2) + 1
    # → BP 1-2 = 2, 3-4 = 3, 5-6 = 4, 7-8 = 5, 9-10 = 6.
    return (min(bp, 10) + 1) // 2 + 1


# ── Alternate clan Banes (V5 Players Guide, "Clan Bane Variants" pp. 56-59) ──
# A chronicle may swap a clan's standard Bane for its variant, applied CLAN-WIDE
# (a lineage trait, not a personal quirk). Default everywhere is the standard
# Bane. Stored as {name, effect}; the app tracks which Bane is active per clan
# and shows its effect text. Variant effects key off Bane Severity (tracked per
# character) — the app surfaces the rule, not the dice math. Caitiff and
# Thin-Blood have no clan Bane variant.
V5_CLAN_BANE_VARIANTS: dict[str, dict[str, str]] = {
    "banu-haqim": {"name": "Noxious Blood", "effect": "Their vitae is poison to mortals — a mortal who drinks it takes Aggravated damage equal to Bane Severity per Rouse Check's worth ingested, and it cannot heal mortal injuries. Makes Banu Haqim ghouls rare."},
    "brujah":     {"name": "Violence", "effect": "On a messy critical on any Skill test outside combat, the Brujah deals damage (physical or mental) equal to Bane Severity to the subject, in addition to other Hunger results — Aggravated unless a point of Willpower is spent to make it Superficial."},
    "gangrel":    {"name": "Survival Instincts", "effect": "Subtract dice equal to Bane Severity from any roll to resist Terror Frenzy (never below one die)."},
    "hecata":     {"name": "Decay", "effect": "Havens and assets rot around them. Gain Flaw dots equal to Bane Severity split among Retainer, Haven, and Resources; buying those Advantages costs extra XP equal to Bane Severity, and buying off the Flaws costs twice the Background dots."},
    "lasombra":   {"name": "Callousness", "effect": "Deduct dice equal to Bane Severity from any Remorse roll (never below one die)."},
    "malkavian":  {"name": "Unnatural Manifestations", "effect": "Using a Discipline power spooks nearby mortals — non-Intimidation social tests with them suffer a penalty equal to Bane Severity for the scene (not Masquerade-breaking); other vampires instantly recognize the Malkavian as Kindred."},
    "ministry":   {"name": "Cold-Blooded", "effect": "Can use Blush of Life only after recently feeding from a living vessel, and doing so takes Rouse Checks equal to Bane Severity instead of one."},
    "nosferatu":  {"name": "Infestation", "effect": "Their haven is always infested (penalty of 2 + Bane Severity to concentration-requiring activity by anyone); any scene in an enclosed location imposes a penalty equal to Bane Severity, and controlling the vermin with Animalism takes a like penalty. Replaces the appearance Bane — the Nosferatu is not necessarily deformed."},
    "ravnos":     {"name": "Unbirth Name", "effect": "Anyone who speaks the Ravnos's unbirth name to their face gains a bonus equal to Bane Severity to resist the Ravnos's Disciplines, and the Ravnos takes an equal penalty to resist that speaker's supernatural powers."},
    "salubri":    {"name": "Asceticism", "effect": "When Hunger is below 3, suffer a penalty equal to Bane Severity to all Discipline dice pools (in addition to the third-eye effect)."},
    "toreador":   {"name": "Agonizing Empathy", "effect": "When their feeding damages a mortal, the Toreador suffers similar (usually Aggravated) damage in return, capped at Bane Severity per feeding."},
    "tremere":    {"name": "Stolen Blood", "effect": "A Blood Surge requires Rouse Checks equal to Bane Severity; if those would raise Hunger to 5+, they may back off the Surge or perform it and hit Hunger 5."},
    "tzimisce":   {"name": "Cursed Courtesy", "effect": "To enter an inhabited residence uninvited, must spend Willpower equal to Bane Severity and suffer a like Discipline-pool penalty during the stay. Cannot also take the Folkloric Block Flaw."},
    "ventrue":    {"name": "Hierarchy", "effect": "Suffer a Discipline-pool penalty equal to Bane Severity when using powers on a vampire of lower generation, and must spend Willpower equal to Bane Severity to directly attack one."},
}


def active_clan_bane(clan: str | None, bane_choice: str | None = "standard") -> dict | None:
    """Resolve a character's active clan Bane (standard or chosen variant) to a
    {name, effect, variant} dict for display on the sheets. Returns None for
    archetypes with no clan Bane (e.g. mortals, or an unknown clan)."""
    info = V5_CLAN_INFO.get(clan or "")
    if not info:
        return None
    if bane_choice == "variant" and clan in V5_CLAN_BANE_VARIANTS:
        v = V5_CLAN_BANE_VARIANTS[clan]
        return {"name": v["name"], "effect": v["effect"], "variant": True}
    # Standard Bane text in V5_CLAN_INFO reads "Name — effect …"; split it so
    # the sheet can show a bold name + the effect, falling back gracefully.
    bane = info.get("bane", "")
    name, sep, effect = bane.partition("—")
    return {
        "name": (name.strip() if sep else info.get("name", clan)),
        "effect": (effect.strip() if sep else bane),
        "variant": False,
    }


# ── Predator Type benefit summaries (paraphrased V5 RAW) ────────────────────
# Each entry lists the mechanical benefits a Predator Type grants at chargen.
# These are advisory — staff still validates the exact dot/specialty/merit
# placement during approval. Player chooses among the listed options.
#
# Restricted types (Blood Leech, Tithe Collector) are staff opt-in via
# chronicle_restrictions — the chargen picker hides them unless unlocked.
#
# Each entry also carries a structured `grants` list the wizard renders as
# interactive pickers. Grant kinds:
#   specialty  — options:[{skill,name}]            → player picks one
#   discipline — options:[disc_*]  (the FREE dot)  → player picks one (+1 dot)
#   fixed      — list, name, dots                  → auto-added to that list
#   delta      — trait (humanity/blood_potency)    → auto ± to the trait
#   choice     — prompt, options:[<grant>,…]       → player picks one sub-grant
#   pool       — list, dots, options:[name,…]      → spend N dots across the names
# Lists: merits / backgrounds / flaws (V5 "advantages" map onto these).
V5_PREDATOR_INFO: dict[str, dict] = {
    "Alleycat": {
        "benefits": "+1 Celerity OR Potence. Intimidation (Stickups) OR Brawl (Grappling) specialty. Lose 1 Humanity. Gain Criminal Contacts (•••). Feeds by force or threat.",
        "grants": [
            {"kind": "specialty", "options": [
                {"skill": "skill_intimidation", "name": "Stickups"},
                {"skill": "skill_brawl", "name": "Grappling"}]},
            {"kind": "discipline", "options": ["disc_celerity", "disc_potence"]},
            {"kind": "delta", "trait": "humanity", "delta": -1},
            {"kind": "fixed", "list": "backgrounds", "name": "Criminal Contacts", "dots": 3},
        ],
    },
    "Bagger": {
        "benefits": "+1 Obfuscate (Blood Sorcery/Oblivion per clan). Larceny (Lock Picking) OR Streetwise (Black Market) specialty. Gain Iron Gullet (•••) AND an Enemy (••) flaw. Feeds on stored/preserved blood. Not for Ventrue.",
        "grants": [
            {"kind": "specialty", "options": [
                {"skill": "skill_larceny", "name": "Lock Picking"},
                {"skill": "skill_streetwise", "name": "Black Market"}]},
            {"kind": "discipline", "options": ["disc_blood_sorcery", "disc_oblivion", "disc_obfuscate"],
             "note": "Blood Sorcery (Tremere/Banu Haqim) · Oblivion (Hecata) · else Obfuscate"},
            {"kind": "fixed", "list": "merits", "name": "Iron Gullet", "dots": 3},
            {"kind": "fixed", "list": "flaws", "name": "Enemy", "dots": 2},
        ],
    },
    "Blood Leech": {
        "benefits": "Staff opt-in. +1 Celerity OR Protean. Brawl (Kindred) OR Stealth (vs Kindred) specialty. Lose 1 Humanity, +1 Blood Potency. Diablerist OR Shunned (••), plus Prey Exclusion: Mortals (••). Feeds on vampire vitae.",
        "grants": [
            {"kind": "specialty", "options": [
                {"skill": "skill_brawl", "name": "Kindred"},
                {"skill": "skill_stealth", "name": "Against Kindred"}]},
            {"kind": "discipline", "options": ["disc_celerity", "disc_protean"]},
            {"kind": "delta", "trait": "humanity", "delta": -1},
            {"kind": "delta", "trait": "blood_potency", "delta": 1},
            {"kind": "choice", "prompt": "Dark Secret", "options": [
                {"kind": "fixed", "list": "flaws", "name": "Dark Secret: Diablerist", "dots": 2},
                {"kind": "fixed", "list": "flaws", "name": "Shunned", "dots": 2}]},
            {"kind": "fixed", "list": "flaws", "name": "Prey Exclusion (Mortals)", "dots": 2},
        ],
    },
    "Cleaver": {
        "benefits": "+1 Dominate OR Animalism. Persuasion (Gaslighting) OR Subterfuge (Coverups) specialty. Gain Herd (••), but Dark Secret: Cleaver (•). Feeds on their own family/friends.",
        "grants": [
            {"kind": "specialty", "options": [
                {"skill": "skill_persuasion", "name": "Gaslighting"},
                {"skill": "skill_subterfuge", "name": "Coverups"}]},
            {"kind": "discipline", "options": ["disc_dominate", "disc_animalism"]},
            {"kind": "fixed", "list": "backgrounds", "name": "Herd", "dots": 2},
            {"kind": "fixed", "list": "flaws", "name": "Dark Secret: Cleaver", "dots": 1},
        ],
    },
    "Consensualist": {
        "benefits": "+1 Auspex OR Fortitude. Medicine (Phlebotomy) OR Persuasion (Vessels) specialty. +1 Humanity. Masquerade Breacher (•) + Prey Exclusion: Non-consenting (•). Only feeds with consent.",
        "grants": [
            {"kind": "specialty", "options": [
                {"skill": "skill_medicine", "name": "Phlebotomy"},
                {"skill": "skill_persuasion", "name": "Vessels"}]},
            {"kind": "discipline", "options": ["disc_auspex", "disc_fortitude"]},
            {"kind": "delta", "trait": "humanity", "delta": 1},
            {"kind": "fixed", "list": "flaws", "name": "Dark Secret: Masquerade Breacher", "dots": 1},
            {"kind": "fixed", "list": "flaws", "name": "Prey Exclusion (Non-consenting)", "dots": 1},
        ],
    },
    "Extortionist": {
        "benefits": "+1 Dominate OR Potence. Intimidation (Coercion) OR Larceny (Security) specialty. 3 dots across Contacts & Resources, but Enemy (••). Feeds in exchange for 'services'.",
        "grants": [
            {"kind": "specialty", "options": [
                {"skill": "skill_intimidation", "name": "Coercion"},
                {"skill": "skill_larceny", "name": "Security"}]},
            {"kind": "discipline", "options": ["disc_dominate", "disc_potence"]},
            {"kind": "pool", "list": "backgrounds", "dots": 3, "options": ["Contacts", "Resources"]},
            {"kind": "fixed", "list": "flaws", "name": "Enemy", "dots": 2},
        ],
    },
    "Farmer": {
        "benefits": "+1 Animalism OR Protean. Animal Ken OR Survival (Hunting) specialty. +1 Humanity. Farmer (••) feeding flaw. Feeds on animals. Not for Ventrue or Blood Potency 3+.",
        "grants": [
            {"kind": "specialty", "options": [
                {"skill": "skill_animal_ken", "name": "Specific Animal"},
                {"skill": "skill_survival", "name": "Hunting"}]},
            {"kind": "discipline", "options": ["disc_animalism", "disc_protean"]},
            {"kind": "delta", "trait": "humanity", "delta": 1},
            {"kind": "fixed", "list": "flaws", "name": "Farmer", "dots": 2},
        ],
    },
    "Graverobber": {
        "benefits": "+1 Fortitude OR Oblivion. Occult (Grave Rituals) OR Medicine (Cadavers) specialty. Iron Gullet (•••) + Haven (•), but Obvious Predator (••) herd flaw. Feeds on corpses/mourners.",
        "grants": [
            {"kind": "specialty", "options": [
                {"skill": "skill_occult", "name": "Grave Rituals"},
                {"skill": "skill_medicine", "name": "Cadavers"}]},
            {"kind": "discipline", "options": ["disc_fortitude", "disc_oblivion"]},
            {"kind": "fixed", "list": "merits", "name": "Iron Gullet", "dots": 3},
            {"kind": "fixed", "list": "backgrounds", "name": "Haven", "dots": 1},
            {"kind": "fixed", "list": "flaws", "name": "Obvious Predator", "dots": 2},
        ],
    },
    "Grim Reaper": {
        "benefits": "+1 Auspex OR Oblivion. Awareness (Death) OR Larceny (Forgery) specialty. +1 Humanity. Allies/Influence (•) in medicine. Prey Exclusion: Healthy Mortals (•). Feeds on the dying.",
        "grants": [
            {"kind": "specialty", "options": [
                {"skill": "skill_awareness", "name": "Death"},
                {"skill": "skill_larceny", "name": "Forgery"}]},
            {"kind": "discipline", "options": ["disc_auspex", "disc_oblivion"]},
            {"kind": "delta", "trait": "humanity", "delta": 1},
            {"kind": "choice", "prompt": "Medical background (•)", "options": [
                {"kind": "fixed", "list": "backgrounds", "name": "Allies (medical)", "dots": 1},
                {"kind": "fixed", "list": "backgrounds", "name": "Influence (medical)", "dots": 1}]},
            {"kind": "fixed", "list": "flaws", "name": "Prey Exclusion (Healthy Mortals)", "dots": 1},
        ],
    },
    "Montero": {
        "benefits": "+1 Dominate OR Obfuscate. Leadership (Hunting Pack) OR Stealth (Stakeout) specialty. Gain Retainers (••). Lose 1 Humanity. Retainers drive prey to the hunter.",
        "grants": [
            {"kind": "specialty", "options": [
                {"skill": "skill_leadership", "name": "Hunting Pack"},
                {"skill": "skill_stealth", "name": "Stakeout"}]},
            {"kind": "discipline", "options": ["disc_dominate", "disc_obfuscate"]},
            {"kind": "fixed", "list": "backgrounds", "name": "Retainers", "dots": 2},
            {"kind": "delta", "trait": "humanity", "delta": -1},
        ],
    },
    "Osiris": {
        "benefits": "+1 Blood Sorcery OR Presence. Occult OR Performance specialty. 3 dots across Fame & Herd; 2 dots across Enemies & Mythic flaws. Feeds on fans/followers.",
        "grants": [
            {"kind": "specialty", "options": [
                {"skill": "skill_occult", "name": "Specific Tradition"},
                {"skill": "skill_performance", "name": "Specific Field"}]},
            {"kind": "discipline", "options": ["disc_blood_sorcery", "disc_presence"],
             "note": "Blood Sorcery (Tremere/Banu Haqim) only; else Presence"},
            {"kind": "pool", "list": "backgrounds", "dots": 3, "options": ["Fame", "Herd"]},
            {"kind": "pool", "list": "flaws", "dots": 2, "options": ["Enemies", "Mythic"]},
        ],
    },
    "Pursuer": {
        "benefits": "+1 Animalism OR Auspex. Investigation (Profiling) OR Stealth (Shadowing) specialty. Gain Bloodhound (•) merit + Contacts (•). Lose 1 Humanity. Stalks prey before striking.",
        "grants": [
            {"kind": "specialty", "options": [
                {"skill": "skill_investigation", "name": "Profiling"},
                {"skill": "skill_stealth", "name": "Shadowing"}]},
            {"kind": "discipline", "options": ["disc_animalism", "disc_auspex"]},
            {"kind": "fixed", "list": "merits", "name": "Bloodhound", "dots": 1},
            {"kind": "fixed", "list": "backgrounds", "name": "Contacts", "dots": 1},
            {"kind": "delta", "trait": "humanity", "delta": -1},
        ],
    },
    "Roadside Killer": {
        "benefits": "+1 Fortitude OR Protean. Survival (the Road) OR Investigation (Vampire Cant) specialty. +2 dots of migrating Herd. Prey Exclusion: Locals. Always on the move.",
        "grants": [
            {"kind": "specialty", "options": [
                {"skill": "skill_survival", "name": "The Road"},
                {"skill": "skill_investigation", "name": "Vampire Cant"}]},
            {"kind": "discipline", "options": ["disc_fortitude", "disc_protean"]},
            {"kind": "fixed", "list": "backgrounds", "name": "Herd (migrating)", "dots": 2},
            {"kind": "fixed", "list": "flaws", "name": "Prey Exclusion (Locals)", "dots": 1},
        ],
    },
    "Sandman": {
        "benefits": "+1 Auspex OR Obfuscate. Medicine (Anesthetics) OR Stealth (Break-in) specialty. Gain Resources (•). Feeds on sleeping mortals.",
        "grants": [
            {"kind": "specialty", "options": [
                {"skill": "skill_medicine", "name": "Anesthetics"},
                {"skill": "skill_stealth", "name": "Break-in"}]},
            {"kind": "discipline", "options": ["disc_auspex", "disc_obfuscate"]},
            {"kind": "fixed", "list": "backgrounds", "name": "Resources", "dots": 1},
        ],
    },
    "Scene Queen": {
        "benefits": "+1 Dominate OR Potence. Etiquette/Leadership/Streetwise (a scene) specialty. Fame (•) + Contacts (•). Disliked (•) OR Prey Exclusion (•). Feeds within a subculture.",
        "grants": [
            {"kind": "specialty", "options": [
                {"skill": "skill_etiquette", "name": "Specific Scene"},
                {"skill": "skill_leadership", "name": "Specific Scene"},
                {"skill": "skill_streetwise", "name": "Specific Scene"}]},
            {"kind": "discipline", "options": ["disc_dominate", "disc_potence"]},
            {"kind": "fixed", "list": "backgrounds", "name": "Fame", "dots": 1},
            {"kind": "fixed", "list": "backgrounds", "name": "Contacts", "dots": 1},
            {"kind": "choice", "prompt": "Flaw", "options": [
                {"kind": "fixed", "list": "flaws", "name": "Disliked (outside subculture)", "dots": 1},
                {"kind": "fixed", "list": "flaws", "name": "Prey Exclusion (other subculture)", "dots": 1}]},
        ],
    },
    "Siren": {
        "benefits": "+1 Fortitude OR Presence. Persuasion (Seduction) OR Subterfuge (Seduction) specialty. Beautiful (••) merit, but Enemy (•) from a spurned partner. Feeds under the guise of sex.",
        "grants": [
            {"kind": "specialty", "options": [
                {"skill": "skill_persuasion", "name": "Seduction"},
                {"skill": "skill_subterfuge", "name": "Seduction"}]},
            {"kind": "discipline", "options": ["disc_fortitude", "disc_presence"]},
            {"kind": "fixed", "list": "merits", "name": "Beautiful", "dots": 2},
            {"kind": "fixed", "list": "flaws", "name": "Enemy (spurned lover)", "dots": 1},
        ],
    },
    "Tithe Collector": {
        "benefits": "Staff opt-in. +1 Dominate OR Presence. Intimidation (Kindred) OR Leadership (Kindred) specialty. 3 dots across Domain & Status, but Adversary (••). Fed by tributes of vessels. (In Memoriam.)",
        "grants": [
            {"kind": "specialty", "options": [
                {"skill": "skill_intimidation", "name": "Kindred"},
                {"skill": "skill_leadership", "name": "Kindred"}]},
            {"kind": "discipline", "options": ["disc_dominate", "disc_presence"]},
            {"kind": "pool", "list": "backgrounds", "dots": 3, "options": ["Domain", "Status"]},
            {"kind": "fixed", "list": "flaws", "name": "Adversary", "dots": 2},
        ],
    },
    "Trapdoor": {
        "benefits": "+1 Protean OR Obfuscate. Persuasion (Marketing) OR Stealth (Ambushes) specialty. Haven (•) + a dot of Retainers/Herd/2nd Haven, but a Creepy/Haunted (•) haven flaw. Lures prey to its lair.",
        "grants": [
            {"kind": "specialty", "options": [
                {"skill": "skill_persuasion", "name": "Marketing"},
                {"skill": "skill_stealth", "name": "Ambushes"}]},
            {"kind": "discipline", "options": ["disc_protean", "disc_obfuscate"]},
            {"kind": "fixed", "list": "backgrounds", "name": "Haven", "dots": 1},
            {"kind": "choice", "prompt": "Extra background (•)", "options": [
                {"kind": "fixed", "list": "backgrounds", "name": "Retainers", "dots": 1},
                {"kind": "fixed", "list": "backgrounds", "name": "Herd", "dots": 1},
                {"kind": "fixed", "list": "backgrounds", "name": "Haven (2nd dot)", "dots": 1}]},
            {"kind": "choice", "prompt": "Haven Flaw", "options": [
                {"kind": "fixed", "list": "flaws", "name": "Haven: Creepy", "dots": 1},
                {"kind": "fixed", "list": "flaws", "name": "Haven: Haunted", "dots": 1}]},
        ],
    },
}


# ── Chargen spreads (Standard V5) ───────────────────────────────────────────
# Skill distributions — counts of skills at each dot level. The player picks
# one; the wizard tracks placement against it (soft-guidance, staff verifies).
V5_SKILL_SPREADS: dict[str, dict] = {
    "jack":       {"label": "Jack-of-all-Trades", "levels": {3: 1, 2: 8, 1: 10},
                   "blurb": "One 3, eight 2s, ten 1s — broad and shallow."},
    "balanced":   {"label": "Balanced",           "levels": {3: 3, 2: 5, 1: 7},
                   "blurb": "Three 3s, five 2s, seven 1s — a steady mix."},
    "specialist": {"label": "Specialist",         "levels": {4: 1, 3: 3, 2: 3, 1: 3},
                   "blurb": "One 4, three 3s, three 2s, three 1s — deep and narrow."},
}

# Discipline distributions. Non-ancilla Kindred use the standard 2+1 in-clan
# spread; In Memoriam ancilla pick Focused / Strategic on the Memoriam step.
# Every Kindred ALSO gets +1 free Discipline dot from their predator type, so
# the wizard tracker target = spread total + PREDATOR_FREE_DISCIPLINE_DOTS.
V5_DISCIPLINE_SPREADS: dict[str, dict] = {
    "standard":  {"label": "Standard (2 + 1)",          "levels": {2: 1, 1: 1}, "total": 3,
                  "blurb": "Two dots in one in-clan Discipline, one in another."},
    "focused":   {"label": "Focused (3 + 1 + 1)",       "levels": {3: 1, 1: 2}, "total": 5,
                  "blurb": "Three in one Discipline, one each in two others. (Ancilla / In Memoriam.)"},
    "strategic": {"label": "Strategic (2 + 2 + 1 + 1)", "levels": {2: 2, 1: 2}, "total": 6,
                  "blurb": "Two each in two Disciplines, one each in two more. (Ancilla / In Memoriam.)"},
}
PREDATOR_FREE_DISCIPLINE_DOTS = 1


# ── Chargen RAW validation (Standard ruleset) ───────────────────────────────
# Enforce the V5 priority-allocation spreads server-side. These check the BASE
# allocation — a trait's dots BEFORE starting-XP purchases — because the wizard
# folds bought dots into the trait value and keeps the purchase ledger in
# `xp_buys` (each entry is one dot: {cat, key, label, cost}). Standard ruleset
# only; callers skip this when the chronicle runs homebrew budgets.

# Base attribute spread: one 4, three 3s, four 2s, one 1.
V5_ATTRIBUTE_SPREAD: tuple[int, ...] = (4, 3, 3, 3, 2, 2, 2, 2, 1)

_ATTR_KEYS: list[str] = [k for _, traits in V5_ATTRIBUTES for k, _ in traits]
_SKILL_KEYS: list[str] = [k for _, traits in V5_SKILLS for k, _ in traits]


def _xp_bought_dots(sheet: dict, key: str) -> int:
    """Dots of `key` bought with starting XP. Each xp_buys entry is one dot."""
    return sum(
        1 for b in (sheet.get("xp_buys") or [])
        if isinstance(b, dict) and b.get("key") == key
    )


def base_trait_value(sheet: dict, key: str) -> int:
    """A trait's value BEFORE starting-XP buys (its priority-spread dots)."""
    try:
        final = int(sheet.get(key, 0) or 0)
    except (TypeError, ValueError):
        final = 0
    return max(0, final - _xp_bought_dots(sheet, key))


def _spread_shape(levels: dict) -> list[int]:
    """Turn a spread's {dot_level: count} into a sorted-desc list of dot values."""
    shape: list[int] = []
    for lvl, n in levels.items():
        shape.extend([int(lvl)] * int(n))
    return sorted(shape, reverse=True)


def _disc_keys() -> list[str]:
    return [k for k, _ in V5_DISCIPLINES]


def _predator_disc_options(predator_type: str | None) -> set[str]:
    """Disciplines a predator type can grant a free dot in (its choice options) —
    allowed at creation even when out-of-clan."""
    info = V5_PREDATOR_INFO.get(predator_type or "")
    opts: set[str] = set()
    if info:
        for g in info.get("grants", []):
            if g.get("kind") == "discipline":
                opts.update(g.get("options", []) or [])
    return opts


def _disc_alloc_ok(base_dots: list[int], spread_shape: list[int], free: int) -> bool:
    """True if the Discipline base allocation matches the spread plus the
    predator's free dot. `base_dots` = each Discipline's pre-XP value; `free` is
    0 or 1 (PREDATOR_FREE_DISCIPLINE_DOTS). The free dot may land on any allowed
    Discipline, so for free=1 we accept any single-dot removal that yields the
    spread shape — covering 2+1+1, 3+1, and 2+2 for the standard 2+1."""
    base = sorted((d for d in base_dots if d > 0), reverse=True)
    target = sorted((d for d in spread_shape if d > 0), reverse=True)
    if free <= 0:
        return base == target
    if sum(base) != sum(target) + free:
        return False
    for i in range(len(base)):
        reduced = sorted(
            (d for d in (base[:i] + [base[i] - 1] + base[i + 1:]) if d > 0),
            reverse=True)
        if reduced == target:
            return True
    return False


def validate_chargen_raw(
    sheet: dict, *, character_type: str = "kindred",
    clan: str = "", predator_type: str | None = None,
    advantage_pool: int | None = None, flaw_cap: int | None = None,
    flaw_min: int = 2,
) -> list[str]:
    """Validate a chargen sheet against V5 "rules as written" priority spreads.

    Checks the BASE allocation (before starting-XP): attributes must be the
    4/3/3/3/2/2/2/2/1 spread, and skills must match one of the three
    distributions (Jack-of-all-Trades / Balanced / Specialist). Returns a list
    of human-readable errors (empty == valid). Standard ruleset only — callers
    skip this when the chronicle runs homebrew budgets.
    """
    errors: list[str] = []

    # Attributes — the multiset of base values must equal the V5 spread.
    attr_base = sorted((base_trait_value(sheet, k) for k in _ATTR_KEYS), reverse=True)
    if attr_base != sorted(V5_ATTRIBUTE_SPREAD, reverse=True):
        errors.append(
            "Attributes must use the V5 spread — one at 4, three at 3, four at 2, "
            f"one at 1 (before starting XP). Your base spread is {attr_base}."
        )

    # Skills — base allocation must match one of the three distributions exactly.
    skill_base = sorted(
        (v for v in (base_trait_value(sheet, k) for k in _SKILL_KEYS) if v > 0),
        reverse=True,
    )
    shapes = {slug: _spread_shape(spr["levels"]) for slug, spr in V5_SKILL_SPREADS.items()}
    chosen = (sheet.get("skill_spread") or "").strip().lower()
    if chosen in shapes:
        if skill_base != shapes[chosen]:
            spr = V5_SKILL_SPREADS[chosen]
            errors.append(
                f"Skills don't match the chosen {spr['label']} distribution "
                f"(before starting XP). {spr['blurb']}"
            )
    elif skill_base not in shapes.values():
        errors.append(
            "Skills must match one of the three distributions — Jack-of-all-Trades, "
            "Balanced, or Specialist (before starting XP)."
        )

    # Specialties — V5 grants one free Skill specialty plus one more for each
    # dotted Academics / Craft / Performance / Science. The player must assign at
    # least that many; predator-granted specialties carry `src` and don't count
    # toward the free allotment.
    free_specialties = 1 + sum(
        1 for k in ("skill_academics", "skill_science", "skill_craft", "skill_performance")
        if int(sheet.get(k, 0) or 0) > 0
    )
    player_specialties = [
        s for s in (sheet.get("specialties") or [])
        if isinstance(s, dict) and not s.get("src")
    ]
    if len(player_specialties) < free_specialties:
        errors.append(
            f"Assign your {free_specialties} free Skill "
            f"{'specialty' if free_specialties == 1 else 'specialties'} — one free, plus "
            "one for each dotted Academics, Craft, Performance, or Science "
            f"(you have {len(player_specialties)})."
        )

    # No trait may reach 5 at creation — Attributes, Skills, and Disciplines cap
    # at 4 dots (a 5th dot comes later, through play).
    if any(int(sheet.get(k, 0) or 0) >= 5
           for k in (_ATTR_KEYS + _SKILL_KEYS + _disc_keys())):
        errors.append(
            "Nothing can be raised to 5 at creation — Attributes, Skills, and "
            "Disciplines cap at 4 dots."
        )

    # Disciplines (Kindred) — base (pre-XP) dots must sit in in-clan Disciplines.
    # A predator type may grant one out-of-clan exception. Caitiff (no in-clan
    # list) and thin-bloods are exempt here; out-of-clan dots bought with starting
    # XP are fine (they're not part of the base allocation).
    if character_type == "kindred":
        inclan = CLAN_DISCIPLINES.get(clan)
        if inclan is not None:
            allowed = set(inclan) | _predator_disc_options(predator_type)
            disc_labels = dict(V5_DISCIPLINES)
            for k in _disc_keys():
                if base_trait_value(sheet, k) > 0 and k not in allowed:
                    errors.append(
                        f"{disc_labels.get(k, k)} isn't in-clan for "
                        f"{clan or 'your clan'} — base Discipline dots must be in-clan "
                        "(a predator type may grant one exception); out-of-clan "
                        "Disciplines can be bought with XP."
                    )

        # Discipline COUNT / shape — base (pre-XP) dots must match the spread
        # (2+1 standard, or the chosen Ancilla spread) PLUS the predator's free
        # dot. Thin-bloods use Alchemy instead of Disciplines, so they're exempt.
        if (clan or "").strip().lower() not in ("thin-blood", "thinblood"):
            _spread_slug = (sheet.get("discipline_spread") or "standard").strip().lower()
            _spread = V5_DISCIPLINE_SPREADS.get(_spread_slug) or V5_DISCIPLINE_SPREADS["standard"]
            _free = PREDATOR_FREE_DISCIPLINE_DOTS if predator_type else 0
            _base_disc = [base_trait_value(sheet, k) for k in _disc_keys()]
            if not _disc_alloc_ok(_base_disc, _spread_shape(_spread["levels"]), _free):
                errors.append(
                    f"Disciplines must follow the {_spread['label']} spread"
                    + (" plus your predator's free dot" if _free else "")
                    + f" — {_spread['total'] + _free} base dots before starting XP. "
                    + _spread["blurb"]
                )

    # Advantages — player-added (no `src`) Merits + Backgrounds + Advantages must
    # fit the pool; Flaws must hit the minimum and not exceed the cap. Auto-granted
    # entries (clan bane, predator grants) carry a `src` tag and don't count.
    if advantage_pool is not None:
        def _player_dots(list_key: str) -> int:
            total = 0
            for it in (sheet.get(list_key) or []):
                if isinstance(it, dict) and not it.get("src"):
                    try:
                        total += int(it.get("dots", 0) or 0)
                    except (TypeError, ValueError):
                        pass
            return total

        # Loresheets count the same as Merits/Backgrounds — they draw the same
        # Advantages pool. Each picked entry costs its own level (non-cumulative),
        # so a loresheet's cost is the sum of its selected `levels`.
        def _loresheet_dots() -> int:
            total = 0
            for it in (sheet.get("loresheets") or []):
                if not (isinstance(it, dict) and not it.get("src")):
                    continue
                for lv in (it.get("levels") or []):
                    try:
                        total += int(lv)
                    except (TypeError, ValueError):
                        pass
            return total

        adv = (_player_dots("merits") + _player_dots("backgrounds")
               + _player_dots("advantages") + _loresheet_dots())
        if adv > advantage_pool:
            errors.append(
                f"Advantages (Merits + Backgrounds + Loresheets) total {adv} dots — "
                f"the limit is {advantage_pool} at creation."
            )
        flaw_dots = _player_dots("flaws")
        if flaw_dots < flaw_min:
            errors.append(
                f"Take at least {flaw_min} dots of Flaws at creation "
                f"(you have {flaw_dots})."
            )
        if flaw_cap is not None and flaw_dots > flaw_cap:
            errors.append(
                f"Flaws total {flaw_dots} dots — the limit is {flaw_cap} at creation."
            )

    return errors
