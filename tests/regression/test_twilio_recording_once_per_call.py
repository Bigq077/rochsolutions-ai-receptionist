"""The Twilio recording request must fire once per call, not once per POST.

Twilio POSTs /ms/incoming a SECOND time with the SAME CallSid once the
<Connect><Stream> verb finishes — it is asking what to do next, not announcing
a new call. start_twilio_recording fired on every inbound POST, so the
post-stream one always hit a call that can no longer be recorded:

    16:21:55.191  POST .../Calls/CAxxxx/Recordings.json  HTTP 400
                  {"code":21220,"message":"Requested resource is not eligible
                   for recording"}

A spurious 400 in every call log, on a line being read to diagnose audio loss.
"""
from __future__ import annotations

import pytest

from app.media_streams import audio_capture as ac


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    """Recording enabled, credentials present, no Redis, clean local guard."""
    monkeypatch.setenv("TWILIO_CALL_RECORDING_ENABLED", "true")
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "ACtest")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "tokentest")
    monkeypatch.setattr(
        "app.media_streams.session._get_redis", lambda: None, raising=False
    )
    ac._recording_claimed_local.clear()
    yield
    ac._recording_claimed_local.clear()


class _Recorder:
    """Stands in for httpx.AsyncClient, counting POSTs."""

    def __init__(self, status: int = 201):
        self.calls: list = []
        self._status = status

    def __call__(self, *a, **kw):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, **kw):
        self.calls.append(url)
        return type("R", (), {
            "status_code": self._status, "text": "", "json": lambda s: {},
        })()


@pytest.fixture
def http(monkeypatch):
    import httpx
    rec = _Recorder()
    monkeypatch.setattr(httpx, "AsyncClient", rec)
    return rec


async def test_second_post_with_same_sid_does_not_hit_twilio(http):
    """THE case: the post-stream re-POST that produced the 21220."""
    await ac.start_twilio_recording("CAsame")
    await ac.start_twilio_recording("CAsame")

    assert len(http.calls) == 1


async def test_first_post_still_records(http):
    await ac.start_twilio_recording("CAfirst")

    assert len(http.calls) == 1
    assert "/Calls/CAfirst/Recordings.json" in http.calls[0]


async def test_distinct_calls_are_each_recorded(http):
    """The guard is per-CallSid, not a global latch — back-to-back calls must
    each still be recorded."""
    await ac.start_twilio_recording("CAone")
    await ac.start_twilio_recording("CAtwo")
    await ac.start_twilio_recording("CAone")   # re-POST for the first call

    assert len(http.calls) == 2


async def test_claim_is_not_burned_when_credentials_are_missing(monkeypatch, http):
    """A misconfigured deploy must not consume the claim and then silently
    skip recording once the secrets are fixed."""
    monkeypatch.delenv("TWILIO_AUTH_TOKEN", raising=False)
    await ac.start_twilio_recording("CAcreds")
    assert len(http.calls) == 0
    assert "CAcreds" not in ac._recording_claimed_local

    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "tokentest")
    await ac.start_twilio_recording("CAcreds")
    assert len(http.calls) == 1


async def test_disabled_flag_makes_it_a_no_op(monkeypatch, http):
    monkeypatch.setenv("TWILIO_CALL_RECORDING_ENABLED", "false")
    await ac.start_twilio_recording("CAoff")
    assert len(http.calls) == 0


async def test_local_guard_is_bounded():
    """A long-lived process must not accumulate CallSids without limit."""
    for i in range(ac._RECORDING_CLAIM_LOCAL_MAX + 50):
        await ac._claim_recording(f"CA{i}")
    assert len(ac._recording_claimed_local) <= ac._RECORDING_CLAIM_LOCAL_MAX


async def test_eviction_is_oldest_first():
    """Eviction must drop the oldest CallSid, not the one currently in flight."""
    await ac._claim_recording("CAoldest")
    for i in range(ac._RECORDING_CLAIM_LOCAL_MAX):
        await ac._claim_recording(f"CAfill{i}")
    assert "CAoldest" not in ac._recording_claimed_local
    assert f"CAfill{ac._RECORDING_CLAIM_LOCAL_MAX - 1}" in ac._recording_claimed_local


# ─────────────────────────────────────────────────────────────────────────
# Redis path — the mechanism that actually runs in production, where the
# re-POST may land on a different worker than the one that served the first.
# ─────────────────────────────────────────────────────────────────────────
class _FakeRedis:
    def __init__(self, fail: bool = False):
        self.store: dict = {}
        self.fail = fail

    async def set(self, key, value, ex=None, nx=False):
        if self.fail:
            raise RuntimeError("redis down")
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True


async def test_redis_claim_is_used_when_available(monkeypatch, http):
    fake = _FakeRedis()
    monkeypatch.setattr(
        "app.media_streams.session._get_redis", lambda: fake, raising=False
    )

    await ac.start_twilio_recording("CAredis")
    await ac.start_twilio_recording("CAredis")

    assert len(http.calls) == 1
    assert "ms_rec_claimed:CAredis" in fake.store
    # the local fallback was never consulted
    assert not ac._recording_claimed_local


async def test_redis_claim_survives_a_second_worker(monkeypatch, http):
    """Two processes sharing Redis must still produce exactly one request —
    this is what the per-process guard alone cannot do."""
    fake = _FakeRedis()
    monkeypatch.setattr(
        "app.media_streams.session._get_redis", lambda: fake, raising=False
    )

    assert await ac._claim_recording("CAworker") is True
    ac._recording_claimed_local.clear()          # simulate a different process
    assert await ac._claim_recording("CAworker") is False


async def test_redis_failure_falls_back_to_local_guard(monkeypatch, http):
    """Redis being down must not stop recording, and must not reopen the
    duplicate within one process."""
    monkeypatch.setattr(
        "app.media_streams.session._get_redis",
        lambda: _FakeRedis(fail=True), raising=False,
    )

    await ac.start_twilio_recording("CAfallback")
    await ac.start_twilio_recording("CAfallback")

    assert len(http.calls) == 1
    assert "CAfallback" in ac._recording_claimed_local
