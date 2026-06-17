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
