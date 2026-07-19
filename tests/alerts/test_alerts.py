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
# notify_abandoned_booking — event-triggered warm-lead-dropped operator alert
# ---------------------------------------------------------------------------

async def test_abandoned_booking_disabled_sends_nothing(mock_sms):
    # flag OFF (no alerts_enabled fixture) ⇒ no-op, nothing sent
    result = await alerts.notify_abandoned_booking(
        clinic="Vital Edge", caller_name="Quentin",
        caller_phone="+447502211207", call_sid="CA123",
    )
    assert result == {}
    mock_sms.assert_not_awaited()


async def test_abandoned_booking_sends_operator_sms(alerts_enabled, mock_sms):
    result = await alerts.notify_abandoned_booking(
        clinic="Vital Edge", caller_name="Quentin",
        caller_phone="+447502211207", call_sid="CA123",
    )
    assert result["sms"] == ["abandoned_booking"]
    mock_sms.assert_awaited_once()
    assert mock_sms.await_args.kwargs["to"] == config.OBS_ALERT_SMS_TO
    # Message must carry the details the operator needs to act.
    body = mock_sms.await_args.kwargs["message"]
    assert "Quentin" in body and "+447502211207" in body and "Vital Edge" in body


async def test_abandoned_booking_tolerates_missing_fields(alerts_enabled, mock_sms):
    # Must never crash on a lead with partial data (e.g. no name captured).
    result = await alerts.notify_abandoned_booking(
        clinic=None, caller_name=None, caller_phone="+447502211207", call_sid=None,
    )
    assert result["sms"] == ["abandoned_booking"]
    mock_sms.assert_awaited_once()


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
