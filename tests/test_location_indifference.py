"""
tests/test_location_indifference.py
-----------------------------------
Clinic-resolver v2 — indifference handler (sign-off sweep Calls 12 & the
force-verify; the resolver bit the tester twice).

Once Susie asks "Awlstuh or Redditch?" some callers decline to choose:
  "whichever" / "either" / "you pick" / "whatever's easiest" /
  "I don't mind" / "both" / "doesn't matter".

The Haiku resolver returns "unknown" for these (they name no clinic), which
previously climbed the re-ask ladder and asked the SAME question again — the
loop that trapped the tester. Indifference IS a decision: it must resolve to
the default clinic (Alcester — open 5 days/week vs Redditch's 1, matching the
resolver's own ambiguity tie-break) so the caller moves on.

_is_location_indifference() is the deterministic predicate. It runs only inside
the location-resolution intercept, where the context is already "which clinic?",
so tokens like "either"/"both" are unambiguous.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.media_streams.connection import (
    _DEFAULT_CLINIC,
    _is_location_indifference,
)


@pytest.mark.parametrize(
    "utterance",
    [
        "whichever",
        "whichever's easiest",
        "whichever is closer",
        "either",
        "either one",
        "either is fine",
        "either works for me",
        "both",
        "both are fine",
        "you pick",
        "you choose",
        "you decide",
        "your choice",
        "your call",
        "up to you",
        "I don't mind",
        "i dont mind",
        "don't care",
        "i really don't care",
        "no preference",
        "no difference",
        "doesn't matter",
        "it doesn't matter to me",
        "does not matter",
        "makes no difference",
        "whatever's easiest",
        "whatever works",
        "any one of them",
        "any of them",
    ],
)
def test_indifference_phrases_detected(utterance):
    assert _is_location_indifference(utterance) is True


@pytest.mark.parametrize(
    "utterance",
    [
        "alcester",
        "redditch",
        "the alcester one",
        "what's the difference between them?",  # a question → LLM, not indifference
        "which is closer to Redditch town?",
        "I'd like to book an appointment",
        "Tuesday afternoon works best",
        "my name is Sarah",
        "",
    ],
)
def test_non_indifference_not_detected(utterance):
    assert _is_location_indifference(utterance) is False


def test_default_clinic_is_alcester():
    # The deterministic default must match the resolver's ambiguity tie-break.
    assert _DEFAULT_CLINIC == "alcester"
