# app/notifications/smart_sms_router.py
"""
Smart SMS routing — sends the right message for every call outcome.
Analyses call outcome and conversation to choose the right template.
"""

import logging
import re
from typing import Dict, Any, Optional

from app.notifications.booking_sms import send_sms
from app.notifications import templates
from app.clinic_config import get_clinic

logger = logging.getLogger(__name__)


# ============================================================================
# CONDITION EXTRACTION
# Turns raw caller speech ("my elbow is bad", "I broke my foot")
# into a clean label suitable for SMS ("elbow pain", "broken foot").
# ============================================================================

# Ordered longest-first so longer phrases match before shorter substrings
_NAMED_CONDITIONS = [
    ("slipped disc",          "a slipped disc"),
    ("plantar fasciitis",     "plantar fasciitis"),
    ("frozen shoulder",       "a frozen shoulder"),
    ("rotator cuff",          "a rotator cuff injury"),
    ("tennis elbow",          "tennis elbow"),
    ("golfer",                "golfer's elbow"),
    ("shin splint",           "shin splints"),
    ("repetitive strain",     "repetitive strain injury"),
    ("osteoarthritis",        "osteoarthritis"),
    ("fibromyalgia",          "fibromyalgia"),
    ("tendinopathy",          "tendinopathy"),
    ("tendinitis",            "tendinitis"),
    ("tendinosis",            "tendinosis"),
    ("bursitis",              "bursitis"),
    ("whiplash",              "whiplash"),
    ("scoliosis",             "scoliosis"),
    ("sciatica",              "sciatica"),
    ("arthritis",             "arthritis"),
    ("sports injury",         "a sports injury"),
    ("muscle strain",         "a muscle strain"),
    ("muscle tear",           "a muscle tear"),
    ("rsi",                   "RSI"),
    ("disc",                  "a disc problem"),
]

_BODY_PARTS = [
    ("lower back",  "lower back pain"),
    ("upper back",  "upper back pain"),
    ("mid back",    "mid-back pain"),
    ("achilles",    "Achilles pain"),
    ("hamstring",   "hamstring pain"),
    ("shoulder",    "shoulder pain"),
    ("back",        "back pain"),
    ("knee",        "knee pain"),
    ("elbow",       "elbow pain"),
    ("neck",        "neck pain"),
    ("hip",         "hip pain"),
    ("ankle",       "ankle pain"),
    ("wrist",       "wrist pain"),
    ("foot",        "foot pain"),
    ("heel",        "heel pain"),
    ("calf",        "calf pain"),
    ("groin",       "groin pain"),
    ("thumb",       "thumb pain"),
    ("finger",      "finger pain"),
    ("toe",         "toe pain"),
    ("hand",        "hand pain"),
    ("arm",         "arm pain"),
    ("leg",         "leg pain"),
    ("chest",       "chest pain"),
    ("rib",         "rib pain"),
    ("jaw",         "jaw pain"),
]

_FRACTURE_WORDS  = {"broken", "fracture", "fractured", "break", "broke", "snapped", "cracked"}
_SURGERY_WORDS   = {"post-op", "post op", "surgery", "operation", "reconstruction", "replacement"}

# Filler phrases stripped from the start of raw reason text
_FILLERS = sorted([
    "i'm not really sure but my ", "i'm not really sure but ",
    "i'm not sure but my ", "i'm not sure but ", "i'm not sure, ",
    "not really sure but ", "i think ", "um, ", "uh, ",
    "i have a problem with my ", "i have a problem with ",
    "i'm having issues with my ", "i'm having issues with ",
    "i've been having problems with my ", "i've been having problems with ",
    "i've been having ", "i have been having ",
    "i'm experiencing ", "i am experiencing ",
    "i've got a problem with ", "i've got ", "i have got ", "i've got a ",
    "i have a ", "i have ", "i'm having ", "i am having ",
    "it's my ", "it is my ", "my ", "it's ", "its ",
    "a bit of ", "some ", "a lot of ",
], key=lambda x: -len(x))


