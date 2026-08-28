"""The service the caller was shown slots for is the service that gets booked.

THE DEFECT (found 2026-08-28, reproduced 3/3 against the live prompt+model)

    caller: "Hi, I'd like to book a sports massage please"
    tool:   check_availability(service='sports_massage')   <- correct
    diary:  PENDING CONFIRMATION - Daniel Okafor - Deep Tissue Massage (60 min)

Susie never says the service aloud, so the caller cannot catch it and neither
can the read-back - it names only the day and the time. Only the practitioner
finds out, from the calendar. Same can't-hear-it shape as the wrong-surname
family, and the same remedy as the duration defect fixed in 6d7d1b2c: hold the
caller's choice in the ENGINE rather than re-deriving it from the model.

TWO INDEPENDENT GAPS, both required for it to bite, both pinned below.

1. `session["_checked_service"]` was written inside the Google-Calendar body of
   `_exec_check_availability`. Theorem returns to the Acuity reader, and Vital
   Edge returns to the diary/published readers, both ABOVE that line - so on
   two of the three live clinics the pin was simply never written.

2. `_exec_book_appointment` has carried a service reconciliation since
   2026-07-08, but it returns to `_book_appointment_provisional` long before
   reaching it, so Vital Edge never ran that either - and the provisional
   booker took `args["service"]` verbatim, with a hardcoded
   "Deep Tissue Massage" fallback.

A fix to either one alone leaves the defect live, which is why both are pinned
here. These tests are deterministic: no model, no calendar, no network.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from app.media_streams.session import _fresh_session
from app.tools import receptionist_tools as rt

CLINIC_ID = "vital_edge"


def _session() -> dict:
    s = _fresh_session()
    s["clinic_id"] = CLINIC_ID
    s["call_sid"] = "CAtest"
    # Past the duration gate: the caller has already chosen 60 minutes.
    s["_service_duration_choice"] = 60
    s["selected_location"] = "kingston"
    s["location_confirmed"] = True
    s["confirmed_location"] = "kingston"
    return s


# ---------------------------------------------------------------------------
# Gap 1 - the pin must be written on the clinic's OWN reader, not just Google's
# ---------------------------------------------------------------------------

async def test_check_availability_pins_the_service_for_a_diary_clinic():
    """Vital Edge returns to the diary reader, above where the pin used to be.

    The dispatch target is stubbed deliberately: the point is that the pin is
    set BEFORE any reader runs, so it cannot depend on which arm is taken or
    on the calendar being reachable.
    """
    session = _session()

    async def _fake_reader(args, sess, clinic):
        return {"available_days": [], "total_days": 0}

    with patch.object(rt, "_check_availability_diary", _fake_reader), \
         patch.object(rt, "_check_availability_published", _fake_reader):
        await rt._exec_check_availability(
            {"service": "sports_massage", "location": "kingston",
             "duration_minutes": 60},
            session,
        )

    assert session.get("_checked_service") == "sports_massage", (
        "check_availability did not pin the service for a diary clinic; "
        "book_appointment has nothing to reconcile against"
    )


async def test_the_pin_survives_a_reader_that_finds_nothing():
    """A no-availability answer must still pin, or the next lookup loses it."""
    session = _session()

    async def _empty(args, sess, clinic):
        return {"error": "no_availability", "slots": [],
                "available_days": [], "total_days": 0}

    with patch.object(rt, "_check_availability_diary", _empty), \
         patch.object(rt, "_check_availability_published", _empty):
        await rt._exec_check_availability(
            {"service": "sports_massage", "location": "kingston",
             "duration_minutes": 60},
            session,
        )

    assert session.get("_checked_service") == "sports_massage"


# ---------------------------------------------------------------------------
# Gap 2 - the provisional booker must honour the pin
# ---------------------------------------------------------------------------

async def _book(session: dict, service_arg: str) -> dict:
    """Run the real provisional booker with only its I/O faked."""
    import app.notifications.owner_notify as owner_notify
    import app.tools.calendar_google as gcal
    from app.clinic_config import get_clinic

    written: dict = {}

    def _create_event(tokens, start_dt, end_dt, summary, description="",
                      calendar_id=None, visibility="default"):
        written["summary"] = summary
        written["description"] = description
        return {"id": "evt-1"}

    async def _tokens(*a, **kw):
        return {"token": "fake", "migrated": True}

    async def _notify(clinic, message):
        return True

    slot = (datetime.now(rt.LONDON_TZ) + timedelta(days=3)).replace(
        hour=14, minute=0, second=0, microsecond=0)

    with patch.object(gcal, "create_event", _create_event), \
         patch.object(gcal, "update_event", lambda *a, **k: {"id": "evt-1"}), \
         patch.object(gcal, "patch_event_time", lambda *a, **k: {"id": "evt-1"}), \
         patch.object(rt, "_get_tokens", _tokens), \
         patch.object(owner_notify, "notify_owner", _notify):
        result = await rt._book_appointment_provisional(
            {
                "patient_name": "Daniel Okafor",
                "phone": "07700900123",
                "service": service_arg,
                "slot_iso": slot.isoformat(),
                "duration_minutes": 60,
                "location": "kingston",
            },
            session,
            get_clinic(CLINIC_ID),
        )
    return {"result": result, "written": written}


async def test_the_provisional_booker_books_the_service_that_was_shown():
    """The live defect, end to end on the booking path."""
    session = _session()
    session["_checked_service"] = "sports_massage"

    out = await _book(session, service_arg="deep_tissue_massage")

    assert out["result"].get("success"), out["result"]
    assert (session.get("collected") or {}).get("service") == "sports_massage", (
        "the diary recorded the model's service, not the one the caller was "
        "shown slots for"
    )
    assert "Sports Massage" in out["written"]["summary"], out["written"]["summary"]
    assert "Deep Tissue" not in out["written"]["summary"], out["written"]["summary"]


async def test_an_unpinned_call_still_books_what_the_model_asked_for():
    """No pin means no opinion — the model's argument must pass through.

    Without this, the reconciliation could quietly become "always book the last
    thing anyone checked", which would be a worse bug than the one it fixes.
    """
    session = _session()
    session.pop("_checked_service", None)

    out = await _book(session, service_arg="deep_tissue_massage")

    assert out["result"].get("success"), out["result"]
    assert (session.get("collected") or {}).get("service") == "deep_tissue_massage"


async def test_a_pinned_service_that_the_clinic_does_not_offer_is_ignored():
    """Never substitute something unbookable — leave the argument alone."""
    session = _session()
    session["_checked_service"] = "not_a_real_service"

    out = await _book(session, service_arg="deep_tissue_massage")

    assert out["result"].get("success"), out["result"]
    assert (session.get("collected") or {}).get("service") == "deep_tissue_massage"


# ---------------------------------------------------------------------------
# The trade-off this fix makes, pinned deliberately
# ---------------------------------------------------------------------------

async def test_a_caller_who_changes_service_re_pins_on_the_next_lookup():
    """The pin must FOLLOW the caller, or the fix becomes its own defect.

    F-021's registered reproduction is a pivot - "how much is a sports
    massage?" then "actually, can I book a regular assessment?". If the pin
    were sticky, reconciliation would drag the booking back to the service the
    caller ABANDONED, which is worse than the bug it replaces.

    It is not sticky: check_availability re-pins on every call, and a pivot
    forces a fresh lookup because the new service has its own duration and its
    own slot grid. This test states that contract so nobody later "optimises"
    the pin into a write-once latch.

    The residual, accepted knowingly and matching what the Google-Calendar path
    has done since 2026-07-08: a caller who pivots and is booked with NO fresh
    lookup keeps the old pin. That booking is already incoherent - the slot was
    generated for the previous service's length - so preferring the checked
    service is the safer of two wrong answers.
    """
    session = _session()

    async def _reader(args, sess, clinic=None):
        return {"available_days": [], "total_days": 0}

    with patch.object(rt, "_check_availability_diary", _reader), \
         patch.object(rt, "_check_availability_published", _reader):
        await rt._exec_check_availability(
            {"service": "sports_massage", "location": "kingston",
             "duration_minutes": 60}, session)
        assert session["_checked_service"] == "sports_massage"

        # The caller changes their mind; the model looks again.
        await rt._exec_check_availability(
            {"service": "deep_tissue_massage", "location": "kingston",
             "duration_minutes": 60}, session)

    assert session["_checked_service"] == "deep_tissue_massage"

    out = await _book(session, service_arg="deep_tissue_massage")
    assert (session.get("collected") or {}).get("service") == "deep_tissue_massage"
    assert "Deep Tissue" in out["written"]["summary"]
