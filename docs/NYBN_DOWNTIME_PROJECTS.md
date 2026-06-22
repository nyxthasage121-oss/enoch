# NYbN Downtime & Projects — build spec

Working spec for adapting Enoch's Projects feature to the full NYbN downtime
ruleset. **Review the ⚠️ OPEN items** — they change how I build. Everything else
is my current understanding; correct anything that's off.

---

## Where we are (built)

- **Projects MVP**: propose → staff approve (staged|roll, freeform|structured payoff,
  per project) → staff complete (applies payoff). Own player tab.
- **Timeskip roll budget**: each character gets N project rolls per timeskip
  (`rolls_per_timeskip`, default 8), shared across their projects. Tracked per
  character per active period; enforced on `/project roll`.

This spec is the plan to grow that MVP into the NYbN system, in phases. **Phase A
is next** (pending the open questions below).

---

## Confirmed decisions (2026-06-02)

- **Outcome handling**: the engine computes the *math* (success tally, overflow,
  DC bumps, stage advancement, the bestial trigger). **Staff apply the narrative**
  flaws / penalties / temp-background-dots, because those are "appropriate" /
  ST-judgment in the rules. Keeps staff in approver mode.
- **Stage DCs**: **presets + custom override**. Presets:
  - Regular: Simple **15** · Average **30** · Hard **45+**
  - Coterie: Simple **30** · Average **45** · Hard **60**
  - Advancement (buying a trait at 3+ dots): 3rd → **30**, 4th → **40**, 5th → **50**,
    plus type modifier: Attribute **+5**, Skill **+0**, Merit/Background **+5**,
    Loresheet **+10**. Meaningful RP can reduce a DC (ST-applied).
- **Pool**: always **Attribute + Skill** (specialties/merits sometimes — Phase B).

---

## Phase A — multi-stage projects + the DC roll engine

**Data:** a project becomes an ordered list of **stages**, each
`{label, dc, progress, done}`. (A single-stage project is just one stage.)

**`/project roll`:** resolve the project's Attribute+Skill pool vs the **current
stage's DC**, consuming 1 from the timeskip budget. Per roll:

| Outcome | Effect |
|---|---|
| **Win** (successes ≥ DC for the stage's remaining need) | successes bank toward the current stage; reaching the DC completes it and advances to the next |
| **Crit** | successes *over* the DC **carry into the next stage**; on the **final** stage → ⚠️ temporary background dots (flag staff) |
| **Messy crit** | *half* the overflow carries; flag staff to apply a penalty/flaw |
| **Bestial failure** | the current stage's DC **rises by ½ the dice pool**; flag staff for a penalty |

**Bestial-on-project trigger:** a **1 on a Hunger die** AND successes
**< ⌈stage DC ÷ 10⌉**. On bestial: the roll's successes **still bank**, then that
stage's DC rises by ½ the dice pool and a penalty is flagged for staff.

**Staff:** the approve form gains a **stage builder** (add stages, each a preset or
custom DC); completion/payoff works as today. An at-a-glance view shows
stage N of M + progress toward the current DC.

### Resolved (2026-06-02)
1. **Bestial trigger** — confirmed: Hunger-die-1 AND successes < ⌈DC/10⌉ (and the
   standard V5 bestial too — "assume both, adjust later").
2. **Crit overflow** — only a **Crit (full)** or **Messy crit (half)** carries
   leftover successes into the next stage; a plain success that clears the DC
   **loses** the overflow. Spillover is null on the final/only stage; a
   final-stage crit/messy flags staff for temp background dots.
3. **Stage order** — **sequential** (in order) by default.
4. **Accumulation** — **cumulative**: successes bank across rolls until the stage
   DC is reached (extended test).

---

## Roll modifiers — blood surge & Willpower reroll

- **Blood surge**: optional per the normal V5 rules — the player *may choose* to
  surge → **+1 Hunger**, surge bonus dice apply to the roll.
- **WP reroll**: spend **2 Willpower** to reroll a project roll; those 2 WP are
  **locked until Midnight** (not refunded at first sunset).

**Resolved:** both go in **Phase A**'s `/project roll` (reusing the bot's existing
`rouse_check` / `blood_surge_bonus` / `reroll_failures`). Surge is built fully; the
WP-reroll deducts 2 WP now, and the precise "locked-until-Midnight" accounting is
finished in **Phase E** alongside Midnight recovery.

---

## Midnight recovery (NYbN — broader timeskip rule, for context)

