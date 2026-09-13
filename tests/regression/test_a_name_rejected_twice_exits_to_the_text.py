"""Gate 5n-c: a name read-back rejected twice exits to the text.

CA499ca674 (northgate demo line, 13 Sep 2026, build 9074e9ea). The caller
rejected Susie's name read-back SIX times over 95 seconds; the model never
said "spell" or "keypad" -- the prompt forbids them -- so Gate 5n, which keys
on those words, never fired. The prompt's own "after two full attempts ...
continue with a placeholder" was advisory and ignored. The model then called
transfer_to_human for a name problem, the demo line has no transfer target,
and the call ended with no booking.

Owner rule (13 Sep 2026): one re-ask, then best effort + text. 5n-c makes
the SECOND rejection of a name read-back the trigger, regardless of the
model's wording. The exit is the same one Gate 5n speaks, so everything
downstream (5n-b, the name chase) is unchanged.
"""
from __future__ import annotations

import pytest

from app.media_streams.turn_handler import sanitise_response

EXIT = "No problem — I'll pop what I've got on the booking, and I'll text you after the call"
CLI = "07502211207"


def _session(**over):
    s = {
        "clinic_id": "northgate", "twilio_from_local": CLI,
        "booking_flow_active": True, "slots_presented": True,
        "selected_slot_speech": "Monday the 14th of September at ten to two",
        "conversation_history": [],
    }
    s.update(over)
    return s


def _turn(s, susie_before: str, caller: str, model_draft: str) -> str:
    """One turn as llm_stream drives it: the previous assistant turn is in
    history, the caller's utterance is `_turn_user_text`, and the model's
    draft goes through sanitise_response. History is appended afterwards."""
    if susie_before:
        s["conversation_history"].append({"role": "assistant", "content": susie_before})
    s["_turn_user_text"] = caller
    s["_turn_serial"] = int(s.get("_turn_serial") or 0) + 1
    out = sanitise_response(model_draft, s)
    s["conversation_history"].append({"role": "user", "content": caller})
    return out


# ── The call, replayed ─────────────────────────────────────────────────────

def test_ca499_the_second_rejection_exits_and_the_booking_proceeds():
    s = _session()

    # Attempt 1.
    out = _turn(s, "That one works — could I take your first name and surname?",
                "um ciao mera", "Did you say Ciao — is that right?")
    assert out == "Did you say Ciao — is that right?"
    assert not s.get("_gate5n_exited")

    # Rejection #1: the one re-ask the owner allows.
    out = _turn(s, out, "no i said um", "Take your time — go ahead.")
    assert out == "Take your time — go ahead."
    assert s.get("_gate5nc_rejections") == 1
    assert not s.get("_gate5n_exited")

    # Attempt 2: heard as a name; the engine persists it and moves on.
    out = _turn(s, out, "i just told you it's jaumeira marrows",
                "Thanks Jaumeira — so that's Monday the 14th of September at ten "
                "to two. I've got you on oh seven five oh two, two one one, two oh "
                "seven — is that the best number for the booking?")
    s["patient_name"] = "Jaumeira Marrows"          # what _v3_try_persist_name does
    s.setdefault("collected", {})["name"] = "Jaumeira Marrows"
    assert out.startswith("Thanks Jaumeira")
    assert s.get("_gate5nc_rejections") == 1, "a name given without a 'no' is an attempt, not a rejection"

    # Rejection #2 -- arrived as a barge-in on the phone question. Today the
    # model said "No problem — did you say Jaumeira — is that right?" and the
    # loop ran four more times. Now: the exit, and the step outstanding.
    out = _turn(s, out, "no it's not jaumeira it's not jaumeira it's jaumeira rosnowski",
                "No problem — did you say Jaumeira — is that right?")
    assert out.startswith(EXIT), out
    assert "did you say" not in out.lower()
    assert "best number" in out.lower(), "the phone step is what is outstanding"
    assert "0 7 5 0 2" in out
    assert s["_gate5n_exited"] is True
    assert s["needs_name_correction_sms"] is True
    assert s["patient_name"] == "Jaumeira", "best effort from the caller's last attempt"
    assert s["_gate5n_best_effort_name"] == "Jaumeira"

    # After the exit the model tries the confirm again: removed, once-only
    # exit, and the placeholder is not spoken (5n-b).
    out = _turn(s, out, "jaumeira rosnowski",
                "Thanks Jaumeira — so that's Monday the 14th of September at ten to "
                "two. I've got you on oh seven five oh two — is that the best number?")
    assert "Jaumeira" not in out, out
    assert out.count(EXIT) == 0, "the promise is made once"
    assert "best number" in out.lower()

    out = _turn(s, out, "no that's wrong",
                "Did you say Jaumeira Rybowski — is that right?")
    assert "did you say" not in out.lower(), out
    assert "Jaumeira" not in out
    assert "?" in out, "a turn that asks nothing is dead air"
    assert "best number" in out.lower() or "use this number" in out.lower()


