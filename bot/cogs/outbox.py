"""outbox.py — Background task that drains the bot_outbox and DMs players."""
import logging

import discord
from discord.ext import commands, tasks

from ..api import ack_outbox, drain_outbox, report_alert
from ..config import settings
from .roll import build_posted_roll_embed

log = logging.getLogger(__name__)

# ── Handler registry ──────────────────────────────────────────────────────────

_HANDLERS: dict = {}


def _handler(command: str):
    """Register a coroutine as the handler for a given outbox command."""
    def decorator(fn):
        _HANDLERS[command] = fn
        return fn
    return decorator


# ── DM helper ─────────────────────────────────────────────────────────────────

async def _dm(bot: commands.Bot, discord_id: str, embed: discord.Embed) -> None:
    """Send an embed to a user via DM. Silently swallows permission errors."""
    try:
        user = await bot.fetch_user(int(discord_id))
        await user.send(embed=embed)
    except discord.Forbidden:
        log.warning("Cannot DM user %s — DMs disabled", discord_id)
    except discord.NotFound:
        log.warning("User %s not found on Discord", discord_id)
    except Exception as exc:
        log.error("DM to %s failed: %s", discord_id, exc)


# ── Command handlers ──────────────────────────────────────────────────────────

@_handler("character_approved")
async def _on_character_approved(bot: commands.Bot, p: dict) -> None:
    e = discord.Embed(
        title="✅ Character Approved",
        description="Your character has been approved by staff and is now **active**.",
        color=0xC8A85B,
    )
    await _dm(bot, p["discord_id"], e)


@_handler("character_rejected")
async def _on_character_rejected(bot: commands.Bot, p: dict) -> None:
    reason = p.get("reason") or "No reason provided."
    e = discord.Embed(
        title="📋 Character Returned",
        description=(
            f"Your character submission was returned by staff.\n\n**Reason:** {reason}\n\n"
            "Please revise your character and resubmit."
        ),
        color=0x8B1A1A,
    )
    await _dm(bot, p["discord_id"], e)


@_handler("claim_approved")
async def _on_claim_approved(bot: commands.Bot, p: dict) -> None:
    awarded = p.get("xp_awarded", 0)
    capped  = p.get("capped", False)
    desc    = f"**+{awarded} XP** awarded to your character."
    if capped:
        desc += (
            "\n\n⚠️ **XP Cap Reached** — your character has earned the maximum 350 XP. "
            "No further XP will be awarded. Please speak with staff about retirement options."
        )
    e = discord.Embed(title="✅ XP Claim Approved", description=desc, color=0xC8A85B)
    await _dm(bot, p["discord_id"], e)


@_handler("claim_rejected")
async def _on_claim_rejected(bot: commands.Bot, p: dict) -> None:
    reason = p.get("reason") or "No reason provided."
    e = discord.Embed(
        title="❌ XP Claim Rejected",
        description=f"**Reason:** {reason}",
        color=0x8B1A1A,
    )
    await _dm(bot, p["discord_id"], e)


@_handler("spend_approved")
async def _on_spend_approved(bot: commands.Bot, p: dict) -> None:
    trait = p.get("trait_name") or "Unknown trait"
    cost  = p.get("xp_cost", 0)
    e = discord.Embed(
        title="✅ XP Spend Approved",
        description=f"**{trait}** has been approved — **{cost} XP** spent.",
        color=0xC8A85B,
    )
    await _dm(bot, p["discord_id"], e)


@_handler("spend_rejected")
async def _on_spend_rejected(bot: commands.Bot, p: dict) -> None:
    reason = p.get("reason") or "No reason provided."
    e = discord.Embed(
        title="❌ XP Spend Rejected",
        description=f"**Reason:** {reason}",
        color=0x8B1A1A,
    )
    await _dm(bot, p["discord_id"], e)


