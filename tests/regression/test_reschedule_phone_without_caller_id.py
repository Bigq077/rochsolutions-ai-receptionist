"""
A caller with no number must still be able to reschedule or cancel.

Turn 2 of the RESCHEDULE / CANCEL FLOW was written assuming a caller ID always
exists:

    "You ALREADY HAVE the number — it is the caller phone in CALL STATE,
     pre-loaded from caller ID — so do NOT ask them for it … Say the digits in
     three groups"

For a caller who withholds their number, or whose caller ID is suppressed,
that is false — and it CONTRADICTED the CALL STATE block, which correctly says
"you do NOT have a number for them". The same prompt told the model both
things, and which one won was a coin-flip on a live flow.

It could not self-correct either. The keypad line in that flow is gated behind
"Only if the caller DECLINES that number", and no number was ever offered to
decline. The model's remaining options were to invent digits to read back, or
to ask the open question the same paragraph explicitly forbids.

This matters more than the equivalent booking bug: lookup_patient keys on
phone. Without a number there is no way to reach the appointment at all, so
this is the difference between a withheld caller being able to cancel and not.

The booking flow's step 8 already had the (a)/(b) split. This gives the
reschedule/cancel flow the same one.
"""

import pytest

from app.prompts.susie_system_prompt import build_system_prompt


def _prompt(*, cli: bool, intent: str = "reschedule") -> str:
    session = {
        "clinic_id": "theorem_v3",
        "collected": {},
        "turn_count": 2,
        "v3_caller_intent": intent,
    }
    if cli:
        session["twilio_from"] = "+447760512084"
        session["twilio_from_local"] = "07760512084"
    else:
        session["twilio_from"] = ""
    return build_system_prompt(session)


# ── no caller ID: the branch that did not exist ────────────────────────────

@pytest.mark.parametrize("intent", ["reschedule", "cancel"])
def test_no_contradiction_when_there_is_no_number(intent):
    """
    The whole defect in one assertion: the prompt must not simultaneously say
    "you do NOT have a number" and "you ALREADY HAVE the number".
    """
    p = _prompt(cli=False, intent=intent)
    assert "NO caller ID on this call" in p, "CALL STATE no longer reports the absence"
    assert "You ALREADY HAVE the number" not in p, (
        "the reschedule flow still claims a number we do not have — the model "
        "is being told both things at once"
    )


def test_turn_2_asks_for_the_keypad():
    """Turn 2 must still end in a question, and it must be the right one."""
    p = _prompt(cli=False)
    assert "type the number the" in p
    assert "star key" in p


def test_it_forbids_the_three_wrong_recoveries():
    """
    Inventing digits, offering a number that does not exist, and asking an
    open question are each explicitly ruled out — the last one because the
    keypad exists precisely to stop callers saying digits aloud.
    """
    p = _prompt(cli=False)
    assert "There is nothing to read back" in p
    assert "do NOT say any digits" in p.replace("Do NOT", "do NOT")
    assert "what number was it booked under" in p


# ── caller ID present: nothing may change ──────────────────────────────────

def test_the_caller_id_path_is_untouched():
    """
    The existing flow is well-tuned and live. This change must be invisible to
    every caller whose number we hold.
    """
    p = _prompt(cli=True)
    assert "You ALREADY HAVE the number" in p
    assert "Only if the caller DECLINES" in p
    assert "You do NOT have a number for this caller" not in p


def test_the_two_branches_are_mutually_exclusive():
    """Exactly one of them renders, never both and never neither."""
    for cli in (True, False):
        p = _prompt(cli=cli)
        has_have = "You ALREADY HAVE the number" in p
        has_none = "You do NOT have a number for this caller" in p
        assert has_have != has_none, (
            f"cli={cli}: expected exactly one branch, got have={has_have} "
            f"none={has_none}"
        )


# ── the flow still works downstream ────────────────────────────────────────

def test_lookup_still_keys_on_phone_after_the_keypad():
    """
    The point of collecting the number is the lookup. Whichever branch runs,
    the instruction to call lookup_patient with it must survive.
    """
    for cli in (True, False):
        assert "lookup_patient(purpose='reschedule'" in _prompt(cli=cli)


def test_the_keypad_wording_matches_the_booking_flow():
    """
    Same sentence shape as the booking flow's keypad ask, so the keypad-arming
    predicate and the phone-step markers behave identically in both flows.
    """
    from app.media_streams.connection import _is_keypad_arming_line

    ask = (
        "Right, Awlstuh. Could you type the number the appointment is booked "
        "under on your keypad? You can press the star key to reset at any time."
    )
    assert _is_keypad_arming_line(ask), (
        "the reschedule keypad ask would not arm the keypad — typed digits "
        "would land in a closed buffer"
    )
    assert ask.split(". ", 1)[1] in _prompt(cli=False), (
        "the wording drifted from what this test verified arms the keypad"
    )
