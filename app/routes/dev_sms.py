# app/routes/dev_sms.py
"""Read-only view of the SMS the cost guard captured instead of sending.

`SMS_TEST_NUMBERS` routes a listed handset to a local inbox rather than to
Twilio (app/notifications/sms_guard.py). Without somewhere to read that inbox
the capture is invisible, and "did the text go?" becomes unanswerable during a
test call — which is the question the guard exists to make cheap.

GATING — two conditions, both required, and neither is new:

  1. ADMIN_KEY + ?key=, exactly the mechanism app/routes/admin.py already uses
     for its own read-only checks. Not a new scheme.
  2. SMS_TEST_NUMBERS must be non-empty.

(2) is the one that matters. The inbox only ever holds messages for numbers on
that list, and the list must never be set on a live service — so on production
this route has nothing to show and now cannot be reached at all, even if
ADMIN_KEY leaks. A route that is inert by construction beats one that is inert
by convention.

The bodies here are POST-sanitiser and are addressed to the developer's own
handset by definition, so this exposes no patient text. It is nonetheless
gated, because "no patient data today" is a property of the config rather than
of the code.
"""
import os

from fastapi import APIRouter

from app.notifications.sms_guard import inbox

router = APIRouter(prefix="/dev")

ADMIN_KEY = os.getenv("ADMIN_KEY", "")


@router.get("/sms")
async def dev_sms(key: str = ""):
    """Messages the guard captured instead of sending, newest first.

    Usage: GET /dev/sms?key=YOUR_ADMIN_KEY
    """
    if not os.getenv("SMS_TEST_NUMBERS", "").strip():
        # Not an error: on a live service this is the CORRECT state.
        return {"ok": False, "error": "sms test capture is not enabled"}
    if not ADMIN_KEY or key != ADMIN_KEY:
        return {"ok": False, "error": "unauthorized"}

    messages = inbox()
    return {
        "ok": True,
        "count": len(messages),
        "segments_saved": sum(int(m.get("segments") or 0) for m in messages),
        "messages": messages,
    }
