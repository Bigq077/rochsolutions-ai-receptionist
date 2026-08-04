"""Which channels can reach the CLINIC OWNER on a test call?

Run this before any test-calling session against a live clinic config. It
answers one question — "if Jules dials this service now, what does the owner
receive?" — by reading the same env vars and clinic.json keys the runtime reads.

Written 2026-08-04 for Vital Edge: Jonathan's mobile is both
`owner_notification_sms` AND `transfer_phone`, his Gmail is both the digest
recipient AND the booking calendar, so four different surfaces converge on one
person. `SMS_ENABLED=false` covers only two of them.

Usage:
    python -m scripts.audit_outbound_to_owner                # clinic from env
    python -m scripts.audit_outbound_to_owner vital_edge     # explicit

Exit 0 = nothing reaches the owner. Exit 1 = at least one live channel.
Read-only: reads config, sends nothing.
"""
from __future__ import annotations

import os
import sys

from app.clinic_config import get_clinic

TRUTHY = ("true", "1", "yes", "on")


def _flag(name: str, default: str = "false") -> bool:
    return (os.getenv(name, default) or "").strip().lower() in TRUTHY


def _env_set(name: str) -> bool:
    return bool((os.getenv(name) or "").strip())


def audit(clinic_id: str) -> int:
    clinic = get_clinic(clinic_id)
    op = clinic.get("operational", {}) or {}
    rows = []

    # ── 1. Owner booking-ping SMS ────────────────────────────────────────────
    owner_sms = clinic.get("owner_notification_sms") or op.get("owner_notification_sms")
    sms_on = _flag("SMS_ENABLED")
    rows.append((
        "owner booking-ping SMS",
        owner_sms or "(not configured)",
        sms_on and bool(owner_sms),
        "SMS_ENABLED=true" if sms_on else "blocked by SMS_ENABLED=false",
    ))

    # ── 2. obs operator alerts (SMS leg) ─────────────────────────────────────
    # Double-gated: OBS_ALERTS_ENABLED, then the same SMS_ENABLED kill switch,
    # because alerts.py sends via app.notifications.sms.send_sms.
    alerts_on = _flag("OBS_ALERTS_ENABLED")
    alert_to = (os.getenv("OBS_ALERT_SMS_TO") or "").strip()
    rows.append((
        "obs operator alert SMS",
        alert_to or "(OBS_ALERT_SMS_TO unset)",
        alerts_on and sms_on and bool(alert_to),
        "OBS_ALERTS_ENABLED + SMS_ENABLED both true" if (alerts_on and sms_on)
        else "blocked by OBS_ALERTS_ENABLED/SMS_ENABLED",
    ))

    # ── 3. obs alerts (Slack leg) — NOT gated by SMS_ENABLED ─────────────────
    slack = _env_set("OBS_SLACK_WEBHOOK")
    rows.append((
        "obs operator alert Slack",
        "OBS_SLACK_WEBHOOK" if slack else "(unset)",
        alerts_on and slack,
        "posts to Slack — SMS_ENABLED does NOT gate this leg",
    ))

    # ── 4. End-of-day digest email ───────────────────────────────────────────
    # There is no EMAIL_ENABLED kill switch. The digest is on unless
    # DIGEST_ENABLED says otherwise, and clinic.json defaults it to True.
    dcfg = clinic.get("digest") or op.get("digest") or {}
    env_digest = os.getenv("DIGEST_ENABLED")
    digest_on = (
        (env_digest or "").strip().lower() in TRUTHY if env_digest is not None
        else bool(dcfg.get("enabled", True))
    )
    recipients = os.getenv("DIGEST_EMAIL_TO") or dcfg.get("email_to") or ""
    smtp = _env_set("SMTP_HOST") and _env_set("SMTP_USERNAME")
    rows.append((
        "end-of-day digest EMAIL",
        str(recipients) or "(no recipient)",
        digest_on and smtp and bool(recipients),
        "NO SMS_ENABLED gate — set DIGEST_ENABLED=false to stop it"
        if digest_on else "DIGEST_ENABLED=false",
    ))

    # ── 5. Live transfer — dials the owner ───────────────────────────────────
    transfer = op.get("transfer_phone") or clinic.get("transfer_phone")
    rows.append((
        "live transfer (RINGS the owner)",
        transfer or "(not configured)",
        bool(transfer),
        "no kill switch — a caller asking for a human dials this number",
    ))

    # ── 6. The booking itself ────────────────────────────────────────────────
    cal = op.get("calendar_id") or clinic.get("calendar_id")
    provisional = op.get("booking_system") == "google_calendar_provisional"
    rows.append((
        "calendar event on owner's calendar",
        cal or "(default)",
        bool(cal),
        "every test booking is a real event the owner can see"
        + (" (PENDING, provisional clinic)" if provisional else ""),
    ))

    print(f"clinic_id : {clinic_id}")
    print(f"MEDIA_STREAMS_CLINIC_ID : {os.getenv('MEDIA_STREAMS_CLINIC_ID') or '(unset)'}")
    print("=" * 78)
    print(f"{'channel':38s} {'reaches owner?':16s} target")
    print("-" * 78)
    live = 0
    for name, target, is_live, note in rows:
        if is_live:
            live += 1
        mark = "*** YES ***" if is_live else "no"
        print(f"{name:38s} {mark:16s} {target}")
        print(f"{'':38s} {'':16s} ^ {note}")
    print("=" * 78)
    if live:
        print(f"{live} channel(s) would reach the owner. Silence them before test calls.")
    else:
        print("No channel reaches the owner with this configuration.")
    return 1 if live else 0


if __name__ == "__main__":
    cid = sys.argv[1] if len(sys.argv) > 1 else (
        os.getenv("MEDIA_STREAMS_CLINIC_ID") or "vital_edge"
    )
    sys.exit(audit(cid.strip().lower()))
