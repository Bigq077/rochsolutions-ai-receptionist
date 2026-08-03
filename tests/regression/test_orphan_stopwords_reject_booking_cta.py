"""B-26 / orphan-detector precision — a booking CTA is not a screen.

Live evidence, 3 Aug 2026 call suite, calls 3 and 5 (`CAc1f19e07589ea2b087c6c7`
and `CAc3c4e6619660fa69416e854`). Both logged:

    orphan NEAR MISS — trauma_fracture matched 1 of the 2 evidence words
    needed; NOT armed: 'Would you like to book an assessment so Marcus can
    take a proper look?'

The collision is the single word "proper": jv_v1's canned `booking_offer`
says "take a proper look", and `trauma_fracture`'s screen_question opens
"That sounds like a proper knock". Nothing clinical was asked on either turn.

Why this is worth a test rather than a shrug: `trauma_fracture` carries only
four evidence words, and the bar is two. One more generic collision in that
sentence and the detector logs a *false* ORPHAN — in the metric B-20 is being
scored against. The failure direction that matters is over-counting, so the
assertions below are about the CTA scoring ZERO, not about it scoring one.
"""

import json
from pathlib import Path

import pytest

from app.media_streams.clinical_screening import (
    _ORPHAN_MIN_EVIDENCE,
    _norm,
    _screen_evidence_words,
)

# The exact string the live clinic speaks, from jv_v1's `booking_offer`.
BOOKING_CTA = "Would you like to book an assessment so Marcus can take a proper look?"


def _jv_clinic():
    p = Path(__file__).resolve().parents[2] / "app" / "clinics" / "jv_v1" / "clinic.json"
    return json.loads(p.read_text(encoding="utf-8"))


def _hits(clinic, sentence):
    """{screen_id: set of evidence words that sentence lands} — the same
    arithmetic match_asked_screen does, isolated from session plumbing."""
    words = set(_norm(sentence).split())
    return {sid: (ev & words) for sid, ev in _screen_evidence_words(clinic).items()}


def test_booking_cta_scores_zero_evidence_against_every_screen():
    """The whole point. Not "below the bar" — zero."""
    for sid, hit in _hits(_jv_clinic(), BOOKING_CTA).items():
        assert hit == set(), (
            f"booking CTA landed {sorted(hit)} on screen {sid!r}; a booking "
            f"offer must contribute no clinical evidence at all"
        )


def test_proper_is_stopworded_not_merely_outvoted():
    """Pins the mechanism, so a future edit that removes "proper" from the
    stopword list fails here and not six weeks later in a sweep count."""
    ev = _screen_evidence_words(_jv_clinic())
    for sid, words in ev.items():
        assert _norm("proper") not in words, (
            f"'proper' is back in {sid!r}'s evidence vocabulary — it is "
            f"conversational scaffolding and collides with the booking CTA"
        )


def test_trauma_fracture_still_has_enough_evidence_to_be_detectable():
    """The fix must not silently disarm the screen it touches. Removing words
    is the safe direction only until a screen drops below the bar, at which
    point it can never be orphan-matched at all."""
    ev = _screen_evidence_words(_jv_clinic())
    assert len(ev["trauma_fracture"]) >= _ORPHAN_MIN_EVIDENCE, (
        f"trauma_fracture is down to {sorted(ev['trauma_fracture'])} — below "
        f"the {_ORPHAN_MIN_EVIDENCE}-word bar, so it is now undetectable"
    )


@pytest.mark.parametrize(
    "screen_id",
    ["cauda_equina", "dvt", "serious_spinal", "trauma_fracture", "vbi_neck", "inflammatory"],
)
def test_each_screens_own_question_still_matches_itself(screen_id):
    """The invariant the stopword list must never break: a screen's real
    question still clears the evidence bar for that screen. This is what makes
    the detector able to see over-screening at all."""
    clinic = _jv_clinic()
    question = next(
        s["screen_question"]
        for s in clinic["clinical_screening"]["screens"]
        if s["id"] == screen_id
    )
    hit = _hits(clinic, question)[screen_id]
    assert len(hit) >= _ORPHAN_MIN_EVIDENCE, (
        f"{screen_id}'s own question now scores only {sorted(hit)} against "
        f"itself — the orphan detector can no longer see this screen"
    )
