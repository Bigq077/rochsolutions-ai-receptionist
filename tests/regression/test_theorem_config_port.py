"""Item 2 of THEOREM_PORT_PLAN — clinic_config.py, pinned.

Three separate things are guarded here, and they fail for different reasons.

1. PRICES. `latency-eval` carried Theorem prices that were six weeks stale
   (£75 assessments, £120 standalone shockwave, £420 packages). `main` and
   `canonical.py` agreed with each other; only this branch's config was wrong.
   The `_CANONICAL_PRICE_MAP` loop now overwrites the `price_gbp` literals from
   `canonical.py` at import, so the literals cannot drift again. These tests
   assert the RESULT of that sync.

   The expected values below are written as literals ON PURPOSE. Importing them
   from canonical.py would make the test agree with itself after a bad edit to
   canonical.py — which is the exact failure this is meant to catch.

2. REDDITCH. `bookable` is the single flag that drives both the prompt's
   redirect block and the guard in llm_stream.py. If it silently flips True,
   Susie starts booking a site that Mark handles himself.

3. CLINICAL SCREENING. Mark does not want it. `screening_enabled()` is opt-in —
   it requires a `clinical_screening` block in the clinic dict. Theorem has
   none, so screening is off by ABSENCE. An absence is easy to undo by
   accident, which is why it is pinned here rather than left as a convention.
"""
import pytest

from app.clinic_config import (
    CLINICS,
    THEOREM_APPOINTMENT_TYPES,
    THEOREM_LOCATIONS,
    get_clinic,
)
from app.media_streams.clinical_screening import screening_enabled


# ── 1. prices ─────────────────────────────────────────────────────────────

# Deliberately duplicated from canonical.py rather than imported. See docstring.
EXPECTED_PRICES = {
    "physio_assessment": 85.0,
    "physio_followup":   85.0,
    "remedial_rehab":    65.0,
    "rehab_pt":          65.0,
    "prescribing":       12.50,
    "acupuncture":       85.0,
    "psychotherapy":     85.0,
}


@pytest.mark.parametrize("apt_id,expected", sorted(EXPECTED_PRICES.items()))
def test_appointment_type_prices_match_canonical(apt_id, expected):
    assert THEOREM_APPOINTMENT_TYPES[apt_id]["price_gbp"] == expected


def test_the_prices_that_are_not_85_are_left_alone():
    """Guards the direction of the fix. Theorem is NOT a flat-£85 clinic —
    rehab is £65 and a prescribing consult is £12.50. Flattening everything to
    £85 would overcharge a prescribing caller nearly sevenfold."""
    assert THEOREM_APPOINTMENT_TYPES["remedial_rehab"]["price_gbp"] == 65.0
    assert THEOREM_APPOINTMENT_TYPES["prescribing"]["price_gbp"] == 12.50


def test_pricing_details_block_matches_canonical():
    """A second, independent price table on the same clinic. It is NOT covered
    by _CANONICAL_PRICE_MAP — these four were stale after the loop landed and
    had to be fixed by hand, so they need their own pin."""
    pd = CLINICS["theorem"]["pricing_details"]
    assert pd["new_patient_assessment_gbp"] == 85.0
    assert pd["standard_followup_gbp"] == 85.0
    assert pd["standalone_shockwave_laser_gbp"] == 130.0
    assert pd["package_4x_shockwave_laser_gbp"] == 468.0
    assert pd["rehab_session_gbp"] == 65.0
    assert pd["prescribing_gbp"] == 12.50
    assert pd["specialist_equipment_surcharge_gbp"] == 45.0


def test_no_stale_75_anywhere_in_theorem_prices():
    """The specific regression. £75 was the wrong number in six places."""
    stale = [
        k for k, v in THEOREM_APPOINTMENT_TYPES.items()
        if v.get("price_gbp") == 75.0
    ]
    assert not stale, f"stale £75 price still present on: {stale}"


# ── 2. Redditch ───────────────────────────────────────────────────────────


def test_redditch_is_not_bookable():
    assert THEOREM_LOCATIONS["redditch"]["bookable"] is False


def test_alcester_is_bookable():
    """Explicit rather than defaulted — a guard reading .get('bookable') with a
    falsy default would silently close the whole clinic."""
    assert THEOREM_LOCATIONS["alcester"]["bookable"] is True


def test_both_locations_state_bookable_explicitly():
    for loc_id, loc in THEOREM_LOCATIONS.items():
        assert "bookable" in loc, f"{loc_id} has no explicit bookable flag"


# ── 3. clinical screening stays OFF ───────────────────────────────────────


@pytest.mark.parametrize("clinic_id", ["theorem", "theorem_v2", "theorem_v3"])
def test_clinical_screening_is_off_for_theorem(clinic_id):
    """Mark's decision: fastest-possible booking, no clinical triage.

    If this fails, someone added a clinical_screening block. That is a clinical
    scope change and a client decision — it is not a code review comment. Do not
    "fix" this test."""
    assert not screening_enabled(get_clinic(clinic_id))


def test_theorem_screening_block_is_emergency_only():
    """Belt and braces, re-aimed 2026-08-29 — same danger, named directly.

    This asserted that no clinical_screening block existed at all, because
    screening was off by ABSENCE and "a block could be added disabled and then
    flipped on in a one-word diff".

    A block exists now. Mark agreed to a deterministic emergency response — his
    prompt already told Susie to say "call 999" and this only makes it fire
    reliably — on condition it adds no question for someone booking. It does
    not: no `screens` means nothing can arm.

    Absence has therefore stopped being the mechanism, but the thing it
    protected has not gone away, so the two keys that would turn triage on are
    now forbidden BY NAME. That is stricter than the old check, not looser: it
    also rules out `screens` being added while `enabled` stays off, which the
    absence check could not distinguish from a legitimate emergency block.

    Still a client decision. Do not "fix" this test — ask Mark.
    """
    block = CLINICS["theorem"].get("clinical_screening") or {}
    assert "enabled" not in block, (
        "someone enabled proactive screening for Theorem — that is triage, and "
        "Mark declined it")
    assert "screens" not in block, (
        "someone added proactive screens for Theorem — each one is a question "
        "asked before booking, which is exactly what he said no to")
    assert (block.get("emergency_red_flags") or {}).get("keywords"), (
        "the emergency intercept lost its keywords — Theorem would be back to "
        "relying on the model noticing a caller describing chest pain")
