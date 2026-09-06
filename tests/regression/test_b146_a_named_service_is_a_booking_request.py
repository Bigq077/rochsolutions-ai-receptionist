"""B-146 — a booking request that never says "book" gets a head, not an apology.

`CA5c65cb4b83091a538c915f5b234a4e8e`, northgate, 2026-09-05 23:13:07. The
caller's FIRST sentence:

    23:13:07.987  'yeah can i have a good sports massage please'
    23:13:07.987  treatment mention (FAQ, no booking intent) —
                  v3_treatment_mentioned set, booking_flow_active left False
    23:13:10.997  filler phrase triggered: 'Sorry, still with you —'   ← no head
    23:13:13.313  LAT turn_seq=19 llm_ttft_ms=4606 content_ttfa_ms=5328

`Intent.BOOK_NEW` triggers on `book|booking|appointment`. The caller named a
SERVICE and a want verb and never said "book", so `classify_intent` returned
[], nothing spoke at 600ms, the model took 4.6s, and `UNKNOWN_SLOW` apologised
for a wait on the opening turn of the call.

ADDING "MASSAGE" TO THE TRIGGER IS THE TRAP. This file already carries the
lesson twice — once above `_HURT` ("adding more synonyms is the trap, the SHAPE
of the matcher is the bug") and once in the screening-trigger bigram defect it
cites. The shape here is right: a request verb corroborated by what is being
asked for. The corroborator was the half that could only see the word
"appointment".

So the SERVICE half is asked of the engine, which decided it one line earlier —
the same `_is_treatment_specific_booking` that writes `v3_treatment_mentioned`
— exactly as `slot_selection` asks for B-90's verdict rather than guessing it.

SCOPE. This changes what is SAID while the caller waits. It does NOT touch
`booking_flow_active`: that flag reaches the write gates, and its FAQ
false-positive is BUG-7, an owner-signed decision recorded in connection.py.
Whether "can I have a sports massage" should also open the booking flow is a
separate call with a separate blast radius.
"""
from __future__ import annotations

import pytest

from app.hold_speech import Intent, classify_intent
from app.media_streams.connection import _is_treatment_specific_booking

GREETING = "Hi there, I'm Susie — how can I help today?"


def _classify(utterance, prev=GREETING, **kw):
    return classify_intent(
        utterance, prev,
        service_named=_is_treatment_specific_booking(utterance),
        **kw,
    )


# ── The live sentence, and the requests around it ───────────────────────────

@pytest.mark.parametrize("utterance", [
    "yeah can i have a good sports massage please",   # the live one
    "can i have a sports massage",
    "could i have a deep tissue massage",
    "i'd like a sports massage",
    "i want a sports massage",
    "i need some acupuncture",
    "i'm after a sports massage",
    "book me a sports massage",
])
def test_a_named_service_earns_the_booking_head(utterance):
    assert Intent.BOOK_NEW in _classify(utterance), utterance


def test_the_live_turn_no_longer_falls_to_the_apology():
    """The defect in one assertion: [] is what produced "Sorry, still with you
    —" 3s later, because `UNKNOWN_SLOW` is the fallback for a turn whose work
    is unknown."""
    said = "yeah can i have a good sports massage please"
    assert classify_intent(said, GREETING) == [], (
        "fixture drift: this used to classify as nothing at all"
    )
    assert _classify(said) == [Intent.BOOK_NEW]


# ── What must NOT get a booking head ────────────────────────────────────────

@pytest.mark.parametrize("utterance", [
    "do you do sports massage",
    "can you do sports massage",
    "d'you offer acupuncture",
    "how much is a sports massage",
    "how long is a sports massage",
    "does the sports massage hurt",
    "is acupuncture any good",
    "what is dry needling",
    "i'd like to know about sports massage",
    "i wanted to ask about acupuncture",
    "just wondering about sports massage prices",
])
def test_a_question_about_a_service_gets_no_booking_head(utterance):
    """"Let's get you booked in —" in front of a price question promises work
    nobody asked for. First person only, and enquiry verbs block outright."""
    assert Intent.BOOK_NEW not in _classify(utterance), utterance


@pytest.mark.parametrize("utterance", [
    "i don't want a massage",
    "i want to cancel my sports massage",
    "i'd like to move my sports massage",
    "can i reschedule the acupuncture",
])
def test_a_negation_or_a_write_verb_declines(utterance):
    assert Intent.BOOK_NEW not in _classify(utterance), utterance


def test_an_utterance_naming_no_service_is_unchanged():
    """The flag is the whole difference. Nothing fires without it."""
    assert Intent.BOOK_NEW not in classify_intent(
        "yeah can i have a good sports massage please", GREETING,
    )


def test_the_head_is_suppressed_while_the_caller_is_answering():
    """BOOK_NEW is a diary intent, so it obeys `answering` like every other
    one — a service named in ANSWER to a confirm question is not a fresh
    request."""
    assert Intent.BOOK_NEW not in _classify(
        "i'd like a sports massage", slot_selection=True,
    )


def test_a_screen_still_silences_everything():
    """A head in front of a clinical screen is the promised-work defect at its
    worst, and no new arm may reach past that gate."""
    assert _classify("i'd like a sports massage", screen_pending=True) == []


def test_the_verdict_comes_from_the_engine_not_a_word_list():
    """The service half is the engine's own detector, asked for by
    `llm_stream`. A second vocabulary here would be a second answer to "does
    this clinic sell that?"."""
    import inspect

    from app.media_streams import llm_stream

    src = inspect.getsource(llm_stream)
    assert "_is_treatment_specific_booking as _names_service" in src
    assert "service_named=_hs_service" in src
    # Read off the UTTERANCE, never off the call-scoped latch: a head keyed on
    # `v3_treatment_mentioned` would fire on every later turn of a call in
    # which a service was mentioned once. B-138 is that mistake.
    assert "_names_service(_hs_utterance" in src
