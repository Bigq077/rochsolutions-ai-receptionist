"""
Regression: "uh yes" to "Is that number okay to use for the booking?" confirms
the calling number. The phone question is recognised by its WORDS or by the
NUMBER in it, not by a fixed list of wordings alone.

CAcae592ce833c6556980d1c7e62288184, northgate demo line, build 2dfc9b01,
11 Sep 2026:

    10:12:04  "Thanks Sandrine — I've got you on oh seven five oh two, two one
               one, two oh seven — is that the best number for the booking?"
                                                 (turn 11, superseded)
    10:12:06  "Is that number okay to use for the booking?"    (turn 12, stuck)
    10:12:16  caller: "uh yes"
    10:12:16  [ms_conn] phone DTMF STAYS ARMED — the caller spoke but no
              number is on record yet: 'uh yes'
    10:12:18  [ms_gate5] booking CTA held back — phone missing; asked for it
              instead: 'Before I do that — is 0 7 5 0 2 2 1 1 2 0 7 the best
              number ... If so, just say use this number.'
    10:12:31  caller: "use this number it is you already asked me 3 times"

`_phone_confirm_is_yes("uh yes")` is True. `_phone_question_on_the_table`
was False: the question that stuck was in the model's own words and matched
none of `_PHONE_STEP_MARKERS`, a list that had been patched one literal at a
time since July (B-25's own docstring lists three such patches). Fails
closed, by design -- and the cost of failing closed here was a demo caller
asked the same question three times.

Two changes. The observed wording joins the markers (both copies). And the
gate gains a rule that cannot go stale: a prompt that reads the caller's own
digits back IS the phone question, whatever words surround them -- a surname
question, a clinic question and a slot readout never contain the caller's
number.
"""
from __future__ import annotations

import pytest

from app.media_streams import connection as c
from app.media_streams.llm_stream import _PHONE_STEP_MARKERS as LLM_MARKERS
from app.prompts.clinic_template_prompt import _PHONE_STEP_MARKERS as TPL_MARKERS

CALLER = "07502211207"


def _session(last_question: str) -> dict:
    return {"twilio_from_local": CALLER, "last_question": last_question}


# ── the call ───────────────────────────────────────────────────────────────

def test_the_question_that_stuck_is_the_phone_question():
    s = _session("Is that number okay to use for the booking?")
    assert c._phone_question_on_the_table(s)
    assert c._phone_confirm_is_yes("uh yes")


@pytest.mark.parametrize("q", [
    "Is that number okay to use for the booking?",
    "Is that number ok to use for the booking?",
    "And is this number okay to use for the booking?",
])
def test_the_models_wordings_are_markers_in_both_copies(q):
    low = q.lower()
    assert any(m in low for m in LLM_MARKERS), q
    assert any(m in low for m in TPL_MARKERS), q


def test_all_three_marker_copies_agree():
    from app.media_streams.latency_timing import _PHONE_QUESTION_MARKERS as LT
    assert set(LLM_MARKERS) == set(TPL_MARKERS) == set(LT)


# ── the number is a marker ─────────────────────────────────────────────────

@pytest.mark.parametrize("q", [
    # the superseded turn 11, verbatim
    "Thanks Sandrine — I've got you on oh seven five oh two, two one one, "
    "two oh seven — is that the best number for the booking?",
    # the same read-back under a wording no list has seen
    "So is oh seven five oh two, two one one, two oh seven right for you?",
    "Shall I put you down on oh seven five oh two two one one two oh seven?",
    # written, as the model sometimes emits it
    "Just to confirm, 07502 211207 — shall I use that?",
    "Is 07502211207 correct?",
    # clipped at 200 chars (B-31): opener gone, number intact
    "...two, two one one, two oh seven for the appointment?",
])
def test_a_prompt_that_reads_the_callers_number_back_is_the_phone_question(q):
    assert c._phone_question_on_the_table(_session(q)), q


@pytest.mark.parametrize("q", [
    "Did you say Sandrine — is that right?",
    "Thanks Quentin — and your surname?",
    "No problem at all — on your keypad, just press 1 for Awlstuh, or 2 for Redditch",
    "Monday 14th September — Number 1, eight in the morning. Number 2, twenty "
    "to three in the afternoon. Number 3, ten past five in the evening. Any of those work?",
    "So that's Monday the 14th of September at ten past twelve — could I take "
    "your first name and surname?",
    # a different number is not the caller's
    "I've got 07700 900123 on file — is that still right?",
    # a partial read is not enough (fails closed)
    "Is it oh seven five oh two?",
])
def test_questions_that_are_not_the_phone_question_stay_closed(q):
    s = _session(q)
    # the B-25 shapes must still be refused; strip markers that legitimately
    # match ("i've got") -- these prompts carry none, so the gate's answer is
    # the number rule's answer.
    assert not c._phone_question_on_the_table(s), q


def test_no_caller_id_means_the_number_rule_cannot_fire():
    s = {"last_question": "So is oh seven five oh two, two one one, two oh seven right?"}
    assert not c._phone_question_on_the_table(s)


@pytest.mark.parametrize("prompt, number, want", [
    ("oh seven five oh two two one one two oh seven", "07502211207", True),
    ("07502 211207", "+447502211207", True),        # E.164 caller-ID, last 7
    ("seven five oh two", "07502211207", False),
    ("", "07502211207", False),
    ("oh seven five oh two two one one two oh seven", "", False),
    ("phone one someone", "07502211207", False),   # "one" inside words is not a digit
])
def test_reads_back(prompt, number, want):
    assert c._prompt_reads_back_caller_number(prompt, number) is want
