"""Job 3a — booking-ack injects reason before timing for opted-in clinics.

CAce1457d1: after bare "Right —", the engine always queued
"Is there a particular day or time…", ignoring prompt_facts.reason_question
and BOOKING STEPS 1b. jv_v1 opted in; the injector did not.
"""
from __future__ import annotations

from app.media_streams.connection import (
    _TIMING_QUESTION_AFTER_BOOKING_ACK,
    _next_question_after_booking_ack,
    _reason_already_known,
)


def test_jv_gets_reason_question_first():
    session = {"clinic_id": "jv_v1"}
    q = _next_question_after_booking_ack(session)
    assert "appointment for" in q.lower()
    assert q != _TIMING_QUESTION_AFTER_BOOKING_ACK


def test_theorem_still_gets_timing():
    """theorem_v3 does not opt into reason_question — timing stays first."""
    session = {"clinic_id": "theorem_v3"}
    assert _next_question_after_booking_ack(session) == _TIMING_QUESTION_AFTER_BOOKING_ACK


def test_reason_already_known_skips_to_timing():
    session = {"clinic_id": "jv_v1", "reason": "shoulder"}
    assert _reason_already_known(session) is True
    assert _next_question_after_booking_ack(session) == _TIMING_QUESTION_AFTER_BOOKING_ACK


def test_already_asked_reason_skips_to_timing():
    session = {"clinic_id": "jv_v1", "_reason_question_asked": True}
    assert _next_question_after_booking_ack(session) == _TIMING_QUESTION_AFTER_BOOKING_ACK


def test_collected_reason_counts_as_known():
    session = {"clinic_id": "jv_v1", "collected": {"reason": "knee"}}
    assert _next_question_after_booking_ack(session) == _TIMING_QUESTION_AFTER_BOOKING_ACK
