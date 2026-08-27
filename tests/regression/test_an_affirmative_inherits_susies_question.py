"""
Regression: "yeah go for it" to Susie's own offer of another day did nothing.

Finding 2, CA890b511e1bbcda1a9c1cecf7a95f8207 (27 Aug 2026, theorem_v3):

    08:42:53  Susie:  "I don't have any further times on that day — would you
                       like me to look at a different day?"
    08:43:02  caller: "yeah go for it"
    08:43:15  model calls check_availability
                        date_hint="afternoons week of 31 August 2026"
              -> BLOCKED: slots already retrieved (last_offered_slots present)
    08:43:16  she re-reads the SAME two Fridays

The model had it right. The GUARD overrode it, because its escape,
`_caller_requests_different_day`, requires the CALLER to name a day or use a
change phrase — and here the day was named by SUSIE. A predicate that reads
only the caller's words cannot see it. Same shape as the Gate-5g name deadlock,
where the only source of the first name was Susie's own speech.

That predicate's own docstring records which direction hurts: "False negative —
silent here when the caller DID want another day. That is CAb81fe651:
Wednesday asked four times, Tuesday served every time, hung up unbooked." This
is that failure, reached through a door the predicate cannot see.

THE RULE: an affirmative inherits the question it answers.

Matched against the sentence THIS CODEBASE generates, taken from the function
that generates it (`format_next_batch_speech([], False)`), so a reword cannot
strand a stale literal. That is what keeps it clear of the standing ban on
matching one literal of MODEL speech: this text is ours, deterministic, and has
exactly one producer.

One-turn scoped for free — it reads the IMMEDIATELY PRECEDING assistant turn
from conversation_history, so it cannot fire a turn later on stale state. That
matters: a wrongly-True answer stands the dedup guard down, and letting an
acceptance through to a fresh lookup is CAce1457d1, where the caller had to
accept twice.
"""
from __future__ import annotations

import ast
import inspect

from app.media_streams.llm_stream import (
    _answering_susies_different_day_offer,
    _caller_requests_different_day,
    _is_short_affirmative,
)
from app.tools.slot_followup import format_next_batch_speech

# Not a copy of the sentence — the sentence itself, from its one producer.
HER_OFFER = format_next_batch_speech([], False)
A_SLOT_OFFER = (
    "Friday 28th August — Number 1, midday. Number 2, two in the afternoon. "
    "Any of those work?"
)


def _state(bot_said: str, caller_said: str):
    """(messages, session) as the guard sees them mid-turn."""
    return (
        [{"role": "user", "content": caller_said}],
        {"conversation_history": [{"role": "assistant", "content": bot_said}]},
    )


# ---------------------------------------------------------------------------
# The live defect
# ---------------------------------------------------------------------------
def test_the_live_defect_yeah_go_for_it_requests_a_different_day():
    messages, session = _state(HER_OFFER, "yeah go for it")
    assert _caller_requests_different_day(messages, session) is True


def test_the_ways_a_caller_says_yes():
    """A SHAPE, not a phrase table. The alternative is the treadmill on
    _PHONE_CONFIRM_AFFIRMATIVE_PHRASES — patched four times, once per literal a
    single live call happened to use."""
    for reply in ("yes", "yeah", "yes please", "go on then", "ok", "okay",
                  "sure", "aye", "please do", "yeah go for it", "alright",
                  "yeah that'd be great"):
        messages, session = _state(HER_OFFER, reply)
        assert _caller_requests_different_day(messages, session) is True, reply


# ---------------------------------------------------------------------------
# It must not fire on a NO, or on anything but that question
# ---------------------------------------------------------------------------
def test_declining_is_not_a_request():
    for reply in ("no thanks", "no", "nah", "nah leave it", "not really",
                  "no don't worry"):
        messages, session = _state(HER_OFFER, reply)
        assert _answering_susies_different_day_offer(messages, session) is False, reply


def test_negation_is_matched_on_whole_words():
    """The screening negators were substring-matched once and "know" contained
    "no". "I don't know" must not read as an affirmative here either."""
    assert _is_short_affirmative("i don't know") is False
    # ...and a word merely CONTAINING a yes-word is not a yes.
    assert _is_short_affirmative("goodness") is False


def test_an_acceptance_of_a_slot_is_not_a_different_day_request():
    """CAce1457d1: letting "that works for me" reach a real lookup made the
    caller accept twice. The preceding question was a SLOT offer, not the
    different-day offer, so this must stay False."""
    messages, session = _state(A_SLOT_OFFER, "yeah that works for me")
    assert _answering_susies_different_day_offer(messages, session) is False


def test_an_affirmative_to_some_other_question_does_nothing():
    messages, session = _state("Does that work for you?", "yeah")
    assert _answering_susies_different_day_offer(messages, session) is False


def test_a_long_reply_is_not_a_bare_affirmative():
    """Capped at six words so an incidental "right" deep in a sentence cannot
    stand the guard down."""
    assert _is_short_affirmative(
        "right so what I actually wanted was something in the evening instead"
    ) is False


def test_it_is_scoped_to_the_immediately_preceding_turn():
    """Stale state is the danger: a wrongly-True answer stands the dedup guard
    down. Reading the last assistant turn makes staleness impossible."""
    messages, session = _state(HER_OFFER, "yeah")
    # She has since said something else — the offer is no longer on the table.
    session["conversation_history"].append(
        {"role": "assistant", "content": "Could I take your first name?"}
    )
    assert _answering_susies_different_day_offer(messages, session) is False


# ---------------------------------------------------------------------------
# Fails closed
# ---------------------------------------------------------------------------
def test_no_session_means_no_suppression_change():
    """`session` defaults to None at call sites that were never threaded; the
    predicate's contract is that this reproduces the old behaviour exactly."""
    messages = [{"role": "user", "content": "yeah go for it"}]
    assert _answering_susies_different_day_offer(messages, None) is False
    assert _caller_requests_different_day(messages, None) is False


def test_it_never_raises_on_junk():
    for m, s in ((None, None), ([], {}), ("nonsense", {"conversation_history": 7}),
                 ([{"role": "user"}], {"conversation_history": [None]})):
        assert _answering_susies_different_day_offer(m, s) is False


# ---------------------------------------------------------------------------
# Structural — the sentence must never be duplicated as a literal
# ---------------------------------------------------------------------------
def test_the_offer_is_taken_from_its_producer_not_copied():
    """If the wording is ever changed, a copied literal here would go stale
    silently and the caller would be back to being ignored."""
    import app.media_streams.llm_stream as ls

    fn = ls._answering_susies_different_day_offer
    # The prose quotes the sentence — that is what it is explaining — so parse
    # the function and judge only its CODE. (String-slicing the docstring out
    # would silently do nothing: the file is CRLF, __doc__ is normalised to LF.)
    body = ast.parse(inspect.getsource(fn)).body[0].body
    if isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
        body = body[1:]
    code = " ".join(ast.unparse(node) for node in body)
    assert "format_next_batch_speech" in code
    assert "further times on that day" not in code, (
        "the sentence is copied as a literal — derive it from its producer"
    )
