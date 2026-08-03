"""B-26 · the prewarm 401 named a consequence that does not occur.

`prewarm()` probes `GET /v1/models`. Synthesis uses
`POST /v1/text-to-speech/{voice}/stream`. A key scoped for TTS but without
`models_read` 401s on the first and succeeds on the second — which is exactly
what eight live calls show (three on 2 Aug, five on 3 Aug): a 401 here,
followed by thirty-five-plus 200s from the synthesis endpoint on the same
calls, some within seconds.

The old line said "credits exhausted or key invalid. The first call will fall
back to OpenAI TTS mid-sentence", at ERROR, on every call. None of that
happened. An ERROR that fires every time and predicts an event that never
occurs teaches an operator to filter the channel that would carry a real
outage — so this test pins the level and the claim, not the prose.
"""

import logging

import httpx
import pytest

from app.media_streams import tts_stream


class _Resp:
    def __init__(self, status_code):
        self.status_code = status_code


class _Client:
    def __init__(self, status_code):
        self._status = status_code

    async def get(self, url, headers=None, timeout=None):
        return _Resp(self._status)


@pytest.fixture
def _keyed(monkeypatch):
    """prewarm() returns early without an API key, so give it one."""
    monkeypatch.setattr(tts_stream, "ELEVENLABS_API_KEY", "sk-test-key")


def _prewarm_records(monkeypatch, caplog, status_code):
    monkeypatch.setattr(tts_stream, "_get_http_client", lambda: _Client(status_code))
    caplog.set_level(logging.DEBUG, logger="app.media_streams.tts_stream")
    caplog.clear()
    return caplog


async def test_401_is_not_logged_at_error(_keyed, monkeypatch, caplog):
    """The level is the load-bearing part: ERROR on every call is the defect."""
    _prewarm_records(monkeypatch, caplog, 401)
    await tts_stream.prewarm()

    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert not errors, (
        "prewarm logged at ERROR for a 401 on /v1/models: "
        f"{[r.getMessage() for r in errors]}"
    )


async def test_401_still_says_something(_keyed, monkeypatch, caplog):
    """Demoting is not deleting — a scope problem is still worth surfacing."""
    _prewarm_records(monkeypatch, caplog, 401)
    await tts_stream.prewarm()

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings, "a 401 on the prewarm probe should still warn"
    assert "401" in warnings[0].getMessage()


async def test_401_does_not_claim_credits_or_a_tts_fallback(_keyed, monkeypatch, caplog):
    """The false claims, pinned individually so a reworded message that
    reintroduces one of them fails loudly."""
    _prewarm_records(monkeypatch, caplog, 401)
    await tts_stream.prewarm()

    msg = " ".join(r.getMessage() for r in caplog.records).lower()
    assert "credits exhausted" not in msg, (
        "the 401 is consistent with a missing `models_read` scope; synthesis "
        "returned 200 on the same calls, so exhausted credits is not supported"
    )
    assert "will fall back to openai" not in msg, (
        "prewarm cannot know this, and on eight live calls it did not happen"
    )


async def test_a_healthy_prewarm_is_unchanged(_keyed, monkeypatch, caplog):
    """No new noise on the path that runs when everything is fine."""
    _prewarm_records(monkeypatch, caplog, 200)
    await tts_stream.prewarm()

    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


async def test_prewarm_never_raises(_keyed, monkeypatch, caplog):
    """A cold pool is a latency problem, never a call-failure one — the
    docstring's promise, kept under a transport error."""

    class _Boom:
        async def get(self, *a, **kw):
            raise httpx.ConnectError("no route to host")

    monkeypatch.setattr(tts_stream, "_get_http_client", lambda: _Boom())
    caplog.set_level(logging.DEBUG, logger="app.media_streams.tts_stream")

    assert await tts_stream.prewarm() == 0.0
