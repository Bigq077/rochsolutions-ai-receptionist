"""
TRANSFER_DISABLED kill-switch — every outbound transfer path must honour it.

Ported to theorem-onboarding 2026-08-04 ahead of the 20-call acceptance suite.
Theorem's transfer_phone is Mark Dyer's real mobile; without this switch the
sweep's transfer scenarios ring him for real.

The switch covers FOUR sites, one more than the original main-branch version:

  1. app/config.py                    — the flag itself
  2. app/routes/realtime.py           — the live media-stream <Dial> (THE one)
  3. app/tools/receptionist_tools.py  — the "transferring a patient" heads-up SMS
  4. app/routes/twilio.py             — the legacy /twilio/voice Gather <Dial>

Site 4 is not on the live /ms/incoming path today, but a kill-switch that
covers only some dial sites is worse than none.
"""

import importlib
from unittest.mock import patch

import pytest


def _reload_config(monkeypatch, value):
    """Re-import app.config with TRANSFER_DISABLED set to `value`."""
    if value is None:
        monkeypatch.delenv("TRANSFER_DISABLED", raising=False)
    else:
        monkeypatch.setenv("TRANSFER_DISABLED", value)
    import app.config
    return importlib.reload(app.config)


# ── 1. the flag itself ──────────────────────────────────────────────────────

def test_defaults_off_when_unset(monkeypatch):
    """Production leaves it unset. Behaviour must be unchanged there."""
    assert _reload_config(monkeypatch, None).TRANSFER_DISABLED is False


@pytest.mark.parametrize("truthy", ["1", "true", "TRUE", "yes", "on", " True "])
def test_truthy_spellings(monkeypatch, truthy):
    assert _reload_config(monkeypatch, truthy).TRANSFER_DISABLED is True


@pytest.mark.parametrize("falsy", ["", "0", "false", "no", "off", "maybe"])
def test_falsy_spellings(monkeypatch, falsy):
    assert _reload_config(monkeypatch, falsy).TRANSFER_DISABLED is False


# ── 2. the live media-stream dial ───────────────────────────────────────────

async def test_realtime_dial_suppressed(monkeypatch):
    """_handle_transfer must return before constructing a Twilio client."""
    _reload_config(monkeypatch, "1")
    from app.routes import realtime

    with patch("twilio.rest.Client") as mock_client:
        await realtime._handle_transfer("CA_test", {"clinic_id": "theorem"})

    assert not mock_client.called, "a live call leg was placed with the switch ON"


async def test_realtime_dial_allowed_when_off(monkeypatch):
    """Guard against a fix that suppresses transfers unconditionally."""
    _reload_config(monkeypatch, None)
    from app.routes import realtime
    import inspect

    src = inspect.getsource(realtime._handle_transfer)
    assert "TRANSFER_DISABLED" in src, "the realtime dial no longer reads the switch"
    # The early return must be conditional, never unconditional.
    assert "if TRANSFER_DISABLED:" in src


# ── 3. the heads-up SMS ─────────────────────────────────────────────────────

def test_headsup_sms_reads_the_switch():
    """The SMS site must gate on the flag, not just on transfer_phone."""
    import inspect
    from app.tools import receptionist_tools

    src = inspect.getsource(receptionist_tools._exec_transfer_to_human)
    assert "TRANSFER_DISABLED" in src, (
        "the transfer heads-up SMS no longer honours TRANSFER_DISABLED"
    )


async def test_headsup_sms_actually_stays_silent(monkeypatch):
    """
    The switch is only worth anything if no message leaves. This used to be a
    string match on `if transfer_phone and not TRANSFER_DISABLED:`, which went
    red on 2026-08-07 when the branch gained a third arm (no dial target → send
    CALLBACK NEEDED instead of a false "call coming through now"). The property
    was never broken; the literal was. Asserting the behaviour instead means a
    future restructure is judged on whether a text escapes, and it also covers
    the new arm — which must NOT become a hole in the kill-switch.
    """
    sent: list = []

    async def _fake_send(to, message):
        sent.append((to, message))

    monkeypatch.setattr("app.notifications.sms.send_sms", _fake_send)
    monkeypatch.setattr("app.config.TRANSFER_DISABLED", True)
    monkeypatch.setattr("app.tools.handoff.send_to_sheet", lambda *a, **k: None)

    from app.tools.receptionist_tools import _exec_transfer_to_human

    await _exec_transfer_to_human(
        {"reason": "sweep"},
        {
            "clinic_id": "theorem_v3",       # transfer_phone is Mark's real mobile
            "collected": {"name": "Test", "phone": "+447700900456"},
            "call_sid": "CA_test",
        },
    )
    import asyncio
    await asyncio.sleep(0)

    assert sent == [], f"the switch was ON and an SMS still went out: {sent}"


# ── 4. the legacy Gather dial ───────────────────────────────────────────────

def test_legacy_twilio_dial_reads_the_switch():
    import inspect
    from app.routes import twilio as twilio_routes

    src = inspect.getsource(twilio_routes._append_transfer)
    assert "TRANSFER_DISABLED" in src, (
        "the legacy /twilio/voice transfer path no longer honours the switch"
    )
    assert "if TRANSFER_DISABLED:" in src


# ── 5. no fifth path crept in ───────────────────────────────────────────────

def test_no_unguarded_dial_site():
    """
    Every module that places an outbound transfer leg must import the switch.
    If this fails, a new dial site was added without a kill-switch — find it
    before the next sweep, not during one.
    """
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[2] / "app"
    offenders = []
    for path in root.rglob("*.py"):
        if "__pycache__" in str(path) or "/tests/" in str(path).replace("\\", "/"):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        places_leg = "dial.number(" in text or "calls(call_sid).update(" in text
        if places_leg and "TRANSFER_DISABLED" not in text:
            offenders.append(str(path.relative_to(root)))

    assert not offenders, f"unguarded outbound transfer site(s): {offenders}"
