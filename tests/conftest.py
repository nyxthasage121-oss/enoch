"""Pytest fixtures — boot the FastAPI app against a fresh test DB.

Env vars are set BEFORE importing the app so settings pick them up at module load.
"""
import os
import tempfile
from pathlib import Path

import pytest

# ── Env setup (must happen before `from web.main import app`) ─────────────────

_TEST_DB = Path(tempfile.gettempdir()) / "enoch_smoke_test.db"
if _TEST_DB.exists():
    _TEST_DB.unlink()

os.environ["DATABASE_URL"]       = str(_TEST_DB)
os.environ["ENOCH_DEV_PREVIEW"]  = "1"
os.environ["SESSION_SECRET"]     = "smoke-test-secret-not-for-production"
os.environ["BOT_SERVICE_TOKEN"]  = "smoke-test-token"

from fastapi.testclient import TestClient   # noqa: E402

from web.main import app                    # noqa: E402


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def _client():
    """Single TestClient for the whole session. Lifespan runs migrations."""
    with TestClient(app) as client:
        # Trigger dev seed once so a baseline character exists for tests.
        client.get("/_dev/seed_data", follow_redirects=False)
        yield client


@pytest.fixture
def anon(_client):
    """Unauthenticated client — clears session cookies before yielding."""
    _client.cookies.clear()
    return _client


@pytest.fixture
def player(_client):
    """Client authenticated as the dev TestPlayer."""
    _client.cookies.clear()
    _client.get("/_dev/seed_data", follow_redirects=False)
    _client.get("/_dev/player",    follow_redirects=False)
    return _client


@pytest.fixture
def staff(_client):
    """Client authenticated as the dev DevStaff."""
    _client.cookies.clear()
    _client.get("/_dev/seed_data", follow_redirects=False)
    _client.get("/_dev/seed",      follow_redirects=False)
    return _client
