"""The hold head is chosen from what the CALLER asked for, not just the tool.

WHY THIS EXISTS
---------------
`WorkKind` is keyed to five tool names, so the arbiter could only speak when a
tool ran. Measured on the 753-call obs corpus (2026-08-29), that is aimed at the
wrong latency source: `check_availability` has a p50 round-trip of 319ms and a
p90 of 607ms, while turn time-to-first-audio is p50 1,938ms / p90 3,171ms. The
dead air is the model, so EVERY turn has roughly two seconds of it -- a price
question, a cancel request and a symptom included, none of which called a tool
and none of which could get a head.

Each test here pins a defect the corpus actually contains.
"""
from __future__ import annotations

import pytest

from app.hold_speech import (
    INTENT_HEADS,
    Intent,
    classify_intent,
    render_intent_head,
    strip_head_echo,
    subject_for,
)

EM = "—"


# ── 1. A head never names something the caller did not say ──────────────────

@pytest.mark.parametrize(
    "utterance, expected_subject",
    [
        ("would you have next saturday", "Saturday"),
        ("uh what about the week after that actually", "the week after"),
        ("afternoons", "afternoon"),
        ("uh 60-minute session please", "sixty-minute"),
        # Nothing nameable was said, so nothing may be named.
        ("do you have anything free", ""),
        ("as soon as possible", ""),
    ],
)
def test_the_subject_is_only_ever_what_the_caller_said(utterance, expected_subject):
    assert subject_for(utterance) == expected_subject


def test_a_pool_without_a_subject_falls_back_rather_than_inventing_one():
    """A head must never render "Let me see what  looks like".

    The same failure `render_head` already guards for `{practitioner}`: a clinic
    with no named practitioner must not say "Sending that over to  -".
    """
    for intent, pool in INTENT_HEADS.items():
        head = render_intent_head(intent, subject="")
        assert head, f"{intent} rendered nothing with no subject"
        assert "{" not in head and "}" not in head, (
            f"{intent} leaked a placeholder: {head!r}"
        )
        assert "  " not in head, f"{intent} left a hole where the subject was: {head!r}"


# ── 2. Deny-by-default: an unmatched turn is silent ─────────────────────────

@pytest.mark.parametrize("utterance", [
    "",
    "quentin rook",          # a name
    "yeah",                  # a bare answer
    "no",
    "the second one",        # a slot selection
    "07700900123",           # a phone number
])
def test_nothing_matched_means_silence(utterance):
    """Silence is the pre-arbiter behaviour, so a rule that fails to fire is a
    no-op rather than a confident wrong head."""
    assert classify_intent(utterance) == []


# ── 3. A clinical screen silences every head, through either door ───────────

def test_a_screening_question_silences_the_head():
    """The reply to a red-flag question is an ANSWER, and it is the worst moment
    in the call to guess.

    Live shape from the corpus: the caller says "no, nothing like that, it's not
    swollen, I haven't been on any long journeys" -- a DVT screen answer -- and
    an availability matcher without a corroborator read "any" + "long" as a
    diary request.
    """
    reply = "no nothing like that it's not swollen i haven't been on any long journeys"
    screen = "is the calf swollen, warm or red compared with the other one?"
    assert classify_intent(reply, screen) == []


def test_the_session_flag_silences_the_head_even_when_susie_said_nothing_screenish():
    """Both doors are checked because either alone has been wrong.

    Stored call: "just book me in for Tuesday" was followed not by the diary but
    by "do you have any numbness around the saddle area". A head saying "Let me
    see what Tuesday looks like" in front of a cauda equina screen is the
    promised-work defect at its worst, and the TEXT of the caller's turn gives
    no warning at all -- only the session knows a screen is armed.
    """
    assert classify_intent("just book me in for tuesday please") != []
    assert classify_intent(
        "just book me in for tuesday please", screen_pending=True
    ) == []


# ── 4. A confirm question silences the DIARY heads only ─────────────────────

def test_answering_a_confirm_question_is_not_a_diary_request():
    """"Five in the evening" after "did you mean...?" is a selection, so a head
    promising a lookup stands in front of work that is not happening."""
    confirm = "Just to confirm - did you mean Tuesday the 28th of July at five in the evening?"
    assert Intent.TIME_BAND not in classify_intent(
        "they are five in the evening please sounds great", confirm
    )


def test_an_apology_still_fires_while_answering_a_confirm_question():
    """Register heads are not diary claims, so the confirm suppression must not
    reach them -- being misheard during a readback is exactly when an apology is
    owed."""
    confirm = "Shall I go ahead and book that in?"
    assert Intent.REPEAT_ASK in classify_intent("no i said tom green", confirm)


# ── 5. Injury is often described with no word for pain ──────────────────────

@pytest.mark.parametrize("utterance", [
    "hi i've done my ankle went over on sunday playing football",
    "my shoulder's been really stiff and sore lately",
    "i twisted my knee at football",
])
def test_an_injury_described_without_the_word_pain_still_earns_sympathy(utterance):
    """The screening triggers learned this the hard way: "my ankle ... I twisted
    it" armed no screen, because the matcher wanted a word for pain. Adding more
    synonyms is the trap; the shape of the matcher is the bug."""
    assert Intent.SYMPTOM in classify_intent(utterance)