@_handler("character_retired")
async def _on_character_retired(bot: commands.Bot, p: dict) -> None:
    name = p.get("name") or "Your character"
    e = discord.Embed(
        title="🪦 Character Retired",
        description=(
            f"**{name}** has reached the end of the 6-month retirement window "
            "after hitting the XP cap and is now marked as **retired**.\n\n"
            "Please reach out to staff about epilogue or starting a new character."
        ),
        color=0x8B1A1A,
    )
    await _dm(bot, p["discord_id"], e)


@_handler("background_released")
async def _on_background_released(bot: commands.Bot, p: dict) -> None:
    char = p.get("character_name") or "Your character"
    name = p.get("name") or "a background"
    dots = p.get("dots_released", 0)
    e = discord.Embed(
        title="🌙 Background Restored",
        description=(
            f"**{char}**'s **{name}** is available again — "
            f"**{dots}** blanked dot(s) refreshed as the new night opened."
        ),
        color=0xC8A85B,
    )
    await _dm(bot, p["discord_id"], e)


# ── Project events ────────────────────────────────────────────────────────────

@_handler("project_approved")
async def _on_project_approved(bot: commands.Bot, p: dict) -> None:
    name  = p.get("project_name") or "Your project"
    desc  = f"**{name}** has been approved by staff and is now active."
    if p.get("progress_type") == "roll":
        desc += "\n\nWork it down with `/project roll` once each night."
    else:
        desc += "\n\nStaff will track its progress over your downtimes."
    e = discord.Embed(title="📜 Project Approved", description=desc, color=0xC8A85B)
    await _dm(bot, p["discord_id"], e)


@_handler("project_rejected")
async def _on_project_rejected(bot: commands.Bot, p: dict) -> None:
    name   = p.get("project_name") or "Your project"
    reason = p.get("reason") or "No reason provided."
    e = discord.Embed(
        title="❌ Project Returned",
        description=f"Your project **{name}** was returned by staff.\n\n"
                    f"**Reason:** {reason}",
        color=0x8B1A1A,
    )
    await _dm(bot, p["discord_id"], e)


@_handler("project_completed")
async def _on_project_completed(bot: commands.Bot, p: dict) -> None:
    name   = p.get("project_name") or "Your project"
    reward = p.get("reward") or "Completed."
    e = discord.Embed(
        title="📜 Project Complete",
        description=f"**{name}** is complete!\n\n**Reward:** {reward}",
        color=0xC8A85B,
    )
    await _dm(bot, p["discord_id"], e)


# ── Coterie events ────────────────────────────────────────────────────────────

@_handler("coterie_request_approved")
async def _on_coterie_request_approved(bot: commands.Bot, p: dict) -> None:
    name = p.get("coterie_name") or "Your coterie"
    e = discord.Embed(
        title="🩸 Coterie Formed",
        description=(
            f"**{name}** has been approved by staff and is now active.\n\n"
            "Use `/coterie status` to view the domain dots, members, and roles."
        ),
        color=0xC8A85B,
    )
    await _dm(bot, p["discord_id"], e)


@_handler("coterie_request_rejected")
async def _on_coterie_request_rejected(bot: commands.Bot, p: dict) -> None:
    proposed = p.get("proposed_name") or "Your coterie request"
    reason   = p.get("reason") or "No reason provided."
    e = discord.Embed(
        title="❌ Coterie Request Rejected",
        description=(
            f"Your request to form **{proposed}** was rejected by staff.\n\n"
            f"**Reason:** {reason}"
        ),
        color=0x8B1A1A,
    )
    await _dm(bot, p["discord_id"], e)


@_handler("coterie_spend_approved")
async def _on_coterie_spend_approved(bot: commands.Bot, p: dict) -> None:
    coterie = p.get("coterie_name") or "your coterie"
    trait   = (p.get("trait_name") or "domain").title()
    cur     = p.get("current_dots", 0)
    new     = p.get("new_dots", 0)
    cost    = p.get("per_member_cost", 0)
    e = discord.Embed(
        title="✅ Coterie Domain Upgraded",
        description=(
            f"**{coterie}** has upgraded **{trait}** from {cur} → {new}.\n\n"
            f"**{cost} XP** has been deducted from your character."
        ),
        color=0xC8A85B,
    )
    await _dm(bot, p["discord_id"], e)


