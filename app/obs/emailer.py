"""
app/obs/emailer.py
------------------
Email transport for the daily digest — a thin adapter over the sender this
deployment already has.

DIVERGENCE FROM `main` (deliberate): upstream this module speaks smtplib directly
and reads its own SMTP_HOST/SMTP_USER/SMTP_PASSWORD. This branch already ships
app/notifications/email.py — a transactional sender used by the end-of-day booking
digest — which reads SMTP_HOST / SMTP_USERNAME / SMTP_PASSWORD / SMTP_PORT /
SMTP_FROM / SMTP_FROM_NAME / SMTP_USE_SSL. Re-implementing SMTP here would give the
service two senders keyed on two different username variables (SMTP_USER vs
SMTP_USERNAME), so configuring one would silently leave the other dead.

So: keep the public API (`is_configured()` / `send_email()`) exactly as
app/obs/digest.py expects, and delegate the transport. One set of SMTP creds, and
the booking digest and the call digest can never disagree about them.

Never raises — a failed send is logged and swallowed so a scheduled run never
crashes.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from app import config

_log = logging.getLogger(__name__)


def is_configured() -> bool:
    """True when a recipient AND working SMTP credentials are both present."""
    if not config.OBS_DIGEST_EMAIL_TO:
        return False
    try:
        from app.notifications.email import _smtp_config
        return _smtp_config() is not None
    except Exception:  # pragma: no cover - defensive
        return False


def send_email(subject: str, body: str, to: Optional[str] = None) -> bool:
    """Send a plain-text email. Returns True on send, False if unconfigured/failed.

    Synchronous by contract (app/obs/digest.py calls it via asyncio.to_thread), but
    the underlying sender is async — so drive it on a private event loop inside this
    worker thread. Never raises.
    """
    recipient = to or config.OBS_DIGEST_EMAIL_TO
    if not recipient:
        return False
    try:
        from app.notifications.email import send_email as _send

        sent = asyncio.run(_send(to=recipient, subject=subject, text=body))
        if sent:
            _log.info("[obs.emailer] digest emailed to %s", recipient)
        return bool(sent)
    except Exception as exc:  # pragma: no cover - network/SMTP failure
        _log.error("[obs.emailer] send failed to %s: %r", recipient, exc)
        return False
