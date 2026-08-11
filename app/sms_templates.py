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
# Returning-on-plan patients: warm welcome-back, no arrival-time fuss
RETURNING_PLAN_NOTE = "Great to have you back — see you at your appointment! 🙌\n\n"
RETURNING_VISIT_NOTE = ""

BOOKING_CONFIRMATION_SMS = (
    "Hi {patient_name} 👋\n\n"
    "Your appointment at {clinic_name} is confirmed:\n\n"
    "📅 {appointment_date}\n"
    "⏰ {appointment_time}\n"
    "📍 {clinic_address}\n\n"
    "{first_visit_note}"
    "{fees_note}"
    "{full_name_request}"
    "Maps: {maps_link}\n\n"
    "To reschedule, reply to this message or call us on {clinic_phone}.\n\n"
    "See you soon!\n— {clinic_name}"
)

# Home-visit confirmation — the visit is at the patient's home, so no clinic
# address / Maps link; instead ask them to text their full address + postcode.
HOME_VISIT_CONFIRMATION_SMS = (
    "Hi {patient_name} 👋\n\n"
    "Your home visit is confirmed:\n\n"
    "📅 {appointment_date}\n"
    "⏰ {appointment_time}\n"
    "🏠 At your home address\n\n"
    "Please reply to this message with your full home address and postcode so "
    "we can finalise your visit.\n\n"
    "To reschedule, reply to this message or call us on {clinic_phone}.\n\n"
    "See you soon!\n— {clinic_name}"
)

# Remote (video / phone) confirmation — the consultation is not at the clinic,
# so the clinic address / Maps link and the "arrive early / bring records" note
# don't apply. The practitioner makes contact at the appointment time.
REMOTE_CONFIRMATION_SMS = (
    "Hi {patient_name} 👋\n\n"
    "Your remote appointment with {clinic_name} is confirmed:\n\n"
    "📅 {appointment_date}\n"
    "⏰ {appointment_time}\n"
    "💻 Video / phone consultation — {clinic_name} will be in touch at your "
    "appointment time with how to join.\n\n"
    "To reschedule, reply to this message or call us on {clinic_phone}.\n\n"
    "See you soon!\n— {clinic_name}"
)

# Explicit full-name request — inserted only when the caller's full name is
# still pending (only first name was captured on the call).
FULL_NAME_REQUEST_NOTE = (
    "Please reply to this message with your full name so we can complete "
    "your booking details.\n\n"
)

