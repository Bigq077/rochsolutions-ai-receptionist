# tests/test_actionable_summary_tenant.py
"""
Tenant-awareness of the call-summary LLM prompt.

The Google-Sheets call summary (columns 1 & 11) is written by an LLM whose
system prompt carries the clinic's identity, owner, pricing and locations.
Historically this block was hard-coded to Theorem, so a Vital Edge call would
be summarised as if it were a Theorem physio call. These tests lock in that the
prompt is now built per-clinic from the clinic contract.
"""

from app.clinic_config import get_clinic
from app.tools.actionable_summary import (
    _SYSTEM_PROMPT,
    _build_system_prompt,
    _resolve_owner_name,
)


def test_theorem_prompt_is_theorem_branded():
    clinic = get_clinic("theorem")
    prompt = _build_system_prompt(clinic)

    assert "Theorem" in prompt
    assert _resolve_owner_name(clinic) == "Mark"
    # Owner is addressed by name, not the generic fallback.
    assert "Mark" in prompt
    assert "Jonathan" not in prompt
    assert "Vital Edge" not in prompt


def test_vital_edge_prompt_is_vital_edge_branded():
    clinic = get_clinic("vital_edge")
    prompt = _build_system_prompt(clinic)

    # Vital Edge's own identity — never Theorem's.
    assert "Vital Edge" in prompt
    assert _resolve_owner_name(clinic) == "Jonathan"
    assert "Jonathan" in prompt

    # Must NOT leak Theorem specifics into a Vital Edge summary.
    assert "Theorem" not in prompt
    assert "Mark" not in prompt
    assert "Alcester" not in prompt
    assert "Redditch" not in prompt
    assert "07870 166861" not in prompt


def test_vital_edge_prompt_carries_its_own_pricing():
    clinic = get_clinic("vital_edge")
    prompt = _build_system_prompt(clinic)
    # £125 / £175 massage pricing, not £75 physio.
    assert "125" in prompt
    assert "75 / 50" not in prompt  # the old Theorem "£75 / 50 min" line


def test_owner_name_falls_back_gracefully():
    # A clinic contract with no owner/practitioner field must not crash and
    # must not invent "Mark".
    assert _resolve_owner_name({}) == "the clinic owner"


def test_build_system_prompt_never_raises_on_sparse_clinic():
    # Minimal contract (only a display name) still yields a usable prompt.
    prompt = _build_system_prompt({"display_name": "Tiny Clinic"})
    assert "Tiny Clinic" in prompt
    assert isinstance(prompt, str) and prompt.strip()


def test_default_constant_still_present_as_fallback():
    # The hard-coded default is retained as the last-resort fallback.
    assert "Theorem" in _SYSTEM_PROMPT
