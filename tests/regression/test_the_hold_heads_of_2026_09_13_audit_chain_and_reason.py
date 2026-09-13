"""Hold-head audit of 13 Sep 2026, owner decisions "A and A":
the thank-you chain, and the lookup head in front of the reason gate.

THE CHAIN. Every booking heard four acknowledgements running:
    "Thanks, got that —"  (first name)
    "Thank you —"         (surname)
    "Thanks for that —"   (number)
    "That's noted —"      (summary)
Each correct; the run is the tell. The surname answer drops its head: a
one-word reply, the model answers in 1.5-2.0s, and the 2.75s receipt rung
never fired on one in the audit -- and still covers it if the model stalls.

THE REASON GATE. "what's the soonest you've got" drew "Let me find the soonest
I've got —" and then "what's the appointment for?": BOOKING STEPS 1b has the
model ask the reason before it opens the diary. While the reason is owed, the
lookup heads yield to BOOK_NEW ("Let's get you booked in —"), which names no
diary. `reason_pending` is the engine's verdict, computed in llm_stream from
the clinic's reason_question key and the A2 reason slots.
"""
from __future__ import annotations

import pytest

from app.hold_speech import Intent, classify_intent

# ── The chain ──────────────────────────────────────────────────────────────

FIRST_NAME_Q = "Lovely — could I take your first name?"
WHOLE_NAME_Q = "So that's Tuesday — could I take your first name and surname?"
SURNAME_Q = "Thanks Elektra — and your surname?"
LAST_NAME_Q = "Thanks Elektra, and what's your last name?"
NUMBER_Q = "Is the number you're calling from the best number for the booking?"


@pytest.mark.parametrize("prev", [SURNAME_Q, LAST_NAME_Q, "And could I take your surname please?"])
@pytest.mark.parametrize("answer", ["roch", "it's roch", "Kowalczyk"])
def test_the_surname_answer_gets_no_head(prev, answer):
    assert classify_intent(answer, prev) == [], (
        "'Thank you —' on the surname is the second link of a four-head chain"
    )


@pytest.mark.parametrize("prev,answer", [
    (FIRST_NAME_Q, "elektra"),
    (WHOLE_NAME_Q, "elektra roch"),
])
def test_the_first_and_whole_name_keep_their_head(prev, answer):
    assert classify_intent(answer, prev) == [Intent.NAME_GIVEN]


def test_the_number_keeps_its_head():
    assert classify_intent("yes", NUMBER_Q) == [Intent.NUMBER_CONFIRMED]


# ── The reason gate ────────────────────────────────────────────────────────

GREETING = ("Hi there, I'm Susie, Northgate Physiotherapy's AI receptionist "
            "— how can I help you today?")
PREF_Q = "Do you have a preference for when you'd like to come in?"


@pytest.mark.parametrize("utterance,lookup", [
    ("what's the soonest you've got", Intent.EARLIEST),
    ("i'd like to book the earliest appointment please", Intent.EARLIEST),
    ("can i come in on tuesday", Intent.NAMED_DAY),
    ("have you got anything in the afternoon", Intent.TIME_BAND),
    ("what have you got next week", Intent.NAMED_WEEK),
])
def test_a_lookup_head_waits_for_the_reason(utterance, lookup):
    assert lookup in classify_intent(utterance, GREETING), "precondition"
    intents = classify_intent(utterance, GREETING, reason_pending=True)
    assert intents and intents[0] is Intent.BOOK_NEW, intents
    assert not any(i in intents for i in (
        Intent.EARLIEST, Intent.NAMED_DAY, Intent.NAMED_WEEK, Intent.TIME_BAND,
        Intent.TIME_AROUND, Intent.AVAIL_QUERY, Intent.SESSION_LENGTH,
    )), intents
    assert intents.count(Intent.BOOK_NEW) == 1, intents


@pytest.mark.parametrize("utterance", [
    "what's the soonest you've got",
    "can i come in on tuesday",
])
def test_once_the_reason_is_in_the_lookup_head_fires(utterance):
    intents = classify_intent(utterance, PREF_Q, reason_pending=False)
    assert intents and intents[0] in (Intent.EARLIEST, Intent.NAMED_DAY), intents


@pytest.mark.parametrize("utterance", [
    "i've hurt my knee, what's the soonest you've got",
    "my back's playing up, can i come in tuesday",
])
def test_a_complaint_in_the_same_breath_is_the_reason(utterance):
    intents = classify_intent(utterance, GREETING, reason_pending=True)
    assert intents[0] is Intent.SYMPTOM, intents
    assert Intent.BOOK_NEW not in intents or Intent.EARLIEST not in intents


def test_an_answer_to_the_reason_question_releases_the_lookup_head():
    prev = "Of course — what's the appointment for?"
    intents = classify_intent("just my left ankle, soonest please", prev, reason_pending=True)
    assert Intent.BOOK_NEW not in intents, intents


def test_an_faq_ahead_of_the_lookup_keeps_its_place():
    intents = classify_intent("how much is it and what's the soonest", GREETING,
                              reason_pending=True)
    assert intents[:2] == [Intent.FAQ_PRICE, Intent.BOOK_NEW], intents


def test_the_flag_does_not_invent_a_head():
    assert classify_intent("is there parking", GREETING, reason_pending=True) == [
        Intent.FAQ_PARKING
    ]
    assert classify_intent("yes", "Is that the best number for the booking?",
                           reason_pending=True) == [Intent.NUMBER_CONFIRMED]


def test_llm_stream_passes_the_verdict():
    import inspect

    from app.media_streams import llm_stream

    src = inspect.getsource(llm_stream)
    assert "reason_pending=_hs_reason_pending" in src
    assert "_clinic_asks_its_own_reason_question as _hs_asks_reason" in src
