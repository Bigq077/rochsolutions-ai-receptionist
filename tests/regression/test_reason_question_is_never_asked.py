"""
Regression: Susie asked what brings the caller in.

Owner decision 2026-08-07, restated 2026-08-08: the reason for the visit is
recorded ONLY when the caller volunteers it, unprompted, in their own words.
There is no turn, step or slot in the flow where asking is correct. An empty
reason is a correct outcome, not a gap to fill — `first_turn_extractor`
captures it deterministically when the caller names a body part themselves.

Two earlier fixes were drafted and withdrawn because they RE-ORDERED the
question rather than removing it (see O-5). Suppression is part of the fix.

CORRECTION 2026-08-09. This docstring used to claim "the prompt has said so
since susie_system_prompt.py:901". It did not — not on this clinic. Line 901 is
inside the `fast_booking` branch, and theorem_v3 renders through
`_build_theorem_v3`, which carried no such rule and DID carry a worked example
demonstrating the question ("Susie: 'No problem at all — what brings you in
today?'"). Two more instructions to ask were live at the same time: the
`reason` field of the book_appointment schema ("Ask for it before checking
availability"), and the A2 gate, which refused the booking outright and told
the model in its error text to ask "What's the appointment for?".

So the model was not disobeying. Three things ordered it to ask, nothing
forbade it, and only the output was suppressed — which is why the question kept
coming back. All three generation-side causes are fixed below; the gate is now
the backstop it was meant to be rather than the entire defence.

Observed on CA041352eb04a40fcb5ebd13ee37379722 (2026-08-08 00:01:04), where the
ENTIRE turn was the reason question, in two phrasings back to back:

    'Before I go ahead and check that day, could I ask what brings you in?'
    "what's the appointment for?"

and on CA1e7552819091949f02c08a39f5203d36 (2026-08-07 23:43:07), where it was
folded into a reply that also carried the slot readback and the name request:

    "Before I get that booked, could I ask what's bringing you in? So that's
     Monday the 10th of August at five in the evening — could I take your first
     name and surname?"

Those two shapes are why the rule is not in the flat _BANNED_SENTENCE_RE list.
The folded case must lose one sentence and keep the rest. The whole-turn case
strips to nothing, and an empty turn is NOT safe: it falls through to the
deferred Gate-5 fallback, which speaks "Sorry, I didn't quite catch that" —
a non-sequitur answering a question the caller was never meant to hear.
"""
from __future__ import annotations

import pytest

from app.media_streams.turn_handler import (
    _REASON_QUESTION_RE,
    _next_booking_question_for,
    sanitise_response,
)


# ── the phrasings, verbatim from the calls ──────────────────────────────────

@pytest.mark.parametrize(
    "sentence",
    [
        "Before I go ahead and check that day, could I ask what brings you in?",
        "what's the appointment for?",
        "What is the appointment for?",
        "Could I ask what's bringing you in?",
        "So what's going on with it?",
        "What's going on with that?",
        "What's been troubling you?",
        "What's the issue?",
    ],
)
def test_reason_question_is_stripped(sentence):
    assert _REASON_QUESTION_RE.sub("", sentence).strip() == "", sentence


# ── things that merely look similar and must survive ────────────────────────

@pytest.mark.parametrize(
    "sentence",
    [
        "Is there a particular day or time that works best for you?",
        "Could I take your first name and surname?",
        "Here's what we've got coming up — Number 1, Monday 10th August.",
        "Is this for our Alcester or Redditch clinic?",
        "So that's Quentin Rock, Monday the 10th of August at five in the "
        "evening — shall I go ahead and book that in?",
        "What's the best number to reach you on?",
    ],
)
def test_ordinary_questions_are_untouched(sentence):
    assert _REASON_QUESTION_RE.sub("", sentence) == sentence, sentence


# ── the folded case: lose one sentence, keep the rest ───────────────────────

def test_only_the_reason_sentence_is_removed_from_a_longer_reply():
    """CA1e755281 23:43:07 — the readback and the name request must survive."""
    reply = (
        "Before I get that booked, could I ask what's bringing you in? "
        "So that's Monday the 10th of August at five in the evening — "
        "could I take your first name and surname?"
    )
    out = _REASON_QUESTION_RE.sub("", reply).strip()
    assert "bringing you in" not in out
    assert "Monday the 10th of August" in out
    assert "first name and surname" in out


# ── the whole-turn case: must not hand back an empty turn ───────────────────

def test_a_turn_that_was_only_the_reason_question_asks_the_real_step_instead():
    """
    CA041352eb 00:01:04 — the whole turn was the reason question. Stripping to
    "" drops into the deferred Gate-5 fallback ("Sorry, I didn't quite catch
    that"), which is a non-sequitur. The outstanding booking step is asked
    instead.
    """
    session = {"booking_flow_active": True}
    out = sanitise_response(
        "Before I go ahead and check that day, could I ask what brings you in?",
        session,
    )
    assert out.strip(), "the turn was emptied — caller gets the fallback re-ask"
    assert "brings you in" not in out.lower()
    assert out.strip() == _next_booking_question_for(session).strip()


def test_both_reason_phrasings_in_one_turn_still_leave_a_question():
    """The same call asked it twice in a row, as two separate sentences."""
    session = {"booking_flow_active": True}
    out = sanitise_response(
        "Before I go ahead and check that day, could I ask what brings you in? "
        "what's the appointment for?",
        session,
    )
    assert out.strip()
    assert "brings you in" not in out.lower()
    assert "appointment for" not in out.lower()


