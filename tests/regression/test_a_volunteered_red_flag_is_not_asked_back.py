# tests/regression/test_a_volunteered_red_flag_is_not_asked_back.py
"""
Susie asked a caller whether he had any bladder changes, eight seconds after he
told her he was losing control of his bladder.

JV, 2026-08-21, call CA4feeeec6f9077d4912eb7d2a7f1d6846:

    11:19:33  screen cauda_equina ARMED by: "yeah i've had really bad back pain
              and i've been losing feeling in my legs and i've had a bit of
              trouble controlling my bladder what should i do"
    11:19:33  Susie: "I'm sorry to hear that. Before we look at the next step,
              can I ask — do you have any numbness around the saddle area
              between your legs, or any changes in your bladder or bowel
              control?"
    11:19:46  caller: "er yeah i do"
    ...
    11:20:01  caller: "oh i don't care can i just book an assessment please"

The ALREADY-ANSWERED GUARD exists precisely to stop this: when the arming
utterance already contains the red-flag answer, escalate instead of asking. It
did not fire, because it requires TWO distinct red-flag keywords and this
utterance scored ONE — `bladder` matched, while "losing feeling" was not a
configured phrase and "my legs" is not "both legs".

Two independent fixes, both pinned here:

  1. `decisive_red_flags` — a short per-screen list of symptoms that are
     decisive ALONE. Nobody volunteers loss of bladder control while describing
     a stiff back.
  2. The lay phrasings people actually use ("losing feeling", "legs giving
     way") added to red_flag_answer_keywords, which independently takes this
     utterance to two signals.

The over-escalation pins matter as much as the positives. A screen that
escalates on an ordinary strain sends people to A&E for nothing and teaches a
clinic to ignore the alert.
"""
from __future__ import annotations

import pytest

from app.clinic_config import get_clinic
from app.media_streams.clinical_screening import (
    _decisive_red_flag,
    _red_flag_hits,
    _screens,
    update_screening_state,
)

# The caller's opening turn, verbatim from the call log.
LIVE_UTTERANCE = (
    "yeah i've had really bad back pain and i've been losing feeling in my "
    "legs and i've had a bit of trouble controlling my bladder what should i do"
)


def _clinic():
    return get_clinic("jv_v1")


def _screen(screen_id: str = "cauda_equina") -> dict:
    scr = next((s for s in _screens(_clinic()) if s.get("id") == screen_id), None)
    assert scr is not None, f"{screen_id} missing from jv_v1"
    return scr


# ---------------------------------------------------------------------------
# The regression
# ---------------------------------------------------------------------------

def test_the_live_call_escalates_without_asking_anything_back():
    session: dict = {}
    result = update_screening_state(session, _clinic(), LIVE_UTTERANCE)

    assert result["action"] == "escalate", (
        "the caller volunteered the red flag; asking the screen question back "
        "is the defect this test exists for"
    )
    # And it must be the escalation, not the question.
    assert "111" in (result["speak"] or "")
    assert "do you have any numbness" not in (result["speak"] or "")
    assert session["screen_arm_paths"]["cauda_equina"] == "arming_utterance"


def test_the_live_utterance_is_decisive_on_its_own():
    assert _decisive_red_flag(LIVE_UTTERANCE, _screen()) == "bladder"


def test_the_lay_phrasings_independently_reach_two_signals():
    """Either fix alone must be enough — they are deliberately redundant."""
    assert _red_flag_hits(LIVE_UTTERANCE, _screen()) >= 2


@pytest.mark.parametrize("utterance", [
    "my back is killing me and i've started wetting myself",
    "bad back and i can't control my bowel",
    "lower back pain and i'm numb around the saddle area",
])
def test_a_single_decisive_symptom_escalates_without_asking(utterance):
    result = update_screening_state({}, _clinic(), utterance)
    assert result["action"] == "escalate"


# ---------------------------------------------------------------------------
# The other side: this must not become a hair trigger
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("utterance", [
    "ive got a bad back and its a bit stiff",
    "my calf is painful and swollen",          # one ordinary keyword: ask, don't escalate
    "i twisted my ankle at football",
    "my back has been sore since i did the gardening",
])
def test_an_ordinary_presentation_still_gets_the_question_asked(utterance):
    result = update_screening_state({}, _clinic(), utterance)
    assert result["action"] in ("ask_screen", "none"), (
        "an ordinary musculoskeletal complaint must never escalate unprompted"
    )


def test_a_denied_decisive_symptom_does_not_escalate():
    """
    _occurrence_negated must apply to the decisive list too. "no trouble with
    my bladder" names the keyword while ruling it out.
    """
    utterance = "no trouble with my bladder at all, just a sore back"
    assert _decisive_red_flag(utterance, _screen()) is None
    result = update_screening_state({}, _clinic(), utterance)
    assert result["action"] != "escalate"


def test_decisive_lists_stay_short():
    """
    Every entry bypasses the screen question entirely, so this list is not a
    place to accumulate keywords. If it is growing, the fix is probably the
    matcher shape, not more phrases.

    The cauda equina list is the longest because several entries are positives
    that CONTAIN a negator — "can't control my bowel", "trouble controlling my
    bladder". Those cannot be collapsed into "bowel"/"bladder": the surrounding
    "can't" puts the bare keyword inside _occurrence_negated's window, so the
    phrase reads as a denial. Spelling them out is what triggers the brake for
    a keyword whose own words include a negator (the same reason "no feeling"
    and "can't walk" are configured as phrases).
    """
    for scr in _screens(_clinic()):
        decisive = scr.get("decisive_red_flags") or []
        assert len(decisive) <= 16, (
            f"{scr.get('id')} has {len(decisive)} decisive keywords"
        )
        # A decisive keyword must also be a red flag for that screen, or the
        # escalation text will not match what the caller said.
        answers = set(scr.get("red_flag_answer_keywords") or [])
        for k in decisive:
            assert any(k in a or a in k for a in answers) or k in answers, (
                f"{scr.get('id')}: decisive keyword {k!r} is not in that "
                f"screen's red_flag_answer_keywords"
            )
