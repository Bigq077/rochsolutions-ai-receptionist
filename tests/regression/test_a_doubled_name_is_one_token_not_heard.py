"""CA66bd0930 (14 Sep 2026, JV, live) — booked, texted and alerted as
"Goner Goner". OPEN_DEFECTS_2026-09-14.md row #1b.

    Susie   'did you say "Goner" — is that right?'
    Caller  "goner"                         <-- STT, twice, of the real name
    Susie   "Thanks Goner — and your surname?"
    Caller  (silence; watchdog re-ask)
    Caller  "goner"
    Susie   "so that's Goner Goner, Monday the 14th … shall I go ahead?"
            [ms_conn v3] name upgraded from booking readback: 'Goner Goner'
    Model   book_appointment(patient_name="Goner Goner")   <-- two tokens: no chase

A second token that repeats the first is not a surname heard; it is the
same word not heard twice. Rule: the name is ONE best-effort token, the
name chase opens the spelling text, the not-heard wording is used — the
exit Gate 5n takes when it cannot hear a name.

The booking path is driven only as far as the provider dispatch, which is
patched: a test that reaches a provider can write a real appointment (see
the 60 accidental Acuity bookings from tests/auto).
"""
from unittest.mock import AsyncMock, patch

import pytest

from app.notifications.name_chase import (
    collapse_doubled_name,
    is_doubled_name,
    name_is_pending,
    name_was_not_heard,
)
from app.tools import receptionist_tools as rt


# ── the rule, pure ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("name", ["Goner Goner", "goner GONER", "Gardener gardener.", "Mark Mark Mark"])
def test_a_repeated_first_name_is_a_doubled_name(name):
    assert is_doubled_name(name)
    assert collapse_doubled_name(name) == name.split()[0]


@pytest.mark.parametrize("name", ["Quentin Roch", "Goner", "", None, "Sam Samuels", "Lee Lees"])
def test_a_real_name_is_left_alone(name):
    assert not is_doubled_name(name)
    assert collapse_doubled_name(name) == (name or "")


def test_the_one_token_is_chased_with_the_not_heard_wording():
    s = {"needs_name_correction_sms": True}
    assert name_is_pending("Goner", s)
    assert name_was_not_heard(s)


# ── the booking gate ────────────────────────────────────────────────────────

async def _drive_gate(args, session):
    """Run `_exec_book_appointment` up to the provider dispatch only."""
    seen = {}

    async def _fake_provider(a, s):
        seen["args"] = dict(a)
        return {"success": False, "error": "test stub — no provider reached"}

    with patch.object(rt, "uses_acuity", return_value=True), \
         patch.object(rt, "_book_appointment_acuity", new=AsyncMock(side_effect=_fake_provider)):
        await rt._exec_book_appointment(args, session)
    return seen["args"]


def _session():
    return {
        "clinic_id": "jv_v1",
        "phone_confirmed": True,
        "collected": {"name": "Goner Goner", "phone": "07581253004",
                      "reason": "broken tibia"},
        "reason": "broken tibia",
        "patient_name": "Goner Goner",
        "conversation_history": [],
    }


async def test_the_doubled_name_from_CA66bd0930_books_as_one_token():
    s = _session()
    args = {"patient_name": "Goner Goner", "phone": "07581253004",
            "slot_iso": "2026-09-14T18:45:00", "service": "msk_initial_assessment",
            "location": "bolton"}
    seen = await _drive_gate(args, s)
    assert seen["patient_name"] == "Goner"
    assert s["needs_name_correction_sms"] is True
    assert s["patient_name"] == "Goner"
    assert s["collected"]["name"] == "Goner"
    assert name_is_pending(seen["patient_name"], s)
    assert name_was_not_heard(s)


async def test_a_real_two_token_name_is_untouched_by_the_gate():
    s = _session()
    s["patient_name"] = s["collected"]["name"] = "Quentin Roch"
    args = {"patient_name": "Quentin Roch", "phone": "07581253004",
            "slot_iso": "2026-09-14T18:45:00", "service": "msk_initial_assessment",
            "location": "bolton"}
    seen = await _drive_gate(args, s)
    assert seen["patient_name"] == "Quentin Roch"
    assert not s.get("needs_name_correction_sms")
    assert not name_is_pending(seen["patient_name"], s)
