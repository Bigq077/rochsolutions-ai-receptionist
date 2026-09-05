"""
A caller who leads with a condition AND asks to book must be offered a booking.

Live call CAe689cfb5, 2026-08-05 14:36, jv_v1:

    caller: "um yeah hi there i've had knee pain for about 3 weeks um it's
             worse going downstairs can i get booked in please"

    Susie:  "That kind of pain around the kneecap that bites going downstairs,
             and often after sitting for a while too — is a really common
             pattern we see, and it responds well once …
             What's the appointment for —
             Is this the first time you've been seen for the knee, or have you
             been in with us before for it?"

~349 characters, 24 seconds of audio, and the caller hung up. She never offered
to book, despite being asked to.

Both questions she DID ask are explicitly forbidden by this same prompt:

  * the reason — 1b: "If the caller has ALREADY said why they are calling — a
    body part, a symptom, an injury … that IS the reason: do NOT ask again."
  * new-vs-returning — "HARD RULE — NEW/RETURNING QUESTION IS PERMANENTLY
    BANNED FROM THIS ENTIRE FLOW."

She broke both because no rule fitted the turn. Two blocks instructed it:

    BOOKING STEPS 1   → "'Right —' and NOTHING ELSE … no question"
    CONDITION FLUENCY → answer, give the pathway, "offer the best-fit service"

and because she said neither scripted thing, `_is_booking_ack` never matched,
connection.py injected no follow-up question (no `booking_flow_active = True`
in that call's log), and she improvised. Same root shape as T-18.

The fix gives step 1 an explicit branch for that opening. These tests pin the
branch, the two prohibitions it restates, and — the part that matters for a
live clinic — that it reaches ONLY clinics with a condition library.
"""

import pytest

from app.clinic_config import get_clinic
from app.prompts.clinic_template_prompt import build_clinic_prompt
from tests.screening_fixture import screening_clinic

MARKER = "THE CALLER LED WITH A CONDITION"


def _prompt(clinic_id: str, clinic=None) -> str:
    static, dynamic = build_clinic_prompt(
        {"clinic_id": clinic_id, "collected": {}, "soft_context": {}},
        clinic if clinic is not None else get_clinic(clinic_id),
    )
    return static + "\n" + dynamic


def _has_condition_library(clinic_id: str) -> bool:
    return bool((get_clinic(clinic_id).get("condition_knowledge") or {}).get("conditions"))


# ── the branch exists and says the right thing ──────────────────────────────

def test_jv_v1_has_the_condition_led_branch():
    assert _has_condition_library("jv_v1"), "jv_v1 lost its condition library"
    assert MARKER in _prompt("jv_v1")


def test_the_branch_overrides_the_bare_ack():
    """Without this the model is told to say 'Right —' and nothing else, which
    is what left a booking request unanswered."""
    block = _block("jv_v1")
    assert "overrides the 'Right —' instruction" in block
    assert "do NOT say 'Right —'" in block


def test_the_branch_requires_a_booking_offer():
    """The whole point: the caller asked to be booked in."""
    block = _block("jv_v1")
    assert "booking offer" in block
    assert "Shall I get you booked in with Marcus for an assessment?" in block, (
        "the worked example lost the practitioner's name or the offer itself"
    )


def test_the_branch_restates_both_prohibitions_she_broke():
    block = _block("jv_v1")
    assert "Do NOT ask what the appointment is for" in block
    assert "Do NOT ask whether they have been seen here before" in block
    assert "Do NOT ask two questions" in block


def test_safety_screening_still_outranks_the_offer():
    """A red-flag presentation must never be answered with 'shall I book you in'.

    Asserted against a screens-ON fixture: no live clinic screens since
    2026-09-05, but the ordering rule must stay correct for any clinic that
    switches them back on.
    """
    block = _block("jv_v1", screening_clinic("jv_v1"))
    assert "the screen comes first and replaces the booking offer" in block


def test_the_turn_is_bounded_in_length():
    """24 seconds is what made the caller hang up. The branch has to say so."""
    block = _block("jv_v1")
    assert "ONE short turn" in block
    assert "one or two sentences" in block


# ── containment: clinics without a condition library are untouched ──────────

@pytest.mark.parametrize("clinic_id", ["vital_edge"])
def test_clinics_without_a_condition_library_never_see_it(clinic_id):
    """vital_edge is live and ships zero condition entries. It shares BOOKING
    STEPS with jv_v1, so an ungated edit would have changed its prompt for a
    branch it can never take. The gate is what makes this change safe to ship
    to a clinic branch."""
    assert not _has_condition_library(clinic_id)
    assert MARKER not in _prompt(clinic_id), (
        f"{clinic_id} has no condition library but received the condition-led "
        "branch — the _has_fluency gate has been lost"
    )


def test_the_gate_is_the_condition_library_not_a_clinic_id():
    """Hardcoding 'jv_v1' here would strand the next clinic that ships a
    library. The gate must be the data, not the name."""
    import inspect

    from app.prompts import clinic_template_prompt as tpl

    src = inspect.getsource(tpl)
    assert "_step1_condition_led" in src
    idx = src.index('_step1_condition_led = ""')
    window = src[max(0, idx - 200):idx + 200]
    assert "_has_fluency" in window, (
        "the condition-led branch is no longer gated on _has_fluency"
    )
    assert '"jv_v1"' not in window, "the branch was gated on a clinic id"


def _block(clinic_id: str, clinic=None) -> str:
    p = _prompt(clinic_id, clinic)
    start = p.index(MARKER)
    return p[start:p.index("EXCEPTION — BOOKING FLOW ALREADY ACTIVE", start)]
