"""
Regression: each service says at boot whether it records calls and where an
alert would go.

Readiness plan Gate 2 -- "every call produces a record; failures visible
within the hour" -- and readiness bar 4. On 12 Sep 2026 the posture of the
three clinic services (Vital Edge, JV, Theorem) could not be established
from anything in the repo or their logs: every OBS_* switch defaults OFF and
fails silently, and the only per-call signal is a `[obs.store] captured`
line whose absence says nothing. The `[deploy]` banner already solved this
for SMS_ENABLED; this extends it to visibility.

Presence only for the secrets -- `set` / `UNSET` -- never a value.
"""
from __future__ import annotations

import logging

import pytest

from app import main as app_main


def _banner_lines(caplog):
    return [r.getMessage() for r in caplog.records if r.getMessage().startswith("[deploy] obs:")]


def test_the_defaults_read_as_off_and_unset(monkeypatch, caplog):
    for name in ("OBS_CAPTURE_ENABLED", "OBS_JUDGE_ENABLED", "OBS_ALERTS_ENABLED",
                 "OBS_DIGEST_ENABLED", "OBS_DATABASE_URL", "OBS_ALERT_SMS_TO",
                 "OBS_DIGEST_EMAIL_TO"):
        monkeypatch.delenv(name, raising=False)
    with caplog.at_level(logging.INFO, logger="app.main"):
        app_main._log_deployment_posture()
    lines = _banner_lines(caplog)
    assert len(lines) == 1, lines
    line = lines[0]
    assert "OBS_CAPTURE_ENABLED=OFF (DEFAULT)" in line
    assert "OBS_ALERTS_ENABLED=OFF (DEFAULT)" in line
    assert "OBS_DIGEST_ENABLED=OFF (DEFAULT)" in line
    assert "OBS_DATABASE_URL=UNSET" in line
    assert "OBS_ALERT_SMS_TO=UNSET" in line


def test_a_configured_service_reads_as_on_and_set_without_leaking_a_value(monkeypatch, caplog):
    monkeypatch.setenv("OBS_CAPTURE_ENABLED", "true")
    monkeypatch.setenv("OBS_JUDGE_ENABLED", "true")
    monkeypatch.setenv("OBS_ALERTS_ENABLED", "false")
    monkeypatch.setenv("OBS_DIGEST_ENABLED", "true")
    monkeypatch.setenv("OBS_DATABASE_URL", "postgresql+psycopg2://u:SECRETPASS@host:5432/db")
    monkeypatch.setenv("OBS_ALERT_SMS_TO", "+447700900000")
    monkeypatch.delenv("OBS_DIGEST_EMAIL_TO", raising=False)
    with caplog.at_level(logging.INFO, logger="app.main"):
        app_main._log_deployment_posture()
    line = _banner_lines(caplog)[0]
    assert "OBS_CAPTURE_ENABLED=ON (explicit)" in line
    assert "OBS_JUDGE_ENABLED=ON (explicit)" in line
    assert "OBS_ALERTS_ENABLED=OFF (explicit)" in line
    assert "OBS_DIGEST_ENABLED=ON (explicit)" in line
    assert "OBS_DATABASE_URL=set" in line
    assert "OBS_ALERT_SMS_TO=set" in line
    assert "OBS_DIGEST_EMAIL_TO=UNSET" in line
    assert "SECRETPASS" not in line and "447700900000" not in line


def test_the_banner_never_raises(monkeypatch, caplog):
    """A startup banner must not be able to stop a service answering the phone."""
    monkeypatch.setenv("OBS_DATABASE_URL", "   ")          # whitespace only
    monkeypatch.setenv("OBS_CAPTURE_ENABLED", "yes please")  # unrecognised
    with caplog.at_level(logging.INFO, logger="app.main"):
        app_main._log_deployment_posture()
    line = _banner_lines(caplog)[0]
    assert "OBS_DATABASE_URL=UNSET" in line
    assert "OBS_CAPTURE_ENABLED=OFF (explicit)" in line
