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
    s["patient_name"] = "Zimara Roshnevowski"       # persisted on the call at 19:25:41
    s.setdefault("collected", {})["name"] = "Zimara Roshnevowski"

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


# ── CA70a83e8c: once a name is on record, rejections still count and the ──
# ── keypad ask is still impossible ─────────────────────────────────────────
#
# 13 Sep 2026, build 81830418. The model persisted a name at the first
# attempt and read it back. The caller then rejected it three times in
# words the gate did not recognise -- "the name's not X", "no wrong again X",
# "oh wrong wrong wrong" -- so 5n-c counted nothing; and Gate 5n, which
# stood down whenever a name was on record, let "could you type your surname
# on the keypad?" through twice. The call ended without a booking.

def test_ca70a8_rejections_of_a_stored_name_count_in_the_callers_own_words():
    s = _session(patient_name="Zimara Morawski", collected={"name": "Zimara Morawski"})
    s["selected_slot"] = "2026-09-16T08:50:00+01:00"
    _turn(s, "Thanks Zimara — so that's Zimara Morawski. I've got you on oh seven "
             "five oh two, two one one, two oh seven — is that the best number for the booking?",
          "no actually not that",
          "No problem — go ahead and type the number on your keypad.")
    assert not s.get("_gate5nc_rejections"), "ambiguous after a phone question: not counted"

    out = _turn(s, "No problem — go ahead and type the number on your keypad.",
                "the name the name's not zimara morawski the name's zimara gronkowski",
                "Got it — Zimara Gronkowski. So that's Zimara Gronkowski, is oh seven "
                "five oh two the best number for you?")
    assert s["_gate5nc_rejections"] == 1
    assert not s.get("_gate5n_exited")

    out = _turn(s, out, "no wrong again zimara gronkowski",
                "Zimara Gronkowski. I've got you on oh seven five oh two — is that the best number?")
    assert s["_gate5nc_rejections"] == 2
    assert out.startswith(EXIT), out
    assert "best number" in out.lower()
    assert "Gronkowski" not in out and "Zimara" not in out


def test_ca70a8_wrong_wrong_wrong_to_the_phone_question_is_ambiguous_and_not_counted():
    # The name was spoken in the same breath as the phone question; "wrong"
    # with no name in it answers the question in front of it. The trade
    # (CAd63554bb): a phone "no it's not" during a dispute must not exit.
    # In the real call the exit had already fired on "no wrong again zimara
    # gronkowski", which names the name.
    s = _session(patient_name="Zimara Gronkowski", collected={"name": "Zimara Gronkowski"})
    s["_gate5nc_rejections"] = 1
    s["conversation_history"].append({"role": "user", "content": "no wrong again zimara gronkowski"})
    out = _turn(s, "Zimara Gronkowski. I've got you on oh seven five oh two — is that the best number?",
                "oh wrong wrong wrong",
                "Thanks for that — I'm sorry about that — could you type your surname on the keypad?")
    assert s["_gate5nc_rejections"] == 1
    # ...and Gate 5n still removes the keypad-for-a-name ask on its own.
    assert "surname" not in out.lower(), out
    assert s.get("_gate5n_exited") is True


# ── CAd63554bb: the placeholder is a name, and a phone no in a dispute ────

def test_cad635_the_best_effort_is_the_last_name_attempt_not_the_first_word_of_a_complaint():
    s = _session(patient_name="Kiera", collected={"name": "Kiera"})
    s["selected_slot"] = "2026-09-15T13:50:00+01:00"
    _turn(s, "Thanks Kiera — I've got you on oh seven five oh two, two one one, two oh seven — "
             "is that the best number for the booking?",
          "no that's wrong that's wrong not kiera it's kiera",
          "No problem — got it — and the number, oh seven five oh two — is that right?")
    assert s["_gate5nc_rejections"] == 1
    out = _turn(s, "No problem — got it — and the number, oh seven five oh two — is that right?",
                "no what was the name you got danny", "Not to worry — I've got you as Kiera.")
    assert out.startswith(EXIT)
    assert s["patient_name"] == "Kiera", "not 'What'"
    assert s["collected"]["name"] == "Kiera"


