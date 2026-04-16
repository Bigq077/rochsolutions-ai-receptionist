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

FIRST_VISIT_NOTE = (
    "As it's your first visit, please arrive 5 mins early and bring any "
    "relevant medical records or scan results.\n\n"
)
RETURNING_VISIT_NOTE = ""

BOOKING_CONFIRMATION_SMS = (
    "Hi {patient_name} 👋\n\n"
    "Your appointment at {clinic_name} is confirmed:\n\n"
    "📅 {appointment_date}\n"
    "⏰ {appointment_time}\n"
    "📍 {clinic_address}\n\n"
    "{first_visit_note}"
    "To confirm your booking, please reply with your full name.\n\n"
    "Maps: {maps_link}\n\n"
    "To reschedule, reply to this message or call us on {clinic_phone}.\n\n"
    "See you soon!\n— {clinic_name}"
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

    # First-visit note
    # TODO: wire up first_visit detection — no is_first_visit field exists in
    # session; using patient_type="NEW" as proxy.
    _pt         = (collected.get("patient_type") or "").upper()
    first_visit = (_pt == "NEW") if _pt else True   # default True if unknown
    note        = FIRST_VISIT_NOTE if first_visit else RETURNING_VISIT_NOTE

    # Clinic-level values — address resolved from selected_location for
    # two-clinic setups (theorem_v2); falls back to CLINIC_ADDRESS env var
    # for single-clinic deployments.
    clinic_name  = CLINIC_NAME
    clinic_phone = CLINIC_PHONE
    _loc = (session.get("selected_location") or "").lower()
    _location_addresses = {
        "alcester": "The Greig Leisure Centre, Kinwarton Road, Alcester, B49 6AD",
        "redditch": "51 Bromsgrove Road, Redditch, B97 4RH",
    }
    clinic_address = _location_addresses.get(_loc) or CLINIC_ADDRESS
    maps_link      = build_maps_link(clinic_address)

    body = BOOKING_CONFIRMATION_SMS.format(
        patient_name     = patient_name,
        clinic_name      = clinic_name,
        appointment_date = appointment_date,
        appointment_time = appointment_time,
        clinic_address   = clinic_address,
        first_visit_note = note,
        maps_link        = maps_link,
        clinic_phone     = clinic_phone,
    )
    return body
