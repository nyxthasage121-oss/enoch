"""Tests for the spend_requests.depends_on dependency chain (migration 023).

Covers the DB-level enforcement (approve gating, reject cascade) and the
auto-chain detection in the player spend POST route.
"""
import pytest


@pytest.fixture(autouse=True)
def _ensure_migrations(_client):
    """Depend on the session-scoped TestClient so migrations run before
    any DB-direct test code touches the schema."""
    yield


def _seed_approved_character(name: str, xp: int = 50) -> int:
    """Create an approved character with `xp` XP available, and return id."""
    from web.db import get_db, create_character, update_character, adjust_xp_manual
    with get_db() as conn:
        ch = create_character(conn, discord_id="dep-test", name=name, clan="ventrue")
        cid = ch["id"]
        update_character(conn, cid, is_approved=1, status="active",
                         approved_by="0", approved_at="2026-05-29T00:00:00Z")
        adjust_xp_manual(conn, cid, xp, "seed for dependency tests", staff_id="0")
        conn.commit()
    return cid


def _delete_character(cid: int):
    from web.db import get_db
    with get_db() as conn:
        conn.execute("DELETE FROM ledger_entries WHERE character_id=?", (cid,))
        conn.execute("DELETE FROM spend_requests WHERE character_id=?", (cid,))
        conn.execute("DELETE FROM characters WHERE id=?", (cid,))
        conn.commit()


def test_create_spend_persists_depends_on():
    from web.db import get_db, create_spend, get_spend
    cid = _seed_approved_character("Dep Parent")
    try:
        with get_db() as conn:
            parent = create_spend(conn, character_id=cid, category="Attribute",
                                  trait_name="Strength", current_dots=1,
                                  new_dots=2, verified_cost=10)
            child = create_spend(conn, character_id=cid, category="Attribute",
                                 trait_name="Strength", current_dots=2,
                                 new_dots=3, verified_cost=15,
                                 depends_on=parent["id"])
            conn.commit()
            assert child["depends_on"] == parent["id"]
            # Reload to be sure the column round-trips.
            child2 = get_spend(conn, child["id"])
            assert child2["depends_on"] == parent["id"]
    finally:
        _delete_character(cid)


def test_create_spend_rejects_wrong_character_parent():
    from web.db import get_db, create_spend
    cid_a = _seed_approved_character("Dep A")
    cid_b = _seed_approved_character("Dep B")
    try:
        with get_db() as conn:
            parent = create_spend(conn, character_id=cid_a, category="Attribute",
                                  trait_name="Strength", current_dots=1,
                                  new_dots=2, verified_cost=10)
            with pytest.raises(ValueError, match="same character"):
                create_spend(conn, character_id=cid_b, category="Attribute",
                             trait_name="Strength", current_dots=2,
                             new_dots=3, verified_cost=15,
                             depends_on=parent["id"])
    finally:
        _delete_character(cid_a)
        _delete_character(cid_b)


def test_approve_blocks_when_parent_pending():
    from web.db import get_db, create_spend, approve_spend
    cid = _seed_approved_character("Dep Block")
    try:
        with get_db() as conn:
            parent = create_spend(conn, character_id=cid, category="Attribute",
                                  trait_name="Strength", current_dots=1,
                                  new_dots=2, verified_cost=10)
            child = create_spend(conn, character_id=cid, category="Attribute",
                                 trait_name="Strength", current_dots=2,
                                 new_dots=3, verified_cost=15,
                                 depends_on=parent["id"])
            conn.commit()
            with pytest.raises(ValueError, match="still pending"):
                approve_spend(conn, child["id"], reviewer_id="0")
    finally:
        _delete_character(cid)


