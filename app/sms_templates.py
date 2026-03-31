# app/sms_templates.py
"""
Booking confirmation SMS template for Susie.

All clinic-specific values come from environment variables (MARK_REVIEW).
build_sms(session) is the single entry point — takes a full session dict
and returns a TTS-ready SMS body string.
"""
import os
import urllib.parse

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
    "📅 {appointment_day}\n"
    "⏰ {appointment_time}\n"
    "📍 {clinic_address}\n\n"
    "{first_visit_note}"
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

    Adapted field names (from pre-task audit):
      - patient name  → session["collected"]["name"]   (first word only)
      - appointment   → session["selected_slot_label"] (e.g. "Friday 18 July at 2:30pm")
      - first visit   → session["collected"]["patient_type"] == "NEW"

    # TODO: wire up first_visit detection — no is_first_visit field exists in
    # session; using patient_type="NEW" as proxy. Default True (new patient)
    # when patient_type is absent so callers always get the arrival note.
    """
    collected = session.get("collected") or {}

    # Patient first name
    name_raw     = (collected.get("name") or "").strip()
    patient_name = name_raw.split()[0] if name_raw else "there"

    # Appointment day / time — split "Friday 18 July at 2:30pm" → ("Friday 18 July", "2:30pm")
    slot_label = (session.get("selected_slot_label") or "").strip()
    if " at " in slot_label:
        appointment_day, appointment_time = slot_label.rsplit(" at ", 1)
    else:
        appointment_day  = slot_label
        appointment_time = ""

    # First-visit note
    # TODO: wire up first_visit detection — no is_first_visit field exists in
    # session; using patient_type="NEW" as proxy.
    _pt         = (collected.get("patient_type") or "").upper()
    first_visit = (_pt == "NEW") if _pt else True   # default True if unknown
    note        = FIRST_VISIT_NOTE if first_visit else RETURNING_VISIT_NOTE

    # Clinic-level values from env
    clinic_name    = CLINIC_NAME
    clinic_address = CLINIC_ADDRESS
    clinic_phone   = CLINIC_PHONE
    maps_link      = build_maps_link(clinic_address)

    body = BOOKING_CONFIRMATION_SMS.format(
        patient_name     = patient_name,
        clinic_name      = clinic_name,
        appointment_day  = appointment_day,
        appointment_time = appointment_time,
        clinic_address   = clinic_address,
        first_visit_note = note,
        maps_link        = maps_link,
        clinic_phone     = clinic_phone,
    )
    return body
