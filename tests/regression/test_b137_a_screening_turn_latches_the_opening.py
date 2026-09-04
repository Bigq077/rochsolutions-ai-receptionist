"""
Regression: the call's reason was None, on a call that opened with the reason.

B-137 — CAdd64c466dc13978306e5558817ce147e, northgate, 4 September 2026.

    11:33:31  FINAL: "um yeah hi i'd like to book an appointment essentially
                      i was playing football um and i rolled my ankle"
    11:33:31  screen trauma_fracture ARMED by that utterance
    …
    11:36:34  pre-SMS reason: collected=None session=None → None
    11:36:45  Row built — outcome=reached_confirmation name=Quentin Rook

No `[first_turn]` line appears anywhere in that call. The complaint was the
caller's FIRST sentence and the record kept nothing.

── WHY B-136 DID NOT COVER IT ─────────────────────────────────────────────────
B-136 added `commit_reason_answer` to the `ask_screen` short-circuit. That
helper only fires when the reason QUESTION was asked and its flag armed. On
this call Susie never asked it — the caller volunteered everything — so the
OPENING path was the one needed:

    commit_opening_reason  ->  reads session["opening_utterance"]

and `opening_utterance` had exactly ONE writer, in llm_stream's turn loop. The
screening short-circuit answers the whole turn and returns before that runs, so
the opening was never latched and every later commit had nothing to read.

The previous call (CA9c39d09f) needed the reason-ANSWER helper; this one needed
the OPENING helper. Same root cause — the screening branch swallowing the turn
— and two different halves of the same repair.

── WHY THE RULE MOVED ─────────────────────────────────────────────────────────
`note_opening_utterance` is the set-once rule lifted out of llm_stream so it has
ONE definition. It had one caller and needed two; leaving a second copy in
connection.py is the drift this codebase repeatedly records as the cause of a
defect rather than a tidiness complaint.
"""
from __future__ import annotations

import inspect

import pytest

from app.media_streams.first_turn_extractor import (
    commit_opening_reason,
    note_opening_utterance,
)

OPENING = (
    "um yeah hi i'd like to book an appointment essentially i was playing "
    "football um and i rolled my ankle"
)


# ---------------------------------------------------------------------------
# The live defect
# ---------------------------------------------------------------------------
def test_the_opening_complaint_becomes_the_reason():
    """THE call, end to end through the two helpers the branch now calls."""
    session = {"clinic_id": "northgate", "collected": {}}

    note_opening_utterance(session, OPENING)
    assert commit_opening_reason(session) is True
    assert session["reason"] == "rolled my ankle"
    assert session["collected"]["reason"] == "rolled my ankle"


def test_without_the_latch_there_is_nothing_to_commit():
    """Pins the DEFECT. This is precisely the state the call ended in: the
    utterance was consumed by the screening branch, so nothing was latched and
    the commit had nothing to read."""
    session = {"clinic_id": "northgate", "collected": {}}

    assert commit_opening_reason(session) is False
    assert not (session.get("reason") or "")


# ---------------------------------------------------------------------------
# The set-once rule, preserved exactly as llm_stream had it
# ---------------------------------------------------------------------------
def test_it_is_never_overwritten():
    """"Opening" means the FIRST thing the caller said, not the most recent."""
    session = {"collected": {}}
    note_opening_utterance(session, OPENING)
    note_opening_utterance(session, "something entirely different later on")

    assert session["opening_utterance"] == OPENING


def test_a_greeting_is_deferred_then_the_real_opening_wins():
    """A bare "hi" is a greeting, not an opening. Latching it would spend the
    one shot this gets on a turn that says nothing."""
    session = {"collected": {}}
    note_opening_utterance(session, "hello")
    note_opening_utterance(session, "hi")
    note_opening_utterance(session, OPENING)

    assert session["opening_utterance"] == OPENING


def test_the_deferral_is_bounded():
    """After two unsubstantive turns it takes whatever arrives. An unbounded
    search would let a quiet caller move the "opening" into the middle of the
    call, which is not what any reader of it expects."""
    session = {"collected": {}}
    note_opening_utterance(session, "hi")
    note_opening_utterance(session, "hello")
    note_opening_utterance(session, "yeah")

    assert session["opening_utterance"] == "yeah"


@pytest.mark.parametrize("junk", ["", "   ", None])
def test_junk_never_latches(junk):
    session = {"collected": {}}
    note_opening_utterance(session, junk)
    assert session.get("opening_utterance") is None


# ---------------------------------------------------------------------------
# Wired, not merely callable — B-134 shipped a file that stayed green when the
# fix was neutered, because it exercised the helpers and not the call site.
# ---------------------------------------------------------------------------
def test_the_screening_branch_latches_and_commits():
    from app.media_streams import connection

    src = inspect.getsource(connection)
    assert "_cs_note_opening(self.session, utterance)" in src
    assert "_cs_commit_opening(self.session)" in src


def test_the_commit_is_gated_on_the_clinic_asking():
    """A clinic that never asks a reason question (theorem, theorem_v3) keeps
    an empty reason as its own correct outcome, and must not silently gain one.
    Gated the same way llm_stream gates it."""
    from app.media_streams import connection

    src = inspect.getsource(connection)
    at = src.index("_cs_commit_opening(self.session)")
    assert "_cs_asks(self.session)" in src[at - 300:at]


def test_llm_stream_uses_the_same_rule_not_a_copy():
    """The whole point of extracting it. If llm_stream regrows its own copy,
    the two definitions drift and only one of them gets the next fix."""
    from app.media_streams import llm_stream

    src = inspect.getsource(llm_stream)
    assert "_note_opening(session, user_text)" in src
    assert 'session["opening_utterance"] = _ou' not in src
