"""End-to-end smoke test for the new coterie contribution flows.

Covers:
  - Time-skip advance (member pays personal XP, status transitions, approval)
  - Personal-XP merit purchase (3-dot cap enforcement)
  - Donation from sheet (shared flag added on approval, cleared on member leave)
  - Inactivity suspend / unsuspend
  - Member-removal contribution clearing
  - Cached chasse/lien/portillon recompute

Run: .venv312/Scripts/python tools/smoke_coterie.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient
from web.main import app
from web.db import (
    get_db, run_migrations,
    create_coterie, add_coterie_member,
    coterie_effective_rating, list_coterie_contributions,
    approve_coterie_spend,
    adjust_xp_manual,
    upsert_player, create_character, approve_character,
    suspend_member_contributions, unsuspend_member_contributions,
    remove_coterie_member,
    get_character, list_coterie_spends,
)

CSRF = "dev-csrf-token"
PLAYER_ID = "777777777777777777"
CHECKS: list[tuple[str, bool, str]] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    CHECKS.append(("OK   " if cond else "FAIL ", label, detail))


def setup():
    run_migrations()
    with get_db() as conn:
        upsert_player(conn, discord_id=PLAYER_ID, username="CoterieTester")
        # Create two characters under the same player so we have multiple
        # potential funders / members for the coterie flows.
        existing = conn.execute(
            "SELECT id, name FROM characters WHERE discord_id=?", (PLAYER_ID,),
        ).fetchall()
        names = {r["name"] for r in existing}
        if "Alice Smoke" not in names:
            alice = create_character(conn, discord_id=PLAYER_ID, name="Alice Smoke",
                                     clan="brujah")
            approve_character(conn, alice["id"], reviewer_id="smoke")
            adjust_xp_manual(conn, alice["id"], +100, "smoke top-up", staff_id="smoke")
        if "Bob Smoke" not in names:
            bob = create_character(conn, discord_id=PLAYER_ID, name="Bob Smoke",
                                   clan="ventrue")
            approve_character(conn, bob["id"], reviewer_id="smoke")
            adjust_xp_manual(conn, bob["id"], +100, "smoke top-up", staff_id="smoke")
        conn.commit()
        chars = conn.execute(
            "SELECT id, name FROM characters WHERE discord_id=? AND name IN ('Alice Smoke','Bob Smoke')",
            (PLAYER_ID,),
        ).fetchall()
        alice_id = next(c["id"] for c in chars if c["name"] == "Alice Smoke")
        bob_id   = next(c["id"] for c in chars if c["name"] == "Bob Smoke")

        # Give Alice a Haven 2 merit on her sheet for the donation test.
        alice = get_character(conn, alice_id)
        sheet = dict(alice.get("sheet_json") or {})
        adv = list(sheet.get("advantages") or [])
        adv = [e for e in adv if str(e.get("name", "")).lower() != "haven"]
        adv.append({"name": "Haven", "dots": 2})
        sheet["advantages"] = adv
        conn.execute("UPDATE characters SET sheet_json=? WHERE id=?",
                     (__import__("json").dumps(sheet), alice_id))

        # Re-create the coterie fresh every run so the test is idempotent.
        # Cascades clean up memberships, spends, contributions thanks to
        # the foreign-key actions on those tables.
        existing = conn.execute(
            "SELECT id FROM coteries WHERE name='Smoke Coterie'",
        ).fetchall()
        for r in existing:
            conn.execute("DELETE FROM coterie_contributions WHERE coterie_id=?",
                         (r["id"],))
            conn.execute("DELETE FROM coterie_spends         WHERE coterie_id=?",
                         (r["id"],))
            conn.execute("DELETE FROM coterie_memberships    WHERE coterie_id=?",
                         (r["id"],))
            conn.execute("DELETE FROM coterie_merits         WHERE coterie_id=?",
                         (r["id"],))
            conn.execute("DELETE FROM coterie_flaws          WHERE coterie_id=?",
                         (r["id"],))
            conn.execute("DELETE FROM coteries               WHERE id=?",
                         (r["id"],))
        co = create_coterie(conn, name="Smoke Coterie", chasse=1, lien=0, portillon=0)
        # Reset Alice's sheet so Haven 2 is un-shared each run.
        alice = get_character(conn, alice_id)
        sheet = dict(alice.get("sheet_json") or {})
        adv = list(sheet.get("advantages") or [])
        adv = [e for e in adv if str(e.get("name", "")).lower() != "haven"]
        adv.append({"name": "Haven", "dots": 2})
        sheet["advantages"] = adv
        conn.execute("UPDATE characters SET sheet_json=? WHERE id=?",
                     (__import__("json").dumps(sheet), alice_id))
        coterie_id = co["id"]
        add_coterie_member(conn, coterie_id, alice_id, role="leader")
        add_coterie_member(conn, coterie_id, bob_id, role="member")
        # Top them up again — multiple test runs would otherwise drain Alice.
        adjust_xp_manual(conn, alice_id, +200, "smoke top-up", staff_id="smoke")
        adjust_xp_manual(conn, bob_id,   +200, "smoke top-up", staff_id="smoke")
        conn.commit()
        return coterie_id, alice_id, bob_id


def main():
    coterie_id, alice_id, bob_id = setup()
    print(f"Setup: coterie={coterie_id}, alice={alice_id}, bob={bob_id}")
    print()

    with TestClient(app) as client:
        # Player session for Alice's owner
        with get_db() as conn:
            from web.db import upsert_player
            # The dev preview login route sets a fixed discord_id; we
            # need our test player on the session. Use the OAuth path or
            # set the session directly via _dev/player and then override.
            pass

        # Use direct dict-based session injection by exploiting the dev
        # preview helper but then mutating the session cookie. Simplest
        # is to log in as our test player by manually setting it.
        from starlette.requests import Request as _R  # noqa: F401
        from itsdangerous import TimestampSigner
        import json
        sess = {"user": {"id": PLAYER_ID, "username": "CoterieTester", "avatar": None},
                "is_staff": False, "_csrf": CSRF}
        # Reach into the app's session middleware for the signing key.
        from web.config import settings as _s
        signer = TimestampSigner(_s.SESSION_SECRET)
        import base64
        raw = base64.b64encode(json.dumps(sess).encode()).decode()
        client.cookies.set("enoch_session", signer.sign(raw).decode())

        # ── 1) Time-skip advance: Alice pays personal XP to bump Chasse ──
        before_chasse = None
        with get_db() as conn:
            before_chasse = coterie_effective_rating(conn, coterie_id, "chasse")
            alice_xp_before = get_character(conn, alice_id)["xp_available"]

        r = client.post(f"/coteries/{coterie_id}/advance",
                        data={"_csrf": CSRF, "target_kind": "chasse",
                              "funded_by_character_id": alice_id,
                              "justification": "smoke advance"},
                        follow_redirects=False)
        check("advance: POST returns 2xx", 200 <= r.status_code < 400,
              f"status={r.status_code}")

        # Verify spend was created and approve it as staff
        with get_db() as conn:
            spends = list_coterie_spends(conn, coterie_id)
            adv_spends = [s for s in spends if s.get("contribution_type") == "timeskip_advance"]
            check("advance: spend row created with contribution_type=timeskip_advance",
                  len(adv_spends) == 1)
            check("advance: status=funded (single funder auto-funded)",
                  bool(adv_spends) and adv_spends[0]["status"] == "funded")
            check("advance: funded_by_character_id matches",
                  bool(adv_spends) and adv_spends[0]["funded_by_character_id"] == alice_id)

            # Approve as staff
            approve_coterie_spend(conn, adv_spends[0]["id"], reviewer_id="smoke")
            conn.commit()
            after_chasse = coterie_effective_rating(conn, coterie_id, "chasse")
            check("advance: chasse rating +1 after approval",
                  after_chasse == before_chasse + 1,
                  f"before={before_chasse} after={after_chasse}")
            alice = get_character(conn, alice_id)
            check("advance: Alice's XP decreased",
                  alice["xp_available"] < alice_xp_before,
                  f"before={alice_xp_before} after={alice['xp_available']}")

            # Confirm contribution row written
            contribs = list_coterie_contributions(
                conn, coterie_id, target_kind="chasse", status=None,
            )
            paid = [c for c in contribs if c["contribution_type"] == "timeskip_advance"]
            check("advance: contribution row written", len(paid) == 1)
            check("advance: contribution.character_id is Alice",
                  bool(paid) and paid[0]["character_id"] == alice_id)
            check("advance: contribution.status='active'",
                  bool(paid) and paid[0]["status"] == "active")

        # ── 2) Personal-XP merit (3-dot cap enforcement) ──
        r = client.post(f"/coteries/{coterie_id}/buy-trait",
                        data={"_csrf": CSRF, "target_kind": "merit",
                              "target_name": "Multilevel Lorekeeping",
                              "dots": 2,
                              "funded_by_character_id": bob_id},
                        follow_redirects=False)
        check("merit: POST returns 2xx", 200 <= r.status_code < 400, f"status={r.status_code}")
        with get_db() as conn:
            spends = [s for s in list_coterie_spends(conn, coterie_id)
                      if s.get("contribution_type") == "paid_xp"]
            check("merit: paid_xp spend row created", len(spends) == 1)
            approve_coterie_spend(conn, spends[0]["id"], reviewer_id="smoke")
            conn.commit()
            ml = coterie_effective_rating(conn, coterie_id, "merit",
                                          "Multilevel Lorekeeping")
            check("merit: Multilevel Lorekeeping=2 after approval", ml == 2,
                  f"got {ml}")

        # Try to buy 2 more (would push to 4) — should be rejected by validator
        r = client.post(f"/coteries/{coterie_id}/buy-trait",
                        data={"_csrf": CSRF, "target_kind": "merit",
                              "target_name": "Multilevel Lorekeeping",
                              "dots": 2,
                              "funded_by_character_id": bob_id},
                        follow_redirects=False)
        # Validator rejects → flash toast in response, no new spend row
        with get_db() as conn:
            spends = [s for s in list_coterie_spends(conn, coterie_id)
                      if s.get("contribution_type") == "paid_xp"
                      and s["trait_name"] == "Multilevel Lorekeeping"]
            check("merit: 3-dot cap enforced (no new spend for over-cap)",
                  len(spends) == 1,
                  f"expected 1 spend, got {len(spends)}")

        # ── 3) Donation: Alice donates Haven 2 ──
        r = client.post(f"/coteries/{coterie_id}/donate",
                        data={"_csrf": CSRF, "target_kind": "background",
                              "target_name": "Haven", "dots": 2,
                              "funded_by_character_id": alice_id},
                        follow_redirects=False)
        check("donate: POST returns 2xx", 200 <= r.status_code < 400, f"status={r.status_code}")
        with get_db() as conn:
            donations = [s for s in list_coterie_spends(conn, coterie_id)
                         if s.get("contribution_type") == "donated"]
            check("donate: donated spend row created", len(donations) == 1)
            check("donate: xp cost = 0",
                  bool(donations) and donations[0]["total_cost"] == 0)
            approve_coterie_spend(conn, donations[0]["id"], reviewer_id="smoke")
            conn.commit()
            haven = coterie_effective_rating(conn, coterie_id, "background", "Haven")
            check("donate: Haven=2 on coterie", haven == 2)
            alice = get_character(conn, alice_id)
            sheet_adv = (alice.get("sheet_json") or {}).get("advantages") or []
            haven_entry = next((e for e in sheet_adv
                                if str(e.get("name", "")).lower() == "haven"), None)
            check("donate: Alice's sheet still has Haven",
                  haven_entry is not None,
                  f"adv={sheet_adv}")
            check("donate: Haven flagged shared with coterie",
                  haven_entry is not None
                  and coterie_id in (haven_entry.get("shared_with_coteries") or []),
                  f"entry={haven_entry}")

        # ── 4) Inactivity sweep: flip Alice to inactive, contributions suspend ──
        with get_db() as conn:
            chasse_active = coterie_effective_rating(conn, coterie_id, "chasse")
            haven_active = coterie_effective_rating(conn, coterie_id, "background", "Haven")
            suspended = suspend_member_contributions(conn, alice_id, actor_id="smoke")
            conn.commit()
            check("inactivity: returns affected coterie list",
                  coterie_id in suspended,
                  f"suspended={suspended}")
            chasse_after = coterie_effective_rating(conn, coterie_id, "chasse")
            haven_after = coterie_effective_rating(conn, coterie_id, "background", "Haven")
            # Chasse before included alice's contribution; haven before included alice's donation
            check("inactivity: chasse dropped (Alice's advance suspended)",
                  chasse_after < chasse_active,
                  f"before={chasse_active} after={chasse_after}")
            check("inactivity: haven dropped to 0 (Alice's donation suspended)",
                  haven_after == 0,
                  f"got {haven_after}")
            # Cached column also updated
            co = conn.execute("SELECT chasse FROM coteries WHERE id=?",
                              (coterie_id,)).fetchone()
            check("inactivity: cached coteries.chasse updated",
                  co["chasse"] == chasse_after,
                  f"cached={co['chasse']} computed={chasse_after}")

        # Reactivate
        with get_db() as conn:
            reactivated = unsuspend_member_contributions(conn, alice_id, actor_id="smoke")
            conn.commit()
            check("reactivation: returns affected coterie list",
                  coterie_id in reactivated)
            haven_back = coterie_effective_rating(conn, coterie_id, "background", "Haven")
            check("reactivation: haven back to 2", haven_back == 2)

        # ── 5) Remove member: Alice leaves, donation flag should clear ──
        with get_db() as conn:
            remove_coterie_member(conn, coterie_id, alice_id)
            conn.commit()
            haven_after_leave = coterie_effective_rating(
                conn, coterie_id, "background", "Haven",
            )
            check("leave: Haven gone from coterie", haven_after_leave == 0)
            alice = get_character(conn, alice_id)
            sheet_adv = (alice.get("sheet_json") or {}).get("advantages") or []
            haven_entry = next((e for e in sheet_adv
                                if str(e.get("name", "")).lower() == "haven"), None)
            check("leave: Haven still on Alice's sheet (un-donated)",
                  haven_entry is not None)
            check("leave: shared_with_coteries flag cleared",
                  haven_entry is not None
                  and coterie_id not in (haven_entry.get("shared_with_coteries") or []),
                  f"entry={haven_entry}")

    # Report
    print()
    print("-" * 90)
    print(f"{'status':6}{'check':60}detail")
    print("-" * 90)
    for status, label, detail in CHECKS:
        print(f"{status} {label[:58]:60}{detail[:200]}")
    print()
    fails = [c for c in CHECKS if c[0] != "OK   "]
    print(f"Summary: {len(fails)}/{len(CHECKS)} failed")
    if fails:
        sys.exit(1)


if __name__ == "__main__":
    main()
