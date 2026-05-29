"""config.py — Bot environment settings."""
import os
from dotenv import load_dotenv

load_dotenv()


class BotSettings:
    # ── Discord ───────────────────────────────────────────────────
    DISCORD_BOT_TOKEN: str      = os.getenv("DISCORD_BOT_TOKEN", "")
    DISCORD_GUILD_ID: int | None = (
        int(os.getenv("DISCORD_GUILD_ID") or 0) or None
    )

    # ── Web API ───────────────────────────────────────────────────
    WEB_URL: str           = os.getenv("WEB_URL", "http://localhost:8000")
    BOT_SERVICE_TOKEN: str = os.getenv("BOT_SERVICE_TOKEN", "")

    # ── Tuning ────────────────────────────────────────────────────
    # How often (seconds) to poll the web API outbox
    OUTBOX_POLL_INTERVAL: int = int(os.getenv("OUTBOX_POLL_INTERVAL") or "10")


settings = BotSettings()
