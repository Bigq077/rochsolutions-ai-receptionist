"""C2 — the typed number is read back at the moment it is typed.

C3 closed fabrication (a fragment is never queued) and the A3 gate holds the
booking to the number on record. What neither can catch is a digit Twilio drops
or mangles in transit: the session, the model and the booking all agree on a
number that is not the one the caller pressed. The caller is the only party who
can detect that, so it has to be spoken.

The turn-flow contract this pins:

    caller types 11 valid digits
      -> committed (phone_confirmed=True, phone_entered_by_keypad=True)
      -> digits are NOT queued as a transcript
      -> "Thanks - I've got 07700900456. Is that correct?"
      -> caller answers
           "no"      -> number torn down, keypad re-armed, counts as a rung
           "yes"     -> falls through to the LLM, already confirmed
           anything  -> falls through to the LLM, already confirmed

Two things make this safe rather than clever, and both are asserted below:

1. `_is_use_this_number("yes")` is True, so the caller's confirmation enters the
   booking verbal-confirm block. It is skipped only by that block's
   `phone_entered_by_keypad` guard. Without it, "yes" overwrites the typed
   number with the caller ID they declined.
2. The pending flag is one-shot. If an ambiguous answer could leave it armed, a
   later "no" about parking would wipe a good number — the exact class of bug
   this whole sequence exists to remove.
"""
from __future__ import annotations

import asyncio
import inspect

import pytest

import app.media_streams.connection as conn


class _Queue:
    def __init__(self):
        self.items = []

    async def put(self, item):
        self.items.append(item)


class _H:
    def __init__(self, session):
        self.session = session
        self.call_sid = "CAtest"
        self.tts_text_queue = _Queue()
        self.transcript_queue = _Queue()

    _readback_keypad_number = conn.WebSocketCallHandler._readback_keypad_number
    _reject_keypad_number = conn.WebSocketCallHandler._reject_keypad_number
    _commit_dtmf_phone_for_booking = conn.WebSocketCallHandler._commit_dtmf_phone_for_booking


@pytest.fixture(autouse=True)
def no_redis(monkeypatch):
    async def _noop(*a, **k):
        return None
    monkeypatch.setattr(conn, "save_session", _noop)


def _committed(**over):
    """A session in the state the commit leaves behind on the v3 booking path."""
    s = dict({"v3_caller_intent": "booking", "twilio_from_local": "07502211207",
              "state": "GREETING"}, **over)
    h = _H(s)
    h._commit_dtmf_phone_for_booking("07700900456")
    return h


# ── The read-back fires exactly where the commit committed ───────────────────

def test_a_committed_number_is_read_back():
    h = _committed()
    assert asyncio.run(h._readback_keypad_number("07700900456")) is True
    assert "07700900456" in h.tts_text_queue.items[0]
    assert "is that correct" in h.tts_text_queue.items[0].lower()


def test_the_digits_are_not_also_queued_to_the_model():
    """Taking the turn means taking it. Queueing as well would have the model
    answering over the top of the read-back."""
    h = _committed()
    asyncio.run(h._readback_keypad_number("07700900456"))
    assert h.transcript_queue.items == []


def test_the_readback_is_in_the_history_the_model_will_see():
    """The digits were never queued, so without this the caller's "yes" arrives
    answering a question that is absent from the conversation."""
    h = _committed()
    asyncio.run(h._readback_keypad_number("07700900456"))
    assert h.session["conversation_history"][-1] == {
        "role": "assistant",
        "content": "Thanks — I've got 07700900456. Is that correct?",
    }


@pytest.mark.parametrize("intent", ["cancel", "reschedule"])
def test_lookup_flows_are_not_read_back(intent):
    """There the number is a search key, and _inject_phone_context_for_lookup
    already drives that turn. The commit declines these, so the read-back does
    too — one scoping decision, not two."""
    h = _committed(v3_caller_intent=intent)
    assert asyncio.run(h._readback_keypad_number("07700900456")) is False
    assert h.tts_text_queue.items == []


@pytest.mark.parametrize("state", [
    "COLLECT_PHONE", "COLLECT_PHONE_RETURNING",
    "COLLECT_PHONE_RESCHEDULE", "RETURNING_PLAN_COLLECT_PHONE",
])
def test_flowengine_states_are_not_read_back_twice(state):
    """These have their own CONFIRM_PHONE readback. A second one is the
    triple-confirm this change exists to remove."""
    h = _committed(state=state)
    assert asyncio.run(h._readback_keypad_number("07700900456")) is False
    assert h.tts_text_queue.items == []


# ── Rejection tears the number down completely ───────────────────────────────

def test_rejection_clears_the_number_and_the_confirmation():
    """Leaving phone_confirmed True would let A1 pass and A3 then hold the
    booking to the rejected number — both gates faithfully delivering it."""
    h = _committed()
    asyncio.run(h._readback_keypad_number("07700900456"))
    asyncio.run(h._reject_keypad_number())
    assert h.session["phone_confirmed"] is False
    assert h.session["phone_entered_by_keypad"] is False
    assert "phone" not in h.session.get("collected", {})
    assert "phone_number" not in h.session


