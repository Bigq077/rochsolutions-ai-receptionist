# tests/regression/test_b65_cancel_apologised_for_a_success.py
"""B-65 — JV CA44046f96321b, 2026-08-20, build a91f0c39b4a9.

The cancellation SUCCEEDED. `cancel_appointment` returned
`{"success": true, "cancelled_event": "… Martin Scobello", "was_at":
"2026-08-24T18:50:00+01:00"}`, the patient got a cancellation text and the owner
got an alert. Susie correctly said "That's all done."

Then she apologised for it — "I actually need to complete the cancellation
properly" — and the caller replied "i'm lost have you cancelled it then".

Two independent defects, one call:

  1. On the farewell turn the model re-issued cancel_appointment. It was refused
     for lack of consent, and that refusal's `message` said the write "cannot
     fire yet". `_note_write_result` attached the already-done rule BESIDE it,
     so the payload carried both "it is done" and "it has not happened". The
     model obeyed the message.

  2. Between the two attempts a fresh lookup_patient moved
     session["_lookup_appointment_id"] onto the caller's OTHER appointment.
     `_match_gcal_event` prefers that id over the `patient_name` in the args —
     which still named the already-cancelled patient — so had consent been
     given, the retry would have deleted an appointment nobody had discussed.
     Only the consent gate stopped it, and defect 1 is what drives a caller
     toward granting that consent.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.media_streams.llm_stream import _note_write_result
from app.media_streams.turn_handler import (
    CANCEL_SUCCEEDED_ID_KEY,
    WRITE_FAMILY_CANCEL,
)
from app.tools.receptionist_tools import _match_gcal_event

MARTIN_ID = "qldg1ta4t004vagln6982nc7os"   # cancelled at 09:38:57
QUENTIN_ID = "ujc6r2s02moaejeap5rvub9ktk"  # never discussed by the caller

# The verbatim refusal from the live call.
CONSENT_REFUSAL = {
    "status": "cancellation_confirmation_required",
    "message": (
        "cancel_appointment cannot fire yet. Ask for consent in the wording "
        "your instructions give you — either 'Shall I go ahead and cancel "
        "that?' or the retention question — and wait for a clear answer."
    ),
}


def _session_after_a_successful_cancel() -> dict:
    session: dict = {}
    _note_write_result(
        session,
        "cancel_appointment",
        {
            "success": True,
            "cancelled_event": "MSK Treatment Session for Marcus — Martin Scobello",
            "was_at": "2026-08-24T18:50:00+01:00",
            "cancelled_appointment_id": MARTIN_ID,
        },
    )
    return session


# ---------------------------------------------------------------------------
# Defect 1 — the payload must not say both things at once
# ---------------------------------------------------------------------------

def test_a_refused_duplicate_cancel_does_not_also_claim_it_never_happened():
    session = _session_after_a_successful_cancel()
    out = _note_write_result(session, "cancel_appointment", dict(CONSENT_REFUSAL))

    assert "message" not in out, (
        "the consent-refusal message survived alongside the already-done rule "
        "— this is the contradiction that made Susie apologise for a "
        "cancellation that had already gone through"
    )
    rule = out.get("caller_message_rule", "")
    assert "already completed successfully" in rule
    assert "Do not apologise" in rule


def test_the_status_survives_so_the_model_still_knows_this_attempt_wrote_nothing():
    session = _session_after_a_successful_cancel()
    out = _note_write_result(session, "cancel_appointment", dict(CONSENT_REFUSAL))
    assert out.get("status") == "cancellation_confirmation_required"


def test_a_first_refusal_with_no_prior_success_keeps_its_message():
    """The consent wording is correct when nothing has been cancelled yet.
    Stripping it there would leave the model with no idea what to ask for."""
    out = _note_write_result({}, "cancel_appointment", dict(CONSENT_REFUSAL))
    assert "cannot fire yet" in out.get("message", "")


def test_an_executors_own_explanation_is_not_overwritten_by_the_generic_rule():
    session = _session_after_a_successful_cancel()
    specific = "A cancellation already completed. Do not cancel anything else."
    out = _note_write_result(
        session,
        "cancel_appointment",
        {"success": False, "status": "cancel_target_changed",
         "caller_message_rule": specific},
    )
    assert out["caller_message_rule"] == specific


# ---------------------------------------------------------------------------
# Defect 2 — which appointment a second cancel would actually have hit
# ---------------------------------------------------------------------------

def test_the_lookup_id_beats_the_patient_name_in_the_args():
    """Pins the mechanism, so nobody 'fixes' this by trusting patient_name.

    This is why the retry was dangerous: the args named Martin, the session
    pointed at Quentin, and the id wins.
    """
    events = [
        {"id": QUENTIN_ID, "summary": "Initial Assessment — Quentin Rook"},
    ]
    args = {"patient_name": "Martin Scobello", "phone": "07502211207"}
    session = {"_lookup_appointment_id": QUENTIN_ID}
    assert _match_gcal_event(events, args, session)["id"] == QUENTIN_ID


def test_records_which_appointment_a_successful_cancel_removed():
    assert _session_after_a_successful_cancel()[CANCEL_SUCCEEDED_ID_KEY] == MARTIN_ID


def test_a_success_without_an_id_leaves_the_guard_disarmed():
    """Fails safe: an unknown target must not start refusing legitimate cancels."""
    session: dict = {}
    _note_write_result(
        session, "cancel_appointment",
        {"success": True, "cancelled_event": "x", "was_at": "y"},
    )
    assert CANCEL_SUCCEEDED_ID_KEY not in session


@pytest.mark.asyncio
async def test_a_second_cancel_aimed_at_a_different_appointment_is_refused(monkeypatch):
    """The live near-miss, end to end: consent granted, target moved."""
    import app.tools.receptionist_tools as rt

    async def _tokens(_cid):
        return {"token": "x"}

    monkeypatch.setattr(rt, "_get_tokens", _tokens)
    monkeypatch.setattr(
        rt, "_match_gcal_event",
        lambda events, args, session: {"id": QUENTIN_ID,
                                       "summary": "Initial Assessment — Quentin Rook"},
    )
    monkeypatch.setattr(
        "app.tools.calendar_google.list_upcoming_events",
        lambda *a, **k: [{"id": QUENTIN_ID}],
    )

    deleted: list = []
    monkeypatch.setattr(
        "app.tools.calendar_google.delete_event",
        lambda *a, **k: deleted.append(a),
    )

    session = _session_after_a_successful_cancel()
    session["clinic_id"] = "jv_v1"
    out = await rt._exec_cancel_appointment(
        {"patient_name": "Martin Scobello", "phone": "07502211207",
         "location": "bolton"},
        session,
    )

    assert out["success"] is False
    assert out["status"] == "cancel_target_changed"
    assert not deleted, (
        "a second cancel deleted a DIFFERENT appointment than the one already "
        "cancelled — this is the wrong-patient deletion the consent gate was "
        "the only thing preventing"
    )


@pytest.mark.asyncio
async def test_a_repeat_cancel_of_the_SAME_appointment_is_not_blocked_by_this_guard(
    monkeypatch,
):
    """The guard is about a moved target, not about retrying. A genuine repeat
    must still reach the executor's own handling."""
    import app.tools.receptionist_tools as rt

    async def _tokens(_cid):
        return {"token": "x"}

    monkeypatch.setattr(rt, "_get_tokens", _tokens)
    monkeypatch.setattr(
        rt, "_match_gcal_event",
        lambda events, args, session: {"id": MARTIN_ID, "summary": "… Martin Scobello"},
    )
    monkeypatch.setattr(
        "app.tools.calendar_google.list_upcoming_events",
        lambda *a, **k: [{"id": MARTIN_ID}],
    )

    session = _session_after_a_successful_cancel()
    session["clinic_id"] = "jv_v1"
    out = await rt._exec_cancel_appointment(
        {"patient_name": "Martin Scobello", "location": "bolton"}, session,
    )
    assert out.get("status") != "cancel_target_changed"
