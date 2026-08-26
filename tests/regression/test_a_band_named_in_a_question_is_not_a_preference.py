"""
Regression: a time band mentioned inside a QUESTION was rendered to the model
as a preference the caller had "stated explicitly".

B-91, second half. `CA70cc833f` (26 Aug 2026, theorem_v3, build `1a711a54`).

    18:28:53  time_of_day_preference captured: mornings (from utterance 'um
              actually on that wednesday do you have any other slots than the
              10 in the morning is that all you have that day')

From that moment every prompt build carried:

    TIME OF DAY PREFERENCE CONFIRMED (caller stated this explicitly — do NOT
    ask again): mornings

They had asked whether the morning slot was all there was. The latch is never
cleared within a call, so a later lookup could be band-filtered and the
remainder reported as the whole day — the B-90 mechanism, second door.

WHY IT SURVIVED: the sibling soft-context extractor ~1200 lines below has
gated on _transcript_is_question since 6e6d7aa, and its own sweep comment
records this shape — "what if I rearrange the morning of" -> mornings (T-11,
4 Aug). One block got the gate; the other did not. On this call they ran on
the same utterance in the same turn and disagreed:

    18:28:53  time_of_day_preference captured: mornings          (no gate)
    18:28:57  soft-context extraction skipped — caller asked a question

TIERS, NOT A VETO. The first cut of this fix refused ALL capture on any
question. Measured over eighteen natural phrasings, that suppressed nine —
every interrogative form, including "do you have anything in the afternoon",
which is a preference by any reading. It also silently holstered the hold clip,
because `expect_slot_presentation` was the one reader of the hard latch with no
soft fallback (Job 3c.4 / CAce1457d1).

The prompt builders already render two different sentences from two different
fields, and only one claims the caller said it. A question now earns the SOFT
tier: the model still sees the preference and still steers on it, and the four
location-intercept routers and the hold clip all still fire, because they read
soft_context first. What disappears is only the false claim of explicitness.
"""
from __future__ import annotations

import inspect

import pytest

from app.media_streams.connection import (
    _extract_time_preference,
    _time_preference_tier,
)


LIVE_UTTERANCE = (
    "um actually on that wednesday do you have any other slots than the "
    "10 in the morning is that all you have that day"
)

# From the 2026-08-04 acceptance sweep, quoted in the soft-context block.
SWEEP_T11 = "what if I rearrange the morning of"


# ---------------------------------------------------------------------------
# The defect
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("utterance", [LIVE_UTTERANCE, SWEEP_T11])
def test_a_question_never_earns_the_explicit_claim(utterance):
    """Neither caller stated a preference, so neither may produce the
    "caller stated this explicitly — do NOT ask again" sentence."""
    assert _time_preference_tier(utterance, is_slot_pick=False) != "hard"


def test_a_pick_from_the_offer_earns_nothing_at_all():
    """B-90/B-91: choosing option 1 is not a statement about mornings, and
    that outranks the question test — a pick is not a question either."""
    assert _time_preference_tier(
        "ten in the morning", is_slot_pick=True
    ) == "none"


# ---------------------------------------------------------------------------
# The cost the first cut of this fix would have paid, and no longer does
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "utterance",
    [
        "have you got anything in the mornings",
        "do you have anything in the afternoon",
        "is there anything in the morning",
        "are there any morning slots",
        "can i have a morning appointment",
        "what have you got in the mornings",
        "do you do evenings",
    ],
)
def test_an_interrogative_preference_still_reaches_the_model(utterance):
    """These ARE preferences. A veto-shaped gate threw all seven away; the
    soft tier keeps every one of them visible."""
    assert _extract_time_preference(utterance), "test case extracts nothing"
    assert _time_preference_tier(utterance, is_slot_pick=False) == "soft"


@pytest.mark.parametrize(
    "utterance",
    [
        "mornings please",
        "afternoons",
        "morning would be better",
        "i can only do mornings",
        "anything in the morning",
        "i work afternoons so mornings please",
    ],
)
def test_a_stated_preference_still_earns_the_hard_latch(utterance):
    """The gate must not swallow the answers the capture block exists for."""
    assert _time_preference_tier(utterance, is_slot_pick=False) == "hard"


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------
def test_the_capture_site_asks_for_a_tier():
    """Anchored on the log call, not the bare phrase — this file's docstring
    quotes that phrase, and a source search would find the comment first."""
    from app.media_streams import connection as c

    src = inspect.getsource(c)
    i = src.index('"[ms_conn v3] time_of_day_preference captured: %s"')
    window = src[i - 8000:i]
    assert "_time_preference_tier" in window, (
        "the capture writes a preference without asking what authority the "
        "utterance earned — this is the block that latched 'mornings' off a "
        "question on CA70cc833f"
    )
    assert '_tod_tier == "hard"' in window, (
        "the hard latch must be conditional on the tier; writing it "
        "unconditionally is the defect"
    )


def test_both_extractors_still_share_the_question_test():
    """The two blocks disagreed on the same utterance in the same turn. They
    must not be allowed to drift apart again."""
    from app.media_streams import connection as c

    src = inspect.getsource(c)
    assert src.count("_transcript_is_question(") >= 3, (
        "expected the definition plus BOTH extractor paths; one has lost it"
    )


def test_the_hold_clip_accepts_the_soft_tier():
    """`expect_slot_presentation` was the ONLY reader of the hard latch with
    no soft fallback. Without this the soft tier means ~3s of silence before
    the slot readout — Job 3c.4 / CAce1457d1."""
    from app.media_streams import connection as c

    src = inspect.getsource(c)
    i = src.index("timing_preference_known=bool(")
    window = src[i:i + 900]
    assert "soft_context" in window and "time_preference" in window, (
        "the hold clip does not arm on a soft time preference"
    )