def test_cad635_a_phone_no_during_a_dispute_does_not_count():
    s = _session(patient_name="Kiera", collected={"name": "Kiera"})
    s["_gate5nc_rejections"] = 1
    _turn(s, "Got it. Is 0 7 5 0 2 2 1 1 2 0 7 the best number for you? If so, just say use this number.",
          "uh no it's not", "No problem — go ahead and type the number on your keypad.")
    assert s["_gate5nc_rejections"] == 1
    assert not s.get("_gate5n_exited")


def test_a_keypad_ask_for_a_name_is_removed_even_when_a_name_is_on_record():
    s = _session(patient_name="Zimara Gronkowski", collected={"name": "Zimara Gronkowski"})
    s["conversation_history"].append({"role": "user", "content": "it's zimara gronkowski"})
    s["_turn_user_text"] = "oh wrong wrong wrong"
    s["_turn_serial"] = 9
    out = sanitise_response("I'm sorry about that — could you type your surname on the keypad?", s)
    assert "keypad" not in out.lower() or "number" in out.lower(), out
    assert "surname" not in out.lower()
    assert s.get("_gate5n_exited") is True


def test_spell_in_another_context_is_left_alone_when_a_name_is_on_record():
    s = _session(patient_name="Sarah Jones", collected={"name": "Sarah Jones"})
    s["_turn_user_text"] = "how do you spell the road name"
    s["_turn_serial"] = 4
    line = "It's spelled B-U-R-T-O-N Road — shall I text you the address?"
    assert sanitise_response(line, s) == line
    assert not s.get("_gate5n_exited")


# ── From the corpus (911 caller turns after a name turn, 13 Sep 2026) ─────

@pytest.mark.parametrize("susie,caller", [
    # "Thanks Quentin —" then the phone question: the no is about the number.
    ("Thanks Quentin — I've got you on 07502 211 207, is that the best number for the booking?",
     "no it's not"),
    ("Thanks Quentin — I've got you on 07502 211 207, is that the best number for the booking?",
     "um no it's a different number"),
    ("Is the number you're calling on the best one for your booking? If so, just say use this number.",
     "no it's not i'll say it verbally"),
    # A booking CTA that read the name back: the no is about the booking.
    ("So that's Quentin, Monday the 24th of August at half past seven — shall I go ahead and book that in?",
     "No"),
    # An ASK is not a read-back.
    ("Monday the 14th at twenty to twelve — could I take your first name and surname?",
     "actually no hang on um do you have anything else on monday"),
    ("Before I do that — could I take your first name and surname?",
     "uh yeah quentin rock did you not already have it"),
    ("Could I take your first name and surname?", "could you say that again please"),
])
def test_corpus_turns_that_are_not_name_rejections(susie, caller):
    s = _session(patient_name="Quentin Rook", collected={"name": "Quentin Rook"})
    _turn(s, susie, caller, "Right —")
    assert not s.get("_gate5nc_rejections"), (susie, caller)


@pytest.mark.parametrize("susie,caller", [
    ("Did you say Home — is that right?", "no tom green"),
    ("Did you say Courts — is that right?", "no"),
    ("Did you say Quensing — is that right?", "i said quensing rock"),
    ("Did you say Cold — is that right?", "no sorry my surname is thompson t-h-o-m-p-s-o-n"),
    ("Sorry, I didn't catch that. Did you say Quite — is that right?", "my name's quentin rook"),
    ("Thanks Jackhammer — and your surname?", "no it's jackamo"),
    ("Did you say Gel — is that right?", "no it's gel marrow"),
    # The name read back and then the phone asked: the no names the name.
    ("Thanks Quentin — I've got you on 07502 211 207, is that the best number for the booking?",
     "no it's not quentin it's quinton"),
    ("So that's Quentin Rook, Monday the 10th of August at quarter past five — shall I go ahead and book that in?",
     "um it's not quentin rook it's quentin roch r-o-c-h"),
])
def test_corpus_turns_that_are_name_rejections(susie, caller):
    s = _session(patient_name="Quentin Rook", collected={"name": "Quentin Rook"})
    _turn(s, susie, caller, "Right —")
    assert s.get("_gate5nc_rejections") == 1, (susie, caller)