@_handler("coterie_spend_rejected")
async def _on_coterie_spend_rejected(bot: commands.Bot, p: dict) -> None:
    coterie = p.get("coterie_name") or "your coterie"
    trait   = (p.get("trait_name") or "domain").title()
    reason  = p.get("reason") or "No reason provided."
    e = discord.Embed(
        title="❌ Coterie Spend Rejected",
        description=(
            f"**{coterie}**'s domain upgrade for **{trait}** was rejected by staff.\n\n"
            f"**Reason:** {reason}\n\n"
            "No XP was deducted."
        ),
        color=0x8B1A1A,
    )
    await _dm(bot, p["discord_id"], e)


# ── Period events ─────────────────────────────────────────────────────────────

@_handler("period_closing_soon")
async def _on_period_closing_soon(bot: commands.Bot, p: dict) -> None:
    """Post a closing-soon announcement to the chronicle channel.
    Silent no-op if CHRONICLE_CHANNEL_ID isn't configured."""
    channel_id = settings.CHRONICLE_CHANNEL_ID
    if not channel_id:
        log.info("period_closing_soon: CHRONICLE_CHANNEL_ID unset — skipping announcement")
        return

    try:
        channel = bot.get_channel(channel_id) or await bot.fetch_channel(channel_id)
    except discord.NotFound:
        log.warning("Chronicle channel %s not found", channel_id)
        return
    except discord.Forbidden:
        log.warning("Bot lacks access to chronicle channel %s", channel_id)
        return

    label  = p.get("label") or "the current XP window"
    closes = p.get("closes_at") or ""
    ptype  = (p.get("period_type") or "").title()
    phase  = (p.get("phase") or "").title()

    e = discord.Embed(
        title="⏳ XP Window Closing Soon",
        description=(
            f"**{label}** closes in less than 24 hours.\n\n"
            "Submit any pending XP claims before the window shuts. "
            "Use `/xp submit` or visit the web roster to file."
        ),
        color=0xC8A85B,
    )
    if ptype or phase:
        e.add_field(name="Period", value=f"{ptype} · {phase}".strip(" ·"), inline=True)
    if closes:
        # Chop trailing 'Z' off ISO for cleaner display
        pretty = closes[:16].replace("T", " ")
        e.add_field(name="Closes (UTC)", value=pretty, inline=True)
    try:
        await channel.send(embed=e)
    except Exception as exc:
        log.error("Failed to post period_closing_soon: %s", exc)


# ── Dice events ───────────────────────────────────────────────────────────────

@_handler("roll_posted")
async def _on_roll_posted(bot: commands.Bot, p: dict) -> None:
    """Post a web-originated dice roll to the chronicle's dice channel. The
    channel id rides in on the payload (resolved web-side from the
    dice_channel_id setting, migration 054), so the bot needs no extra config.
    Silent no-op if the channel is missing or unreachable."""
    raw = str(p.get("channel_id") or "").strip()
    if not raw.isdigit():
        log.warning("roll_posted: missing/invalid channel_id — skipping")
        return
    channel_id = int(raw)
    try:
        channel = bot.get_channel(channel_id) or await bot.fetch_channel(channel_id)
    except discord.NotFound:
        log.warning("roll_posted: dice channel %s not found", channel_id)
        return
    except discord.Forbidden:
        log.warning("roll_posted: bot lacks access to dice channel %s", channel_id)
        return

    embed = build_posted_roll_embed(p)
    await channel.send(embed=embed)


# ── ST tracker (vitals board) ─────────────────────────────────────────────────

_VITALS_GOLD = 0xC8A85B


