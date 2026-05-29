"""POST smoke test — exercises every reported broken flow against TestClient.
Run: .venv312/Scripts/python tools/smoke_posts.py
"""
import sys
import datetime as dt
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient
from web.main import app
from web.db import (
    get_db, run_migrations, create_period, create_criterion,
    adjust_xp_manual, create_hunting_site, set_period_active,
)

CSRF = "dev-csrf-token"
RESULTS = []


def hit(label, method, url, *, client, data=None):
    fn = getattr(client, method.lower())
    kwargs = {"follow_redirects": False}
    if data is not None:
        kwargs["data"] = data
    try:
        r = fn(url, **kwargs)
        ok = 200 <= r.status_code < 400
        status = "OK   " if ok else "FAIL "
        snippet = ""
        if not ok:
            snippet = r.text[:600].replace("\n", " | ")
        RESULTS.append((status, label, method, url, r.status_code, snippet))
        return r
    except Exception as e:
        RESULTS.append(("CRASH", label, method, url, "EXC", f"{type(e).__name__}: {e}"))
        return None


def setup_data():
    """Idempotent setup — period, criterion, hunting site."""
    with get_db() as conn:
        now = dt.datetime.utcnow()
        period_row = conn.execute(
            "SELECT id FROM play_periods WHERE label='Smoke Night' LIMIT 1"
        ).fetchone()
        if not period_row:
            create_period(
                conn,
                label="Smoke Night",
                period_type="night",
                phase="full",
                opens_at=(now - dt.timedelta(hours=1)).isoformat(),
                closes_at=(now + dt.timedelta(hours=48)).isoformat(),
                created_by="smoke",
            )
            period_row = conn.execute(
                "SELECT id FROM play_periods WHERE label='Smoke Night' LIMIT 1"
            ).fetchone()
        set_period_active(conn, period_row["id"])

        crit = conn.execute(
            "SELECT id FROM criteria WHERE label='Smoke Crit' LIMIT 1"
        ).fetchone()
        if not crit:
            create_criterion(
                conn, label="Smoke Crit", xp_value=2,
                category="player", description="smoke",
                requires_rp_links=False, requires_text_note=False,
            )

        site = conn.execute(
            "SELECT id FROM hunting_sites WHERE name='Smoke Site' LIMIT 1"
        ).fetchone()
        if not site:
            create_hunting_site(
                conn, name="Smoke Site", borough="manhattan",
                description="smoke", blood_quality=3,
                predator_dcs={}, coterie_id=None,
                is_contested=False, active=True,
            )
        conn.commit()


