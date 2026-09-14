"""CA5c7a273c47 (14 Sep 2026, demo, build 962f1caf) — booked as "I've Gardener".

    Susie   "could I take your first name and surname?"
    Caller  "uh gardener"
    Susie   "could I take your first name as well?"
    Caller  "uh i've got bowel"
    Susie   "Did you say Bowel — is that right?"
    Caller  "no that's not right i said i got bowel"
    Susie   "No problem — I'll pop what I've got on the booking … text you …"
            [ms_gate5nf] second ask — exit spoken; best effort "Gardener"
    Caller  "uh use this number"          (loop fix 962f1caf — PROVED here)
    Model   book_appointment(patient_name="I've Gardener")

The chase's placeholder is the first token, so the caller's reply would
have renamed the diary entry "<their name> Gardener". While the exit stands
the booking carries the engine's best-effort name.

Driven only as far as the provider dispatch, which is patched (see the 60
accidental Acuity bookings from tests/auto).
"""
from unittest.mock import AsyncMock, patch

from app.tools import receptionist_tools as rt


async def _drive_gate(args, session):
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
        "collected": {"name": "Gardener", "phone": "07502211207", "reason": "ankle"},
        "reason": "ankle",
        "patient_name": "Gardener",
        "_gate5n_exited": True,
        "_gate5n_best_effort_name": "Gardener",
        "needs_name_correction_sms": True,
        "conversation_history": [],
    }


def _args(name):
    return {"patient_name": name, "phone": "07502211207",
            "slot_iso": "2026-09-16T08:50:00", "service": "msk_initial_assessment",
            "location": "bolton"}


async def test_the_model_composed_name_is_replaced_by_the_exit_name():
    s = _session()
    seen = await _drive_gate(_args("I've Gardener"), s)
    assert seen["patient_name"] == "Gardener"
    assert s["patient_name"] == "Gardener"
    assert s["collected"]["name"] == "Gardener"
    assert s["needs_name_correction_sms"] is True


async def test_a_name_confirmed_after_the_exit_is_left_alone():
    # The name collector drops needs_name_correction_sms on a real confirm.
    s = _session()
    s.pop("needs_name_correction_sms")
    s["patient_name"] = s["collected"]["name"] = "Quentin Gardner"
    seen = await _drive_gate(_args("Quentin Gardner"), s)
    assert seen["patient_name"] == "Quentin Gardner"


async def test_no_exit_no_change():
    s = _session()
    for k in ("_gate5n_exited", "_gate5n_best_effort_name", "needs_name_correction_sms"):
        s.pop(k)
    s["patient_name"] = s["collected"]["name"] = "Quentin Roch"
    seen = await _drive_gate(_args("Quentin Roch"), s)
    assert seen["patient_name"] == "Quentin Roch"
