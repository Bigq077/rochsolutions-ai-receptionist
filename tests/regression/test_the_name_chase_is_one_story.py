"""The name chase — one story, told the same way in voice and text.

CAb5a26a10 (northgate demo line, 13 Sep 2026, build 6c63c98f). Gate 5n fired,
the booking went in under a placeholder, and what the caller would then have
received (with SMS on) was four pieces built at different times that did not
agree: a confirmation addressed "Hi Still" that asked for nothing; a second
"didn't catch your name" text that only the deterministic booking path ever
sent; a 30-minute "reminder ... to confirm your appointment"; and a reply
handler that updated Acuity only, on a Google Calendar clinic.

Owner decisions, 13 Sep 2026:
  1. one merged text, immediately -- the booking AND the ask;
  2. one follow-up at +2 h, worded as a follow-up, then stop;
  3. the placeholder is never spoken in the readback;
  4. the calendar entry carries "(name to confirm)";
  5. the close restates the situation and that the text is how they confirm.
"""
from __future__ import annotations

import inspect
from datetime import datetime

import pytest

from app.notifications import name_chase as chase
from app.media_streams.turn_handler import sanitise_response

WHEN = datetime(2026, 9, 14, 8, 50)
LABEL = "Monday 14 September at 8:50am"


# ── The words ──────────────────────────────────────────────────────────────

def test_the_when_label_is_the_confirmation_shape_on_every_platform():
    assert chase.when_label_for_sms(WHEN) == LABEL
    assert chase.when_label_for_sms(None) == "your appointment"


def test_the_one_text_says_the_booking_and_asks_for_the_name():
    t = chase.booking_text_name_pending(
        clinic_name="Northgate Physio", when_label=LABEL,
        location="Didsbury", clinic_phone="0161 000 0000",
    )
    assert t.startswith("Hi, this is Susie from Northgate Physio.")
    assert LABEL in t and "Didsbury" in t
    assert "reply with your first name and surname" in t
    assert "Hi Still" not in t and "Hi Lecture" not in t
    assert "0161 000 0000" in t


def test_the_follow_up_is_not_a_reminder_and_never_says_confirm():
    t = chase.nudge_text(when_label=LABEL)
    assert "reminder" not in t.lower()
    assert "to confirm" not in t.lower()
    assert LABEL in t and "first name and surname" in t
    assert not t.startswith("Hi Still")


def test_the_thank_you():
    assert chase.thanks_text(first_name="Xiomara", when_label=LABEL) == (
        "Thanks Xiomara — booking updated. See you on Monday 14 September at 8:50am."
    )


def test_the_close_restates_the_situation_and_the_text():
    c = chase.close_line(LABEL)
    assert c.startswith(f"All booked for {LABEL}.")
    assert "didn't quite catch your name" in c
    assert "texting you now" in c
    assert "reply with your first name and surname" in c
    steer = chase.close_instruction(LABEL)
    assert c in steer
    assert "do not say the placeholder name aloud" in steer
    assert "do not ask for the name again" in steer


# ── The calendar tag ───────────────────────────────────────────────────────

def test_the_tag_is_added_once_and_resolved_with_the_real_name():
    s = chase.tag_summary("Initial Assessment for Priya — Still")
    assert s.endswith("(name to confirm)")
    assert chase.tag_summary(s) == s, "never doubled"
    assert chase.resolved_summary(s, "Still", "Xiomara Roch") == (
        "Initial Assessment for Priya — Xiomara Roch"
    )


def test_a_hand_edited_title_only_loses_the_tag():
    s = "Initial Assessment for Priya — Someone Else (name to confirm)"
    assert chase.resolved_summary(s, "Still", "Xiomara Roch") == (
        "Initial Assessment for Priya — Someone Else"
    )


@pytest.mark.parametrize("name,pending", [
    ("Still", True), ("", True), (None, True), ("  ", True),
    ("Xiomara Roch", False), ("Quentin Rook", False),
])
def test_one_token_is_pending(name, pending):
    assert chase.name_is_pending(name) is pending


