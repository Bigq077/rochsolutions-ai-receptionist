"""
A three-letter word is not mouth-noise, and neither is "3pm".

`_single_word_filter_reason` Condition 1 discarded every single-word transcript
of three characters or fewer. It is the FOURTH filter in this chain (the other
three are covered by test_digit_answers_survive_both_filters.py and
test_slot_signal_filter_does_not_drop_picks.py) and the only one with no
active-question exemption at all — it fires even while Susie is waiting for an
answer to a question she just asked.

Confirmed live, CA1747c2d9 on 2026-08-06:

    20:53:19  [ms_stt] fragment discarded (reason=too-short): 'owt'
    20:53:19  [ms_lost] reason=too-short text='owt' call_total=1

"owt" is in this clinic's own STT keyterms prompt. We prime AssemblyAI to
recognise it and then delete it.

Two shapes were dying:

  * DIALECT AND SHORT ENGLISH — "aye" (a yes), "owt", "ta". _V3_PRESERVE had
    been growing to compensate: "yes", "no", "yep", "nah", "one", "two", "six",
    "ten" are all in that list only because Condition 1 would otherwise eat
    them. Every word nobody thought to add was deleted in silence.
  * CLOCK TIMES — "3pm", "9am", "2pm" as too-short; "12pm" as no-vowels,
    because its alpha part is "pm".

"3pm" mattered twice over: a330eb7 had already fixed it at the slot-signal
filter downstream, and it never got there. Second time in one night a fix was
shadowed by an earlier gate.

The fix is shape-based, matching this function's own docstring ("key on the
SHAPE of the token rather than on a vocabulary, so they cannot fail one word at
a time"): any digit exempts, and the length threshold drops to 2. Noise
rejection moves entirely to Conditions 2a/2b/3, which is what they are for.
"""

import pytest

from app.media_streams.connection import (
    _V3_NOISE_FRAGMENTS,
    _V3_PRESERVE,
    _single_word_filter_reason,
)


def _discarded(word: str) -> bool:
    """True if this word is thrown away before anything can act on it."""
    if word.strip().lower() in _V3_PRESERVE:
        return False
    return bool(_single_word_filter_reason(word))


# ── clock times ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("answer", [
    "3pm", "9am", "2pm", "5pm", "8am",
    "12pm",   # died as no-vowels, not too-short — alpha part is "pm"
    "10am", "11am",
    "2:30", "3:15", "15:00",
])
def test_a_clock_time_is_never_noise(answer):
    assert not _discarded(answer), f"{answer!r} discarded — the caller's turn is gone"


def test_the_digit_exemption_is_shape_based_not_a_list():
    """Any digit-bearing token, including forms nobody has thought of yet."""
    for made_up in ("7pm", "4:45", "22nd", "1a", "6"):
        assert not _discarded(made_up)


# ── dialect and short English ──────────────────────────────────────────────

@pytest.mark.parametrize("word", ["aye", "owt", "ta"])
def test_keyterm_dialect_survives(word):
    """We prime the STT for these. Deleting them is self-contradictory."""
    assert not _discarded(word)


@pytest.mark.parametrize("word", [
    "aye",                      # a yes
    "sat", "sun", "mon", "tue", # day abbreviations
    "two", "ten", "six",        # were only safe via _V3_PRESERVE
])
def test_three_letter_words_are_not_discarded_on_length(word):
    assert not _discarded(word)


# ── noise must still be rejected ───────────────────────────────────────────

@pytest.mark.parametrize("noise", [
    "ing", "ic", "er", "um", "uh", "hmm", "hm", "mm", "ah", "eh",
    "mhm", "mmm", "uhh", "umm", "huh", "terday", "s",
])
def test_every_known_noise_fragment_still_discards(noise):
    """
    The whole safety argument for lowering the threshold: Conditions 2a/2b/3
    already reject these on shape, so length was doing no work that mattered.
    """
    assert _discarded(noise), f"{noise!r} now reaches the LLM as if it were speech"


def test_the_full_noise_vocabulary_still_discards():
    """Belt and braces — nothing in the curated list slipped through."""
    survivors = [w for w in _V3_NOISE_FRAGMENTS if not _discarded(w)]
    assert not survivors, f"noise fragments no longer rejected: {sorted(survivors)}"


@pytest.mark.parametrize("stub", ["a", "i", "s", "e", "o"])
def test_single_characters_still_discard(stub):
    assert _discarded(stub)


# ── the threshold itself ───────────────────────────────────────────────────

def test_threshold_is_two_not_three():
    """
    Pinned deliberately. At 3 this deletes "aye" and "owt" again, silently,
    and _V3_PRESERVE starts having to grow once more to compensate.
    """
    assert _single_word_filter_reason("abc") == ""      # 3 chars, real word
    assert _single_word_filter_reason("ab") == "too-short"
    assert _single_word_filter_reason("a") == "too-short"


def test_preserve_list_is_a_backstop_not_the_mechanism():
    """
    "aye" and "owt" must survive on shape, without being added to the preserve
    list — otherwise the next unlisted dialect word is deleted the same way.
    """
    assert "aye" not in _V3_PRESERVE
    assert "owt" not in _V3_PRESERVE
    assert not _discarded("aye")
    assert not _discarded("owt")
