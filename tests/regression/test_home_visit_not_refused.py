"""Vital Edge acceptance run 2026-08-04, call 12 (CA64edbf41) — found while
triaging it, and worse than the row itself.

The row read "SOFT FAIL (promise)": Susie said a Surbiton home visit "should be
fine". That half is closed — the Jonathan-confirms caveat is mandated in every
home-visit block and in the FAQ answer, and NOT asking for an address is
explicitly instructed ("Jonathan sorts the location out on the callback").

What the triage found is that the prompt CONTRADICTED ITSELF on every call:

  step 2 (engine)  "There is NO remote, video, or phone appointment option and
                    NO home visit — never offer one ... say plainly that all
                    sessions are in person at the Kingston clinic"
  HOME VISITS      "do NOT refuse and do NOT divert them to a callback — take
  (clinic.json)     it as a NORMAL booking"

Home visits were riding on the REMOTE flag, which is a different axis. Vital
Edge has no video and does do home visits, so it landed in the branch that
refuses them. Call 12 happened to go the permissive way; the refusing way loses
a caller the clinic wants, and nothing made it deterministic.

Both template_v1 clinics do home visits (owner-confirmed 2026-08-04: Marcus
anywhere around Greater Manchester, Jonathan if not too far, subject to
confirmation), so both were carrying the contradiction. jv_v1 sells a named
Home Visit service at £80 and was still told "never offer one".
"""

import pytest

from app.prompts.clinic_template_prompt import (
    _home_visits_enabled,
    _home_visits_offered,
)
from app.prompts.susie_system_prompt import build_system_prompt_parts

REFUSAL_MARKERS = (
    "no home visit",
    "no home-visit",
    "there is no home-visit option",
)


def _prompt(clinic_id):
    static, dynamic = build_system_prompt_parts({
        "call_sid": "CAtest_homevisit",
        "clinic_id": clinic_id,
        "booking_flow_active": True,
        "collected": {},
    })
    return f"{static}\n{dynamic}"


# --------------------------------------------------------------------------
# The contradiction, on both clinics that carried it.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("clinic_id", ["vital_edge", "jv_v1"])
def test_a_home_visit_clinic_is_never_told_it_has_no_home_visits(clinic_id):
    low = _prompt(clinic_id).lower()
    for marker in REFUSAL_MARKERS:
        assert marker not in low, f"{clinic_id} is still told: {marker!r}"


@pytest.mark.parametrize("clinic_id", ["vital_edge", "jv_v1"])
def test_a_home_visit_clinic_is_never_told_to_refuse_one(clinic_id):
    low = _prompt(clinic_id).lower()
    assert "or a home visit, say" not in low
    assert "appointment or a home visit" not in low


def test_vital_edge_keeps_its_home_visit_instructions():
    """Removing the refusal must not disturb the block that says what to do
    instead — take the booking, note it, Jonathan confirms."""
    low = _prompt("vital_edge").lower()
    assert "home visit requested" in low
    assert "confirms whether he can come to you" in low
    assert "do not ask for the caller's address or postcode" in low


# --------------------------------------------------------------------------
# The remote denial is a SEPARATE axis and must survive untouched. Vital Edge
# genuinely has no video/phone option; losing this would trade one false
# promise for another.
# --------------------------------------------------------------------------


def test_vital_edge_still_refuses_remote_appointments():
    low = _prompt("vital_edge").lower()
    assert "no remote, video, or phone appointment option" in low
    assert "never say we offer video or phone consultations" in low
    assert "all sessions are in person" in low


def test_jv_v1_still_offers_remote_appointments():
    """jv_v1 DOES do video/phone — the fix must not make it deny them."""
    low = _prompt("jv_v1").lower()
    assert "we offer video and phone consultations" in low


# --------------------------------------------------------------------------
# The predicate, on synthetic config. No live clinic currently exercises the
# refusal branch (theorem/demo use a different prompt engine and never render
# step 2), so this is the only place the OFF direction is actually tested —
# and it must keep working for a future in-clinic-only clinic.
# --------------------------------------------------------------------------


def test_a_clinic_with_no_home_visit_signal_is_still_refused():
    assert _home_visits_offered({"prompt_facts": {}, "services": []}) is False
    assert _home_visits_offered({}) is False


@pytest.mark.parametrize(
    "clinic",
    [
        {"modalities": ["home_visit"]},
        {"services": [{"service_id": "home_visit"}]},
        {"services": [{"pricing": {"home_visit_gbp": 80}}]},
    ],
)
def test_structured_declarations_offer_home_visits(clinic):
    assert _home_visits_offered(clinic) is True
    assert _home_visits_enabled(clinic) is True


@pytest.mark.parametrize(
    "prompt_facts",
    [
        {"home_visit_note": "Jonathan can often come to you."},
        {"home_visit_area": "Bolton and Greater Manchester"},
    ],
)
def test_a_prose_declaration_offers_home_visits_but_not_pricing(prompt_facts):
    """The two predicates deliberately disagree here. Prose is enough to stop a
    REFUSAL; it is not enough to switch on the advisory block, which asserts a
    coverage area, a travel charge and how the address is taken — none of which
    Vital Edge has confirmed."""
    clinic = {"prompt_facts": prompt_facts}
    assert _home_visits_offered(clinic) is True
    assert _home_visits_enabled(clinic) is False


def test_vital_edge_does_not_gain_the_unverified_advisory_block():
    """Guards the side effect found while fixing this: widening the PRICING
    predicate instead would have switched on 'FIRST APPOINTMENT & HOME VISITS'
    and told callers their address is taken by text after booking — a process
    nobody has confirmed with Jonathan."""
    assert _home_visits_enabled(  # the pricing/advisory signal
        {"prompt_facts": {"home_visit_note": "x"}}
    ) is False
    assert "taken by text after booking" not in _prompt("vital_edge").lower()


def test_non_dict_services_do_not_crash_the_predicate():
    """theorem and demo carry `services` as a list of plain strings."""
    assert _home_visits_offered({"services": ["massage", "physio"]}) is False
