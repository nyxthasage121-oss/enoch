# Inconnu → Enoch Port Survey ("Irad")

Reference clone: `C:\Users\caram\Projects\inconnu` (shallow, **MIT**, `tiltowait/inconnu` v2026.04.1).
Decision: **lift Inconnu's DB-agnostic V5 logic into Enoch** — not a separate bot. "Irad" = the bot half of Enoch (Enoch = web/chronicle app, Irad = the Discord bot).

## Stack reality — what ports cleanly

| | Inconnu | Enoch | Consequence |
|---|---|---|---|
| Python | 3.12 | 3.12 | ✅ same |
| Discord lib | py-cord 2.7 | discord.py | *Logic* ports; *command/modal wiring* must be re-authored |
| Database | MongoDB (beanie) | SQL (libsql/Turso) | *Logic* ports; *persistence rewrites* (Mongo aggregation → SQL) |
| License | MIT | yours | Copy-with-credit is legit |

- **Credit TODO:** add a line in Enoch's README crediting `tiltowait/inconnu`, and keep Inconnu's MIT notice on any file lifted substantially.
- Enoch already has its **own** V5 roll engine (`bot/roll.py`) — port roll-derived features onto *that*; don't import Inconnu's `Roll`.

## Bucket A — already at parity (do NOT port)

Enoch already covers Inconnu's core:
- Dice + trait-pool rolling, specialties, Blood Surge, WP-reroll → `/roll`, `bot/roll.py`
- Rouse / Wake / Mend / Frenzy / Remorse / Blush / Slake → Enoch's misc verbs
- Macros → `/macro`; sheets, traits, specialties, XP award/deduct/list, Blood Potency → web + `/character`
- Settings, character creation, image upload → Enoch does these on the **web** by design
- Roll display / dicemoji → treat as parity (optional cosmetic polish only)

## Bucket B — worth lifting (real gaps · DB-agnostic · clean)

| # | Feature | Inconnu source | Lift type | Value | Effort | Verdict |
|---|---|---|---|---|---|---|
| **B1** | **Bulk XP award** — paste `N xp @user Character` lines → validate-all-or-nothing → preview → atomic commit | `src/inconnu/experience/bulk.py` | Logic + UX pattern (rebuild on web) | **Cuts staff effort** | S–M | **PORT FIRST.** Fills the icebox "bulk XP" item. Put it on the **web** (paste box → preview → commit) reusing `adjust_xp_manual` + a transaction — the "improved" take on Inconnu's 4000-char Discord modal. Merges with the parked Excel-import idea. |
| **B2** | **Roll probabilities** — `/probability pool hunger dc` → % per outcome, avg successes/margin, reroll strategies | `src/inconnu/reference/probabilities.py` | Pure logic over your roll engine | Player QoL | S | **PORT.** Re-simulate over `bot/roll.py`; skip the Mongo cache (10k sims is sub-ms). *Improve:* compute exact odds (binomial) instead of simulating. |
| **B3** | **Resonance + temperament + dyscrasia** — `/resonance` random generator with disciplines, emotions, Acute dyscrasias | `src/inconnu/reference/resonance.py` (+ bundled `dyscrasias.db`) | Pure logic + portable SQLite data | Player QoL; feeds **Phase E** resonance downtime | S | **PORT.** Tables are inline; dyscrasia data is a liftable SQLite file (already not Mongo). Connects to `/hunt`. |
| **B4** | **Roll statistics** — per-character outcome/trait tallies over time | `src/inconnu/reference/statistics.py` | Needs a prerequisite | Player QoL | M–L | **LATER.** Requires logging every roll to a new `roll_log` table first, then SQL aggregations (rewrite the Mongo `$group` pipelines). Lower priority. |
| **B5** | **Convictions / Touchstones** *(verify first)* | `src/inconnu/character/convictions.py` | Sheet addition | Player completeness | S | **VERIFY** Enoch doesn't already track these (check `web/v5_traits.py` / `sheet_json`; `/remorse` may imply them). If genuinely missing, small add. |

## Bucket C — distinct net-new subsystem (a product question, not a port)

- **Roleposting** (Tupperbox-style) — post IC *as your character* via webhooks, with auto-generated headers (blush/hunger/merits), bookmarks, tags, search, and edit-with-changelog/diff. Source: `src/inconnu/roleplay/*` + `src/inconnu/header/*`. **The single biggest thing Inconnu has that Enoch doesn't** — but it's a whole webhook subsystem (M–L), and it's pure player RP immersion that **doesn't cut staff effort**. Graduation test: a *want*, not a *need*. Park as a flagged epic — *"Do you want Irad to also be an RP-posting bot?"* If yes someday, it's Irad's signature feature.

## Bucket D — skip

- Mongo persistence / data models (rewrite for SQL, don't port)
- py-cord `interface/` cogs (adapt the patterns, don't copy the code)
- Discord-side trait/character editing (Enoch does this on the web by design)
- `coinflip`, `percentile` (trivial; add only if wanted)

## Recommended order

1. **B1 Bulk XP** (web) — the only staff-effort item; merges with the parked Excel/bulk-import idea.
2. **B3 Resonance** — cheap, self-contained, feeds Phase E.
3. **B2 Probabilities** — cheap player QoL.
4. Then decide **C Roleposting** (product question) and **B4 Statistics** (needs roll-logging first).

Each lands as a normal Enoch feature (migration + tests), keeps the 361-test suite green, and earns its keep via the graduation test in [`MAYBE.md`](./MAYBE.md).
