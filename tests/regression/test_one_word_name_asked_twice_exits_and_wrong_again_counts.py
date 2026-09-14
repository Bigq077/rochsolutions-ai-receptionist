"""Two demo calls on 991d8864 (14 Sep 2026) — the realistic name failures.
Owner rule (13 Sep): one re-ask, then the best effort goes on the booking
and the spelling is chased by text.

CA1cdbe346b4 12:31 — ONE WORD, asked for the other half four times:

    Susie   "could I take your first name and surname?"
    Caller  "my name is gardner"
    Susie   "Thanks — and your first name?"                  ask #1 (fine)
    Caller  "gardner"
    Susie   "could I take your first name as well…?"         ask #2 → EXIT
    Caller  "yeah that's gardner"
    Susie   "is Gardner your first name or your surname?"    ask #3
    Caller  "it's my full name gardner gardner"
    Susie   "so that's Gardner Gardner noted."               heard

5n-d counted none of these (no "didn't catch / again"). Gate 5n-f: while
one word of the name is on record, the second model turn that asks for a
name part is replaced by the 5n exit, with that word.

CA50437bb292 12:34 — READ BACK WRONG three times, one rejection counted:

    Susie   "so that's Emma Ojma. I've got you on 0750… best number?"
    Caller  "no wrong again wrong again"                     not counted
    Susie   "could you say your surname again?" → "good about"
    Susie   "so that's Emma Goodabout. I've got you on 0750… best number?"
    Caller  "no wrong again wrong again"                     not counted

The tail of Susie's turn asked about the NUMBER, so the dispute rule read
the "no" as a number answer (CAd63554bb). But she read a NEW name back in
that same breath and the caller said "wrong": that is about the name.
"""
from app.media_streams.turn_handler import (
    _GATE5N_EXIT_LINE,
    _is_name_rejection,
    sanitise_response,
)


def _s(**kw):
    s = {"clinic_id": "northgate", "collected": {"phone": "07502211207", "reason": "ankle"},
         "conversation_history": [], "selected_slot": "2026-09-22T08:50:00", "_turn_serial": 1}
    s.update(kw)
    return s


def _turn(s, caller, susie):
    """One model turn: caller spoke, Susie's reply passes the gates."""
    s["_turn_serial"] += 1
    s["_turn_user_text"] = caller
    out = sanitise_response(susie, s)
    s["conversation_history"] += [{"role": "user", "content": caller},
                                  {"role": "assistant", "content": out}]
    return out


# ── CA1cdbe346b4: one word, asked for the other half ────────────────────────

def test_the_second_ask_for_the_other_half_exits_with_the_word_we_have():
    s = _s(patient_name="Gardner")
    s["collected"]["name"] = "Gardner"
    first = _turn(s, "yeah my name is gardner", "Thanks — and your first name?")
    assert first == "Thanks — and your first name?", "ask #1 is always spoken"
    second = _turn(s, "gardner",
                   "Got it — so could I take your first name as well, just so "
                   "I've got the full name for the booking?")
    assert second.startswith(_GATE5N_EXIT_LINE), second
    assert "?" in second
    assert s["patient_name"] == "Gardner"
    assert s["needs_name_correction_sms"] is True
    assert s["_gate5n_exited"] is True


def test_a_first_name_or_surname_question_is_an_ask_for_the_other_half():
    s = _s(patient_name="Gardner")
    _turn(s, "gardner", "Thanks — and your first name?")
    out = _turn(s, "yeah that's gardner",
                "I want to make sure I've got both names — is Gardner your "
                "first name or your surname?")
    assert out.startswith(_GATE5N_EXIT_LINE)


def test_the_happy_path_is_untouched():
    """First name given, surname asked once, surname given: no exit."""
    s = _s(patient_name="Emma")
    out = _turn(s, "emma", "Thanks Emma — and your surname?")
    assert out == "Thanks Emma — and your surname?"
    s["patient_name"] = "Emma Smith"
    s["collected"]["name"] = "Emma Smith"
    out = _turn(s, "smith", "Thanks Emma — is that the best number for the booking?")
    assert "_gate5n_exited" not in s
    assert out.endswith("booking?")


