# tests/regression/test_a_screen_never_gates_on_its_own_answer.py
"""
A screen must not require, in order to arm, the very symptom it exists to ask
about. Recorded here because Phase 3 of the screening plan proposed exactly
that and it was rejected on measurement.

THE PROPOSAL. cauda_equina and dvt used flat `trigger_keywords` lists, so any
mention of a back armed the cauda screen and any mention of a calf armed DVT.
That over-screens: "I want a sports massage, my lower back is tight" was asked
about bladder control and saddle numbness, which is alarming and reads as not
having listened. The proposed fix was `trigger_all_groups` - a region phrase
AND a second "neuro/acuity" signal - the AND mechanism vbi_neck already uses.

WHY IT WAS REJECTED. The proposed second groups are the screen questions' own
answers:

    cauda_equina   13 of 25   the question asks about saddle numbness and
                              bladder/bowel changes; the group gated on
                              numb / bladder / bowel / saddle / cant feel
    dvt            15 of 16   the question asks about swelling, warmth,
                              redness, surgery and long journeys; the group
                              gated on swollen / warm / red / surgery / flight
    vbi_neck       14 of 14   (pre-existing - see the note at the bottom)

Gating on the answer converts a screen into a confirmation. It can only fire
for a caller who has already volunteered the red flag, and the caller the screen
is FOR is the one with a bad back and early saddle numbness they have not
thought to mention - embarrassing, easy to dismiss, and the whole reason a
receptionist asks rather than waits.

That is F-032 restated. `test_screen_cauda_lay_phrasing.py` is a P1 missed-screen
fix from Jules's 14-call sweep, where a caller said "my back's sore", nothing
armed, and Susie went straight to booking. The grouping reversed it: 29 existing
tests went red, and they were right.

THE EVIDENCE DID NOT SUPPORT IT EITHER. Layer 1 armed cauda on exactly 2 calls
in the stored corpus, both on a bare "my back" - both CORRECT under F-032. The
six "spurious arms" in the Phase 1 before-table were Layer-2 arms and re-asks,
which trigger changes do not touch. There was no measured over-trigger to fix.

THE REAL COMPLAINT IS TONE, NOT RECALL. "The bladder question is alarming for
someone who just wants a massage" is fixed by the routine-framing preamble
("these are just the routine checks we do for back pain - almost everyone says
no to these"), which costs no recall. For the highest-consequence screen in the
system, tone is the lever; the trigger list is not.

ASYMMETRY, stated once so it does not have to be rediscovered: over-triggering
costs one appropriate-sounding question. Under-triggering misses a cauda equina.

WHAT DID SHIP. `_screen_triggered` now OR-s the two mechanisms instead of
returning early on the groups branch, so a screen may carry both. vbi_neck is
the screen that uses it, and it is where the AND bar is genuinely right: neck
pain AND a neuro sign IS the syndrome, and a plain stiff-neck caller should not
be asked about blackouts. Its residual `trigger_keywords` catch the caller who
leads with the dangerous half and never says "neck".
"""
from __future__ import annotations

import json
import re

import pytest

from app.clinic_config import get_clinic
from app.media_streams import clinical_screening as cs


@pytest.fixture()
def jv():
    return get_clinic("jv_v1")


def _screen(clinic, sid):
    for s in cs._screens(clinic):
        if s.get("id") == sid:
            return s
    raise AssertionError("no screen %r" % (sid,))


# -- 1. The region phrase alone must still arm ----------------------------
# These are the F-032 cases. If this block goes red, the narrowing has been
# re-attempted - read the docstring before making it green.
@pytest.mark.parametrize(
    "utterance,expected",
    [
        ("my back's sore", "cauda_equina"),
        ("i've got a bad back", "cauda_equina"),
        ("pain in my lower back", "cauda_equina"),
        ("i think it's sciatica", "cauda_equina"),
        ("i've done my back in", "cauda_equina"),
        ("my calf's been hurting for a few days", "dvt"),
        ("i pulled my calf playing football", "dvt"),
    ],
)
def test_a_region_phrase_alone_still_arms(jv, utterance, expected):
    assert cs.match_screen_trigger(utterance, jv, {}) == expected


# -- 2. No screen may gate on its own question's answer -------------------
# The mechanical form of the rule. cauda_equina and dvt must not carry a
# second trigger group at all; vbi_neck is the documented exception.
_ANSWER_GATED_EXCEPTIONS = {"vbi_neck"}


def test_no_screen_gates_arming_on_its_own_question(jv):
    offenders = []
    for s in cs._screens(jv):
        sid = s.get("id")
        groups = s.get("trigger_all_groups")
        if not groups or len(groups) < 2 or sid in _ANSWER_GATED_EXCEPTIONS:
            continue
        question = re.sub(r"[^a-z ]", " ", (s.get("screen_question") or "").lower())
        echoed = [k for k in groups[1] if k in question]
        if echoed:
            offenders.append((sid, echoed))
    assert not offenders, (
        "these screens can only arm for a caller who has already volunteered the "
        "red flag, which is not a screen: %s" % json.dumps(offenders)
    )


# -- 3. The decisive phrases arm regardless of body part ------------------
# Additive sensitivity. Every one of these armed NOTHING before 2026-08-21.
@pytest.mark.parametrize(
    "utterance",
    [
        "i've been having trouble controlling my bladder",
        "i'm losing feeling in my legs",
        "i've lost feeling in my legs",
        "my legs keep giving way",
        "i've got weakness in my legs",
        "i've been wetting myself",
        "i've gone numb between my legs",
    ],
)
def test_a_decisive_phrase_arms_cauda_without_a_body_part(jv, utterance):
    assert cs.match_screen_trigger(utterance, jv, {}) == "cauda_equina"


# -- 4. The engine change: groups OR keywords, on the one screen using both
@pytest.mark.parametrize(
    "utterance",
    ["i blacked out twice this week", "i keep seeing double", "i had a drop attack"],
)
def test_vbi_arms_on_a_decisive_phrase_with_no_neck_mention(jv, utterance):
    assert "neck" not in utterance
    assert cs.match_screen_trigger(utterance, jv, {}) == "vbi_neck"


def test_vbi_still_needs_both_signals_for_ordinary_neck_pain(jv):
    # The AND bar is right here and must survive the OR: a stiff neck with no
    # neuro sign is not screened for vertebrobasilar insufficiency.
    assert cs.match_screen_trigger("my neck's been stiff since i slept funny", jv, {}) is None
    assert cs.match_screen_trigger("i've got a bit of neck pain", jv, {}) is None
    assert cs.match_screen_trigger(
        "my neck is stiff and i feel dizzy when i turn it", jv, {}
    ) == "vbi_neck"


def test_vbi_carries_both_trigger_mechanisms(jv):
    # Guards the engine change itself. Before it, `_screen_triggered` returned
    # on the groups branch and these keywords would have been dead config.
    s = _screen(jv, "vbi_neck")
    assert s.get("trigger_all_groups"), "vbi_neck lost its AND groups"
    assert s.get("trigger_keywords"), "vbi_neck lost its decisive keywords"


# -- 5. Benign presentations still get no screen --------------------------
@pytest.mark.parametrize(
    "utterance",
    [
        "i'd like to book a sports massage please",
        "please book that in",
        "can i book in for thursday afternoon",
        "how much is an initial assessment",
    ],
)
def test_a_benign_utterance_arms_nothing(jv, utterance):
    assert cs.match_screen_trigger(utterance, jv, {}) is None