def test_rejection_rearms_the_keypad_with_the_mandated_line():
    """clinic_template_prompt names this exact sentence as the only acceptable
    keypad-arming response; paraphrasing it desyncs code from prompt."""
    h = _committed()
    asyncio.run(h._readback_keypad_number("07700900456"))
    asyncio.run(h._reject_keypad_number())
    assert h.session["v3_phone_dtmf_active"] is True
    assert h.session["phone_awaiting_dtmf"] is True
    assert h.session["phone_dtmf_buffer"] == ""
    assert h.tts_text_queue.items[-1] == (
        "No problem — go ahead and type the number on your keypad. "
        "You can press the star key to reset at any time."
    )


def test_rejection_counts_toward_the_same_ladder():
    """A rejected read-back is a failed entry. Giving it a fresh ladder would
    let type -> reject -> type -> reject run forever."""
    h = _committed()
    asyncio.run(h._readback_keypad_number("07700900456"))
    asyncio.run(h._reject_keypad_number())
    assert h.session["phone_dtmf_reask_count"] == 1


# ── The two safety properties ────────────────────────────────────────────────

def test_yes_is_matched_by_is_use_this_number():
    """Not incidental — this is why the intercept must sit ABOVE the booking
    verbal-confirm block, and why that block's keypad guard is load-bearing."""
    for yes in ("yes", "yeah", "yep", "correct", "that is right"):
        assert conn._is_use_this_number(yes) is True, yes


def test_the_keypad_guard_still_protects_a_typed_number():
    """If this guard is ever removed, "yes" to the read-back overwrites the
    typed number with the caller ID the caller declined."""
    src = inspect.getsource(conn.WebSocketCallHandler)
    assert 'if self.session.get("phone_entered_by_keypad"):' in src


def test_the_pending_flag_is_one_shot():
    """Cleared before the answer is even classified, so no branch can return
    with it still armed."""
    src = inspect.getsource(conn.WebSocketCallHandler)
    i_read = src.index('if self.session.get("v3_keypad_readback_pending"):')
    i_clear = src.index('self.session["v3_keypad_readback_pending"] = False')
    i_reject = src.index("_is_phone_readback_rejection(utterance)")
    assert i_read < i_clear < i_reject, (
        "the flag must be cleared before the answer is classified, or an "
        "unmatched answer leaves it armed for a later 'no' to land on"
    )


def test_the_intercept_precedes_the_verbal_confirm_block():
    src = inspect.getsource(conn.WebSocketCallHandler)
    # The intercept's own `if`, not the setter in _readback_keypad_number —
    # that one sits earlier in the class regardless and would pass for free.
    assert src.index('if self.session.get("v3_keypad_readback_pending"):') < src.index(
        "Booking-flow verbal phone confirm"
    )


# ── The rejection matcher ────────────────────────────────────────────────────

@pytest.mark.parametrize("no", [
    "no", "nope", "nah", "no that's wrong", "that's not right",
    "no it's incorrect", "um no", "wrong number", "let me try again",
    "that's not it", "can I type it again",
])
def test_rejections_are_caught(no):
    assert conn._is_phone_readback_rejection(no) is True, no


@pytest.mark.parametrize("not_a_rejection", [
    "yes", "yeah that's right", "correct", "yes that's the one", "",
    # The length cap earns its keep here: a long turn merely containing "no"
    # is not a rejection of the number.
    "no rush but can you also tell me about parking and the stairs",
    "now that you mention it, is there parking at the clinic",
])
def test_non_rejections_fall_through(not_a_rejection):
    assert conn._is_phone_readback_rejection(not_a_rejection) is False, not_a_rejection


def test_ambiguity_falls_through_rather_than_wiping_a_good_number():
    """The asymmetry that makes this safe: a missed rejection costs a turn, a
    false rejection destroys a number the caller typed correctly."""
    for ambiguous in ("hang on", "what was that", "sorry", "erm"):
        assert conn._is_phone_readback_rejection(ambiguous) is False, ambiguous


# ── CA6e1024db (2 Aug 2026): the emphatic rejection ──────────────────────────
#
# The caller rejected the read-back and typed nine more digits into a keypad
# that was never re-armed. Every one was discarded (`DTMF raw digit='0'
# v3_phone_dtmf_active=False`, no `buf=` line), 10s of dead air followed, and
# the call was abandoned with name, number and slot all collected.
#
# Three layers had to miss, and each missed for its own reason. They mask one
# another — any single one catching it hides the other two — so all three are
# pinned here.

def test_the_emphatic_rejection_that_lost_CA6e1024db():
    """Layer 1. Verbatim from the call.

    words[0] == "no" and it IS in _PHONE_READBACK_REJECT_WORDS, but the turn is
    11 words and the cap is 6, so the word branch drops it. The phrase branch
    has to carry this one: a caller who is emphatic enough to reject a read-back
    is exactly the caller who repeats themselves.
    """
    assert conn._is_phone_readback_rejection(
        "no it's wrong it's wrong it's wrong let me retype it"
    ) is True


