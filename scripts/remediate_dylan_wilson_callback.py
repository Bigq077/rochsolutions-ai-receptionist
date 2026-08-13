#!/usr/bin/env python3
"""One-shot: text Jonathan about the missed Dylan Wilson callback.

CAc36368cbeb, 2026-08-13 — Susie promised a callback and never notified anyone.
Run this on a host that has Twilio creds + SMS_ENABLED (Render shell is fine):

    SMS_ENABLED=true python scripts/remediate_dylan_wilson_callback.py

Idempotent enough to re-run; Jonathan may get a duplicate if he already got one
by hand — check with him first if unsure.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path


def _load_dotenv() -> None:
    env = Path(__file__).resolve().parent.parent / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8", errors="ignore").splitlines():
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


async def main() -> int:
    _load_dotenv()
    os.environ["SMS_ENABLED"] = "true"

    from app.clinic_config import get_clinic
    from app.notifications.sms import send_sms
    from app.tools.receptionist_tools import _owner_callback_number

    clinic = get_clinic("vital_edge")
    to = _owner_callback_number(clinic)
    if not to:
        print("No owner number on vital_edge", file=sys.stderr)
        return 1

    msg = (
        "📞 CALLBACK — Dylan Wilson. Number: +13102695437. "
        "wants a quick chat with Jonathan before booking. "
        "(Missed auto-notify on CAc36368cbeb — sending now.)"
    )
    sid = await send_sms(to=to, message=msg)
    print(f"to=***{to[-4:]} sid={sid or 'NONE/suppressed'}")
    return 0 if sid else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
