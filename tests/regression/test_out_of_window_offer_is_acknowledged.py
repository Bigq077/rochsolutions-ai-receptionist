"""Job 3c.2 / CAce1457d1 — out-of-window offers must keep their acknowledgement.

Caller asked for 5:30–9pm; Susie offered half four and said only "Does that
work?". The model had generated the required comparison —

    "The closest I've got to half five through nine is Friday at half four —
     does that work?"

— and Gate 5b's closest/nearest opener strip deleted "to [window] is", leaving
only the slot + CTA. Bare scarcity ("The closest I have is Friday…") must still
strip; the "to [request]" form must survive.
"""
from __future__ import annotations

from app.media_streams.turn_handler import sanitise_response


def test_closest_to_requested_window_survives_gate5():
    raw = (
        "The closest I've got to half five through nine is Friday at half four "
        "— does that work?"
    )
    out = sanitise_response(raw, {})
    assert "half five" in out.lower() or "through nine" in out.lower(), out
    assert "half four" in out.lower(), out
    assert "does that work" in out.lower(), out


def test_closest_to_evenings_survives_gate5():
    raw = "The closest I've got to evenings is Thursday at half four — does that work?"
    out = sanitise_response(raw, {})
    assert "evening" in out.lower(), out
    assert "half four" in out.lower(), out


def test_bare_closest_scarcity_still_strips():
    """Unchanged: robotic scarcity with no comparison to a request."""
    raw = "The closest I have is Friday at half four — does that work?"
    out = sanitise_response(raw, {})
    assert not out.lower().startswith("the closest"), out
    assert "half four" in out.lower() or "friday" in out.lower(), out


def test_prompt_requires_out_of_window_acknowledgement():
    from app.prompts.clinic_template_prompt import build_clinic_prompt
    from app.clinic_config import get_clinic

    clinic = get_clinic("jv_v1") or {}
    session = {"clinic_id": "jv_v1", "collected": {}}
    static, _ = build_clinic_prompt(session, clinic)
    low = static.lower()
    assert "out-of-window" in low or "outside it" in low
    assert "half five to nine" in low or "from half five" in low
