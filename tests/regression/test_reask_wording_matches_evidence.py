# tests/regression/test_reask_wording_matches_evidence.py
"""P2 (2026-07-24) — Susie apologised for mishearing callers she had heard perfectly.

Incident
--------
jv_v1 call, 23:14-23:17. FOUR re-asks opened with "Sorry, I didn't catch that",
and on every one the caller had not spoken at all:

    23:14:19  re-ask after  6.0s   caller first spoke 10.78s after the prompt
    23:15:06  re-ask after 10.0s   caller first spoke 17.08s after the prompt
    23:16:12  re-ask after 10.0s   caller first spoke 30.66s after the prompt
    23:16:27  safety net           (same silence)

Inbound audio was flawless for the whole call: 9183 Twilio media frames over
186s, 98.7% of the expected ~20ms rate, and the safety net itself logged
`inbound_audio=flowing media_gap=0.0s frames=7011`. Not one partial transcript
appeared in any of those windows. The caller's engaged replies on the same call
all landed in 1.8-3.3s, so these were real silences, not slow recognition.

So the claim "I didn't catch that" was false every time it was made. To a
listener that is indistinguishable from actually being deaf, which is why
"Susie can't hear me" was the reported symptom on a call where she heard
everything said to her.

Structurally this is the same defect as the DVT escalation asserting a symptom
the caller had denied (a04fc58): stating as established fact something the
available evidence contradicts.

Note the no-input watchdog fires ONLY when nothing was detected —
_mark_prompt_speech_detected() cancels the task the instant a partial arrives —
so the apology was never the common case to begin with.

Fix
---
_reask_prefix() picks the opening from what actually happened, via an
audio_probe supplied by WebSocketCallHandler. The recovery ladder — timings,
attempt counts, escalation, transfer — is completely unchanged.
"""

import pytest

from app.media_streams.connection import SilenceHandler

_APOLOGY_1 = "Sorry, I didn't catch that"
_APOLOGY_2 = "I'm sorry, I'm still not hearing you clearly. Let's try again"


def _handler(probe):
    """A SilenceHandler with only the probe wired — _reask_prefix touches no more."""
    h = SilenceHandler.__new__(SilenceHandler)
    h._audio_probe = probe
    h._REASK_VOICE_RECENCY_SEC = SilenceHandler._REASK_VOICE_RECENCY_SEC
    return h


# ---------------------------------------------------------------------------
# The regression: silence must not be reported as a mishearing.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("attempt", [1, 2])
def test_silent_caller_is_not_told_we_missed_them(attempt):
    """THE INCIDENT: audio healthy, caller simply has not spoken."""
    prefix = _handler(lambda: ("flowing", float("inf")))._reask_prefix(attempt)
    assert "didn't catch" not in prefix
    assert "not hearing you" not in prefix
    assert prefix.strip(), "a re-ask must still say something"


def test_silent_caller_gets_a_presence_check():
    assert _handler(lambda: ("flowing", float("inf")))._reask_prefix(1) == (
        "Are you still there?"
    )


def test_long_silence_after_an_earlier_utterance_is_still_silence():
    """The caller spoke 30s ago; that is not something we just failed to catch."""
    prefix = _handler(lambda: ("flowing", 30.7))._reask_prefix(1)
    assert "didn't catch" not in prefix


# ---------------------------------------------------------------------------
# A genuine mishearing must still apologise. This is the half that keeps the
# apology honest rather than removing it.
# ---------------------------------------------------------------------------
def test_recent_voice_with_no_transcript_still_apologises():
    """Caller audibly spoke and STT produced nothing — we DID miss it."""
    assert _handler(lambda: ("flowing", 1.5))._reask_prefix(1) == _APOLOGY_1
    assert _handler(lambda: ("flowing", 1.5))._reask_prefix(2) == _APOLOGY_2


def test_voice_just_inside_the_recency_window_apologises():
    within = SilenceHandler._REASK_VOICE_RECENCY_SEC - 0.1
    assert _handler(lambda: ("flowing", within))._reask_prefix(1) == _APOLOGY_1


def test_voice_just_outside_the_recency_window_does_not():
    beyond = SilenceHandler._REASK_VOICE_RECENCY_SEC + 0.1
    assert _handler(lambda: ("flowing", beyond))._reask_prefix(1) != _APOLOGY_1


# ---------------------------------------------------------------------------
# A real fault should say so — and must not be softened into "are you there?",
# which would blame a silent caller for our broken leg.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("status", ["stalled", "never"])
@pytest.mark.parametrize("attempt", [1, 2])
def test_dead_inbound_leg_says_it_cannot_hear(status, attempt):
    prefix = _handler(lambda: (status, float("inf")))._reask_prefix(attempt)
    assert "still there" not in prefix.lower()
    assert "take your time" not in prefix.lower()
    assert "hear" in prefix.lower()


# ---------------------------------------------------------------------------
# Never let a diagnostic change the recovery ladder.
# ---------------------------------------------------------------------------
def test_no_probe_falls_back_to_original_wording():
    """Every existing test double constructs SilenceHandler without a probe."""
    assert _handler(None)._reask_prefix(1) == _APOLOGY_1
    assert _handler(None)._reask_prefix(2) == _APOLOGY_2


def test_probe_raising_falls_back_to_original_wording():
    def _boom():
        raise RuntimeError("probe exploded")

    assert _handler(_boom)._reask_prefix(1) == _APOLOGY_1
    assert _handler(_boom)._reask_prefix(2) == _APOLOGY_2


def test_probe_is_optional_on_the_constructor():
    """Older call sites must keep working without passing audio_probe."""
    import inspect

    sig = inspect.signature(SilenceHandler.__init__)
    assert sig.parameters["audio_probe"].default is None
