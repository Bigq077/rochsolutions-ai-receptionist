# tests/regression/test_tbc_price_defer.py
"""
P1 #2 — Susie invented a home-visit price for a service whose price is
deliberately unconfirmed.

Jules's sweep recorded her quoting "£80, same as in-clinic" for a neuro home
visit. The findings report attributes this to the model "reasoning around" a
config guardrail. It did not: **there was no guardrail on that field.**

`pricing.home_visit_gbp` is `null` for the two Neurological Physiotherapy
services, with the intent recorded only in a sibling `home_visit_gbp_note`
("Kept null so Susie defers rather than quoting a wrong/placeholder price").
Both price renderers gate on `is not None`, so a null was rendered as *silence* —
and the surrounding prompt actively invited the inference:

  * the knowledge block states neuro is delivered "at home or in clinic";
  * every OTHER home-visit-capable service lists its home price
    (Acupuncture "in-clinic £48 | home visit £70");
  * neuro showed "in-clinic £80 | remote £70" and no home line at all.

Offered + every sibling priced + this one blank => infer from the nearest
number. £80 is the in-clinic price. The existing TBC guard in
`_render_policies` does not cover this: it scans only `pricing_and_policies`
fields that are *strings* containing "tbc", never service pricing nulls.

Fix: an explicitly-null `home_visit_gbp` (key PRESENT, value None) is rendered
as a loud do-not-quote marker in both renderers. Key ABSENT keeps meaning "not
offered as a home visit" and must stay silent — that distinction is the whole
discriminator and is asserted below.

This is the same failure class as the documented Call-5 regression noted in
`_home_visits_enabled` (2026-07-08), where suppressing a home-visit price from
the prompt caused the in-clinic price to be quoted instead.
"""
from __future__ import annotations

import pytest

from app.clinic_config import get_clinic
from app.prompts.clinic_template_prompt import (
    _render_prices,
    _service_price_summary,
    build_clinic_prompt,
)

NEURO_ASSESS = "Neurological Physiotherapy — Initial Assessment"
NEURO_FOLLOW = "Neurological Physiotherapy — Follow-up"
MARKER = "PRICE NOT CONFIRMED"


@pytest.fixture()
def jv():
    return get_clinic("jv_v1")


@pytest.fixture()
def jv_static(jv):
    static, _ = build_clinic_prompt({"clinic_id": "jv_v1", "turn_count": 1}, jv)
    return static


# ── 1. The PRICES block must flag the unconfirmed rate, not omit it ───────
def test_prices_block_flags_unconfirmed_home_visit(jv):
    out = _render_prices(jv, {"practitioner": "Marcus"})
    assert MARKER in out, (
        "a null home_visit_gbp rendered as silence — the model infers a price "
        "from the in-clinic figure"
    )


@pytest.mark.parametrize("svc", [NEURO_ASSESS, NEURO_FOLLOW])
def test_unconfirmed_services_named_in_the_marker(jv, svc):
    out = _render_prices(jv, {"practitioner": "Marcus"})
    marker_line = next(ln for ln in out.splitlines() if MARKER in ln)
    assert svc in marker_line


def test_marker_forbids_reusing_the_in_clinic_price(jv):
    """The specific wrong answer observed was "£80, same as in-clinic"."""
    out = _render_prices(jv, {"practitioner": "Marcus"})
    marker_line = next(ln for ln in out.splitlines() if MARKER in ln)
    low = marker_line.lower()
    assert "in-clinic" in low and "remote" in low, (
        "must explicitly forbid reusing the in-clinic/remote price — that is "
        "the exact inference that produced the invented quote"
    )


# ── 2. The compact per-service summary leaks the same inference ───────────
def test_service_summary_flags_unconfirmed_home_visit(jv):
    """`neuro_assessment (in-clinic £80 | remote £70)` sat directly beside
    `acupuncture (in-clinic £48 | home visit £70)`. The absence is the tell."""
    neuro = next(s for s in jv["services"] if s.get("name") == NEURO_ASSESS)
    summary = _service_price_summary(neuro, jv.get("modalities"), True)
    assert MARKER in summary


