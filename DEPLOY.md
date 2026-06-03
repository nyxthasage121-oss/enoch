# Enoch — Deploy Guide

End-to-end checklist to take Enoch from local dev to production on Railway
with a Turso database, Discord OAuth, and the bot wired in. Roughly 30–60
minutes if all accounts already exist.

---

## Prerequisites

- A Discord account that owns (or has Manage Server on) the NYbN server
- A Turso account — https://turso.tech (free tier is plenty)
- A Railway account — https://railway.com (free tier is plenty for staging)
- The Turso CLI installed locally: `curl -sSfL https://get.tur.so/install.sh | bash`
- Python 3.12 locally for testing

---

## Step 1 — Discord Developer Portal

You're registering one **application** that exposes both an OAuth flow (used
by the web app to log players in) and a **bot user** (used by `bot/`).

1. Go to https://discord.com/developers/applications → **New Application**.
   Name it "Enoch" (or whatever shows up nicely in OAuth consent).
2. **OAuth2 → General** tab:
   - Note the **Client ID** → `DISCORD_CLIENT_ID`
   - Click **Reset Secret** → note the **Client Secret** → `DISCORD_CLIENT_SECRET`
   - Under **Redirects**, add: `https://YOUR-RAILWAY-DOMAIN/auth/callback`
     (for local: also add `http://localhost:8000/auth/callback`)
3. **Bot** tab → **Add Bot**:
   - **Reset Token** → note the token → `DISCORD_BOT_TOKEN`
   - Toggle on **Server Members Intent** (required to resolve staff roles)
   - Toggle on **Message Content Intent** if you plan to add prefix commands later
4. **OAuth2 → URL Generator**:
   - Scopes: `bot`, `applications.commands`
   - Bot permissions: `Send Messages`, `Embed Links`, `Use Slash Commands`,
     `Send Messages in Threads` (and `Read Messages/View Channels` if posting to
     the chronicle channel)
   - Copy the generated URL, open it in a browser, invite the bot to your
     NYbN server.

### Getting Discord IDs

In Discord → User Settings → Advanced → toggle **Developer Mode** on. Then
right-click → **Copy ID** on:

- Your **NYbN server** → `DISCORD_GUILD_ID`
- Each **staff role** → comma-join into `STAFF_ROLE_IDS=12345,67890`
- The **chronicle announcements channel** → `CHRONICLE_CHANNEL_ID`
  (optional — leave blank to silence period-closing reminders)

---

## Step 2 — Turso database

```bash
turso auth signup            # one-time
turso db create enoch-prod   # creates the database
turso db show enoch-prod     # prints the libsql:// URL
turso db tokens create enoch-prod   # prints the auth token
```

Save these two values:

- The `libsql://...` URL → `DATABASE_URL`
- The token → `TURSO_AUTH_TOKEN`

Enoch's migrations (`migrations/*.sql`) run automatically on web startup
via FastAPI's `lifespan`. No manual schema setup needed.

---

## Step 3 — Railway

Enoch is two services in one repo: `web` (FastAPI) and `worker` (the bot).
The `Procfile` already declares both. Railway will pick them up.

1. **Create project** → **Deploy from GitHub repo** → pick this repo.
2. Railway will detect the Procfile and offer to create two services
   (`web` and `worker`). Accept both.
3. **Environment variables** — on the **web** service, set:

   | Var | Value |
   |---|---|
   | `SESSION_SECRET` | `python -c "import secrets; print(secrets.token_hex(32))"` |
   | `DATABASE_URL` | from Turso step |
   | `TURSO_AUTH_TOKEN` | from Turso step |
   | `DISCORD_CLIENT_ID` | from Discord step |
   | `DISCORD_CLIENT_SECRET` | from Discord step |
   | `DISCORD_REDIRECT_URI` | `https://YOUR-DOMAIN/auth/callback` |
   | `DISCORD_GUILD_ID` | NYbN server ID |
   | `STAFF_ROLE_IDS` | comma-joined staff role IDs |
   | `CHRONICLE_CHANNEL_ID` | channel ID for closing reminders (optional) |
   | `BOT_SERVICE_TOKEN` | `python -c "import secrets; print(secrets.token_urlsafe(32))"` |