@pytest.mark.parametrize("no", [
    # Over the 6-word cap, so each one depends on a phrase match.
    "no it's wrong let me type it in again please",
    "no no no it's wrong I'll do it again",
    "sorry that's wrong can I retype it for you",
    "it's wrong",
    "let me retype it",
])
def test_long_form_rejections_are_caught(no):
    assert conn._is_phone_readback_rejection(no) is True, no


def test_the_cap_still_earns_its_keep():
    """The fix must not be "raise the cap". These are the turns the cap exists
    to reject, and they must stay rejected — a false rejection destroys a
    number the caller typed correctly."""
    for not_a_rejection in (
        "no rush but can you also tell me about parking and the stairs",
        "now that you mention it, is there parking at the clinic",
        "no problem at all, what time did you say it was",
    ):
        assert conn._is_phone_readback_rejection(not_a_rejection) is False, not_a_rejection


# ── Layer 3: the Spec M net must catch an improvised re-arm line ─────────────

def test_the_mandated_line_arms_the_keypad():
    """_reject_keypad_number's wording and the Spec M net must agree — if they
    ever diverge the deterministic path arms DTMF and the net does not."""
    assert conn._is_keypad_arming_line(
        "No problem — go ahead and type the number on your keypad. "
        "You can press the star key to reset at any time."
    ) is True


def test_the_improvised_retype_line_arms_the_keypad():
    """CA6e1024db layer 3, verbatim. When layer 1 missed, the model improvised
    this — and it does not contain the word "keypad", so the net's
    `"keypad" in line` requirement failed and DTMF stayed shut while the caller
    typed nine digits into it."""
    assert conn._is_keypad_arming_line(
        "Go ahead and retype it. You can press the star key to reset at any time."
    ) is True


def test_the_building_access_line_still_does_not_arm_the_keypad():
    """P10. This line has "keypad" and is NOT a phone prompt — it was falsely
    arming DTMF on Calls 4/5. Widening the net must not re-open that."""
    assert conn._is_keypad_arming_line(
        "When you arrive, use the top keypad to enter the code 1234."
    ) is False


def test_the_spec_m_net_actually_uses_the_predicate():
    """The helper is only worth anything if the live call site calls it. Pinned
    because the original defect was an inline condition in a 15k-line method
    that no test could reach — the predicate passing while the call site keeps
    its own copy is exactly the failure this must not allow back."""
    src = inspect.getsource(conn.WebSocketCallHandler)
    assert "_is_keypad_arming_line(_last_bot)" in src
    assert '"keypad" in _last_bot_lc' not in src, (
        "Spec M has drifted back to an inline condition"
    )


def test_the_readback_itself_never_arms_the_keypad():
    """This turn wants the caller's VOICE. If the read-back armed DTMF the
    dead-air net would stay suppressed and a silent caller is never
    re-prompted."""
    h = _committed()
    asyncio.run(h._readback_keypad_number("07700900456"))
    assert conn._is_keypad_arming_line(h.tts_text_queue.items[0]) is False


# ── Wording constraints ──────────────────────────────────────────────────────

def test_the_readback_does_not_rearm_dtmf():
    """Spec M (~line 9727) re-arms DTMF on any bot line containing "keypad"
    plus "type" or "star key". This turn wants the caller's voice."""
    h = _committed()
    asyncio.run(h._readback_keypad_number("07700900456"))
    said = h.tts_text_queue.items[0].lower()
    assert "keypad" not in said
    assert "star key" not in said


def test_the_number_is_not_pre_spaced():
    """_spell_phone renders a phone-shaped span digit-by-digit on the way out.
    Pre-spacing here would spell it twice."""
    h = _committed()
    asyncio.run(h._readback_keypad_number("07700900456"))
    assert "07700900456" in h.tts_text_queue.items[0]
    assert "0 7 7 0 0" not in h.tts_text_queue.items[0]


def test_the_readback_closes_the_keypad_so_dead_air_can_fire():
    """The dead-air nudge is suppressed while DTMF is live (~12931). Leaving it
    armed would mean a caller who says nothing is never re-prompted."""
    h = _committed()
    asyncio.run(h._readback_keypad_number("07700900456"))
    assert h.session["v3_phone_dtmf_active"] is False
    assert h.session["phone_awaiting_dtmf"] is False


# ── C4: the prompts must agree with the code ─────────────────────────────────

def test_prompts_no_longer_tell_the_model_to_skip_a_readback_that_now_happens():
    from app.prompts import clinic_template_prompt, susie_system_prompt

    for mod in (clinic_template_prompt, susie_system_prompt):
        src = inspect.getsource(mod)
        assert "the keypad is already accurate" not in src, (
            f"{mod.__name__} still asserts a premise CA3590527b disproved"
        )


def test_the_template_prompt_explains_who_reads_it_back():
    from app.prompts import clinic_template_prompt

    src = inspect.getsource(clinic_template_prompt)
    assert "the system reads it back" in src