# ── What must NOT count ────────────────────────────────────────────────────

def test_a_no_to_the_phone_question_is_not_a_name_rejection():
    s = _session(patient_name="Sarah Jones", collected={"name": "Sarah Jones"})
    out = _turn(s, "Thanks Sarah — is oh seven five oh two the best number for you?",
                "no use my mobile it's oh seven seven", "No problem — go ahead with the number.")
    assert out == "No problem — go ahead with the number."
    assert not s.get("_gate5nc_rejections")


def test_a_no_to_anything_but_a_name_is_not_counted():
    s = _session()
    out = _turn(s, "Do you have a preference for when you'd like to come in?",
                "no not really", "Let me have a look at what's free —")
    assert not s.get("_gate5nc_rejections")


def test_one_correction_that_lands_does_not_exit():
    s = _session()
    _turn(s, "Could I take your first name and surname?", "sara jones",
          "Did you say Sarah — is that right?")
    out = _turn(s, "Did you say Sarah — is that right?", "no it's sara without the h",
                "Sara — got it. Is oh seven five oh two the best number for you?")
    assert s.get("_gate5nc_rejections") == 1
    assert not s.get("_gate5n_exited")
    assert out.startswith("Sara — got it.")


def test_the_same_utterance_is_counted_once_across_chunks():
    s = _session()
    s["conversation_history"].append(
        {"role": "assistant", "content": "Did you say Ciao — is that right?"})
    s["_turn_user_text"] = "no that's wrong"
    s["_turn_serial"] = 3
    sanitise_response("Sorry about that —", s)
    sanitise_response("could you say it once more?", s)
    assert s.get("_gate5nc_rejections") == 1


def test_the_same_words_on_the_next_turn_count_again_and_are_not_dropped():
    # "no" twice in a row is two turns, not one: the serial tells them apart.
    s = _session()
    s["conversation_history"].append({"role": "user", "content": "zimara roshnevowski"})
    s["conversation_history"].append(
        {"role": "assistant", "content": "Did you say Ciao — is that right?"})
    s["_turn_user_text"] = "no"
    s["_turn_serial"] = 3
    sanitise_response("Sorry — could you say it once more?", s)
    s["conversation_history"].append(
        {"role": "assistant", "content": "Did you say Zimara — is that right?"})
    s["_turn_serial"] = 4
    out = sanitise_response("Sorry — one more time?", s)
    assert s["_gate5nc_rejections"] == 2
    assert out.startswith(EXIT)
    # the next turn, same words again, is spoken -- not dropped as "rest of the exit turn"
    s["_turn_serial"] = 5
    assert sanitise_response("Is oh seven five oh two the best number?", s) != ""


def test_a_rejection_after_the_booking_is_confirmed_is_ignored():
    s = _session(booking_confirmed=True, patient_name="Jaumeira",
                 collected={"name": "Jaumeira"})
    s["_gate5nc_rejections"] = 1
    out = _turn(s, "Thanks Jaumeira — you're all booked in.", "no that's wrong",
                "Sorry — what needs changing?")
    assert out == "Sorry — what needs changing?"
    assert not s.get("_gate5n_exited")


def test_rejection_of_a_spoken_name_needs_a_name_shaped_no():
    # After "Thanks X —" (not a yes/no question) a bare "no" is ambiguous;
    # only a no that is about the name counts.
    s = _session(patient_name="Jaumeira", collected={"name": "Jaumeira"})
    _turn(s, "Thanks Jaumeira — is oh seven five oh two the best number?",
          "no", "No problem — what's the best number?")
    assert not s.get("_gate5nc_rejections")
    _turn(s, "No problem — what's the best number?",
          "no it's not jaumeira", "Sorry — could you say your name once more?")
    assert s.get("_gate5nc_rejections") == 1


def test_the_prompt_and_the_gate_tell_the_same_story():
    from app.clinic_config import get_clinic
    from app.prompts.clinic_template_prompt import build_clinic_prompt

    sys_p, _ = build_clinic_prompt({"clinic_id": "northgate"}, get_clinic("northgate"))
    assert "the team will confirm your name when they get in touch" not in sys_p
    assert "text you after the call so you can reply with the spelling" in sys_p
    assert "rejected" in sys_p and "twice" in sys_p


