"""
If Susie asks which clinic, the system must be listening for the answer.

`v3_location_q_active` is what tells the short-utterance guard that a question
is outstanding. Cleared by the keypad rung (by design — it ends the ladder),
it could only be re-armed by a post-run_turn scan that was gated on
`v3_treatment_mentioned`. That flag is set only by
`_is_treatment_specific_booking()` — only when the caller named a treatment or
a body part.

So a caller who says "I need to book an appointment for tomorrow" could never
re-arm it. CA1747c2d9, 2026-08-06:

    20:52:36  keypad rung clears v3_location_q_active
    20:53:15  LLM re-asks "Which clinic were you thinking of — Awlstuh or
              Redditch?"   ← matches TWO signals, but the flag stayed shut
    20:53:20  'ofter'  → "no active question, Haiku skipped, timer re-armed"
    20:53:34  'hello'  → "no active question, Haiku skipped, timer re-armed"

The caller said "pardon" twice and "hello" once, heard nothing, and hung up.
The guard that discarded them carries a comment asserting it "never fires
here" because "within v3_location_q_active the system is always waiting". It
fired three times, because the flag was False.

Removing the precondition is the fix, not a loosening. Whether the caller
mentioned a body part has no bearing on whether a clinic question is
outstanding. What still bounds the arm: it will not fire if the gate is
already active, or if the clinic is already confirmed.
"""

import inspect

import pytest

from app.media_streams import connection as c


def _arm_block() -> str:
    src = inspect.getsource(c)
    start = src.index("CODE SPEC AD")
    return src[start:src.index("B2: deferred gate5 fallback emission", start)]


def _signals() -> tuple:
    """The clinic-question signals as the code actually holds them."""
    block = _arm_block()
    start = block.index("_clinic_question_signals = (")
    end = block.index(")", start)
    return tuple(
        line.strip().strip(',').strip('"')
        for line in block[start:end].splitlines()[1:]
        if line.strip().startswith('"')
    )


# ── the precondition that caused it ────────────────────────────────────────

def test_the_treatment_precondition_is_gone():
    """
    v3_treatment_mentioned must not gate the arm. A caller who books without
    naming a body part is the ordinary case, not an edge case.
    """
    block = _arm_block()
    condition = block[block.index("if ("):block.index("self.session[\"v3_location_q_active\"] = True")]
    assert "v3_treatment_mentioned" not in condition, (
        "the clinic-question arm is gated on the caller having named a "
        "treatment again — the 20:53 defect is back"
    )


def test_the_two_real_bounds_are_still_there():
    """Don't double-arm, and don't re-open a settled clinic."""
    block = _arm_block()
    condition = block[block.index("if ("):block.index("self.session[\"v3_location_q_active\"] = True")]
    assert 'not self.session.get("v3_location_q_active")' in condition
    assert 'not self.session.get("v3_location_confirmed")' in condition


# ── the question that was missed ───────────────────────────────────────────

def test_the_exact_sentence_from_the_lost_call_is_recognised():
    spoken = "Which clinic were you thinking of — Awlstuh or Redditch?"
    hits = [s for s in _signals() if s in spoken.lower()]
    assert hits, f"{spoken!r} matches no clinic-question signal"


@pytest.mark.parametrize("reply", [
    "Which clinic were you thinking of — Awlstuh or Redditch?",
    "Is this for our Awlstuh or Redditch clinic?",
    "Which clinic would suit you best?",
    "Which location works better for you?",
])
def test_every_way_susie_asks_it_is_recognised(reply):
    assert any(s in reply.lower() for s in _signals())


@pytest.mark.parametrize("reply", [
    "That's you booked in for Monday at three.",
    "It's eighty-five pounds for the initial assessment.",
    "Could I take your first name and surname?",
])
def test_ordinary_replies_do_not_arm_the_gate(reply):
    assert not any(s in reply.lower() for s in _signals())


# ── the flag is what the discard guard reads ──────────────────────────────

def test_the_discard_guard_reads_this_flag():
    """
    Ties the two halves together: this is why the flag being False deleted the
    caller's answers. If the guard stops reading it, this fix is inert and the
    test should fail loudly rather than pass vacuously.
    """
    src = inspect.getsource(c)
    guard = src.index("Haiku skipped, timer re-armed")
    window = src[guard - 3000:guard]
    assert 'self.session.get("v3_location_q_active")' in window, (
        "the short-utterance discard no longer keys on v3_location_q_active"
    )
