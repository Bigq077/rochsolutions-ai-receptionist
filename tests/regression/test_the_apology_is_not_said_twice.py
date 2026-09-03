"""Susie apologised, then apologised again 2 seconds later.

Two of the three symptom calls on 2026-09-03, demo line:

    01:56:05  head:  'Sorry to hear that —'
    01:56:08  model: "I'm sorry to hear that — shoulder pain that's really…"

    02:07:04  head:  'Sorry to hear that —'
    02:07:06  model: "I'm sorry to hear that — a sore ankle can really…"

`join_after_head` has stripped a duplicate opener since the 95 stored
duplicates it was built from, but every pattern in `_INTERIM_DUPE_RE` is a
LOOKUP phrase -- "Let me see", "Let me check", "Right with you". The symptom
head is a different family and was never in the list.

THE SAFETY IS THE HEAD, NOT THE PHRASE. Stripping an apology wherever one
appeared would delete the FIRST apology on a turn whose head was a lookup
phrase: a caller saying what is wrong with them would hear "Let me see what
Monday looks like — a sore ankle can really get in the way", with the sympathy
silently removed. So the strip only fires when the head Susie already spoke was
itself an apology. That case is pinned below and is the one to keep if any of
these tests ever have to be relaxed.

AND NOT EVERY "sorry" IS SYMPATHY. "Sorry about that, could you say that
again?" and "I'm sorry, I didn't catch that" apologise for OUR failure. They
are a different speech act, the caller is owed them, and removing them would
leave the bare request. Hence two branches: "sorry TO HEAR" may end on any
punctuation, a bare "sorry" only counts when it ends on a dash or an ellipsis.
"""
from __future__ import annotations

import pytest

from app.media_streams.llm_stream import join_after_head

APOLOGY_HEAD = "Sorry to hear that —"
LOOKUP_HEAD = "Let me see what Monday looks like —"


@pytest.mark.parametrize("reply,expected_start", [
    # The two live shapes.
    ("I'm sorry to hear that — shoulder pain that's really limiting is worth a look.",
     "shoulder pain"),
    ("I'm sorry to hear that. A sore ankle can really get in the way.",
     "a sore ankle"),
    # Other shapes of the same duplicate.
    ("Sorry to hear that, a sore ankle is worth a look.", "a sore ankle"),
    ("I am so sorry to hear that — that sounds painful.", "that sounds painful"),
    ("Sorry — that sounds painful.", "that sounds painful"),
])
def test_a_second_apology_is_removed_when_the_head_already_apologised(
    reply, expected_start
):
    got = join_after_head(reply, APOLOGY_HEAD)
    assert got.lower().startswith(expected_start.lower()), (
        f"{reply!r} -> {got!r}; the caller heard {APOLOGY_HEAD!r} two seconds "
        f"earlier and this repeats it"
    )
    assert "sorry" not in got.lower().split(".")[0], (
        f"the duplicate apology survived: {got!r}"
    )


def test_the_first_apology_survives_a_lookup_head():
    """THE case that makes the strip safe. Deleting sympathy because a lookup
    phrase happened to play would be a worse defect than the one being fixed."""
    reply = "I'm sorry to hear that — a sore ankle can really get in the way."
    got = join_after_head(reply, LOOKUP_HEAD)
    assert "sorry to hear" in got.lower(), (
        f"the caller's FIRST apology was removed after a lookup head: {got!r}"
    )


@pytest.mark.parametrize("reply", [
    "Sorry about that, could you say it again?",
    "I'm sorry, I didn't catch that.",
    "Sorry, could you repeat that?",
])
def test_an_apology_for_our_own_failure_is_never_removed(reply):
    """A different speech act. The caller is owed it, and removing it leaves a
    bare request that reads as brusque."""
    got = join_after_head(reply, APOLOGY_HEAD)
    assert "sorry" in got.lower(), (
        f"{reply!r} -> {got!r}; this apologises for OUR mishearing, not for "
        f"their injury, and the head did not already say it"
    )


def test_a_reply_that_does_not_apologise_is_untouched_but_for_the_normal_join():
    """The third call of the night: the model paraphrased instead of repeating,
    and there is nothing to strip. Only the ordinary decapitalisation applies,
    because the head ends on a dash."""
    reply = "A shoulder that's really painful — that kind of pain can make things hard."
    got = join_after_head(reply, APOLOGY_HEAD)
    assert got.lower().startswith("a shoulder that's really painful")
    assert "that kind of pain can make things hard" in got


def test_a_reply_that_is_nothing_but_the_apology_keeps_the_old_fail_safe():
    """Falls through to the `if not body` branch, which already owns this
    decision: the chunk is returned unchanged unless the caller opted into
    suppression, because saying it twice beats saying nothing."""
    assert join_after_head("I'm sorry to hear that.", APOLOGY_HEAD)
    assert join_after_head(
        "I'm sorry to hear that.", APOLOGY_HEAD, suppress_pure_duplicate=True
    ) == ""


def test_no_head_means_no_change():
    reply = "I'm sorry to hear that — a sore ankle can really get in the way."
    assert join_after_head(reply, "") == reply
