"""Fill the official V5 fillable character-sheet PDF with a character's data.

The blank sheet (``packages/assets/v5_character_sheet.pdf``) is Paradox /
Renegade's official "VtM5e 2-page mini" AcroForm, distributed free for personal
/ fan use — the same sheet Progeny fills. We pour the character's data into its
existing form fields with pypdf and stream the result (no rendering). The field
names + mapping mirror Progeny's ``pdfCreator.ts``; four skill checkbox names
are irregular in the PDF and are noted below.
"""
from __future__ import annotations

from io import BytesIO
from pathlib import Path

from pypdf import PdfReader, PdfWriter

from .v5_traits import V5_DISCIPLINES

_PDF_PATH = (Path(__file__).resolve().parent.parent
             / "packages" / "assets" / "v5_character_sheet.pdf")

# Attribute sheet-key -> PDF checkbox prefix (Str-1 … Str-5).
_ATTR_PREFIX = {
    "attr_strength": "Str", "attr_dexterity": "Dex", "attr_stamina": "Sta",
    "attr_charisma": "Cha", "attr_manipulation": "Man", "attr_composure": "Com",
    "attr_intelligence": "Int", "attr_wits": "Wit", "attr_resolve": "Res",
}

# Skill sheet-key -> (checkbox prefix, specialty text field). Four are irregular
# in the PDF: Firearms→Fri/specFir, Leadership→Lead/specLea, Stealth→Ste/specStea,
# Streetwise→Stre/specStree.
_SKILL_FIELDS = {
    "skill_athletics": ("Ath", "specAth"), "skill_brawl": ("Bra", "specBra"),
    "skill_craft": ("Cra", "specCra"), "skill_drive": ("Dri", "specDri"),
    "skill_firearms": ("Fri", "specFir"), "skill_melee": ("Mel", "specMel"),
    "skill_larceny": ("Lar", "specLar"), "skill_stealth": ("Ste", "specStea"),
    "skill_survival": ("Sur", "specSur"),
    "skill_animal_ken": ("AniKen", "specAniKen"), "skill_etiquette": ("Etiq", "specEtiq"),
    "skill_insight": ("Insi", "specInsi"), "skill_intimidation": ("Inti", "specInti"),
    "skill_leadership": ("Lead", "specLea"), "skill_performance": ("Perf", "specPerf"),
    "skill_persuasion": ("Pers", "specPers"), "skill_streetwise": ("Stre", "specStree"),
    "skill_subterfuge": ("Subt", "specSubt"),
    "skill_academics": ("Acad", "specAcad"), "skill_awareness": ("Awar", "specAwar"),
    "skill_finance": ("Fina", "specFina"), "skill_investigation": ("Inve", "specInve"),
    "skill_medicine": ("Medi", "specMedi"), "skill_occult": ("Occu", "specOccu"),
    "skill_politics": ("Poli", "specPoli"), "skill_science": ("Scie", "specScie"),
    "skill_technology": ("Tech", "specTech"),
}

_ON = "/Yes"   # the AcroForm checkbox on-state in this sheet


def _ints(s: dict, key: str, default: int = 0) -> int:
    try:
        return int(s.get(key) or default)
    except (TypeError, ValueError):
        return default


def _dot_checks(prefix: str, value: int, cap: int = 5) -> dict:
    """{prefix-1: on … prefix-N: on} for a 0..cap rating."""
    return {f"{prefix}-{i}": _ON for i in range(1, min(cap, max(0, value)) + 1)}


