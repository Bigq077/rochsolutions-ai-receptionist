# tests/regression/test_provisional_90min_bookable.py
"""
Vital Edge abandoned 90-minute booking (2026-07-24 15:32, call
CAe3aaab38808a21026e37b870720e9e5f).

The caller asked for a 90-minute deep tissue massage. check_availability
returned a published slot (Sat 25th 10:00), but Susie replied "we don't have
any availability for a 90-minute … that's only a 60-minute session" and the
caller gave up.

Root cause: Vital Edge runs the google_calendar_provisional model, where a
published calendar event is a START-TIME marker only — the caller chooses 60 or
90 minutes and the practitioner confirms. Duration is NOT a property of the
slot (see the duplicate-start collapse in _check_availability_published). But
the model-facing availability payload exposed each slot's `end`, computed from
the published window (a 60-minute default). The LLM read that 60-minute window
as a fixed session length and refused the 90-minute request.

Fix #1 (this file, test one): _check_availability_published drops `end` from the
model-facing slots so the LLM cannot infer a session length from the published
window. Nothing downstream reads that `end` (_resolve_slot_iso keys on `start`,
_filter_same_day_slots on `date`), and session["last_offered_slots"] retains
start+end for internal slot resolution.

Fix #2 (test two): when the provisional booking flips the published slot to
PENDING, it now also patches the event's block to the booked duration
(start..end), so a 90-minute booking reads as 90 minutes on Jonathan's
calendar instead of keeping the published 60-minute block.
"""

from datetime import datetime, timedelta
from unittest.mock import patch, AsyncMock

import app.tools.receptionist_tools as rt
from app.tools.receptionist_tools import LONDON_TZ


def _published_event(start_dt, minutes=60):
    """A published availability event = one bookable start time."""
    end_dt = start_dt + timedelta(minutes=minutes)
    return {
        "id": "evt_" + start_dt.strftime("%Y%m%d%H%M"),
        "summary": "Available",
        "start": {"dateTime": start_dt.isoformat()},
        "end": {"dateTime": end_dt.isoformat()},
    }


async def test_published_slots_omit_end_from_model_payload():
    """The model must not see a per-slot `end` (a fixed 60-min window) — that is
    what made it refuse 90-minute bookings. Internal last_offered_slots keeps it.
    """
    start_dt = (datetime.now(LONDON_TZ) + timedelta(days=2)).replace(
        hour=10, minute=0, second=0, microsecond=0
    )
    events = [_published_event(start_dt, minutes=60)]  # a 60-minute window
    clinic = {
        "booking_system": "google_calendar_provisional",
        "days_ahead": 14,
        "slot_minutes": 60,
    }
    session = {}

    with patch.object(rt, "_get_tokens", AsyncMock(return_value={"access_token": "x"})), \
         patch.object(rt, "_save_gcal_tokens", AsyncMock(return_value=None)), \
         patch("app.tools.calendar_google.list_upcoming_events", return_value=events):
        result = await rt._check_availability_published(
            {"location": "", "date_hint": ""}, session, clinic
        )

    days = result.get("available_days") or []
    assert days, f"expected the published slot to be offered, got {result!r}"
    model_slots = days[0]["slots"]
    assert model_slots, "expected at least one model-facing slot"
    # The defect: exposing `end` let the LLM read a fixed 60-min session length.
    assert all("end" not in s for s in model_slots), (
        f"model-facing slots must not carry `end` (start-time markers only): {model_slots}"
    )
    assert all("start" in s for s in model_slots)
    # Booking resolution still has the full window internally.
    assert session.get("last_offered_slots"), "internal slot list must be retained"
    assert all("end" in s for s in session["last_offered_slots"]), (
        "last_offered_slots must keep start+end for _resolve_slot_iso"
    )


async def test_provisional_flip_patches_block_to_booked_duration():
    """A 90-minute booking must set the flipped event's block to 90 minutes, not
    leave the published 60-minute window."""
    start_dt = (datetime.now(LONDON_TZ) + timedelta(days=2)).replace(
        hour=10, minute=0, second=0, microsecond=0
    )
    start_iso = start_dt.isoformat()
    src_event_id = "evt_published_123"

    clinic = {
        "booking_system": "google_calendar_provisional",
        "slot_minutes": 60,
        "services": [
            {
                "service_id": "deep_tissue_massage",
                "name": "Deep Tissue Massage",
                "typical_duration_minutes_options": [60, 90],
            }
        ],
    }
    session = {
        "_provisional_slot_events": {start_iso: src_event_id},
        "last_offered_slots": [{"start": start_iso, "end": (start_dt + timedelta(minutes=60)).isoformat()}],
        "collected": {},
    }
    args = {
        "patient_name": "Jane Caller",
        "phone": "07502211207",
        "service": "deep_tissue_massage",
        "slot_iso": start_iso,
        "duration_minutes": 90,  # caller chose the 90-minute session
    }

    patch_time_mock = AsyncMock(return_value={"id": src_event_id})

    with patch.object(rt, "_get_tokens", AsyncMock(return_value={"access_token": "x"})), \
         patch.object(rt, "_save_gcal_tokens", AsyncMock(return_value=None)), \
         patch("app.tools.calendar_google.update_event", return_value={"id": src_event_id}), \
         patch("app.tools.calendar_google.patch_event_time") as pet, \
         patch("app.tools.calendar_google.create_event", return_value={"id": "new"}), \
         patch("app.tools.handoff.send_to_sheet", return_value=None), \
         patch("app.notifications.owner_notify.notify_owner", AsyncMock(return_value=None)), \
         patch("app.notifications.sms.send_sms", AsyncMock(return_value=None)):
        result = await rt._book_appointment_provisional(args, session, clinic)

    assert result.get("success") is True, f"booking should succeed: {result!r}"
    assert pet.called, "patch_event_time must be called to set the block length"
    # Positional args: (tokens, event_id, start_dt, end_dt, calendar_id)
    call_args = pet.call_args.args
    passed_start = call_args[2]
    passed_end = call_args[3]
    booked_minutes = (passed_end - passed_start).total_seconds() / 60
    assert booked_minutes == 90, (
        f"flipped block must be 90 minutes, got {booked_minutes}"
    )
