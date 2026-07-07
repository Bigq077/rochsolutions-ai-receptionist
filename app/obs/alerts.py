"""
app/obs/alerts.py
-----------------
Rule-based failure alerting (spec §5.2).

Two channels:
- Technical exceptions → Sentry (via `capture_exception`; gated by SENTRY_DSN at
  sentry_sdk.init in app/main.py — a no-op when Sentry is not initialised).
- Call-level failure conditions → operator (Quentin) via SMS and/or Slack, routed
  by a small data-driven severity→channel table so routing is testable.

Everything call-level is gated on config.OBS_ALERTS_ENABLED (default OFF): with the
flag off, `route_call` / `dispatch` return immediately and nothing is ever sent, so
this layer cannot ping anyone or affect any clinic until explicitly enabled. Even
enabled, it only messages the configured operator channels — never a clinic.

Cadence:
- IMMEDIATE conditions (pipeline error, STT/TTS failure, booking API error,
  escalation-not-delivered) alert the moment the call ends.
- DAILY conditions (benign short call, retry storm) are accumulated into a roll-up
  buffer and sent once via `flush_daily_rollup()` (see app/obs/rollup.py) so benign
  events never generate per-call noise.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app import config
from app.notifications.sms import send_sms

_log = logging.getLogger(__name__)

# --- cadence / severity constants ------------------------------------------
IMMEDIATE = "immediate"
DAILY = "daily"

# condition -> routing. Channels: "sentry", "sms", "slack", "rollup".
# This table IS the spec §5.2 conditions table, made data-driven and testable.
_CONDITION_SPECS: Dict[str, Dict[str, Any]] = {
    "pipeline_error":           {"severity": "high",     "cadence": IMMEDIATE, "channels": ("sentry", "sms", "slack")},
    "stt_tts_failure":          {"severity": "high",     "cadence": IMMEDIATE, "channels": ("sentry", "sms", "slack")},
    "booking_api_error":        {"severity": "high",     "cadence": IMMEDIATE, "channels": ("sms", "slack")},
    "escalation_not_delivered": {"severity": "critical", "cadence": IMMEDIATE, "channels": ("sms", "slack")},
    "short_call":               {"severity": "medium",   "cadence": DAILY,     "channels": ("rollup",)},
    "retry_storm":              {"severity": "medium",   "cadence": DAILY,     "channels": ("rollup",)},
}


@dataclass
class Alert:
    condition: str
    severity: str
    cadence: str
    channels: tuple
    message: str


# Module-level daily roll-up buffer (best-effort, in-process). The durable source
# of truth for these events is the Phase 1 `calls` table; this buffer just avoids
# per-call noise between roll-up sends.
_ROLLUP: List[Alert] = []


# ---------------------------------------------------------------------------
# Sentry passthrough (technical channel)
# ---------------------------------------------------------------------------

def capture_exception(exc: BaseException) -> None:
    """Send an exception to Sentry if it is initialised; otherwise a silent no-op.

    Not gated on OBS_ALERTS_ENABLED — the technical channel is controlled by
    SENTRY_DSN (sentry_sdk.capture_exception is a no-op when Sentry is not init'd).
    """
    try:
        import sentry_sdk
        sentry_sdk.capture_exception(exc)
    except Exception:  # pragma: no cover - never let telemetry break a caller
        pass


def _capture_message(message: str, level: str = "error") -> None:
    try:
        import sentry_sdk
        sentry_sdk.capture_message(message, level=level)
    except Exception:  # pragma: no cover
        pass


# ---------------------------------------------------------------------------
# Evaluation (pure, testable)
# ---------------------------------------------------------------------------

def _max_retries(record: Dict[str, Any]) -> int:
    counts = record.get("slot_retry_counts") or {}
    try:
        return max(counts.values()) if counts else 0
    except (TypeError, ValueError):
        return 0


def evaluate_call(record: Dict[str, Any], signals: Optional[Dict[str, Any]] = None) -> List[Alert]:
    """Return the alerts a completed call triggers. Pure — no side effects.

    :param record: a CallLogger.build_record() dict.
    :param signals: optional extra flags observed on the live session, e.g.
        {"pipeline_error", "stt_error", "tts_error", "calendar_error",
         "transfer_sms_failed"}. Absent keys are treated as falsey.
    """
    s = signals or {}
    fired: List[str] = []

    if s.get("pipeline_error") or record.get("reason") == "pipeline_error":
        fired.append("pipeline_error")
    if s.get("stt_error") or s.get("tts_error"):
        fired.append("stt_tts_failure")
    if s.get("calendar_error") or record.get("calendar_error"):
        fired.append("booking_api_error")
    if record.get("transfer_attempted") and s.get("transfer_sms_failed"):
        fired.append("escalation_not_delivered")
    if (record.get("duration_s") or 0) < 15 and (record.get("turn_count") or 0) <= 1:
        fired.append("short_call")
    if _max_retries(record) >= 3:
        fired.append("retry_storm")

    return [_build_alert(cond, record) for cond in fired]


def _build_alert(condition: str, record: Dict[str, Any]) -> Alert:
    spec = _CONDITION_SPECS[condition]
    sid = record.get("call_sid")
    clinic = record.get("clinic_id") or "unknown clinic"
    caller = record.get("caller_number") or "unknown number"

    messages = {
        "pipeline_error":
            f"[Susie] Pipeline error on {clinic} call {sid}. Check Sentry/logs.",
        "stt_tts_failure":
            f"[Susie] Speech pipeline (STT/TTS) failure on {clinic} call {sid}.",
        "booking_api_error":
            f"[Susie] Booking API error on {clinic} call {sid} — the booking may not have gone through.",
        "escalation_not_delivered":
            f"[Susie] ESCALATION NOT DELIVERED on {clinic} call {sid}. A caller "
            f"({caller}) asked for a human but the alert SMS failed. Please call them back.",
        "short_call":
            f"short call {sid} ({clinic}, {record.get('duration_s')}s, "
            f"{record.get('turn_count')} turns)",
        "retry_storm":
            f"retry storm {sid} ({clinic}, max {_max_retries(record)} retries)",
    }
    return Alert(
        condition=condition,
        severity=spec["severity"],
        cadence=spec["cadence"],
        channels=spec["channels"],
        message=messages[condition],
    )


# ---------------------------------------------------------------------------
# Senders (guarded)
# ---------------------------------------------------------------------------

async def _send_operator_sms(message: str) -> bool:
    to = config.OBS_ALERT_SMS_TO
    if not to:
        return False
    try:
        sid = await send_sms(to=to, message=message)
        return bool(sid)
    except Exception as exc:  # pragma: no cover - defensive
        _log.error("[obs.alerts] operator SMS failed: %r", exc)
        return False


async def _post_slack(message: str) -> bool:
    url = config.OBS_SLACK_WEBHOOK
    if not url:
        return False
    try:
        import httpx
        async with httpx.AsyncClient(timeout=5) as http:
            await http.post(url, json={"text": message})
        return True
    except Exception as exc:  # pragma: no cover - defensive
        _log.error("[obs.alerts] Slack post failed: %r", exc)
        return False


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

async def dispatch(alerts: List[Alert]) -> Dict[str, List[str]]:
    """Route alerts to channels. No-op (returns {}) when disabled.

    IMMEDIATE alerts are sent now; DAILY alerts are appended to the roll-up buffer.
    Returns a map of channel -> [conditions sent] for observability/testing.
    """
    if not config.OBS_ALERTS_ENABLED or not alerts:
        return {}

    sent: Dict[str, List[str]] = {"sms": [], "slack": [], "sentry": [], "rollup": []}
    for alert in alerts:
        if alert.cadence == DAILY:
            _ROLLUP.append(alert)
            sent["rollup"].append(alert.condition)
            continue
        if "sentry" in alert.channels:
            _capture_message(alert.message, level="error")
            sent["sentry"].append(alert.condition)
        if "sms" in alert.channels and await _send_operator_sms(alert.message):
            sent["sms"].append(alert.condition)
        if "slack" in alert.channels and await _post_slack(alert.message):
            sent["slack"].append(alert.condition)
    return sent


async def route_call(record: Dict[str, Any], session: Optional[Dict[str, Any]] = None) -> Dict[str, List[str]]:
    """Evaluate a completed call and dispatch its alerts. Never raises.

    Safe to call unconditionally in the teardown path: returns {} instantly when
    OBS_ALERTS_ENABLED is off.
    """
    if not config.OBS_ALERTS_ENABLED:
        return {}
    try:
        sig = _signals_from_session(session or {})
        return await dispatch(evaluate_call(record, sig))
    except Exception as exc:  # pragma: no cover - defensive; must not break teardown
        sid = record.get("call_sid") if isinstance(record, dict) else None
        _log.error("[obs.alerts] route_call failed call_sid=%s: %r", sid, exc)
        return {}


def _signals_from_session(session: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "pipeline_error": session.get("pipeline_error"),
        "stt_error": session.get("stt_error") or session.get("stt_failed"),
        "tts_error": session.get("tts_error") or session.get("tts_failed"),
        "calendar_error": session.get("calendar_error"),
        "transfer_sms_failed": session.get("transfer_sms_failed"),
    }


# ---------------------------------------------------------------------------
# Daily roll-up
# ---------------------------------------------------------------------------

def get_rollup() -> List[Alert]:
    """Current buffered daily alerts (read-only view for tests / inspection)."""
    return list(_ROLLUP)


def reset_rollup() -> None:
    _ROLLUP.clear()


async def flush_daily_rollup() -> Optional[str]:
    """Send one summary of buffered DAILY alerts and clear the buffer.

    Returns the summary text sent, or None if disabled / nothing buffered.
    """
    if not config.OBS_ALERTS_ENABLED or not _ROLLUP:
        return None

    from collections import Counter
    counts = Counter(a.condition for a in _ROLLUP)
    header = "[Susie] Daily roll-up — low-severity call events:"
    lines = [f"  • {cond}: {n}" for cond, n in counts.items()]
    detail = [f"  - {a.message}" for a in _ROLLUP]
    summary = "\n".join([header, *lines, "", *detail])

    await _send_operator_sms(summary)
    await _post_slack(summary)
    reset_rollup()
    return summary
