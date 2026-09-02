"""
Gate 5g and Gate 5g-b must not depend on `booking_flow_active`.

CA9cc94a17f8182d62d7832250409cfd45 - 2026-09-02, northgate, the demo line.
130 seconds, outcome abandoned. Susie said, with neither a name nor a phone
number on record:

    14:47:25  "Saturday 12th September at twenty past twelve in the afternoon
               is available. Before I book that in for you, I'll need your
               full name and mobile number."

That sentence carries "book that in", so Gate 5g should have replaced it. It
did not fire, and neither did Gate 5g-b, because BOTH were gated on
`booking_flow_active` - and that flag was False for the entire call. There is
no "[ms_conn] booking_flow_active = True" line anywhere in the log.

WHY IT NEVER ARMED. Two arms can set it, and this caller missed both:

  * the speculative arm needs a treatment mention AND booking intent in the
    SAME utterance. The caller opened "yeah i booked an appointment" (intent,
    no treatment) and said "um just my left ankle nothing serious" on the next
    turn (treatment, no intent). One turn apart, so the AND never held.
  * the acknowledgement arm needs the caller to affirm a booking OFFER. No
    offer was ever made - they had opened by asking to book.

A caller who opens with "I'd like to book" is not an unusual caller.

WHY THE FIX IS NOT TO WIDEN THE FLAG. `booking_flow_active` is not a neutral
fact about the call. It renders "BOOKING FLOW ACTIVE" into the prompt's call
state, and per the Spec Y comment at the treatment-mention arm that marker is
"the sole thing that makes the LLM tack a 'day or time?' booking push onto
every later FAQ answer" - BUG-7, narrowed in an owner-signed frozen-zone
change on 2026-06-15. It answers "should the model push a booking?". The gates
were asking "is a booking being arranged?". Two different questions behind one
name, and the flag's deliberate narrowness silently became the gates'.

So the flag keeps its meaning and both gates stop reading it as their only
signal. They anchor instead on `slots_presented` - written in ONE place when
an offer is read out, never reset anywhere, and True on this call from
14:46:22. It is a fact about what the caller has heard rather than an
instruction to the model, so arming a guard with it cannot change what the
model is told to do.

GATE 5g WAS FIRST WRITTEN WITH NO ANCHOR AT ALL, on the argument that a write
CTA is its own evidence. That argument is sound about whether the gate should
fire and silent about what firing costs, which is the half that mattered: the
pattern spans [^.!?]* on both sides, so a readback joined to its CTA by a dash
instead of a full stop is deleted along with it. Twenty tests failed at once,
every one of them a fixture that had never reached an offer. The anchor keeps
that blast radius inside calls where the trade is worth making. See
test_a_dash_joined_readback_is_why_the_anchor_stays.
"""

import pytest

from app.media_streams.turn_handler import sanitise_response


# The sentence, verbatim from the call.
LIVE = (
    "Saturday 12th September at twenty past twelve in the afternoon is "
    "available. Before I book that in for you, I'll need your full name "
    "and mobile number."
)

# The session as it actually was: the flag never armed, but an offer had been
# read out at 14:46:22 ("[ms_llm] slots_presented=True slots_count=3").
LIVE_SESSION = {
    "booking_flow_active": False,
    "slots_presented": True,
    "twilio_from": "",
}


def test_the_live_call_is_caught():
    out = sanitise_response(LIVE, dict(LIVE_SESSION))
    assert "first name and surname" in out
    assert "mobile number" not in out
    # The slot itself is the caller's and must survive.
    assert "twenty past twelve" in out


# -- Gate 5g: anchored on the fact, not the instruction --------------------

def test_the_write_cta_is_held_back_on_slots_presented_alone():
    """Gate 5g no longer needs `booking_flow_active` - but it does still need
    an anchor, and this is the one it got.

    Dropping the anchor entirely was tried first, on the argument that a
    write CTA is its own evidence. That argument is about whether the gate
    SHOULD fire and says nothing about what firing COSTS. The pattern spans
    [^.!?]* on both sides, so a readback joined to its CTA by a dash rather
    than a full stop is eaten along with it - see
    test_a_dash_joined_readback_is_why_the_anchor_stays. Twenty tests failed
    at once, all of them fixtures that had never reached an offer."""
    out = sanitise_response(
        "So that's Monday at five. Shall I go ahead and book that in?",
        {"slots_presented": True, "twilio_from": ""},
    )
    assert "first name and surname" in out
    assert "book that in" not in out.lower()


def test_a_call_that_never_reached_an_offer_is_untouched():
    """The contract Gate 5g had before this change and keeps after it."""
    spoken = "So that's Monday at five. Shall I go ahead and book that in?"
    assert sanitise_response(spoken, {}) == spoken


