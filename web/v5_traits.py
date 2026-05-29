"""V5 trait reference data.

Single source of truth for the V5 sheet structure — imported by both the
player route (for the editor) and the staff route (for the read-only sheet
display). Keep player.py + staff.py in sync via this module.
"""

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


# ── Predator Type benefit summaries (paraphrased V5 RAW) ────────────────────
# Each entry lists the mechanical benefits a Predator Type grants at chargen.
# These are advisory — staff still validates the exact dot/specialty/merit
# placement during approval. Player chooses among the listed options.
#
# Restricted types (Blood Leech, Tithe Collector) are staff opt-in via
# chronicle_restrictions — the chargen picker hides them unless unlocked.
V5_PREDATOR_INFO: dict[str, dict[str, str]] = {
    "Alleycat": {
        "benefits": "+1 Celerity OR Potence. Intimidation (Stickups) OR Brawl (Grappling) specialty. Lose 1 Humanity. Gain Criminal Contacts (•••). Feeds by force or threat.",
    },
    "Bagger": {
        "benefits": "+1 Obfuscate (Blood Sorcery/Oblivion per clan). Larceny (Lock Picking) OR Streetwise (Black Market) specialty. Gain Iron Gullet (•••) AND an Enemy (••) flaw. Feeds on stored/preserved blood. Not for Ventrue.",
    },
    "Blood Leech": {
        "benefits": "Staff opt-in. +1 Celerity OR Protean. Brawl (Kindred) OR Stealth (vs Kindred) specialty. Lose 1 Humanity, +1 Blood Potency. Diablerist OR Shunned (••), plus Prey Exclusion: Mortals (••). Feeds on vampire vitae.",
    },
    "Cleaver": {
        "benefits": "+1 Dominate OR Animalism. Persuasion (Gaslighting) OR Subterfuge (Coverups) specialty. Gain Herd (••), but Dark Secret: Cleaver (•). Feeds on their own family/friends.",
    },
    "Consensualist": {
        "benefits": "+1 Auspex OR Fortitude. Medicine (Phlebotomy) OR Persuasion (Vessels) specialty. +1 Humanity. Masquerade Breacher (•) + Prey Exclusion: Non-consenting (•). Only feeds with consent.",
    },
    "Extortionist": {
        "benefits": "+1 Dominate OR Potence. Intimidation (Coercion) OR Larceny (Security) specialty. 3 dots across Contacts & Resources, but Enemy (••). Feeds in exchange for 'services'.",
    },
    "Farmer": {
        "benefits": "+1 Animalism OR Protean. Animal Ken OR Survival (Hunting) specialty. +1 Humanity. Farmer (••) feeding flaw. Feeds on animals. Not for Ventrue or Blood Potency 3+.",
    },
    "Graverobber": {
        "benefits": "+1 Fortitude OR Oblivion. Occult (Grave Rituals) OR Medicine (Cadavers) specialty. Iron Gullet (•••) + Haven (•), but Obvious Predator (••) herd flaw. Feeds on corpses/mourners.",
    },
    "Grim Reaper": {
        "benefits": "+1 Auspex OR Oblivion. Awareness (Death) OR Larceny (Forgery) specialty. +1 Humanity. Allies/Influence (•) in medicine. Prey Exclusion: Healthy Mortals (•). Feeds on the dying.",
    },
    "Montero": {
        "benefits": "+1 Dominate OR Obfuscate. Leadership (Hunting Pack) OR Stealth (Stakeout) specialty. Gain Retainers (••). Lose 1 Humanity. Retainers drive prey to the hunter.",
    },
    "Osiris": {
        "benefits": "+1 Blood Sorcery OR Presence. Occult OR Performance specialty. 3 dots across Fame & Herd; 2 dots across Enemies & Mythic flaws. Feeds on fans/followers.",
    },
    "Pursuer": {
        "benefits": "+1 Animalism OR Auspex. Investigation (Profiling) OR Stealth (Shadowing) specialty. Gain Bloodhound (•) merit + Contacts (•). Lose 1 Humanity. Stalks prey before striking.",
    },
    "Roadside Killer": {
        "benefits": "+1 Fortitude OR Protean. Survival (the Road) OR Investigation (Vampire Cant) specialty. +2 dots of migrating Herd. Prey Exclusion: Locals. Always on the move.",
    },
    "Sandman": {
        "benefits": "+1 Auspex OR Obfuscate. Medicine (Anesthetics) OR Stealth (Break-in) specialty. Gain Resources (•). Feeds on sleeping mortals.",
    },
    "Scene Queen": {
        "benefits": "+1 Dominate OR Potence. Etiquette/Leadership/Streetwise (a scene) specialty. Fame (•) + Contacts (•). Disliked (•) OR Prey Exclusion (•). Feeds within a subculture.",
    },
    "Siren": {
        "benefits": "+1 Fortitude OR Presence. Persuasion (Seduction) OR Subterfuge (Seduction) specialty. Beautiful (••) merit, but Enemy (•) from a spurned partner. Feeds under the guise of sex.",
    },
    "Tithe Collector": {
        "benefits": "Staff opt-in. +1 Dominate OR Presence. Intimidation (Kindred) OR Leadership (Kindred) specialty. 3 dots across Domain & Status, but Adversary (••). Fed by tributes of vessels. (In Memoriam.)",
    },
    "Trapdoor": {
        "benefits": "+1 Protean OR Obfuscate. Persuasion (Marketing) OR Stealth (Ambushes) specialty. Haven (•) + a dot of Retainers/Herd/2nd Haven, but a Creepy/Haunted (•) haven flaw. Lures prey to its lair.",
    },
}
