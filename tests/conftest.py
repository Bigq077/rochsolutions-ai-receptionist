"""
Shared pytest fixtures for the AI Receptionist test suite.

Fixtures:
  - mock_redis_session_store: in-memory dict that replaces Redis for session
    operations so tests run without a real Redis instance.
  - disable_twilio_validation: patches the Twilio signature validator so tests
    can call /twilio/* endpoints without a real X-Twilio-Signature header.
  - async_client: an httpx.AsyncClient bound to the FastAPI app.
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch
from typing import Any, Dict


# ---------------------------------------------------------------------------
# In-memory session store
# ---------------------------------------------------------------------------

class _InMemorySessionStore:
    """Minimal in-memory replacement for Redis-backed session storage."""

    def __init__(self):
        self._sessions: Dict[str, Dict[str, Any]] = {}
        self._locks: Dict[str, bool] = {}

    def reset(self):
        self._sessions.clear()
        self._locks.clear()

    async def get_session(self, call_sid: str) -> Dict[str, Any]:
        from app.storage.redis_store import _fresh_default_session
        return dict(self._sessions.get(call_sid) or _fresh_default_session())

    async def save_session(self, call_sid: str, session: Dict[str, Any]) -> None:
        self._sessions[call_sid] = dict(session)

    async def acquire_once_lock(self, key: str, ttl_seconds: int = 300) -> bool:
        if key in self._locks:
            return False
        self._locks[key] = True
        return True


_STORE = _InMemorySessionStore()


@pytest.fixture(autouse=True)
def mock_redis_session_store():
    """
    Patch all Redis session calls with an in-memory dict.
    Automatically applied to every test (autouse=True).
    """
    _STORE.reset()
    with (
        patch("app.routes.twilio.get_session", side_effect=_STORE.get_session),
        patch("app.routes.twilio.save_session", side_effect=_STORE.save_session),
        patch("app.routes.twilio.acquire_once_lock", side_effect=_STORE.acquire_once_lock),
    ):
        yield _STORE


@pytest.fixture()
def disable_twilio_validation():
    """
    Bypass Twilio webhook signature validation.
    Use in tests that POST to /twilio/* without a real signature.
    """
    with patch("app.routes.twilio.TWILIO_AUTH_TOKEN", ""):
        yield


# ---------------------------------------------------------------------------
# Async HTTP client
# ---------------------------------------------------------------------------

@pytest.fixture()
async def async_client(disable_twilio_validation):
    """httpx.AsyncClient bound to the FastAPI app (no live server needed)."""
    from httpx import AsyncClient, ASGITransport
    from app.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="https://testserver",
    ) as client:
        yield client
