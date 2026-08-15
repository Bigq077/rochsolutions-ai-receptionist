"""Job 3c.5 — 'time preference noted' is form-filling, not speech.

CAce1457d1: Susie said "That's a time preference noted — but could you tell
me what…". Strip the admin sentence; leave a real follow-up question.
"""
from __future__ import annotations

from app.media_streams.turn_handler import sanitise_response


def test_time_preference_noted_is_stripped():
    out = sanitise_response(
        "That's a time preference noted — but could you tell me what's going on?",
        {},
    )
    assert "preference noted" not in out.lower()
    assert "could you tell me" in out.lower()


def test_plain_evenings_ack_survives():
    """Natural echo of the caller's words is fine."""
    text = "Evenings — let me check what we have."
    assert sanitise_response(text, {}) == text


def test_prompt_no_longer_teaches_evenings_noted():
    from app.clinic_config import get_clinic
    from app.prompts.clinic_template_prompt import build_clinic_prompt

    body = build_clinic_prompt({}, get_clinic("jv_v1"))[0]
    assert "Evenings, noted" not in body
    assert "time preference noted" in body.lower()  # the ban is stated
