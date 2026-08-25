"""The refusal must quote the clinic's OWN minimum age, never a literal.

Both halves of the under-age gate wrote "18" into the sentence:

    llm_stream._execute_tools     "Say kindly that appointments are for those
                                   aged 18 and over"
    clinic_template_prompt        "UNDER this clinic's minimum age of 18 ...
      _b7_call_state               appointments are for those aged 18 and over"

That was true of the only clinic with the gate switched on (Vital Edge, 18) and
became a lie the moment a second clinic switched it on with a different number:
Susie would refuse correctly and then quote a policy the clinic does not have.
Theorem's minimum is 7 — a caller's twelve-year-old would have been refused with
"appointments are for those aged 18 and over", which is wrong twice over,
because at 12 they should not have been refused at all.

Second defect fixed here. `_b7_call_state` gated on
`pricing_and_policies.minimum_age_years` alone, while the engine helper
`minimum_age_years()` reads that shape AND the flat top-level one used by
clinics whose contract comes from `clinic_config.CLINICS` rather than a
clinic.json. Two readers of one policy disagreeing about where it lives is how
a safeguarding gate arms in the engine and stays silent in the prompt — the
caller is refused at the write with no explanation having been set up.
"""

import inspect

from app.clinic_config import get_clinic
from app.prompts.clinic_template_prompt import build_clinic_prompt
from app.tools.receptionist_tools import minimum_age_years

VE = "vital_edge"


def _state_for(clinic: dict, age: int) -> str:
    _, dyn = build_clinic_prompt(
        {"clinic_id": clinic.get("clinic_id", "x"), "_under_age_declared": age},
        clinic,
    )
    return dyn


# ── the number is read, not written ────────────────────────────────────────

def test_call_state_quotes_a_seven_plus_clinics_own_minimum():
    dyn = _state_for({"clinic_id": "t", "minimum_age_years": 7}, 5)
    assert "minimum age of 7" in dyn
    assert "aged 7 and over" in dyn
    assert "18" not in dyn.split("minimum age of")[1][:120], (
        "the refusal still names 18 for a clinic whose minimum is 7"
    )


def test_call_state_still_quotes_18_for_vital_edge():
    """The clinic that already had the gate must render byte-compatibly."""
    assert minimum_age_years(get_clinic(VE)) == 18
    dyn = _state_for(get_clinic(VE), 15)
    assert "minimum age of 18" in dyn
    assert "aged 18 and over" in dyn


def test_the_flat_config_shape_arms_the_prompt_half_too():
    """A clinic whose contract comes from clinic_config.CLINICS carries the key
    at top level, not under pricing_and_policies. The engine helper reads both;
    the prompt used to read only one, so the write gate would refuse a caller
    the CALL STATE had never been told about."""
    flat = {"clinic_id": "t", "minimum_age_years": 7}
    nested = {"clinic_id": "t", "pricing_and_policies": {"minimum_age_years": 7}}
    assert minimum_age_years(flat) == minimum_age_years(nested) == 7
    for clinic in (flat, nested):
        assert "UNDER this clinic's minimum age of 7" in _state_for(clinic, 5)


def test_a_clinic_with_no_policy_still_renders_nothing():
    assert "UNDER this clinic" not in _state_for({"clinic_id": "t"}, 15)


# ── the write-gate refusal ─────────────────────────────────────────────────

def test_the_write_refusal_does_not_hardcode_an_age():
    """Source-level, because the message is composed inside `_execute_tools`
    and cannot be reached without driving a whole tool loop. Narrow on purpose:
    it asserts the literal is GONE and the derivation is present, so deleting
    the derivation fails even if some other '18' appears in the file."""
    from app.media_streams import llm_stream
    src = inspect.getsource(llm_stream)
    assert "aged 18 and over" not in src, (
        "the write-gate refusal still hardcodes 18 — it will quote the wrong "
        "policy at every clinic whose minimum is not 18"
    )
    assert 'f"appointments are for those aged {_min} and over"' in src, (
        "the refusal no longer derives the minimum from the clinic contract"
    )


def test_the_template_refusal_does_not_hardcode_an_age():
    from app.prompts import clinic_template_prompt
    src = inspect.getsource(clinic_template_prompt)
    assert "minimum age of 18" not in src
    assert "those aged 18 and over" not in src
