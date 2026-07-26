"""Don't promise a confirmation text that will never be sent.

Verification call CA4969580082db5e757c3b1d04dd38e7ae, 2026-07-26, closing turn:

    ASSISTANT | All booked — you're in for Wednesday the 29th at seven in the
                evening. I've just sent you a confirmation text.

No text was sent. `SMS_ENABLED` defaults OFF on this branch — deliberately, so
an eval service can never text a real caller (see notifications/sms.py) — and
the 2026-07-25 log shows `[sms] SMS_ENABLED is off — outbound SMS suppressed`.
The closing line was hard-coded into the prompt with no knowledge of the flag,
so every caller on this service was told about a text that never arrived.

It would have been said to the demo caller in front of ~100 clinics.

The prompt now reads the same env var the send path gates on, so the promise and
the send cannot disagree. The home-visit branch still asks the caller to text US
their address — that direction is unaffected by our outbound switch.
"""
from __future__ import annotations

import importlib

import pytest

import app.prompts.clinic_template_prompt as ctp


@pytest.fixture
def clinic():
    from app.clinic_config import get_clinic
    return get_clinic("jv_v1")


def _prompt(monkeypatch, clinic, sms_enabled: str) -> str:
    """Build the clinic prompt with SMS_ENABLED set, flattened to one string.

    The flag is read at module import, so reload after setting it.
    build_clinic_prompt returns (system_prompt, call_state) — join both, since
    the booking-closing wording lives in the first and the CALL STATE steers in
    the second.
    """
    monkeypatch.setenv("SMS_ENABLED", sms_enabled)
    importlib.reload(ctp)
    built = ctp.build_clinic_prompt(clinic, {})
    if isinstance(built, (tuple, list)):
        return "\n".join(str(part) for part in built)
    return str(built)


@pytest.fixture(autouse=True)
def _restore():
    """Leave the module in its default state for every other test."""
    yield
    importlib.reload(ctp)


# ─────────────────────────────────────────────────────────────────────────
# SMS OFF — the state this branch actually runs in
# ─────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("off", ["false", "", "0", "no", "FALSE", "anything-else"])
def test_no_text_is_promised_when_sms_is_off(monkeypatch, clinic, off):
    text = _prompt(monkeypatch, clinic, off).lower()
    assert "i've just sent you a confirmation text" not in text
    assert "ive just sent you a confirmation text" not in text


def test_the_model_is_told_not_to_promise_one(monkeypatch, clinic):
    """Removing the sentence isn't enough — the model can invent it. State the
    prohibition explicitly."""
    text = _prompt(monkeypatch, clinic, "false").lower()
    assert "never tell the caller a confirmation text has been sent" in text


def test_the_booking_closing_still_exists(monkeypatch, clinic):
    """The line must lose the promise, not the confirmation."""
    text = _prompt(monkeypatch, clinic, "false")
    assert "All booked" in text
    assert "take care" in text


def test_home_visit_still_asks_the_caller_to_text_us(monkeypatch, clinic):
    """Inbound direction — the caller texting US their address — does not
    depend on our outbound SMS switch and must survive."""
    text = _prompt(monkeypatch, clinic, "false").lower()
    assert "text us your full home address" in text


# ─────────────────────────────────────────────────────────────────────────
# SMS ON — the promise is true, so it should be made
# ─────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("on", ["true", "1", "yes", "on", "TRUE", " true "])
def test_the_text_is_promised_when_sms_is_on(monkeypatch, clinic, on):
    text = _prompt(monkeypatch, clinic, on)
    assert "I've just sent you a confirmation text" in text


def test_the_prohibition_is_absent_when_sms_is_on(monkeypatch, clinic):
    text = _prompt(monkeypatch, clinic, "true").lower()
    assert "never tell the caller a confirmation text has been sent" not in text


# ─────────────────────────────────────────────────────────────────────────
# The invariant that matters
# ─────────────────────────────────────────────────────────────────────────
def test_promise_and_send_gate_cannot_disagree(monkeypatch, clinic):
    """Both sides read the same env var with the same truthy set. Assert they
    agree for every spelling — this is the property, not the wording."""
    from app.notifications import sms as sms_mod
    import inspect

    truthy = ("true", "1", "yes", "on")
    src = inspect.getsource(sms_mod)
    for value in truthy:
        assert f'"{value}"' in src, (
            f"send path no longer treats {value!r} as enabled — the prompt gate "
            "in clinic_template_prompt.py must be updated to match"
        )

    for value in truthy:
        assert "I've just sent you a confirmation text" in _prompt(
            monkeypatch, clinic, value
        ), value
    for value in ("false", "0", "no", ""):
        assert "I've just sent you a confirmation text" not in _prompt(
            monkeypatch, clinic, value
        ), value