# Silent-misspelling recovery (P5). When a new patient's full name WAS captured
# on the call, STT may still have misheard the spelling and the verbal flow
# never reads the surname back — so give a low-friction way to correct it.
SPELLING_CONFIRM_NOTE = (
    "We've booked you in as {full_name}. If that's spelled differently, just "
    "reply with the correct spelling.\n\n"
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

    # First-visit / returning-plan note selection.
    # Priority: returning_plan flag (set by RETURNING_PLAN_LOOKUP) → explicit
    # patient_type / new_or_returning → booked-service inference → safe default
    # of FIRST_VISIT_NOTE for unknown callers.
    if session.get("returning_plan"):
        note = RETURNING_PLAN_NOTE
        first_visit = False
    else:
        _pt = (
            collected.get("patient_type")
            or session.get("new_or_returning")
            or ""
        ).upper()
        if _pt:
            first_visit = (_pt == "NEW")
        else:
            # Call-3 P3 (2026-07-19): the media-streams template flow never
            # writes patient_type, so every caller defaulted to the new-patient
            # flavour — a returning follow-up got "what to bring on your first
            # visit". The booked service id (persisted post-reconciliation by
            # book_appointment) is the truthful signal: treatment/follow-up
            # services are returning-only by definition. Anything else keeps
            # the safe new-patient default.
            _svc = (
                session.get("_booked_service")
                or session.get("_checked_service")
                or ""
            ).lower()
            first_visit = not any(
                k in _svc for k in ("treatment", "followup", "follow_up")
            )
        note = FIRST_VISIT_NOTE if first_visit else RETURNING_VISIT_NOTE

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

    # Data-driven template clinics (prompt_engine == "template_v1", e.g. jv_v1)
    # carry their own branding/address/phone in clinic.json. Theorem env vars and
    # the hardcoded alcester/redditch map don't apply to them, so resolve the SMS
    # clinic name, phone and address (→ Maps link) straight from the clinic config.
    # Theorem/demo are unaffected (they have no template_v1 prompt_engine).
    _is_template_clinic = False
    # Optional per-clinic fees / policy line for the in-clinic confirmation.
    # Driven from clinic.json ("sms_fees_note"); "" for clinics that don't set it
    # so their message is byte-identical (theorem/demo have no such key).
    fees_note = ""
    try:
        from app.clinic_config import get_clinic
        _clinic = get_clinic(session.get("clinic_id"))
        fees_note = _clinic.get("sms_fees_note") or ""
        # Identity is NOT a template-engine concern. Gated on template_v1, a
        # clinic running any other prompt builder fell through to the env
        # defaults and sent "Your appointment at the clinic is confirmed… call
        # us on ." — while its own config held the real name and number the
        # whole time. A patient was told to ring a number that was not there.
        # A clinic that knows its own name and number should use them whichever
        # builder it runs; the env vars stay as the last fallback for a
        # deployment with no clinic config at all. What stays gated below is
        # `_is_template_clinic`, which drives the spelling-confirm note — a
        # genuinely template-only behaviour.
        clinic_name  = (
            _clinic.get("sms_name") or _clinic.get("clinic_name") or clinic_name
        )
        # `sms_phone` wins where a clinic sets one: the number a patient should
        # ring off the back of a text is the line the text came FROM, so that
        # "call us" and "reply to this message" reach the same place. Falls back
        # to `phone` for clinics that don't distinguish the two.
        clinic_phone = (
            _clinic.get("sms_phone") or _clinic.get("phone") or clinic_phone
        )

        if _clinic.get("prompt_engine") == "template_v1":
            _is_template_clinic = True
            _locs  = _clinic.get("locations") or []
            _match = next(
                (l for l in _locs if str(l.get("location_id", "")).lower() == _loc),
                (_locs[0] if _locs else None),
            )
            if _match and _match.get("address_full"):
                clinic_address = _match["address_full"]
    except Exception:
        pass

    maps_link      = build_maps_link(clinic_address)

    # Pending full name?  Only the caller's first name is collected on the
    # call, so the stored name is typically a single token.  When a full name
    # has already been confirmed (contains a space) the SMS must NOT re-ask.
    _full_name_confirmed = bool(name_raw) and (" " in name_raw)
    pending_full_name    = not _full_name_confirmed
    if pending_full_name:
        full_name_request = FULL_NAME_REQUEST_NOTE
    elif _is_template_clinic:
        # P5: full name captured on the call — the surname is never read back
        # aloud, so a silent STT misspelling ("Quinton Rock" for "Quentin
        # Roch") has no recovery path except this SMS line. Fires for ANY
        # caller whose full name was captured this call, new or returning:
        # a returning caller's name is just as vulnerable to the homophone
        # (Call-3 P3, 2026-07-19 — previously scoped to first_visit only,
        # which would have silently dropped the safety net once returning
        # callers were classified correctly). Template clinics only, so
        # theorem/demo are unchanged.
        full_name_request = SPELLING_CONFIRM_NOTE.format(full_name=name_raw)
    else:
        full_name_request = ""

    import logging as _logging_sms
    _logging_sms.getLogger(__name__).info(
        "BOOKING_CONFIRM pending_full_name=%s template=%s new=%s → full-name line: %s",
        pending_full_name, _is_template_clinic, first_visit,
        "request" if pending_full_name else
        ("spelling-confirm" if _is_template_clinic else "omitted"),
    )

    # Modality is recorded by the booking executor as collected["location"]
    # ("bolton" / "remote" / "home_visit"); the service string also carries
    # "home_visit" for the dedicated home-visit service. Both are checked so a
    # home visit of ANY service (e.g. acupuncture at home) is detected too.
    _svc = (collected.get("service") or "").lower()
    _loc_modality = (collected.get("location") or "").lower()

    # Home visits happen at the patient's home, not the clinic — so the clinic
    # address / Maps link don't apply. Use a tailored body that asks the patient
    # to text their full address + postcode (collected by SMS, not voice, to
    # avoid transcription errors). The 30-min address nudge is scheduled by the
    # booking executor.
    if "home_visit" in _svc or "home visit" in _svc or _loc_modality == "home_visit":
        return HOME_VISIT_CONFIRMATION_SMS.format(
            patient_name     = patient_name,
            clinic_name      = clinic_name,
            appointment_date = appointment_date,
            appointment_time = appointment_time,
            clinic_phone     = clinic_phone,
        )

    # Remote (video / phone) consultations are not at the clinic — no address /
    # Maps link / arrival note. Detected from the booking modality.
    if _loc_modality in ("remote", "video", "phone", "online", "virtual"):
        return REMOTE_CONFIRMATION_SMS.format(
            patient_name     = patient_name,
            clinic_name      = clinic_name,
            appointment_date = appointment_date,
            appointment_time = appointment_time,
            clinic_phone     = clinic_phone,
        )

    body = BOOKING_CONFIRMATION_SMS.format(
        patient_name      = patient_name,
        clinic_name       = clinic_name,
        appointment_date  = appointment_date,
        appointment_time  = appointment_time,
        clinic_address    = clinic_address,
        first_visit_note  = note,
        fees_note         = fees_note,
        full_name_request = full_name_request,
        maps_link         = maps_link,
        clinic_phone      = clinic_phone,
    )
    return body
