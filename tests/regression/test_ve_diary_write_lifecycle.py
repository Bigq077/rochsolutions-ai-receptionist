"""Vital Edge: the booking WRITE lifecycle under availability_mode "diary".

The provisional lifecycle was built for a calendar of PUBLISHED slots, where a
booking consumes an existing event, a cancellation hands it back, and a
reschedule swaps one for another. Under "diary" the calendar means the opposite
— every event BLOCKS — so each of those writes has to be re-read:

  book       there is no published event to flip → CREATE one in the gap
  cancel     handing the slot "back" leaves an event behind, and an event blocks,
             so a cancellation would make the time permanently UNBOOKABLE → DELETE
  reschedule no slot to consume → MOVE the existing event

Only cancel needed changing. Book and reschedule already take the right branch,
but they do so because `_provisional_slot_events` happens to be empty under
diary mode — correct by accident, not by intent. These tests pin all three, so
a future change that populates that map cannot silently re-break them.
"""

import asyncio
from datetime import datetime, timedelta

import pytest

from app.tools import receptionist_tools as rt
from app.tools.receptionist_tools import LONDON_TZ


def _clinic(mode="diary"):
    return {
        "clinic_id": "vital_edge",
        "practitioner": "Jonathan",
        "display_name": "Vital Edge Therapy",
        "sms_name": "Vital Edge Therapy",
        "booking_system": "google_calendar_provisional",
        "availability_mode": mode,
        "calendar_id": "vitaledgetherapy@gmail.com",
        "slot_minutes": 60,
        "primary_location": "kingston",
    }


class Calls(dict):
    """Records which calendar writes happened."""


@pytest.fixture
def cal(monkeypatch):
    calls = Calls(created=[], updated=[], deleted=[], patched=[])

    async def _tok():
        return {"access_token": "x"}

    async def _save(*a, **k):
        return None

    def _create(tokens, start, end, summary, description, calendar_id, vis="default"):
        calls["created"].append({"start": start, "end": end, "summary": summary})
        return {"id": "new-event-1"}

    def _update(tokens, event_id, summary, description, calendar_id):
        calls["updated"].append({"id": event_id, "summary": summary})
        return {"id": event_id}

    def _delete(tokens, event_id, calendar_id):
        calls["deleted"].append(event_id)
        return True

    def _patch_time(tokens, event_id, start, end, calendar_id):
        calls["patched"].append({"id": event_id, "start": start})
        return {"id": event_id}

    monkeypatch.setattr(rt, "_get_tokens", _tok)
    monkeypatch.setattr(rt, "_save_gcal_tokens", _save)
    for name, fn in (
        ("create_event", _create), ("update_event", _update),
        ("delete_event", _delete), ("patch_event_time", _patch_time),
    ):
        monkeypatch.setattr(f"app.tools.calendar_google.{name}", fn)
    # Silence the outbound notifications — not what these tests are about.
    async def _noop(*a, **k):
        return None
    monkeypatch.setattr("app.notifications.owner_notify.notify_owner", _noop)
    monkeypatch.setattr("app.notifications.sms.send_sms", _noop)
    return calls


def _slot(hours_ahead=48):
    return (datetime.now(LONDON_TZ) + timedelta(hours=hours_ahead)).replace(
        minute=0, second=0, microsecond=0
    )


# ── book: create, never flip ────────────────────────────────────────────────

def test_booking_creates_a_new_event_in_the_gap(cal):
    """Nothing published to consume, so the booking must create its own event."""
    start = _slot()
    out = asyncio.run(rt._book_appointment_provisional(
        {"patient_name": "Quentin Rook", "phone": "07502211207",
         "service": "deep_tissue_massage", "slot_iso": start.isoformat()},
        {}, _clinic(),
    ))
    assert out.get("success") is not False
    assert len(cal["created"]) == 1, "no event created — the booking is not on the calendar"
    assert cal["created"][0]["start"] == start
    assert not cal["updated"], "flipped a published event that does not exist under diary mode"
    assert cal["created"][0]["summary"].startswith("PENDING CONFIRMATION")


