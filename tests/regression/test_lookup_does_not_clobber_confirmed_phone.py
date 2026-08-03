"""lookup_recent_appointment must not overwrite the number confirmed on THIS
call with the number on file at the booking provider.

`collected["phone"]` is the reference `_reconcile_booking_phone` (the A3 gate)
compares the model's `phone` argument against. If a lookup overwrites it, a
caller who typed a new number and then hit the lookup path would have the A3
gate "correct" their booking back to the stale number on file — the gate that
exists to protect the confirmed number would be the thing discarding it.

Hardening, not a reproduced defect. Reachability was measured against the obs
corpus and came back 0 of 155: ten calls stored a caller-supplied number, four
ran the returning-patient path, none did both. See B-47 in REGISTER_B_U.md.

Note this path is Theorem-only (receptionist_tools.py:6148 returns early for
any other clinic), so it matters for the clinic being onboarded, not for jv_v1
or vital_edge.
"""
import pytest

from app.tools import receptionist_tools as rt


ON_FILE = "07111222333"      # the stale number Acuity has
TYPED = "07798571247"        # what the caller confirmed on this call


class _FakeAdapter:
    """Returns one appointment matching whatever phone the lookup asks for."""

    def __init__(self, phone):
        self._phone = phone

    async def list_appointments(self, min_date, max_date, calendar_id=None):
        return [
            {
                "phone": self._phone,
                "firstName": "Quentin",
                "lastName": "Roch",
                "type": "Physiotherapy",
                "datetime": "2026-07-30T10:00:00+01:00",
            }
        ]


@pytest.fixture
def _acuity(monkeypatch):
    monkeypatch.setattr(rt, "_get_acuity_adapter", lambda: _FakeAdapter(ON_FILE))


def _session(**over):
    s = {
        "clinic_id": "theorem_v3",
        "collected": {},
        "selected_location": "alcester",
    }
    s.update(over)
    return s


async def test_confirmed_number_survives_the_lookup(_acuity):
    """The B-47-adjacent case. The caller confirmed TYPED on this call; Acuity
    has ON_FILE. The lookup must leave the confirmed number alone."""
    session = _session(
        collected={"phone": TYPED},
        phone_confirmed=True,
        phone_entered_by_keypad=True,
        phone_number=TYPED,
    )

    result = await rt._exec_lookup_recent_appointment({"phone": ON_FILE}, session)

    assert result["found"] is True, "the lookup itself must still work"
    assert session["collected"]["phone"] == TYPED
    assert session["phone_number"] == TYPED


async def test_a3_gate_would_not_rewrite_the_booking_after_a_lookup(_acuity):
    """The consequence, stated in the gate's own terms.

    _reconcile_booking_phone compares the model's `phone` arg against
    collected["phone"]. With the confirmed number preserved, a model passing
    the confirmed number reads as "match" and the booking is left alone. Before
    the guard, collected["phone"] was ON_FILE and this returned a "mismatch"
    that would have rewritten the booking to the stale number.
    """
    session = _session(
        collected={"phone": TYPED}, phone_confirmed=True, phone_number=TYPED
    )
    await rt._exec_lookup_recent_appointment({"phone": ON_FILE}, session)

    fix, reason = rt._reconcile_booking_phone({"phone": TYPED}, session)
    assert reason == "match"
    assert fix is None


async def test_verbally_confirmed_caller_id_is_protected_too(_acuity):
    """The guard is on phone_confirmed, not phone_entered_by_keypad: a caller
    who said "yes, use this number" has confirmed it just as much as one who
    typed it, and A1/A3 already treat that flag as the authority."""
    session = _session(
        collected={"phone": TYPED},
        phone_confirmed=True,
        phone_entered_by_keypad=False,
    )

    await rt._exec_lookup_recent_appointment({"phone": ON_FILE}, session)
    assert session["collected"]["phone"] == TYPED


async def test_lookup_still_populates_phone_when_nothing_was_confirmed(_acuity):
    """The guard must not break the path it is protecting. With no confirmed
    number, the lookup is the best source there is and must still fill it in —
    that is the whole point of the returning-patient flow."""
    session = _session()
    assert session.get("phone_confirmed") is not True

    result = await rt._exec_lookup_recent_appointment({"phone": ON_FILE}, session)

    assert result["found"] is True
    assert session["collected"]["phone"] == ON_FILE
    assert session["phone_number"] == ON_FILE


async def test_lookup_still_populates_the_name_either_way(_acuity):
    """Only the phone is guarded. The canonical name from the provider is still
    the point of the lookup and must land in both cases."""
    for confirmed in (True, False):
        session = _session(
            collected={"phone": TYPED} if confirmed else {},
            phone_confirmed=confirmed,
        )
        await rt._exec_lookup_recent_appointment({"phone": ON_FILE}, session)
        assert session["full_name"] == "Quentin Roch"
        assert session["collected"]["full_name"] == "Quentin Roch"
        assert session["returning_plan_lookup_type"] == "Physiotherapy"


async def test_guard_is_theorem_only_by_construction(_acuity):
    """Recorded so the blast radius is not re-derived later: the whole function
    returns early for any non-Theorem clinic, so neither the defect nor this
    guard can affect jv_v1 or vital_edge."""
    session = _session(clinic_id="jv_v1", phone_confirmed=True)
    result = await rt._exec_lookup_recent_appointment({"phone": ON_FILE}, session)
    assert result["found"] is False
    assert "Theorem" in result["message"]
