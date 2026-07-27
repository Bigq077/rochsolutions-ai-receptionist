# tests/regression/test_continuation_word_endpointer.py
"""
RS-06 / C23 (2026-07-27): AssemblyAI ends the caller's turn after 600ms of
silence (min_turn_silence, config.py). A deliberate mid-sentence pause exceeds
600ms, so the turn finalises on a fragment and Susie answers half a sentence /
fires "take your time". Raising the global silence would fix it only by adding
latency to EVERY turn — unacceptable on latency-eval.

The cheap, asymmetric alternative: if the finalised transcript ends on a
*continuation word* (a conjunction / preposition / article / possessive / hanging
aux), the 600ms silence is almost certainly a thinking pause, not the end of the
turn — so the endpoint wait is extended ONLY for those. Complete-looking turns
keep today's latency exactly.

This pins the heuristic itself. Wordlist tuned to function words + clearly-
hanging tokens; commonly-terminal verbs ("want", "like") are deliberately OUT,
since "that's what I want" is complete — the following "to"/"for" carries the
continuation instead ("I want to …" ends on "to").
"""
from __future__ import annotations

import pytest

from app.media_streams.connection import _ends_on_continuation_word


@pytest.mark.parametrize("text", [
    # the C23 probes — all trail off mid-clause
    "hi i wanted to ask about",
    "it's for my",
    "and i was hoping to come in",
    # other common mid-sentence pause shapes
    "i'd like to book an appointment for",
    "the pain is in my",
    "can i come in on",
    "i think it started because",
    "i want to book a",
    "my number is oh seven and",
])
def test_incomplete_utterance_extends(text):
    assert _ends_on_continuation_word(text) is True, f"{text!r} ends mid-clause"


@pytest.mark.parametrize("text", [
    # complete turns — must NOT extend (keep today's latency)
    "yes",
    "yes please",
    "no thank you",
    "that's the one i want",
    "my name is tom green",
    "half past four sounds great",
    "i'd like a sports massage",
    "it is",
    "that's right",
    "monday please",
    "",
])
def test_complete_utterance_does_not_extend(text):
    assert _ends_on_continuation_word(text) is False, f"{text!r} is a complete turn"