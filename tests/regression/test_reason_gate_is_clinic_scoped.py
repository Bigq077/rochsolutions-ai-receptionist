"""
Regression: Gate 5b-r must not strip the reason question at the one clinic that
asks it on purpose.

Two owner decisions are both live and they point opposite ways:

  2026-08-07/08 (Theorem)         Susie NEVER asks what brings the caller in.
                                  Enforced by Gate 5b-r in sanitise_response.
  2026-08-04    (Vital Edge)      Susie DOES ask, in the clinic's own wording,
                                  exactly once — 902411a / bec1b5e, gated on
                                  prompt_facts.reason_question.
  2026-08-11    (jv_v1)           Susie DOES ask. jv_v1 was originally listed
                                  under the Theorem decision; the owner scoped
                                  that decision to Theorem only, and jv_v1 now
                                  opts in the same way Vital Edge does.

jv_v1 is the case this gate was designed for: it opted in with a clinic.json key
and NO engine edit, which is what test_the_gate_reads_config_not_a_hardcoded_
clinic_name exists to keep possible.

The cost of the wrong default is not theoretical — jv_v1 ran it. With the key
absent the model asked, the gate deleted the sentence, the caller heard the
substituted name question instead, no reason was ever collected, and every
book_appointment call died on the A2 gate in receptionist_tools.py. That is the
same chain spelled out below, reached from the other side.

Gate 5b-r arrived from theorem-onboarding with no clinic gate. Landing it on the
canonical branch ungated is a booking-failure landmine for Vital Edge, and — the
part that matters — THE SUITE CANNOT SEE IT. Vital Edge's mandated wording,

    "Is there a particular area or reason for the massage — like back tension,
     general stress, or something else?"

does not match _REASON_QUESTION_RE, so test_reason_question_once stays green
either way. But the model composes each turn freely, and on CA86c320ef it
improvised "Right — What's the appointment for?", which the regex DOES strip.
The chain from there:

    question stripped -> never spoken -> note_reason_question_asked never
    latches -> no reason collected -> book_appointment REFUSES for want of one

i.e. a silent booking failure at a live clinic, produced by a fix for a
different clinic, invisible to every existing test.

These tests pin the gate on the phrasing that actually reaches the gate — the
model's improvisation, not the clinic's mandated sentence — because pinning the
mandated sentence would pass with the gate removed and prove nothing.
"""
from __future__ import annotations

import pytest

from app.media_streams.turn_handler import (
    _REASON_QUESTION_RE,
    _clinic_asks_its_own_reason_question,
    sanitise_response,
)

# The phrasing the model actually produced on a live Vital Edge call, and the
# one the regex strips. Using the clinic's own mandated wording here would make
# these tests pass with the gate deleted.
IMPROVISED = "Right — What's the appointment for?"


def test_the_improvised_phrasing_is_what_the_regex_targets():
    """Precondition. If this ever stops matching, the tests below go vacuous."""
    assert _REASON_QUESTION_RE.search(IMPROVISED), (
        "the fixture no longer exercises the gate — pick a phrasing the regex "
        "still strips, or these tests prove nothing"
    )


@pytest.mark.parametrize("clinic_id", ["vital_edge", "jv_v1"])
def test_a_clinic_that_opted_in_keeps_its_reason_question(clinic_id):
    session = {"clinic_id": clinic_id}
    assert _clinic_asks_its_own_reason_question(session) is True

    out = sanitise_response(IMPROVISED, session)
    assert "appointment for" in out, (
        f"Gate 5b-r stripped {clinic_id}'s reason question. The question is "
        "never spoken, so the once-only latch never fires, so no reason is "
        "collected, so book_appointment refuses — a silent booking failure"
    )


@pytest.mark.parametrize("clinic_id", ["theorem", "theorem_v3"])
def test_every_other_clinic_still_has_the_question_suppressed(clinic_id):
    session = {"clinic_id": clinic_id}
    assert _clinic_asks_its_own_reason_question(session) is False

    out = sanitise_response(IMPROVISED, session)
    assert "appointment for" not in out, (
        f"{clinic_id} must never ask what brings the caller in — the owner "
        f"decision Gate 5b-r exists to enforce"
    )


def test_an_unknown_clinic_fails_closed():
    """The safe direction: no opt-in means the suppression still runs."""
    for session in ({}, {"clinic_id": None}, {"clinic_id": "does_not_exist"}):
        assert _clinic_asks_its_own_reason_question(session) is False
        assert "appointment for" not in sanitise_response(IMPROVISED, session)


def test_the_gate_reads_config_not_a_hardcoded_clinic_name():
    """
    The gate must key off prompt_facts.reason_question, not off the string
    "vital_edge". A clinic that opts in tomorrow has to work without an engine
    edit — this repo's rule is that clinic behaviour lives in clinic.json.
    """
    import inspect

    from app.media_streams import turn_handler

    src = inspect.getsource(turn_handler._clinic_asks_its_own_reason_question)
    assert "reason_question" in src
    assert "vital_edge" not in src, (
        "the gate hardcodes a clinic name — that is the bug, not the fix"
    )
