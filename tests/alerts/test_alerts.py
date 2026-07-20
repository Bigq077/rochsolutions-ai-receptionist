"""Unit tests for app.obs.alerts — the rule-based failure-alert router."""
from __future__ import annotations

import pytest

from app import config
from app.obs import alerts
from tests.alerts.conftest import make_record


# ---------------------------------------------------------------------------
# evaluate_call — one assertion per §5.2 condition (pure, no sends)
# ---------------------------------------------------------------------------

def _only(record, signals=None):
    got = alerts.evaluate_call(record, signals)
    assert len(got) == 1, f"expected exactly one alert, got {[a.condition for a in got]}"
    return got[0]


def test_clean_call_fires_nothing():
    assert alerts.evaluate_call(make_record()) == []


def test_pipeline_error_high_immediate_sentry_and_sms():
    a = _only(make_record(), {"pipeline_error": True})
    assert a.condition == "pipeline_error"
    assert a.severity == "high"
    assert a.cadence == alerts.IMMEDIATE
    assert "sentry" in a.channels and "sms" in a.channels


def test_stt_tts_failure():
    a = _only(make_record(), {"tts_error": True})
    assert a.condition == "stt_tts_failure"
    assert a.cadence == alerts.IMMEDIATE


def test_booking_api_error():
    a = _only(make_record(), {"calendar_error": "500 from Acuity"})
    assert a.condition == "booking_api_error"
    assert "sms" in a.channels


def test_escalation_not_delivered_is_critical():
    a = _only(make_record(transfer_attempted=True), {"transfer_sms_failed": True})
    assert a.condition == "escalation_not_delivered"
    assert a.severity == "critical"
    assert a.cadence == alerts.IMMEDIATE


def test_escalation_needs_both_transfer_and_sms_failure():
    # transfer attempted but SMS delivered ⇒ no alert
    assert alerts.evaluate_call(make_record(transfer_attempted=True), {}) == []


def test_no_audio_close_fires_dead_air_alert():
    a = _only(make_record(reason="graceful_exit"), {"no_audio_close": True})
    assert a.condition == "no_audio_call"
    assert a.cadence == alerts.IMMEDIATE
    assert "sms" in a.channels


def test_stt_error_suppresses_no_audio_duplicate():
    # A confirmed STT error already alerts via stt_tts_failure; the dead-air
    # backstop must not double-SMS the same call.
    a = _only(make_record(), {"stt_error": True, "no_audio_close": True})
    assert a.condition == "stt_tts_failure"


def test_abandoned_ghost_call_is_immediate():
    a = _only(make_record(reason="caller_hung_up", turn_count=0, duration_s=27))
    assert a.condition == "abandoned_call"
    assert a.cadence == alerts.IMMEDIATE
    assert "sms" in a.channels


def test_zero_turn_booked_call_is_not_abandoned():
    # Edge case: a call can end with turn_count 0 in the record but still be a
    # legitimate booking/transfer/graceful exit (e.g. DTMF-only flow) — must not
    # be treated as a ghost call.
    assert alerts.evaluate_call(make_record(reason="booked", turn_count=0)) == []


def test_short_call_is_daily_rollup():
    a = _only(make_record(duration_s=8, turn_count=1))
    assert a.condition == "short_call"
    assert a.cadence == alerts.DAILY
    assert a.channels == ("rollup",)


def test_retry_storm_is_daily_rollup():
    a = _only(make_record(slot_retry_counts={"phone": 3, "day": 1}))
    assert a.condition == "retry_storm"
    assert a.cadence == alerts.DAILY


# ---------------------------------------------------------------------------
# dispatch — routing to channels (senders mocked)
# ---------------------------------------------------------------------------

async def test_dispatch_disabled_sends_nothing(mock_sms):
    # flag defaults OFF in tests unless alerts_enabled is used
    alert = alerts.evaluate_call(make_record(), {"pipeline_error": True})
    result = await alerts.dispatch(alert)
    assert result == {}
    mock_sms.assert_not_awaited()


async def test_dispatch_immediate_sends_sms(alerts_enabled, mock_sms):
    alert = alerts.evaluate_call(make_record(), {"pipeline_error": True})
    result = await alerts.dispatch(alert)
    assert result["sms"] == ["pipeline_error"]
    mock_sms.assert_awaited_once()
    assert mock_sms.await_args.kwargs["to"] == config.OBS_ALERT_SMS_TO


async def test_dispatch_daily_goes_to_rollup_not_sms(alerts_enabled, mock_sms):
    alert = alerts.evaluate_call(make_record(duration_s=5, turn_count=1))
    result = await alerts.dispatch(alert)
    assert result["rollup"] == ["short_call"]
    mock_sms.assert_not_awaited()
    assert [a.condition for a in alerts.get_rollup()] == ["short_call"]


async def test_route_call_end_to_end_immediate(alerts_enabled, mock_sms):
    sent = await alerts.route_call(make_record(), {"calendar_error": "boom"})
    assert sent["sms"] == ["booking_api_error"]
    mock_sms.assert_awaited_once()


async def test_route_call_never_raises_on_bad_input(alerts_enabled, mock_sms):
    # None record would explode inside evaluate_call; route_call must swallow it.
    assert await alerts.route_call(None, {}) == {}


# ---------------------------------------------------------------------------
# daily roll-up flush
# ---------------------------------------------------------------------------

async def test_flush_daily_rollup_sends_summary_and_clears(alerts_enabled, mock_sms):
    await alerts.dispatch(alerts.evaluate_call(make_record(duration_s=5, turn_count=1)))
    await alerts.dispatch(alerts.evaluate_call(make_record(slot_retry_counts={"phone": 4})))
    assert len(alerts.get_rollup()) == 2

    summary = await alerts.flush_daily_rollup()
    assert summary is not None
    assert "short_call" in summary and "retry_storm" in summary
    mock_sms.assert_awaited_once()
    assert alerts.get_rollup() == []


async def test_flush_noop_when_empty(alerts_enabled, mock_sms):
    assert await alerts.flush_daily_rollup() is None
    mock_sms.assert_not_awaited()


# ---------------------------------------------------------------------------
# Sentry passthrough is a safe no-op when Sentry is not initialised
# ---------------------------------------------------------------------------

def test_capture_exception_is_safe_without_sentry():
    # Must not raise even though Sentry is not initialised in the test env.
    alerts.capture_exception(ValueError("boom"))