4. **Worker service** — set the same `DATABASE_URL`, `TURSO_AUTH_TOKEN`,
   `DISCORD_GUILD_ID`, `STAFF_ROLE_IDS`, `CHRONICLE_CHANNEL_ID`,
   `BOT_SERVICE_TOKEN`, plus:

   | Var | Value |
   |---|---|
   | `DISCORD_BOT_TOKEN` | from Discord step |
   | `WEB_URL` | `https://YOUR-DOMAIN` (the web service's public URL) |

5. **Do not set** `ENOCH_DEV_PREVIEW=1` in production — it disables every
   auth guard.

6. Railway will deploy. Tail logs on the web service — you should see:
   - `Running database migrations…`
   - `Applying migration: 001_initial.sql` through `017_staff_roles.sql`
     (every numbered file in `migrations/` runs once, in order)
   - `Migrations complete.`
   - `Uvicorn running on http://0.0.0.0:PORT`

   Migrations 014–017 add: chronicle map system, period schedule templates,
   ruleset selector + per-tier budgets, and granular staff roles.

7. On the worker logs, look for the cogs loading + the slash-command sync
   line. The first sync can take up to a minute.

8. **Mount a persistent volume at `/app/web/static/uploads/`** before the
   first character image upload. Without it, uploaded profile images are
   wiped on every container restart. Railway: Service → Volumes → Add → set
   the mount path. Same applies to any platform that uses ephemeral
   container storage.

---

## Step 4 — One-time staff role assignment

After your first staff login (anyone with a `STAFF_ROLE_IDS` Discord role
can sign in to the dashboard, but every mutation is gated by Enoch's
internal role permissions):

1. Sign in as your Admin account → `/staff/admin#players`
2. Find your row, set role to **Admin**. Save.
3. Sign out and back in — your session now carries `admin` and unlocks
   every action. Assign other staff to Moderator / Storyteller / Helper from
   the same Players tab. (Roles were renamed 2026-06 from Lead ST / Co-ST /
   Reviewer / Helper.)

> **First settings-admin bootstrap:** chronicle-wide settings (XP rules,
> ruleset selector) sit behind a separate `settings_admin` flag, not just the
> Admin role. On a brand-new database, set
> `ENOCH_SETTINGS_ADMIN_IDS=<your-discord-id>` on the web service so your
> account can flip settings and grant the flag to others from `/staff/admin`.
> (Existing chronicles received it via migration 024's backfill.)

Without this step, even staff members who pass the Discord-role gate will
get 403 on approvals, settings changes, and most mutations.

---

## Step 5 — Post-deploy verification

1. **OAuth**: hit `https://YOUR-DOMAIN/` → Sign In with Discord → consent →
   should redirect to `/characters` with your username in the sidebar.
2. **Staff access**: if your Discord user has one of the `STAFF_ROLE_IDS`,
   the Staff section appears in the sidebar.
3. **Bot DMs**: create a test character + claim, approve from staff —
   you should get a DM from the bot.
4. **Chronicle reminder**: create a period that closes within 24h, mark it
   active. Within an hour (or on the next dashboard load) the
   `period_closing_soon` event fires. If `CHRONICLE_CHANNEL_ID` is set,
   check the channel.
5. **Export**: `/staff/admin` → **Download** → should serve a JSON file
   named `enoch-export-YYYY-MM-DD.json`. Save one before any risky
   operation.

---

## Operational notes

- **Backups**: Turso has built-in PITR but it's wise to hit
  `/staff/admin/export.json` weekly and stash the JSON somewhere offsite.
- **Schema changes**: add a new `migrations/NNN_short_name.sql` file. It
  applies on next web boot. Already-applied migrations are tracked in the
  `migrations` meta table so the same file is never run twice.
- **Bot redeploy doesn't disrupt the web**, and vice versa — they share
  data only through the DB and the outbox table.
- **`ENOCH_DEV_PREVIEW=1`** is the kill switch for local testing — never
  set it on Railway. The `/_dev/login` route bypasses Discord OAuth entirely.

---

## Rollback

Railway → service → **Deployments** tab → click an older successful
deploy → **Redeploy**. The Turso database is unaffected by app rollbacks;
data loss requires a Turso restore (`turso db shell enoch-prod` → check
PITR docs).
