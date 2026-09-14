"""CA29c06f3309 (14 Sep 2026, demo, build 436480a1) — proving #1b live.

    Susie   "thanks Gardner — and your surname?"
    Caller  "gardner"
    Susie   "I've got you on oh seven five … is that the best number?"
    …
    Susie   "so that's Gardner Gardner, Monday … shall I go ahead?"   <-- heard
    Susie   "all booked … I didn't quite catch your name, so I'm texting
             you now"                                                <-- too late

The booking gate (6ac7a989) collapsed the name at the WRITE, so the diary,
the text and the alert are right. But the caller heard the doubled name
read back and was told about the text only at the close. Owner, 14 Sep: say
it the moment the surname comes back as the same word — Gate 5n-e, the
same exit 5n / 5n-c / 5n-d speak, followed by the step outstanding.
"""
from app.media_streams.turn_handler import (
    _GATE5N_EXIT_LINE,
    _surname_is_the_first_name_again,
    sanitise_response,
)

SURNAME_ASK = "Sorry about that — thanks Gardner — and your surname?"


def _session_after_the_surname_ask(caller="gardner"):
    return {
        "clinic_id": "northgate",
        "patient_name": "Gardner",
        "collected": {"name": "Gardner", "phone": "07502211207", "reason": "knee"},
        "conversation_history": [
            {"role": "user", "content": "no i said my name is gardner"},
            {"role": "assistant", "content": SURNAME_ASK},
        ],
        "_turn_user_text": caller,
        "_turn_serial": 9,
        "selected_slot": "2026-09-14T13:50:00",
    }


def test_the_repeated_first_name_is_not_a_surname():
    s = _session_after_the_surname_ask()
    assert _surname_is_the_first_name_again(s, "gardner")
    assert _surname_is_the_first_name_again(s, "um it's gardner")
    assert _surname_is_the_first_name_again(s, "gardner gardner")
    assert not _surname_is_the_first_name_again(s, "smith")
    assert not _surname_is_the_first_name_again(s, "gardner smith")


def test_the_exit_is_spoken_on_the_surname_turn_not_at_the_close():
    s = _session_after_the_surname_ask()
    out = sanitise_response(
        "I've got you on oh seven five oh two, two one one, two oh seven — "
        "is that the best number for the booking?", s,
    )
    assert out.startswith(_GATE5N_EXIT_LINE), out
    assert "?" in out, "the outstanding step follows the exit"
    assert s["patient_name"] == "Gardner"
    assert s["collected"]["name"] == "Gardner"
    assert s["needs_name_correction_sms"] is True
    assert s["_gate5n_exited"] is True


def test_a_real_surname_is_left_alone():
    s = _session_after_the_surname_ask(caller="smith")
    reply = "Thanks — is that the best number for the booking?"
    assert sanitise_response(reply, s) == reply
    assert "_gate5n_exited" not in s


def test_it_fires_once_per_turn():
    s = _session_after_the_surname_ask()
    first = sanitise_response("I've got you on oh seven — is that the best number?", s)
    second = sanitise_response("Shall I go ahead and book that in?", s)
    assert first.startswith(_GATE5N_EXIT_LINE)
    assert second == "", "the rest of the model's reply this turn is dropped"