def test_a_two_token_placeholder_is_pending_when_the_engine_says_so():
    # CAb5a26a10 booked "Still Gping" -- two tokens. The one-token rule alone
    # would never have chased the call that motivated all of this.
    assert chase.name_is_pending("Still Gping") is False
    assert chase.name_is_pending("Still Gping", {"_gate5n_exited": True}) is True
    assert chase.name_is_pending("Still Gping", {"needs_name_correction_sms": True}) is True
    assert chase.name_was_not_heard({}) is False
    assert chase.name_was_not_heard(None) is False


# ── Voice: the exit, the readback, the prompt ──────────────────────────────

def _session(**over):
    s = {
        "clinic_id": "northgate", "twilio_from_local": "07502211207",
        "booking_flow_active": True, "slots_presented": True,
        "phone_confirmed": True, "_gate5n_exited": True,
        "_gate5n_best_effort_name": "Still", "patient_name": "Still",
    }
    s.update(over)
    return s


def test_the_exit_says_what_will_happen_and_what_they_do():
    s = _session(_gate5n_exited=False, patient_name=None,
                 _turn_user_text="still wrong it's xiomara")
    out = sanitise_response("Could you spell your surname for me?", s)
    assert "I'll text you after the call so you can reply with the spelling" in out
    assert "double-check the spelling by text" not in out


@pytest.mark.parametrize("said,expected", [
    ("So that's Still, Monday the 14th of September at ten to nine — shall I go ahead and book that in?",
     "So that's Monday the 14th of September at ten to nine — shall I go ahead and book that in?"),
    ("Thanks Still — is oh seven five oh two the best number for you?",
     "Thanks — is oh seven five oh two the best number for you?"),
    ("Got it — Still. I've got you on oh seven five oh two, is that the best number?",
     "Got it — I've got you on oh seven five oh two, is that the best number?"),
])
def test_the_placeholder_is_never_spoken_in_a_readback(said, expected):
    assert sanitise_response(said, _session()) == expected


def test_the_word_is_left_alone_when_it_is_not_the_placeholder():
    line = "I'll pop that on the booking. Is that still the best number?"
    assert sanitise_response(line, _session()) == line


def test_a_real_name_is_not_stripped():
    s = _session(_gate5n_exited=False, _gate5n_best_effort_name="", patient_name="Sarah Jones")
    line = "So that's Sarah, Monday the 14th — shall I go ahead and book that in?"
    assert sanitise_response(line, s) == line


def test_the_prompt_tells_the_model_the_situation():
    from app.clinic_config import get_clinic
    from app.prompts.clinic_template_prompt import build_clinic_prompt

    _, dyn = build_clinic_prompt({"clinic_id": "northgate", "_gate5n_exited": True},
                                 get_clinic("northgate"))
    assert "NAME could not be heard" in dyn
    assert "do NOT say the placeholder name" in dyn
    _, dyn2 = build_clinic_prompt({"clinic_id": "northgate"}, get_clinic("northgate"))
    assert "NAME could not be heard" not in dyn2


# ── Text: the confirmation, the nudge, the reply ───────────────────────────

def _live_session(**over):
    s = {
        "collected": {"name": "Still Gping", "patient_type": "NEW"},
        "clinic_id": "northgate",
        "selected_slot": "2026-09-14T08:50:00",
        "selected_slot_speech": "Monday 14th September at 8:50am",
    }
    s.update(over)
    return s


