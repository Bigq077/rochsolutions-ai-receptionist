# app/notifications/templates.py
"""
Clinic-aware SMS templates.
Every function accepts optional clinic_name and clinic_phone kwargs so the
correct branding appears in every message regardless of which clinic is active.

Demo clinic  : "Roch Physio Clinic" / 07366 530580
Theorem Health: "Theorem Health"     / 07870 166861
"""

from datetime import datetime
from typing import Optional


# ============================================================================
# DEFAULTS (Theorem values — override per clinic via kwargs)
# ============================================================================

_DEFAULT_CLINIC_NAME  = "Theorem Health"
_DEFAULT_CLINIC_PHONE = "07366 530580"


def _appt_noun(appointment_noun: Optional[str]) -> str:
    """
    Modality word for "your ___ appointment", or "" for a bare "your
    appointment".

    The reminder body used to hardcode "physiotherapy" for every tenant. Vital
    Edge sells only massage and Theorem's book runs from acupuncture to
    psychotherapy, so two of the three live clinics were texting patients the
    wrong word for what they had booked. The noun now comes from the clinic's
    own config (operational.sms_appointment_noun); a clinic that sets nothing
    gets the neutral wording rather than another clinic's speciality.
    """
    n = (appointment_noun or "").strip()
    return f"{n} " if n else ""


def _cn(clinic_name: Optional[str]) -> str:
    return clinic_name  or _DEFAULT_CLINIC_NAME


def _cp(clinic_phone: Optional[str]) -> str:
    return clinic_phone or _DEFAULT_CLINIC_PHONE


def _first(patient_name: Optional[str]) -> str:
    """
    Greeting name — first token only.

    SMS greetings should read "Hi Quentin", not "Hi Quentin Rock". Booking/
    reminder callers already pass a first name, but the cancel/reschedule paths
    pass the full name looked up from the calendar, so normalise here at the
    rendering boundary. Empty / "none" / "unknown" → "there" (preserves the
    prior fallback behaviour).

    Booking-state words are rejected for the same reason: the calendar title of
    a provisional booking starts "PENDING CONFIRMATION — …", and a cancellation
    SMS greeted a live caller "Hi PENDING". The parser that produced that is
    fixed at source (_gcal_event_patient_name); this is the last line of defence
    at the rendering boundary, where a status word is never a person.
    """
    name = (patient_name or "").strip()
    if not name or name.lower() in {"none", "unknown"}:
        return "there"
    first = name.split()[0]
    if first.lower().strip(":,") in {
        "pending", "booked", "confirmed", "provisional", "cancelled", "canceled",
    }:
        return "there"
    return first


# ============================================================================
# ✅ BOOKING CONFIRMATION
# ============================================================================

def format_booking_confirmation(
    patient_name: str,
    appointment_time: datetime,
    location: str,
    service: str = "physiotherapy",
    practitioner: Optional[str] = None,
    is_new_patient: bool = False,
    has_insurance: bool = False,
    insurer: Optional[str] = None,
    clinic_name:  Optional[str] = None,
    clinic_phone: Optional[str] = None,
) -> str:
    """✅ Booking confirmed."""
    day_name = appointment_time.strftime("%A")
    day_num  = appointment_time.strftime("%d").lstrip("0")
    month    = appointment_time.strftime("%b")
    time_str = appointment_time.strftime("%I:%M%p").lstrip("0").lower()
    date_str = appointment_time.strftime("%B %d, %Y")  # e.g., "April 08, 2026"
    date_str = date_str.replace(" 0", " ")  # Remove leading zero from day

    name    = _cn(clinic_name)
    phone   = _cp(clinic_phone)
    loc_str = location.title() if location else "our"

    msg = (
        f"Hi {patient_name}, you're all booked in with {name} on "
        f"{date_str} at {time_str} at our {loc_str} clinic."
    )
    if practitioner:
        msg += f" Your appointment is with {practitioner}."
    if is_new_patient:
        msg += " Please arrive 5 minutes early."
    msg += f" If you need to reschedule, just call us on {phone}. We look forward to seeing you! 🙌"
    return msg


