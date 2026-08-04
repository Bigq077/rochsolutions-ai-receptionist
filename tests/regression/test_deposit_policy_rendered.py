"""Vital Edge acceptance run 2026-08-04, call 5 (CA848598c8), build 4f4803e.

Caller interrogated pricing. Susie answered every fee correctly and then
DEFERRED the deposit question to Jonathan — "FAIL (deposit)" on the sheet —
even though vital_edge/clinic.json says:

    "deposit_required": "No deposit or booking fee"

She was obeying the prompt. Two independent faults in _render_policies:

  1. deposit_required was never rendered AT ALL. No branch emitted it, so a
     clinic that had settled the question still could not answer it.

  2. The UNCONFIRMED-POLICIES block hardcoded "(e.g. whether a deposit is
     required)" as its worked example. That is CORRECT for jv_v1, whose
     deposit_required really is "TBC with Marcus" — the sentence was written
     for that clinic — but Vital Edge inherited it verbatim and was actively
     instructed to defer the one policy it had an answer for.

So the model was told to defer, and given nothing to defer *from*. These tests
pin both halves, and pin the containment: jv_v1 must keep deferring, because
for Marcus the deposit genuinely is unsettled. Rendering a confirmed-sounding
deposit line for jv_v1 would be a worse defect than the one being fixed.
"""

import pytest

from app.prompts.clinic_template_prompt import _render_policies
from app.prompts.susie_system_prompt import build_system_prompt_parts


def _prompt(clinic_id):
    """Render through the same entry point the live call path uses, so these
    assertions are about what Susie is actually handed. (Going one level lower
    to build_clinic_prompt reaches a different config shape for theorem/demo.)"""
    session = {
        "call_sid": "CAtest_deposit",
        "clinic_id": clinic_id,
        "booking_flow_active": True,
        "collected": {},
    }
    static, dynamic = build_system_prompt_parts(session)
    return f"{static}\n\n{dynamic}"


def _policies(pricing_and_policies, practitioner="Jonathan"):
    """Render POLICIES for a synthetic clinic, so the branch is exercised
    directly rather than only through whichever clinics happen to exist."""
    return _render_policies(
        {"pricing_and_policies": pricing_and_policies},
        {"practitioner": practitioner},
    )


# --------------------------------------------------------------------------
# The defect: Vital Edge could not state a policy it had confirmed.
# --------------------------------------------------------------------------


def test_vital_edge_states_its_deposit_policy():
    text = _prompt("vital_edge")
    assert "No deposit or booking fee" in text


def test_vital_edge_is_told_not_to_defer_the_deposit():
    """The fact alone is not enough — the UNCONFIRMED block exerts pull toward
    deferral, so the deposit line carries an explicit never-defer instruction,
    the same idiom this file already uses for the what-to-wear question."""
    line = next(
        l for l in _prompt("vital_edge").splitlines()
        if l.strip().startswith("Deposit:")
    )
    assert "never defer" in line.lower()


def test_vital_edge_worked_example_is_not_the_deposit():
    """The example must name a field that is ACTUALLY unconfirmed for this
    clinic. Naming the deposit is what produced the observed deferral."""
    line = next(
        l for l in _prompt("vital_edge").splitlines() if "UNCONFIRMED POLICIES" in l
    )
    example = line.split("(e.g.", 1)[1].split(")", 1)[0]
    assert "deposit" not in example.lower()
    assert "report" in example.lower()


def test_vital_edge_unconfirmed_block_still_lists_its_real_tbc_field():
    """Fixing the example must not disarm the block. reports_and_letters is
    genuinely TBC for Vital Edge and must still be named."""
    line = next(
        l for l in _prompt("vital_edge").splitlines() if "UNCONFIRMED POLICIES" in l
    )
    assert "reports and letters" in line


# --------------------------------------------------------------------------
# Containment: jv_v1's deposit IS unsettled and must keep being deferred.
# --------------------------------------------------------------------------


def test_jv_v1_does_not_state_a_deposit_as_confirmed():
    text = _prompt("jv_v1")
    assert "Deposit:" not in text
    assert "never defer this question." in text  # the wear line still renders


def test_jv_v1_still_defers_the_deposit():
    line = next(
        l for l in _prompt("jv_v1").splitlines() if "UNCONFIRMED POLICIES" in l
    )
    assert "deposit required" in line
    assert "whether a deposit is required" in line


@pytest.mark.parametrize("clinic_id", ["theorem", "demo"])
def test_clinics_without_a_deposit_policy_render_nothing(clinic_id):
    text = _prompt(clinic_id)
    assert "Deposit:" not in text
    assert "UNCONFIRMED POLICIES" not in text


# --------------------------------------------------------------------------
# The guard itself, on synthetic config — the direction that matters is that a
# TBC value must NEVER be promoted into a confirmed-sounding statement.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        "TBC",
        "tbc",
        "TBC with Marcus",
        "To be confirmed - TBC",
    ],
)
def test_a_tbc_deposit_is_never_rendered_as_a_fact(value):
    out = _policies({"deposit_required": value})
    assert "Deposit:" not in out
    assert "UNCONFIRMED POLICIES" in out
    assert "deposit required" in out


@pytest.mark.parametrize(
    "value",
    [
        "No deposit or booking fee",
        "£20 deposit taken at booking",
        "50% payable in advance",
    ],
)
def test_a_confirmed_deposit_is_rendered_as_a_fact(value):
    out = _policies({"deposit_required": value})
    assert f"Deposit: {value}." in out
    assert "never defer" in out.lower()


@pytest.mark.parametrize("value", [None, "", "   ", 20, True, {}, []])
def test_a_missing_or_non_string_deposit_renders_nothing(value):
    """clinic.json is hand-edited per clinic; a non-string must not crash the
    prompt build or emit a half-formed line."""
    out = _policies({"deposit_required": value})
    assert "Deposit:" not in out


def test_the_worked_example_tracks_the_first_tbc_field():
    out = _policies({"reports_and_letters": "TBC", "deposit_required": "£10"})
    assert "whether a report or letter can be provided" in out
    assert "Deposit: £10." in out


def test_an_unmapped_tbc_field_gets_a_grammatical_fallback():
    """Any future clinic.json key must produce a readable sentence without
    needing an entry in the phrasing table."""
    out = _policies({"parking_charges": "TBC"})
    assert "(e.g. the parking charges policy)" in out


def test_the_block_disappears_when_nothing_is_tbc():
    out = _policies({"deposit_required": "No deposit", "cancellation_policy": "24h"})
    assert "UNCONFIRMED POLICIES" not in out
    assert "Deposit: No deposit." in out
