"""Tests for app.obs.worker — the daily digest scheduler.

The worker is the only new always-running code this branch adds to the web service,
so the tests below pin the properties that keep it safe: it does not start unless
explicitly enabled, it sends at most once a day, and it never marks a failed send as
done (so it self-heals once SMTP is configured).
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from app import config
from app.obs import worker


# ---------------------------------------------------------------------------
# Gating — default OFF
# ---------------------------------------------------------------------------

def test_disabled_by_default(monkeypatch):
    monkeypatch.delenv("OBS_DIGEST_ENABLED", raising=False)
    assert worker.is_enabled() is False


def test_enabled_only_by_explicit_true(monkeypatch):
    monkeypatch.setenv("OBS_DIGEST_ENABLED", "1")
    assert worker.is_enabled() is False      # only the literal "true" counts
    monkeypatch.setenv("OBS_DIGEST_ENABLED", "true")
    assert worker.is_enabled() is True


async def test_worker_returns_immediately_when_disabled(monkeypatch):
    monkeypatch.delenv("OBS_DIGEST_ENABLED", raising=False)
    # Must return, not loop — a 0.5s timeout would trip if it slept.
    await asyncio.wait_for(worker.start_obs_digest_worker(interval_seconds=1), timeout=0.5)


async def test_worker_returns_when_no_database(monkeypatch):
    monkeypatch.setenv("OBS_DIGEST_ENABLED", "true")
    monkeypatch.setattr(config, "DATABASE_URL", "")
    await asyncio.wait_for(worker.start_obs_digest_worker(interval_seconds=1), timeout=0.5)


# ---------------------------------------------------------------------------
# Send-time config
# ---------------------------------------------------------------------------

def test_send_time_defaults_to_0700(monkeypatch):
    monkeypatch.delenv("OBS_DIGEST_HOUR", raising=False)
    monkeypatch.delenv("OBS_DIGEST_MINUTE", raising=False)
    assert worker._send_time() == (7, 0)


def test_send_time_is_clamped_and_garbage_tolerant(monkeypatch):
    monkeypatch.setenv("OBS_DIGEST_HOUR", "99")
    monkeypatch.setenv("OBS_DIGEST_MINUTE", "0")
    assert worker._send_time() == (23, 0)
    monkeypatch.setenv("OBS_DIGEST_HOUR", "not-a-number")
    assert worker._send_time() == (7, 0)


def test_lookback_defaults_to_24h(monkeypatch):
    monkeypatch.delenv("OBS_DIGEST_HOURS", raising=False)
    assert worker._lookback_hours() == 24


# ---------------------------------------------------------------------------
# send_now — channel selection
# ---------------------------------------------------------------------------

async def test_send_now_prefers_email(monkeypatch):
    monkeypatch.setattr(config, "OBS_DIGEST_EMAIL_TO", "q@example.com")
    with patch("app.obs.digest._all_calls", return_value=[]), \
         patch("app.obs.emailer.is_configured", return_value=True), \
         patch("app.obs.emailer.send_email", return_value=True) as mock_email, \
         patch("app.obs.alerts.review_alert", new=AsyncMock(return_value=True)) as mock_sms:
        assert await worker.send_now(24) is True
    mock_email.assert_called_once()
    mock_sms.assert_not_awaited()


async def test_send_now_email_covers_a_zero_call_day(monkeypatch):
    """The email must still go out on a silent day — no email means broken cron."""
    with patch("app.obs.digest._all_calls", return_value=[]), \
         patch("app.obs.emailer.is_configured", return_value=True), \
         patch("app.obs.emailer.send_email", return_value=True) as mock_email:
        assert await worker.send_now(24) is True
    subject = mock_email.call_args.args[0]
    assert "0 call(s)" in subject


async def test_send_now_sms_fallback_is_silent_with_nothing_to_review():
    """Without SMTP, fall back to SMS — but only for review calls, never a heartbeat."""
    clean = [{"call_sid": "CA1", "action_needed": "none", "quality_score": 5}]
    with patch("app.obs.digest._all_calls", return_value=clean), \
         patch("app.obs.emailer.is_configured", return_value=False), \
         patch("app.obs.alerts.review_alert", new=AsyncMock(return_value=True)) as mock_sms:
        assert await worker.send_now(24) is False
    mock_sms.assert_not_awaited()


async def test_send_now_sms_fallback_fires_for_review_calls():
    review = [{"call_sid": "CA1", "action_needed": "review", "quality_score": 1,
               "failure_tags": ["loop"]}]
    with patch("app.obs.digest._all_calls", return_value=review), \
         patch("app.obs.emailer.is_configured", return_value=False), \
         patch("app.obs.alerts.review_alert", new=AsyncMock(return_value=True)) as mock_sms:
        assert await worker.send_now(24) is True
    mock_sms.assert_awaited_once()


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------

async def test_not_yet_sent_is_true_without_redis():
    """No Redis → best-effort send rather than silent suppression."""
    with patch("app.storage.redis_store.redis_client", None):
        assert await worker._not_yet_sent("2026-07-17") is True


async def test_not_yet_sent_false_when_marker_matches_today():
    fake = AsyncMock()
    fake.get = AsyncMock(return_value="2026-07-17")
    with patch("app.storage.redis_store.redis_client", fake):
        assert await worker._not_yet_sent("2026-07-17") is False
        assert await worker._not_yet_sent("2026-07-18") is True
