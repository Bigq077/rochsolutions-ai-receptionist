# tests/regression/test_booking_collection_gate.py
"""
A1 / A2 — `book_appointment` completed without a reason and without a confirmed
phone number.

Evidence: `docs/plan/FIX_QUEUE_PRE_DEMO.md` (A1, A2) and the verification call
`CA4969580082db5e757c3b1d04dd38e7ae`, which booked successfully with
`collected.reason = None`, having asked the reason *after* the slots were offered
(the caller never answered it), and having spent a turn asking for a number it
already held from caller ID before announcing "I already have your number
confirmed."

Both steps were enforced by the prompt plus the `llm_stream.py` backstops, and
those backstops stop blocking the moment the model has *asked* the question.
Asked is not answered — which is precisely how that call booked.

`_exec_book_appointment` now holds the tool boundary for both, in the same shape
as the clinical-screening backstop: refuse with `success: False` and an
instruction the model can act on.

Tests use `_resolve_clinic_id` (the first statement after the gates) as a
tripwire: reaching it proves the booking proceeded. No calendar or network is
touched. The screening backstop is stubbed clear so only the collection gate is
under test.
"""
from __future__ import annotations

import pytest

from app.tools import receptionist_tools as rt
from app.media_streams import clinical_screening as cs


class _ProceededPastGate(Exception):
    """Raised by the patched _resolve_clinic_id to prove the gates were passed."""


@pytest.fixture(autouse=True)
def _screen_clear(monkeypatch):
    """Screening is not what these tests are about — hold it open."""
    monkeypatch.setattr(cs, "booking_blocked_reason", lambda *_a, **_k: None)


@pytest.fixture(autouse=True)
def tripwire(monkeypatch):
    def sentinel(*_a, **_k):
        raise _ProceededPastGate()
    monkeypatch.setattr(rt, "_resolve_clinic_id", sentinel)


def _ok_session(**over):
    s = {"clinic_id": "jv_v1", "reason": "left shoulder pain", "phone_confirmed": True}
    s.update(over)
    return s


# ── A2 · no reason on record ──────────────────────────────────────────────
async def test_refuses_when_reason_unset():
    session = {"clinic_id": "jv_v1", "phone_confirmed": True}
    result = await rt._exec_book_appointment({}, session)
    assert result.get("success") is False
    assert "reason" in (result.get("error") or "").lower()


async def test_refuses_when_reason_is_blank_or_non_text():
    for bad in ("", "   ", None, False, {"shoulder": True}):
        session = {"clinic_id": "jv_v1", "phone_confirmed": True, "reason": bad}
        result = await rt._exec_book_appointment({}, session)
        assert result.get("success") is False, f"blank reason {bad!r} must not book"


async def test_reason_from_session_satisfies_the_gate():
    with pytest.raises(_ProceededPastGate):
        await rt._exec_book_appointment({}, _ok_session())


async def test_reason_from_collected_mirror_satisfies_the_gate():
    session = {
        "clinic_id": "jv_v1",
        "phone_confirmed": True,
        "collected": {"reason": "knee giving way"},
    }
    with pytest.raises(_ProceededPastGate):
        await rt._exec_book_appointment({}, session)


async def test_reason_from_tool_args_satisfies_the_gate():
    """The model can supply the reason it collected, so the gate cannot deadlock
    on a session slot that no path happened to write."""
    session = {"clinic_id": "jv_v1", "phone_confirmed": True}
    with pytest.raises(_ProceededPastGate):
        await rt._exec_book_appointment({"reason": "ankle rolled at football"}, session)


async def test_reason_is_committed_both_ways_for_the_call_record():
    """collected.reason is what obs and the SMS router read — a booking that
    passes the gate must carry the reason, not leave it None as call
    CA4969580082db5e757c3b1d04dd38e7ae did."""
    session = {"clinic_id": "jv_v1", "phone_confirmed": True}
    with pytest.raises(_ProceededPastGate):
        await rt._exec_book_appointment({"reason": "lower back pain"}, session)
    assert session["reason"] == "lower back pain"
    assert session["collected"]["reason"] == "lower back pain"


async def test_existing_reason_is_not_overwritten_by_tool_args():
    session = _ok_session(collected={"reason": "left shoulder pain"})
    with pytest.raises(_ProceededPastGate):
        await rt._exec_book_appointment({"reason": "something the model made up"}, session)
    assert session["reason"] == "left shoulder pain"
    assert session["collected"]["reason"] == "left shoulder pain"


# ── A1 · phone not confirmed ──────────────────────────────────────────────
async def test_refuses_when_phone_confirmed_unset():
    """The exact CA4969… shape: the phone question was asked, the answer was not
    an acceptance, phone_confirmed never flipped — and it booked anyway."""
    session = {"clinic_id": "jv_v1", "reason": "left shoulder pain"}
    result = await rt._exec_book_appointment({"phone": "+447502211207"}, session)
    assert result.get("success") is False
    assert "number" in (result.get("error") or "").lower()


async def test_refuses_when_phone_confirmed_is_false():
    """False means the caller actively rejected the number — stricter than unset."""
    session = _ok_session(phone_confirmed=False)
    result = await rt._exec_book_appointment({}, session)
    assert result.get("success") is False


async def test_refuses_when_phone_confirmed_is_merely_truthy():
    """Only the authoritative True passes — 'yes', 1 or a number string are not
    the flag every phone-confirm path sets."""
    for truthy in ("yes", 1, "07502211207"):
        session = _ok_session(phone_confirmed=truthy)
        result = await rt._exec_book_appointment({}, session)
        assert result.get("success") is False, f"{truthy!r} must not pass as confirmed"


async def test_a_passing_phone_arg_does_not_substitute_for_confirmation():
    """collected["phone"] is pre-filled from caller ID at call start, so a phone
    in the args proves nothing about the caller having confirmed it."""
    session = {
        "clinic_id": "jv_v1",
        "reason": "left shoulder pain",
        "collected": {"phone": "+447502211207"},
    }
    result = await rt._exec_book_appointment({"phone": "+447502211207"}, session)
    assert result.get("success") is False


# ── Both satisfied ────────────────────────────────────────────────────────
async def test_proceeds_when_reason_and_phone_are_both_on_record():
    with pytest.raises(_ProceededPastGate):
        await rt._exec_book_appointment(
            {"patient_name": "Quentin Roche", "phone": "+447502211207"},
            _ok_session(),
        )
