# tests/regression/test_single_word_dispatch_default.py
"""
CODE SPEC G inversion (2026-07-24) — single words dispatch by default.

Background
----------
`connection.py` filters every single-word FINAL transcript before it reaches the
LLM.  Four *mechanical* noise conditions run first (≤3 chars, no vowels, all
vowels, exact noise-list match) plus a rapid-continuation MERGE.  Those are
sound and are unchanged by this fix.

The defect is what happens to a single word that SURVIVES all of them.  CODE
SPEC G (`connection.py` ~6376) then re-armed the silence timer unless the word
appeared in the hand-maintained `_SCHEDULING_SINGLES` allowlist:

    if (_is_single_word
            and len(_stripped.split()) == 1
            and _stripped not in _SCHEDULING_SINGLES):
        ...
        else:
            logger.info("[ms_stt] non-scheduling single word %r — "
                        "silence timer re-armed", _stripped)

That is deny-by-default governed by a ~35-word list.  It can only ever be as
complete as the last incident, and every gap is a silently discarded caller
answer that presents to the operator as caller silence, not as an error.

Two confirmed live losses from exactly this shape:
  * Vital Edge, 2026-07-24 — caller answered the timing question with "today".
    Not in the list → dropped → caller hung up → `outcome=abandoned`, no
    booking.  Patched by adding words (see
    test_scheduling_singles_relative_day.py).
  * The same class recurred often enough during the 2026-07-24 sign-off sweep
    to be reported as "sometimes it doesn't pick up short sentences".

Fix
---
Invert the default.  A single word that survives the mechanical noise
conditions is dispatched to the LLM, which already holds the conversation
context needed to judge relevance ("today" answers "when would you like to come
in?"; the allowlist never had that context and never could).

`_SCHEDULING_SINGLES` is deliberately left in the module by this commit — it
becomes non-load-bearing rather than deleted, so the change reverts cleanly.
Its own test file keeps passing and is simply no longer the thing standing
between a caller's answer and the LLM.

Cost note: this routes strictly MORE traffic to the LLM.  The new traffic is
bounded to real words, containing a vowel, over three characters, not on the
noise list — i.e. plausible caller speech that should always have dispatched.
A wasted LLM turn costs ~2 s; a dropped answer costs the booking.

Contract asserted here
----------------------
`should_dispatch_single_word(word)` is the extracted SPEC G predicate.  The
extraction is the seam that makes this behaviour testable at all — the logic
previously lived inline inside `handle_transcript`, which no test can reach.
"""

import pytest

from app.media_streams.connection import (
    _SCHEDULING_SINGLES,
    should_dispatch_single_word,
)


# ---------------------------------------------------------------------------
# The regression: real words that are NOT on the allowlist and were dropped.
# None of these is reachable by extending the list -- that is the point.
# ---------------------------------------------------------------------------
UNLISTED_REAL_ANSWERS = [
    "lunchtime",   # answers "what time suits?"
    "midday",
    "early",
    "late",
    "urgent",      # answers "how soon do you need to be seen?"
    "physio",      # answers "what were you after?"
    "massage",
    "acupuncture",
    "assessment",
    "bolton",      # answers a location question
    "marcus",      # answers "who would you like to see?"
    "wednesday",   # on the list, but see test below -- guards the whole class
]


# Mechanical noise. These are caught by Conditions 1/2a/2b/3 BEFORE SPEC G and
# must keep being dropped -- the inversion must not widen the noise gate.
MECHANICAL_NOISE = [
    "ng",    # Condition 2a -- no vowels
    "rch",   # Condition 2a
    "sht",   # Condition 2a
    "oo",    # Condition 2b -- all vowels
    "ah",    # Condition 2b
    "ee",    # Condition 2b
    "um",    # Condition 1 -- <=3 chars
    "the",   # Condition 1
]


def test_unlisted_real_words_dispatch():
    """The regression itself: a real single-word answer reaches the LLM.

    Every word here is absent from _SCHEDULING_SINGLES. Before the inversion
    each one hit the drop path and re-armed the silence timer, stranding the
    caller in the recovery ladder with no error logged anywhere.
    """
    dropped = [w for w in UNLISTED_REAL_ANSWERS if not should_dispatch_single_word(w)]
    assert not dropped, (
        f"single-word answers still dropped: {dropped} — each is a plausible "
        "reply to a question Susie actually asks, and a drop here presents as "
        "caller silence, not as an error."
    )


def test_dispatch_does_not_consult_the_allowlist():
    """Membership in _SCHEDULING_SINGLES must no longer decide the outcome.

    This is the structural assertion. If someone re-introduces an allowlist
    check, a word on the list and a comparable word off it will diverge and
    this fails — which is the failure mode we are trying to make impossible.
    """
    on_list = "tomorrow"
    off_list = "lunchtime"
    assert on_list in _SCHEDULING_SINGLES
    assert off_list not in _SCHEDULING_SINGLES
    assert should_dispatch_single_word(on_list) == should_dispatch_single_word(off_list), (
        "allowlist membership still changes dispatch — the deny-by-default "
        "behaviour has been reintroduced."
    )


@pytest.mark.parametrize("word", MECHANICAL_NOISE)
def test_mechanical_noise_still_dropped(word):
    """The inversion must not widen the noise gate.

    Conditions 1/2a/2b/3 are the legitimate part of this filter: they key on
    the shape of the token, not on a vocabulary, so they cannot fail one word
    at a time the way the allowlist did.
    """
    assert not should_dispatch_single_word(word), (
        f"{word!r} is mechanical STT noise and must still be suppressed"
    )


def test_previously_fixed_words_stay_fixed():
    """The words patched into the allowlist after live losses must still work.

    They now pass via the inverted default rather than via list membership, so
    this guards against the fix silently regressing them on the way through.
    """
    for word in ("today", "tomorrow", "asap", "saturday", "flexible"):
        assert should_dispatch_single_word(word), (
            f"{word!r} regressed — this word cost a real booking once already"
        )
