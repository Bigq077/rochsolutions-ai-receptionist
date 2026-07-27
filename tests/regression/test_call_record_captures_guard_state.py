# tests/regression/test_call_record_captures_guard_state.py
"""
Record whether Gate 5f caught a false booking claim — so "did the caller hear a
phantom?" is a query, not an argument.

2026-07-27, call CAa4942bcea4: the obs transcript ends with

    "All booked — you're in for Friday the 31st at five past five in the evening."

and the call has booking_confirmed=False and no calendar_event_id. Read at face
value that is F-023, a phantom booking, and it was reported as one.

It almost certainly was not. The obs transcript is built from `full_reply`
(llm_stream.py:619), which is assembled RAW (:1109) — while Gate 5f runs on the
TTS path only (:1497, :1524). So the transcript records what the model GENERATED,
not what the caller HEARD. The guard's own state — `_false_confirm_guard_fired`,
`_false_confirm_resteered` (turn_handler.py:639-643) — is the only evidence of
which it was, and it lived on the session and died there.

That ambiguity is expensive: it cost this session an evening, and it puts every
"All booked" quoted from a transcript in the findings docs in doubt — including
the 26 Jul verify call that reopened F-023, which may have been over-diagnosed
from the start.

With this, the question is answerable from the row:

    guards.false_confirm_fired > 0 AND NOT booking_confirmed
        -> the model tried to claim a booking, the guard caught it,
           the caller heard the re-steer. NOT a phantom.

    "All booked" in the transcript, guards.false_confirm_fired == 0
        -> the guard never matched. A REAL phantom. Escalate.

Write-only: reads two session keys at teardown and copies them into the record,
which obs stores wholesale as `raw`. It cannot influence a live call.
"""
from __future__ import annotations

from app.call_logger import CallLogger


def _record(session: dict) -> dict:
    session.setdefault("clinic_id", "jv_v1")
    return CallLogger("CAtest0000000000000000000000000002", session)._build_record()


def test_guard_state_is_captured_when_it_fired():
    """The CAa4942bcea4 shape: claims made, guard caught them, no booking."""
    sess = {
        "_false_confirm_guard_fired": 3,
        "_false_confirm_resteered": True,
        "booking_confirmed": False,
    }
    guards = _record(sess)["guards"]
    assert guards["false_confirm_fired"] == 3
    assert guards["false_confirm_resteered"] is True


def test_a_clean_call_records_zero_not_null():
    """Zero must be distinguishable from 'not captured'.

    A NULL here would read as "instrumentation didn't populate it" — the exact
    confusion that cost an hour on 2026-07-26 with booking_confirmed.
    """
    guards = _record({"booking_confirmed": True})["guards"]
    assert guards["false_confirm_fired"] == 0
    assert guards["false_confirm_resteered"] is False


def test_the_real_phantom_signature_is_distinguishable():
    """A genuine phantom is: booking claimed, guard silent, nothing booked.

    This is the row shape that must trigger escalation, and it must not look
    like the guarded case above.
    """
    guarded = _record({"_false_confirm_guard_fired": 2, "booking_confirmed": False})
    phantom = _record({"booking_confirmed": False})
    assert guarded["guards"]["false_confirm_fired"] > 0
    assert phantom["guards"]["false_confirm_fired"] == 0


def test_counter_is_coerced_to_int():
    """Session values are whatever wrote them; the record must be typed."""
    guards = _record({"_false_confirm_guard_fired": "2"})["guards"]
    assert guards["false_confirm_fired"] == 2


def test_a_corrupt_counter_does_not_break_teardown():
    """Teardown runs on every call. It must never raise on odd session state."""
    guards = _record({"_false_confirm_guard_fired": object()})["guards"]
    assert guards["false_confirm_fired"] == 0
