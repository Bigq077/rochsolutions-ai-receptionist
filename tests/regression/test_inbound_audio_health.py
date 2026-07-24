# tests/regression/test_inbound_audio_health.py
"""P1 (2026-07-24) — a dead inbound audio leg was indistinguishable from a quiet caller.

Incident
--------
jv_v1 call at 22:37. The caller spoke three times over the final 48 seconds and
Susie never reacted: two re-asks, then a graceful close with outcome=no_audio.
The log shows why nothing was diagnosable — for that whole window there was not
one `barge-in: partial=` line and not one FINAL.

Two INDEPENDENT detectors saw nothing:
  * AssemblyAI produced zero partials;
  * the energy VAD in _handle_media, which reads raw mu-law bytes and never
    touches AssemblyAI, never fired (it was armed: _clearing was False and the
    silence timer was running, which is why WATCHDOG_FIRE ran its full 10s).

Two unrelated detectors cannot both miss audible speech, so the bytes never
arrived. The inbound leg was down while the outbound leg kept working — the
caller heard every prompt.

Why it was invisible
--------------------
Nothing in the system could tell "caller is silent" from "we are deaf":
  * `_last_audio_received_at` was written in _handle_media and read NOWHERE;
  * `SilenceHandler.on_audio_received()` was a literal `pass`;
  * the STT send loop substitutes synthetic silence when its queue runs dry
    (stt_stream.py KEEPALIVE_BYTES), so AssemblyAI never errors and no
    reconnect is logged.
Both cases therefore produced the identical ladder: re-ask, re-ask, close,
outcome=no_audio.

Fix
---
`_inbound_audio_status()` classifies the leg from Twilio media frames, which
arrive every ~20ms for the whole call including silence. The 10s dead-air net
logs the verdict on every fire and no longer pretends a fault is a quiet
caller; the outcome splits to `no_inbound_audio` so these are countable.
"""

import pytest

from app.media_streams import connection as conn_mod
from app.tools.call_summary import infer_call_outcome


class _FakeHandler:
    """Minimal stand-in exposing only what _inbound_audio_status touches.

    Binding the real unbound methods keeps this honest — if the production
    signature or field names change, this fails rather than drifting.
    """

    _MEDIA_STALL_SEC = conn_mod.WebSocketCallHandler._MEDIA_STALL_SEC
    _inbound_audio_status = conn_mod.WebSocketCallHandler._inbound_audio_status
    _note_utterance_lost = conn_mod.WebSocketCallHandler._note_utterance_lost

    def __init__(self):
        self._media_frames_in = 0
        self._last_audio_received_at = 0.0
        self._last_voiced_audio_at = 0.0
        self.session = {}


@pytest.fixture
def handler(monkeypatch):
    h = _FakeHandler()
    monkeypatch.setattr(conn_mod.time, "monotonic", lambda: 1000.0)
    return h


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------
def test_no_frames_ever_is_never(handler):
    """Stream opened, Twilio never sent a media frame."""
    assert handler._inbound_audio_status()[0] == "never"


def test_frames_then_silence_is_stalled(handler):
    """THE INCIDENT: frames were arriving, then stopped."""
    handler._media_frames_in = 5000
    handler._last_audio_received_at = 1000.0 - 48.0   # 48s ago, as observed
    handler._last_voiced_audio_at = 1000.0 - 60.0
    status, gap = handler._inbound_audio_status()
    assert status == "stalled"
    assert gap == pytest.approx(48.0)


def test_frames_arriving_but_never_voiced_is_silent(handler):
    """A caller who simply says nothing — the ladder is CORRECT here."""
    handler._media_frames_in = 5000
    handler._last_audio_received_at = 1000.0 - 0.02   # last frame 20ms ago
    handler._last_voiced_audio_at = 0.0
    assert handler._inbound_audio_status()[0] == "silent"


def test_healthy_call_is_flowing(handler):
    handler._media_frames_in = 5000
    handler._last_audio_received_at = 1000.0 - 0.02
    handler._last_voiced_audio_at = 1000.0 - 3.0
    assert handler._inbound_audio_status()[0] == "flowing"


