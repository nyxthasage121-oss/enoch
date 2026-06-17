"""Chargen autosave (save-as-you-go): POST autosave=1 saves a draft in the
background and returns the draft_id as JSON (no redirect); a follow-up autosave
carrying that draft_id updates the same row instead of spawning duplicates, so
in-progress work and corrections persist as the player moves through the wizard.
"""


def test_autosave_creates_draft_returns_json_and_reuses_id(player):
    r = player.post("/characters/new", data={
        "_csrf": "dev-csrf-token", "autosave": "1", "as_draft": "1",
        "name": "Autosave WIP", "clan": "brujah",
    }, follow_redirects=False)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    did = body["draft_id"]
    assert isinstance(did, int) and did > 0

    from web.db import get_db, get_character
    with get_db() as conn:
        ch = get_character(conn, did)
        assert ch["is_draft"] == 1 and ch["name"] == "Autosave WIP"

    # A second autosave carrying the draft_id must UPDATE the same row.
    r2 = player.post("/characters/new", data={
        "_csrf": "dev-csrf-token", "autosave": "1", "as_draft": "1",
        "draft_id": str(did), "name": "Autosave WIP v2", "clan": "brujah",
    }, follow_redirects=False)
    assert r2.status_code == 200 and r2.json()["draft_id"] == did

    with get_db() as conn:
        assert get_character(conn, did)["name"] == "Autosave WIP v2"
        n = conn.execute(
            "SELECT COUNT(*) c FROM characters WHERE name LIKE 'Autosave WIP%'"
        ).fetchone()["c"]
        assert n == 1, "autosave must update the draft, not create duplicates"
        conn.execute("DELETE FROM characters WHERE id=?", (did,))