# ── the substitution must not be a reason question itself ───────────────────

def test_the_substituted_question_is_not_itself_stripped():
    """
    A replacement that the same gate deletes would empty the turn on the next
    pass. This already happened once in this file's sibling gate, where a
    replacement carrying CTA vocabulary deleted itself.
    """
    for session in (
        {"booking_flow_active": True},
        {"booking_flow_active": True, "patient_name": "Quentin Rock"},
    ):
        q = _next_booking_question_for(session)
        assert _REASON_QUESTION_RE.sub("", q) == q, q


# ── the residue case: a survivor that asks nothing is still dead air ─────────
#
# CAb1592daa 2026-08-08 23:16:42. The reply was the reason question plus a
# justifying clause. The strip took the question; the clause survived, so the
# dead-end substitution above never fired. The caller heard "Just so Mark has a
# heads up.", then nothing for nine seconds, then said "hello" into the silence
# and hung up thirty seconds later.

def test_a_dangling_justification_does_not_survive_alone():
    """The clause only made sense attached to the question that was removed."""
    session = {"booking_flow_active": True}
    out = sanitise_response(
        "What's the appointment for? Just so Mark has a heads up.", session
    )
    assert "heads up" not in out.lower(), (
        "the caller hears a justification for a question they were never asked"
    )
    assert "?" in out, "the turn asks nothing — this is the nine seconds of silence"
    assert out.strip() == _next_booking_question_for(session).strip()


@pytest.mark.parametrize(
    "residue",
    [
        "Just so Mark has a heads up.",
        "So Mark knows what to prepare.",
        "That way Mark can prepare properly.",
        "And it helps Mark prepare.",
    ],
)
def test_every_justification_shape_leaves_a_question_on_the_table(residue):
    session = {"booking_flow_active": True}
    out = sanitise_response(f"What's the appointment for? {residue}", session)
    assert "?" in out, f"dead air after: {residue}"


def test_substantive_content_survives_and_gains_a_question():
    """
    A slot readback is the caller's, not the model's scaffolding — it must not
    be thrown away with the question. But the turn still has to ask something.
    """
    session = {"booking_flow_active": True}
    out = sanitise_response(
        "So that's Wednesday the 19th of August at ten in the morning. "
        "What's the appointment for?",
        session,
    )
    assert "Wednesday the 19th of August" in out
    assert "appointment for" not in out.lower()
    assert "?" in out


def test_a_reply_that_still_asks_something_is_left_alone():
    """No substitution when the caller already has a question to answer."""
    session = {"booking_flow_active": True}
    out = sanitise_response(
        "What's the appointment for? And could I take your first name and surname?",
        session,
    )
    assert "first name and surname" in out
    assert out.count("?") == 1, "a second question was bolted on"


def test_the_substitution_fires_at_most_once_per_turn():
    """
    sanitise_response runs per streamed chunk. Without the latch a turn split
    across two chunks would have the outstanding step appended twice, and the
    caller would be asked their name in the same breath, twice.
    """
    session = {"booking_flow_active": True}
    first = sanitise_response("What's the appointment for?", session)
    second = sanitise_response("Just so Mark has a heads up.", session)
    assert "?" in first
    assert "first name" not in second.lower(), "asked twice in one turn"


def test_the_latch_does_not_leak_into_the_next_turn():
    """
    A turn-scoped latch left set would silently restore the dead air it exists
    to prevent — the failure would look exactly like no fix at all.
    """
    session = {"booking_flow_active": True}
    sanitise_response("What's the appointment for?", session)
    session.pop("_gate5br_substituted", None)          # what llm_stream does per turn
    out = sanitise_response(
        "What's the appointment for? Just so Mark has a heads up.", session
    )
    assert "?" in out


# ── the generation side: nothing may instruct the model to ask ──────────────

def test_theorem_book_appointment_does_not_ask_for_a_reason():
    """
    The schema ships with every request and is not subject to the prompt. While
    it said "Ask for it before checking availability", no amount of output
    suppression could hold.
    """
    from app.tools.receptionist_tools import build_tool_schemas

    for tool in build_tool_schemas("theorem_v3"):
        reason = (tool.get("input_schema") or {}).get("properties", {}).get("reason")
        if tool["name"] != "book_appointment" or not reason:
            continue
        desc = reason["description"]
        assert "Ask for it" not in desc
        assert "REQUIRED IN PRACTICE" not in desc
        assert "NEVER ask" in desc
        break
    else:
        pytest.fail("book_appointment has no reason field — test is stale")


def test_other_clinics_keep_the_reason_requirement():
    """Scope corrected 2026-08-08: asking is CORRECT on JV and Vital Edge."""
    from app.tools.receptionist_tools import build_tool_schemas

    for tool in build_tool_schemas("jv_v1"):
        if tool["name"] == "book_appointment":
            desc = tool["input_schema"]["properties"]["reason"]["description"]
            assert "REQUIRED IN PRACTICE" in desc
            return
    pytest.fail("jv_v1 has no book_appointment — test is stale")


def test_the_theorem_prompt_forbids_the_question_and_never_demonstrates_it():
    from app.prompts.susie_system_prompt import _build_theorem_v3

    static, dynamic = _build_theorem_v3(
        {"clinic_id": "theorem_v3", "twilio_from": "+447502211207"}
    )
    rendered = f"{static}\n{dynamic}".lower()
    assert "the reason question is permanently banned" in rendered
    assert "what brings you in today" not in rendered, (
        "the prompt demonstrates the question the gate deletes"
    )