> "Midnight has come! Everyone can recover Willpower equal to your Resolve or
> Composure if not in a conflict scene. Everyone can recover 1 Aggravated damage
> for an increase of 2 Hunger if not in a conflict scene."

A periodic **recovery event**, not a project mechanic → **Phase E** (downtime
actions / timeskip rules). Captured here so the WP-lock-until-Midnight rule has a
home.

---

## Later phases (reference — not yet specced in detail)

- **B — Background integration** (ties to the Background Blanking feature).
  **MVP BUILT 2026-06-02:** `/project roll` takes `background:` (spend a tracked
  background for bonus dice = its available dots, then it's blanked for the rest
  of the timeskip via the blanking engine), `teamwork:` (+dice), `adversary:`
  (−dice). *Still ST-judgment / not mechanized:* Enemies "countering" a project,
  using a Background to cut a DC (and blanking it till done), per-type teamwork
  limits, and own/retainer/coterie backgrounds being interchangeable — staff
  handle these via notes + the penalty flags.
- **C — Advancement via projects**: buying Attribute/Skill/Merit/Background/
  Loresheet at **3+ dots requires a project** (DC table); learning ceremonies/
  rituals/formulae (4×level TS-weeks **or** Int/Resolve + Occult/Science at DC
  5×level; library dots add). **PARTIAL 2026-06-02:** advancement projects already
  work via the existing roll-project + structured-payoff (grant the trait on
  completion); the **DC table is now shown as a reference in the staff stage
  builder** so staff set correct DCs. **RESOLVED 2026-06-02 → staff-coordinated**
  (no code gating): staff require/track a project and run the normal XP spend
  separately, per ST discretion. Phase C is considered DONE — no spend↔project
  code linkage. (If they ever want it enforced in code, the options were: gate a
  3+-dot spend on a linked project completing, or have the project replace XP.)
