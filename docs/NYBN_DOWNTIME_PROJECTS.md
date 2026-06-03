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
  builder** so staff set correct DCs. ⚠️ **OPEN architecture decision** — how
  strictly to tie a 3+-dot advancement to the XP spend system:
  (a) **staff-coordinated** (today): staff just require/track a project and run
  the XP spend separately — no code gating, max ST discretion;
  (b) **gate the spend**: a 3+-dot `spend_request` can't be approved until a
  linked project completes (XP still paid);
  (c) **project replaces XP**: the project grants the trait, no XP.
- **D — Coterie projects**: coterie-owned, elevated DCs (30/45/60), multi-stage,
  coterie-mates combine successes; retainers teamwork-only; benefit lost if the
  coterie disbands (reclaimable by a member's regular project).
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
