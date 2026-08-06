"""
A withheld caller ID must produce a keypad ask — never an offer to use a number
that does not exist, and never a booking with no number at all.

CA4ab554ce0dd8530e40df3cb8f7e28588, 2026-08-06. The caller withheld their
number, so twilio_from was blanked at call start (d063680, correctly). Then:

    22:30:51  "Thanks Amir — is the number you're calling from the best one
               for the booking? If so, just say use this number."
    22:31:01  caller: "um use this number"
    22:31:03  → straight to the slot readback
              → "shall I go ahead and book that in?"
    22:31:27  [smart_sms] 📵 No phone number — skipping SMS
              📊 outcome=reached_confirmation  phone=no

She asked a question with no possible answer, the caller answered it, and the
call arrived one "yes" away from a booking carrying no phone number.

Two independent holes, one each side of the model:

  C — CALL STATE said NOTHING. `caller phone (pre-loaded from caller ID): …`
      was simply omitted when there was no caller ID. The static prompt has
      carried a correct branch for this since the port — "CALL STATE SHOWS NO
      calling number … go STRAIGHT to the keypad line" — but the model had no
      way to distinguish "no caller ID" from "not mentioned". Absence is not an
      instruction. CALL STATE now says so in words.

  D — the code let the "yes" through. The verbal phone-confirm intercept
      already refused to store an empty number, so nothing wrong was written —
      but it then fell through silently to run_turn, and the model carried on.
      The book_appointment backstop would NOT have caught it either: that
      blocks only when phone_confirmed is unset AND the phone question was
      never asked, and she had asked it. The unsure-ladder does not catch it
      either — "use this number" verdicts as yes, not unsure.

C is advice to a model. D is the one that guards the booking.
"""

import inspect

import pytest

from app.media_streams import connection as c
from app.prompts.susie_system_prompt import _build_theorem_v3


BASE = {
    "clinic_id": "theorem_v3",
    "collected": {},
    "selected_location": "alcester",
    "v3_location_confirmed": True,
}


def _call_state(**overrides) -> str:
    session = dict(BASE)
    session.update(overrides)
    return _build_theorem_v3(session)[1]


# ── C: CALL STATE must say there is no number ──────────────────────────────


def test_no_caller_id_is_stated_not_omitted():
    """The whole defect: the key was absent, and absence taught the model
    nothing. It must be present and negative."""
    state = _call_state(twilio_from_local="")
    assert "NO caller ID on this call" in state


def test_the_forbidden_offer_is_named_verbatim():
    """The sentence she actually said is the one the instruction must forbid,
    in the words she used — a paraphrase would not have stopped it."""
    state = _call_state(twilio_from_local="")
    assert "just say use this number" in state
    assert "keypad" in state


def test_a_normal_call_is_untouched():
    """A caller ID that exists must still be pre-loaded, and must NOT pick up
    the withheld-number instruction."""
    state = _call_state(twilio_from_local="07700900123")
    assert "07700900123" in state
    assert "NO caller ID on this call" not in state


def test_a_number_already_collected_suppresses_the_warning():
    """If the caller keyed a number in, there is no missing number to warn
    about — the branch is for the phone step, not for the whole call."""
    session = dict(BASE)
    session["collected"] = {"phone": "07700900456"}
    state = _build_theorem_v3(session)[1]
    assert "NO caller ID on this call" not in state


def test_the_static_branch_this_relies_on_still_exists():
    """C only works because the static prompt already knows what to do when
    CALL STATE reports no number. If that branch is ever removed, the CALL
    STATE line becomes an orphan fact with no instruction attached."""
    static = _build_theorem_v3(dict(BASE))[0]
    assert "CALL STATE SHOWS NO calling number" in static


# ── D: the code must not let the "yes" through ─────────────────────────────


def test_yes_with_no_caller_id_takes_the_turn():
    """
    Anchored on the log prefix, which appears only at the call site. The
    branch must ask for the keypad and stop the turn — not fall through to
    run_turn as it did on CA4ab554ce.
    """
    src = inspect.getsource(c)
    # Anchored on the branch head — it is reached only when the phone question
    # is genuinely on the table, and this spelling is unique in the file (the
    # store branch above reads `elif _bk_caller_num and _bk_phone_step:`).
    at = src.index("elif _bk_phone_step:")
    window = src[at:at + 4600]

    assert "[ms_conn v3] caller confirmed the calling " in window
    # …it asks for the keypad, in words that still register as the phone step…
    assert "type the number" in window
    # …it arms DTMF capture so the digits have somewhere to land…
    assert 'self.session["v3_phone_dtmf_active"] = True' in window
    assert 'self.session["phone_awaiting_dtmf"] = True' in window
    # …it speaks…
    assert "self.tts_text_queue.put(_no_cli_ask)" in window
    # …and it does NOT fall through to the LLM.
    assert "continue" in window


def test_the_branch_never_fires_when_a_number_exists():
    """
    Ordering is the guard: the store-the-caller-ID branch is tested first, so
    a normal call can never reach the keypad ask. If these two are ever
    reordered, every caller with a working caller ID gets asked to type it in.
    """
    src = inspect.getsource(c)
    store = src.index("elif _bk_caller_num and _bk_phone_step:")
    ask = src.index("elif _bk_phone_step:")
    assert store < ask, (
        "the no-caller-ID branch now precedes the store branch — a caller "
        "whose number we hold would be asked to key it in"
    )


@pytest.mark.parametrize("utterance", [
    "use this number",
    "um use this number",
    "yeah use this number",
])
def test_the_utterance_that_did_it_still_reads_as_yes(utterance):
    """
    The branch hangs off _phone_confirm_is_yes. If that ever stops matching
    "use this number", the new guard goes quiet and the call falls through
    again — which is exactly the failure, just via a different door.
    """
    assert c._phone_confirm_is_yes(utterance), (
        f"{utterance!r} no longer verdicts as yes — the no-caller-ID guard "
        "is now unreachable for the phrase that caused the incident"
    )