def format_insurance_booking_confirmation(
    patient_name: str,
    appointment_time: datetime,
    location: str,
    insurer: str,
    service: str = "physiotherapy",
    practitioner: Optional[str] = None,
    clinic_name:  Optional[str] = None,
    clinic_phone: Optional[str] = None,
) -> str:
    """✅ Booking confirmed — insurance patient."""
    day_name = appointment_time.strftime("%A")
    day_num  = appointment_time.strftime("%d").lstrip("0")
    month    = appointment_time.strftime("%b")
    time_str = appointment_time.strftime("%I:%M%p").lstrip("0").lower()
    date_str = appointment_time.strftime("%B %d, %Y")  # e.g., "April 08, 2026"
    date_str = date_str.replace(" 0", " ")  # Remove leading zero from day

    name  = _cn(clinic_name)
    phone = _cp(clinic_phone)
    loc_str = location.title() if location else "our"

    msg = (
        f"Hi {patient_name}, you're all booked in with {name} on "
        f"{date_str} at {time_str} at our {loc_str} clinic."
    )
    if practitioner:
        msg += f" Your appointment is with {practitioner}."
    msg += (
        f" We'll provide all the documentation you need for your {insurer} claim. "
        f"If you need to reschedule, call {phone}. See you then! 🙌"
    )
    return msg


# ============================================================================
# 🔔 REMINDER TEMPLATES
# ============================================================================

def format_24hr_reminder(
    patient_name: str,
    appointment_time: datetime,
    location: str,
    what_to_bring: bool = False,
    is_new_patient: bool = False,
    has_insurance: bool = False,
    clinic_name:  Optional[str] = None,
    clinic_phone: Optional[str] = None,
    appointment_noun: Optional[str] = None,
) -> str:
    """Reminder — 24 hours before."""
    day_name = appointment_time.strftime("%A")
    time_str = appointment_time.strftime("%I:%M%p").lstrip("0").lower()
    loc_str  = location.title() if location else "our"

    msg = (
        f"Hi {_first(patient_name)}, just a reminder — your "
        f"{_appt_noun(appointment_noun)}appointment is tomorrow "
        f"({day_name}) at {time_str} at our {loc_str} clinic."
    )
    if what_to_bring or is_new_patient:
        msg += " Please arrive 5 minutes early to complete your paperwork."
    msg += f" Need to reschedule? Call {_cp(clinic_phone)}. {_cn(clinic_name)}"
    return msg


def format_same_day_reminder(
    patient_name: str,
    appointment_time: datetime,
    location: str,
    clinic_name:  Optional[str] = None,
    clinic_phone: Optional[str] = None,
    appointment_noun: Optional[str] = None,
) -> str:
    """Reminder — same day, 2 hours before."""
    time_str = appointment_time.strftime("%I:%M%p").lstrip("0").lower()
    loc_str  = location.title() if location else "our"

    return (
        f"Hi {_first(patient_name)}, just a reminder — your "
        f"{_appt_noun(appointment_noun)}appointment is today at {time_str} "
        f"at our {loc_str} clinic. See you soon! {_cn(clinic_name)} - {_cp(clinic_phone)}"
    )


def format_insurance_reminder(
    patient_name: str,
    insurer: str,
    clinic_name: Optional[str] = None,
) -> str:
    """Insurance reminder (sent with 24hr reminder)."""
    return (
        f"Hi {patient_name}, reminder: we'll provide all the documentation you need "
        f"for your {insurer} claim. Bring your membership number if you have it. "
        f"{_cn(clinic_name)}"
    )


def format_what_to_bring_reminder(
    patient_name: str,
    clinic_name: Optional[str] = None,
) -> str:
    """What to bring — new patient."""
    return (
        f"Hi {patient_name}, for your first visit please bring: photo ID, "
        f"insurance details (if claiming), and any relevant medical reports. "
        f"{_cn(clinic_name)}"
    )


