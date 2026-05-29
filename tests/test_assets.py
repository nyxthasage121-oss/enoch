"""Tests for static_url asset cache-busting (web/main.py)."""
import pytest


@pytest.fixture(autouse=True)
def _migrations(_client):
    yield


def test_static_url_appends_content_hash():
    from web.main import static_url
    u = static_url("css/codex.css")
    assert u.startswith("/static/css/codex.css?v="), u
    # Deterministic for unchanged content.
    assert static_url("css/codex.css") == u
    # A different file fingerprints differently.
    assert static_url("js/codex.js") != u


def test_static_url_missing_file_falls_back_to_bare_path():
    from web.main import static_url
    assert static_url("css/nope-not-real.css") == "/static/css/nope-not-real.css"


def test_rendered_pages_link_versioned_assets(_client):
    """A rendered page must emit cache-busted asset URLs, not bare paths —
    proves static_url is wired into the base context for every render."""
    r = _client.get("/", follow_redirects=True)
    assert r.status_code == 200
    assert "/static/css/codex.css?v=" in r.text
    assert "/static/js/codex.js?v=" in r.text
    # The bare, un-versioned link must be gone.
    assert 'href="/static/css/codex.css"' not in r.text
