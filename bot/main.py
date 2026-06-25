"""main.py — Enoch Discord bot entry point.

Run as:  python -m bot.main
The Procfile worker process uses this as its command.
"""
import asyncio
import logging

import discord
from discord.ext import commands

from .config import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s  %(name)s  %(message)s",
)
log = logging.getLogger(__name__)

_COGS = [
    "bot.cogs.outbox",
    "bot.cogs.xp",
    "bot.cogs.characters",
    "bot.cogs.coteries",
    "bot.cogs.roll",
    "bot.cogs.timeskip",
    "bot.cogs.projects",
    "bot.cogs.staff",
    "bot.cogs.settings",
    "bot.cogs.reference",
]


class EnochBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.members = True  # needed to fetch_user for DMs
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self) -> None:
        for cog in _COGS:
            await self.load_extension(cog)
            log.info("Loaded cog: %s", cog)

        # Sync slash commands to the configured guild for instant propagation,
        # or globally if no guild is set.
        if settings.DISCORD_GUILD_ID:
            guild = discord.Object(id=settings.DISCORD_GUILD_ID)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            log.info("Slash commands synced to guild %d", settings.DISCORD_GUILD_ID)
        else:
            await self.tree.sync()
            log.info("Slash commands synced globally (propagation may take up to 1 hour)")

    async def on_ready(self) -> None:
        assert self.user is not None
        log.info("Enoch ready — logged in as %s (id=%d)", self.user, self.user.id)
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name="the night",
            )
        )

    async def on_app_command_error(
        self,
        interaction: discord.Interaction,
        error: discord.app_commands.AppCommandError,
    ) -> None:
        log.error("App command error: %s", error)
        msg = "Something went wrong. Please try again or contact a Storyteller."
        try:
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
        except Exception:
            pass


async def main() -> None:
    if not settings.DISCORD_BOT_TOKEN:
        raise RuntimeError(
            "DISCORD_BOT_TOKEN is not set. "
            "Add it to your .env file or Railway service variables."
        )
    async with EnochBot() as bot:
        await bot.start(settings.DISCORD_BOT_TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
