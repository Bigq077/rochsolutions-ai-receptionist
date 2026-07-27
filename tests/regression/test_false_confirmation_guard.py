# tests/regression/test_false_confirmation_guard.py
"""
P1 #5 / F-023 — false "all booked" (phantom booking).

CALL 5: caller said "can we book now" -> book_appointment was REJECTED
(confirmation_required, no calendar event) -> the model narrated "All booked"
anyway. The caller hung up believing they had an appointment the clinic has no
record of. CALL 12 hit the identical rejection and correctly asked "shall I book
that in?" and retried. So it is intermittent — the model's handling of the
rejection is non-deterministic.

The fix is deterministic, in three layers (backlog: "enforce in the
response/gate layer, not prompt"):

  Layer 1  book_appointment returning success:True sets session
           ["booking_write_confirmed"] — the "a real booking exists" signal that
           did not exist before. (_note_book_write_result)
  Layer 2  a blocked/failed book result gets an explicit caller_message_rule
           forbidding a success claim, so the model is steered too.
  Layer 3  sanitise_response Gate 5f: while booking_flow_active AND no booking
           has succeeded, a chunk that CLAIMS the booking is done is re-steered
           to the confirmation question (first hit) or dropped (subsequent).

The detector is deliberately tight — it fires on a completion CLAIM, never on an
offer/question/negation ("shall I book that in?", "I haven't booked anything",
"I'll book that in now"). It is measured against both classes below because the
over-fire failure mode is severe: Gate 5c once stripped a legitimate confirmation
and abandoned a completed booking (turn_handler.py, 2026-06-12).

SCOPE: booking phantoms only. Reschedule ("moved") is a separate phrase family
and flag — out of scope here.
"""
from __future__ import annotations

import pytest

from app.media_streams import turn_handler as th
from app.media_streams import llm_stream as ls


# ── The detector: must fire on completion claims ──────────────────────────
@pytest.mark.parametrize(
    "text",
    [
        "you're all booked for tuesday at ten",
        "that's all booked in for you",
        "you're booked in for monday",
        "great youre booked",
        "that's booked in",
        "all booked in for you then",
        "i've booked that in for you",
        "that's confirmed then",
        "your appointment is booked",
        "your booking is confirmed",
        "perfect youre all booked in",
        "ive got you booked in",
        "weve booked you in for thursday",
        # 2026-07-27 phantom: bare "all booked" with "you're" between it and "in"
        # — the read-back-flow shape that slipped Gate 5f on verify call CA77eebe.
        "all booked youre in for wednesday the 29th at quarter past six",
        "all booked",
    ],
)
def test_detector_fires_on_completion_claim(text):
    assert th._false_confirmation_claim(text) is True


# ── The detector: must NOT fire on offers/questions/negations/in-progress ──
@pytest.mark.parametrize(
    "text",
    [
        "shall i go ahead and book that in for you?",
        "would you like me to book that in?",
        "i haven't booked anything yet",
        "before i book that in can i take your number",
        "i can book that for you once i have your surname",
        "i'll book that in now",
        "let me just get that booked in for you",
        "i'm going to book that for you",
        "can i book you in for tuesday?",
        "do you want me to book that?",
        "i just need your phone number to book that in",
        "i cannot book that without a surname",
        "we have a slot at ten shall i book it?",
        "to book that in i'll need your date of birth",
        "i'll get you booked in once you confirm",
        "youre all set to give us a call anytime",
        "once thats sorted ill get you booked in",
        "well get that sorted for you when you come in",
        "ive got that noted down for you",
        "ive got your name and number now",
        "im just checking availability for you",
        "i havent been able to book that im afraid",
        # "all booked UP" = no availability, the opposite of a confirmation —
        # the bare-"all booked" pattern must exclude it.
        "were all booked up for today im afraid",
        "sorry were fully booked that afternoon",
    ],
)
def test_detector_silent_on_non_claims(text):
    assert th._false_confirmation_claim(text) is False


# ── Gate 5f behaviour in sanitise_response ────────────────────────────────
def _booking_session(**over):
    s = {"booking_flow_active": True, "_clinical_depth_cache": "", "v3_cta_count": 0}
    s.update(over)
    return s


def test_phantom_is_resteered_to_a_question():
    session = _booking_session()
    out = th.sanitise_response("You're all booked for Tuesday.", session)
    assert "?" in out, "must re-steer to a confirmation question"
    assert th._false_confirmation_claim(out) is False, "re-steer must not itself be a claim"
    assert session.get("_false_confirm_resteered") is True


def test_second_phantom_chunk_is_dropped():
    session = _booking_session(_false_confirm_resteered=True)
    out = th.sanitise_response("You're all booked in.", session)
    assert out == "", "a second claim in the same turn is dropped, not repeated"


def test_guard_off_once_a_real_booking_exists():
    # booking_write_confirmed set -> a genuine "you're booked" is legitimate.
    session = _booking_session(booking_write_confirmed=True)
    out = th.sanitise_response("You're all booked for Tuesday.", session)
    assert "booked" in out.lower(), "must not touch a legitimate confirmation"


def test_guard_off_when_not_in_booking_flow():
    session = {"_clinical_depth_cache": ""}  # no booking_flow_active
    out = th.sanitise_response("You're all booked for Tuesday.", session)
    assert "booked" in out.lower()


def test_legitimate_confirmation_question_passes_through():
    session = _booking_session()
    txt = "So that's Tom, Tuesday at ten — shall I go ahead and book that in?"
    out = th.sanitise_response(txt, session)
    assert "?" in out and out.strip() != ""


# ── Layer 1 + 2: _note_book_write_result ──────────────────────────────────
def test_success_sets_the_confirmed_flag():
    session = {}
    result = ls._note_book_write_result(session, "book_appointment", {"success": True, "booked_slot": "Tue 10am"})
    assert session.get("booking_write_confirmed") is True
    assert result.get("success") is True


def test_block_attaches_do_not_claim_rule_and_sets_no_flag():
    session = {}
    result = ls._note_book_write_result(
        session, "book_appointment", {"success": False, "status": "confirmation_required"}
    )
    assert "booking_write_confirmed" not in session
    assert "not" in (result.get("caller_message_rule") or "").lower()


def test_non_booking_tool_untouched():
    session = {}
    r_in = {"available_days": []}
    result = ls._note_book_write_result(session, "check_availability", r_in)
    assert result is r_in
    assert "booking_write_confirmed" not in session