def test_approve_proceeds_once_parent_approved():
    from web.db import get_db, create_spend, approve_spend, get_spend
    cid = _seed_approved_character("Dep Flow", xp=200)
    try:
        with get_db() as conn:
            parent = create_spend(conn, character_id=cid, category="Attribute",
                                  trait_name="Strength", current_dots=1,
                                  new_dots=2, verified_cost=10)
            child = create_spend(conn, character_id=cid, category="Attribute",
                                 trait_name="Strength", current_dots=2,
                                 new_dots=3, verified_cost=15,
                                 depends_on=parent["id"])
            conn.commit()
            approve_spend(conn, parent["id"], reviewer_id="0")
            conn.commit()
            approve_spend(conn, child["id"], reviewer_id="0")
            conn.commit()
            assert get_spend(conn, child["id"])["status"] == "approved"
    finally:
        _delete_character(cid)


def test_reject_parent_cascades_to_dependents():
    from web.db import get_db, create_spend, reject_spend, get_spend
    cid = _seed_approved_character("Dep Cascade")
    try:
        with get_db() as conn:
            parent = create_spend(conn, character_id=cid, category="Attribute",
                                  trait_name="Strength", current_dots=1,
                                  new_dots=2, verified_cost=10)
            child = create_spend(conn, character_id=cid, category="Attribute",
                                 trait_name="Strength", current_dots=2,
                                 new_dots=3, verified_cost=15,
                                 depends_on=parent["id"])
            grandchild = create_spend(conn, character_id=cid, category="Attribute",
                                      trait_name="Strength", current_dots=3,
                                      new_dots=4, verified_cost=20,
                                      depends_on=child["id"])
            conn.commit()
            reject_spend(conn, parent["id"], reviewer_id="0",
                         reason="bad concept fit")
            conn.commit()
            # All three rejected, with cascade reasons on dependents.
            assert get_spend(conn, parent["id"])["status"] == "rejected"
            assert get_spend(conn, child["id"])["status"] == "rejected"
            assert get_spend(conn, grandchild["id"])["status"] == "rejected"
            assert "bad concept fit" in get_spend(conn, child["id"])["rejection_reason"]
            assert "Parent spend" in get_spend(conn, grandchild["id"])["rejection_reason"]
    finally:
        _delete_character(cid)


def test_player_spend_post_auto_chains_pending_predecessor(player):
    """The /characters/{id}/spend POST should detect a pending spend
    whose new_dots matches the incoming current_dots and chain to it
    automatically. Means the player can rapid-fire two submissions
    without needing depends_on UI."""
    from web.db import get_db, adjust_xp_manual, update_character
    # Use the dev character — make sure it's approved + has XP.
    with get_db() as conn:
        row = conn.execute(
            "SELECT id FROM characters WHERE name='Valeria Morano'"
        ).fetchone()
        cid = row["id"]
        update_character(conn, cid, is_approved=1, status="active")
        adjust_xp_manual(conn, cid, 200, "auto-chain test seed", staff_id="0")
        conn.commit()

    # First submission — no parent, lands as a root spend.
    r1 = player.post(
        f"/characters/{cid}/spend",
        data={"_csrf": "dev-csrf-token", "category": "Attribute",
              "trait_name": "Strength", "current_dots": "1",
              "new_dots": "2", "note": "first dot"},
    )
    assert r1.status_code == 200

    # Second submission — same trait, current_dots=2, should chain.
    r2 = player.post(
        f"/characters/{cid}/spend",
        data={"_csrf": "dev-csrf-token", "category": "Attribute",
              "trait_name": "Strength", "current_dots": "2",
              "new_dots": "3", "note": "second dot"},
    )
    assert r2.status_code == 200

    try:
        with get_db() as conn:
            rows = conn.execute(
                "SELECT id, depends_on, new_dots FROM spend_requests "
                "WHERE character_id=? AND trait_name='Strength' "
                "ORDER BY id DESC LIMIT 2", (cid,),
            ).fetchall()
            assert len(rows) == 2
            # Newer (newdots=3) should depend on older (newdots=2).
            new, old = rows[0], rows[1]
            assert new["new_dots"] == 3
            assert old["new_dots"] == 2
            assert new["depends_on"] == old["id"]
    finally:
        with get_db() as conn:
            conn.execute("DELETE FROM spend_requests WHERE character_id=?", (cid,))
            conn.commit()