def test_booking_still_flips_a_published_slot_when_one_exists(cal):
    """The published model is unchanged — the map is what selects the branch."""
    start = _slot()
    session = {"_provisional_slot_events": {start.isoformat(): "published-1"}}
    asyncio.run(rt._book_appointment_provisional(
        {"patient_name": "Quentin Rook", "phone": "07502211207",
         "service": "deep_tissue_massage", "slot_iso": start.isoformat()},
        session, _clinic(mode="published"),
    ))
    assert [u["id"] for u in cal["updated"]] == ["published-1"]
    assert not cal["created"]


# ── cancel: delete under diary, restore only under published ────────────────

def _run_cancel(monkeypatch, mode, cal):
    start = _slot()
    event = {
        "id": "booked-1",
        "summary": "PENDING CONFIRMATION — Quentin Rook — Deep Tissue Massage (60 min)",
        "start": {"dateTime": start.isoformat()},
        "end": {"dateTime": (start + timedelta(hours=1)).isoformat()},
    }

    def _events(*a, **k):
        return [event]

    monkeypatch.setattr("app.tools.calendar_google.list_upcoming_events", _events)
    monkeypatch.setattr("app.clinic_config.get_clinic", lambda *a, **k: _clinic(mode))
    monkeypatch.setattr(rt, "_match_gcal_event", lambda *a, **k: event)
    return asyncio.run(rt._exec_cancel_appointment(
        {"phone": "07502211207"}, {"clinic_id": "vital_edge"},
    ))


def test_cancel_under_diary_deletes(monkeypatch, cal):
    """The fix. Leaving a renamed event behind would block the time forever,
    so a cancellation would REMOVE availability instead of returning it."""
    _run_cancel(monkeypatch, "diary", cal)
    assert cal["deleted"] == ["booked-1"]
    assert not cal["updated"], "left an event on the calendar — that time is now blocked"


def test_cancel_under_published_restores_the_slot(monkeypatch, cal):
    """Unchanged: where events ARE the offer, handing the slot back is right."""
    _run_cancel(monkeypatch, "published", cal)
    assert [u["summary"] for u in cal["updated"]] == ["Available"]
    assert not cal["deleted"]


def test_cancel_under_handoff_deletes(monkeypatch, cal):
    """Handoff offers nothing, so a restored marker means nothing — and would
    become a phantom block the moment the clinic is switched to diary."""
    _run_cancel(monkeypatch, "handoff", cal)
    assert cal["deleted"] == ["booked-1"]
    assert not cal["updated"]


# ── reschedule: move, never duplicate ───────────────────────────────────────

def test_reschedule_under_diary_moves_the_event(monkeypatch, cal):
    """No slot to consume and no old slot to hand back — just move it. A
    restore here would leave a block at the time the caller just gave up."""
    old = _slot()
    new = _slot(72)
    event = {
        "id": "booked-1",
        "summary": "PENDING CONFIRMATION — Quentin Rook — Deep Tissue Massage",
        "start": {"dateTime": old.isoformat()},
        "end": {"dateTime": (old + timedelta(hours=1)).isoformat()},
    }
    monkeypatch.setattr("app.tools.calendar_google.list_upcoming_events", lambda *a, **k: [event])
    monkeypatch.setattr("app.clinic_config.get_clinic", lambda *a, **k: _clinic())
    monkeypatch.setattr(rt, "_match_gcal_event", lambda *a, **k: event)

    asyncio.run(rt._exec_reschedule_appointment(
        {"phone": "07502211207", "new_slot_iso": new.isoformat()},
        {"clinic_id": "vital_edge"},
    ))

    assert [p["id"] for p in cal["patched"]] == ["booked-1"], "event was not moved"
    assert cal["patched"][0]["start"] == new
    assert not cal["created"], "created a second event — the caller now has two"
    assert "Available" not in [u["summary"] for u in cal["updated"]], (
        "handed the old time back as an event — under diary that BLOCKS it"
    )
