# tests/regression/test_call_record_captures_service.py
"""
The durable call record must say WHICH SERVICE was booked.

F-021 — book_appointment books a different service than the one
check_availability was called with — is reproducible 4/4 (campaign calls 4, 7,
11, 14) and is still open. It is the worst failure class in this system: the call
sounds perfect, a real calendar event exists, and the clinic has the wrong
appointment type and duration.

Yet on 2026-07-27 it was **unauditable**. Eight UK calls, two real bookings, and
`collected` on every row held exactly:

    {"name": …, "phone": …, "reason": …, "chosen_day": null, "selected_slot": …}

No service. So "did she book what the caller asked for?" could not be answered
from the record at all — only by listening back.

Every booking path already writes `session["collected"]["service"]`
(receptionist_tools.py:2528 / 4243 / 4681 / 4808), and check_availability already
pins `session["_checked_service"]` (:3765) precisely so booking uses the same
service. Both existed; neither reached the record. `_build_record` copied a fixed
five keys and dropped the rest.

This is a WRITE-ONLY change to the teardown record: it adds keys, reads session
state that is already populated, and cannot influence a live call. Capturing both
`service` and `checked_service` makes F-021 a query — a booked row where the two
disagree IS the defect — instead of a listen-back.
"""
from __future__ import annotations

from app.call_logger import CallLogger


def _record(session: dict) -> dict:
    session.setdefault("clinic_id", "jv_v1")
    return CallLogger("CAtest0000000000000000000000000001", session)._build_record()


def test_booked_service_is_captured():
    """The service book_appointment actually used reaches the record."""
    sess = {
        "collected": {
            "name": "Quentin Rock",
            "phone": "07502211207",
            "service": "sports_massage",
            "slot": "2026-08-05T19:00:00",
        },
        "booking_confirmed": True,
    }
    got = _record(sess)["collected"]
    assert got["service"] == "sports_massage"


def test_checked_service_is_captured_so_f021_is_a_query():
    """A booked row where checked != booked IS F-021, visible without audio."""
    sess = {
        "_checked_service": "msk_initial_assessment",
        "collected": {
            "name": "Quentin Rock",
            "phone": "07502211207",
            "service": "acupuncture",          # ← the F-021 shape (campaign CALL 7)
            "slot": "2026-08-05T19:00:00",
        },
        "booking_confirmed": True,
    }
    got = _record(sess)["collected"]
    assert got["checked_service"] == "msk_initial_assessment"
    assert got["service"] == "acupuncture"
    assert got["service"] != got["checked_service"], (
        "this fixture is the F-021 signature and must be detectable from the "
        "record alone"
    )


def test_booking_location_is_captured():
    """Modality (bolton / remote / home_visit) drives price and duration."""
    sess = {"collected": {"service": "msk_initial_assessment", "location": "home_visit"}}
    assert _record(sess)["collected"]["location"] == "home_visit"


def test_absent_keys_are_none_not_missing():
    """A call that never booked must still produce the full shape.

    Downstream reads `collected["service"]` directly; a missing key would raise
    where a None reads correctly as "no service was booked".
    """
    got = _record({})["collected"]
    for key in ("name", "phone", "reason", "chosen_day", "selected_slot",
                "service", "checked_service", "location"):
        assert key in got, f"{key} missing from the record shape"
        assert got[key] is None


def test_existing_five_keys_are_unchanged():
    """Additive only — the pre-existing contract must not move.

    tests/capture/test_store.py asserts collected["chosen_day"]; the SMS and
    summary paths read name/phone/selected_slot.
    """
    sess = {
        "collected": {
            "name": "Tom Green",
            "phone": "07700900123",
            "reason": "shoulder pain",
            "chosen_day": "Monday",
            "selected_slot": "2026-08-03T17:15:00",
        },
    }
    got = _record(sess)["collected"]
    assert got["name"] == "Tom Green"
    assert got["phone"] == "07700900123"
    assert got["reason"] == "shoulder pain"
    assert got["chosen_day"] == "Monday"
    assert got["selected_slot"] == "2026-08-03T17:15:00"


def test_session_is_not_mutated_by_building_the_record():
    """Teardown capture must never write back into the live session."""
    sess = {"collected": {"service": "acupuncture"}, "_checked_service": "acupuncture"}
    before = {"collected": dict(sess["collected"]), "_checked_service": sess["_checked_service"]}
    _record(sess)
    assert sess["collected"] == before["collected"]
    assert sess["_checked_service"] == before["_checked_service"]