def test_normal_jitter_is_not_a_stall(handler):
    """Frames are ~20ms apart; a few late ones must not read as a fault.

    False "stalled" would be worse than the bug: it would tell the owner the
    line is broken on ordinary calls.
    """
    handler._media_frames_in = 5000
    handler._last_voiced_audio_at = 1000.0 - 3.0
    for lateness in (0.02, 0.1, 0.5, 1.0, 1.4):
        handler._last_audio_received_at = 1000.0 - lateness
        assert handler._inbound_audio_status()[0] == "flowing", (
            f"{lateness}s between frames misread as a stall"
        )


def test_stall_threshold_is_far_outside_jitter():
    """~75 missed 20ms frames before we call it a fault."""
    assert conn_mod.WebSocketCallHandler._MEDIA_STALL_SEC >= 1.0


# ---------------------------------------------------------------------------
# Outcome split
# ---------------------------------------------------------------------------
def test_quiet_caller_still_reports_no_audio():
    assert infer_call_outcome({"no_audio_close": True}, {}) == "no_audio"


def test_dead_inbound_leg_reports_no_inbound_audio():
    outcome = infer_call_outcome(
        {"no_audio_close": True, "inbound_audio_fault": "stalled"}, {}
    )
    assert outcome == "no_inbound_audio", (
        "a fault on the inbound leg must be countable separately from a "
        "caller who chose not to speak — they need opposite responses"
    )


def test_new_outcome_still_routes_to_the_owner_sms():
    """A new outcome must not fall through the SMS router and go silent.

    These are precisely the calls the owner needs to hear about.
    """
    import inspect

    from app.notifications import smart_sms_router

    src = inspect.getsource(smart_sms_router.build_sms_body) \
        if hasattr(smart_sms_router, "build_sms_body") \
        else inspect.getsource(smart_sms_router)
    assert "no_inbound_audio" in src, (
        "smart_sms_router does not mention no_inbound_audio — the owner alert "
        "is silently skipped for inbound-audio faults"
    )


# ---------------------------------------------------------------------------
# Utterance-loss accounting
# ---------------------------------------------------------------------------
def test_lost_utterances_are_tallied_by_reason(handler, caplog):
    with caplog.at_level("WARNING"):
        handler._note_utterance_lost("same_breath_straggler", "warm", "745ms early")
        handler._note_utterance_lost("same_breath_straggler", "get")
        handler._note_utterance_lost("single_char_word", "r clinic")

    assert handler.session["utterances_lost"] == {
        "same_breath_straggler": 2,
        "single_char_word": 1,
    }
    assert "[ms_lost]" in caplog.text
    assert "call_total=3" in caplog.text


def test_accounting_never_breaks_a_call(caplog):
    """Diagnostics must not be able to take down a live call."""
    h = _FakeHandler()
    h.session = None  # session not ready / torn down
    with caplog.at_level("WARNING"):
        h._note_utterance_lost("stale_pre_barge_in", "hello")  # must not raise
    assert "[ms_lost]" in caplog.text


def test_every_drop_site_reports_a_reason():
    """Each guard that discards a transcript must call _note_utterance_lost.

    The point of this change is that no drop path stays invisible. A new
    `continue` added without accounting recreates the original problem.
    """
    import inspect

    src = inspect.getsource(conn_mod)
    for marker in (
        "stale transcript discarded (pre-barge-in)",
        "C8-2 transcript dropped",
        "same-breath straggler dropped",
        "discarding noise utterance",
        "reason=single_char_word",
    ):
        assert marker in src, f"drop site vanished: {marker!r}"

    # Reasons actually wired up, one per guard family.
    for reason in (
        "stale_pre_barge_in",
        "location_ack_race",
        "same_breath_straggler",
        "name_clarification_in_flight",
        "single_char_word",
    ):
        assert f'"{reason}"' in src, f"drop site not accounted: {reason}"