# ============================================================================
# 🔄 RESCHEDULE & ❌ CANCELLATION TEMPLATES
# ============================================================================

def format_reschedule_confirmation(
    patient_name: str,
    old_time: datetime,
    new_time: datetime,
    location: str,
    clinic_name:  Optional[str] = None,
    clinic_phone: Optional[str] = None,
) -> str:
    """🔄 Appointment rescheduled."""
    new_day      = new_time.strftime("%A")
    new_day_num  = new_time.strftime("%d").lstrip("0")
    new_month    = new_time.strftime("%b")
    new_time_str = new_time.strftime("%I:%M%p").lstrip("0").lower()
    new_date_str = new_time.strftime("%B %d, %Y")  # e.g., "April 08, 2026"
    new_date_str = new_date_str.replace(" 0", " ")  # Remove leading zero from day
    loc_str      = location.title() if location else ""

    phone = _cp(clinic_phone)
    name  = _cn(clinic_name)

    _greeting = _first(patient_name)
    loc_clause = f" at our {loc_str} clinic" if loc_str else ""
    return (
        f"Hi {_greeting}, your appointment has been moved to "
        f"{new_date_str} at {new_time_str}{loc_clause}. "
        f"If anything changes, give us a call on {phone} and we'll sort it. See you then! {name}"
    )


def format_cancellation_confirmation(
    patient_name: str,
    appointment_time: datetime,
    is_late_cancellation: bool = False,
    clinic_name:  Optional[str] = None,
    clinic_phone: Optional[str] = None,
) -> str:
    """❌ Appointment cancelled."""
    day_name = appointment_time.strftime("%A")
    day_num  = appointment_time.strftime("%d").lstrip("0")
    month    = appointment_time.strftime("%b")
    time_str = appointment_time.strftime("%I:%M%p").lstrip("0").lower()

    phone = _cp(clinic_phone)
    name  = _cn(clinic_name)

    _greeting = _first(patient_name)
    msg = (
        f"Hi {_greeting}, your appointment on {day_name} {day_num} {month} "
        f"at {time_str} has been cancelled as requested."
    )
    if is_late_cancellation:
        msg += " As this was within 24 hours, our cancellation fee may apply."
    msg += (
        f" Whenever you're ready to rebook, just give us a call — "
        f"we'll get you sorted quickly. {phone} {name}"
    )
    return msg


def format_late_cancellation_warning(
    patient_name: str,
    clinic_name:  Optional[str] = None,
    clinic_phone: Optional[str] = None,
) -> str:
    """Late cancellation warning (within 24 hours)."""
    _greeting = _first(patient_name)
    return (
        f"Hi {_greeting}, your appointment has been cancelled. "
        f"As this was within 24 hours, our £25 cancellation fee applies. "
        f"Ready to rebook? Call {_cp(clinic_phone)}. {_cn(clinic_name)}"
    )


# ============================================================================
# 🙋 HUMAN CALLBACK TEMPLATES
# ============================================================================

def format_callback_confirmation(
    patient_name: str,
    clinic_name:  Optional[str] = None,
    clinic_phone: Optional[str] = None,
) -> str:
    """🙋 Human callback requested."""
    name  = _cn(clinic_name)
    phone = _cp(clinic_phone)
    if patient_name:
        return (
            f"Hi {patient_name}, you asked for a callback from our team. "
            f"We've noted your details and someone will be in touch as soon as possible. "
            f"If it's urgent, call us directly on {phone}. {name}"
        )
    return (
        f"Hi, you requested a callback from our team at {name}. "
        f"Someone will be in touch shortly. If it's urgent, call {phone}."
    )


def format_message_received_confirmation(
    patient_name: str,
    clinic_name:  Optional[str] = None,
    clinic_phone: Optional[str] = None,
) -> str:
    """Message received confirmation."""
    if patient_name:
        return (
            f"Hi {patient_name}, we've received your message and will respond shortly. "
            f"{_cn(clinic_name)} - {_cp(clinic_phone)}"
        )
    return f"Message received. We'll respond shortly. {_cn(clinic_name)} - {_cp(clinic_phone)}"