- **D — Coterie projects ✅ DONE (2026-06-18, migration 041)**: coterie-owned,
  elevated DCs (30/45/60), multi-stage, coterie-mates combine successes;
  retainers teamwork-only; benefit lost if the coterie disbands (reclaimable by
  a member's regular project).
  - **Confirmed design (2026-06-18):**
    - **Roll budget** → each member's roll on a coterie project spends from **their
      own** per-character timeskip budget (the existing `rolls_per_timeskip` pool,
      shared with their solo projects). No separate coterie budget.
    - **Who acts** → **any** coterie member may propose it (staff still approve) and
      **any** member may roll on it; successes accumulate cumulatively (existing
      stage logic just keeps banking across members).
  - **Build outline:** add nullable `coterie_id` to `projects` (a coterie project
    sets `coterie_id`; `character_id` becomes the proposer / nullable for the owner).
    Propose flow offers "coterie project" when the char is in a coterie. Roll flow
    lets any member roll — **lift the one-roll-per-period-per-project block**
    (`last_roll_period_id`) for coterie projects since multiple members roll the
    same period; still decrement the rolling member's per-character budget. Staff
    stage builder uses the coterie DC preset (30/45/60). Retainers = teamwork dice
    only (existing `teamwork:`). On coterie disband, forfeit the coterie's projects
    (reclaim = a member's new regular project, staff-coordinated — no auto-transfer).
  - **What landed (2026-06-18):** migration 041 adds nullable `projects.coterie_id`
    (set => coterie project; `character_id` stays the proposer). `create_project`
    takes `coterie_id`; `list_projects_for_character` now excludes coterie rows; new
    `list_projects_for_coterie`; the staff list queries carry `coterie_name`. The two
    roll fns (`record_project_roll` / `resolve_project_roll`) take `actor_character_id`
    and charge THAT member's per-character budget (any member rolls from their own
    pool; successes bank cumulatively); coterie rolls are attributed by member name in
    the log. Bot API `GET /api/characters/{id}/projects` merges the character's coterie
    projects; `POST /api/projects/{id}/roll` resolves the acting member from
    `requester_discord_id` + coterie membership (non-member → 403). Web: a member-gated
    `POST /coteries/{id}/projects/propose`, a Coterie Projects panel on the coterie
    detail page (list + propose form, gated on an active coterie), and a hint on the
    character projects card. Staff queue tags coterie projects + uses the 30/45/60
    preset in the stage builder. Tests `tests/test_coterie_projects.py` (9). Full
    suite 463 green, ruff clean.
    **Notes/decisions:** the stage engine was already a shared-per-character budget
    (migration 035), so the old "lift the one-roll-per-period block" note was moot.
    Coterie projects live on the **coterie detail page** (consistent with the other
    coterie actions), not the character card. No disband flow exists yet, so "forfeit
    on disband" is satisfied passively — `get_coterie_for_character` is active-only, so
    a non-active coterie's projects stop surfacing to the bot. Structured payoff still
    grants to the proposer's personal sheet; the staff form warns to use free-form +
    the coterie's own tools for coterie-wide rewards. *Known edge:* `character_id` is
    still `NOT NULL` with `ON DELETE CASCADE`, so hard-deleting the proposer's
    character cascades the coterie project (rare — characters retire, not delete).
- **E — Other downtime actions on the roll budget**: Hunting (1 roll; Resonance
  Negligible, or 2 rolls + Bloodhound for chosen Resonance); Willpower recovery
  (1 roll, scene with a Touchstone); cultivating Resonances (Manipulation + Insight/
  Subterfuge/Persuasion/Intimidation, DC 2, extended threshold 6 — fleeting→intense
  = 1 stain/roll, intense→acute = 2 stains/roll, mortals no stains); blood-surge/
  WP/hunger TS constraints; Midnight recovery; fleeting Resonance for training.

---

## Player-facing project template (theirs, for reference)

```
## PROJECT: NAME
**Character:**
**Phases:**
**Dice Pool:**
**DC:**
**Progress:**
**Synopsis**
```

---

## Project modes — chronicle-wide ruleset toggle (locked 2026-06-22)

Owner wants the project rules selectable so the NYbN house rules can be stripped.
Decision: a **chronicle-wide** setting `project_mode` (Admin → Settings) with four
modes. Key insight from reviewing the V5 Appendix II "Projects" rules: **RAW and
NYbN are different ENGINES, not different numbers** — so this is engine selection,
not a values swap.

- **NYbN** (current, default) — multi-stage extended test, staff-set stage DCs
  (15/30/45 · coterie 30/45/60), crit/messy overflow, **bestial auto-bumps the
  stage DC**, shared per-character timeskip roll budget. Unchanged.
- **RAW** — V5 Appendix II: **Scope** (dots the goal adds) · **Launch roll**
  Skill+Background vs Difficulty Scope+2 (no WP/surge; crit = no stake, win =
  stake Scope+1−margin dots, min 1, dots tied up) · **Project Die** starts 10,
  −1 per increment (time) · **Goal roll** = conflict vs a pool equal to the
  current die value (plotter gets NO crits, opposition crits DO count), win
  knocks the die down by margin, die <1 = success, lose = lose Background dots
  from the stake, stake 0 = collapse. **PARKED** — owner chose "hold off." When
  built, do it **engine-math + staff-apply** (engine tracks Scope/die/margins and
  flags "stake N"/"lose N"; staff apply to the sheet — same as NYbN penalties),
  NOT auto-locking real sheet dots.
- **Homebrew** (designed, TO BUILD next) — owner's hybrid:
  - **Launch roll = optional per chronicle** (`homebrew_launch_roll` on/off; some
    servers run it, some don't). When on: a roll to *open* the project (win →
    opens + the test begins; fail → retry next timeskip). When off: starts directly.
  - **Goal + DC = staff-set, fully customizable** (NO baked-in formula — "leave it
    free to whoever sets it"). Two goal flavors, both with a staff-set DC:
    **dot-based** ("gain N dots in <trait>", with the dot-count shown as a hint),
    or **free-text** (a non-dot narrative goal — staff write it + set the DC).
  - **Extended test = cumulative** successes toward the goal DC, per timeskip.
  - **Messy crit / bestial failure = PAUSE + flag the ST** (NOT auto-bump like
    NYbN): the project pauses (no more rolls), gets a "Needs ST" flag in the staff
    queue (can also surface on the Alerts page), the ST advises the player
    out-of-band, then clears the pause to resume. Normal successes bank; a plain
    failure just makes no progress.
- **Off** ("No Projects") — hide the whole feature: the player Projects tab, the
  coterie Projects panel, the staff Projects queue + nav, and block propose/roll.

**Build order:** (1) foundation = the `project_mode` setting + Admin toggle + Off
hiding + NYbN as the default working mode (RAW/Homebrew shown but not yet
selectable/working); (2) the **Homebrew engine** (launch/goal-DC/cumulative/pause);
(3) RAW later. Toggle is chronicle-wide (not per-project) per owner.
