"""A minimum age nobody asks about is a gate that never arms.

`under_age_blocks_booking` only fires once `capture_under_age` has latched an
age, and `capture_under_age` only reads an age the caller has SAID. Nothing was
prompting them to say one.

Observed on Theorem, 2026-08-25 (CAd48ea4e1315c26d17023287fbdb97773 and
CA6d41a6fea6ecf2a9a1a2326cbd98c76e): a parent described their son's ankle
injury and Susie went straight into booking, never establishing his age. The
gate had been armed at 7 that morning and would not have fired.

Theorem got its own copy of the ask, in `_build_theorem_v3`. The template
clinics render `clinic_template_prompt` instead, so the fix did not reach them
and Vital Edge — minimum 18 — had exactly the same hole.

SCOPE IS THE POINT. The ask is gated on the clinic actually having a
`minimum_age_years`, so it renders for vital_edge and not for jv_v1, whose
stated policy is the opposite: "No minimum age — discounts available for under
18". Asking a jv_v1 parent their child's age would be friction in service of a
rule that clinic does not have, and this file pins that it does not happen.
"""

import pytest

from app.clinic_config import get_clinic
from app.prompts.clinic_template_prompt import build_clinic_prompt
from app.tools.receptionist_tools import minimum_age_years

MARKER = "ESTABLISH THE AGE"


def _prompt(clinic_id: str) -> str:
    static, dynamic = build_clinic_prompt(
        {"clinic_id": clinic_id}, get_clinic(clinic_id)
    )
    return static + dynamic


# ── it renders exactly where a minimum exists ──────────────────────────────

def test_a_clinic_with_a_minimum_age_is_told_to_ask():
    assert minimum_age_years(get_clinic("vital_edge")) is not None
    assert MARKER in _prompt("vital_edge"), (
        "vital_edge has a minimum age of 18 but Susie is never told to "
        "establish it — the under-age gate can then only arm by luck"
    )


def test_a_clinic_with_no_minimum_age_is_not_told_to_ask():
    """jv_v1's policy is 'No minimum age — discounts available for under 18'.

    Asking a parent their child's age there is pure friction, and it would also
    imply a restriction the clinic does not have.
    """
    assert minimum_age_years(get_clinic("jv_v1")) is None
    assert MARKER not in _prompt("jv_v1")


@pytest.mark.parametrize("clinic_id", ["vital_edge", "jv_v1"])
def test_the_ask_tracks_the_config_not_the_clinic_name(clinic_id):
    """The rule must be driven by minimum_age_years alone. Anything keyed on a
    clinic id silently excludes the next clinic that switches a minimum on."""
    expected = minimum_age_years(get_clinic(clinic_id)) is not None
    assert (MARKER in _prompt(clinic_id)) is expected


# ── what it must not say ───────────────────────────────────────────────────

def test_the_ask_does_not_quote_the_threshold():
    """Susie asks the age; she does not announce the minimum first.

    Leading with "we only see over-18s" turns an ordinary question into a
    refusal forming, and on Theorem that exact shape lost a caller who rang off
    (CA750c8d70d2ecab156fc87540749fc863). The number belongs in the decline,
    which CALL STATE supplies once an age is actually known.
    """
    prompt = _prompt("vital_edge")
    para = prompt[prompt.index(MARKER):prompt.index(MARKER) + 900]
    assert "18" not in para, (
        "the ask quotes the minimum age; it should ask the question and let "
        "CALL STATE handle the decline"
    )


def test_the_ask_names_the_child_words_the_detector_uses():
    """The prompt cue and the engine detector must agree on what a child
    reference looks like, or Susie asks in cases the gate cannot act on."""
    para = _prompt("vital_edge")
    for word in ("son", "daughter", "child", "grandson", "granddaughter"):
        assert word in para, f"{word!r} missing from the ask rule"


def test_the_ask_defers_the_decline_to_call_state():
    para = _prompt("vital_edge")
    assert "CALL STATE" in para[para.index(MARKER):para.index(MARKER) + 900], (
        "the ask must point at CALL STATE for the decline, so the refusal "
        "keeps deriving the clinic's own minimum rather than being improvised"
    )
