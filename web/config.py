"""config.py — All environment variables in one place."""
import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    # ── Database ─────────────────────────────────────────────────
    # `or` (not a getenv default) so an empty-string env var — e.g. a blank
    # DATABASE_URL set in a host's dashboard — still falls back to a real
    # on-disk file instead of `sqlite3.connect("")`, which hands out a throwaway
    # temp DB per connection and breaks migrations on first boot.
    DATABASE_URL: str          = os.getenv("DATABASE_URL") or "enoch.db"
    TURSO_AUTH_TOKEN: str|None = os.getenv("TURSO_AUTH_TOKEN")

    # ── Session ──────────────────────────────────────────────────
    SESSION_SECRET: str = os.getenv("SESSION_SECRET", "dev-secret-change-in-production")

    # ── Discord OAuth ─────────────────────────────────────────────
    DISCORD_CLIENT_ID: str|None     = os.getenv("DISCORD_CLIENT_ID")
    DISCORD_CLIENT_SECRET: str|None = os.getenv("DISCORD_CLIENT_SECRET")
    DISCORD_REDIRECT_URI: str       = os.getenv(
        "DISCORD_REDIRECT_URI", "http://localhost:8000/auth/callback"
    )

    # ── Discord Guild ─────────────────────────────────────────────
    # Primary NYbN server ID
    DISCORD_GUILD_ID: int|None = (
        int(os.getenv("DISCORD_GUILD_ID") or 0) or None
    )
    # Comma-separated role IDs that grant staff access
    STAFF_ROLE_IDS: list[int] = [
        int(x) for x in os.getenv("STAFF_ROLE_IDS", "").split(",") if x.strip()
    ]
    # Channel ID the bot posts period-closing reminders + chronicle
    # announcements to. Falls back to None which silently disables
    # announcements (e.g. during testing).
    CHRONICLE_CHANNEL_ID: int|None = (
        int(os.getenv("CHRONICLE_CHANNEL_ID") or 0) or None
    )

    # ── Enoch / Inconnu sync ──────────────────────────────────────
    # The guild ID used inside the Inconnu bot's tables (may differ)
    ENOCH_GUILD_ID: int|None = (
        int(os.getenv("ENOCH_GUILD_ID") or 0) or None
    )

    # ── Bot service token ─────────────────────────────────────────
    # Shared secret the Discord bot sends as Bearer token to /api/*
    BOT_SERVICE_TOKEN: str|None = os.getenv("BOT_SERVICE_TOKEN")

    # ── Webhooks ──────────────────────────────────────────────────
    PROFILE_WEBHOOK_URL: str|None = os.getenv("PROFILE_WEBHOOK_URL")

    # ── NYbN rules ────────────────────────────────────────────────
    XP_CAP: int                  = 350
    COTERIE_MAX_MEMBERS: int     = 6
    RETIREMENT_WINDOW_DAYS: int  = 180   # 6-month auto-retirement window after XP cap

    # ── Runtime ───────────────────────────────────────────────────
    # True when running on Railway (sets https_only on session cookie)
    HTTPS_ONLY: bool = os.getenv("RAILWAY_ENVIRONMENT") is not None
    DEV_PREVIEW: bool = os.getenv("ENOCH_DEV_PREVIEW") == "1"


settings = Settings()
