"""Day-only hint must arm the hold clip (Job 3c.4 / CAce1457d1).

Caller says "what'll you got friday" with no morning/afternoon word.
`time_of_day_preference` stays unset, and `v3_last_presented_date_hint` only
exists AFTER a presentation — so the first lookup left
`expect_slot_presentation` False and the clip holstered through ~3s of silence.

Confirmed engine-general (Theorem too). Capture the day before the arm check.
"""
from __future__ import annotations

from app.media_streams.connection import _extract_day_preference
from app.media_streams.filler_guard import expect_slot_presentation


def test_extract_day_preference_hears_friday_in_a_sentence():
    assert _extract_day_preference("what'll you got friday") == "friday"


def test_extract_day_preference_hears_bare_weekday():
    assert _extract_day_preference("monday") == "monday"


def test_extract_day_preference_hears_relative_days():
    assert _extract_day_preference("tomorrow please") == "tomorrow"
    assert _extract_day_preference("next week anytime") == "next week"


def test_extract_day_preference_ignores_no_day():
    assert _extract_day_preference("it's me shoulder") is None
    assert _extract_day_preference("hiya, can i book something") is None


def test_day_preference_alone_is_enough_to_arm_the_clip():
    """Mirror the connection.py arm site: day_preference counts as known timing."""
    session = {"day_preference": "friday"}
    timing_known = bool(
        session.get("time_of_day_preference")
        or session.get("day_preference")
        or session.get("v3_last_presented_date_hint")
    )
    assert timing_known is True
    assert expect_slot_presentation(
        timing_preference_known=timing_known,
        slots_already_presented=False,
        slot_map_active=False,
        name_collection_pending=False,
        phone_collection_active=False,
        location_question_active=False,
    ) is True


def test_without_day_or_tod_the_clip_stays_holstered():
    session = {}
    timing_known = bool(
        session.get("time_of_day_preference")
        or session.get("day_preference")
        or session.get("v3_last_presented_date_hint")
    )
    assert timing_known is False
    assert expect_slot_presentation(
        timing_preference_known=timing_known,
        slots_already_presented=False,
        slot_map_active=False,
        name_collection_pending=False,
        phone_collection_active=False,
        location_question_active=False,
    ) is False
