"""No generic hold phrase, ever — every waiting moment has its own head.

Owner complaint, 12 Sep 2026: callers too often hear "sorry, still here" /
"one second" instead of the situational heads, most noticeably right after
their first sentence.

Measured on the last 100 stored calls (6-12 Sep 2026, 932 assistant turns,
hold_speech ON on all four lines):

    "Sorry, still with you —" / "Still with you —"   45 turns in 30 calls
    ...on the first reply of the call                  6 of 82
    ...standing alone, the answer a later turn        41 of 45
    classify_intent() on the utterance before each    40 of 45 returned []
    classify_intent() across ALL 403 caller turns    178 (44%) returned []

So the generic phrase was exactly the classifier's blind spot. The 40 misses
were eleven MOMENTS, not forty utterances; each row below is one of them, with
a corpus shape (paraphrased, never a caller's words) and the head it now earns.

Three things are pinned:

  1. every moment row classifies to the intended intent and renders the
     intended head;
  2. the 2.75s fallback (UNKNOWN_SLOW) is a RECEIPT, and no first-rung pool
     anywhere talks about the wait -- the apology exists only in LONG_WAIT,
     the second rung;
  3. the reopened 30 Aug decision: a pick that names no day gets
     "That one works —" and never echoes a time (D-r/D-s).
"""
from __future__ import annotations

import re

import pytest

from app.hold_speech import (
    EM_DASH,
    HEADS,
    INTENT_HEADS,
    Intent,
    WorkKind,
    classify_intent,
    head_families,
    render_head,
    render_intent_head,
    subject_for,
)

GREETING = ("Hi there, I'm Susie, Northgate Physiotherapy's AI receptionist "
            "— how can I help you today?")
READOUT = ("Tuesday 15th September — Number 1, half past ten in the morning. "
           "Number 2, ten past twelve. Number 3, ten past five in the evening.")
GENERIC = re.compile(
    r"\b(?:still (?:with you|here)|one sec\w*|one second|one moment|a moment|"
    r"bear with|just a sec\w*|hold on|hang on|right with you|just getting that)\b",
    re.IGNORECASE,
)


def _head(text, prev, **kw):
    hits = classify_intent(text, prev, **kw)
    if not hits:
        return "", []
    return render_intent_head(hits[0], subject=subject_for(text)), hits


# ── 1. the eleven moments ────────────────────────────────────────────────────

@pytest.mark.parametrize("said,prev,intent,head", [
    # opening turn: "to book an appointment" -- no want-verb
    ("um yeah that's to book an appointment", GREETING,
     Intent.BOOK_NEW, f"Let's get you booked in {EM_DASH}"),
    ("um to book an appointment mate", GREETING,
     Intent.BOOK_NEW, f"Let's get you booked in {EM_DASH}"),
    # opening turn: body part, no pain word, after the GREETING
    ("i am yeah a little bit of a problem with my left ankle it's nothing serious",
     GREETING, Intent.SYMPTOM, f"Sorry to hear that {EM_DASH}"),
    # "hello?" mid-call
    ("hello", "ankles can keep grumbling — worth getting looked at.",
     Intent.CHECK_IN, f"Yes, I'm here {EM_DASH}"),
    # a clock time to be near
    ("uh what have you got around 12", READOUT,
     Intent.TIME_AROUND, f"Let me see what I've got around twelve {EM_DASH}"),
    ("uh as close as possible to 12 please", READOUT,
     Intent.TIME_AROUND, f"Let me see what I've got around twelve {EM_DASH}"),
    # "what have you got" with no diary noun
    ("um what have you got", "Do you have a preference for when you'd like to come in?",
     Intent.AVAIL_QUERY, f"Let me see what we've got {EM_DASH}"),
    # yes to "would you like to book?"
    ("uh yes please", "Would you like to book an assessment so Priya can take a proper look?",
     Intent.BOOK_NEW, f"Let's get you booked in {EM_DASH}"),
    # yes to the number confirm
    ("um yes it is", "oh seven five oh two — is that the best number for the booking?",
     Intent.NUMBER_CONFIRMED, f"Thanks for that {EM_DASH}"),
    # the name
    ("um yeah that'll be quentin rock",
     "So that's Tuesday at twenty past eleven — could I take your first name and surname?",
     Intent.NAME_GIVEN, f"Thank you {EM_DASH}"),
    # preference answer swallowed by the bare-answer rule
    ("yeah anytime next week", "Do you have a preference for when you'd like to come in?",
     Intent.NAMED_WEEK, f"Let me look at next week for you {EM_DASH}"),
    # refusal / correction naming nothing concrete
    ("um that's not soon enough", READOUT, Intent.REFUSAL, f"Not to worry {EM_DASH}"),
    ("uh you got cut off", "Any of those work?", Intent.REFUSAL, f"Not to worry {EM_DASH}"),
    # message / relay
    ("hi i'm running late", GREETING, Intent.MESSAGE_REQ, f"Not a problem {EM_DASH}"),
    ("please can you let marcus know", "no worries — anything I can help with?",
     Intent.MESSAGE_REQ, f"Not a problem {EM_DASH}"),
    # which clinic -- options lifted from Susie's sentence, none in the code
    ("alcester", "Is this for our Alcester or Redditch clinic?",
     Intent.CLINIC_CHOSEN, f"Right you are {EM_DASH}"),
])
def test_each_corpus_moment_has_its_own_head(said, prev, intent, head):
    got_head, hits = _head(said, prev)
    assert hits and hits[0] is intent, (said, hits)
    assert got_head == head, (said, got_head)
    assert not GENERIC.search(got_head)


