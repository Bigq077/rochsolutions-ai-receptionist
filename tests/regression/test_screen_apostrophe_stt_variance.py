# tests/regression/test_screen_apostrophe_stt_variance.py
"""
P1 #4 — deterministic safety screens miss when STT drops the apostrophe.

Jules's 14-call sweep recorded a back-pain screen that "didn't fire at all
before booking — the speech-to-text garbled the trigger word". The garbling is
specific and reproducible: `clinical_screening._norm` stripped every punctuation
character EXCEPT the apostrophe, while 17 of the jv_v1 screening keywords are
contractions ("can't breathe", "can't feel", "can't put weight", "grip's gone").
Speech-to-text routinely emits those as "cant breathe" / "grips gone", so the
literal substring never matched and the screen silently did not arm.

Blast radius is wider than the one missed back-pain screen:

  * EMERGENCY INTERCEPT — "can't breathe" and "can't speak properly" are
    emergency_red_flags keywords. A caller saying "I cant breathe" did NOT
    trigger the deterministic 999 line. This is the life-safety path.
  * CAUDA EQUINA — "can't feel" is the red-flag ANSWER keyword; "I cant feel my
    legs" classified as `unclear` instead of `red_flag`.
  * TRAUMA/FRACTURE — 11 of its keywords are contractions.

Fix: `_norm` deletes apostrophes (straight and curly) rather than preserving
them, so both sides of every comparison agree. `_NEGATIVE_PATTERNS` is passed
through `_norm` at import for the same reason — it is the one literal set that
is compared RAW against normalised text, and it contains "i haven't" / "i don't"
/ "everything's fine". Without that, this fix would have broken clear-negative
classification and left screens pending forever.

Each case is asserted in BOTH forms — with and without the apostrophe — because
the requirement is parity, not merely that the stripped form works.
"""
from __future__ import annotations

import pytest

from app.clinic_config import get_clinic
from app.media_streams import clinical_screening as cs


@pytest.fixture()
def jv():
    return get_clinic("jv_v1")


# ── 1. Emergency intercept — the ~140ms life-safety path ──────────────────
@pytest.mark.parametrize(
    "utterance",
    [
        "i can't breathe",
        "i cant breathe",
        "i can’t breathe",          # curly apostrophe (U+2019)
        "he can't speak properly",
        "he cant speak properly",
    ],
)
def test_emergency_intercept_survives_apostrophe_variance(jv, utterance):
    assert cs.detect_emergency(utterance, jv) is True, (
        f"emergency intercept did not fire for {utterance!r} — "
        "the deterministic 999 path depends on this"
    )


# ── 2. Cauda equina — red-flag ANSWER keyword "can't feel" ────────────────
@pytest.mark.parametrize(
    "utterance",
    ["i can't feel my legs properly", "i cant feel my legs properly"],
)
def test_cauda_equina_positive_answer_survives_apostrophe_variance(jv, utterance):
    screen = cs.get_screen(jv, "cauda_equina")
    assert cs.classify_screen_answer(utterance, screen) == "red_flag"


# ── 3. Trauma/fracture — trigger AND answer keywords ──────────────────────
@pytest.mark.parametrize(
    "utterance",
    ["i can't put any weight on it", "i cant put any weight on it"],
)
def test_trauma_screen_arms_despite_apostrophe_variance(jv, utterance):
    assert cs.match_screen_trigger(utterance, jv, {}) == "trauma_fracture"


@pytest.mark.parametrize("utterance", ["my grip's gone", "my grips gone"])
def test_trauma_positive_answer_survives_apostrophe_variance(jv, utterance):
    screen = cs.get_screen(jv, "trauma_fracture")
    assert cs.classify_screen_answer(utterance, screen) == "red_flag"


# ── 4. Serious spinal — "can't sleep for the pain" trigger ────────────────
@pytest.mark.parametrize(
    "utterance",
    ["i can't sleep for the pain", "i cant sleep for the pain"],
)
def test_serious_spinal_arms_despite_apostrophe_variance(jv, utterance):
    assert cs.match_screen_trigger(utterance, jv, {}) == "serious_spinal"


# ── 5. REGRESSION GUARD — clear-negative classification must still work ───
# _NEGATIVE_PATTERNS is the one literal set compared RAW against normalised
# text. Deleting apostrophes from the text without normalising these patterns
# would send "i haven't"/"i don't"/"everything's fine" answers from `clear` to
# `unclear`, leaving the screen pending and blocking a legitimate booking.
@pytest.mark.parametrize(
    "utterance",
    [
        "no i haven't noticed anything like that",
        "no i havent noticed anything like that",
        "i don't have any of that",
        "i dont have any of that",
        "everything's fine thanks",
        "everythings fine thanks",
    ],
)
def test_clear_negative_still_classifies_as_clear(jv, utterance):
    screen = cs.get_screen(jv, "cauda_equina")
    assert cs.classify_screen_answer(utterance, screen) == "clear", (
        f"{utterance!r} must read as a clear negative — otherwise the screen "
        "stays pending and blocks a legitimate booking"
    )


# ── 6. Bias check — the fix must not make screens fire on nothing ─────────
@pytest.mark.parametrize(
    "utterance",
    ["i'd like to book an appointment please", "id like to book an appointment please"],
)
def test_benign_booking_request_arms_no_screen(jv, utterance):
    assert cs.match_screen_trigger(utterance, jv, {}) is None
