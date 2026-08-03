"""Regression — the reschedule closing must confirm the move and survive Gate 5.

Defect (call CA1fc9cb13337ccc7eb936e0dbf5c8fc3d, 2026-08-02, build 85fabee):
the reschedule confirmation in clinic_template_prompt was a single bare line,
"I've rescheduled to [date/time]. Confirmation text on its way." — no warm
close and, unlike the booking closing, no ban on the generic sign-off. The
model appended "Is there anything else I can help with?", Gate 5 stripped it
as a banned phrase, and the call ended on a truncated statement with nothing
after it. The caller could not tell the reschedule had actually happened.

Two further hazards this pins down:

1. SMS_ENABLED defaults OFF on this branch, so "Confirmation text on its way"
   was a promise of a text that is never sent. booking_success was gated on
   SMS_ENABLED on 2026-07-26; reschedule and cancel were missed.

2. The closing must NOT borrow the booking phrase family. _note_write_result
   (llm_stream) never sets booking_write_confirmed on a reschedule — by design,
   "Reschedule is intentionally out of scope". So Gate 5f's false-confirmation
   guard has no success signal to stand it down, and any closing that matches
   _FALSE_CONFIRM_CLAIM_RE would be stripped on any reschedule turn where
   booking_flow_active happens to be set. That is the worst case asserted below.
"""

import importlib
import os

import pytest

from app.media_streams.turn_handler import sanitise_response

RESCHEDULE_CLOSING = (
    "That's you rescheduled — you're now in for Monday the 1st of June at "
    "three in the afternoon. We'll see you then — take care."
)


def _render(monkeypatch, sms_enabled: str) -> str:
    """Render the jv_v1 system prompt with SMS_ENABLED forced either way."""
    monkeypatch.setenv("SMS_ENABLED", sms_enabled)
    import app.prompts.clinic_template_prompt as ctp

    importlib.reload(ctp)
    from app.clinic_config import get_clinic

    clinic = get_clinic("jv_v1")
    static, dynamic = ctp.build_clinic_prompt({"clinic_id": "jv_v1"}, clinic)
    return static + dynamic


def test_prompt_mandates_a_closing_with_a_warm_close(monkeypatch):
    prompt = _render(monkeypatch, "false")
    assert "RESCHEDULE CLOSING" in prompt
    assert "That's you rescheduled" in prompt
    assert "We'll see you then — take care." in prompt


def test_prompt_forbids_the_generic_signoff_on_the_reschedule_path(monkeypatch):
    """The bare closing let the model reach for the sign-off Gate 5 strips."""
    prompt = _render(monkeypatch, "false")
    closing = prompt[prompt.index("RESCHEDULE CLOSING"):]
    assert "do NOT end with 'Is there anything else I can help with?'" in closing


def test_no_text_promised_when_sms_is_off(monkeypatch):
    """SMS_ENABLED off — the prompt must not promise a text anywhere."""
    prompt = _render(monkeypatch, "false")
    assert "Confirmation text on its way" not in prompt
    assert "NEVER tell the caller a confirmation text has been sent" in prompt


def test_text_promised_when_sms_is_on(monkeypatch):
    """Gating must not strip the promise for clinics that do send texts."""
    prompt = _render(monkeypatch, "true")
    assert "Confirmation text on its way" in prompt
    assert "NEVER tell the caller a confirmation text has been sent" not in prompt


@pytest.mark.parametrize(
    "session",
    [
        {},
        # Worst case: a reschedule turn that also tripped booking_flow_active.
        # booking_write_confirmed is absent because a reschedule never sets it.
        {"booking_flow_active": True},
    ],
)
def test_closing_survives_gate5_intact(session):
    """The mandated closing must reach TTS unchanged — this is the defect."""
    assert sanitise_response(RESCHEDULE_CLOSING, dict(session)) == RESCHEDULE_CLOSING


def test_old_closing_was_stripped_by_gate5():
    """Documents the original failure: the sign-off is removed, leaving a stub."""
    old = (
        "I've rescheduled to Friday the 14th of August at six in the evening. "
        "Is there anything else I can help with?"
    )
    cleaned = sanitise_response(old, {})
    assert "Is there anything else" not in cleaned
    assert cleaned.strip() == (
        "I've rescheduled to Friday the 14th of August at six in the evening."
    )
