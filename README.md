# Enoch

XP & downtime tracker for **New York by Night**, a *Vampire: The Masquerade* (V5)
chronicle. A FastAPI web app and a Discord bot share one database — staff
approve, the system does the bookkeeping.

## Highlights

- **Characters** — a guided V5 creation wizard (clans, predator types,
  disciplines, merits/flaws, Blood Potency, Humanity) with RAW-compliant
  spreads, plus full sheets staff can review.
- **XP, staff-approved** — players submit earn *claims* and *spend* requests;
  staff approve or reject from a queue. Per-trait costs and caps are automated.
- **Coteries** — player-proposed, staff-approved; tracks domain
  (Chasse / Lien / Portillon) and shared funding.
- **Downtime & timeskips** — multi-stage *Projects* (extended tests rolled over
  play periods), *Background Blanking*, hunting sites, and a per-timeskip roll
  budget.
- **Native V5 dice** — `/roll` with Hunger dice, criticals, messy crits, and
  bestial failures; plus rouse, frenzy, remorse, mend, and more.
- **Discord bot** — `/character`, `/xp`, `/coterie`, `/roll`, `/hunt`,
  `/condition`, `/bond`, `/project`, `/blank`, `/timeskip`.

## Stack

FastAPI · Jinja2 + HTMX + Alpine.js + Tailwind (the "Zillah Codex" design
system) · discord.py · libsql/Turso (SQLite locally). Migrations in
`migrations/` auto-apply on startup.

## Running it

```bash
pip install -r requirements.txt     # add requirements-dev.txt for tests
uvicorn web.main:app --reload       # web app
python -m bot.main                  # Discord bot (worker)
pytest                              # tests
```

Configuration is via environment variables — see [.env.example](.env.example).
Deployment (Railway) is covered in [DEPLOY.md](DEPLOY.md); deeper design notes
live in [docs/](docs/).

## Credits

Enoch is an independent rewrite, but it grew out of — and adapts a number of
patterns from — a friend's **MCbN XP Tracker**
([jkomg/mcbn-xp-tracker](https://github.com/jkomg/mcbn-xp-tracker)), the tracker
for the *Music City by Night* chronicle. Thanks to
[@jkomg](https://github.com/jkomg) for the groundwork; the specific borrowed
patterns are credited inline in the code.

The Discord bot's **sheet-independent character model** — lightweight
one-command creation, open key-value traits, and setting vitals from chat with
no full sheet and no approval gate — is adapted from **Inconnu**
([tiltowait/inconnu](https://github.com/tiltowait/inconnu), MIT-licensed, © 2021
tiltowait). Enoch's implementation is its own (SQL, not Inconnu's MongoDB), but
the design is theirs; the lifted pieces are credited inline in the code. The V5
dice-face emoji art in `bot/assets/dice/` is also Inconnu's, used under the same
licence (see `bot/assets/dice/CREDITS.txt`).