def test_it_counts_once_per_turn():
    s = _s(patient_name="Gardner")
    _turn(s, "gardner", "Thanks — and your first name?")
    s["_turn_user_text"] = "gardner"
    sanitise_response("And your first name?", s)          # same turn, chunk 2
    assert s["_gate5nf_asks"] == 1


def test_the_exit_books_the_word_held_at_ask_one_not_the_latest_attempt():
    """CA95b498efb1 (14 Sep 2026, demo, 6e588f53): "I've" went in the diary."""
    s = _s()
    _turn(s, "uh gardener", "Thanks, got that — could I take your first name as well?")
    assert s["_gate5nf_word"] == "gardener"
    _turn(s, "i've got bowel", "Did you say Ivor Bowel — is that right?")
    out = _turn(s, "no that's not no that's not right i said i got bowel",
                "I'm sorry about that — could you say your first name again for me?")
    assert out.startswith(_GATE5N_EXIT_LINE), out
    assert s["patient_name"] == "Gardener"
    assert s["_gate5n_best_effort_name"] == "Gardener"


def test_a_two_rejection_exit_books_the_held_word_not_the_rejected_one():
    """CAb421b89c91 (14 Sep 2026, demo, 1bf58a35): booked as the rejected "Bowel"."""
    s = _s()
    _turn(s, "um yeah so that would be um gardner", "Thanks, got that — and your first name?")
    assert s["_gate5nf_word"] == "gardner"
    _turn(s, "i've got bowel", "Did you say Bowe — is that right?")
    _turn(s, "no that's not right i said i got bowel", "Sorry about that — did you say Bowel — is that right?")
    out = _turn(s, "no that's not right i said i got bowel", "Sorry — did you say Bowel?")
    assert out.startswith(_GATE5N_EXIT_LINE), out
    assert s["patient_name"] == "Gardner"


def test_a_held_word_that_was_itself_read_back_and_rejected_is_not_booked():
    s = _s()
    _turn(s, "god", "Thanks — and your first name?")
    assert s["_gate5nf_word"] == "god"
    _turn(s, "no my name is gardner", "Did you say God — is that right?")
    out = _turn(s, "no that's wrong it's gardner", "Sorry — did you say God?")
    assert out.startswith(_GATE5N_EXIT_LINE), out          # rejection #2
    assert s["_gate5nf_word_rejected"] is True
    assert s["patient_name"] == "Gardner"


def test_a_pronoun_contraction_is_never_the_best_effort_name():
    from app.media_streams.turn_handler import _best_effort_name_from_history
    for said in ("i've got bowel", "i'm gardener", "uh ive got it"):
        got = _best_effort_name_from_history({"_turn_user_text": said,
                                              "conversation_history": []})
        assert got.lower() not in {"i've", "i'm", "ive", "got"}, (said, got)


# ── CA50437bb292: "wrong again" after a fresh read-back ─────────────────────

def test_wrong_again_after_a_new_readback_counts_even_when_the_tail_is_the_number():
    s = _s(patient_name="Emma Ojma", _gate5nc_rejections=1)
    s["conversation_history"] = [
        {"role": "user", "content": "no no that's wrong wrong name my name's emma ojma"},
        {"role": "assistant", "content": (
            "No problem — thanks Emma — so that's Emma Ojma. I've got you on oh "
            "seven five oh two, two one one, two oh seven — is that the best "
            "number for the booking?")},
    ]
    assert _is_name_rejection(s, "no wrong again wrong again")


def test_a_plain_no_to_the_number_question_is_still_about_the_number():
    """CAd63554bb: 'uh no it's not' answers the number, not the name."""
    s = _s(patient_name="Danny", _gate5nc_rejections=1)
    s["conversation_history"] = [
        {"role": "user", "content": "danny"},
        {"role": "assistant", "content": "Thanks Danny — is that the best number for the booking?"},
    ]
    assert not _is_name_rejection(s, "uh no it's not")