# ============================================================================
# 📞 ABANDONED BOOKING — SHOWED INTEREST, DIDN'T BOOK
# ============================================================================

def format_abandoned_booking_sms(
    patient_name: str,
    reason: Optional[str] = None,
    location: Optional[str] = None,
    condition_label: Optional[str] = None,
    clinic_name:  Optional[str] = None,
    clinic_phone: Optional[str] = None,
) -> str:
    """📞 Abandoned call — showed interest but didn't complete booking."""
    name  = _cn(clinic_name)
    phone = _cp(clinic_phone)

    return (
        f"Hi, you called {name} earlier and we'd love to help. "
        f"Booking takes less than 2 minutes over the phone — "
        f"give us a call back whenever suits you. 😊 {phone}"
    )


# ============================================================================
# ⏸️ REACHED FINAL CONFIRMATION — CALL ENDED BEFORE CONFIRMING
# ============================================================================

def format_reached_confirmation_sms(
    patient_name: str,
    clinic_name:  Optional[str] = None,
    clinic_phone: Optional[str] = None,
) -> str:
    """⏸️ Caller reached 'shall I go ahead and book?' but call ended before confirming."""
    phone = _cp(clinic_phone)
    _greeting = _first(patient_name)
    return (
        f"Hi {_greeting}, it looks like we got cut off just before confirming your "
        f"appointment. Call us back or reply to this message and we'll get it booked "
        f"in for you straight away. {phone}"
    )


# ============================================================================
# 🔇 NO AUDIO — SYSTEM COULDN'T HEAR CALLER (GRACEFUL CLOSE)
# ============================================================================

def format_no_audio_sms(
    patient_name: str,
    clinic_name:  Optional[str] = None,
    clinic_phone: Optional[str] = None,
) -> str:
    """🔇 Safety net graceful close — call ended because system couldn't hear caller."""
    phone = _cp(clinic_phone)
    _greeting = _first(patient_name)
    return (
        f"Hi {_greeting}, we weren't able to hear you during your call — "
        f"it may have been a connection issue on the line. Please call us back "
        f"whenever you're ready and we'll get you sorted straight away. {phone}"
    )


# ============================================================================
# ⚠️ CONDITION MENTIONED — NO BOOKING MADE
# ============================================================================

def format_condition_sms(
    condition_label: str,
    clinic_name:  Optional[str] = None,
    clinic_phone: Optional[str] = None,
) -> str:
    """⚠️ Caller mentioned a condition but didn't book."""
    name  = _cn(clinic_name)
    phone = _cp(clinic_phone)
    subject = condition_label or "your condition"
    return (
        f"Hi, you called about {subject}. "
        f"Our physios have a great track record with this — "
        f"most patients feel a difference within 1–2 sessions. "
        f"Don't let it drag on — give us a call back and we'll get you seen quickly. "
        f"{phone} {name}"
    )


# ============================================================================
# 💰 PRICING ENQUIRY — DIDN'T BOOK
# ============================================================================

def format_price_inquiry_sms(
    patient_name: str,
    service: Optional[str] = None,
    clinic_name:  Optional[str] = None,
    clinic_phone: Optional[str] = None,
    price_line: str = "A 50-min physio appointment is £75 — most patients see results within 2–3 sessions. ",
) -> str:
    """💰 Price enquiry — didn't book.

    price_line is a full clause (with trailing space) so a clinic can override the
    price/duration AND drop any outcome claim (e.g. jv, whose non-diagnostic rules
    forbid "see results within N sessions"). Default reproduces the original text
    for clinics that don't override — byte-identical.
    """
    name  = _cn(clinic_name)
    phone = _cp(clinic_phone)
    return (
        f"Hi, you called {name} earlier asking about our prices. "
        f"{price_line}"
        f"Ready to book? Call us back anytime, we'd love to help. {phone}"
    )


# ============================================================================
# 🏥 INSURANCE ENQUIRY — DIDN'T BOOK
# ============================================================================