def test_sympathy_beats_a_day_named_in_passing():
    """"went over on sunday" names the day the injury happened, not a booking
    preference. Reading it as one produced "Let me see what Sunday looks like -
    I'm sorry to hear that", which gets the order of concern exactly backwards.
    """
    hits = classify_intent("hi i've done my ankle went over on sunday playing football")
    assert hits[0] is Intent.SYMPTOM, hits


# ── 6. The model's duplicate opener is removed ──────────────────────────────

@pytest.mark.parametrize(
    "head, payload, must_start_with",
    [
        (f"Sorry to hear that {EM}",
         "I'm sorry to hear that - tight hamstrings from running can really nag.",
         "tight hamstrings"),
        (f"Sorry about that {EM}",
         "Apologies for that - so that's Tom Green, Wednesday the 30th of July.",
         "so that's Tom Green"),
        (f"Let's get that moved {EM}",
         "Of course - which day were you thinking?",
         "which day"),
        (f"Let me see what Tuesday looks like {EM}",
         "Just a moment while I check what's available. The available slots are...",
         "The available slots"),
    ],
)
def test_the_model_does_not_say_the_head_again(head, payload, must_start_with):
    assert strip_head_echo(payload, head).startswith(must_start_with)


def test_a_reply_that_is_nothing_but_an_opener_survives():
    """Stripping it would leave the turn with no audio at all -- a dead-end
    head, which is the defect this whole change exists to remove. Saying the
    phrase twice is a much smaller fault than saying nothing."""
    assert strip_head_echo("Of course.", f"Not a problem {EM}") == "Of course."


def test_an_answer_is_never_mistaken_for_an_acknowledgement():
    """The allow-list replaced a shape-based version ("a short clause with no
    digits") that ate "I've got you on oh three three" -- half a phone number,
    spelled out, so it carried no digits to see."""
    payload = "I've got you on oh three three, six one seven, seven six, nine eight six seven."
    assert strip_head_echo(payload, f"No problem at all {EM}") == payload


# ── 7. The FAQ heads promise no lookup ──────────────────────────────────────

def test_a_price_question_gets_a_topic_head_not_a_diary_one():
    """34.6% of the 601 stored hold phrases were followed by a question rather
    than data -- the phrase promised a lookup that never happened. An FAQ turn
    is the largest single group of those."""
    hits = classify_intent("hi i was wondering how much is an appointment")
    assert Intent.FAQ_PRICE in hits
    head = render_intent_head(Intent.FAQ_PRICE)
    assert head == f"On price {EM}"


def test_availability_language_is_not_a_treatments_question():
    """A loose "do you..." trigger swallowed every "do you have anything on
    Friday" into FAQ_TREATS, which would answer a diary request with a service
    list."""
    hits = classify_intent("do you have anything on wednesday")
    assert Intent.FAQ_TREATS not in hits
    assert Intent.NAMED_DAY in hits


def test_slots_being_open_is_not_an_opening_hours_question():
    """A bare \\bopen\\b matched "any other slots open that Wednesday"."""
    hits = classify_intent("do you have any other slots open that wednesday")
    assert Intent.FAQ_HOURS not in hits


# ── 8. The subject survives rotation ────────────────────────────────────────

@pytest.mark.parametrize("index", [0, 1, 2, 3])
def test_the_callers_day_is_not_dropped_on_a_later_head(index):
    """The subject-free member of a pool is a FALLBACK, not a rotation partner.

    NAMED_DAY is ["Let me see what {subject} looks like -", "Let me see -"], and
    `index` is the number of heads already spoken this call. Rotating across the
    whole pool meant the SECOND head of a call answered "would you have
    Saturday?" with a bare "Let me see -" -- throwing away the one word that
    makes it situational. Naming what the caller asked for is the entire point.
    """
    head = render_intent_head(Intent.NAMED_DAY, subject="Saturday", index=index)
    assert "Saturday" in head, (
        f"the caller's day was dropped on head #{index}: {head!r}"
    )


def test_rotation_still_varies_where_a_pool_has_real_variants():
    """The fix must not freeze pools that genuinely have several phrasings."""
    from app.hold_speech import HEADS, WorkKind, render_head

    pool = HEADS[WorkKind.DIARY_READ]
    assert len(pool) > 1
    rendered = {render_head(WorkKind.DIARY_READ, index=i) for i in range(len(pool))}
    assert len(rendered) == len(pool), "work heads stopped rotating"


def test_a_pool_with_no_subject_free_member_still_never_leaks_a_placeholder():
    """The guard stays even though the new selection makes it near-unreachable:
    "Let me see what  looks like" is the failure it exists to prevent."""
    for intent in INTENT_HEADS:
        head = render_intent_head(intent, subject="", index=3)
        assert "{" not in head, f"{intent} leaked a placeholder: {head!r}"
