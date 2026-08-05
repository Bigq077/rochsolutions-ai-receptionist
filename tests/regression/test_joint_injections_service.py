"""
Joint injections — Mark's new service, taught to Susie 2026-08-05.

Source: theoremhealth.co.uk/joint-injections. Corticosteroid injections for
hip, shoulder and knee, delivered by Mark (HCPC physiotherapist, non-medical
prescriber and injection therapist) at the Awlstuh clinic.

Three things make this worth a regression net rather than a prompt edit alone.

  1. **The facts have to reach the model.** theorem_v3 does not read
     clinic.json — it runs `_build_theorem_v3`, hardcoded Python. A fact added
     to config, or to a block that is never appended to `static_blocks`, reads
     at runtime as the model failing to know something it was never told.

  2. **An injection can never be the booked appointment.** `_VALID_SERVICES`
     is a hard whitelist of one — "physiotherapy assessment" — and
     check_availability rejects anything else outright. That happens to match
     the clinical pathway (the injection is only given after an assessment),
     but the prompt asserts it in words, and words and whitelist have to stay
     in step. If someone widens the whitelist, the prompt's "the only
     appointment you can book" line silently becomes a lie.

  3. **Susie is not a clinician.** Whether to inject, and where, is Mark's
     judgement at the assessment. The prohibitions below are the whole reason
     this service can be handled by a receptionist at all.
"""

import re

import pytest

from app.media_streams.turn_handler import sanitise_response
from app.prompts.susie_system_prompt import build_system_prompt_parts
from app.tools.receptionist_tools import _VALID_SERVICES

CLINIC_ID = "theorem_v3"


def _prompt() -> str:
    static, dynamic = build_system_prompt_parts({
        "clinic_id": CLINIC_ID,
        "collected": {},
        "soft_context": {},
    })
    return static + "\n" + dynamic


# ── 1. The facts reach the model ────────────────────────────────────────────

def test_the_joint_injection_block_is_actually_rendered():
    """Not just defined — appended to static_blocks and joined into the build."""
    assert "JOINT INJECTIONS" in _prompt(), (
        "the joint_injections block is not in the rendered prompt — check it "
        "is in the static_blocks list, not merely assigned"
    )


@pytest.mark.parametrize("fact", [
    # who and where
    "non-medical prescriber",
    "injection therapist",
    "No GP referral",
    "AWLSTUH ONLY",
    # the three joints and their published conditions
    "osteoarthritis",
    "bursitis",
    "frozen shoulder",
    "rotator-cuff tendonitis",
    "subacromial impingement",
    "trochanteric bursitis",
    # the three published FAQs
    "two to seven days",
    "steroid flare",
    "twenty-four to forty-eight hours",
    "three in a single joint",
    "twelve-month period",
])
def test_published_facts_are_present(fact):
    assert fact in _prompt(), f"{fact!r} did not reach the model"


def test_prices_are_stated_once_in_the_prices_block():
    """One source of truth per number. The injection block explains why £235 is
    the usual total; it does not restate the individual figures."""
    p = _prompt()
    assert "Corticosteroid joint injection: £150" in p
    assert "£235" in p
    block = p[p.index("JOINT INJECTIONS"):]
    block = block[:block.index("\nPOLICIES")]
    assert "£150" not in block, (
        "the injection price is now stated in two places — they will drift"
    )


# ── 2. Booking stays pinned to the assessment ───────────────────────────────

def test_only_the_assessment_is_bookable():
    """The prompt tells Susie the assessment is the only thing she can book.
    That is only true while the whitelist says so."""
    assert _VALID_SERVICES == frozenset({"physiotherapy assessment"}), (
        f"_VALID_SERVICES changed to {set(_VALID_SERVICES)}. The joint "
        "injection block tells Susie the physiotherapy assessment is the only "
        "appointment she can check or book — update that wording too."
    )


def test_prompt_forbids_booking_the_injection_itself():
    p = _prompt()
    assert "Never try to book an injection as the appointment" in p
    assert "physiotherapy assessment" in p


def test_injection_is_in_the_named_treatment_redirect_list():
    """The existing named-treatment contract routes to an assessment. Injections
    must be caught by it, or 'I want a cortisone injection' takes the generic
    booking path instead."""
    p = _prompt()
    named = p[p.index("If the patient mentions any specific treatment"):]
    named = named[:2000]
    for term in ("joint", "steroid", "cortisone", "corticosteroid"):
        assert term in named.lower(), (
            f"{term!r} injection is not in the named-treatment list"
        )


# ── 3. Susie stays a receptionist ───────────────────────────────────────────

@pytest.mark.parametrize("boundary", [
    "Never say whether an injection is right for this caller",
    "Never advise on safety with their medications",
    "Never promise relief or a timescale",
    "Never treat an injection enquiry as a reason to skip",
])
def test_clinical_boundaries_are_stated(boundary):
    assert boundary in _prompt(), (
        f"the boundary {boundary!r} is gone — Susie may give clinical advice "
        "about a medical procedure"
    )


def test_screening_and_urgent_rules_still_apply_to_injection_callers():
    """An injection enquiry is not a bypass. The escalation wording must still
    be in the same prompt."""
    p = _prompt()
    assert re.search(r"\b999\b", p) and "A&E" in p, (
        "the urgent-symptom escalation is gone from the prompt entirely"
    )
    assert "clinical screening" in p.lower()


# ── 4. The taught line must survive the gates ───────────────────────────────

def test_the_injection_redirect_survives_gate5():
    """A mandated line the gates rewrite is a line Susie cannot say — this is
    exactly how T-18 opened seven seconds of dead air."""
    taught = (
        "Joint injections are something Mark does himself at our Awlstuh "
        "clinic — they're always given after an assessment, so he can check "
        "the injection's the right thing for your knee and exactly where it "
        "needs to go. Shall I book you that assessment?"
    )
    assert taught in _prompt(), "the worked example was reworded — update this test"

    session = {"clinic_id": CLINIC_ID, "collected": {}, "soft_context": {}}
    spoken = sanitise_response(taught, session)
    assert "joint injections" in spoken.lower(), (
        f"Gate 5 stripped the injection redirect: {spoken!r}"
    )
    assert "shall i book you that assessment" in spoken.lower(), (
        f"Gate 5 removed the booking question from the redirect: {spoken!r}"
    )