def main():
    run_migrations()
    setup_data()
    with TestClient(app) as client:
        # ── Player session ──
        client.get("/_dev/seed_data", follow_redirects=False)
        client.get("/_dev/player", follow_redirects=False)

        with get_db() as conn:
            row = conn.execute(
                "SELECT id FROM characters WHERE name='Valeria Morano' LIMIT 1"
            ).fetchone()
            cid = row["id"]
            crit_id = conn.execute(
                "SELECT id FROM criteria WHERE label='Smoke Crit' LIMIT 1"
            ).fetchone()["id"]
            site_id = conn.execute(
                "SELECT id FROM hunting_sites WHERE name='Smoke Site' LIMIT 1"
            ).fetchone()["id"]
            adjust_xp_manual(conn, cid, +50, "smoke top-up", staff_id="smoke")
            conn.commit()

        # ── Player POSTs ──
        hit("player claim submit", "POST", f"/characters/{cid}/claim",
            client=client, data={"_csrf": CSRF, "criteria_ids": [crit_id], "rp_links": []})
        hit("player claim save draft", "POST", f"/characters/{cid}/claim",
            client=client, data={"_csrf": CSRF, "criteria_ids": [crit_id], "rp_links": [], "as_draft": "1"})
        hit("player spend submit", "POST", f"/characters/{cid}/spend",
            client=client, data={
                "_csrf": CSRF, "category": "discipline",
                "trait_name": "Auspex", "current_dots": 0, "new_dots": 1,
                "note": "smoke",
            })

        # ── Staff session ──
        client.get("/_dev/seed", follow_redirects=False)

        hit("staff admin adjust-xp", "POST", "/staff/admin/adjust-xp",
            client=client, data={
                "_csrf": CSRF, "character_id": cid, "delta": 5,
                "note": "smoke", "action_type": "grant_xp",
            })

        hit("staff char adjust-xp", "POST", f"/staff/characters/{cid}/adjust-xp",
            client=client, data={
                "_csrf": CSRF, "delta": 1, "note": "smoke",
                "action_type": "grant_xp",
            })

        hit("staff schedule create", "POST", "/staff/periods/schedules",
            client=client, data={
                "_csrf": CSRF, "name": "smoke sched",
                "label_pattern": "Night {n}", "period_type": "night",
                "phase": "full", "cadence_days": 14, "duration_hours": 48,
                "anchor_at": "2026-06-01T20:00",
            })

        hit("staff retire char", "POST", f"/staff/characters/{cid}/retire",
            client=client, data={"_csrf": CSRF, "reason": "smoke"})
        hit("staff unretire char", "POST", f"/staff/characters/{cid}/unretire",
            client=client, data={"_csrf": CSRF})
        hit("staff toggle lock", "POST", f"/staff/characters/{cid}/toggle-lock",
            client=client, data={"_csrf": CSRF})
        hit("staff set ingrained", "POST", f"/staff/characters/{cid}/set-ingrained",
            client=client, data={"_csrf": CSRF, "discipline": "Animalism"})

        hit("staff create site", "POST", "/staff/sites",
            client=client, data={
                "_csrf": CSRF, "name": "Smoke Site 2", "borough": "Queens",
                "blood_quality": 2,
                "sect_control": "Camarilla",
                "description": "",
                "dc_Alleycat": 2,
            })
        hit("staff toggle site", "POST", f"/staff/sites/{site_id}/toggle",
            client=client, data={"_csrf": CSRF})
        hit("staff edit site", "POST", f"/staff/sites/{site_id}/edit",
            client=client, data={
                "_csrf": CSRF, "name": "Smoke Site", "borough": "Manhattan",
                "blood_quality": 4,
                "sect_control": "Hecata",
                "description": "edited",
                "dc_Alleycat": 3,
            })

        # ── Chargen draft / review-flow regressions (Steward 2026-05) ──
        # Player flips back to player session to exercise these.
        client.cookies.clear()
        client.get("/_dev/seed_data", follow_redirects=False)
        client.get("/_dev/player", follow_redirects=False)

        from web.db import upsert_settings
        with get_db() as conn:
            upsert_settings(conn, require_sheet_on_create=0)
            conn.commit()
        try:
            # Short-form Submit must stage as a draft (is_draft=1).
            r = hit("short-form submit stages draft", "POST", "/characters/new",
                    client=client,
                    data={"_csrf": CSRF, "name": "Smoke ShortForm",
                          "clan": "ventrue"})
            with get_db() as conn:
                row = conn.execute(
                    "SELECT id, is_draft FROM characters "
                    "WHERE name='Smoke ShortForm'"
                ).fetchone()
                assert row is not None, "short-form submit did not create"
                assert row["is_draft"] == 1, "short-form submit must stage as draft"
                sf_id = row["id"]

            # Submit for Review flips is_draft off.
            hit("submit-for-review flips draft", "POST",
                f"/characters/{sf_id}/submit-for-review",
                client=client, data={"_csrf": CSRF})
            with get_db() as conn:
                row = conn.execute(
                    "SELECT is_draft, is_approved FROM characters WHERE id=?",
                    (sf_id,),
                ).fetchone()
                assert row["is_draft"] == 0, "submit-for-review must clear draft"
                assert row["is_approved"] == 0, "submit-for-review must not auto-approve"

            # Plain-form approve from staff detail page (no HX header) must
            # redirect, not return the roster partial.
            client.cookies.clear()
            client.get("/_dev/seed_data", follow_redirects=False)
            client.get("/_dev/seed", follow_redirects=False)
            r = hit("approve via plain form redirects", "POST",
                    f"/staff/characters/{sf_id}/approve",
                    client=client, data={"_csrf": CSRF})
            if r is not None:
                loc = r.headers.get("location", "")
                assert r.status_code == 303 and loc.endswith(
                    f"/staff/characters/{sf_id}"), \
                    f"expected 303 → detail page, got {r.status_code} {loc}"
        finally:
            with get_db() as conn:
                upsert_settings(conn, require_sheet_on_create=1)
                conn.execute("DELETE FROM characters WHERE name='Smoke ShortForm'")
                conn.commit()

    print()
    print("-" * 110)
    print(f"{'st':6}{'label':28}{'method':7}{'url':50}{'code':6}detail")
    print("-" * 110)
    for status, label, method, url, code, snippet in RESULTS:
        print(f"{status} {label[:26]:28}{method:7}{url[:48]:50}{code!s:6}{snippet[:250]}")
    print()
    bad = [r for r in RESULTS if r[0] != "OK   "]
    print(f"Summary: {len(bad)}/{len(RESULTS)} broken")


if __name__ == "__main__":
    main()
