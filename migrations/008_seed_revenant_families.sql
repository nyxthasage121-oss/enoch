-- 008_seed_revenant_families.sql
-- Seed the canonical NYbN Revenant family list into chronicle_settings.
-- Only applies when the families column is still its default empty array,
-- so chronicles that already curated their own list don't get overwritten.

-- Insert the singleton settings row if it doesn't exist yet (idempotent).
INSERT OR IGNORE INTO chronicle_settings (id, server_start_date)
VALUES (1, strftime('%Y-%m-%d', 'now'));

-- Seed the family list. Each entry carries the parent clan, the three
-- family Disciplines (V5 canon), and the Bane / Compulsion text drawn
-- from the user's reference. Stored as one JSON array.
UPDATE chronicle_settings
SET revenant_families = json('[
  {
    "name": "Zantosa",
    "parent_clan": "Tzimisce",
    "disciplines": ["Auspex", "Presence", "Protean"],
    "bane": "Zantosa exemplify the old saying that art in the blood takes strange forms. They desire beauty so intensely that they suffer in its absence. While the character finds itself in less than beautiful surroundings, lose dice equal to Bane Severity from any Discipline dice pool.",
    "compulsion": "Obsession — enraptured by beauty, the Zantosa becomes temporarily obsessed with a singular gorgeous thing. Any actions not focused on it take a two-dice penalty until the scene ends or perception of the beloved object is lost."
  },
  {
    "name": "Bratovich",
    "parent_clan": "Tzimisce",
    "disciplines": ["Animalism", "Potence", "Protean"],
    "bane": "The Blood of the Bratovich simmers with barely contained rage. Subtract dice equal to Bane Severity from any roll to resist fury frenzy (cannot reduce pool below one die).",
    "compulsion": "Rebellion — the Bratovich takes a stand against the status quo. Two-dice penalty to all rolls until they go against orders or expectations, by force if necessary."
  },
  {
    "name": "Kairouan Brotherhood",
    "parent_clan": "Banu Haqim",
    "disciplines": ["Auspex", "Celerity", "Obfuscate"],
    "bane": "Their Blood has recoiled and aborted Blood Bond connections. They cannot be ghouled with a single dose of Vitae, binding requires additional drinks equal to Bane Severity.",
    "compulsion": "Perfectionism — anything less than exceptional performance instills failure. Two-dice penalty on all dice pools until a critical win on a Skill roll, or the scene ends (penalty reduces on repeated actions)."
  },
  {
    "name": "Ducheski",
    "parent_clan": "Tremere",
    "disciplines": ["Auspex", "Dominate", "Blood Sorcery"],
    "bane": "When a Ducheski uses a Discipline power, mortals nearby are spooked. Social interactions with them (apart from intimidation) suffer a dice penalty equal to Bane Severity. Vampires recognize the Ducheski as Supernatural without penalty.",
    "compulsion": "Delusion — extrasensory gifts run wild. Two-dice penalty to Dexterity, Manipulation, Composure, and Wits rolls as well as resisting terror frenzy, for one scene."
  },
  {
    "name": "D''habi",
    "parent_clan": "Nosferatu",
    "disciplines": ["Animalism", "Dominate", "Oblivion"],
    "bane": "Swarms of vermin follow the D''habi. Their havens are always infested, causing a penalty of two plus Bane Severity to concentration-requiring activities (anyone) and social tests at Storyteller discretion. In any enclosed location they occupy, the infestation imposes a Bane Severity penalty.",
    "compulsion": "Cryptophilia — the D''habi becomes consumed with a hunger for secrets. Two-dice penalty to actions not spent learning a secret, until they learn one big enough to matter."
  },
  {
    "name": "Rafastio",
    "parent_clan": "Hecata",
    "disciplines": ["Auspex", "Animalism", "Blood Sorcery"],
    "bane": "If they slumber in the same place more than once in seven nights, roll dice equal to Bane Severity. They take Aggravated damage equal to the number of 10s rolled. Resting places must be at least a mile apart. Rafastio cannot take the No Haven Flaw at character creation.",
    "compulsion": "Tempting Fate — the Rafastio is driven to court danger. Two-dice penalty to any solution that isn''t the most daring or dangerous, until the problem is solved or further attempts become impossible."
  },
  {
    "name": "Grimaldi",
    "parent_clan": "Lasombra",
    "disciplines": ["Celerity", "Dominate", "Fortitude"],
    "bane": "A Grimaldi suffers a Discipline dice pool penalty equal to Bane Severity when using powers on a vampire. They must spend Willpower equal to Bane Severity to directly attack a vampire.",
    "compulsion": "Arrogance — the Revenant stops at nothing to assume command. Two-dice penalty to actions not directly associated with leadership, until an order has been obeyed (not via Dominate)."
  },
  {
    "name": "Obertus",
    "parent_clan": "Tzimisce",
    "disciplines": ["Auspex", "Obfuscate", "Protean"],
    "bane": "All Obertus are cursed with at least one mental derangement. When suffering a Bestial Failure or Compulsion, take a penalty equal to Bane Severity to one category of dice pools (Physical, Social, or Mental) for the entire scene, in addition to any Compulsion penalties.",
    "compulsion": "Delusion — extrasensory gifts run wild. Two-dice penalty to Dexterity, Manipulation, Composure, and Wits rolls as well as resisting terror frenzy, for one scene."
  },
  {
    "name": "Marijava",
    "parent_clan": "Toreador",
    "disciplines": ["Celerity", "Presence", "Animalism"],
    "bane": "Marijava tend toward the inhuman. When making a Remorse roll, deduct dice equal to Bane Severity (cannot reduce pool below one die).",
    "compulsion": "Ruthlessness — the Marijava feels setbacks profoundly and escalates to ruthless methods. Next time they fail any action, take a two-dice penalty to all rolls until they succeed at the same action, or the scene ends. Future attempts at the triggering action are also affected."
  }
]')
WHERE id = 1
  AND (revenant_families IS NULL
       OR TRIM(revenant_families) = ''
       OR revenant_families = '[]');
