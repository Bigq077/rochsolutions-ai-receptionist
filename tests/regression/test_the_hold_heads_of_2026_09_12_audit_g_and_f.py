"""Hold-head audit of 12-13 Sep 2026 (10 demo calls, 56 heads since c117c0b4):
G -- a write head on a yes to the OFFER; F -- a lookup head on a past day.

G (P2, "a write claimed, none made"). Three turns:
    CAe541a6a9 T10  Susie: "...ten past twelve on Monday. Shall I book that in
                            for you?"  (slot_followup's one-slot OFFER, before
                            the name)
                    caller: "uh yeah go for it"
                    head  : "Getting that in the diary —"
                    model : "so that's Monday ... could I take your first name?"
    CAafb7f031 T4   same shape; the head then preceded a wrong re-read
    CAafb7f031 T6   "Right, booking you in —" then "could I take your name?"

`_CONFIRM_CTA` matched "book that in" alone. The WRITE CTA is Step F5's
"shall I go ahead and book that in?", spoken only with name and phone in
hand; "Shall I book that in for you?" is the offer, and a yes to it is a
PICK -- "That one works —" from `_answer_moment`.

F (P3, promised work). Six turns, five calls, one opener:
    caller: "um so the thing is um last tuesday"
    head  : "Let me see what Tuesday looks like —"
    model : "take your time — go ahead."
A day in the PAST is a story, not a diary request. NAMED_DAY now has a
blocker for "last <day>" / "since <day>". (The engine-side `day_preference`
capture of the same utterance is a separate change.)
"""
from __future__ import annotations

import pytest

from app.hold_speech import (
    Intent,
    WorkKind,
    classify_intent,
    confirm_write_kind,
)

OFFER = "Yes — ten past twelve in the afternoon on Monday 14th September is free. Shall I book that in for you?"
F5_CTA = ("So that's Quentin Rock, Monday the 14th of September at ten past twelve "
          "in the afternoon — shall I go ahead and book that in?")
F5_CTA_PUT = "Midday on Monday the 14th — shall I go ahead and put that in for you?"


# ── G ──────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("yes", ["uh yeah go for it", "yes please", "yeah"])
def test_a_yes_to_the_one_slot_offer_is_not_a_write(yes):
    assert confirm_write_kind(OFFER, True) is WorkKind.NONE, (
        "'Getting that in the diary —' on a yes to the OFFER, before the name"
    )
    intents = classify_intent(yes, OFFER, slot_selection=True)
    assert Intent.SLOT_PICKED in intents, intents


@pytest.mark.parametrize("cta", [F5_CTA, F5_CTA_PUT])
def test_a_yes_to_the_f5_summary_is_still_the_write(cta):
    assert confirm_write_kind(cta, True) is WorkKind.WRITE_BOOK


def test_a_no_to_the_f5_summary_is_still_no_write():
    assert confirm_write_kind(F5_CTA, False) is WorkKind.NONE


# ── F (head half) ──────────────────────────────────────────────────────────

GREETING = ("Hi there, I'm Susie, Northgate Physiotherapy's AI receptionist "
            "— how can I help you today?")


@pytest.mark.parametrize("utterance", [
    "um so the thing is um last tuesday",
    "so the thing is last tuesday",
    "i did my back in last saturday",
    "it's been bad since monday",
])
def test_a_past_day_gets_no_lookup_head(utterance):
    intents = classify_intent(utterance, GREETING)
    assert Intent.NAMED_DAY not in intents, (
        f"{utterance!r}: 'Let me see what <day> looks like —' promised a lookup "
        "the model rightly never did (5 of 5 calls opening this way)"
    )


@pytest.mark.parametrize("utterance", [
    "can i come in on tuesday",
    "what about tuesday",
    "tuesday afternoon",
    "next tuesday would be good",
])
def test_a_future_day_still_gets_its_head(utterance):
    intents = classify_intent(utterance, "Do you have a preference for when you'd like to come in?")
    assert Intent.NAMED_DAY in intents, utterance


# ── #3: the symptom outranks the FAQ (owner, 13 Sep) ──────────────────────
#
# CA5c69c585 / CAdb28a1a6 / CA2b11b233 (12-13 Sep): "i've got a knee thing and
# a shoulder thing ... i also need to know about parking" got "As for parking —"
# and then the model's "sorry to hear you're dealing with both of those".
# SYMPTOM already precedes FAQ_PARKING in the rule order; it lost because
# "knee thing" has no word for pain. The vague class -- a body part with a
# complaint-noun -- is now symptom vocabulary.

SYMPTOM_AND_PARKING = ("uh right so i've got a knee thing and a shoulder thing i'm in "
                       "shoreditch but i'm free tuesdays and thursdays but only after 4 "
                       "i also need to know about parking")


@pytest.mark.parametrize("utterance", [
    SYMPTOM_AND_PARKING,
    "i've got an issue with my hip and wanted to ask about parking",
    "my back's playing up, is it easy to park",
    "something wrong with my knee and i need to know about parking",
])
def test_a_vague_complaint_beats_the_faq_in_the_same_breath(utterance):
    intents = classify_intent(utterance, GREETING)
    assert intents and intents[0] is Intent.SYMPTOM, intents


@pytest.mark.parametrize("utterance", [
    "is there parking near the clinic",
    "where do i park",
    "i'll ring back thing is i want to book",
])
def test_no_body_complaint_means_no_symptom_head(utterance):
    intents = classify_intent(utterance, "Anything else I can help with?")
    assert Intent.SYMPTOM not in intents, intents
