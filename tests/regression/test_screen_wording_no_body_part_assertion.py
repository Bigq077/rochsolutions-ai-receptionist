# tests/regression/test_screen_wording_no_body_part_assertion.py
"""
A screen must not tell the caller which body part they were talking about.

2026-07-27, live call CA7c945058 (jv_v1): the caller said "my LEG's been swollen
and warm for a couple of days". The DVT screen armed correctly and deterministically
(`arm_paths={'dvt': 'arming_utterance'}`) — that part worked — and then said:

    "CALF symptoms like that need checking urgently to rule out a clot..."

The caller never said calf. `29e3f9b` (2026-07-27) generalised the DVT *trigger* to
the symptom combination ("swollen warm/red/hot") so the screen survives STT mangling
of "calf", which means it now arms on a swollen, warm LEG / ANKLE / THIGH / KNEE —
but the escalation text still asserts "Calf". Widening the trigger made the mismatch
routine rather than rare.

This is the same defect class the DVT `_escalation_note` already records being fixed
once, on 2026-07-24: the text used to assert "A swollen, warm calf like that" at a
caller who had just denied swelling. That fix removed the *symptom* assertion and
left the *body-part* assertion behind.

The other five screens already use non-asserting deixis ("Those particular symptoms",
"those signs", "That pattern of stiffness"). This pins all six, so neither the DVT
wording regresses nor a future edit introduces the same assertion elsewhere.

Config-only (clinic.json). Clinical content — urgency, rule out a clot, NHS 111,
A&E if severe, do not massage, call back — is unchanged and asserted below.
"""
from __future__ import annotations

import re

import pytest

from app.clinic_config import get_clinic
from app.media_streams import clinical_screening as cs

# Body parts a screen must never assert back at the caller. The caller's own words
# are unknown at the point the scripted escalation is spoken — the deterministic
# trigger can fire on any limb — so naming one risks describing a symptom the
# caller never reported.
#
# "back" is deliberately NOT in this list: the escalations legitimately say
# "do call us back once you've been seen", and a bare word-boundary match on
# "back" flags that as an anatomical claim. "neck" is included but exempted for
# vbi_neck below, which is defined by neck pain.
_BODY_PARTS = (
    "calf", "leg", "ankle", "knee", "thigh", "shin", "foot",
    "arm", "wrist", "elbow", "shoulder", "neck", "hip",
)
_PARTS_ALT = "|".join(_BODY_PARTS)

# An ASSERTION about the caller's anatomy, rather than any mention of the word.
# Three shapes, all seen in the defect or its 2026-07-24 predecessor:
#   "…is the calf swollen…"        article/possessive + (adjectives) + part
#   "Calf symptoms like that…"     part used as the subject, sentence-initial
#   "…calf symptoms…"              part qualifying "symptoms"/"pain"
_ASSERTION_RES = (
    re.compile(rf"\b(?:the|your|a|an|that|this)\s+(?:\w+\s+){{0,2}}({_PARTS_ALT})\b"),
    re.compile(rf"^\s*({_PARTS_ALT})\b"),
    re.compile(rf"\b({_PARTS_ALT})\s+(?:symptoms?|pain|swelling)\b"),
)

_SCREEN_IDS = (
    "dvt", "cauda_equina", "serious_spinal",
    "trauma_fracture", "vbi_neck", "inflammatory",
)


@pytest.fixture
def jv():
    return get_clinic("jv_v1")


def _body_parts_in(text: str) -> list[str]:
    """Body parts this text ASSERTS the caller has, deduped and ordered."""
    low = (text or "").lower()
    found: list[str] = []
    for rx in _ASSERTION_RES:
        for m in rx.finditer(low):
            part = m.group(1)
            if part not in found:
                found.append(part)
    return found


@pytest.mark.parametrize("screen_id", _SCREEN_IDS)
def test_escalation_does_not_assert_a_body_part(jv, screen_id):
    """No screen's escalation may name the body part it is escalating about.

    vbi_neck is the deliberate exception: it is *defined* by neck pain, the caller
    has necessarily said so to arm it, and its wording ("Those symptoms alongside
    neck pain") is referential rather than an assertion about the caller.
    """
    screen = cs.get_screen(jv, screen_id)
    if screen is None:
        pytest.skip(f"{screen_id} not configured for jv_v1")
    text = screen.get("escalation", "") or ""
    found = _body_parts_in(text)
    if screen_id == "vbi_neck":
        assert found in ([], ["neck"]), (
            f"vbi_neck may reference 'neck' only; found {found}"
        )
        return
    assert not found, (
        f"{screen_id} escalation asserts body part(s) {found} — the caller may have "
        f"reported a different one. Use non-asserting wording "
        f"('Symptoms like that', 'those signs'). Text: {text!r}"
    )


def test_dvt_escalation_keeps_its_clinical_content(jv):
    """Generalising the wording must not drop any clinical instruction."""
    text = (cs.get_screen(jv, "dvt").get("escalation") or "").lower()
    for required in ("clot", "111", "a&e", "massage"):
        assert required in text, f"DVT escalation lost {required!r}: {text!r}"
    assert "urgent" in text, "DVT escalation lost its urgency framing"


def test_dvt_screen_question_does_not_presume_the_calf(jv):
    """The screen QUESTION has the same exposure as the escalation.

    It is asked when the screen arms without a red flag already volunteered — i.e.
    exactly when the caller has said something like "my leg is swollen". Asking
    "is the calf swollen?" of that caller is the same mismatch.
    """
    q = (cs.get_screen(jv, "dvt").get("screen_question") or "")
    assert "calf" not in q.lower(), (
        f"DVT screen_question presumes the calf; the trigger now arms on any limb. "
        f"Question: {q!r}"
    )
    # The clinical substance of the question must survive the rewording.
    low = q.lower()
    for required in ("swollen", "warm", "red", "surgery"):
        assert required in low, f"DVT screen_question lost {required!r}: {q!r}"


def test_other_five_screens_still_use_non_asserting_openings(jv):
    """Guard the opposite regression: the five that were already correct."""
    expected_openers = {
        "cauda_equina":    "those particular symptoms",
        "serious_spinal":  "because of those signs",
        "trauma_fracture": "with an injury like that",
        "vbi_neck":        "those symptoms",
        "inflammatory":    "that pattern of stiffness",
    }
    for sid, opener in expected_openers.items():
        screen = cs.get_screen(jv, sid)
        if screen is None:
            pytest.skip(f"{sid} not configured")
        text = (screen.get("escalation") or "").lower()
        assert text.startswith(opener), (
            f"{sid} escalation no longer opens with the non-asserting "
            f"{opener!r}: {text[:80]!r}"
        )
