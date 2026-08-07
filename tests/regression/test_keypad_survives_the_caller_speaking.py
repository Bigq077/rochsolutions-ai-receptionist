"""
"Hang on, let me find it" must not close the keypad.

CA9758ceab, 2026-08-07 — a booking that did not happen:

    10:14:58  "Thanks Mark — could you type your number on your keypad?"
              v3_phone_dtmf_active = True
    10:14:58  caller: "okay i'll do that for you in a minute"
              → conversational speech in empty DTMF mode — exiting
              → v3_phone_dtmf_active = False
    10:15:01  "No rush at all."          ← overwrites last_bot_prompt
    10:15:10  DTMF raw digit='0' v3_phone_dtmf_active=False
       …      eleven digits, not one buf= line
    10:15:16  10s dead-air — "are you still there?"
    10:15:27  caller: "i just typed it in"
    10:16:15  hung up, no booking

Three gates had to fail and all three did:

  1. THE DISARM was unconditional. _is_conversational_during_dtmf() only chose
     a log line; it did not gate the exit. The comment claimed "the patient is
     clearly not typing a number" — he had just said he was about to.
  2. THE RESCUE keyed on last_bot_prompt still being a keypad prompt. Susie's
     own "No rush at all." overwrote it nine seconds before he typed.
  3. THE PHONE STEP WAS NEVER RECORDED as asked: she said "type YOUR number"
     and _PHONE_STEP_MARKERS only held "type THE number". That gap was opened
     the same morning by acbe0c6, which removed "on your keypad" to fix a
     collision with the LOCATION rung and narrowed the list by one word too
     many.

And the loss was invisible: lost_total=0 by_reason={} on a call that binned
eleven digits.
"""

import inspect

import pytest

from app.media_streams import connection as c
from app.media_streams.connection import (
    _LOC_RUNG3_DTMF,
    _is_phone_keypad_prompt,
)
from app.prompts.clinic_template_prompt import _PHONE_STEP_MARKERS


THE_ASK = (
    "Thanks Mark — could you type your number on your keypad? "
    "You can press the star key to reset at any time."
)


# ── gate 1: the disarm is now conditional ──────────────────────────────────

def _disarm_block() -> str:
    src = inspect.getsource(c)
    start = src.index("phone DTMF STAYS ARMED")
    return src[max(0, start - 2500):start + 400]


def test_the_keypad_stays_armed_when_no_number_is_on_record():
    block = _disarm_block()
    assert 'self.session.get("phone_confirmed")' in block
    assert 'self.session.get("phone_entered_by_keypad")' in block
    assert "STAYS ARMED" in block


def test_the_disarm_is_no_longer_unconditional():
    """
    The exact defect: `v3_phone_dtmf_active = False` sat outside the
    conversational check, so ANY speech closed the keypad.
    """
    block = _disarm_block()
    disarm = block.index('self.session["v3_phone_dtmf_active"] = False')
    guard = block.index('self.session.get("phone_confirmed")')
    assert guard < disarm, (
        "the disarm runs before the number-on-record check — it is "
        "unconditional again"
    )


# ── gate 2: the rescue no longer depends on last_bot_prompt ────────────────

def test_the_rescue_fires_on_state_not_only_on_the_last_prompt():
    src = inspect.getsource(c)
    at = src.index("_phone_outstanding = bool(")
    block = src[at:at + 1200]
    assert 'self.session.get("booking_flow_active")' in block
    assert 'not self.session.get("phone_confirmed")' in block
    assert "_phone_outstanding" in src[src.index("auto-activating") - 900:]


def test_the_old_prompt_test_is_kept_as_well():
    """Belt and braces — the original path still works for the silent case."""
    src = inspect.getsource(c)
    at = src.index("auto-activating v3_phone_dtmf_active")
    assert "_is_phone_keypad_prompt" in src[at - 1200:at]


def test_the_prompt_that_broke_it_is_still_not_a_keypad_prompt():
    """
    "No rush at all." must remain a non-keypad prompt — the fix is that the
    rescue no longer depends on this answer, not that this answer changed.
    """
    assert not _is_phone_keypad_prompt("No rush at all.")
    assert _is_phone_keypad_prompt(THE_ASK)


# ── gate 3: the marker that missed ─────────────────────────────────────────

def test_the_real_ask_registers_as_the_phone_step():
    assert any(m in THE_ASK.lower() for m in _PHONE_STEP_MARKERS), (
        "'type your number' still matches nothing — the phone step is not "
        "recorded as asked and book_appointment's backstop misjudges the call"
    )


def test_both_phrasings_are_covered():
    for phrasing in ("type the number", "type your number"):
        assert phrasing in _PHONE_STEP_MARKERS


def test_the_location_collision_stays_closed():
    """
    acbe0c6 removed "on your keypad" because the LOCATION rung carries it.
    Adding "type your number" must not reopen that — the location rung asks
    the caller to PRESS 1 or 2, never to type a number.
    """
    loc = _LOC_RUNG3_DTMF.lower()
    hits = [m for m in _PHONE_STEP_MARKERS if m in loc]
    assert not hits, f"the location rung matches the phone markers via {hits}"


def test_all_three_marker_copies_agree():
    """
    Three copies exist because llm_stream imports turn_handler and
    latency_timing must stay stdlib-only. They drift silently unless pinned.
    """
    from app.media_streams.llm_stream import _PHONE_STEP_MARKERS as B
    from app.media_streams.latency_timing import _PHONE_QUESTION_MARKERS as C

    assert set(_PHONE_STEP_MARKERS) == set(B) == set(C)


# ── the loss is now counted ────────────────────────────────────────────────

def test_a_discarded_digit_is_recorded():
    """
    The accumulate gate returned silently. Eleven digits vanished and the call
    still reported lost_total=0 — the third class of invisible loss in one day.
    """
    src = inspect.getsource(c)
    at = src.index("Only accumulate DTMF while in phone-collection")
    block = src[at:at + 1400]
    assert "_note_utterance_lost" in block
    assert "dtmf_digit_discarded" in block