@pytest.mark.asyncio
async def test_the_confirmation_becomes_the_one_text_when_the_name_is_pending(monkeypatch):
    """The live path: the ONE text is the ordinary confirmation -- address,
    first-visit note, Maps link all kept -- not greeted by the placeholder,
    carrying the ask the caller was promised on the call."""
    from app.notifications import booking_sms

    sent = {}

    async def _capture(to, message, from_number=None):
        sent["to"], sent["message"] = to, message
        return "SM_test"

    monkeypatch.setattr(booking_sms, "send_sms", _capture)
    ok = await booking_sms.send_booking_confirmation(
        patient_phone="+447502211207", patient_name="Still Gping",
        appointment_time=WHEN, location="Didsbury",
        clinic_name="Northgate Physio", clinic_phone="0161 000 0000",
        session=_live_session(), name_pending=True,
    )
    assert ok is True
    m = sent["message"]
    assert m.startswith("Hi there \U0001F44B"), m
    assert "Hi Still" not in m
    assert "I didn't quite catch your name on the call" in m
    assert "reply to this message with your first name and surname" in m
    assert "Please reply to this message with your full name" not in m, "one ask, not two"
    assert "September 14, 2026" in m and "8:50am" in m
    assert "first visit" in m, "the first-visit note survives"
    assert "Maps:" in m


def test_a_heard_first_name_keeps_the_ordinary_ask():
    from app.sms_templates import build_sms

    m = build_sms(_live_session(collected={"name": "Sarah"}))
    assert m.startswith("Hi Sarah \U0001F44B")
    assert "Please reply to this message with your full name" in m
    assert "didn't quite catch" not in m


def test_a_full_name_on_record_is_byte_identical_to_before():
    from app.sms_templates import build_sms

    s = _live_session(collected={"name": "Xiomara Roch"})
    assert build_sms(s) == build_sms(s, name_not_heard=False)
    assert "reply to this message with your f" not in build_sms(s)


def test_a_home_visit_with_a_pending_name_asks_for_both():
    from app.sms_templates import build_sms

    s = _live_session(collected={"name": "Still", "location": "home_visit"})
    m = build_sms(s, name_not_heard=True)
    assert "full home address and postcode" in m
    assert "first name and surname" in m
    assert m.startswith("Hi there \U0001F44B")
    # ...and a home visit with a full name on record asks for the address only
    s2 = _live_session(collected={"name": "Xiomara Roch", "location": "home_visit"})
    m2 = build_sms(s2)
    assert "full home address and postcode" in m2
    assert "full name" not in m2 and "surname" not in m2


@pytest.mark.asyncio
async def test_the_legacy_no_session_caller_gets_the_standalone_text(monkeypatch):
    from app.notifications import booking_sms

    sent = {}

    async def _capture(to, message, from_number=None):
        sent["message"] = message
        return "SM_test"

    monkeypatch.setattr(booking_sms, "send_sms", _capture)
    await booking_sms.send_booking_confirmation(
        patient_phone="+447502211207", patient_name="Still",
        appointment_time=WHEN, location="Didsbury",
        clinic_name="Northgate Physio", clinic_phone="0161 000 0000",
        session=None, name_pending=True,
    )
    assert sent["message"].startswith("Hi, this is Susie from Northgate Physio.")
    assert "reply with your first name and surname" in sent["message"]


def test_the_nudge_defaults_to_two_hours():
    from app.notifications.scheduler import schedule_name_confirm_reminder

    assert inspect.signature(schedule_name_confirm_reminder).parameters["delay_minutes"].default == 120


def test_the_nudge_sender_uses_the_follow_up_wording():
    from app.notifications import scheduler

    src = inspect.getsource(scheduler.process_name_confirm_reminders)
    assert "nudge_text(" in src
    assert "just a reminder" not in src
    assert "to confirm your appointment" not in src


@pytest.mark.asyncio
async def test_a_reply_renames_the_calendar_event_on_a_google_clinic(monkeypatch):
    from app.tools import calendar_google, receptionist_tools

    calls = {}

    def _update(toks, event_id, summary, description, calendar_id):
        calls.update(event_id=event_id, summary=summary, calendar_id=calendar_id)
        return {}

    async def _toks(clinic_id=None):
        return {"token": "x"}

    monkeypatch.setattr(calendar_google, "update_event", _update)
    monkeypatch.setattr(receptionist_tools, "_get_tokens", _toks)
    pending = {
        "provider": "google_calendar", "appointment_id": "evt1",
        "calendar_id": "cal@x", "first_name": "Still",
        "event_summary": "Initial Assessment for Priya — Still (name to confirm)",
        "clinic_id": "northgate",
    }
    assert await chase.apply_reply(pending, "Xiomara", "Roch") is True
    assert calls == {
        "event_id": "evt1", "calendar_id": "cal@x",
        "summary": "Initial Assessment for Priya — Xiomara Roch",
    }


