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

from pathlib import Path

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
    "no_audio_call":            {"severity": "high",     "cadence": IMMEDIATE, "channels": ("sms", "slack")},
    "escalation_not_delivered": {"severity": "critical", "cadence": IMMEDIATE, "channels": ("sms", "slack")},
    "booking_not_written":      {"severity": "critical", "cadence": IMMEDIATE, "channels": ("sms", "slack")},
    "abandoned_call":           {"severity": "medium",   "cadence": IMMEDIATE, "channels": ("sms", "slack")},
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

# Runtime booking integrations that hand back a durable id we can check a
# spoken confirmation against.
#
# Read `booking_system` from the RESOLVED clinic config, never `booking.system`
# from clinic.json. They are different things and disagree:
#   booking.system  — the human-readable line in the knowledge base, e.g.
#                     "Carepatron", which is what the clinic tells patients.
#   booking_system  — the integration the code actually writes through.
# Joint Venture and Northgate say "Carepatron" and write to google_calendar.
_ID_BACKED_BOOKING_SYSTEMS = ("acuity", "google_calendar", "google_calendar_provisional")

_CALENDAR_BACKED_CACHE: Dict[str, bool] = {}


def calendar_backed(clinic_id: Optional[str]) -> bool:
    """True when this clinic's booking system returns a durable booking id.

    Resolved through clinic_config.get_clinic(), because the live clinics are
    not all clinic.json files: theorem_v3 — the Acuity site with real patients —
    is built in clinic_config.py as a deepcopy and has no directory of its own.
    An earlier version of this read app/clinics/<id>/clinic.json directly and so
    returned False for theorem_v3, silently disabling this alert for exactly the
    clinic it exists to protect.

    Fails CLOSED to False: an unknown clinic never alerts. A missed alert is
    recoverable by the weekly --obs triage; a false critical SMS on every
    successful booking would get the whole channel muted.
    """
    if not clinic_id:
        return False
    if clinic_id in _CALENDAR_BACKED_CACHE:
        return _CALENDAR_BACKED_CACHE[clinic_id]

    backed = False
    try:
        from app.clinic_config import CLINICS, get_clinic

        # get_clinic() falls back to a DEFAULT config for an id it does not
        # know, and that default reports booking_system=google_calendar — so
        # trusting it blind would fail OPEN and alert on every unknown clinic.
        # Only ask about a clinic that actually exists: either in the in-code
        # registry (theorem, theorem_v3, ...) or as a clinic.json directory
        # (jv_v1, northgate, vital_edge).
        known = clinic_id in (CLINICS or {}) or (
            Path(__file__).resolve().parents[1] / "clinics" / clinic_id / "clinic.json"
        ).is_file()
        if known:
            system = str(
                (get_clinic(clinic_id) or {}).get("booking_system") or ""
            ).lower()
            backed = system in _ID_BACKED_BOOKING_SYSTEMS
    except Exception as exc:  # pragma: no cover - config shape is the risk
        _log.warning("[obs.alerts] calendar_backed(%s) failed: %r", clinic_id, exc)
        backed = False

    _CALENDAR_BACKED_CACHE[clinic_id] = backed
    return backed


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
        {"pipeline_error", "stt_error", "tts_error", "no_audio_close",
         "calendar_error", "transfer_sms_failed"}. Absent keys are treated as falsey.
    """
    s = signals or {}
    fired: List[str] = []

    if s.get("pipeline_error") or record.get("reason") == "pipeline_error":
        fired.append("pipeline_error")
    if s.get("stt_error") or s.get("tts_error"):
        fired.append("stt_tts_failure")
    elif s.get("no_audio_close"):
        # Dead-air graceful close with no confirmed STT/TTS error — could be a
        # silently-dead STT session or a muted caller; the operator should see
        # it either way. elif: when stt_error is already flagged the
        # stt_tts_failure alert covers it, so don't double-SMS.
        fired.append("no_audio_call")
    if s.get("calendar_error") or record.get("calendar_error"):
        fired.append("booking_api_error")
    if record.get("transfer_attempted") and s.get("transfer_sms_failed"):
        fired.append("escalation_not_delivered")
    # Susie told the caller they were booked, but no calendar id came back.
    # booking_confirmed is set where the confirmation SENTENCE is composed
    # (flow.py CONFIRM_BOOKING), not where the write succeeds, so the two can
    # disagree - and 127 broad excepts in receptionist_tools.py can swallow the
    # failure in between. Nothing else notices: the transcript reads as a clean
    # booking, so the judge scores it well and booking_api_error needs an
    # explicitly raised calendar_error that never survived the except.
    # Gated on calendar_backed() so portal-handoff clinics never trip it.
    if (
        record.get("booking_confirmed")
        and not record.get("acuity_booking_id")
        and not record.get("calendar_event_id")
        and s.get("calendar_write_expected")
    ):
        fired.append("booking_not_written")
    # Record-level backstop for ghost calls: turn_count==0 means the caller never
    # produced a transcribed exchange at all. Normally no_audio_close (above) already
    # caught this via the 10s safety net's own graceful-close leg — but that leg needs
    # its own second fire to complete, and a caller who hangs up right after the
    # separate watchdog ladder retires (see call CAb2baf83044f90331f670055e5fb55b8c,
    # 2026-07-20) can end the call before the safety net gets there, leaving
    # no_audio_close unset. This checks the durable record instead of a live-session
    # flag, so it can't miss that race. `not fired` skips it whenever a more specific
    # condition (no_audio_call, stt_tts_failure, pipeline_error) already explains
    # the same call, so it never double-alerts.
    if not fired and (record.get("turn_count") or 0) == 0 and record.get("reason") not in (
        "booked", "transferred", "graceful_exit",
    ):
        fired.append("abandoned_call")
    elif (record.get("duration_s") or 0) < 15 and (record.get("turn_count") or 0) <= 1:
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
        "no_audio_call":
            f"[Susie] Dead-air call on {clinic} call {sid} — caller ({caller}) "
            f"heard Susie but nothing was ever transcribed "
            f"({record.get('duration_s')}s). Possible STT failure or muted caller.",
        "booking_not_written":
            f"[Susie] NO BOOKING WRITTEN on {clinic} call {sid}. Susie told "
            f"{caller} they were booked but no calendar id came back - the "
            f"appointment probably does not exist. Check the calendar and call "
            f"them back.",
        "escalation_not_delivered":
            f"[Susie] ESCALATION NOT DELIVERED on {clinic} call {sid}. A caller "
            f"({caller}) asked for a human but the alert SMS failed. Please call them back.",
        "abandoned_call":
            f"[Susie] Ghost call on {clinic} — {caller} called and hung up without "
            f"saying anything ({record.get('duration_s')}s, call {sid}).",
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


async def review_alert(message: str) -> bool:
    """Immediate operator alert for a low-quality call (Phase 3 judge bridge, §5.3).

    Gated on OBS_ALERTS_ENABLED like every other alert — a no-op when the router is
    off. Sends to the same operator SMS/Slack channels; never a clinic.
    """
    if not config.OBS_ALERTS_ENABLED:
        return False
    sms = await _send_operator_sms(message)
    slack = await _post_slack(message)
    return bool(sms or slack)


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
        "no_audio_close": session.get("no_audio_close"),
        "calendar_error": session.get("calendar_error"),
        "calendar_write_expected": calendar_backed(session.get("clinic_id")),
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
