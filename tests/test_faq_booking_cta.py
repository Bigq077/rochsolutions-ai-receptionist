"""
tests/test_faq_booking_cta.py
-----------------------------
F13 — booking-CTA must NOT be tacked onto a pure informational FAQ answer.

Sweep finding F13 (docs/sweep_findings.md): on plain price / parking / hours
questions Susie appended "Would you like to book an appointment?" — a booking
push the sweep marks as a FAIL on Call 4 (price) and Call 5 (parking).

Gate 5c in app/media_streams/turn_handler.sanitise_response previously stripped
that redundant CTA ONLY when ``booking_flow_active`` was True (mid-booking). On a
pure-FAQ turn BOTH ``booking_flow_active`` and ``v3_treatment_mentioned`` are
absent (see connection.py:7681-7699), so the CTA sailed through.

Correct behaviour:
  - pure informational FAQ (neither flag) → strip the trailing CTA.
  - concern turn (``v3_treatment_mentioned`` True) → KEEP the CTA — the
    assessment offer is the desired close (Call 9/12/14 passed with it).
  - standalone booking question (the CTA IS the whole response) → KEEP it
    (whole-response guard).
  - mid-booking (``booking_flow_active`` True) → strip redundant tail (existing).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.media_streams.turn_handler import sanitise_response


# ---------------------------------------------------------------------------
# F13 core: pure informational FAQ answers must lose the trailing CTA
# ---------------------------------------------------------------------------

def test_faq_price_answer_strips_trailing_cta() -> None:
    session: dict = {}  # neither booking_flow_active nor v3_treatment_mentioned
    text = (
        "It's £85 for a fifty-minute assessment. "
        "Would you like to book an appointment?"
    )
    out = sanitise_response(text, session)
    assert "would you like to book" not in out.lower(), out
    assert "£85" in out


def test_faq_parking_answer_strips_trailing_cta() -> None:
    session: dict = {}
    text = (
        "There's free parking with around eighty spaces at the Greig Centre. "
        "Would you like to book an appointment?"
    )
    out = sanitise_response(text, session)
    assert "would you like to book" not in out.lower(), out
    assert "parking" in out.lower()


# ---------------------------------------------------------------------------
# Guards: the CTA MUST survive where it is legitimate
# ---------------------------------------------------------------------------

def test_concern_turn_keeps_booking_cta() -> None:
    """v3_treatment_mentioned → the assessment offer is the desired close."""
    session = {"v3_treatment_mentioned": True}
    text = (
        "I'm sorry to hear that — back pain can be really draining. "
        "Physiotherapy is well-suited to that kind of problem. "
        "Would you like to book an assessment with Mark?"
    )
    out = sanitise_response(text, session)
    assert "book an assessment" in out.lower(), out


def test_standalone_booking_question_kept() -> None:
    """The CTA is the whole response → whole-response guard keeps it."""
    session: dict = {}
    text = "Would you like to book an appointment?"
    out = sanitise_response(text, session)
    assert "would you like to book" in out.lower(), out


def test_booking_flow_active_still_strips_redundant() -> None:
    """Existing mid-booking behaviour must be unchanged."""
    session = {"booking_flow_active": True}
    text = (
        "There's free parking at the Greig Centre. "
        "Would you like to book an appointment?"
    )
    out = sanitise_response(text, session)
    assert "would you like to book" not in out.lower(), out
    assert "parking" in out.lower()