def format_insurance_inquiry_sms(
    patient_name: str,
    insurer: Optional[str] = None,
    bupa_mentioned: bool = False,
    clinic_name:  Optional[str] = None,
    clinic_phone: Optional[str] = None,
    accepts_referrals: bool = False,
    practitioner: Optional[str] = None,
    self_pay_only: bool = False,
) -> str:
    """🏥 Insurance enquiry / 🚫 Bupa — didn't book.

    Three mutually exclusive models:
    * self_pay_only=True  → clinic works with NO insurer at all (e.g. vital_edge).
      Never claim we bill, work with, or pay-and-claim through any provider.
    * accepts_referrals=True → clinic accepts private-insurance referrals (incl.
      Bupa), so use an Option-B message: confirm we accept the referral, no
      billing-mechanism promise, practitioner follows up to collect details (jv).
    * both False → the original Theorem "can't bill Bupa / pay-and-claim" copy,
      byte-identical for clinics that don't override.
    """
    name  = _cn(clinic_name)
    phone = _cp(clinic_phone)

    # Self-pay-only clinics must never imply any insurer relationship, and must
    # not quote another clinic's price.
    if self_pay_only:
        return (
            f"Hi, you called {name} about insurance. "
            f"We're a self-pay clinic and don't work with insurance providers. "
            f"If you have private cover you're welcome to check with your insurer, "
            f"though this type of treatment usually isn't covered. "
            f"Happy to get you booked in whenever suits — just give us a call. {phone}"
        )

    if accepts_referrals:
        _who = practitioner or "our team"
        if insurer:
            insurer_clause = f", including {insurer},"
        elif bupa_mentioned:
            insurer_clause = ", including Bupa,"
        else:
            insurer_clause = ""
        return (
            f"Hi, you called {name} about using your health insurance. "
            f"Good news — we accept private health insurance referrals{insurer_clause}. "
            f"Give us a call back whenever you'd like to book, and {_who} will be in touch "
            f"to sort out the insurance details with you. {phone}"
        )

    if bupa_mentioned:
        return (
            f"Hi, you called {name} about Bupa cover. "
            f"Unfortunately we can't bill Bupa directly, but many patients pay privately "
            f"at £75 and find it great value. "
            f"If you'd like to chat it through, give us a call — no pressure at all. {phone}"
        )

    insurer_clause = f" with {insurer}" if insurer else ""
    return (
        f"Hi, you called about using your health insurance{insurer_clause} with {name}. "
        f"We work with most major insurers — you pay us directly and claim back from your provider. "
        f"Give us a call back if you'd like to get booked in, we'll walk you through it. {phone}"
    )


# ============================================================================
# 🌙 OUT OF HOURS — DIDN'T BOOK
# ============================================================================

def format_out_of_hours_sms(
    clinic_name:  Optional[str] = None,
    clinic_phone: Optional[str] = None,
    hours_summary: Optional[str] = None,
) -> str:
    """🌙 Out of hours call — didn't book."""
    name  = _cn(clinic_name)
    phone = _cp(clinic_phone)
    hours_clause = f" We're open {hours_summary}." if hours_summary else ""
    return (
        f"Hi, you called {name} outside of our opening hours.{hours_clause} "
        f"Call back when you're ready or reply to this message and we'll get you booked in. {phone}"
    )


# ============================================================================
# ❓ GENERAL ENQUIRY — CURIOUS BUT UNCOMMITTED
# ============================================================================

def format_general_thankyou_sms(
    patient_name: str,
    clinic_name:  Optional[str] = None,
    clinic_phone: Optional[str] = None,
) -> str:
    """❓ General enquiry — curious but uncommitted."""
    name  = _cn(clinic_name)
    phone = _cp(clinic_phone)
    return (
        f"Hi, thanks for calling {name}! "
        f"If you have any more questions or want to book in, just give us a call — "
        f"our AI receptionist is available any time and can get you sorted in under 2 minutes. 😊 {phone}"
    )


# ============================================================================
# 💌 POST-APPOINTMENT TEMPLATES
# ============================================================================

