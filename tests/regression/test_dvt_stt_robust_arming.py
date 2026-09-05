# tests/regression/test_dvt_stt_robust_arming.py
"""
DVT screen didn't arm when STT mangled "calf" (F-017 / sweep RS, C3 2026-07-24/25).

The jv_v1 DVT trigger_keywords keyed on the body part ("calf", "swollen leg"),
so AssemblyAI returning "call"/"cough" for "calf" meant nothing matched and the
deterministic screen never armed — the model was left to do it alone.

Fix (config, in clinic.json): also trigger on the DVT *symptom combination*
("swollen and warm", "warm and red", …). That survives ANY calf-mangle because
it doesn't depend on the body-part word, and it's far too specific to fire on the
common mishears ("call me back", "I've got a cough") — so it adds robustness
without over-arming. A body-part word is NOT required: a swollen+warm/red leg is
the DVT presentation regardless of how "calf" transcribed.
"""
from __future__ import annotations

import pytest

from app.clinic_config import get_clinic
from app.media_streams import clinical_screening as cs
from tests.screening_fixture import screening_clinic, screening_clinic_json


@pytest.fixture
def jv():
    return screening_clinic()


@pytest.mark.parametrize("text", [
    "in the back of my cough is swollen and warm",   # calf -> cough (the C3 call)
    "my call is all swollen and red",                # calf -> call
    "it's swollen and warm behind the knee",         # body part never named as calf
    "the leg's gone swollen and hot",
])
def test_dvt_arms_on_symptom_combo_despite_calf_mangle(jv, text):
    sess = {}
    cs.update_screening_state(sess, jv, text)
    # Deterministic DVT layer must ENGAGE regardless of how "calf" transcribed —
    # either arm+ask, or (when the caller already volunteered the red-flag
    # symptoms) arm+escalate. Both are correct; the F-017 failure was that
    # NEITHER happened and the model was left to screen alone.
    engaged = (
        sess.get("pending_screen") == "dvt"
        or sess.get("screen_red_flag") == "dvt"
    )
    assert engaged, (
        f"{text!r} states the DVT symptom combination — the deterministic layer "
        "must arm or escalate regardless of the calf-word"
    )


@pytest.mark.parametrize("text", [
    "can you call me back please",
    "i've got a bit of a cough",
    "i'll call you tomorrow to confirm",
    "i had a call from the clinic earlier",
])
def test_common_mishears_do_not_over_arm_dvt(jv, text):
    sess = {}
    cs.update_screening_state(sess, jv, text)
    assert sess.get("pending_screen") != "dvt", (
        f"{text!r} is benign — the symptom-combo triggers must not arm DVT"
    )
    assert sess.get("screen_red_flag") != "dvt", (
        f"{text!r} is benign — must not escalate DVT"
    )


def test_plain_calf_still_arms(jv):
    """Regression: the original body-part trigger must keep working."""
    sess = {}
    cs.update_screening_state(sess, jv, "my calf has been painful and swollen")
    assert sess.get("pending_screen") == "dvt"