def build_vitals_embeds(p: dict) -> list[discord.Embed]:
    """Render a vitals-board snapshot (from a 'vitals_posted' payload, migration
    055) as one or more Discord embeds. Chunks rows so no embed exceeds Discord's
    limits and caps at 10 embeds/message; offline-testable (no bot/channel)."""
    rows = p.get("rows") or []
    count = int(p.get("count", len(rows)))
    by = p.get("generated_by") or "Staff"

    def _line(r: dict) -> str:
        s = (f"**{r.get('name', '?')}** — H {r.get('hunger', 0)}/5 · "
             f"HP {r.get('health', '?')} · WP {r.get('wp', '?')} · "
             f"Hum {r.get('humanity', '?')} · {r.get('xp', 0)} XP")
        if r.get("player"):
            s += f"  ·  _{r['player']}_"
        if r.get("flags"):
            s += f"\n⚠ {', '.join(r['flags'])}"
        return s

    if not rows:
        e = discord.Embed(title="🩸 Chronicle Vitals", color=_VITALS_GOLD,
                          description="No active characters.")
        e.set_footer(text=f"Posted from the web tracker by {by}")
        return [e]

    CHUNK, MAX_EMBEDS = 20, 10
    pages = [rows[i:i + CHUNK] for i in range(0, len(rows), CHUNK)]
    shown, dropped = pages[:MAX_EMBEDS], rows[MAX_EMBEDS * CHUNK:]
    embeds = []
    for idx, page in enumerate(shown):
        title = "🩸 Chronicle Vitals"
        if len(shown) > 1:
            title += f" ({idx + 1}/{len(shown)})"
        e = discord.Embed(title=title, color=_VITALS_GOLD,
                          description="\n".join(_line(r) for r in page))
        if idx == 0:
            e.set_author(name=f"{count} active character" + ("" if count == 1 else "s"))
        foot = f"Posted from the web tracker by {by}"
        if idx == len(shown) - 1 and dropped:
            foot = f"… +{len(dropped)} more not shown · " + foot
        e.set_footer(text=foot)
        embeds.append(e)
    return embeds


@_handler("vitals_posted")
async def _on_vitals_posted(bot: commands.Bot, p: dict) -> None:
    """Post a vitals-board snapshot to the chronicle's ST-tracker channel. The
    channel id rides in on the payload (resolved web-side from st_channel_id,
    migration 055). Silent no-op if the channel is missing or unreachable."""
    raw = str(p.get("channel_id") or "").strip()
    if not raw.isdigit():
        log.warning("vitals_posted: missing/invalid channel_id — skipping")
        return
    channel_id = int(raw)
    try:
        channel = bot.get_channel(channel_id) or await bot.fetch_channel(channel_id)
    except discord.NotFound:
        log.warning("vitals_posted: ST channel %s not found", channel_id)
        return
    except discord.Forbidden:
        log.warning("vitals_posted: bot lacks access to ST channel %s", channel_id)
        return

    await channel.send(embeds=build_vitals_embeds(p))


# ── Cog ───────────────────────────────────────────────────────────────────────

class OutboxCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.poll_outbox.start()

    def cog_unload(self) -> None:
        self.poll_outbox.cancel()

    @tasks.loop(seconds=settings.OUTBOX_POLL_INTERVAL)
    async def poll_outbox(self) -> None:
        try:
            items = await drain_outbox()
        except Exception as exc:
            log.warning("Outbox drain failed: %s", exc)
            return

        for item in items:
            await self._process(item)

    @poll_outbox.before_loop
    async def _before_poll(self) -> None:
        await self.bot.wait_until_ready()

    @poll_outbox.error
    async def _poll_error(self, error: Exception) -> None:
        log.error("Outbox poll task raised: %s — restarting", error)
        self.poll_outbox.restart()

    async def _process(self, item: dict) -> None:
        cmd     = item["command"]
        payload = item["payload"]   # already a dict — drain_outbox parses JSON
        fn      = _HANDLERS.get(cmd)

        try:
            if fn:
                await fn(self.bot, payload)
            else:
                log.warning("No handler registered for outbox command: %s", cmd)
            await ack_outbox(item["id"], success=True)
        except Exception as exc:
            log.error("Failed to process outbox %d (%s): %s", item["id"], cmd, exc)
            await report_alert("error", "outbox",
                               f"Failed to process outbox {item['id']} ({cmd}): {exc}")
            try:
                await ack_outbox(item["id"], success=False, error=str(exc))
            except Exception:
                pass


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(OutboxCog(bot))
