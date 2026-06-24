"""Dice-roll history + stats (migration 053): db helpers, web logging on roll,
the History-tab panel, and the bot-facing API."""


def test_log_list_and_stats(_client):
    from web.db import (get_db, list_character_rolls, log_roll,
                        roll_outcome_stats)
    with get_db() as conn:
        conn.execute("DELETE FROM roll_log WHERE character_id=1")
        log_roll(conn, 1, pool=5, hunger=1, difficulty=2, successes=3,
                 outcome="success", label="5d")
        log_roll(conn, 1, pool=7, difficulty=3, successes=1, outcome="failure",
                 label="7d")
        log_roll(conn, 1, kind="reroll", pool=7, successes=4, outcome="critical",
                 label="7d")
        conn.commit()
        rolls = list_character_rolls(conn, 1, limit=10)
        stats = roll_outcome_stats(conn, 1)
    assert len(rolls) == 3
    assert rolls[0]["outcome"] == "critical"        # newest first
    assert stats["total"] == 3
    assert stats["counts"]["success"] == 1 and stats["counts"]["critical"] == 1
    assert round(stats["win_rate"], 2) == round(2 / 3, 2)   # success + critical


def test_web_roll_is_logged(player):
    from web.db import get_db
    with get_db() as conn:
        conn.execute("DELETE FROM roll_log WHERE character_id=1")
        conn.commit()
    player.post("/characters/1/roll",
                data={"_csrf": "dev-csrf-token", "pool": "5", "difficulty": "0"})
    with get_db() as conn:
        n = conn.execute(
            "SELECT COUNT(*) AS n FROM roll_log WHERE character_id=1 AND source='web'"
        ).fetchone()["n"]
    assert n == 1


def test_history_tab_shows_rolls(player):
    from web.db import get_db, log_roll
    with get_db() as conn:
        conn.execute("DELETE FROM roll_log WHERE character_id=1")
        log_roll(conn, 1, pool=6, successes=2, outcome="success", label="6d test pool")
        conn.commit()
    r = player.get("/characters/1")
    assert r.status_code == 200
    assert "Dice Rolls" in r.text and "6d test pool" in r.text


def test_odds_logged_and_marked(player):
    from web.db import get_db
    with get_db() as conn:
        conn.execute("DELETE FROM roll_log WHERE character_id=1")
        conn.commit()
    player.post("/characters/1/roll/odds",
                data={"_csrf": "dev-csrf-token", "pool": "6", "difficulty": "3"})
    with get_db() as conn:
        kinds = [r["kind"] for r in conn.execute(
            "SELECT kind FROM roll_log WHERE character_id=1").fetchall()]
    assert "odds" in kinds
    r = player.get("/characters/1")
    assert "· odds" in r.text and "Odds preview" in r.text   # shown even with no real rolls


def test_bot_roll_api_round_trip(_client):
    from web.db import get_db
    headers = {"Authorization": "Bearer smoke-test-token"}
    with get_db() as conn:
        conn.execute("DELETE FROM roll_log WHERE character_id=1")
        conn.commit()
    r = _client.post("/api/characters/1/rolls",
                     json={"pool": 8, "successes": 5, "outcome": "critical", "label": "8d"},
                     headers=headers)
    assert r.status_code == 200 and r.json()["ok"] is True
    r2 = _client.get("/api/characters/1/rolls?limit=5", headers=headers)
    assert r2.status_code == 200
    rolls = r2.json()["rolls"]
    assert rolls and rolls[0]["outcome"] == "critical" and rolls[0]["source"] == "bot"
