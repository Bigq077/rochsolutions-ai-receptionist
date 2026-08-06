"""
A caller who answers with a digit must be heard.

Person C, theorem_v3, 2026-08-06. Offered "Number 1, three in the afternoon /
Number 2, five in the evening", they answered four times and Susie heard none
of it:

    17:16:44  [ms_stt] garbage transcript: '3'
    17:17:03  fragment discarded (reason=single_char_word): "3 o'clock"   #1
    17:17:18  fragment discarded (reason=single_char_word): "3 o'clock"   #2
    17:17:36  fragment discarded (reason=single_char_word): "3 o'clock"   #3
    17:17:48  lost_total=3 by_reason={'single_char_word': 3}  → abandoned

Between the discards the watchdog told them "Sorry, I didn't catch that" with
voice_gap=0.0s and voiced_since_prompt=True — i.e. while they were audibly
speaking.

TWO filters in TWO files, and the digit blind spot was in a third place:

  1. stt_stream._is_garbage_transcript — required \\d{3,}, a rule written for
     phone numbers. "3" has no run of three digits and no letters, so it was
     garbage. Killed the bare digit.
  2. connection.py single_char_word — discards a 2-word fragment containing a
     single-character word. "3 o'clock", "9 am", "2 pm" all match. Killed the
     clock-time answer.
  3. _V3_PRESERVE protected "one".."ten" spelled out but carried no digits.

Direction of the fix matters and is the reason it is safe: a false negative
deletes a caller's turn outright, a false positive costs one cheap classifier
pass. Digits are never mouth-noise — STT does not hallucinate "3" from a cough.

Companion to test_slot_signal_filter_does_not_drop_picks.py, which covers the
third gate downstream ("Three.", "3pm", "Saturday"). All three must hold for a
slot answer to survive; fixing any one alone leaves the others live.
"""

import pytest

from app.media_streams.stt_stream import _is_garbage_transcript
from app.media_streams.connection import (
    _SINGLE_CHAR_PRONOUNS,
    _V3_PRESERVE,
    _is_slot_selection_candidate,
)


def _single_char_word_discard(utterance: str) -> bool:
    """Mirror of the guard at connection.py (condition 1 extension)."""
    stripped = utterance.strip().lower()
    words = stripped.split()
    non_pronoun_singles = [
        w for w in words
        if len(w) == 1 and w not in _SINGLE_CHAR_PRONOUNS and not w.isdigit()
    ]
    return len(words) == 2 and bool(non_pronoun_singles) and stripped not in _V3_PRESERVE


def _survives_every_filter(utterance: str) -> bool:
    return (
        not _is_garbage_transcript(utterance)
        and not _single_char_word_discard(utterance)
        and _is_slot_selection_candidate(utterance)
    )


# ── filter 1: the STT socket boundary ──────────────────────────────────────

@pytest.mark.parametrize("answer", [
    "3",                      # Person C's first answer, 17:16:44
    "1", "2", "4", "5", "9",
    "10", "11", "12",         # two digits died here too
    "2:30", "3:15",           # clock times with no letters
])
def test_a_bare_digit_is_not_garbage(answer):
    assert not _is_garbage_transcript(answer), (
        f"{answer!r} destroyed at the STT boundary before connection.py sees it"
    )


def test_phone_numbers_still_pass():
    """The original purpose of the rule — must not regress."""
    assert not _is_garbage_transcript("07870166861")
    assert not _is_garbage_transcript("07870 166861")


@pytest.mark.parametrize("noise", ["", "   ", "hmm", "er", "uh", "mm"])
def test_genuine_noise_is_still_discarded(noise):
    assert _is_garbage_transcript(noise)


# ── filter 2: the two-word single-character guard ──────────────────────────

@pytest.mark.parametrize("answer", [
    "3 o'clock",   # Person C, three times
    "9 am", "2 pm", "5 pm", "11 am",
])
def test_a_clock_time_answer_is_not_a_fragment(answer):
    assert not _single_char_word_discard(answer), (
        f"{answer!r} discarded as single_char_word — the caller's turn is gone"
    )


@pytest.mark.parametrize("noise", ["r clinic", "t there", "k yeah"])
def test_a_stray_letter_is_still_a_fragment(noise):
    """The case this guard exists for. Widening must not swallow it."""
    assert _single_char_word_discard(noise)


def test_pronouns_still_exempt():
    assert not _single_char_word_discard("i believe")
    assert not _single_char_word_discard("a moment")


# ── filter 3 gap: digits in the preserve list ──────────────────────────────

@pytest.mark.parametrize("digit", list("123456789") + ["10", "11", "12"])
def test_digits_are_preserved_like_their_spelled_forms(digit):
    assert digit in _V3_PRESERVE, (
        f'_V3_PRESERVE protects the spelled form but not {digit!r}'
    )


# ── end to end: Person C's actual call ─────────────────────────────────────

@pytest.mark.parametrize("answer", [
    "3", "3 o'clock", "1", "2", "9 am", "2:30", "10",
])
def test_person_c_would_now_be_heard(answer):
    assert _survives_every_filter(answer), (
        f"{answer!r} still dies somewhere in the chain"
    )


def test_the_three_filters_are_independent():
    """
    Each gate must be separately correct — this is what made the original
    diagnosis wrong twice. "3" only ever reached filter 1; "3 o'clock" only
    ever reached filter 2. Fixing one and shipping leaves the other live.
    """
    assert not _is_garbage_transcript("3 o'clock")        # passes gate 1
    assert not _single_char_word_discard("3")             # never reached gate 2
    assert _is_slot_selection_candidate("3")              # gate 3 was fine for "3"