@pytest.mark.asyncio
async def test_a_reply_updates_acuity_on_an_acuity_clinic(monkeypatch):
    from app.tools import receptionist_tools

    calls = {}

    class _Adapter:
        async def _request_with_retry(self, method, path, json=None, allow_retry=True):
            calls.update(method=method, path=path, json=json)

    monkeypatch.setattr(receptionist_tools, "_get_acuity_adapter", lambda: _Adapter())
    pending = {"provider": "acuity", "appointment_id": "123", "first_name": "Still"}
    assert await chase.apply_reply(pending, "Xiomara", "Roch") is True
    assert calls == {"method": "PUT", "path": "/appointments/123",
                     "json": {"firstName": "Xiomara", "lastName": "Roch"}}
    # first name only: surname left alone
    assert await chase.apply_reply(pending, "Xiomara", "") is True
    assert calls["json"] == {"firstName": "Xiomara"}


@pytest.mark.asyncio
async def test_a_reply_with_no_appointment_is_refused():
    assert await chase.apply_reply({"provider": "acuity"}, "X", "Y") is False


# ── The seams: every path tells the same story ─────────────────────────────

def test_both_booking_paths_open_the_chase_and_send_the_one_text():
    from app.tools import receptionist_tools

    src = inspect.getsource(receptionist_tools)
    assert src.count("await _chase.start(") == 2, "Acuity path and Google Calendar path"
    assert src.count("_chase.name_is_pending(patient_name, session)") == 2, "the session's verdict counts"
    assert src.count("name_pending=_name_not_heard,") == 2, "the not-heard wording only when not heard"
    assert src.count("if _name_not_heard:") == 2
    assert src.count('_result["close_with"] = _chase.close_instruction(') == 2
    assert "summary = _chase.tag_summary(summary)" in src
    assert "create_pending_name_confirmation(" not in src.split("await _chase.start(")[0].split("# Stage 2")[-1], (
        "the record is the chase's to create"
    )


def test_the_record_carries_the_provider():
    from app.storage.redis_store import create_pending_name_confirmation

    params = inspect.signature(create_pending_name_confirmation).parameters
    for p in ("provider", "calendar_id", "event_summary", "clinic_id", "when_label"):
        assert p in params, p


def test_the_inbound_handler_uses_the_chase_and_thanks_the_caller():
    from app.routes import twilio

    src = inspect.getsource(twilio.sms_inbound)
    assert "_chase.apply_reply(pending, first_name, last_name)" in src
    assert "_chase.thanks_text(" in src
    assert 'f"/appointments/{appointment_id}"' not in src, "Acuity-only write-back is gone"


def test_the_deterministic_path_does_not_send_a_second_text():
    from app.media_streams import flow

    src = inspect.getsource(flow)
    assert 'if _needs_name_sms and self.session.get("_name_chase_open"):' in src
    assert "_cb_done = _nc_close(str(_slot_cb))" in src


@pytest.mark.asyncio
async def test_the_chase_flag_is_set_even_when_redis_is_down(monkeypatch):
    # The ONE text goes with the confirmation regardless of Redis, so the
    # deterministic close must know not to send its own second text.
    import app.storage.redis_store as rs

    async def _boom(**kw):
        raise RuntimeError("redis down")

    monkeypatch.setattr(rs, "create_pending_name_confirmation", _boom)
    session = {"clinic_id": "northgate"}
    ok = await chase.start(
        session=session, clinic={"clinic_id": "northgate"}, phone="07502211207",
        patient_name="Still", provider="acuity", appointment_id="1",
        when_label=LABEL, location="Didsbury",
    )
    assert ok is False
    assert session["_name_chase_open"] is True
