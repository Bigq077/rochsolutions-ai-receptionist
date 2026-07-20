"""
app/obs/emailer.py
------------------
Minimal SMTP email sender for the daily digest (Phase 5 / tiered alerting).

Uses Python's stdlib smtplib over STARTTLS — no new dependency and no new service:
point it at Gmail, SendGrid, Mailgun, or any SMTP provider via the SMTP_* env vars.
Gated on config; never raises (a failed send is logged and swallowed so a cron run
never crashes). Only the daily digest uses this — urgent call-back alerts stay SMS.
"""
from __future__ import annotations

import logging
import smtplib
import ssl
from email.message import EmailMessage
from typing import Optional

from app import config

_log = logging.getLogger(__name__)


def is_configured() -> bool:
    """True when a recipient and SMTP credentials are all present."""
    return bool(
        config.OBS_DIGEST_EMAIL_TO
        and config.SMTP_HOST
        and config.SMTP_USER
        and config.SMTP_PASSWORD
    )


def send_email(subject: str, body: str, to: Optional[str] = None) -> bool:
    """Send a plain-text email. Returns True on send, False if not configured / failed.

    Synchronous (call via asyncio.to_thread from async code). Never raises.
    """
    recipient = to or config.OBS_DIGEST_EMAIL_TO
    if not (recipient and config.SMTP_HOST and config.SMTP_USER and config.SMTP_PASSWORD):
        return False
    try:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = config.SMTP_FROM or config.SMTP_USER
        msg["To"] = recipient
        msg.set_content(body)

        context = ssl.create_default_context()
        with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=20) as server:
            server.starttls(context=context)
            server.login(config.SMTP_USER, config.SMTP_PASSWORD)
            server.send_message(msg)
        _log.info("[obs.emailer] digest emailed to %s", recipient)
        return True
    except Exception as exc:  # pragma: no cover - network/SMTP failure
        _log.error("[obs.emailer] send failed to %s: %r", recipient, exc)
        return False
