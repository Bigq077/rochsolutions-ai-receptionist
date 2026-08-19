"""A phone number read back to the caller is paced, and paced identically,
whichever part of the system produced the words.

Three things go wrong on a fast readback, and only the first is cosmetic:

  1. The caller cannot check eleven digits against the number in their hand.
  2. If they cannot check it, a digit Twilio mangled in transit survives —
     and A3 then holds the booking to it (see _readback_keypad_number).
  3. A wrong number reaches the calendar entry and the confirmation SMS, where
     nobody notices until the patient does not turn up.

Before this change, the readback in the booking, reschedule and cancel flows —
the three that matter — had NO deterministic pacing at all.  `_spell_phone`
only ever fired on numerals, and the template prompt used by every template_v1
clinic asks the model for the already-spoken word form, which it generally
produces.  So the one utterance on the call that most needs pacing was the one
utterance the pacing rule declined to touch.

Companion file: test_tts_phone_speller.py, which owns the numeral half of the
rule and, more importantly, the false-positive set.  Both halves feed
_pace_digit_groups, and the convergence test below is what pins them together.
"""
import pytest

from app.media_streams import tts_stream
from app.media_streams.tts_stream import (
    _apply_tts_substitutions_elevenlabs as _subs,
    _apply_tts_substitutions_openai as _subs_openai,
    _is_spoken_phone_number,
)


SPOKEN = "oh seven five oh two, two one one, two oh seven"


# ── The defect: the word form was never paced ───────────────────────────────

def test_an_ungrouped_spoken_run_is_regrouped():
    """The model wrote every digit in one breath.  Nothing downstream would
    have added a pause, because there is not a digit in the string."""
    out = _subs("I've got you on oh seven five oh two two one one two oh seven.")
    assert SPOKEN in out


def test_a_per_digit_spoken_run_is_regrouped():
    """The Theorem prompt asks for one digit at a time; read as authored that
    is a comma after every digit, which is not three groups and not checkable."""
    out = _subs(
        "So that's oh, seven, five, oh, two, two, one, one, two, oh, seven — correct?"
    )
    assert SPOKEN in out
    assert out.count(",") == 2


def test_per_digit_numerals_are_regrouped_too():
    """Same defect, numeral spelling — "0 7 5 0 2 ..." is digit-SPACING, not
    grouping, and honouring it produced eleven one-digit groups."""
    out = _subs("So that's 0 7 5 0 2 2 1 1 2 0 7 — is that correct?")
    assert SPOKEN in out
    assert out.count(",") == 2


# ── Convergence: one number, one spoken form, four producers ────────────────

@pytest.mark.parametrize("produced_by,text", [
    # clinic_template_prompt.py step 8 / reschedule-cancel lookup step (VE, JV)
    ("template prompt",   f"I've got you on {SPOKEN} — is that the best number?"),
    # config.py Theorem prompt, "Caller gives phone number"
    ("theorem prompt",    "So that's 0 7 5 0 2 2 1 1 2 0 7 — is that correct?"),
    # connection.py _readback_keypad_number — booking AND cancel/reschedule
    ("keypad readback",   "Thanks — I've got 07502211207. Is that correct?"),
    # whatever a free-form turn happens to emit
    ("free-form numerals", "Just to confirm, 07502 211 207 — right?"),
])
def test_every_producer_lands_on_the_same_spoken_form(produced_by, text):
    assert SPOKEN in _subs(text), produced_by


def test_the_readback_is_slowed_whoever_produced_it(): 
    for text in (
        f"I've got you on {SPOKEN} — is that the best number?",
        "So that's 0 7 5 0 2 2 1 1 2 0 7 — is that correct?",
        "Thanks — I've got 07502211207. Is that correct?",
    ):
        assert _is_spoken_phone_number(_subs(text))


# ── Grouping the model chose on purpose still survives ──────────────────────

def test_a_spoken_landline_keeps_its_authored_grouping():
    """0121 496 0000 is read 0121 / 496 / 0000.  The word rule runs AFTER the
    numeral rule and reads its output, so without the same grouping test it
    would re-impose the mobile 5/3/3 on a landline the numeral rule had just
    got right."""
    out = _subs("Our other site is on oh one two one, four nine six, oh oh oh oh.")
    assert "oh one two one, four nine six, oh oh oh oh" in out


def test_the_canonical_form_is_a_fixed_point():
    """Substitutions are applied twice on the live path — connection.py
    _tts_loop and again inside synthesise_chunk."""
    once  = _subs("I've got you on 07502211207.")
    assert _subs(once) == once
    assert _subs(_subs(once)) == once


def test_openai_fallback_paces_the_word_form_identically():
    text = f"I've got you on {SPOKEN} — is that right?"
    assert _subs_openai(text) == _subs(text)


# ── False positives.  A wrongly-paced sentence is worse than a fast one ─────

@pytest.mark.parametrize("phrase", [
    "Oh, one moment — let me check that for you.",
    "That's one or two, maybe three or four, five or six weeks of rehab, "
    "seven or eight sessions, nine or ten.",
    "Oh, that's fine — one of our physios can see you.",
    "Zero pressure either way.",
    "The first appointment is 45 minutes.",
    "That's on 6:30 in the evening.",
    "I can do the 1st, the 2nd or the 15th.",
    "We're at B49 5AB, just off the high street.",
])
def test_ordinary_speech_is_neither_rewritten_nor_slowed(phrase):
    out = _subs(phrase)
    assert out == phrase
    assert not _is_spoken_phone_number(out)


def test_a_run_that_is_not_a_phone_number_is_left_alone():
    """Twelve digit-words is not a UK number; guess at it and a reference code
    gets read as a phone number."""
    phrase = "oh one two three four five six seven eight nine oh one two"
    assert _subs(phrase) == phrase


def test_a_run_that_does_not_start_on_zero_is_left_alone():
    """The 0-prefix is what keeps ordinary counting out of the rule."""
    phrase = "one two three four five six seven eight nine one two"
    assert _subs(phrase) == phrase


# ── The separator must stay a comma ─────────────────────────────────────────

def test_the_number_is_never_split_across_two_synthesis_calls():
    """" — " between groups reads as a longer pause and is the obvious
    improvement.  It is also wrong: chunker._SPLIT_MIN_LEFT is 40, so
    split_tts_text accepts the em-dash after the second group and sends the
    last three digits to ElevenLabs as a separate request — an uncontrolled gap
    mid-number, with a barge-in able to land inside it.  This test fails if
    anyone swaps the separator, which is the point."""
    from app.media_streams.chunker import split_tts_text

    line  = _subs(
        "I've got you on 07502211207 — is that the best number for the booking?"
    )
    parts = split_tts_text(line)
    assert any(SPOKEN in p for p in parts), parts
