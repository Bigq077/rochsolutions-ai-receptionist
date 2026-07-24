# tests/regression/test_screen_answer_negation.py
"""P1 (2026-07-24) — denying a red-flag symptom escalated the caller.

Incident
--------
`classify_screen_answer()` checked `red_flag_answer_keywords` before it looked
at anything else, so a keyword only had to APPEAR in the reply. The natural
ways a caller says no all repeat the word they are denying, so they escalated:

    "no its not swollen or warm"                 -> dvt            red_flag
    "no, nothing like that, no surgery or trips" -> dvt            red_flag
    "no weight loss no fevers"                   -> serious_spinal red_flag
    "no dizziness or double vision"              -> vbi_neck       red_flag
    "no numbness and my bladder is fine"         -> cauda_equina   red_flag

Five of eight natural negative answers. `red_flag` speaks the NHS 111
escalation AND sets `screen_red_flag`, which blocks `book_appointment` at the
tool boundary for the remainder of the call — so the most natural way to say
"no" cost the booking.

Why it surfaced now
-------------------
The path was live but rarely reached: the STT keyterm boost was broken, "calf"
transcribed as "car"/"coffee", and `match_screen_trigger()` never armed the
screen. Repairing the boost (see test_stt_keyterms_per_clinic.py) makes this
classifier run on every clinical call, so the two fixes ship together. On the
21:49 call the caller said "it's not swollen or warm"; with the boost fixed and
this defect unfixed, that answer would have blocked the booking.

Fix
---
`_occurrence_negated()` — a red-flag keyword inside the scope of a negator does
not count. Deliberately generous, with two brakes so it cannot swallow a real
positive:

  1. a keyword carrying its own negator is never negatable ("no feeling",
     "cant walk", "grips gone");
  2. an affirmative marker between the negator and the keyword cancels the
     negation ("no, but Ive had surgery") — the false negative that would
     otherwise cost a DVT on a compound screen question.
"""

import json
from pathlib import Path

import pytest

from app.media_streams.clinical_screening import (
    _occurrence_negated,
    _red_flag_hits,
    classify_screen_answer,
)

_CLINIC = Path("app/clinics/jv_v1/clinic.json")


@pytest.fixture(scope="module")
def screens():
    clinic = json.loads(_CLINIC.read_text(encoding="utf-8"))
    return {s["id"]: s for s in clinic["clinical_screening"]["screens"]}


# ---------------------------------------------------------------------------
# The regression: denials must not escalate.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "screen_id,answer",
    [
        # The five measured on 2026-07-24, one per affected screen.
        ("dvt", "no its not swollen or warm"),
        ("dvt", "no, nothing like that, no surgery or long trips"),
        ("serious_spinal", "no weight loss no fevers"),
        ("vbi_neck", "no dizziness or double vision"),
        ("cauda_equina", "no numbness and my bladder is fine"),
        # The live 21:55 answer that the two layers disagreed about.
        ("dvt", "no im just kind of tired lately so the muscle might be a bit tired"),
        # Other natural phrasings of the same denial.
        ("dvt", "no theres no swelling and its not warm"),
        ("trauma_fracture", "no its not out of shape"),
        ("inflammatory", "no its not both sides"),
    ],
)
def test_denied_symptom_does_not_escalate(screens, screen_id, answer):
    verdict = classify_screen_answer(answer, screens[screen_id])
    assert verdict != "red_flag", (
        f"{screen_id}: {answer!r} classified {verdict!r} — the caller is "
        "DENYING the symptom, and red_flag blocks booking for the whole call"
    )


# ---------------------------------------------------------------------------
# Brake 1 — red flags that are phrased negatively must still fire.
# This is the unbounded-harm direction; it matters more than the half above.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "screen_id,answer",
    [
        ("cauda_equina", "no feeling in my legs"),
        ("cauda_equina", "i cant feel anything down there"),
        ("trauma_fracture", "i cant put weight on it"),
        ("trauma_fracture", "i cant use it at all"),
        ("trauma_fracture", "my grips gone"),
    ],
)
def test_self_negating_red_flags_still_fire(screens, screen_id, answer):
    assert classify_screen_answer(answer, screens[screen_id]) == "red_flag", (
        f"{screen_id}: {answer!r} is a red flag PHRASED negatively — the "
        "negation guard must not read its own negator as a denial"
    )


# ---------------------------------------------------------------------------
# Brake 2 — a volunteered positive after a "no" must still fire. The screen
# questions are compound ("is it swollen... AND have you had surgery"), so
# answering one half no and the other half yes is an expected shape.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "screen_id,answer",
    [
        ("dvt", "no but ive had surgery recently"),
        ("dvt", "no swelling but it is warm"),
        ("dvt", "i had a long flight last week"),
        ("serious_spinal", "no fevers but ive lost weight"),
        ("cauda_equina", "no numbness but my bladder has been leaking"),
    ],
)
def test_volunteered_positive_after_a_no_still_fires(screens, screen_id, answer):
    assert classify_screen_answer(answer, screens[screen_id]) == "red_flag", (
        f"{screen_id}: {answer!r} volunteers a red flag — reading the leading "
        "'no' as governing it is the false negative that costs a DVT"
    )


# ---------------------------------------------------------------------------
# Plain positives are untouched.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "screen_id,answer",
    [
        ("dvt", "yes it is swollen"),
        ("dvt", "yes its warm and red compared to the other one"),
        ("cauda_equina", "yes there is numbness in the saddle area"),
        ("vbi_neck", "yes i get double vision"),
        ("inflammatory", "yes both hands"),
        ("serious_spinal", "i have a history of cancer"),
    ],
)
def test_positive_answers_unchanged(screens, screen_id, answer):
    assert classify_screen_answer(answer, screens[screen_id]) == "red_flag"


# ---------------------------------------------------------------------------
# Unit-level behaviour of the guard itself.
# ---------------------------------------------------------------------------
def test_one_unnegated_occurrence_keeps_the_flag():
    """Denying a keyword once does not license it elsewhere in the sentence."""
    assert not _occurrence_negated("it wasnt swollen yesterday but its swollen now", "swollen")


def test_absent_keyword_is_not_negated():
    """Absence must not read as a denial — _kw_in still gates the call."""
    assert not _occurrence_negated("everything is fine", "swollen")


def test_scope_breaker_ends_the_negation():
    assert _occurrence_negated("no swelling", "swelling")
    assert not _occurrence_negated("no pain but swelling", "swelling")


def test_negation_does_not_reach_across_a_long_clause():
    """The window is one clause wide, not the whole utterance."""
    assert not _occurrence_negated(
        "no i went to the shops and then later on it was swollen", "swollen"
    )


def test_red_flag_hits_ignores_denied_keywords(screens):
    """The two-signal unprompted guard must not be cleared by a denial.

    "its not swollen or warm" is two distinct keywords and would otherwise
    meet the bar that exists to make unprompted escalation specific.
    """
    dvt = screens["dvt"]
    assert _red_flag_hits("its not swollen or warm", dvt) == 0
    assert _red_flag_hits("its swollen and warm", dvt) == 2
