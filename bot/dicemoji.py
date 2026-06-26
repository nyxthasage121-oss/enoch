"""dicemoji.py — V5 dice-face emoji for roll results, Inconnu-style.

Eight emoji: normal (``ln_``) and Hunger (``h_``) dice, each rendered as one of
four V5 faces — bestial (a 1), crit (a 10), succ (6-9), fail (2-5). The bot
uploads them ONCE as *application* emoji on startup (no per-server setup), then
roll embeds render each die as its emoji, falling back to plain numbers whenever
the emoji aren't available.

Art lifted from tiltowait/inconnu (MIT, © 2021 tiltowait) — see assets/dice/.
"""
import logging
from pathlib import Path

log = logging.getLogger(__name__)

_ASSET_DIR = Path(__file__).parent / "assets" / "dice"

# Logical face keys (also the asset filenames, sans extension).
DICE_FACES = ["ln_bestial", "ln_crit", "ln_succ", "ln_fail",
              "h_bestial", "h_crit", "h_succ", "h_fail"]

# Application-emoji names are prefixed to avoid colliding with anything else.
_EMOJI_PREFIX = "enoch_"


def face_key(die: int, hunger: bool) -> str:
    """The face-emoji key for a die value — Inconnu's four-way mapping."""
    if die == 1:
        face = "bestial"
    elif die == 10:
        face = "crit"
    elif die >= 6:
        face = "succ"
    else:
        face = "fail"
    return ("h_" if hunger else "ln_") + face


def emojify(dice: list[int], hunger: bool, emoji_map: dict[str, str]) -> str:
    """Render dice as emoji via ``emoji_map`` (face_key → '<:name:id>'). Any face
    the map is missing falls back to the bare number, so this is always safe."""
    return " ".join(emoji_map.get(face_key(d, hunger), str(d)) for d in dice)


async def ensure_dice_emoji(client) -> dict[str, str]:
    """Ensure the 8 dice emoji exist as this application's emoji, uploading any
    missing ones from the bundled PNGs. Returns ``{face_key: '<:name:id>'}`` and
    stashes it on ``client.dice_emoji``. Best-effort — any failure leaves the map
    empty so rolls simply use plain numbers."""
    emoji_map: dict[str, str] = {}
    try:
        existing = {e.name: e for e in await client.fetch_application_emojis()}
        for face in DICE_FACES:
            name = _EMOJI_PREFIX + face
            emoji = existing.get(name)
            if emoji is None:
                png = (_ASSET_DIR / f"{face}.png").read_bytes()
                emoji = await client.create_application_emoji(name=name, image=png)
                log.info("Uploaded dice emoji %s", name)
            emoji_map[face] = str(emoji)
    except Exception as exc:
        log.warning("Dice emoji unavailable (rolls will use numbers): %s", exc)
        emoji_map = {}
    client.dice_emoji = emoji_map
    return emoji_map
