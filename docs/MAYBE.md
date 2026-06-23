# Enoch — Icebox & Roadmap

*The "maybe later" chart. If a feature idea isn't being built right now, it lives here — not in your head.*

---

## Grounding — you already did the hard part

The thing you set out to build — **let staff handle XP spends and awards instead of doing the math by hand** — is done. Players submit, the app does the math, staff approve/reject from a queue (or grant/remove/refund manually), and every staff action is permission-gated. That was the whole point.

**Everything below is optional.** Read this list as "nice extras," not "a backlog I'm behind on."

---

## The graduation rule

An idea leaves this file and becomes real work only when it clears one bar:

- **Player- or staff-facing feature** → build it when it (a) cuts staff manual effort *or* removes a real player pain you've actually hit, and (b) you've decided **web vs Discord**. Default to **web** — the web app absorbs anything bulk, admin-only, occasional, or form-heavy. Discord is only for fast, in-the-moment actions (check XP, roll, submit a spend). This is your release valve against command bloat.
- **Internal / tech-debt item** → build it only when it's actively biting (a real bug, or the same number living in four places and drifting). Not before.

If an idea can't clear the bar yet, write down *what would make it clear* (its trigger) and leave it parked.

---

## Icebox — parked candidates

| Idea | Where | Trigger — when it's worth it | Effort | Cuts staff effort? |
|---|---|---|---|---|
| **Bulk Excel/.xlsx import** | Web · Admin → Import tab | *Paste-based bulk XP shipped at `/staff/xp/bulk` (2026-06-02).* Still worth it for: launch-day roster migration from a real sheet, or importing full character data (not just XP) | M | ✅ |
| **Merit/Flaw/Background catalog + autocomplete** | Web · chargen | Free-text typos start reaching staff and you're cleaning them up | M | ✅ fewer bad submissions to fix |
| **reject → private re-draft** (keep editing instead of hard reject) | Web · review loop | A rejection forces a player to rebuild from scratch and it stings | S | ◐ smoother player + staff loop |
| **Staff-role Discord sync** | Bot + Web | *Only if* NYbN starts maintaining tiered staff roles in Discord — conflicts with the current "roles are manual" design, so likely stays parked | M | ◐ saves manual staff adds |
| **Loresheet cost label** | Web · spend form | If loresheets ever need their own spend line vs. being treated as Backgrounds | S | ✗ clarity only |

*Effort is rough: **S** = an afternoon, **M** = a focused day or two.*
*A couple of smaller internal chargen cleanups exist too (step-machine refactor, a JS↔Python preview contract test) — pull them in only if chargen starts fighting you.*

**Resolved 2026-06-23 — the internal DRY/tech-debt trio is cleared:**
- **Staff-role label DRY** → one source `db.STAFF_ROLE_LABELS` (main.py's `_ctx` + the Admin role picker import it; the bot keeps a documented static mirror since slash-command Choices must be import-time). Also fixed stale Admin help text.
- **Chargen table DRY** → the tier numbers were already single-sourced (`db._TIER_DEFAULTS` → `tier_budget()` feeds the wizard *and* the staff admin); fixed only a stale dead `_budgetsSeed` JS fallback.
- **Numeric-field hardening** → not actually an issue: the routes use `form_int()` / try-except throughout, so a malformed POST falls back to a default instead of 500-ing.

---

## Already in flight — don't duplicate here

- **Downtime system, Phases B–E** → fully specced in [`NYBN_DOWNTIME_PROJECTS.md`](./NYBN_DOWNTIME_PROJECTS.md). Phases B, C, **and D (coterie projects)** are in; **E** (other downtime actions, resonances) is the next chunk — its first action, generic **Hunting** (spend a timeskip roll), shipped 2026-06-23. The **Homebrew project engine** (`project_mode`) also shipped 2026-06-23 (migration 050); **RAW** remains the only parked engine. That doc is their home — refine there, not here.
- **Inconnu port ("Irad")** → [`INCONNU_PORT_SURVEY.md`](./INCONNU_PORT_SURVEY.md). Lifting Inconnu's MIT V5 logic into Enoch; **B1 bulk-XP** is the first target and merges with the bulk-import row above.

## Required for launch — NOT optional

- **Production env wiring** — Discord OAuth credentials, bot token, Turso provisioning. See `.env.example`. This isn't a "maybe"; it's the gate between local and live.

---

## Decided NO — stop re-litigating these

Already weighed and declined. Listed so they stop costing you mental cycles:

- **Inconnu two-way *sync*** — still a no; Enoch is the source of truth. *(Mining Inconnu's MIT source is a separate, active **yes** — see [`INCONNU_PORT_SURVEY.md`](./INCONNU_PORT_SURVEY.md). We lift its V5 logic into Enoch; we don't keep two systems in lockstep.)*
- **Wiki / Notion sync** — huge (two 1000+ line subsystems), hardcoded to another chronicle's taxonomy.
- **Discord activity tracking** — needs a privileged intent and isn't even tied to XP.
- **Cubby-channel system** — fights your outbox notification model.
- **Loresheets as first-class data** — deliberately handled as Backgrounds.

---

*How to use this file: when something nags you as "needed," add a row to the Icebox with its trigger — then don't build it that same day. Revisit when you're between things. Most rows will still be parked, and that's the file doing its job.*
