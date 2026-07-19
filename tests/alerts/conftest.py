"""
Fixtures for the failure-alerting tests.

Senders are always mocked — these tests never send a real SMS or hit Slack
(spec §5.2). The roll-up buffer is reset around every test so state never leaks.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app import config
from app.obs import alerts


@pytest.fixture(autouse=True)
def _reset_rollup():
    alerts.reset_rollup()
    yield
    alerts.reset_rollup()


@pytest.fixture(autouse=True)
def _default_alerts_disabled(monkeypatch):
    """Force the router OFF by default so the suite is hermetic.

    config.OBS_ALERTS_ENABLED is read from the environment at import time, so a
    deployment that sets OBS_ALERTS_ENABLED=true in .env would otherwise flip the
    "disabled-path" tests. Autouse runs before the explicitly-requested
    `alerts_enabled`, which re-enables it for the tests that need it.
    """
    monkeypatch.setattr(config, "OBS_ALERTS_ENABLED", False)


@pytest.fixture
def alerts_enabled(monkeypatch):
    """Turn the router on with an operator SMS number, Slack off."""
    monkeypatch.setattr(config, "OBS_ALERTS_ENABLED", True)
    monkeypatch.setattr(config, "OBS_ALERT_SMS_TO", "+440000000000")  # synthetic
    monkeypatch.setattr(config, "OBS_SLACK_WEBHOOK", "")
    return config


@pytest.fixture
def mock_sms():
    """Patch the operator SMS sender; returns a message SID by default."""
    with patch("app.obs.alerts.send_sms", new=AsyncMock(return_value="SM123")) as m:
        yield m


def make_record(**overrides) -> dict:
    """A clean completed-call record; override fields to trip a condition."""
    rec = {
        "call_sid": "CAalert0001",
        "clinic_id": "theorem",
        "caller_number": "+440000000000",
        "reason": "booked",
        "duration_s": 120,
        "turn_count": 6,
        "transfer_attempted": False,
        "slot_retry_counts": {},
    }
    rec.update(overrides)
    return rec
