"""Regression — the name fallback must never target an appointment it guessed.

_match_gcal_event picks the calendar event that cancel_appointment and
reschedule_appointment act on. It tries appointment_id, then the booked-under
phone, then patient_name. Both callers are destructive, so a wrong match here
moves or cancels a stranger's appointment.

The name branch had two reachable hazards:

1. The model passes a placeholder. Seen live on call
   CA1fc9cb13337ccc7eb936e0dbf5c8fc3d (2026-08-02):
   reschedule_appointment(patient_name="Unknown", ...) despite lookup_patient
   having returned "Quentin Rook". It was harmless only because the
   appointment_id branch matched first — nothing stopped it reaching the name
   branch on a call where the id and phone both missed.

2. The comparison is a substring, so a short fragment matches any summary
   containing it, and the first match won silently even when several matched.

The branch now refuses anything it cannot resolve to exactly one event.
"""

import pytest

from app.tools.receptionist_tools import _match_gcal_event


def _ev(event_id: str, summary: str, phone: str = "") -> dict:
    return {
        "id": event_id,
        "summary": summary,
        "description": f"Phone: {phone}" if phone else "",
        "start": {"dateTime": "2026-08-06T16:30:00+01:00"},
        "end": {"dateTime": "2026-08-06T17:10:00+01:00"},
    }


# ── The earlier branches must keep winning ──────────────────────────────────

def test_confirmed_appointment_id_still_wins():
    """The branch that saved the live call — must not regress."""
    events = [_ev("evt-1", "Quentin Rook — Initial Assessment"), _ev("evt-2", "Someone Else")]
    found = _match_gcal_event(
        events,
        {"patient_name": "Unknown"},
        {"_lookup_appointment_id": "evt-1"},
    )
    assert found is not None and found["id"] == "evt-1"


def test_phone_still_matches_before_name():
    events = [_ev("evt-1", "Someone Else"), _ev("evt-2", "Quentin Rook", phone="07798571247")]
    found = _match_gcal_event(events, {"phone": "07798571247"}, {})
    assert found is not None and found["id"] == "evt-2"


# ── The name branch must refuse what it cannot resolve ──────────────────────

@pytest.mark.parametrize(
    "placeholder",
    ["Unknown", "unknown", "  Unknown  ", "the caller", "patient", "N/A", "TBC", "None"],
)
def test_placeholder_name_targets_nothing(placeholder):
    """A placeholder must not select an event even when one literally matches."""
    events = [_ev("evt-1", "Unknown Caller — Initial Assessment"),
              _ev("evt-2", "The Patient — Follow Up")]
    assert _match_gcal_event(events, {"patient_name": placeholder}, {}) is None


def test_short_fragment_does_not_match_a_longer_name():
    """'jo' must not silently select 'Jonathan Smith'."""
    events = [_ev("evt-1", "Jonathan Smith — Initial Assessment")]
    assert _match_gcal_event(events, {"patient_name": "jo"}, {}) is None


def test_ambiguous_name_refuses_to_guess():
    """Several matches means we cannot know which appointment the caller means."""
    events = [_ev("evt-1", "John Smith — Initial Assessment"),
              _ev("evt-2", "Jane Smith — Follow Up")]
    assert _match_gcal_event(events, {"patient_name": "smith"}, {}) is None


def test_empty_name_targets_nothing():
    events = [_ev("evt-1", "Quentin Rook — Initial Assessment")]
    assert _match_gcal_event(events, {"patient_name": ""}, {}) is None
    assert _match_gcal_event(events, {}, {}) is None


# ── A real, unambiguous name still works ────────────────────────────────────

def test_unique_real_name_still_matches():
    events = [_ev("evt-1", "Quentin Rook — Initial Assessment"),
              _ev("evt-2", "Jane Doe — Follow Up")]
    found = _match_gcal_event(events, {"patient_name": "Quentin Rook"}, {})
    assert found is not None and found["id"] == "evt-1"


def test_unique_first_name_still_matches():
    events = [_ev("evt-1", "Quentin Rook — Initial Assessment"),
              _ev("evt-2", "Jane Doe — Follow Up")]
    found = _match_gcal_event(events, {"patient_name": "quentin"}, {})
    assert found is not None and found["id"] == "evt-1"
