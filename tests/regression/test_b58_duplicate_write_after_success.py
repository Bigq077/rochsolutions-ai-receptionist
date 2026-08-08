# tests/regression/test_b58_duplicate_write_after_success.py
"""
B-58 — a write refused AFTER the same write already succeeded on this call.

`CA0f9a12` (8 Aug 2026). Billy's appointment was cancelled successfully. He said
"thank you bye". On that farewell turn the model fired `cancel_appointment` one
more time; the gate refused it, and `_note_write_result` did what it does to any
refusal — armed Gate 5f and attached the no-claim rule. That rule ended with
"Their original appointment still stands." So Susie began:

    "I'm sorry — there was a problem completing that cancellation.
     Could you give us a call back on oh seven eight seven oh, on…"

Twilio's stop event landed 150 ms after the first ElevenLabs chunk returned and
the second was cancelled mid-synthesis, so he heard nothing. That was his
hang-up speed, not a safety net: a caller who waited two seconds, or said
"sorry, what?", would have been told a completed cancellation had failed and
would have rung the clinic.

Two defects, fixed separately and pinned separately below.

1.  The rules asserted facts the code never checked. All `_note_write_result`
    knows is that *this attempt* was refused — never what the calendar holds.
    "Their original appointment still stands" was a claim about the world. Every
    rule now constrains Susie's speech instead, which cannot be false and cannot
    weaken a genuine refusal, because it only ever narrows what may be asserted.

2.  Only `book_appointment` had a call-scoped success latch
    (`booking_write_confirmed`). Cancel and reschedule had none, so a duplicate
    on the farewell turn was indistinguishable from a first attempt that failed.
    All three families now latch in `WRITE_SUCCEEDED_KEY`, and a refusal in a
    latched family neither arms the gate nor claims a failure.

Layer 3 of the original plan — dropping the tool from the schema once its family
has succeeded — is deliberately NOT here. It would make a second, legitimate
cancellation on the same call structurally impossible (a caller with two
appointments), and the caller would get silence rather than a controlled
outcome. See docs/plan/REGISTER_B_U.md B-58.
"""
from __future__ import annotations

import pytest

from app.media_streams import llm_stream as ls
from app.media_streams.turn_handler import (
    WRITE_FAMILY_BOOKING,
    WRITE_FAMILY_CANCEL,
    WRITE_FAMILY_RESCHEDULE,
    WRITE_REFUSED_KEY,
)


# (tool, the executor's success payload, the refusal the gate then constructs)
_FAMILIES = [
    (
        "cancel_appointment",
        {"success": True, "cancelled": "Initial Consultation", "was_at": "Mon 10 Aug 18:00"},
        {"status": "cancellation_confirmation_required"},
        WRITE_FAMILY_CANCEL,
    ),
    (
        "reschedule_appointment",
        {"success": True, "new_time": "Tue 11 Aug 09:00"},
        {"status": "reschedule_confirmation_required"},
        WRITE_FAMILY_RESCHEDULE,
    ),
    (
        "book_appointment",
        {"success": True, "appointment_id": 1748296801},
        {"status": "confirmation_required"},
        WRITE_FAMILY_BOOKING,
    ),
]


# ── Defect 1: the rules must not describe the world ────────────────────────────

@pytest.mark.parametrize("family", [
    WRITE_FAMILY_BOOKING, WRITE_FAMILY_RESCHEDULE, WRITE_FAMILY_CANCEL,
])
def test_no_claim_rule_never_asserts_calendar_state(family):
    """The sentence Billy nearly heard, and its siblings.

    Substring, not regex: the cancel rule legitimately contains "still stands"
    inside a PROHIBITION ("do not say their original appointment still
    stands"), so the assertion is on the assertive form only.
    """
    rule = ls._WRITE_NO_CLAIM_RULE[family]
    lowered = rule.lower()
    for claim in (
        "their original appointment still stands",
        "your original appointment still stands",
        "the appointment still stands",
    ):
        # Permitted only when negated by an immediately preceding "do not say".
        idx = lowered.find(claim)
        while idx != -1:
            preceding = lowered[max(0, idx - 40):idx]
            assert "do not say" in preceding, (
                f"{family} rule asserts calendar state: ...{rule[max(0, idx-40):idx+len(claim)]}"
            )
            idx = lowered.find(claim, idx + 1)


@pytest.mark.parametrize("family", [
    WRITE_FAMILY_BOOKING, WRITE_FAMILY_RESCHEDULE, WRITE_FAMILY_CANCEL,
])
def test_no_claim_rule_scopes_itself_to_this_attempt(family):
    rule = ls._WRITE_NO_CLAIM_RULE[family].lower()
    assert "this" in rule and "attempt" in rule, (
        "the rule must be about THIS attempt, not about the booking as such"
    )
    assert "did not go through" in rule


# ── Defect 2: the call-scoped latch ────────────────────────────────────────────

