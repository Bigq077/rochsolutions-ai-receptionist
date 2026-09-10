# tests/regression/test_n3_a_repeat_request_is_not_a_fragment.py
"""
N3 - CAb2fc0c23f14a5aff45a062992f02852a, 10 Sep 2026 22:05, northgate, build
dfc8b91454b8. The caller asked Susie to repeat herself and waited 19 seconds.

    22:05:43.5  Susie: "Wednesday 16th September - Number 1, eight in the
                        morning. Number 2, one in the afternoon. Number 3,
                        twenty past four in the afternoon."
    22:05:49.9  caller: "say that again"
                -> [ms_conn] slot fragment ignored -- re-arming
                -> [ms_lost] reason=slot_fragment call_total=1
    22:06:01.5  Susie: "Still with you - which of those would you like?"
                        (the watchdog, covering silence it did not cause, with
                         a prompt that is not a repeat)
    22:06:06.5  caller: "say them again please"     <- FOUR words this time
                -> turn_seq=12 outcome=no_content ttfa_ms=11624
    22:06:08.9  Susie: "Wednesday the 16th - eight in the morning, one in the
                        afternoon, or twenty past four in the afternoon."

**The second ask only got through because he added "please".** It takes the
`len(words) > 3` early return in `_is_short_meaningless_fragment`
(`connection.py:1070`); the first ask was three words, none of them on
`_COMMUNICATIVE_WORDS`, so it was discarded at `connection.py:10060`.

Driven directly against the predicate, EVERY short way of asking for a repeat
was dropped: "say that again", "say them again", "say it again", "repeat
that", "pardon", "sorry", "again".

THIS IS THE FOURTH TIME THIS SITE HAS EATEN A REAL ANSWER, and the third of
this exact shape - a word of meaning missing from the list, the watchdog then
re-asking the wrong question, ~10-19s lost, the caller repeating themselves:

  * T-15  (2026-08-04) "more so afternoons"  -> fixed by adding the
          time-of-day words to this same list. Its comment says it: "a
          hand-maintained vocabulary sitting between the caller and what they
          asked for ... the most likely answer to the question being asked was
          missing from it."
  * B-37  (CA8d90deb2, 3 Aug) "uh go ahead"  -> fixed with a bypass arm.
  * Spec AJ Bug B (Redditch 18:31) "works for me" during TIME_SELECTION.

So the fix follows T-15's precedent rather than inventing a new arm: the words
are added to the list of words of meaning, and the utterance reaches the LLM,
which already answers a repeat request correctly - it did exactly that at
22:06:08.9 once "please" let it through. No new speech path is introduced,
because a new re-ask is a new thing that can say the wrong sentence.

**A repeat-request PHRASE list was considered and rejected.** B-37's own
property test says why: a list that enumerates INTENTS goes stale the moment a
caller phrases it a new way, while a list of noises cannot. Adding words of
meaning to `_COMMUNICATIVE_WORDS` widens what gets HEARD; it never narrows it.
"""
from __future__ import annotations

import inspect

import pytest

from app.media_streams import connection as c
from app.media_streams.connection import _is_short_meaningless_fragment


# ── The exhibit ───────────────────────────────────────────────────────────
def test_the_exhibit_say_that_again_reaches_the_llm():
    """Word for word, 22:05:49.9. This is the whole defect."""
    assert not _is_short_meaningless_fragment("say that again"), (
        "'say that again' is still discarded as a meaningless fragment"
    )


@pytest.mark.parametrize("ask", [
    "say that again",
    "say them again",
    "say it again",
    "repeat that",
    "come again",
    "pardon",
    "sorry",
    "again",
    "tell me more",
    "one more time",
])
def test_every_short_way_of_asking_for_a_repeat_is_heard(ask):
    """The family, not the one phrasing.

    Fixing only the exhibit would leave a caller who says "pardon" in exactly
    the same silence - which is the stale-vocabulary failure this site keeps
    reproducing.
    """
    assert not _is_short_meaningless_fragment(ask), (
        f"{ask!r} is discarded - the caller asks and nothing answers"
    )


def test_the_four_word_ask_that_rescued_the_call_still_works():
    """"say them again please" survived only on word count. Pinned so a later
    narrowing of the list cannot quietly take away the thing that saved him."""
    assert not _is_short_meaningless_fragment("say them again please")


# ── The guard must still guard ────────────────────────────────────────────
@pytest.mark.parametrize("fragment", [
    "with me",
    "suits me",
    "actually",
    "that one",
    "okay then",
])
def test_genuine_fragments_are_still_dropped(fragment):
    """Re-pinned from test_slot_fragment_timeofday, deliberately.

    A guard that never fires is the same as no guard, and the cheap way to make
    this file pass would be to gut the predicate. These five are the reason not
    to.
    """
    assert _is_short_meaningless_fragment(fragment), (
        f"{fragment!r} is no longer re-armed - the fragment guard is now inert"
    )


def test_phone_numbers_are_never_dropped():
    """Hard constraint, re-pinned because this change edits the same predicate.
    A discarded phone number is a booking sent to nobody."""
    assert not _is_short_meaningless_fragment("07502211207")
    assert not _is_short_meaningless_fragment("07502")


def test_negation_still_reaches_the_llm():
    for no in ("no", "not", "none", "never"):
        assert not _is_short_meaningless_fragment(no)


# ── The design property ───────────────────────────────────────────────────
def test_no_pure_filler_token_is_communicative():
    """Load-bearing, and the inverse of B-37's property test.

    `_COMMUNICATIVE_WORDS` enumerates MEANING, so adding to it only ever widens
    what the LLM hears - which is why growing it is safe and why this fix takes
    that route. The way it could go wrong is the opposite one: if a pure noise
    token ("uh", "um") ever landed here, every fragment containing it would
    become communicative and the guard would be inert.
    """
    overlap = c._PURE_FILLER_TOKENS & c._COMMUNICATIVE_WORDS
    assert not overlap, f"filler tokens leaked into the communicative list: {overlap}"


def test_the_drop_still_reads_this_predicate():
    """Fixture drift. If the call site stops consulting the predicate, every
    assertion above is testing a function nothing calls."""
    src = inspect.getsource(c.WebSocketCallHandler._llm_loop)
    assert "_is_short_meaningless_fragment" in src, (
        "the fragment drop no longer reads this predicate - re-aim this file"
    )