def extract_condition_label(reason: str) -> str:
    """
    Extract a clean, human-readable condition label from raw caller speech.

    Examples:
      "I'm not really sure but my elbow is bad" → "elbow pain"
      "I broke my foot"                          → "broken foot"
      "sciatica for the last three months"       → "sciatica"
      "lower back pain after the gym"            → "lower back pain"
    """
    if not reason:
        return ""

    r = reason.lower()

    is_fracture  = any(w in r for w in _FRACTURE_WORDS)
    is_post_surg = any(w in r for w in _SURGERY_WORDS)

    # 1. Named conditions (more specific — check first)
    for key, label in _NAMED_CONDITIONS:
        if key in r:
            return label

    # 2. Body parts
    for key, label in _BODY_PARTS:
        if key in r:
            if is_fracture:
                return f"a broken {key}"
            if is_post_surg:
                return f"post-surgery {key} recovery"
            return label

    # 3. Reject questions / enquiries — these are not conditions
    _QUESTION_STARTS = (
        "what", "how", "which", "can ", "could", "would", "should",
        "is ", "are ", "do ", "does ", "will ", "why", "when", "where",
        "have you", "do you", "can you", "could you",
    )
    if r.endswith("?") or any(r.startswith(q) for q in _QUESTION_STARTS):
        return ""

    # 4. Fallback: strip filler words and return cleaned text
    cleaned = reason.strip()
    for filler in _FILLERS:
        if cleaned.lower().startswith(filler):
            cleaned = cleaned[len(filler):].strip()
            break

    # Remove trailing noise ("is bad", "is hurting", "is giving me problems" etc.)
    cleaned = re.sub(
        r"\s+(is bad|is killing me|is hurting|is really bad|hurts|is sore|"
        r"is giving me problems|is playing up|has been bad|has been hurting)[\s,\.]*$",
        "", cleaned, flags=re.IGNORECASE,
    ).strip()

    if cleaned and 2 <= len(cleaned) <= 60:
        return cleaned[0].upper() + cleaned[1:]

    return ""


# ============================================================================
# MAIN ENTRY POINT
# ============================================================================

async def send_smart_followup_sms(
    session: Dict[str, Any],
    summary: Dict[str, Any],
) -> bool:
    """Route to appropriate SMS template based on call context."""

    collected = session.get("collected", {}) or {}

    # Phone — use the caller's Twilio number first (always available)
    patient_phone = (
        session.get("twilio_from", "") or
        collected.get("phone", "")
    )
    if patient_phone and patient_phone.startswith("client:"):
        patient_phone = ""

    # First name only
    name_raw     = collected.get("name", "") or ""
    patient_name = name_raw.split()[0] if name_raw.strip() else ""

    outcome        = summary.get("outcome", "")
    meta_data      = summary.get("meta", {}) or {}
    insurance_data = summary.get("insurance", {}) or {}
    handoff_data   = summary.get("handoff", {}) or {}
    faq_data       = summary.get("faq", []) or []

    # Clinic branding
    _clinic      = get_clinic(session.get("clinic_id"))
    clinic_name  = _clinic.get("sms_name") or _clinic.get("display_name")
    clinic_phone = _clinic.get("phone")
    hours_summary = _clinic.get("hours_summary")  # e.g. "Mon–Fri 8:30am–9pm"

    # Call duration
    duration = 0
    try:
        duration_str = (
            meta_data.get("duration_sec") or
            session.get("twilio_duration_sec") or
            "0"
        )
        duration = int(duration_str)
    except Exception:
        duration = 0

    # ── FILTER RULES ─────────────────────────────────────────────────────────

    if not patient_phone:
        logger.info("📵 No phone number — skipping SMS")
        return False

    if duration < 15:
        logger.info(f"⏱️  Call too short ({duration}s) — skipping SMS")
        return False

    # Booked — handled separately (booking confirmation SMS is already sent)
    if outcome == "booked":
        logger.info("✅ Booked — booking confirmation SMS already sent")
        return False

    # ── CHOOSE TEMPLATE ──────────────────────────────────────────────────────

    message = _choose_template(
        outcome        = outcome,
        patient_name   = patient_name,
        collected      = collected,
        insurance_data = insurance_data,
        handoff_data   = handoff_data,
        faq_data       = faq_data,
        session        = session,
        clinic_name    = clinic_name,
        clinic_phone   = clinic_phone,
        hours_summary  = hours_summary,
    )

    if not message:
        logger.warning(f"⚠️  No template for outcome: {outcome}")
        return False

    # ── SEND ─────────────────────────────────────────────────────────────────

    try:
        await send_sms(to=patient_phone, message=message)
        logger.info(f"✅ Smart SMS sent [{outcome}] → {patient_phone}")
        return True
    except Exception as e:
        logger.error(f"❌ Failed to send SMS: {e}", exc_info=True)
        return False


# ============================================================================
# TEMPLATE SELECTOR
# ============================================================================

