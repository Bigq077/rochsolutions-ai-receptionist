"""Vital Edge holding switch: offer no times at all.

CAdafca484696fce9a538695f2a95ee04e (8 Aug 2026). `vitaledgetherapy@gmail.com`
holds Jonathan's BOOKED work — "Massage with Roger", "Padel with Jose" — and
`_check_availability_published` treats every timed event as a bookable slot. So
the times Susie offered were the times he was already busy, and a completed
booking would have landed on top of an existing client.

Until availability is computed by subtracting the diary from a working envelope,
`availability_mode: "handoff"` stops slots being offered at all. Offering nothing
is recoverable; offering someone else's appointment is not.

These tests pin the two properties that matter:
  1. no slot ever escapes while the switch is on — and in particular the call
     never reaches the calendar, so a stray event cannot leak through;
  2. the switch is OFF by default, so no other clinic changes behaviour.
"""

import asyncio

import pytest

from app.tools import receptionist_tools as rt


def _ve_clinic(**over):
    clinic = {
        "clinic_id": "vital_edge",
        "booking_system": "google_calendar_provisional",
        "calendar_id": "vitaledgetherapy@gmail.com",
        "availability_mode": "handoff",
        "days_ahead": 14,
    }
    clinic.update(over)
    return clinic


def _run(clinic, args=None):
    return asyncio.run(
        rt._check_availability_published(args or {"date_hint": "any"}, {}, clinic)
    )


def test_handoff_offers_no_slots():
    out = _run(_ve_clinic())
    assert out["slots"] == []
    assert out["available_days"] == []
    assert out["total_days"] == 0
    assert out["error"] == "availability_handoff"


def test_handoff_never_touches_the_calendar(monkeypatch):
    """The guard must return BEFORE any calendar read.

    A guard that filtered results afterwards would still be one bug away from
    leaking a busy event, and would still burn the Google round trip on a live
    call. So assert the calendar is never consulted at all.
    """
    def _boom(*a, **k):  # pragma: no cover - must never run
        raise AssertionError("calendar was read while availability_mode=handoff")

    monkeypatch.setattr(rt, "_get_tokens", _boom, raising=False)
    out = _run(_ve_clinic())
    assert out["slots"] == []


def test_handoff_instructs_the_model_not_to_invent_times():
    """The message is the only thing standing between the caller and a
    hallucinated slot, so pin its load-bearing instructions."""
    msg = _run(_ve_clinic())["message"].lower()
    assert "must not" in msg or "cannot" in msg
    assert "add_to_waitlist" in msg
    # It must not tell the model to claim it is checking a calendar it cannot read.
    assert "checking the calendar" in msg  # present only as a prohibition
    assert "do not" in msg or "must not" in msg


def test_practitioner_name_comes_from_clinic_json():
    """CLAUDE.md §5: clinic-specific behaviour belongs in clinic.json. The name
    is spoken to the caller, so a literal here would be wrong for every other
    tenant that ever sets this switch."""
    assert "jonathan" in _run(_ve_clinic(practitioner="Jonathan"))["message"].lower()
    assert "sam" in _run(_ve_clinic(practitioner="Sam"))["message"].lower()
    # No name configured → a neutral phrase, never a stray literal.
    generic = _run(_ve_clinic(practitioner=""))["message"].lower()
    assert "the practitioner will text or call" in generic
    assert "jonathan" not in generic


@pytest.mark.parametrize("mode", ["", None, "normal", "published"])
def test_switch_is_off_by_default(mode, monkeypatch):
    """Absent or any other value → the ordinary published path runs.

    Guarded by making the calendar read fail loudly: reaching it proves the
    guard did not fire.
    """
    reached = {"yes": False}

    async def _tokens():
        reached["yes"] = True
        return None  # no tokens → early, harmless return

    monkeypatch.setattr(rt, "_get_tokens", _tokens, raising=False)
    _run(_ve_clinic(availability_mode=mode))
    assert reached["yes"], f"guard fired for availability_mode={mode!r}"


def test_ve_clinic_json_has_the_switch_on():
    """The holding switch is only useful if it is actually set for VE."""
    from app.clinic_config import get_clinic

    assert (get_clinic("vital_edge") or {}).get("availability_mode") == "handoff"
