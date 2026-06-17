"""Regression: a new coterie must not be granted Chasse (or any domain)
automatically. NYbN coteries start at 0 and build Chasse/Lien/Portillon via
contributions — see create_coterie + the staff direct-create clamp."""
from web.db import get_db, create_coterie


def test_new_coterie_starts_at_zero_domain(_client):
    with get_db() as conn:
        co = create_coterie(conn, "ChasseZeroRegression")
    assert co["chasse"] == 0, "coterie should not get Chasse automatically"
    assert co["lien"] == 0
    assert co["portillon"] == 0


def test_staff_granted_domain_is_durable(_client):
    """A staff-set starting domain must be backed by a contribution so it
    survives a recompute — a bare column write (the old behaviour) got wiped
    the first time any coterie event recomputed the cached ratings."""
    from web.db import (add_coterie_contribution, _recompute_coterie_ratings,
                        get_coterie)
    with get_db() as conn:
        co = create_coterie(conn, "DurableDomain")
        add_coterie_contribution(
            conn, coterie_id=co["id"], contribution_type="staff_grant",
            target_kind="chasse", target_name=None, dots=2,
            note="start", recompute=False)
        _recompute_coterie_ratings(conn, co["id"])
        assert get_coterie(conn, co["id"])["chasse"] == 2
        _recompute_coterie_ratings(conn, co["id"])             # a later event
        assert get_coterie(conn, co["id"])["chasse"] == 2      # must not wipe it