def fill_character_pdf(char: dict) -> bytes:
    """Return the official sheet filled with this character's data (PDF bytes)."""
    s = char.get("sheet_json") or {}
    text: dict = {}
    checks: dict = {}

    # ── Identity ────────────────────────────────────────────────────────
    text["Name"] = char.get("name") or ""
    text["Clan"] = (char.get("clan") or "").replace("-", " ").title()
    text["Predator type"] = char.get("predator_type") or ""
    text["Ambition"] = char.get("ambition") or ""
    text["Desire"] = char.get("desire") or ""
    text["Sire"] = char.get("sire") or ""
    text["pcConcept"] = char.get("concept") or ""
    text["Sect"] = char.get("covenant") or ""
    if s.get("generation"):
        text["Title"] = f"{s['generation']}th Generation"   # PDF "Title" = generation
    text["cEXP"] = str(char.get("xp_total") or 0)
    text["tEXP"] = str((char.get("xp_total") or 0) - (char.get("xp_spent") or 0))

    # ── Attributes ──────────────────────────────────────────────────────
    for key, pref in _ATTR_PREFIX.items():
        checks.update(_dot_checks(pref, _ints(s, key)))

    # ── Skills + specialties ────────────────────────────────────────────
    specs_by_skill: dict = {}
    for sp in (s.get("specialties") or []):
        if sp.get("skill") and sp.get("name"):
            specs_by_skill.setdefault(sp["skill"], []).append(sp["name"])
    for key, (pref, spec_field) in _SKILL_FIELDS.items():
        checks.update(_dot_checks(pref, _ints(s, key)))
        if specs_by_skill.get(key):
            text[spec_field] = ", ".join(specs_by_skill[key])

    # ── Vitals tracks (Humanity is an image overlay in the sheet — skip) ─
    checks.update(_dot_checks("Health", _ints(s, "attr_stamina") + 3, cap=15))
    checks.update(_dot_checks("WP", _ints(s, "attr_composure") + _ints(s, "attr_resolve"), cap=15))
    checks.update(_dot_checks("BloodPotency", _ints(s, "blood_potency"), cap=10))

    # ── Disciplines → Disc1…Disc6 (name + dots + power names) ───────────
    learned = [(k, lbl) for k, lbl in V5_DISCIPLINES if _ints(s, k) > 0]
    powers = s.get("powers") or []
    for idx, (key, lbl) in enumerate(learned[:6], start=1):
        text[f"Disc{idx}"] = lbl
        checks.update(_dot_checks(f"Disc{idx}", _ints(s, key)))
        disc_powers = [p for p in powers if p.get("discipline") == key]
        for j, p in enumerate(disc_powers[:5], start=1):
            nm = p.get("name", "")
            if p.get("level"):
                nm = f"{nm} ({p['level']})"
            text[f"Disc{idx}_Ability{j}"] = nm

    # ── Merits / Backgrounds / Advantages / Flaws → Merit1…Merit21 ──────
    slot = 1
    for list_key in ("merits", "backgrounds", "advantages", "flaws"):
        for m in (s.get(list_key) or []):
            if slot > 21:
                break
            nm = m.get("name", "")
            if m.get("detail"):
                nm = f"{nm} ({m['detail']})"
            text[f"Merit{slot}"] = nm
            try:
                checks.update(_dot_checks(f"Merit{slot}", int(m.get("dots") or 0)))
            except (TypeError, ValueError):
                pass
            slot += 1

    # ── Touchstones / Convictions ───────────────────────────────────────
    ts = [t for t in (s.get("touchstones") or []) if t.get("name") or t.get("conviction")]
    if ts:
        text["Convictions"] = "  •  ".join(t["conviction"] for t in ts if t.get("conviction"))
        text["touchstoneNotes"] = "  •  ".join(t["name"] for t in ts if t.get("name"))

    # ── Write the filled sheet ──────────────────────────────────────────
    reader = PdfReader(str(_PDF_PATH))
    writer = PdfWriter()
    writer.append(reader)
    values = {k: v for k, v in {**text, **checks}.items() if v not in (None, "")}
    for page in writer.pages:
        writer.update_page_form_field_values(page, values, auto_regenerate=False)
    # Tell viewers to regenerate appearances so filled values show everywhere.
    try:
        writer.set_need_appearances_writer(True)
    except Exception:
        pass
    buf = BytesIO()
    writer.write(buf)
    return buf.getvalue()
