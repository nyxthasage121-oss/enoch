"""outbox.py — Background task that drains the bot_outbox and DMs players."""
import logging

import discord
from discord.ext import commands, tasks

from ..api import ack_outbox, drain_outbox
from ..config import settings

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
            try:
                await ack_outbox(item["id"], success=False, error=str(exc))
            except Exception:
                pass


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(OutboxCog(bot))
