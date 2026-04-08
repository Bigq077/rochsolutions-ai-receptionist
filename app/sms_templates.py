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
    "📅 {appointment_date}\n"
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

    # Format full appointment date as "April 8, 2026"
    # Extract month and day from appointment_day (e.g., "Friday 18 July")
    from datetime import datetime
    appointment_date = appointment_day  # fallback to day for now
    try:
        # Try to parse "Day DD Month" format and add current/next year
        parts = appointment_day.split()
        if len(parts) >= 3:
            day_num = parts[1]
            month_name = parts[2]
            current_year = datetime.now().year
            # Parse the date to get the proper formatted string
            date_obj = datetime.strptime(f"{day_num} {month_name} {current_year}", "%d %B %Y")
            # If the date is in the past, assume it's next year
            if date_obj < datetime.now():
                date_obj = datetime.strptime(f"{day_num} {month_name} {current_year + 1}", "%d %B %Y")
            appointment_date = date_obj.strftime("%B %d, %Y")  # e.g., "April 08, 2026"
            # Remove leading zero from day
            appointment_date = appointment_date.replace(" 0", " ")
    except (ValueError, IndexError):
        pass  # Use appointment_day as fallback

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
