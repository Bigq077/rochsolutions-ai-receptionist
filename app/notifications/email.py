"""
Transactional email sender (SMTP).

Mirrors the SMS service's design principle: **fail gracefully, never crash the
app**. If SMTP is not configured (no SMTP_HOST/credentials), send_email() logs a
warning and returns False rather than raising — so features that depend on email
(e.g. the end-of-day booking digest) degrade to a no-op until creds are set.

Transport is plain stdlib smtplib (works with Gmail, SendGrid, Mailgun, SES SMTP,
etc.). The blocking send runs in a worker thread so callers can await it.

Required env to actually send:
    SMTP_HOST           e.g. smtp.gmail.com / smtp.sendgrid.net
    SMTP_USERNAME       SMTP login (for SendGrid this is the literal "apikey")
    SMTP_PASSWORD       SMTP password / API key
Optional:
    SMTP_PORT           default 587 (STARTTLS). Use 465 for implicit SSL.
    SMTP_FROM           From address; defaults to SMTP_USERNAME.
    SMTP_FROM_NAME      Display name on the From header; default "Susie".
    SMTP_USE_SSL        "true" forces implicit SSL regardless of port.
"""

from __future__ import annotations

import asyncio
import logging
import os
import smtplib
from email.message import EmailMessage
from email.utils import formataddr
from typing import Optional

logger = logging.getLogger(__name__)


def _smtp_config() -> Optional[dict]:
    """Return SMTP config from env, or None if not configured."""
    host = (os.getenv("SMTP_HOST") or "").strip()
    username = (os.getenv("SMTP_USERNAME") or "").strip()
    password = (os.getenv("SMTP_PASSWORD") or "").strip()
    if not host or not username or not password:
        return None

    port = int(os.getenv("SMTP_PORT", "587") or "587")
    use_ssl = (os.getenv("SMTP_USE_SSL", "").strip().lower() in ("1", "true", "yes")) or port == 465
    from_addr = (os.getenv("SMTP_FROM") or username).strip()
    from_name = (os.getenv("SMTP_FROM_NAME") or "Susie").strip()
    return {
        "host": host,
        "port": port,
        "username": username,
        "password": password,
        "use_ssl": use_ssl,
        "from_addr": from_addr,
        "from_name": from_name,
    }


def _send_blocking(cfg: dict, to_addrs: list[str], subject: str, text: str, html: Optional[str]) -> bool:
    msg = EmailMessage()
    msg["From"] = formataddr((cfg["from_name"], cfg["from_addr"]))
    msg["To"] = ", ".join(to_addrs)
    msg["Subject"] = subject
    msg.set_content(text)
    if html:
        msg.add_alternative(html, subtype="html")

    try:
        if cfg["use_ssl"]:
            with smtplib.SMTP_SSL(cfg["host"], cfg["port"], timeout=30) as server:
                server.login(cfg["username"], cfg["password"])
                server.send_message(msg)
        else:
            with smtplib.SMTP(cfg["host"], cfg["port"], timeout=30) as server:
                server.starttls()
                server.login(cfg["username"], cfg["password"])
                server.send_message(msg)
        logger.info("Email sent to %s (subject=%r)", to_addrs, subject)
        return True
    except Exception as e:
        logger.error("Failed to send email to %s: %r", to_addrs, e, exc_info=True)
        return False


async def send_email(
    to: str | list[str],
    subject: str,
    text: str,
    html: Optional[str] = None,
) -> bool:
    """
    Send an email. Returns True on success, False if SMTP is unconfigured or the
    send fails (never raises). Accepts a single recipient or a list.
    """
    cfg = _smtp_config()
    if not cfg:
        logger.warning(
            "Cannot send email (subject=%r) — SMTP not configured (set SMTP_HOST/"
            "SMTP_USERNAME/SMTP_PASSWORD). This is a no-op, app continues.",
            subject,
        )
        return False

    to_addrs = [to] if isinstance(to, str) else list(to)
    to_addrs = [a.strip() for a in to_addrs if a and a.strip()]
    if not to_addrs:
        logger.warning("send_email: no valid recipient — skipping (subject=%r)", subject)
        return False

    return await asyncio.to_thread(_send_blocking, cfg, to_addrs, subject, text, html)
