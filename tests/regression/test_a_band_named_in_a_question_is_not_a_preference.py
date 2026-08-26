"""
Regression: a time band mentioned inside a QUESTION was banked as a standing
preference the caller never stated.

B-91, second half. `CA70cc833f` (26 Aug 2026, theorem_v3, build `1a711a54`).

    18:28:53  time_of_day_preference captured: mornings (from utterance 'um
              actually on that wednesday do you have any other slots than the
              10 in the morning is that all you have that day')

From that moment every prompt build carried:

    TIME OF DAY PREFERENCE CONFIRMED (caller stated this explicitly — do NOT
    ask again): mornings

The caller stated nothing of the kind. They asked whether the morning slot was
all there was. The latch is never cleared within a call, so had they gone on to
ask about another day they would have been shown a morning-only view and told
it was the whole day — which is the B-90 mechanism reached by a second door.

WHY IT SURVIVED: the sibling extractor ~1200 lines below has had exactly this
gate since 6e6d7aa, and its own sweep comment already records this shape —

    "what if I rearrange the morning of"  ->  timing preference = mornings (T-11)

— from the 2026-08-04 acceptance sweep. The gate was added to one block and not
the other, and the two ran on the same utterance in the same turn with opposite
results:

    18:28:53  time_of_day_preference captured: mornings          (no gate)
    18:28:57  soft-context extraction skipped — caller asked a question  (gated)

DIRECTION IS DELIBERATE. The capture block's own comment states the bias:
failing to set a filter merely costs a re-ask, while setting one wrongly hides
real availability and reports the remainder as complete. This gate fails in the
cheap direction.

THE COST IS REAL AND ACCEPTED: "have you got anything in the mornings" contains
"have you" and so no longer latches. That caller still gets mornings on the
turn they asked — the model reads the utterance directly — and what is lost is
persistence into later turns. Recorded here so the trade is visible if it is
ever revisited.
"""
from __future__ import annotations

import inspect

import pytest

from app.media_streams.connection import _transcript_is_question


LIVE_UTTERANCE = (
    "um actually on that wednesday do you have any other slots than the "
    "10 in the morning is that all you have that day"
)

# From the 2026-08-04 acceptance sweep, quoted in the soft-context block.
SWEEP_T11 = "what if I rearrange the morning of"


@pytest.mark.parametrize("utterance", [LIVE_UTTERANCE, SWEEP_T11])
def test_the_defect_utterances_are_recognised_as_questions(utterance):
    """Both name a band; neither states a preference."""
    assert _transcript_is_question(utterance)


@pytest.mark.parametrize(
    "utterance",
    [
        "mornings please",
        "afternoons are better for me",
        "anytime next week",
        "i don't really care anytime",
    ],
)
def test_a_stated_preference_is_not_gated(utterance):
    """The gate must not swallow the answers it exists to preserve — these are
    the forms the capture block was built for."""
    assert not _transcript_is_question(utterance)


def test_the_capture_site_applies_the_question_gate():
    """Wiring. Anchored on the log call, not the bare phrase — this file's own
    docstring quotes that phrase, and a source search would find the comment."""
    from app.media_streams import connection as c

    src = inspect.getsource(c)
    i = src.index('"[ms_conn v3] time_of_day_preference captured: %s"')
    window = src[i - 8000:i]
    assert "_transcript_is_question" in window, (
        "the time-of-day capture does not gate on whether the caller was "
        "asking a question — the sibling soft-context extractor does, and "
        "this is the block that latched 'mornings' off a question on "
        "CA70cc833f"
    )


def test_both_extractors_share_the_gate():
    """The two blocks disagreed on the same utterance in the same turn. They
    must not be allowed to drift apart again."""
    from app.media_streams import connection as c

    src = inspect.getsource(c)
    assert src.count("_transcript_is_question(") >= 3, (
        "expected the helper at its definition plus BOTH extractor call "
        "sites; one of them has lost its gate"
    )