def format_post_appointment_thankyou(
    patient_name: str,
    practitioner: Optional[str] = None,
    clinic_name:  Optional[str] = None,
    clinic_phone: Optional[str] = None,
) -> str:
    """Thank you after appointment."""
    msg = f"Hi {patient_name}, thank you for visiting {_cn(clinic_name)} today"
    if practitioner:
        msg += f" with {practitioner}"
    msg += (
        ". Remember to do your prescribed exercises! "
        f"Call {_cp(clinic_phone)} if you have any questions."
    )
    return msg


def format_insurance_receipt_ready(
    patient_name: str,
    insurer: str,
    clinic_name:  Optional[str] = None,
    clinic_phone: Optional[str] = None,
) -> str:
    """Insurance receipt ready notification."""
    return (
        f"Hi {patient_name}, your receipt and clinical notes for your {insurer} claim "
        f"have been emailed. Call {_cp(clinic_phone)} if you need anything else. "
        f"{_cn(clinic_name)}"
    )


def format_insurance_receipt_notification(
    patient_name: str,
    insurer: str,
    clinic_name:  Optional[str] = None,
    clinic_phone: Optional[str] = None,
) -> str:
    """Alias for format_insurance_receipt_ready (backward compatibility)."""
    return format_insurance_receipt_ready(
        patient_name, insurer,
        clinic_name=clinic_name, clinic_phone=clinic_phone,
    )


# ============================================================================
# LEGACY / MANUAL-FOLLOWUP TEMPLATES
# ============================================================================

def format_no_suitable_time_sms(
    patient_name: str,
    reason: Optional[str] = None,
    clinic_name:  Optional[str] = None,
    clinic_phone: Optional[str] = None,
) -> str:
    """No suitable appointment time found."""
    name  = _cn(clinic_name)
    phone = _cp(clinic_phone)
    if reason:
        return (
            f"Hi, sorry we couldn't find a suitable time for {reason}. "
            f"Reply YES and we'll call you back to arrange something. Or call {phone}. {name}"
        )
    return (
        f"Hi, sorry we couldn't find a suitable appointment time. "
        f"Reply YES for a callback or call us on {phone}. We'll find something that works! {name}"
    )


def format_technical_issue_sms(
    patient_name: str,
    clinic_name:  Optional[str] = None,
    clinic_phone: Optional[str] = None,
) -> str:
    """Technical issue during call."""
    name  = _cn(clinic_name)
    phone = _cp(clinic_phone)
    return (
        f"Hi, sorry — we had a technical issue with your call to {name}. "
        f"Please call us back on {phone} or reply YES and we'll call you. Apologies!"
    )


def format_reschedule_request_sms(
    patient_name: str,
    clinic_name:  Optional[str] = None,
    clinic_phone: Optional[str] = None,
) -> str:
    """Reschedule request received."""
    name  = _cn(clinic_name)
    phone = _cp(clinic_phone)
    if patient_name:
        return (
            f"Hi {patient_name}, thanks for calling about rescheduling. "
            f"We'll help you find a new time — call {phone} or reply YES and we'll call you. {name}"
        )
    return f"Thanks for calling about rescheduling. Call {phone} or reply YES for a callback. {name}"


def format_cancellation_request_sms(
    patient_name: str,
    clinic_name:  Optional[str] = None,
    clinic_phone: Optional[str] = None,
) -> str:
    """Cancellation request received."""
    name  = _cn(clinic_name)
    phone = _cp(clinic_phone)
    if patient_name:
        return (
            f"Hi {patient_name}, thanks for calling about cancelling your appointment. "
            f"Please call {phone} to confirm and we'll sort it out. {name}"
        )
    return f"Thanks for calling about cancellation. Call {phone} to confirm. {name}"


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def get_location_short_name(location: str) -> str:
    """Convert location to short name."""
    location_lower = (location or "").lower()
    if "alcester" in location_lower:
        return "Alcester"
    elif "redditch" in location_lower:
        return "Redditch"
    return location
