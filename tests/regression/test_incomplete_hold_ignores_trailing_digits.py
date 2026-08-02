# tests/regression/test_incomplete_hold_ignores_trailing_digits.py
"""
B-05 (2026-08-02): the incomplete-turn hold fired on complete slot selections,
costing the caller _INCOMPLETE_HOLD_S of silence on the commonest turn in a
booking.

_ends_on_continuation_word tokenised with ``[^a-z' ]+``, which deleted every
numeral BEFORE the last word was read.  "um yeah quarter to 7" therefore
tokenised to ["um", "yeah", "quarter", "to"], ended on the preposition "to",
and was held as a mid-clause fragment.  Nothing was ever going to arrive to
merge with it — the turn was already complete — so the hold ran to its full
timeout and the fragment was re-dispatched afterwards.

British clock readings are exactly the shape that trips this: the preposition
sits immediately before the numeral ("quarter to 7", "the one at 4",
"28 please at 5").  Confirmed in the obs store: the slot-selection turn of
CA85b1f4cc and CA63da640f4d is verbatim 'um yeah quarter to 7' in both.

The tell that this was a tokenisation bug and not a wordlist judgement: on the
identical audio, "quarter to seven" was correctly read as COMPLETE, because
"seven" is alphabetic and survived the strip.  Endpointing depended on whether
AssemblyAI rendered the number as a word or a digit.

flow.py's parallel guard (_looks_incomplete_turn) never had this defect — it
splits on whitespace and keeps digits.  The two implementations disagreed and
connection.py's was the wrong one.
"""
from __future__ import annotations

import pytest

from app.media_streams.connection import (
    _CONTINUATION_TAIL_WORDS,
    _ends_on_continuation_word,
)


# ── The live regressions ────────────────────────────────────────────────────
@pytest.mark.parametrize("text", [
    # verbatim from the obs store, slot-selection turn, two separate calls
    "um yeah quarter to 7",
    "um yeah quarter to 7 in the evening",
    # same shape, other clock readings offered by the same slot payload
    "quarter to 7",
    "the one at 4",
    "28 please at 5",
    "yeah the 6 in the evening one at 6",
    # a bare digit answer to a numbered menu
    "2",
    "number 2",
])
def test_complete_numeric_answer_is_not_held(text):
    assert _ends_on_continuation_word(text) is False, (
        f"{text!r} is a complete answer — holding it buys nothing and costs "
        f"the caller the full incomplete-hold window in silence"
    )


# ── The property that proves it was a tokenisation bug ──────────────────────
@pytest.mark.parametrize("spoken,digits", [
    ("um yeah quarter to seven", "um yeah quarter to 7"),
    ("the one at four", "the one at 4"),
    ("half past five", "half past 5"),
    ("i'll take number two", "i'll take number 2"),
])
def test_digit_and_word_spellings_agree(spoken, digits):
    """Same audio, same meaning — AssemblyAI's choice of rendering must not
    change whether the turn is treated as finished."""
    assert _ends_on_continuation_word(spoken) == _ends_on_continuation_word(digits), (
        f"{spoken!r} and {digits!r} must endpoint identically"
    )


# ── Nothing that was held before may stop being held for the wrong reason ───
@pytest.mark.parametrize("text", [
    # the original RS-06 / C23 probes — genuinely mid-clause, must still hold
    "hi i wanted to ask about",
    "it's for my",
    "and i was hoping to come in",
    "i'd like to book an appointment for",
    "the pain is in my",
    "can i come in on",
    "i think it started because",
    "i want to book a",
    "my number is oh seven and",
    # digits present but the turn still trails off — the digit fix must not
    # swallow these
    "i'm free after 5 but",
    "yeah 2 or",
    "i had it done in 2019 and",
    "can i do 4 in the",
])
def test_genuine_mid_clause_fragments_still_held(text):
    assert _ends_on_continuation_word(text) is True, f"{text!r} ends mid-clause"


# ── The safety invariant that makes this a no-regression change ─────────────
def test_no_continuation_word_contains_a_digit():
    """Keeping digits can only ever turn a True into a False, never the
    reverse: the wordlist holds no digit-bearing token, so a token carrying a
    digit can never match, and the only tokens the fix newly exposes are
    digit-bearing ones.  Strictly fewer holds, never more."""
    offenders = [w for w in _CONTINUATION_TAIL_WORDS if any(c.isdigit() for c in w)]
    assert offenders == [], (
        f"a digit-bearing continuation word would break the monotonicity "
        f"argument for this fix: {offenders}"
    )


@pytest.mark.parametrize("text", [
    "quarter to 7",
    "at 5",
    "on 21",
    "the 3",
    "my 2",
    "and 10",
    "because 12",
])
def test_a_turn_ending_in_a_digit_is_never_held(text):
    """The general form of the defect, independent of any one utterance."""
    assert _ends_on_continuation_word(text) is False


# ── Unchanged behaviour on the non-numeric cases ────────────────────────────
@pytest.mark.parametrize("text", [
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