@pytest.mark.parametrize("tool,success,refusal,family", _FAMILIES)
def test_success_latches_for_the_whole_call(tool, success, refusal, family):
    s = {}
    ls._note_write_result(s, tool, dict(success))
    assert s[ls.WRITE_SUCCEEDED_KEY][family] is True


@pytest.mark.parametrize("tool,success,refusal,family", _FAMILIES)
def test_duplicate_refusal_does_not_arm_gate_5f(tool, success, refusal, family):
    """The load-bearing assertion.

    Gate 5f reads WRITE_REFUSED_KEY and strips the turn's confirmation. On the
    farewell turn there is nothing to strip and everything to lose: arming it
    turned a goodbye into an apology.
    """
    s = {}
    ls._note_write_result(s, tool, dict(success))
    ls._note_write_result(s, tool, dict(refusal))
    assert not (s.get(WRITE_REFUSED_KEY) or {}).get(family), (
        "a duplicate write in an already-completed family must not arm the guard"
    )


@pytest.mark.parametrize("tool,success,refusal,family", _FAMILIES)
def test_duplicate_refusal_is_told_it_is_a_duplicate(tool, success, refusal, family):
    s = {}
    ls._note_write_result(s, tool, dict(success))
    out = ls._note_write_result(s, tool, dict(refusal))
    rule = (out.get("caller_message_rule") or "").lower()
    assert "already completed" in rule
    assert "do not apologise" in rule
    assert "saying goodbye" in rule
    # The apology Billy nearly heard, in the form the model would have read.
    assert "did not go through and does not undo it" in rule


def test_the_ca0f9a12_sequence_end_to_end():
    """Replay: successful cancel, then cancel_appointment on 'thank you bye'."""
    session = {}
    ok = ls._note_write_result(
        session,
        "cancel_appointment",
        {"success": True, "cancelled": "Initial Consultation", "was_at": "Mon 10 Aug 18:00"},
    )
    assert "caller_message_rule" not in ok

    # Farewell turn. WRITE_REFUSED_KEY is turn-scoped and cleared at the top of
    # every turn by the tool loop — replicate that, or the test proves nothing
    # about the second turn.
    session.pop(WRITE_REFUSED_KEY, None)

    dup = ls._note_write_result(
        session, "cancel_appointment", {"status": "cancellation_confirmation_required"}
    )
    rule = dup["caller_message_rule"]
    assert "NOT cancelled" not in rule
    assert "still stands" not in rule
    assert not (session.get(WRITE_REFUSED_KEY) or {}).get(WRITE_FAMILY_CANCEL)


# ── The behaviour that must survive: a FIRST refusal is unchanged ─────────────

@pytest.mark.parametrize("tool,success,refusal,family", _FAMILIES)
def test_a_first_refusal_still_arms_and_still_forbids_the_claim(
    tool, success, refusal, family
):
    """B-36 must not be undone by B-58. No prior success — nothing is latched."""
    s = {}
    out = ls._note_write_result(s, tool, dict(refusal))
    assert s[WRITE_REFUSED_KEY][family] is True
    assert out["caller_message_rule"] == ls._WRITE_NO_CLAIM_RULE[family]


def test_the_latch_is_per_family_not_global():
    """A cancelled appointment must not excuse a refused BOOKING on the same call."""
    s = {}
    ls._note_write_result(s, "cancel_appointment", {"success": True})
    out = ls._note_write_result(
        s, "book_appointment", {"status": "confirmation_required"}
    )
    assert s[WRITE_REFUSED_KEY][WRITE_FAMILY_BOOKING] is True
    assert out["caller_message_rule"] == ls._WRITE_NO_CLAIM_RULE[WRITE_FAMILY_BOOKING]


def test_a_second_genuine_write_still_succeeds():
    """The known cost of the coarser per-family key, pinned as acceptable.

    A caller with two appointments cancels both. The second cancellation is a
    real write and must run and latch exactly like the first — the latch only
    ever changes what happens on the REFUSAL path.
    """
    s = {}
    ls._note_write_result(s, "cancel_appointment", {"success": True, "was_at": "Mon 10 Aug"})
    second = ls._note_write_result(
        s, "cancel_appointment", {"success": True, "was_at": "Thu 13 Aug"}
    )
    assert "caller_message_rule" not in second
    assert s[ls.WRITE_SUCCEEDED_KEY][WRITE_FAMILY_CANCEL] is True


def test_within_turn_retry_still_disarms_the_guard():
    """The 2026-06-12 over-fire, re-pinned.

    refuse -> succeed within one turn must clear the marker. The new latch sits
    after that branch and must not have displaced it.
    """
    s = {}
    ls._note_write_result(s, "book_appointment", {"status": "confirmation_required"})
    assert s[WRITE_REFUSED_KEY][WRITE_FAMILY_BOOKING] is True
    ls._note_write_result(s, "book_appointment", {"success": True})
    assert not (s.get(WRITE_REFUSED_KEY) or {}).get(WRITE_FAMILY_BOOKING)
    assert s["booking_write_confirmed"] is True
