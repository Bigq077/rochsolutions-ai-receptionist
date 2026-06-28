# app/sms_templates.py
"""
Booking confirmation SMS template for Susie.

All clinic-specific values come from environment variables (MARK_REVIEW).
build_sms(session) is the single entry point — takes a full session dict
and returns a TTS-ready SMS body string.
"""
import os
import re
import urllib.parse
from datetime import datetime

CLINIC_NAME    = os.getenv("CLINIC_NAME",    "the clinic")   # MARK_REVIEW
CLINIC_ADDRESS = os.getenv("CLINIC_ADDRESS", "")              # MARK_REVIEW
CLINIC_PHONE   = os.getenv("CLINIC_PHONE",   "")              # MARK_REVIEW

# Branded short links per location (MARK_REVIEW — set up the redirects before
# go-live). Each must redirect to that location's Google Maps page. Plain SMS
# cannot mask a URL behind anchor text, so a short branded link is the clean,
# clickable alternative to a long Google Maps URL. Env-overridable so the real
# short domain can be supplied without a code change.
MAPS_SHORT_URL_ALCESTER = os.getenv("MAPS_SHORT_URL_ALCESTER", "https://bit.ly/theorem-alcester")
MAPS_SHORT_URL_REDDITCH = os.getenv("MAPS_SHORT_URL_REDDITCH", "https://bit.ly/theorem-redditch")
_location_short_links = {
    "alcester": MAPS_SHORT_URL_ALCESTER,
    "redditch": MAPS_SHORT_URL_REDDITCH,
}

# Fees / surcharges note (shown to new and unknown callers in place of the old
# first-visit arrival note). Wording confirmed by Mark/Quentin.
FEES_NOTE = (
    "Fees: £85, credit/debit cards taken.\n"
    "Surcharges: +£45 for Laser or Shockwave Therapy.\n"
    "Cancellations with less than 24hrs notice are charged.\n"
    "Evening appointments £10 (from 5pm).\n\n"
)
# Returning-on-plan patients: warm welcome-back, no arrival-time fuss
RETURNING_PLAN_NOTE = "Great to have you back — see you at your appointment! 🙌\n\n"

BOOKING_CONFIRMATION_SMS = (
    "Hi {patient_name} 👋\n\n"
    "Your appointment at {clinic_name} is confirmed:\n\n"
    "📅 {appointment_date}\n"
    "⏰ {appointment_time}\n"
    "📍 {clinic_address}\n\n"
    "{info_note}"
    "{full_name_request}"
    "Maps: {maps_link}\n\n"
    "See you soon!\n— {clinic_name}"
)

# Explicit full-name request — inserted only when the caller's full name is
# still pending (only first name was captured on the call).
FULL_NAME_REQUEST_NOTE = (
    "Please reply to this message with your full name so we can complete "
    "your booking details.\n\n"
)


def build_maps_link(address: str) -> str:
    """Return a Google Maps URL for address, or '' if address is empty."""
    if not address:
        return ""
    return f"https://maps.google.com/?q={urllib.parse.quote(address)}"