def test_a_dash_joined_readback_is_why_the_anchor_stays():
    """What firing costs, stated so the anchor is not removed again.

    The substitution takes the whole sentence, and a dash does not end one.
    Inside a call that has reached an offer this is the accepted trade - the
    question the caller cannot act on is worse than the readback they lose.
    Outside one it is pure loss, which is what the anchor prevents."""
    spoken = (
        "So that's Sarah, Wednesday the 16th of August at ten in the "
        "morning - shall I go ahead and book that in?"
    )
    # Never reached an offer: untouched, readback intact.
    assert sanitise_response(spoken, {}) == spoken
    # Reached an offer: held back, and the readback goes with it.
    out = sanitise_response(spoken, {"slots_presented": True, "twilio_from": ""})
    assert "first name and surname" in out
    assert "Wednesday the 16th" not in out


def test_the_phone_question_follows_once_the_name_is_in():
    out = sanitise_response(
        "Shall I go ahead and book that in?",
        {"patient_name": "Quentin Rock", "slots_presented": True,
         "twilio_from": ""},
    )
    assert "keypad" in out.lower()


def test_the_gate_is_silent_once_both_steps_are_in():
    spoken = "Shall I go ahead and book that in?"
    session = {"patient_name": "Quentin Rock", "phone_confirmed": True,
               "slots_presented": True}
    assert sanitise_response(spoken, session) == spoken


# -- what keeps Gate 5g safe on the calls where it now fires ----------------
#
# The anchor decides WHICH CALLS it can fire on; _BOOKING_CTA_SENTENCE_RE
# still decides which SENTENCES. Every arm of it
# carries a booking verb, and the reschedule and cancel confirmations are
# excluded on purpose: they share the opener "shall I go ahead", and replacing
# a legitimate move or cancel confirmation with a request for a phone number
# would break a different flow outright.

@pytest.mark.parametrize("spoken", [
    "Shall I go ahead and move it for you?",
    "Shall I go ahead and cancel that for you?",
    "Shall I go ahead and check that for you?",
])
def test_other_write_families_are_untouched_without_the_flag(spoken):
    """Re-anchoring must not let this gate reach reschedule or cancel. Their
    CTAs share the opener and nothing else, and the anchor is deliberately
    SET here so the exclusion is proved on a gate that is armed rather than
    on one that was never going to fire."""
    session = {"slots_presented": True, "twilio_from": ""}
    assert sanitise_response(spoken, session) == spoken


@pytest.mark.parametrize("spoken", [
    "You're all booked in for Monday at five.",
    "I've booked that in for you.",
])
def test_a_completed_booking_is_not_rewritten_into_a_question(spoken):
    """"booked in" is a CLAIM about a finished write, not a request for one.
    It belongs to the false-confirmation guard and must not be turned into a
    request for the caller's name."""
    session = {"slots_presented": True, "twilio_from": ""}
    assert "first name and surname" not in sanitise_response(spoken, session)


# -- Gate 5g-b keeps an anchor, and a better one ---------------------------

def test_the_combined_question_is_caught_on_slots_presented_alone():
    out = sanitise_response(
        "Could I take your full name and mobile number?", dict(LIVE_SESSION)
    )
    assert "first name and surname" in out
    assert "mobile" not in out.lower()


def test_a_callback_still_asks_for_both():
    """The reason Gate 5g-b keeps an anchor where Gate 5g drops it. A write CTA
    is said in one situation; "can I take your name and number" is said in two,
    and the other one is a callback - where asking for both at once is correct.
    A callback flow never reads appointment times out, so it never sets
    `slots_presented`."""
    spoken = "I can take your name and number and have someone call you back?"
    session = {"booking_flow_active": False, "slots_presented": False}
    assert sanitise_response(spoken, session) == spoken


def test_booking_flow_active_still_arms_it_on_its_own():
    """The flag was not removed from this gate, only joined. A booking that
    arms it before any slot is read out is still governed."""
    out = sanitise_response(
        "Could I take your full name and mobile number?",
        {"booking_flow_active": True, "slots_presented": False, "twilio_from": ""},
    )
    assert "first name and surname" in out


# -- the arming shape that caused it, pinned so it cannot be misremembered --

def test_the_two_arming_signals_really_did_land_on_different_turns():
    """Not a test of the fix - a record of the diagnosis, so nobody re-derives
    it. The speculative arm needs booking intent and a treatment mention in one
    utterance; this caller split them across two."""
    from app.media_streams.connection import _transcript_has_booking_intent

    assert _transcript_has_booking_intent("yeah i booked an appointment") is True
    assert _transcript_has_booking_intent("um just my left ankle nothing serious") is False