# ── 2. picks that name no day: "That one works —", never a time ─────────────

@pytest.mark.parametrize("said,prev,kw", [
    ("yeah 10 past 12 works", READOUT, {}),
    ("number 1 midday", "On Wednesday I also have — Number 1, midday.", {}),
    ("yeah that works", "the available slot is four in the afternoon. Does that work?", {}),
    ("yeah three works", "Number 3, three in the afternoon. Any of those work?", {}),
    ("ten in the morning", READOUT, {"slot_selection": True}),
])
def test_a_pick_without_a_day_gets_the_pick_head_and_echoes_no_time(said, prev, kw):
    head, hits = _head(said, prev, **kw)
    assert hits == [Intent.SLOT_PICKED], (said, hits)
    assert head == f"That one works {EM_DASH}", head


def test_a_day_pick_still_names_the_day():
    # A DAY becomes a pick only on the engine's verdict (B-90) -- without it
    # "friday works" is a request about a Friday we may not have offered.
    head, hits = _head("yeah 8 o clock friday works", "Which one works best for you?",
                       slot_selection=True)
    assert hits == [Intent.SLOT_PICKED]
    assert classify_intent("yeah 8 o clock friday works", "Which one works best for you?") == []
    assert head == f"Friday it is {EM_DASH}"


def test_a_request_in_pick_clothing_is_not_a_pick():
    """"what about around twelve" after a readout asks for MORE, not this."""
    head, hits = _head("uh what about around 12", READOUT)
    assert hits[0] is Intent.TIME_AROUND, hits


# ── 3. the pools ────────────────────────────────────────────────────────────

def test_the_first_rung_fallback_is_a_receipt_not_an_apology():
    pool = HEADS[WorkKind.UNKNOWN_SLOW]
    assert pool
    for head in pool:
        assert not GENERIC.search(head), head
        assert "sorry" not in head.lower(), head


def test_no_first_rung_pool_talks_about_the_wait():
    for pools in (HEADS, INTENT_HEADS):
        for key, pool in pools.items():
            if key is WorkKind.LONG_WAIT:
                continue
            for head in pool:
                assert not GENERIC.search(head), (key, head)
    assert not any("one sec" in h.lower() for h in HEADS[WorkKind.DIARY_READ])


def test_the_apology_lives_only_in_the_second_rung():
    pool = HEADS[WorkKind.LONG_WAIT]
    assert pool and all(h.lower().startswith("sorry") for h in pool)
    # Its own family, so the N4 same-family refusal lets rung 2 speak after
    # a receipt head.
    for first in HEADS[WorkKind.UNKNOWN_SLOW]:
        for second in pool:
            assert not (head_families(first) & head_families(second))
    assert render_head(WorkKind.LONG_WAIT, index=0) == pool[0]


# ── 4. the guards that must survive ─────────────────────────────────────────

def test_a_screen_answer_is_still_silence():
    assert classify_intent("yes please", "Is the calf swollen or warm to touch?") == []
    assert classify_intent("yes", "Any numbness?", screen_pending=True) == []


def test_hello_on_the_first_turn_is_just_a_hello():
    assert classify_intent("hello", GREETING) == []


def test_a_greeting_in_front_of_the_check_is_still_a_check():
    """CA5c69c585, 12 Sep 2026, first live run: "hello you still there" was
    read as a name answer and got "Thanks, got that —"."""
    hits = classify_intent("hello you still there",
                           "so that's Tuesday — could I take your first name and surname?")
    assert hits == [Intent.CHECK_IN], hits
    assert classify_intent("sorry", "could I take your first name and surname?") != [Intent.CHECK_IN]


def test_the_models_still_here_is_stripped_after_the_check_in_head():
    """CAe541a6a9 and CA5c69c585, 12 Sep 2026: head "I'm here, yes —", then the
    model's own "still here — could I take your name?" on top of it."""
    from app.hold_speech import strip_head_echo
    head = f"I'm here, yes {EM_DASH}"
    assert strip_head_echo("still here — could I take your first name?", head) == \
        "could I take your first name?"
    assert strip_head_echo("yes, still here — could I take your first name?", head) == \
        "could I take your first name?"
    assert strip_head_echo("Yes, I'm here — take your time.", head) == "take your time."


def test_a_bare_yes_to_the_name_question_has_not_given_a_name():
    assert classify_intent("yes", "could I take your first name and surname?") == []


def test_a_no_to_an_offer_is_a_refusal_not_a_booking():
    hits = classify_intent("no thanks", "Would you like to book in?")
    assert hits == [Intent.REFUSAL]


def test_the_historical_false_positives_stay_fixed():
    """The `number <digit>` proxy of 30 Aug silenced these for the rest of a
    call; the readout arm added here must not bring that back."""
    assert classify_intent("um what do you have next tuesday", READOUT)[0] is Intent.NAMED_DAY
    assert classify_intent("actually what's the soonest you've got", READOUT) == [Intent.EARLIEST]
