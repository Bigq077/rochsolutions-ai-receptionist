# tests/regression/test_b43_write_path_reasoning_leak.py
"""
B-43 — "I need to action the cancellation now." was spoken to a caller.

`CA12db707b1b887d38b7408aa36fc990d6`, 3 Aug 2026 10:39:23.738, build
`9d9efddb9a22`.

Gate 5g's `_SELF_NARRATION_RE` carried exactly one arm for this family:
`I need to book (?:this|it) in now` — a **booking-specific literal**. Nine
plausible phrasings of the same internal sentence went straight to TTS and one
was caught.

Identical shape to B-36 cause 2: a guard scoped to BOOKING while the same
failure sits verbatim on the two destructive paths. Adding "cancel" and
"reschedule" literals would have been the same bug waiting for a third verb, so
the verb is generalised instead.

**Why widening is safe here specifically.** Gate 5g is structural — a sentence
only counts as self-narration if it ALSO carries no second-person reference and
is not a question. Those guards, not the verb list, are what protect the
sentences a caller may legitimately hear: "I need to book YOU in now" and
"I need to book this in now — shall I go ahead?" both survive. The verb list
decides what to look at; the guards decide what to spare.
"""
from __future__ import annotations

import pytest

from app.media_streams import turn_handler as th


def _session():
    return {"_clinical_depth_cache": "", "v3_cta_count": 0}


# ── The family. One of these was caught before; all of them must be now ───
_INTERNAL = [
    "I need to book this in now.",                    # the one arm that worked
    "I need to book it in now.",
    "I need to action the cancellation now.",         # <- CA12db707b, verbatim
    "I need to process the cancellation now.",
    "I need to action the reschedule now.",
    "I need to make that change now.",
    "I need to cancel this now.",
    "I need to move this now.",
    "I need to reschedule this now.",
    "I need to sort that now.",
    "I need to do that now.",
]


@pytest.mark.parametrize("sentence", _INTERNAL)
def test_write_path_narration_is_self_narration(sentence):
    assert th._is_self_narration(sentence) is True


@pytest.mark.parametrize("sentence", _INTERNAL)
def test_write_path_narration_never_reaches_tts(sentence):
    assert th.sanitise_response(sentence, _session()) == ""


def test_the_verbatim_call_sentence():
    assert th.sanitise_response(
        "I need to action the cancellation now.", _session()
    ) == ""


def test_the_two_sentence_chunk_from_the_call():
    """The caller heard the first sentence; the second was fine and natural.
    Only the narration should go."""
    out = th.sanitise_response(
        "I need to action the cancellation now. Let me do that for you.",
        _session(),
    )
    assert "action the cancellation" not in out
    assert "Let me do that for you." in out


# ── What the structural guards must keep protecting ───────────────────────
@pytest.mark.parametrize(
    "sentence",
    [
        # second person — addressed to the caller, not to itself
        "I need to book you in now.",
        "I need to get that sorted for you now.",
        "I need to move your appointment now, is that alright?",
        # questions
        "Shall I book that in now?",
        "I need to book this in now — shall I go ahead?",   # the module's KEEP case
        # not this construction at all
        "I'll book that in now.",
        "I need your date of birth now.",
        "I need to check the diary now.",
    ],
)
def test_caller_addressed_speech_survives(sentence):
    assert th._is_self_narration(sentence) is False, (
        f"over-fire: {sentence!r} would be deleted from the caller's audio"
    )
    assert th.sanitise_response(sentence, _session()).strip() != ""


def test_the_guards_and_not_the_verb_list_do_the_sparing():
    """Design property. The same verb and object, flipped only by a
    second-person reference — that is what makes generalising the verb safe."""
    assert th._is_self_narration("I need to book this in now.") is True
    assert th._is_self_narration("I need to book you in now.") is False


def test_the_bounded_gap_does_not_span_sentences():
    """The arm allows up to 40 chars between verb and "now". It must not reach
    across a sentence boundary and eat the following sentence."""
    out = th.sanitise_response(
        "I need to book this in. You're all set for Tuesday now.", _session()
    )
    assert "all set for Tuesday" in out