def build_sms(session: dict) -> str:
    """
    Build the booking confirmation SMS body from a session dict.

    Slot resolution order:
      1. session["selected_slot"]        — ISO datetime, always set by live flow
      2. session["selected_slot_speech"] — human-readable, always set by live flow
      3. session["selected_slot_label"]  — legacy key, never written by live flow

    Patient name  → session["collected"]["name"]
    First visit   → session["collected"]["patient_type"] == "NEW"
                    (defaults True when absent so new callers always get arrival note)
    """
    collected = session.get("collected") or {}

    # Patient first name
    name_raw     = (collected.get("name") or "").strip()
    patient_name = name_raw.split()[0] if name_raw else "there"

    # Slot resolution — priority:
    #   1. selected_slot (ISO datetime) — always set by the live call flow, most reliable
    #   2. selected_slot_speech (e.g. "Tuesday 15th April at 2:30pm") — also always set
    #   3. selected_slot_label — legacy key, never written by live flow (kept as last resort)
    _slot_iso    = (session.get("selected_slot") or "").strip()
    _slot_speech = (session.get("selected_slot_speech") or "").strip()
    slot_label   = (session.get("selected_slot_label") or _slot_speech or "").strip()

    appointment_date = ""
    appointment_time = ""

    # Path 1: parse ISO datetime directly — exact and unambiguous
    if _slot_iso:
        try:
            _slot_dt = datetime.fromisoformat(_slot_iso)
            appointment_date = _slot_dt.strftime("%B %d, %Y").replace(" 0", " ")
            appointment_time = _slot_dt.strftime("%I:%M%p").lstrip("0").lower()
        except (ValueError, TypeError):
            pass

    # Path 2: parse from human-readable speech label (e.g. "Tuesday 15th April at 2:30pm")
    if not appointment_date and slot_label:
        if " at " in slot_label:
            appointment_day, appointment_time = slot_label.rsplit(" at ", 1)
        else:
            appointment_day = slot_label
        try:
            parts = appointment_day.split()
            if len(parts) >= 3:
                # Strip ordinal suffix: "15th" → "15", "3rd" → "3"
                day_num    = re.sub(r"(st|nd|rd|th)$", "", parts[1], flags=re.IGNORECASE)
                month_name = parts[2]
                current_year = datetime.now().year
                date_obj = datetime.strptime(f"{day_num} {month_name} {current_year}", "%d %B %Y")
                if date_obj < datetime.now():
                    date_obj = datetime.strptime(
                        f"{day_num} {month_name} {current_year + 1}", "%d %B %Y"
                    )
                appointment_date = date_obj.strftime("%B %d, %Y").replace(" 0", " ")
        except (ValueError, IndexError):
            appointment_date = appointment_day  # raw fallback

    # Final fallback — should never reach here on a real call
    if not appointment_date:
        appointment_date = slot_label or "—"
    if not appointment_time:
        appointment_time = "—"

    # Note selection. Every caller — new, returning, or on a plan — gets the
    # fees note. Returning-on-plan patients also get a warm welcome-back line
    # ahead of it.
    if session.get("returning_plan"):
        note = RETURNING_PLAN_NOTE + FEES_NOTE
    else:
        note = FEES_NOTE

    # Clinic-level values — address resolved from selected_location for
    # two-clinic setups (theorem_v2); falls back to CLINIC_ADDRESS env var
    # for single-clinic deployments.
    clinic_name  = CLINIC_NAME
    _loc = (session.get("selected_location") or "").lower()
    _location_addresses = {
        "alcester": "The Greig Leisure Centre, Kinwarton Road, Alcester, B49 6AD",
        "redditch": "51 Bromsgrove Road, Redditch, B97 4RH",
    }
    clinic_address = _location_addresses.get(_loc) or CLINIC_ADDRESS

    # Maps link: prefer the location's branded short link; fall back to the full
    # Google Maps URL if no short link is configured for this location.
    maps_link = _location_short_links.get(_loc) or build_maps_link(clinic_address)

    # Pending full name?  Only the caller's first name is collected on the
    # call, so the stored name is typically a single token.  When a full name
    # has already been confirmed (contains a space) the SMS must NOT re-ask.
    _full_name_confirmed = bool(name_raw) and (" " in name_raw)
    pending_full_name    = not _full_name_confirmed
    full_name_request    = FULL_NAME_REQUEST_NOTE if pending_full_name else ""

    import logging as _logging_sms
    _logging_sms.getLogger(__name__).info(
        "BOOKING_CONFIRM pending_full_name=%s → SMS %s full-name instruction",
        pending_full_name,
        "requests" if pending_full_name else "omits",
    )

    body = BOOKING_CONFIRMATION_SMS.format(
        patient_name      = patient_name,
        clinic_name       = clinic_name,
        appointment_date  = appointment_date,
        appointment_time  = appointment_time,
        clinic_address    = clinic_address,
        info_note         = note,
        full_name_request = full_name_request,
        maps_link         = maps_link,
    )
    return body
