# tests/regression/test_screen_cauda_lay_phrasing.py
"""
F-032 (P1, missed screen) — cauda equina must arm on the common LAY phrasings
of back pain, not only on "back pain" / "my back".

Jules's 14-call sweep, CALL 14 turn 2: under one-breath compression the caller
said "my back's sore", the cauda screen did NOT arm, and Susie went straight to
`check_availability` + slots with no safety screen for a back-pain caller. Cauda
equina is the highest-consequence screen (a missed one risks permanent nerve
damage), so a recall gap here is a P1.

Root cause (verified against jv_v1/clinic.json): the cauda `trigger_keywords`
only covered "back pain" / "backache" / "my back". The everyday ways a caller
describes the same complaint — "bad back", "sore back", "back's sore" — matched
NOTHING and armed no screen.

Fix: additive vocabulary only (`sore back`, `bad back`, `back is sore`,
`backs sore`, `stiff back`, `aching back`, `hurt my back`, `done my back`,
`dodgy back`, `sore lower back`, `put my back out`, `back went`). Recall only —
no engine change.

SCOPE — deliberately additive. This does NOT fix F-029 (the cauda screen
FALSELY arming on "behind my back" for a shoulder complaint), which is a
precision problem in the opposite direction and needs its own change; mixing the
two in one commit would blur the regression signal. The benign-phrase cases
below only assert my additions introduce no NEW false arms.

Note on STT: the CALL 14 transcript degraded "back's sore" all the way to
"back so". That specific garble is an STT/normalisation problem, not a keyword
one — matching a bare "back so" would also fire on "call you back soon" and
create a fresh over-screen. This fix closes the well-transcribed lay-phrasing
class; arbitrary STT mangling is out of its scope.
"""
from __future__ import annotations

import pytest

from app.clinic_config import get_clinic
from app.media_streams import clinical_screening as cs


@pytest.fixture()
def jv():
    return get_clinic("jv_v1")


# ── 1. The lay phrasings that armed NO screen before this fix ──────────────
@pytest.mark.parametrize(
    "utterance",
    [
        "my back's sore",
        "back's sore",
        "sore back",
        "bad back",
        "my back's really sore",
        "i've got a bad back",
        "i've done my back in",
        "put my back out at the weekend",
        "dodgy lower back",
        "stiff back this morning",
        "i hurt my back lifting",
        "my back went yesterday",
    ],
)
def test_cauda_arms_on_lay_back_pain_phrasing(jv, utterance):
    assert cs.match_screen_trigger(utterance, jv, {}) == "cauda_equina"


# ── 2. The additions must not introduce NEW false arms ─────────────────────
# "behind my back" is the pre-existing F-029 over-arm via the OLD `my back`
# keyword and is intentionally left as-is (its own fix) — so it is not asserted
# here. These are phrases my new keywords must NOT catch.
@pytest.mark.parametrize(
    "utterance",
    [
        "i'll call you back",
        "can you get back to you",
        "i'll be back soon",
        "give me a sore throat remedy",   # 'sore' present, not a back complaint
        "my knee is sore",                # 'sore' present, wrong region
        "my shoulder is stiff",           # 'stiff' present, wrong region
    ],
)
def test_additions_do_not_over_arm(jv, utterance):
    assert cs.match_screen_trigger(utterance, jv, {}) != "cauda_equina"


# ── 3. The original cauda vocabulary still arms (no regression) ────────────
@pytest.mark.parametrize(
    "utterance",
    [
        "ive got really bad back pain",
        "pain in my lower back",
        "i think it's sciatica",
        "my back has been aching",
    ],
)
def test_original_cauda_triggers_still_arm(jv, utterance):
    assert cs.match_screen_trigger(utterance, jv, {}) == "cauda_equina"
