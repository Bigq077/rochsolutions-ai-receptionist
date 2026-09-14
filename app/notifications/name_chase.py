"""The name chase — one story, told the same way in voice and text.

When a booking goes in under a name Susie could not hear (one best-effort
token, see turn_handler Gate 5n), the caller has been told on the call:

    "I'll pop what I've got on the booking, and I'll text you after the call
     so you can reply with the spelling."

Everything after that promise lives here, so the promise and what happens
next cannot drift apart again. Owner decisions, 13 Sep 2026, after
CAb5a26a10 booked "Still Gping":

  1. ONE text, immediately, that says the booking AND asks for the name --
     replacing a confirmation addressed to the wrong name that asked nothing,
     and a second "didn't catch your name" text that only one booking path
     ever sent.
  2. The calendar entry carries "(name to confirm)" so the front desk can see
     it without the SMS thread. Dropped when the reply lands.
  3. A reply updates the booking on WHICHEVER provider the clinic uses. The
     inbound handler only ever knew Acuity; on a Google Calendar clinic
     (northgate, JV) the texted name was never applied.
  4. The caller gets a thank-you when the reply lands.
  5. No reply: ONE nudge at +2 h, worded as a follow-up, never "to confirm
     your appointment" (which reads as not booked). Then stop.

Nothing here can fail a booking: every entry point is best-effort and logs.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

#: The tag on a calendar entry whose name is a placeholder.
NAME_TO_CONFIRM_TAG = " (name to confirm)"

_TAG_RE = re.compile(r"\s*\(name to confirm\)", re.IGNORECASE)


def when_label_for_sms(dt: Optional[datetime]) -> str:
    """'Monday 14 September at 8:50am' -- the shape the confirmation text uses,
    built without %-I so it is the same on every platform."""
    if not dt:
        return "your appointment"
    day = dt.strftime("%d").lstrip("0")
    t = dt.strftime("%I:%M%p").lstrip("0").lower()
    return f"{dt.strftime('%A')} {day} {dt.strftime('%B')} at {t}"


def is_doubled_name(name: Optional[str]) -> bool:
    """A "surname" that is the first name said again -- "Goner Goner". PURE.

    CA66bd0930 (14 Sep 2026, JV). STT turned the caller's name into a common
    word twice; the read-back asked "did you say Goner?", the caller said
    "goner", the surname question got "goner", and the booking, the text and
    the owner alert all went out as "Goner Goner". A second token that
    repeats the first is not a surname heard -- it is the same word not heard
    twice -- so the name counts as ONE best-effort token and is chased for
    the spelling like any other placeholder.
    """
    toks = [t.strip(".,'\"-").lower() for t in (name or "").split()]
    return len(toks) >= 2 and len(set(toks)) == 1


def collapse_doubled_name(name: Optional[str]) -> str:
    """The single token of a doubled name; any other name unchanged."""
    return (name or "").split()[0] if is_doubled_name(name) else (name or "")


def name_was_not_heard(session: Optional[Dict[str, Any]]) -> bool:
    """The engine could not hear the name and has said so: turn_handler
    Gate 5n (`_gate5n_exited`) or the name collector's best-effort exits
    (`needs_name_correction_sms`). The stored name is then a placeholder
    whatever its shape -- CAb5a26a10 booked "Still Gping", two tokens."""
    s = session or {}
    return bool(s.get("_gate5n_exited") or s.get("needs_name_correction_sms"))


def name_is_pending(
    patient_name: Optional[str], session: Optional[Dict[str, Any]] = None
) -> bool:
    """The name on the booking still needs the caller's reply.

    One token on record = only half a name was heard (the rule
    book_appointment has always used to create the pending record), OR the
    engine says the name was not heard at all. Stated once so the SMS, the
    tag and the record agree.
    """
    return len((patient_name or "").split()) < 2 or name_was_not_heard(session)


def tag_summary(summary: str) -> str:
    """Append the tag to a calendar title, once."""
    s = (summary or "").rstrip()
    return s if _TAG_RE.search(s) else s + NAME_TO_CONFIRM_TAG


def resolved_summary(summary: str, placeholder: str, full_name: str) -> str:
    """The calendar title with the placeholder replaced and the tag dropped.

    Replaces the first whole-word occurrence of the placeholder; if the
    placeholder is not in the title (someone edited it), the tag alone is
    dropped and the name is left where the clinic put it.
    """
    s = _TAG_RE.sub("", summary or "")
    if placeholder:
        s2 = re.sub(rf"\b{re.escape(placeholder)}\b", full_name, s, count=1)
        if s2 != s:
            return s2.strip()
    return s.strip()


def booking_text_name_pending(
    *,
    clinic_name: str,
    when_label: str,
    location: str,
    clinic_phone: str,
) -> str:
    """The one text, for a caller with no session (legacy callers of
    send_booking_confirmation only). On a live call the confirmation is
    sms_templates.build_sms(session, name_not_heard=True), which keeps the
    address, Maps link, first-visit note and the home-visit / remote bodies
    and swaps in NAME_NOT_HEARD_NOTE for the greeting-by-name."""
    loc = f" at our {location} clinic" if location else ""
    return (
        f"Hi, this is Susie from {clinic_name}. You're booked for {when_label}"
        f"{loc}. I didn't quite catch your name on the call — please reply "
        "with your first name and surname and I'll update the booking. "
        f"Need to reschedule? Call us on {clinic_phone}."
    )


def nudge_text(*, when_label: str) -> str:
    """The one follow-up. A follow-up, not a reminder; never 'to confirm'."""
    return (
        f"Hi, it's Susie — I still need your name for the booking on "
        f"{when_label}. Just reply to this text with your first name and "
        "surname."
    )


def close_line(when_label: str) -> str:
    """What Susie says after the write. Owner, 13 Sep 2026: remind them of
    the situation, and that the text is how they confirm."""
    return (
        f"All booked for {when_label}. I didn't quite catch your name, so I'm "
        "texting you now — just reply with your first name and surname and "
        "I'll put it on the booking."
    )


def close_instruction(when_label: str) -> str:
    """The tool-result steer for the model's close, verbatim. Lives on the
    booking result so the LLM path and the deterministic path say the same."""
    return (
        "The caller's name was NOT heard on this call; a placeholder is on the "
        "booking and a text asking for their name is being sent now. Close "
        f"with EXACTLY: \"{close_line(when_label)}\" -- do not say the "
        "placeholder name aloud, and do not ask for the name again."
    )


def thanks_text(*, first_name: str, when_label: str) -> str:
    return f"Thanks {first_name} — booking updated. See you on {when_label}."


async def start(
    *,
    session: Dict[str, Any],
    clinic: Dict[str, Any],
    phone: str,
    patient_name: str,
    provider: str,
    appointment_id: str,
    when_label: str,
    location: str,
    calendar_id: str = "",
    event_summary: str = "",
) -> bool:
    """Open the chase for a booking just written. Best-effort, returns whether
    a record was created. Called by BOTH booking paths under name_is_pending.

    `_name_chase_open` is set before anything can fail: it tells the
    deterministic CONFIRM_BOOKING close that the ONE text is going with the
    booking confirmation, so its own second "didn't catch your name" text
    must not follow -- true whether or not Redis took the record.
    """
    session["_name_chase_open"] = True
    try:
        from app.flows.triage_legacy import normalize_phone
        from app.storage.redis_store import create_pending_name_confirmation

        norm_phone = normalize_phone(phone)
        first = (patient_name or "").split()[0] if (patient_name or "").strip() else ""
        clinic_id = clinic.get("clinic_id") or session.get("clinic_id") or ""
        await create_pending_name_confirmation(
            phone=norm_phone,
            first_name=first,
            appointment_id=str(appointment_id or ""),
            location=location or "",
            provider=provider,
            calendar_id=calendar_id or "",
            event_summary=event_summary or "",
            clinic_id=clinic_id,
            when_label=when_label or "",
        )
        logger.info(
            "[name_chase] opened: phone=%r provider=%s appt=%r placeholder=%r",
            norm_phone, provider, appointment_id, first,
        )
        try:
            from app.clinic_config import twilio_number_for_clinic
            from app.notifications.scheduler import schedule_name_confirm_reminder

            await schedule_name_confirm_reminder(
                phone=norm_phone,
                first_name=first,
                from_number=twilio_number_for_clinic(clinic_id) if clinic_id else None,
                when_label=when_label or "",
            )
        except Exception as e:  # pragma: no cover - never fails the booking
            logger.warning("[name_chase] nudge schedule failed (non-fatal): %r", e)
        return True
    except Exception as e:  # pragma: no cover - never fails the booking
        logger.warning("[name_chase] open failed (non-fatal): %r", e)
        return False


async def apply_reply(pending: Dict[str, Any], first_name: str, last_name: str) -> bool:
    """Write the texted name onto the booking, on the provider it lives in.
    Returns True when the provider accepted the update."""
    provider = (pending.get("provider") or "acuity").lower()
    appointment_id = pending.get("appointment_id") or ""
    full = f"{first_name} {last_name}".strip()
    if not appointment_id:
        logger.warning("[name_chase] reply with no appointment id: %r", pending)
        return False
    try:
        if provider.startswith("google"):
            import asyncio as _asyncio

            from app.tools.calendar_google import update_event
            from app.tools.receptionist_tools import _get_tokens

            toks = await _get_tokens(pending.get("clinic_id") or None)
            if not toks:
                logger.warning("[name_chase] no calendar tokens for %r", pending.get("clinic_id"))
                return False
            new_summary = resolved_summary(
                pending.get("event_summary") or "", pending.get("first_name") or "", full
            )
            await _asyncio.to_thread(
                update_event, toks, appointment_id, new_summary, None,
                pending.get("calendar_id") or "primary",
            )
            logger.info("[name_chase] calendar event %r renamed: %r", appointment_id, new_summary)
            return True
        # Acuity: firstName always; lastName only when the reply carried one.
        from app.tools.receptionist_tools import _get_acuity_adapter

        adapter = _get_acuity_adapter()
        if not adapter:
            logger.warning("[name_chase] no Acuity adapter")
            return False
        payload: Dict[str, Any] = {"firstName": first_name}
        if last_name:
            payload["lastName"] = last_name
        await adapter._request_with_retry(
            "PUT", f"/appointments/{appointment_id}", json=payload,
            allow_retry=False,
        )
        logger.info("[name_chase] Acuity appt %r updated: %r", appointment_id, full)
        return True
    except Exception as e:
        logger.warning("[name_chase] provider update failed (non-fatal): appt=%r err=%r",
                       appointment_id, e)
        return False
