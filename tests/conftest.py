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


@pytest.fixture(autouse=True)
def block_outbound_sms(request):
    """No test sends a real text. Autouse, opt-out only.

    8 Aug 2026: a regression test for the cancel path put SIX live cancellation
    texts on the repo owner's phone ("Hi PENDING, your appointment on Monday 10
    Aug at 6:00pm has been cancelled"). The test DID patch
    `app.notifications.sms.send_sms` — but `booking_sms.py`, `owner_alert.py`
    and `notifications/__init__.py` each bind their own reference at import
    time (`from app.notifications.sms import send_sms`), so patching the source
    module left every one of those copies pointing at Twilio.

    That is not a mistake a test author can be trusted to remember, so the block
    lives here and covers every binding. Same class as the tests/auto smoke test
    that booked 60 real Acuity appointments, and the same remedy: opt IN.

    A test that genuinely needs the real sender marks itself
    `@pytest.mark.live_sms` — which should essentially never be used.
    """
    if request.node.get_closest_marker("live_sms"):
        yield
        return

    sent: list = []

    async def _blocked(*args, **kwargs):
        sent.append({"to": kwargs.get("to"), "message": kwargs.get("message")})
        return {"blocked_in_tests": True}

    # Every MODULE-LEVEL binding, not just the source. Function-level imports
    # (`owner_notify`, `scheduler`, `receptionist_tools`) resolve at call time
    # and are covered by patching the source module.
    # `smart_sms_router` binds a binding — `from booking_sms import send_sms` —
    # so patching either source module still misses its copy.
    targets = [
        "app.notifications.sms.send_sms",
        "app.notifications.booking_sms.send_sms",
        "app.notifications.owner_alert.send_sms",
        "app.notifications.smart_sms_router.send_sms",
        "app.notifications.send_sms",
    ]
    patches = []
    for t in targets:
        try:
            p = patch(t, side_effect=_blocked)
            p.start()
            patches.append(p)
        except (AttributeError, ModuleNotFoundError):
            # A binding that does not exist cannot leak. Never fail the suite
            # over one — but never silently skip the ones that do exist either.
            continue
    try:
        yield sent
    finally:
        for p in patches:
            p.stop()


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
