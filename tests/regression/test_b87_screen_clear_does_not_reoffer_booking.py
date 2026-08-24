"""B-87 — after a screen cleared, Susie re-offered a booking already asked for.

CA6e30f29f (24 Aug 21:04, jv_v1), verbatim:

    CALLER  "i'd like to book an appointment i've hurt my ankle"
    SUSIE   <trauma screen, spoken deterministically>
    CALLER  "no i can walk on it fine"
    SUSIE   "That's reassuring. Would you like to book an assessment...?"   <-- re-ask
    CALLER  "uh yes please"
    SUSIE   "Is there a particular day or time that works best for you?"

A whole extra exchange — ~14 seconds — to answer a question the caller had
already answered in their opening sentence.

Cause: the screening block ends in `continue`, which skips the booking-intent
detection ~4000 lines downstream, so `booking_flow_active` was never set from
the opening utterance. With no booking state, the model offered.

Gate 5c cannot rescue this: stripping "Would you like to book...?" leaves
"That's reassuring." with no question, so its dead-end guard correctly KEEPS
the offer. The intent has to be captured at arm time instead.

These tests pin the rule at the level of the two pure helpers the fix composes,
plus the config text it reuses. The interception itself is exercised by the
live call recorded above.
"""

import json
from pathlib import Path

import pytest

from app.media_streams.clinical_screening import (
    PENDING_SCREEN_KEY,
    SCREEN_RED_FLAG_KEY,
    update_screening_state,
)
from app.media_streams.connection import (
    _TIMING_QUESTION_AFTER_BOOKING_ACK,
    _transcript_has_booking_intent,
)

CLINIC = json.loads(
    Path("app/clinics/jv_v1/clinic.json").read_text(encoding="utf-8")
)

OPENER = "um yeah hi there i'd like to book an appointment i've hurt my ankle"


def _ask_then_answer(answer, opener=OPENER):
    """Replay arm -> ask -> answer exactly as connection.py drives it."""
    s = {}
    pre = _transcript_has_booking_intent(opener)
    r1 = update_screening_state(s, CLINIC, opener)
    armed = s.get(PENDING_SCREEN_KEY)
    # the screen must look "asked" for the answer to be graded
    s["last_bot_prompt"] = (r1.get("speak") or "")[:200]
    s["last_question"] = r1.get("speak") or ""
    pending_before = s.get(PENDING_SCREEN_KEY)
    r2 = update_screening_state(s, CLINIC, answer)
    cleared = bool(
        r2["action"] == "none"
        and pending_before
        and not s.get(PENDING_SCREEN_KEY)
        and not s.get(SCREEN_RED_FLAG_KEY)
    )
    return pre, armed, r1["action"], r2["action"], cleared, s


# ── The defect ─────────────────────────────────────────────────────────────

def test_the_opener_carries_booking_intent():
    """The signal that was being dropped."""
    assert _transcript_has_booking_intent(OPENER) is True


def test_a_clean_clear_is_detectable_as_a_transition():
    """The fix keys on pending-before AND not-pending-after AND no red flag."""
    pre, armed, a1, a2, cleared, _ = _ask_then_answer("uh no i can walk on it fine")

    assert pre is True
    assert armed == "trauma_fracture"
    assert a1 == "ask_screen"
    assert cleared is True, "a cleared screen must be distinguishable from 'nothing happened'"


def test_the_reused_timing_question_is_a_question():
    """It replaces the model's turn, so it must not dead-end the caller.

    Reused from the booking-ack path rather than duplicated — a second copy of
    this sentence would drift from the one the rest of the flow speaks.
    """
    assert _TIMING_QUESTION_AFTER_BOOKING_ACK.strip().endswith("?")
    assert "day or time" in _TIMING_QUESTION_AFTER_BOOKING_ACK.lower()


# ── Cases that must NOT take the new path ──────────────────────────────────

def test_a_red_flag_escalates_and_is_never_treated_as_cleared():
    """The escalation must win. Booking stays blocked, no timing question."""
    pre, armed, a1, a2, cleared, s = _ask_then_answer(
        "um yeah it's really swollen i can't put weight on it"
    )

    assert a2 == "escalate"
    assert s.get(SCREEN_RED_FLAG_KEY) == "trauma_fracture"
    assert cleared is False, "a red flag must never look like a clean clear"


def test_a_screen_with_no_prior_booking_request_is_untouched():
    """Caller who only described a problem must still be asked if they want to book.

    The fix must not manufacture booking intent that was never expressed.
    """
    opener = "i've hurt my ankle"
    assert _transcript_has_booking_intent(opener) is False

    pre, armed, a1, a2, cleared, _ = _ask_then_answer(
        "no i can walk on it fine", opener=opener
    )
    assert armed == "trauma_fracture", "the screen should still arm"
    assert cleared is True
    assert pre is False, "no booking intent -> the model keeps the offer"


@pytest.mark.parametrize("utterance,expected", [
    ("i'd like to book an appointment", True),
    ("can i book in for tuesday", True),
    ("i want to make an appointment", True),
    ("my ankle hurts", False),
    ("uh no i can walk on it fine", False),
    ("that's reassuring", False),
])
def test_intent_predicate_is_the_shared_one(utterance, expected):
    """Reused, not re-derived.

    A second booking-intent predicate would drift from the one the booking-ack
    path uses, and the two would disagree about the same sentence — this
    codebase has been bitten by duplicated predicates repeatedly.
    """
    assert _transcript_has_booking_intent(utterance) is expected


def test_an_ungradable_answer_leaves_the_screen_pending():
    """Not a clear, so the new path must not fire and booking stays blocked."""
    pre, armed, a1, a2, cleared, s = _ask_then_answer("what do you mean sorry")

    assert cleared is False
    assert s.get(PENDING_SCREEN_KEY) == "trauma_fracture" or a2 != "none"
