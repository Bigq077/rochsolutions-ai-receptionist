"""
SMS must default ON for this live clinic branch.

theorem-onboarding descends from latency-eval, which defaults SMS_ENABLED OFF
because it is an isolated timing-eval service that must never text a real
caller. That default arrived with the lineage, past a comment that explicitly
said not to port it to live branches — and Mark's line then sent nothing at
all: no booking confirmation, no staff transfer notice, no reminder.

It also made Susie say something untrue. The theorem_v3 prompt closes a
cancellation with "Confirmation text on its way" unconditionally, so every
caller was promised a text that could never arrive.

The failure direction matters here and is the whole point of the test: on a
live clinic line, a forgotten environment variable must not silence patient
communications. Suppression has to be deliberate.
"""

import importlib

import pytest


def _sms_module(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("SMS_ENABLED", raising=False)
    else:
        monkeypatch.setenv("SMS_ENABLED", value)
    import app.notifications.sms as sms
    return importlib.reload(sms)


class _FakeMessages:
    def __init__(self):
        self.calls = []

    def create(self, body, from_, to):
        self.calls.append({"body": body, "from_": from_, "to": to})
        return type("Msg", (), {"sid": "SM_fake", "status": "queued"})()


class _FakeClient:
    def __init__(self):
        self.messages = _FakeMessages()


async def _attempt(monkeypatch, value):
    """Try to send one SMS with SMS_ENABLED at `value`; return the fake client."""
    sms = _sms_module(monkeypatch, value)
    svc = sms.SMSService.__new__(sms.SMSService)
    client = _FakeClient()
    svc.client = client
    svc.from_number = "+447380841468"
    sid = await svc.send_sms(to="+447502211207", message="test")
    return client, sid


async def test_unset_env_still_sends(monkeypatch):
    """THE regression. An unset variable must not silence a live clinic."""
    client, sid = await _attempt(monkeypatch, None)
    assert client.messages.calls, (
        "SMS_ENABLED unset suppressed the send — the latency-eval default "
        "has been reintroduced on a live clinic branch"
    )
    assert sid == "SM_fake"


@pytest.mark.parametrize("off", ["false", "0", "no", "off", "FALSE"])
async def test_explicit_off_suppresses(monkeypatch, off):
    """Suppression must remain possible — deliberately, and only deliberately."""
    client, sid = await _attempt(monkeypatch, off)
    assert not client.messages.calls
    assert sid is None


@pytest.mark.parametrize("on", ["true", "1", "yes", "on", "TRUE"])
async def test_explicit_on_sends(monkeypatch, on):
    client, sid = await _attempt(monkeypatch, on)
    assert client.messages.calls
    assert sid == "SM_fake"


def test_one_gate_only(monkeypatch):
    """Every SMS surface funnels through send_sms. If a second Twilio send site
    appears elsewhere in app/, it bypasses this gate entirely — in either
    direction."""
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[2] / "app"
    offenders = []
    for path in root.rglob("*.py"):
        if "__pycache__" in str(path):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        # Twilio sends take from_/to; Anthropic messages.create does not.
        if "messages.create(" in text and "from_=" in text:
            rel = str(path.relative_to(root)).replace("\\", "/")
            if rel != "notifications/sms.py":
                offenders.append(rel)

    assert not offenders, f"Twilio SMS sent outside the SMS_ENABLED gate: {offenders}"