# ── CAcb580641: the exit asks the step genuinely outstanding, and only it ──
#
# 13 Sep 2026, build 815ae8b1, the first call on 5n-c. Three times were on
# offer and none picked (STT garbled the pick; the engine kept the slot
# cache). The exit fired on the second rejection and asked for the PHONE --
# name -> phone was all _nk_outstanding_plain knew -- and then the rest of
# the model's reply, "Which of those times works best for you -- one in the
# afternoon, ten to two, or half past three?", leaked out behind it because
# sanitise_response runs per chunk. Two questions in one breath.

def _slots_on_offer(s):
    s["last_offered_slots"] = [
        {"start": "2026-09-14T13:00:00+01:00"},
        {"start": "2026-09-14T13:50:00+01:00"},
        {"start": "2026-09-14T15:30:00+01:00"},
    ]
    s["v3_dtmf_slot_map"] = {
        "1": "one in the afternoon", "2": "ten to two in the afternoon",
        "3": "half past three in the afternoon",
    }
    s["v3_dtmf_slot_context"] = "time"
    s.pop("selected_slot", None)
    s.pop("_accepted_slot_record", None)
    return s


def test_cacb58_the_exit_asks_for_the_time_when_none_is_picked_and_nothing_else():
    s = _slots_on_offer(_session())
    _turn(s, "That one works — could I take your first name and surname?",
          "um yeah that'll be zimara roshnevowski",
          "Thanks, got that — did you say Zimara — is that right?")
    _turn(s, "Thanks, got that — did you say Zimara — is that right?",
          "no i didn't i said zimara roshnevowski",
          "Sorry about that — Thanks Zimara — just a moment while I check what's available.")
    assert s["_gate5nc_rejections"] == 1

    # Rejection #2 arrives; the model's reply streams in three chunks.
    s["conversation_history"].append({"role": "assistant", "content":
        "You're right, sorry about that. So that's Zimara Roshnevowski, Monday "
        "the 14th of September — which time works best for you?"})
    s["_turn_user_text"] = "no that's wrong no that's wrong it's it's zimara roshnowski"
    s["_turn_serial"] = int(s.get("_turn_serial") or 0) + 1
    c1 = sanitise_response("Not to worry — I've got you down as Zimara.", s)
    c2 = sanitise_response("Which of those times works best for you —", s)
    c3 = sanitise_response("one in the afternoon, ten to two, or half past three?", s)

    assert c1.startswith(EXIT), c1
    assert c1.endswith(
        "Which of those times works best for you — one in the afternoon, "
        "ten to two in the afternoon, or half past three in the afternoon?"
    ), c1
    assert "best number" not in c1.lower(), "the time is outstanding, not the phone"
    assert c1.count("?") == 1, "one question"
    assert c2 == "" and c3 == "", "the rest of the model's reply is dropped"

    # The next turn is a new utterance: nothing is dropped.
    s["conversation_history"].append({"role": "user", "content": s["_turn_user_text"]})
    s["conversation_history"].append({"role": "assistant", "content": c1})
    s["_turn_user_text"] = "ten to two"
    s["_turn_serial"] += 1
    out = sanitise_response("Ten to two it is — is oh seven five oh two the best number for you?", s)
    assert out.startswith("Ten to two it is")


def test_the_exit_asks_for_the_phone_once_a_time_is_picked():
    s = _slots_on_offer(_session())
    s["selected_slot"] = "2026-09-14T13:50:00+01:00"
    s["_gate5nc_rejections"] = 1
    s["conversation_history"].append({"role": "user", "content": "it's zimara roshnevowski"})
    s["conversation_history"].append(
        {"role": "assistant", "content": "Did you say Zimara — is that right?"})
    s["_turn_user_text"] = "no that's wrong"
    s["_turn_serial"] = 5
    out = sanitise_response("Sorry — could you say it once more?", s)
    assert out.startswith(EXIT)
    assert "best number" in out.lower()
    assert "which of those" not in out.lower()


def test_the_bare_slot_question_when_the_map_is_not_a_time_map():
    from app.media_streams.turn_handler import _slot_question_for

    assert _slot_question_for({"v3_dtmf_slot_map": {"1": "Monday", "2": "Tuesday"},
                               "v3_dtmf_slot_context": "day"}) == "Which of those works best for you?"
    assert _slot_question_for({"v3_dtmf_slot_map": {"1": "ten to two"},
                               "v3_dtmf_slot_context": "time"}) == (
        "Which of those times works best for you — ten to two?")
