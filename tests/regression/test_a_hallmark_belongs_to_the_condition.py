"""Susie must not report symptoms the caller never described.

northgate CAeedecabfd099658cb5d486d5fbd63d7a, 9 Sep 2026, judge 2, tag
`hallucination`, caller abandoned:

    caller : "um just for the left ankle and achilles tendons"
    Susie  : "Oh, sorry to hear that -- that kind of stiffness and soreness in
              the Achilles, especially that first-few-minutes-in-the-morning
              feeling, responds really well once it's properly assessed."

The caller named a body part. Susie reported stiffness, soreness and morning
symptoms -- three clinical findings nobody gave her, on a physiotherapy line, in
a register that sounds like assessment.

NOT invention in the ordinary sense: `_render_condition_fluency` deliberately
teaches hallmark features so Susie sounds specialist instead of saying "that's
very common", and that block is worth keeping. Its docstring already stated the
right rule -- "the caller is never told what THEY have" -- but nothing in the
RENDERED text enforced it, and "woven together with THEIR specifics" reads as an
invitation to attribute. "That kind of stiffness" presupposes stiffness was
mentioned; when the caller named only a body part, it was not.

These tests pin the rule into the rendered prompt, and pin its SCOPE -- the two
clinics that carry `condition_knowledge`, and no others.
"""
import pytest

from app.clinic_config import get_clinic
from app.prompts.clinic_template_prompt import build_clinic_prompt


def _prompt(clinic_id: str) -> str:
    clinic = get_clinic(clinic_id)
    a, b = build_clinic_prompt({"clinic_id": clinic_id}, clinic)
    return (a or "") + (b or "")


# ── the rule reaches the model ───────────────────────────────────────────────
@pytest.mark.parametrize("clinic_id", ["northgate", "jv_v1"])
def test_the_rule_is_in_the_rendered_prompt(clinic_id):
    """Rendered, not merely present in the source.

    A fact in clinic.json with no renderer branch reads as a model failure --
    this codebase has been caught by that three times. So this asserts the
    string the MODEL sees.
    """
    text = _prompt(clinic_id)
    assert "WHOSE SYMPTOM IS IT" in text
    assert "A hallmark belongs to the CONDITION, never to" in text
    assert "PRESUPPOSES" in text


@pytest.mark.parametrize("clinic_id", ["northgate", "jv_v1"])
def test_the_fall_through_no_longer_invites_feature_listing(clinic_id):
    """The tail for a condition ABSENT from the library said "acknowledge its
    recognised features specifically" -- the same invitation one layer down."""
    text = _prompt(clinic_id)
    assert "acknowledge its recognised features" not in text, (
        "the fall-through still invites listing features at the caller"
    )
    assert "not as symptoms this caller reported" in text


@pytest.mark.parametrize("clinic_id", ["northgate", "jv_v1"])
def test_the_exhibit_is_carried_so_the_rule_is_not_re_softened(clinic_id):
    """The failing sentence itself is in the prompt.

    Rules in this codebase get re-softened when the call behind them is lost.
    """
    assert "first-few-minutes-in-the-morning" in _prompt(clinic_id)


# ── and reaches NOBODY else ──────────────────────────────────────────────────
def test_only_the_condition_knowledge_clinics_are_touched():
    """Vital Edge ships `treatment_guidance` and gets bespoke knowledge instead;
    theorem/theorem_v3 use a different builder entirely. A prompt rule that
    leaks physiotherapy vocabulary onto a clinic that did not ask for it is the
    defect this scoping exists to prevent."""
    for clinic_id in ("vital_edge",):
        assert "WHOSE SYMPTOM IS IT" not in _prompt(clinic_id)


def test_the_block_it_lives_in_still_renders_only_where_expected():
    """`condition_knowledge` is the switch. If a clinic gains the block the
    rule follows it automatically; if one loses it, this test says so."""
    for clinic_id in ("northgate", "jv_v1"):
        conds = (get_clinic(clinic_id).get("condition_knowledge") or {}).get("conditions") or []
        assert conds, f"{clinic_id} lost condition_knowledge -- the rule no longer renders"
    for clinic_id in ("vital_edge", "demo", "theorem"):
        conds = (get_clinic(clinic_id).get("condition_knowledge") or {}).get("conditions") or []
        assert not conds, f"{clinic_id} gained condition_knowledge -- re-check the scope test above"


def test_the_valuable_half_of_the_block_survives():
    """The fix must not turn specialist knowledge back into "that's very common".

    That generic reply is what the block was built to stop, and losing it would
    be a worse regression than the one being fixed.
    """
    text = _prompt("northgate")
    assert "CONDITION LIBRARY" in text
    assert "Banned as a complete answer: 'that's very common'" in text
    assert "hallmark features" in text
