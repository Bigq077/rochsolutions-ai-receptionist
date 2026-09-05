# tests/regression/test_a_screen_question_is_phrased_so_yes_means_concerning.py
"""
A safety screen must be phrased so that "yes" is the CONCERNING answer.

classify_screen_answer is single-polarity by construction: an affirmative lead
is red_flag, a negative is clear. That is not a defect — it is the only thing a
keyword grader can do without parsing the question. It does mean the polarity
of the QUESTION is load-bearing, and nothing enforced it.

trauma_fracture was inverted, live on jv_v1, until 2026-08-21. It asked:

    "...are you able to use it or put weight through it, and is there any
     marked swelling or does it look out of shape at all?"

The first limb makes "yes" the REASSURING answer, the second makes it the
concerning one. With one grader and two polarities, both directions failed:

  "yes I can put weight through it"          -> red_flag  (needless A&E referral)
  "yeah I can walk on it fine"               -> red_flag  (needless A&E referral)
  "no I can't put any weight on it at all"   -> clear     (fracture booked in)
  "no it's swollen up massively"             -> clear     (fracture booked in)

The false CLEARS are the dangerous half: the call sounds perfect, the screen
records itself as completed, booking is never frozen, and someone with a
possible fracture is booked for hands-on physio. That is the exact failure mode
CLAUDE.md names as the worst in this system.

Fixed config-only, by flipping the first limb to "is it too painful to use it or
put your weight through it". The MARK_REVIEW constraint on the original wording
still holds and is pinned below: it must stay right for BOTH upper and lower
limb injuries, because a wrist caller got the weight-bearing-only version on
Call-2 (2026-07-20).

This file pins the specific defect AND the general class, so an inverted screen
cannot be introduced on any of the six.
"""
from __future__ import annotations

import pytest

from app.clinic_config import get_clinic
from app.media_streams.clinical_screening import classify_screen_answer, get_screen
from tests.screening_fixture import screening_clinic

_SCREEN_IDS = (
    "cauda_equina", "dvt", "serious_spinal",
    "trauma_fracture", "vbi_neck", "inflammatory",
)

# Constructions that make "yes" the REASSURING answer. A screen question
# containing one of these is inverted with respect to the grader.
#
# Each is a capability question ("can you still...?"). The safe way to ask the
# same clinical thing is to ask about the SYMPTOM instead ("is it too painful
# to...?"), which puts the concerning answer back on "yes".
_INVERTING_CONSTRUCTIONS = (
    "are you able to",
    "are you still able",
    "can you still",
    "can you use",
    "can you put weight",
    "can you walk on",
    "is it ok to",
    "does it feel fine",
)

_QUESTION_FIELDS = (
    "screen_question",
    "screen_reask_question",
    "screen_probe_question",
)


@pytest.fixture
def jv():
    return screening_clinic()


def _screen(jv, sid):
    screen = get_screen(jv, sid)
    if screen is None:
        pytest.skip(f"{sid} not configured for jv_v1")
    return screen


# ── the general class ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("screen_id", _SCREEN_IDS)
@pytest.mark.parametrize("field", _QUESTION_FIELDS)
def test_no_screen_question_is_phrased_so_yes_is_reassuring(jv, screen_id, field):
    text = (_screen(jv, screen_id).get(field) or "").lower()
    if not text:
        return
    for bad in _INVERTING_CONSTRUCTIONS:
        assert bad not in text, (
            f"{screen_id}.{field} asks a capability question ({bad!r}), which makes "
            f'"yes" the reassuring answer. classify_screen_answer grades an '
            f'affirmative as red_flag, so this inverts the screen: the caller who '
            f"is fine gets sent to A&E and the caller who is not gets booked in. "
            f"Ask about the symptom instead. Text: {text!r}"
        )


@pytest.mark.parametrize("screen_id", _SCREEN_IDS)
def test_a_bare_yes_escalates_and_a_bare_no_clears(jv, screen_id):
    """The invariant the wording rule exists to protect."""
    screen = _screen(jv, screen_id)
    assert classify_screen_answer("yes", screen) == "red_flag"
    assert classify_screen_answer("no", screen) == "clear"


# ── the specific defect ───────────────────────────────────────────────────────

# Answers that mean THE CALLER IS FINE. Every one of these must clear.
_TRAUMA_REASSURING = (
    "no",
    "no it's fine",
    "no nothing like that",
    "yeah I can walk on it fine, no swelling",
    "no not too painful, I can use it",
)

# Answers that mean POSSIBLE FRACTURE. Every one of these must escalate.
# The four marked (was clear) silently booked the caller in before the fix.
_TRAUMA_CONCERNING = (
    "yes",
    "yes it is",
    "yeah it's too painful to stand on",
    "no I can't put any weight on it at all",   # was clear
    "it's swollen up massively",                # was clear
    "yes it looks out of shape",
    "no, I can't walk on it",
    "I can't stand on it",                      # was clear
    "yes I can't put any weight on it at all",  # was clear
)


@pytest.mark.parametrize("answer", _TRAUMA_REASSURING)
def test_trauma_screen_clears_a_caller_who_is_fine(jv, answer):
    assert classify_screen_answer(answer, _screen(jv, "trauma_fracture")) == "clear", (
        f"{answer!r} means the caller is fine but did not clear the screen — "
        f"this sends them to A&E for an X-ray they do not need"
    )


@pytest.mark.parametrize("answer", _TRAUMA_CONCERNING)
def test_trauma_screen_escalates_a_possible_fracture(jv, answer):
    assert classify_screen_answer(answer, _screen(jv, "trauma_fracture")) == "red_flag", (
        f"{answer!r} describes a possible fracture but did not flag — the call "
        f"sounds perfect, the screen records as completed, booking is never "
        f"frozen, and the caller is booked in for hands-on physio"
    )


def test_trauma_question_still_covers_upper_and_lower_limb(jv):
    """The MARK_REVIEW constraint on the original wording.

    Call-2 (2026-07-20): a wrist caller was asked the weight-bearing-only
    version, which sounded wrong. Fixing the polarity must not undo that.
    """
    for field in ("screen_question", "screen_reask_question"):
        q = (_screen(jv, "trauma_fracture").get(field) or "").lower()
        assert "use it" in q, (
            f"trauma_fracture.{field} lost the upper-limb limb ('use it'), so a "
            f"wrist or shoulder caller is asked only about weight-bearing: {q!r}"
        )
        assert "weight" in q, f"trauma_fracture.{field} lost the lower-limb limb: {q!r}"


def test_trauma_question_keeps_its_clinical_substance(jv):
    """Rewording for polarity must not drop what the screen actually asks about."""
    q = (_screen(jv, "trauma_fracture")["screen_question"]).lower()
    for required in ("swelling", "out of shape"):
        assert required in q, f"trauma_fracture screen_question lost {required!r}: {q!r}"