def _choose_template(
    outcome:        str,
    patient_name:   str,
    collected:      Dict,
    insurance_data: Dict,
    handoff_data:   Dict,
    faq_data:       list,
    session:        Dict,
    clinic_name:    Optional[str] = None,
    clinic_phone:   Optional[str] = None,
    hours_summary:  Optional[str] = None,
) -> Optional[str]:
    """Choose the right template based on call outcome."""

    raw_reason  = collected.get("reason", "") or ""
    location    = session.get("selected_location", "")
    insurer     = insurance_data.get("insurer_name", "") or ""

    # Extract a clean condition label from the raw reason
    condition_label = extract_condition_label(raw_reason)

    # Flags
    asked_about_price     = _check_price_question(faq_data, session)
    asked_about_insurance = _check_insurance_question(faq_data, insurance_data, session)
    bupa_mentioned        = _check_bupa_mention(faq_data, insurance_data)

    ck = {"clinic_name": clinic_name, "clinic_phone": clinic_phone}

    # ── ROUTING ──────────────────────────────────────────────────────────────

    # 1. HUMAN CALLBACK REQUESTED
    if outcome == "human_requested":
        return templates.format_callback_confirmation(
            patient_name=patient_name, **ck)

    # 2. OUT OF HOURS
    if outcome == "out_of_hours":
        return templates.format_out_of_hours_sms(
            hours_summary=hours_summary, **ck)

    # 3. RESCHEDULED (manual followup for reschedule)
    if outcome == "rescheduled" or (
        outcome == "manual_followup" and any(
            w in (handoff_data.get("reason") or "").lower()
            for w in ["reschedule", "change", "move"]
        )
    ):
        return templates.format_reschedule_request_sms(
            patient_name=patient_name, **ck)

    # 4. CANCELLATION (manual followup for cancellation)
    if outcome == "manual_followup" and any(
        w in (handoff_data.get("reason") or "").lower()
        for w in ["cancel", "cancellation"]
    ):
        return templates.format_cancellation_request_sms(
            patient_name=patient_name, **ck)

    # 5. NO SUITABLE TIME
    if outcome == "manual_followup" and "time" in (handoff_data.get("reason") or "").lower():
        return templates.format_no_suitable_time_sms(
            patient_name=patient_name, reason=condition_label or raw_reason, **ck)

    # 6. GENERAL MANUAL FOLLOWUP
    if outcome == "manual_followup":
        return templates.format_callback_confirmation(
            patient_name=patient_name, **ck)

    # 7. FAQ — BUPA SPECIFICALLY
    if bupa_mentioned:
        return templates.format_insurance_inquiry_sms(
            patient_name=patient_name, bupa_mentioned=True, **ck)

    # 8. FAQ — INSURANCE (non-Bupa)
    if asked_about_insurance:
        return templates.format_insurance_inquiry_sms(
            patient_name=patient_name,
            insurer=insurer or None,
            bupa_mentioned=False,
            **ck,
        )

    # 9. FAQ — PRICE
    if asked_about_price:
        return templates.format_price_inquiry_sms(
            patient_name=patient_name, **ck)

    # 10. ABANDONED — with a clean condition label only (never raw speech)
    if outcome == "abandoned" and condition_label:
        return templates.format_abandoned_booking_sms(
            patient_name    = patient_name,
            condition_label = condition_label,
            **ck,
        )

    # 11. ABANDONED — general (no clean condition extracted)
    if outcome == "abandoned":
        return templates.format_abandoned_booking_sms(
            patient_name=patient_name, **ck)

    # 12. FAQ — GENERAL (no specific topic)
    if outcome == "faq_only":
        return templates.format_general_thankyou_sms(
            patient_name=patient_name, **ck)

    # 13. TECHNICAL FAILURE
    if outcome == "failed":
        return templates.format_technical_issue_sms(
            patient_name=patient_name, **ck)

    # 14. FALLBACK
    return templates.format_general_thankyou_sms(
        patient_name=patient_name, **ck)


# ============================================================================
# DETECTION HELPERS
# ============================================================================

def _check_price_question(faq_data: list, session: Dict) -> bool:
    """Check if patient asked about pricing."""
    keywords = {"price", "cost", "how much", "fee", "charge", "expensive", "£"}
    for turn in faq_data:
        if isinstance(turn, dict) and any(k in turn.get("question", "").lower() for k in keywords):
            return True
    for turn in (session.get("turns", []) or [])[-6:]:
        if isinstance(turn, dict) and any(k in turn.get("user", "").lower() for k in keywords):
            return True
    return False


def _check_insurance_question(faq_data: list, insurance_data: Dict, session: Dict) -> bool:
    """Check if patient asked about insurance."""
    if insurance_data.get("insurer_name"):
        return True
    keywords = {"insurance", "insurer", "bupa", "axa", "aviva", "vitality", "claim", "cover"}
    for turn in faq_data:
        if isinstance(turn, dict) and any(k in turn.get("question", "").lower() for k in keywords):
            return True
    for turn in (session.get("turns", []) or [])[-6:]:
        if isinstance(turn, dict) and any(k in turn.get("user", "").lower() for k in keywords):
            return True
    return False


def _check_bupa_mention(faq_data: list, insurance_data: Dict) -> bool:
    """Check if Bupa was specifically mentioned."""
    if ((insurance_data or {}).get("insurer_name") or "").lower() == "bupa":
        return True
    for turn in faq_data:
        if isinstance(turn, dict) and "bupa" in turn.get("question", "").lower():
            return True
    return False
