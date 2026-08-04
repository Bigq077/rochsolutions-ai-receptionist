"""
The answer to "mornings or afternoons?" must not be discarded (T-15).

Observed live, call 7 of the Theorem acceptance sweep, 2026-08-04 21:54:47.

    Susie:  "Do you prefer mornings or afternoons?"
    Caller: "more so afternoons"
    log:    [ms_conn] slot fragment ignored — re-arming: 'more so afternoons'

The answer was dropped. Ten seconds later the watchdog re-asked "Still with
you — which of those would you like?", pointing at a set of days the caller had
already rejected. They had to repeat the whole request.

`_is_short_meaningless_fragment` treats any utterance of ≤3 words with no word
from `_COMMUNICATIVE_WORDS` as safe to discard. "afternoons" was not on that
list — so the single most likely answer to the question Susie had just asked
was, by construction, meaningless to her.

The comment on `_PURE_FILLER_TOKENS` in the same module names this exact
failure mode: "a hand-maintained vocabulary sitting between the caller and what
they asked for", listing B-25, the step-8 reword, the timing singles and B-36
cause 1 as prior instances. T-15 is the next one.

These tests pin both directions. Widening the allowlist only ever routes MORE
utterances to the LLM, which is the safe direction — dropping is the
destructive act — but a fragment guard that never fires is also useless, so the
true fragments are pinned too.
"""

import pytest

from app.media_streams.connection import _is_short_meaningless_fragment


# Exactly as STT delivered it on the live call.
LIVE_CALL_7 = "more so afternoons"


def test_the_live_regression():
    assert not _is_short_meaningless_fragment(LIVE_CALL_7), (
        "the call-7 time-of-day answer is being discarded again"
    )


@pytest.mark.parametrize("answer", [
    "afternoons",
    "afternoon",
    "mornings",
    "morning",
    "more so afternoons",
    "evening",
    "evenings",
    "midday",
    "lunchtime",
    "early",
    "late",
    "earlier",
    "later",
    "weekend",
    "weekends",
    "weekday",
    "weekdays",
])
def test_time_of_day_answers_reach_the_llm(answer):
    """Every one of these is a complete, unambiguous answer to a question Susie
    routinely asks. None may be discarded as noise."""
    assert not _is_short_meaningless_fragment(answer), (
        f"{answer!r} discarded — it answers 'mornings or afternoons?'"
    )


@pytest.mark.parametrize("fragment", [
    "with me",
    "suits me",
    "actually",
    "that one",
    "okay then",
])
def test_genuine_fragments_are_still_dropped(fragment):
    """The guard must keep working. These carry no answer and re-arming on them
    is correct — a guard that never fires is the same as no guard."""
    assert _is_short_meaningless_fragment(fragment), (
        f"{fragment!r} is no longer re-armed — the fragment guard is now inert"
    )


def test_phone_numbers_are_never_dropped():
    """Pre-existing hard constraint, re-pinned because this change edits the
    same predicate. A discarded phone number is a booking sent to nobody."""
    assert not _is_short_meaningless_fragment("07502211207")
    assert not _is_short_meaningless_fragment("07502")


def test_negation_still_reaches_the_llm():
    """The other hard constraint on this predicate: a caller saying no must
    always be heard."""
    for no in ("no", "not", "none", "never"):
        assert not _is_short_meaningless_fragment(no)
