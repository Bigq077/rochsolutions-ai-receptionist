"""B-13 — a failed ElevenLabs prewarm must not log as "connection ready".

The prewarm GETs /v1/models at webhook time to warm the TLS pool.  It
interpolated ``resp.status_code`` into its log line but never branched on it, so
an auth failure produced:

    [ms_tts] prewarm: connection ready in 214ms (status=401)

at INFO — the word "ready", the wrong level, and the number that contradicts it
buried at the end.  The socket really is warm on a 401, which is why this read
as a success for so long; the credential behind it is dead, which is what
actually matters.

The severity is understood everywhere else in this module: ``_ELEVENLABS_EXHAUSTED``
exists for exactly this fault, and ``synthesise_chunk`` logs ``logger.error`` and
switches the process to the OpenAI fallback on the same status.  That path just
does not run until a caller is on the line.  The prewarm is the earliest moment
anything can know, and it was throwing the signal away.

The half of this worth guarding is the pair: a 401 must be loud, and a 200 must
stay quiet.  A prewarm that cries wolf on every healthy start is worse than the
silence it replaced, because the line it drowns is this one.
"""
import logging

import pytest

from app.media_streams import tts_stream


class _FakeResponse:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


class _FakeClient:
    def __init__(self, status_code: int) -> None:
        self._status_code = status_code

    async def get(self, *_args, **_kwargs):
        return _FakeResponse(self._status_code)


@pytest.fixture
def prewarm_with(monkeypatch):
    """Run prewarm() against a stubbed ElevenLabs and return the log records."""

    async def _run(status_code: int, caplog):
        monkeypatch.setattr(tts_stream, "ELEVENLABS_API_KEY", "test-key")
        monkeypatch.setattr(
            tts_stream, "_get_http_client", lambda: _FakeClient(status_code)
        )
        with caplog.at_level(logging.INFO, logger=tts_stream.logger.name):
            elapsed = await tts_stream.prewarm()
        return elapsed, caplog.records

    return _run


# ── The defect ──────────────────────────────────────────────────────────────

async def test_a_401_does_not_log_as_ready(prewarm_with, caplog):
    _, records = await prewarm_with(401, caplog)

    assert records, "prewarm logged nothing at all on a 401"
    text = " ".join(r.getMessage() for r in records)
    assert "ready" not in text.lower(), (
        "a rejected API key still reports the connection as ready: " + text
    )


async def test_a_401_is_logged_loudly_enough_to_see(prewarm_with, caplog):
    """B-26, 3 Aug 2026 — this test used to require ERROR. That requirement was
    wrong and it is now inverted; the level assertion lives in
    `test_prewarm_401_does_not_claim_tts_failure.py`.

    The premise here was "a dead credential ... the one fault that silences the
    assistant". True of a 401 from `synthesise_chunk`. **Not** true of a 401
    from `GET /v1/models`, which is not the synthesis endpoint: eleven live
    calls across 2–3 Aug 2026 logged this 401 while
    `POST /v1/text-to-speech/{voice}/stream` returned 200 throughout, some of
    them seconds apart. So the ERROR fired on every call for a consequence that
    never arrived, which is how a channel stops being read.

    What survives is the half that was always right: a 401 must not be silent
    and must not read as success. That is asserted below and in
    `test_a_401_does_not_log_as_ready`.
    """
    _, records = await prewarm_with(401, caplog)

    assert any(r.levelno >= logging.WARNING for r in records), (
        "a 401 on the prewarm probe logged below WARNING — it is a real "
        "credential-scope signal even though it does not predict a TTS failure"
    )


async def test_a_401_says_what_is_wrong_not_just_the_number(prewarm_with, caplog):
    """A bare 'status=401' is what made this invisible for as long as it was."""
    _, records = await prewarm_with(401, caplog)

    text = " ".join(r.getMessage() for r in records).lower()
    assert "key" in text or "credit" in text, (
        "the 401 line names no cause; an operator reading it still has to know "
        "what 401 means to ElevenLabs: " + text
    )


# ── The false-positive half: a healthy prewarm must stay quiet ──────────────

async def test_a_200_still_logs_ready_at_info(prewarm_with, caplog):
    _, records = await prewarm_with(200, caplog)

    assert records, "the healthy path stopped logging entirely"
    assert all(r.levelno <= logging.INFO for r in records), (
        "a healthy prewarm now logs at warning or above — this drowns the 401 "
        "line it exists to make visible"
    )
    assert "ready" in " ".join(r.getMessage() for r in records).lower()


@pytest.mark.parametrize("status_code", [429, 500, 503])
async def test_other_failures_are_visible_but_not_errors(
    prewarm_with, caplog, status_code
):
    """A degraded API is worth a warning; it is not a dead credential."""
    _, records = await prewarm_with(status_code, caplog)

    assert any(r.levelno == logging.WARNING for r in records), (
        f"status {status_code} logged below WARNING"
    )
    assert not any(r.levelno >= logging.ERROR for r in records), (
        f"status {status_code} logged as ERROR — only 401 is a dead credential, "
        "and an error that fires on a transient 503 stops being read"
    )
    assert "ready" not in " ".join(r.getMessage() for r in records).lower()


# ── What deliberately did NOT change ────────────────────────────────────────

@pytest.mark.parametrize("status_code", [200, 401, 500])
async def test_prewarm_still_returns_elapsed_on_every_status(
    prewarm_with, caplog, status_code
):
    """The return feeds latency accounting, not health.

    A warm socket is a warm socket regardless of what the API said, and the
    caller of prewarm() treats a 0.0 as "the pool is cold".  Reporting a fault
    must not make the greeting pay a cold start it did not incur.
    """
    elapsed, _ = await prewarm_with(status_code, caplog)

    assert elapsed > 0.0, (
        "a non-200 now returns 0.0 — the log fix leaked into latency accounting"
    )


async def test_prewarm_does_not_arm_the_exhaustion_flag(prewarm_with, caplog):
    """B-13 is a log fix, not a behaviour change.

    Arming _ELEVENLABS_EXHAUSTED here would move the fallback decision off the
    synth path and onto a startup probe — defensible, but a separate row with
    its own evidence.  Asserted so it cannot be folded in silently.
    """
    before = tts_stream._ELEVENLABS_EXHAUSTED
    await prewarm_with(401, caplog)

    assert tts_stream._ELEVENLABS_EXHAUSTED is before, (
        "the prewarm now arms the exhaustion flag — that is a behaviour change "
        "that belongs in its own commit"
    )


async def test_a_missing_key_still_skips_silently(monkeypatch, caplog):
    """No key configured is a deployment choice, not a fault to shout about."""
    monkeypatch.setattr(tts_stream, "ELEVENLABS_API_KEY", "")
    with caplog.at_level(logging.INFO, logger=tts_stream.logger.name):
        elapsed = await tts_stream.prewarm()

    assert elapsed == 0.0
    assert not any(r.levelno >= logging.WARNING for r in caplog.records)
