"""After a name exit, the booking carries only words the caller said.

CA5c7a273c47 (14 Sep 2026, demo, 962f1caf) — the model booked "I've Gardener"
out of "uh gardener" / "uh i've got bowel". "I've" is not a name.

CAb421b89c91 (14 Sep 2026, demo, 1bf58a35) — the model passed "Bowel Gardner",
which the owner says was RIGHT; the first version of this guard swapped in the
engine's one word and threw the good name away.

Rule, while the exit stands: keep each word of the model's name that the caller
said and that is not a filler word; if none survive, book the engine's best
effort. The name chase replaces the WHOLE booked name when the reply arrives.

Driven only as far as the provider dispatch, which is patched (see the 60
accidental Acuity bookings from tests/auto).
"""
from unittest.mock import AsyncMock, patch

from app.notifications.name_chase import resolved_summary
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


def _session(best_effort, *caller_turns):
    return {
        "clinic_id": "jv_v1",
        "phone_confirmed": True,
        "collected": {"name": best_effort, "phone": "07502211207", "reason": "ankle"},
        "reason": "ankle",
        "patient_name": best_effort,
        "_gate5n_exited": True,
        "_gate5n_best_effort_name": best_effort,
        "needs_name_correction_sms": True,
        "conversation_history": [{"role": "user", "content": t} for t in caller_turns],
        "_turn_user_text": "uh yeah go for it",
    }


def _args(name):
    return {"patient_name": name, "phone": "07502211207",
            "slot_iso": "2026-09-16T08:50:00", "service": "msk_initial_assessment",
            "location": "bolton"}


async def test_a_filler_word_is_dropped_from_the_models_name():
    s = _session("Gardener", "uh gardener", "uh i've got bowel",
                 "no that's not right i said i got bowel", "uh use this number")
    seen = await _drive_gate(_args("I've Gardener"), s)
    assert seen["patient_name"] == "Gardener"
    assert s["collected"]["name"] == "Gardener"
    assert s["needs_name_correction_sms"] is True


async def test_the_right_full_name_from_CAb421b89c91_is_kept():
    s = _session("Bowel", "um yeah so that would be um gardner", "i've got bowel",
                 "no that's not right i said i got bowel",
                 "no that's not right i said i got bowel", "uh use this number")
    seen = await _drive_gate(_args("Bowel Gardner"), s)
    assert seen["patient_name"] == "Bowel Gardner"
    assert s["patient_name"] == "Bowel Gardner"


async def test_a_word_the_caller_never_said_is_not_booked():
    s = _session("Bowel", "gardner", "i've got bowel")
    seen = await _drive_gate(_args("Ivor Bowel"), s)
    assert seen["patient_name"] == "Bowel"


async def test_nothing_survives_so_the_best_effort_is_booked():
    s = _session("Gardener", "uh gardener")
    seen = await _drive_gate(_args("Ivor Bowe"), s)
    assert seen["patient_name"] == "Gardener"


async def test_a_name_confirmed_after_the_exit_is_left_alone():
    # The name collector drops needs_name_correction_sms on a real confirm.
    s = _session("Gardner")
    s.pop("needs_name_correction_sms")
    seen = await _drive_gate(_args("Quentin Gardner"), s)
    assert seen["patient_name"] == "Quentin Gardner"


def test_the_chase_replaces_the_whole_booked_name():
    title = "Bowel Gardner - Initial Assessment (name to confirm)"
    assert resolved_summary(title, "Bowel Gardner", "Jane Smith") == \
        "Jane Smith - Initial Assessment"


async def test_the_chase_record_carries_the_whole_name_as_placeholder():
    from app.notifications import name_chase
    captured = {}

    async def _fake_create(**kw):
        captured.update(kw)

    with patch("app.storage.redis_store.create_pending_name_confirmation",
               new=AsyncMock(side_effect=_fake_create)), \
         patch("app.notifications.scheduler.schedule_name_confirm_reminder", new=AsyncMock()):
        await name_chase.start(
            session={"clinic_id": "northgate"}, clinic={"clinic_id": "northgate"},
            phone="07502211207", patient_name="Bowel Gardner", provider="google_calendar",
            appointment_id="evt1", when_label="Tuesday 15 September at 8:50am",
            location="Didsbury",
        )
    assert captured["placeholder"] == "Bowel Gardner"
    assert captured["first_name"] == "Bowel"
