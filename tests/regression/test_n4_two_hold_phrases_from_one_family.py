# tests/regression/test_n4_two_hold_phrases_from_one_family.py
"""
N4 - Susie apologised for the same wait twice, in two wordings.

Theorem, 9 Sep 2026 10:03, `CAf9e32e638f07efa25f06c01103bab3ac`, build
`f97932045fe7`:

    caller : "um to book an appointment mate"
    Susie  : "Sorry, still with you —"
    Susie  : "Still with you —"
    Susie  : "Is this for our Awlstuh or Redditch clinic?"

The caller hung up. The judge tagged it `dead_end` and quoted both phrases.

THE TIMING WAS RIGHT. Turn 1 stalled for 12.7s (`llm_ttft_ms=12748`), the first
phrase landed at 3.3s and the re-arm at ~10s, which is exactly what B-19 built
the second filler for. What was wrong is the WORDING: `WorkKind.UNKNOWN_SLOW`
holds

    ["Sorry, still with you —", "Still with you —"]

two members that differ only by a leading "Sorry, ". `_second_filler_text`
rotates by `len(used_fillers)`, so a second stall in one turn is GUARANTEED to
produce the other one.

Its rule 3 already forbids this in words - *"Never a verbatim repeat. Hearing
the identical phrase twice reads as a stuck line rather than a hold."* - but the
implementation was `text == first_text`, and the two wordings are not
byte-equal. Same shape as the head-echo defect (`66b8c209` -> `ff0fa987`, where
equality could not see a chunk that continued) and as D5 (`9cff40aa`, where the
rotation handed back an opener the model had just used). Third instance: an
equality test standing in for "is this the same thing again?".

Measured over the corpus on 2026-09-09: **21 calls** carry two wait
acknowledgements back to back, on `jv_v1`'s patient line as well as Theorem, and
every one of them is this pair.

WHAT THIS DOES AND DOES NOT FIX. Refusing leaves the caller in silence for the
rest of the stall, and the docstring's own escape hatch says that is the better
fault - one apology and quiet reads better than two apologies, which is the
"stuck line" rule 3 exists to prevent. It does NOT shorten the 12.7s stall, and
it does not give the re-arm something else to say: `UNKNOWN_SLOW` is
deliberately contentless and both its members are the same sentence. A third,
structurally different wording would let the second phrase speak again, and
that is caller-facing copy - an owner decision, not one to make here.
"""
from __future__ import annotations

import pytest

from app.hold_speech import HEADS, INTENT_HEADS, WorkKind, head_families
from app.media_streams.llm_stream import _second_filler_text


UNKNOWN_SLOW_POOL = HEADS[WorkKind.UNKNOWN_SLOW]


def _session():
    return {"_ack_filler_active": True, "used_fillers": ["x"]}


# ── the pair that started this ──────────────────────────────────────────────

def test_the_pool_members_really_are_near_duplicates():
    """If this pool ever gains a genuinely different wording, the refusal below
    stops costing us anything - which is the point of pinning it."""
    assert len(UNKNOWN_SLOW_POOL) >= 2
    a, b = UNKNOWN_SLOW_POOL[0].lower(), UNKNOWN_SLOW_POOL[1].lower()
    assert a.endswith(b.rstrip(" —-")) or b in a, (a, b)


def test_a_second_wait_acknowledgement_is_refused():
    for first, second in ((UNKNOWN_SLOW_POOL[0], UNKNOWN_SLOW_POOL[1]),
                          (UNKNOWN_SLOW_POOL[1], UNKNOWN_SLOW_POOL[0])):
        got = _second_filler_text(_session(), first, False, candidate=second)
        assert got is None, (first, second, got)


def test_a_verbatim_repeat_is_still_refused():
    """Rule 3's original case must not regress."""
    first = UNKNOWN_SLOW_POOL[0]
    assert _second_filler_text(_session(), first, False, candidate=first) is None


# ── and a genuinely different phrase still speaks ───────────────────────────

def test_a_different_family_still_speaks():
    """The re-arm exists because 12 seconds of silence is worse than a hold
    phrase. Only a SECOND phrase of the same kind is refused."""
    first = UNKNOWN_SLOW_POOL[0]
    other = HEADS[WorkKind.PATIENT_LOOKUP][0]
    assert _second_filler_text(_session(), first, False, candidate=other) == other


def test_the_existing_refusals_are_untouched():
    s = _session()
    first = HEADS[WorkKind.PATIENT_LOOKUP][0]
    other = HEADS[WorkKind.DIARY_READ][0]
    # the LLM answered during the wait
    assert _second_filler_text(s, first, True, candidate=other) is None
    # a tool filler took over
    assert _second_filler_text({"_ack_filler_active": False}, first, False,
                               candidate=other) is None
    # two write-acks in a row
    w1, w2 = HEADS[WorkKind.WRITE_BOOK][0], HEADS[WorkKind.WRITE_BOOK][-1]
    assert _second_filler_text(s, w1, False, candidate=w2) is None


# ── the family lookup itself ────────────────────────────────────────────────

def test_head_families_reads_the_pools_rather_than_a_copy():
    """Built from HEADS/INTENT_HEADS so it cannot drift the way a
    hand-maintained list would. A wording may belong to SEVERAL pools -
    "Let me see -" is the subject-free fallback of five - so membership is
    asserted rather than equality."""
    for kind, pool in HEADS.items():
        for head in pool:
            if "{" in head:
                continue          # see the placeholder test below
            assert kind.value in head_families(head), (kind, head)
    for intent, pool in INTENT_HEADS.items():
        for head in pool:
            if "{" in head:
                continue          # needs a subject rendered in
            assert intent.value in head_families(head), (intent, head)


def test_head_families_is_none_for_ordinary_speech():
    for text in ("", "  ", "So that's Thursday at four.", "Alcester."):
        assert head_families(text) == frozenset(), text


def test_head_families_never_raises():
    for bad in (None, "", 7, object()):
        head_families(bad)


def test_placeholder_heads_are_a_known_and_deliberate_gap():
    """A head carrying {practitioner} or {subject} is not matched.

    Rendering it needs the session value, and the live string would be
    "Sending that over to Mark —" rather than the template. It is left out
    rather than half-handled: rule 4 (`is_write_filler`) already covers the
    write-ack family, which is what these mostly are, and a filler must not
    grow a second, weaker copy of `_HEAD_RE`'s job. If the double-phrase
    defect ever appears on a placeholder pool, THAT is the fix to reach for.
    """
    for pool in HEADS.values():
        for head in pool:
            if "{" in head:
                assert head_families(head) == frozenset(), head