# ── 3. Must not regress: real home prices still render ────────────────────
def test_confirmed_home_visit_prices_still_render(jv):
    out = _render_prices(jv, {"practitioner": "Marcus"})
    assert "Acupuncture: £70" in out
    assert "MSK Treatment Session: £80" in out


def test_in_clinic_and_remote_neuro_prices_unchanged(jv_static):
    assert f"{NEURO_ASSESS} — 60 mins: £80" in jv_static
    assert f"{NEURO_ASSESS} — 60 mins: £70" in jv_static


# ── 4. Must not over-fire: key ABSENT means "no home visit", stay silent ──
def test_services_without_a_home_visit_key_are_not_flagged(jv):
    """Sports Massage / Virtual / Outdoor have no `home_visit_gbp` key at all.
    They are not home-visit services and must not acquire a TBC marker."""
    out = _render_prices(jv, {"practitioner": "Marcus"})
    marker_lines = [ln for ln in out.splitlines() if MARKER in ln]
    joined = " ".join(marker_lines)
    for name in ("Sports Massage", "Virtual Appointment", "Outdoor Sports Rehabilitation"):
        assert name not in joined, f"{name} does not offer home visits — must stay silent"


def test_absent_key_service_summary_is_untouched(jv):
    sports = next(s for s in jv["services"] if s.get("name") == "Sports Massage")
    assert MARKER not in _service_price_summary(sports, jv.get("modalities"), True)


# ── 4b. `available_as` is authoritative, not key-presence ─────────────────
# A null price means opposite things depending on whether the modality is
# offered. Initial Assessment carries `remote_gbp: None` with `available_as`
# = [in_clinic, home_visit] — remote is NOT offered, so that null is correctly
# silent. Neuro carries `home_visit_gbp: None` with home_visit IN available_as
# — offered but unpriced, so it must be flagged. Both are "key present, value
# None"; only `available_as` separates them.
def test_null_price_for_an_unoffered_modality_is_not_flagged(jv):
    """Initial Assessment must never be marked TBC — it has a home price (£80),
    and its null `remote_gbp` simply means remote is not offered."""
    out = _render_prices(jv, {"practitioner": "Marcus"})
    marker_line = next(ln for ln in out.splitlines() if MARKER in ln)
    assert "Initial Assessment (Musculoskeletal)" not in marker_line
    assert "Initial Assessment (Musculoskeletal): £80" in out


def test_available_as_overrides_key_presence():
    """An explicit null whose modality is NOT in available_as stays silent."""
    from app.prompts.clinic_template_prompt import _home_visit_price_unconfirmed

    offered = {"available_as": ["in_clinic", "home_visit"], "pricing": {"home_visit_gbp": None}}
    not_offered = {"available_as": ["in_clinic"], "pricing": {"home_visit_gbp": None}}
    priced = {"available_as": ["in_clinic", "home_visit"], "pricing": {"home_visit_gbp": 70}}

    assert _home_visit_price_unconfirmed(offered) is True
    assert _home_visit_price_unconfirmed(not_offered) is False
    assert _home_visit_price_unconfirmed(priced) is False


# ── 5. Config-driven, not hardcoded for jv_v1 ─────────────────────────────
def test_behaviour_is_generic_over_config():
    synthetic = {
        "services": [
            {"name": "Priced Service", "pricing": {"in_clinic_gbp": 50, "home_visit_gbp": 75}},
            {"name": "Unconfirmed Service", "pricing": {"in_clinic_gbp": 60, "home_visit_gbp": None}},
            {"name": "No Home Service", "pricing": {"in_clinic_gbp": 40}},
        ],
        "modalities": ["in_clinic", "home_visit"],
    }
    out = _render_prices(synthetic, {"practitioner": "the clinician"})
    assert "Priced Service: £75" in out
    marker_line = next(ln for ln in out.splitlines() if MARKER in ln)
    assert "Unconfirmed Service" in marker_line
    assert "No Home Service" not in marker_line


# ── 6. End-to-end: the marker survives into the assembled prompt ──────────
def test_marker_present_in_assembled_prompt(jv_static):
    assert MARKER in jv_static
