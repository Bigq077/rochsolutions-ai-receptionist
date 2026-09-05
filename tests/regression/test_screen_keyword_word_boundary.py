# tests/regression/test_screen_keyword_word_boundary.py
"""
P1 #3 (part 1) — screening keywords matched as substrings, not words.

`_screen_triggered`, `detect_emergency`, `_red_flag_hits` and
`classify_screen_answer` all tested keywords with `keyword in text`. Several
jv_v1 keywords are short common fragments — 'red', 'hot', 'numb', 'fell',
'calf', 'fever' — so they matched INSIDE unrelated words.

The dangerous direction is red-flag ANSWER classification. A positive answer
sets `screen_red_flag`, speaks the urgent-care escalation deterministically, and
`booking_blocked_reason` then blocks booking for the remainder of the call.
Verified before the fix, all on the DVT screen (keywords 'red', 'hot', 'warm'):

    "no im just really tired"       -> red_flag   ('red' inside 'tired')
    "ive recovered well since then" -> red_flag   ('red' inside 'recovered')
    "my physio referred me"         -> red_flag   ('red' inside 'referred')
    "no number changes at all"      -> red_flag   ('numb' inside 'number')

So a caller who answered "no, I'm just really tired" was told they may have a
clot and could not book. "tired" is not a rare word in a physiotherapy call.

The trigger side had the mirror-image defect, over-firing rather than
over-escalating: "my fellow runner recommended you" armed the trauma screen
because 'fell' is inside 'fellow'.

Fix: `_kw_in()` — space-padded containment. `_norm` already reduces text to
[a-z0-9 ] with single spaces, so `" red " in " ... "` is exact word-boundary
matching without regex escaping.

DELIBERATELY OUT OF SCOPE — `_NEGATIVE_PATTERNS` (compared raw in
`classify_screen_answer`) is left substring-matched. Its looseness errs toward
classifying an answer as `clear`, i.e. toward NOT escalating and NOT blocking a
booking. Tightening it could turn clear negatives into `unclear`, leaving screens
pending and blocking legitimate bookings — the opposite of safe. This commit
fixes only the direction that over-escalates.
"""
from __future__ import annotations

import pytest

from app.clinic_config import get_clinic
from app.media_streams import clinical_screening as cs
from tests.screening_fixture import screening_clinic, screening_clinic_json


@pytest.fixture()
def jv():
    return screening_clinic()


# ── 1. Red-flag ANSWER classification must not fire on embedded fragments ──
@pytest.mark.parametrize(
    "utterance",
    [
        "no im just really tired",
        "no nothing like that im just tired",
        "ive recovered well since then",
        "my physio referred me",
        "im a bit worried about it",
    ],
)
def test_dvt_answer_not_escalated_by_embedded_fragment(jv, utterance):
    """'red' lives inside tired / recovered / referred / worried."""
    screen = cs.get_screen(jv, "dvt")
    assert cs.classify_screen_answer(utterance, screen) != "red_flag", (
        f"{utterance!r} falsely escalated — this speaks an urgent-care warning "
        "and blocks booking for the rest of the call"
    )


def test_cauda_answer_not_escalated_by_numb_inside_number(jv):
    screen = cs.get_screen(jv, "cauda_equina")
    assert cs.classify_screen_answer("no number changes at all", screen) != "red_flag"


# ── 2. Genuine red-flag answers must STILL escalate ───────────────────────
@pytest.mark.parametrize(
    "utterance",
    ["yes its red and hot", "its warm and swollen", "yes the calf is red"],
)
def test_genuine_dvt_red_flag_still_escalates(jv, utterance):
    screen = cs.get_screen(jv, "dvt")
    assert cs.classify_screen_answer(utterance, screen) == "red_flag"


@pytest.mark.parametrize(
    "utterance",
    ["yes ive got numbness in the saddle area", "ive lost control of my bowel"],
)
def test_genuine_cauda_red_flag_still_escalates(jv, utterance):
    screen = cs.get_screen(jv, "cauda_equina")
    assert cs.classify_screen_answer(utterance, screen) == "red_flag"


# ── 3. Triggers must not arm on embedded fragments ────────────────────────
@pytest.mark.parametrize(
    "utterance",
    ["my fellow runner recommended you", "we had a lovely time at the fellowship"],
)
def test_trauma_screen_not_armed_by_fell_inside_fellow(jv, utterance):
    assert cs.match_screen_trigger(utterance, jv, {}) != "trauma_fracture"


# ── 4. Genuine triggers must STILL arm ────────────────────────────────────
@pytest.mark.parametrize(
    "utterance,expected",
    [
        ("i fell down the stairs", "trauma_fracture"),
        ("i came off my bike yesterday", "trauma_fracture"),
        ("my calf is swollen and hot", "dvt"),
        ("ive got really bad back pain", "cauda_equina"),
        ("my neck hurts and i keep feeling dizzy", "vbi_neck"),
        ("i had cancer a few years back", "serious_spinal"),
    ],
)
def test_genuine_triggers_still_arm(jv, utterance, expected):
    assert cs.match_screen_trigger(utterance, jv, {}) == expected


# ── 5. Emergency intercept: exact words, still fires ──────────────────────
@pytest.mark.parametrize(
    "utterance", ["i cant breathe", "i can't breathe", "hes having a stroke"]
)
def test_emergency_still_fires(jv, utterance):
    assert cs.detect_emergency(utterance, jv) is True


def test_emergency_not_fired_by_embedded_fragment(jv):
    """'chest pain' etc. must be whole words; a substring must not trigger 999."""
    assert cs.detect_emergency("i want to book a chesterfield appointment", jv) is False


# ── 6. The matcher itself ─────────────────────────────────────────────────
@pytest.mark.parametrize(
    "keyword,text,expected",
    [
        ("red", "no im just really tired", False),
        ("red", "its red and swollen", True),
        ("hot", "i took a photo of it", False),
        ("hot", "its hot to touch", True),
        ("fell", "my fellow runner", False),
        ("fell", "i fell over", True),
        ("back pain", "ive got really bad back pain", True),
        ("back pain", "my back is fine but i have knee pain", False),
    ],
)
def test_kw_in_word_boundary(keyword, text, expected):
    assert cs._kw_in(keyword, cs._norm(text)) is expected


# ── 7. Inflection tolerance — a strict boundary on BOTH ends loses the
#      clinical vocabulary. 'numb' must still reach 'numbness' (the cauda
#      positive) while still being kept out of 'number'.
@pytest.mark.parametrize(
    "keyword,text,expected",
    [
        ("numb", "ive got numbness down there", True),
        ("numb", "no number changes at all", False),
        ("numb", "my foot feels numb", True),
        ("fever", "ive been getting fevers", True),
        ("fever", "no fever at all", True),
        ("crack", "i heard it cracked", True),
        ("operation", "ive had two operations on it", True),
        ("swelling", "there was massive swelling", True),
        ("red", "its gone red", True),
        ("red", "ive recovered since", False),
        ("hot", "its really hot", True),
        ("hot", "i took a photo", False),
    ],
)
def test_kw_in_allows_inflection_but_not_different_words(keyword, text, expected):
    assert cs._kw_in(keyword, cs._norm(text)) is expected


def test_cauda_numbness_still_escalates(jv):
    """The clinical term is 'numbness'; the keyword is 'numb'. This is the
    unbounded-harm miss, so it is asserted on the real config."""
    screen = cs.get_screen(jv, "cauda_equina")
    assert cs.classify_screen_answer("i feel numbness down below", screen) == "red_flag"
