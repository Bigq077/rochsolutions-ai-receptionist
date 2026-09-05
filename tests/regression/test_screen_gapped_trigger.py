# tests/regression/test_screen_gapped_trigger.py
"""
P1 #3 (part 2) — multi-word triggers must survive filler words in natural speech.

Jules's sweep: "when a caller rattled off their request in one breath, the
back-pain safety screen didn't fire at all before booking." Part of that is STT
apostrophes (fixed) and substring bugs (fixed). The rest is that a multi-word
trigger keyword was matched CONTIGUOUSLY, but people don't speak in the config's
exact phrasing — they drop filler words in the middle:

    keyword "losing weight"    caller "losing a bit of weight"        (gap 3)
    keyword "stiff for an hour" caller "stiff every morning for … hour" (gap 2)
    keyword "both wrists"      caller "both my wrists"                 (gap 1)

Each armed NO screen before this change.

Fix: for TRIGGER matching only, the words of a multi-word keyword may appear in
order with up to `_TRIGGER_MAX_GAP` (3) filler words between them.

SCOPE — deliberately trigger-only. Answer classification and the emergency
intercept keep the strict word-boundary matching from the previous commit. A
missed TRIGGER just fails to ask a screen question (recoverable, and the whole
point of this fix); a looser ANSWER classifier over-escalates and blocks a
booking, and a looser emergency matcher speaks the 999 line spuriously. Those
are the wrong direction to loosen, so they are untouched here.

The window is bounded at 3 precisely so scattered words in an unrelated sentence
do not connect into a spurious trigger — see the negative cases below.
"""
from __future__ import annotations

import pytest

from app.clinic_config import get_clinic
from app.media_streams import clinical_screening as cs
from tests.screening_fixture import screening_clinic, screening_clinic_json


@pytest.fixture()
def jv():
    return screening_clinic()


# ── 1. The recall misses that motivated this ──────────────────────────────
@pytest.mark.parametrize(
    "utterance,expected",
    [
        ("ive been losing a bit of weight and sweating at night", "serious_spinal"),
        ("my hands are stiff every morning for about an hour", "inflammatory"),
        ("both my wrists are swollen and stiff", "inflammatory"),
    ],
)
def test_gapped_multiword_triggers_now_arm(jv, utterance, expected):
    assert cs.match_screen_trigger(utterance, jv, {}) == expected


# ── 2. Contiguous phrasing must still work (no regression) ────────────────
@pytest.mark.parametrize(
    "utterance,expected",
    [
        ("ive got really bad back pain", "cauda_equina"),
        ("my calf is swollen and hot", "dvt"),
        ("i came off my bike yesterday", "trauma_fracture"),
        ("my neck hurts and i keep feeling dizzy", "vbi_neck"),
        ("i get morning stiffness in my joints", "inflammatory"),
    ],
)
def test_contiguous_triggers_still_arm(jv, utterance, expected):
    assert cs.match_screen_trigger(utterance, jv, {}) == expected


# ── 3. Bounded window: scattered words must NOT connect ───────────────────
# The words of a multi-word keyword appear, but flung across an unrelated
# sentence, further apart than the window. These must stay None.
@pytest.mark.parametrize(
    "utterance",
    [
        "both my knees are sore and separately my wrists ache",   # both … wrists, gap>3
        "the weather at night has been lovely for weight training",
        "i want both a massage and to book my wrists in later",
        "stiff competition for the morning slots",
        "my hands are fine and my morning routine is stiff with yoga",
    ],
)
def test_scattered_words_do_not_form_a_trigger(jv, utterance):
    assert cs.match_screen_trigger(utterance, jv, {}) is None


# ── 4. Answer classification stays STRICT (not gapped) ────────────────────
# Gapping is trigger-only. A red-flag answer keyword must still match tightly,
# so answer classification does not gain escalation surface.
def test_answer_classification_not_gapped(jv):
    screen = cs.get_screen(jv, "trauma_fracture")
    # "can't put weight" as a keyword; a gapped "cant ... put ... weight" spread
    # across a sentence must NOT be treated the same way triggers are. A tightly
    # phrased genuine answer still escalates.
    assert cs.classify_screen_answer("i cant put weight on it", screen) == "red_flag"
    # scattered, non-answer: must not escalate
    assert cs.classify_screen_answer(
        "i cant wait to put some weight back on after being ill", screen
    ) != "red_flag"


# ── 5. The gapped matcher unit contract ───────────────────────────────────
@pytest.mark.parametrize(
    "keyword,text,expected",
    [
        ("losing weight", "losing a bit of weight", True),      # gap 3
        ("losing weight", "losing quite a good bit of weight", False),  # gap 4 > window
        ("both wrists", "both my wrists", True),                # gap 1
        ("both wrists", "both my knees not my wrists", False),  # gap 3 but... check window
        ("back pain", "bad back pain", True),                   # contiguous
        ("back pain", "pain in my back", False),                # reverse order — not handled
        ("stiff", "im really stiff", True),                     # single word defers to _kw_in
    ],
)
def test_phrase_in_gapped(keyword, text, expected):
    assert cs._phrase_in(keyword, cs._norm(text), cs._TRIGGER_MAX_GAP) is expected
