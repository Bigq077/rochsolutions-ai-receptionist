# tests/regression/test_a_plain_yes_flags_a_red_flag_screen.py
"""
"Yes" must flag a red-flag screen. It did not.

`classify_screen_answer` could only ever return `red_flag` by matching a
`red_flag_answer_keywords` entry. There was no affirmative branch at all, so the
most natural possible answer to a direct yes/no question fell through to
`unclear` — while "no" cleared the screen reliably. Asymmetric in the dangerous
direction.

`unclear` is not a safe default here. It leaves the screen pending and hands the
decision back to the LLM, which defeats the entire purpose of a deterministic
safety layer.

Observed live on JV, 2026-08-21, call CA6246ecb88d7a7fb0d33f3f8f66ef4905:

    10:53:14  screen cauda_equina ARMED by: "yeah i've had really bad back pain
              i've been losing feeling in my legs and i've had a bit of trouble
              controlling my bladder"
    10:53:14  screen cauda_equina asked deterministically
    10:53:26  screen cauda_equina answer unclear: 'yeah i do'
    ...       no escalation, call ended abandoned

The caller described cauda equina syndrome — a surgical emergency where delay
costs permanent loss of bladder, bowel and sexual function — and was never told
to seek urgent care. Layer 1 armed correctly and asked correctly; the failure is
one branch downstream of it.

A second defect found while fixing that one, pinned below: the bare negatives
"no"/"nope"/"nah"/"none"/"neither" were matched as SUBSTRINGS, so "no" matched
inside "know" and the answer "yes i know i do" classified as `clear`. They are
whole words now.
"""
from __future__ import annotations

import pytest

from app.clinic_config import get_clinic
from app.media_streams.clinical_screening import _screens, classify_screen_answer


def _screen(clinic_id: str = "jv_v1", screen_id: str = "cauda_equina") -> dict:
    screens = _screens(get_clinic(clinic_id))
    scr = next((s for s in screens if s.get("id") == screen_id), None)
    assert scr is not None, f"{screen_id} missing from {clinic_id}"
    return scr


# ---------------------------------------------------------------------------
# The regression: a bare affirmative is a positive
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("answer", [
    "yeah i do",          # the exact reply from the live call
    "yes",
    "yeah",
    "yep",
    "i do",
    "i have",
    "yes i have",
    "yes both",
    "yeah a bit",
    "definitely",
])
def test_a_leading_affirmative_is_a_red_flag(answer):
    assert classify_screen_answer(answer, _screen()) == "red_flag", (
        f"{answer!r} must escalate — 'unclear' hands a surgical emergency back "
        f"to the LLM, which is exactly what happened on the 21 Aug call"
    )


def test_the_live_call_answer_specifically():
    # Guard the literal string from CA6246ecb88d7a7fb0d33f3f8f66ef4905.
    assert classify_screen_answer("yeah i do", _screen()) == "red_flag"


# ---------------------------------------------------------------------------
# The negative path must be untouched — a wrong escalation is its own harm
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("answer", [
    "no",
    "nope",
    "nah",
    "no i dont",
    "no numbness at all",
    "nothing like that",
    "all fine",
    "i havent had any of that",
    "yes none of that",
    # British idiom: opens affirmative, means no. The negator wins because the
    # affirmative branch runs last.
    "yeah no i dont have that",
])
def test_a_negative_answer_still_clears(answer):
    assert classify_screen_answer(answer, _screen()) == "clear", (
        f"{answer!r} must stay clear — over-escalation blocks legitimate "
        f"bookings and teaches the clinic to ignore the alert"
    )


def test_a_keyword_answer_still_flags():
    # The pre-existing keyword path must be untouched.
    assert classify_screen_answer(
        "i have trouble controlling my bladder", _screen()) == "red_flag"


# ---------------------------------------------------------------------------
# Substring negators: "no" inside "know"
# ---------------------------------------------------------------------------

def test_a_negator_inside_another_word_does_not_clear_a_screen():
    """'know' contains 'no'. This answer used to classify as `clear`."""
    assert classify_screen_answer("yes i know i do", _screen()) == "red_flag"


def test_unsure_is_not_a_no():
    """
    'not' contains 'no', so "im not sure" used to clear the screen outright.
    Unsure must leave it pending so the question is re-asked.
    """
    assert classify_screen_answer("im not sure", _screen()) == "unclear"


# ---------------------------------------------------------------------------
# The classifier is shared, so the fix must hold for every screen
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("screen_id", [
    "cauda_equina", "dvt", "serious_spinal",
    "trauma_fracture", "vbi_neck", "inflammatory",
])
def test_every_screen_escalates_on_a_plain_yes(screen_id):
    assert classify_screen_answer("yes", _screen(screen_id=screen_id)) == "red_flag"


@pytest.mark.parametrize("screen_id", [
    "cauda_equina", "dvt", "serious_spinal",
    "trauma_fracture", "vbi_neck", "inflammatory",
])
def test_every_screen_still_clears_on_a_plain_no(screen_id):
    assert classify_screen_answer("no", _screen(screen_id=screen_id)) == "clear"


def test_an_empty_answer_is_still_unclear():
    assert classify_screen_answer("", _screen()) == "unclear"


# ---------------------------------------------------------------------------
# A leading disfluency must not defeat the affirmative branch
#
# The branch above reads the FIRST word, so one filler token bypassed it
# completely: "yeah i do" flagged, "er yeah i do" fell through to `unclear`.
#
# Live on JV 2026-08-21, call CA4feeeec6f9077d4912eb7d2a7f1d6846 at 11:19:46 —
# ten minutes AFTER the affirmative branch shipped, on a cauda equina screen,
# with the caller confirming saddle numbness / bladder change:
#
#     11:19:33  screen cauda_equina asked deterministically
#     11:19:46  screen cauda_equina answer unclear: 'er yeah i do'
#
# People hesitate when answering a frightening question. Requiring a clean first
# token made the safety branch fire only for callers who happen not to.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("answer", [
    "er yeah i do",       # the exact reply from the live call
    "um yeah",
    "er yes",
    "uh yeah i do",
    "erm yes",
    "um um yes",          # more than one filler
    "hmm yeah i have",
])
def test_a_disfluency_does_not_hide_an_affirmative(answer):
    assert classify_screen_answer(answer, _screen()) == "red_flag"


@pytest.mark.parametrize("screen_id", [
    "cauda_equina", "dvt", "serious_spinal",
    "trauma_fracture", "vbi_neck", "inflammatory",
])
def test_every_screen_escalates_on_a_hesitant_yes(screen_id):
    assert classify_screen_answer(
        "er yeah i do", _screen(screen_id=screen_id)) == "red_flag"


# The strip is for the AFFIRMATIVE lead only. Widening the negative branches the
# same way would add false-CLEAR surface, which is the dangerous direction — so
# pin that a hesitant negative still clears, and that the filler strip has not
# turned a negative into a positive.
@pytest.mark.parametrize("answer", [
    "er no not really",
    "um nah nope",
    "uh no i dont",
    "erm none of those",
])
def test_a_hesitant_negative_still_clears(answer):
    assert classify_screen_answer(answer, _screen()) == "clear"


def test_filler_alone_is_still_unclear():
    """Stripping every word must not leave an empty lead reading as agreement."""
    assert classify_screen_answer("er um", _screen()) == "unclear"
