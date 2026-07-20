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


def _get_confirmed_phone(session: dict) -> str:
    """
    Return a phone number safe for downstream SMS use.
    - phone_confirmed=True  → use collected["phone"] (explicitly confirmed)
    - phone_confirmed=False → return "" (caller explicitly rejected — do not use)
    - phone_confirmed=None  → use twilio_from only if phone_from_twilio=True
    """
    collected = session.get("collected", {}) or {}
    confirmed = session.get("phone_confirmed")

    if confirmed is True:
        return (
            collected.get("phone")
            or session.get("phone_number")
            or ""
        )
    if confirmed is False:
        return ""
    if session.get("phone_from_twilio"):
        raw = session.get("twilio_from_local") or session.get("twilio_from") or ""
        if raw and not raw.startswith("client:"):
            return raw
    return ""


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

    patient_phone = _get_confirmed_phone(session)

    # First name only — sanitised: reject None, empty, and junk placeholders
    name_raw     = collected.get("name", "") or ""
    patient_name = name_raw.split()[0] if name_raw.strip() else ""
    if patient_name.lower() in {"none", "unknown", "null", "n/a", "na", "undefined"}:
        patient_name = ""

    outcome        = summary.get("outcome", "")
    meta_data      = summary.get("meta", {}) or {}
    insurance_data = summary.get("insurance", {}) or {}
    handoff_data   = summary.get("handoff", {}) or {}
    faq_data       = summary.get("faq", []) or []

    # Pre-SMS reason visibility — makes it obvious when a propagation break
    # (e.g. top-level-only write bypassing COLLECT_REASON) reaches the router.
    logger.info(
        "[smart_sms] pre-SMS reason: collected=%r session=%r summary.appointment=%r outcome=%r",
        collected.get("reason"),
        session.get("reason"),
        (summary.get("appointment", {}) or {}).get("reason"),
        outcome,
    )

    # Clinic branding
    _clinic      = get_clinic(session.get("clinic_id"))
    clinic_name  = _clinic.get("sms_name") or _clinic.get("display_name") or _clinic.get("clinic_name")
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

    # Defense-in-depth: if the outcome is "abandoned" but session evidence shows
    # a genuine FAQ-engagement interaction (not a dropped booking attempt), treat
    # the call as faq_only so the abandoned template is never sent.
    # Primary fix is in infer_call_outcome() — this guard catches any case where
    # the summary outcome wasn't recalculated before the SMS path was reached.
    if outcome == "abandoned":
        _faq_intent = str(session.get("intent") or "").lower()
        _faq_state  = str(session.get("state") or session.get("flow_state") or "").upper()
        _is_faq_engaged = (
            _faq_intent.startswith("faq")
            or _faq_intent == "general_query"
            or int(session.get("faq_follow_up_count") or 0) >= 1
            or bool(session.get("faq_turns") or [])
            or _faq_state in {
                "FAQ_BOOKING_OFFER", "GENERAL_BOOKING_OFFER",
                "ANSWER_FAQ", "ANSWER_GENERAL",
            }
            or bool(session.get("_faq_ans_at"))
        )
        if _is_faq_engaged:
            logger.info(
                "🛡️  outcome=abandoned but engaged FAQ call detected "
                "(intent=%r state=%r faq_count=%s) — overriding to faq_only for SMS",
                _faq_intent, _faq_state, session.get("faq_follow_up_count"),
            )
            outcome = "faq_only"

    # Booked — handled separately (booking confirmation SMS is already sent)
    # Clinical safety escalation — the caller was told to seek urgent care
    # (999/A&E/111/GP). No marketing/recovery SMS is appropriate here, and the
    # abandoned template ("sorry we couldn't get you booked in…") would be
    # actively harmful. Send nothing; Marcus sees the outcome in the summary
    # row and daily digest.
    if outcome == "safety_escalation":
        logger.info(
            "🚨 safety_escalation — no follow-up SMS (caller directed to "
            "urgent care during the call)"
        )
        return False

    if outcome == "booked":
        logger.info("✅ Booked — booking confirmation SMS already sent")
        return False

    # Cancelled — cancellation confirmation SMS already sent by cancel_appointment tool
    if outcome == "cancelled":
        logger.info("[smart_sms] outcome=cancelled — confirmation already sent during call, skipping")
        return False

    # Reschedule reached transaction stage but backend failed — patient already told verbally
    if outcome == "reschedule_failed":
        logger.info("🔴 reschedule_failed — skipping SMS (patient informed verbally during call)")
        return False

    # Cancelled / any flow where a confirmation SMS was already sent during the call
    if session.get("confirmation_sms_sent"):
        logger.info("📩 Confirmation SMS already sent during call — skipping follow-up")
        return False

    # Idempotency: this router can be invoked from two places (the media-stream
    # cleanup path AND the /twilio/status webhook). Only the first one may send.
    if session.get("followup_sms_sent"):
        logger.info("📩 Follow-up SMS already sent — skipping duplicate")
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
        session["followup_sms_sent"] = True   # idempotency latch (mirrored to /status)
        logger.info("✅ Smart SMS sent [%s] → ***%s", outcome, patient_phone[-4:] if patient_phone else "????")
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
    bupa_mentioned        = _check_bupa_mention(faq_data, insurance_data, session)

    ck = {"clinic_name": clinic_name, "clinic_phone": clinic_phone}

    # Clinic-specific copy inputs (price/insurance). Defaults reproduce the
    # original Theorem copy; a clinic overrides via clinic.json. jv: £52/40-min +
    # no outcome claim, and Option-B insurance (accepts referrals incl. Bupa).
    _c            = get_clinic(session.get("clinic_id")) or {}
    _ins_cfg      = _c.get("insurance") or {}
    _accepts_ref  = bool(_ins_cfg.get("other_insurers_accepted") or _ins_cfg.get("bupa_accepted"))
    # Self-pay-only clinics (vital_edge) must never be told we work with insurers.
    _self_pay     = bool(_ins_cfg.get("self_pay_only")) or (
        _ins_cfg.get("bupa_accepted") is False
        and _ins_cfg.get("other_insurers_accepted") is False
        and bool(_ins_cfg)
    )
    # practitioner lives top-level for some clinics, under prompt_facts for others.
    _prac         = _c.get("practitioner") or (_c.get("prompt_facts") or {}).get("practitioner")
    _price        = _c.get("sms_assessment_price")
    _dur          = _c.get("sms_assessment_duration")
    # `sms_price_line` is a full clause a clinic can set verbatim — needed where
    # "An assessment is £X for Y minutes" is the wrong shape (e.g. a massage
    # clinic with two session lengths). Falls back to the assessment form, then
    # to the original Theorem copy (byte-identical for clinics that set neither).
    _price_line   = (
        _c.get("sms_price_line")
        or (f"An assessment is {_price} for {_dur} minutes. " if (_price and _dur) else None)
        or "A 50-min physio appointment is £75 — most patients see results within 2–3 sessions. "
    )

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
            patient_name=patient_name, bupa_mentioned=True,
            accepts_referrals=_accepts_ref, practitioner=_prac,
            self_pay_only=_self_pay, **ck)

    # 8. FAQ — INSURANCE (non-Bupa)
    if asked_about_insurance:
        return templates.format_insurance_inquiry_sms(
            patient_name=patient_name,
            insurer=insurer or None,
            bupa_mentioned=False,
            accepts_referrals=_accepts_ref,
            practitioner=_prac,
            self_pay_only=_self_pay,
            **ck,
        )

    # 9. FAQ — PRICE
    if asked_about_price:
        return templates.format_price_inquiry_sms(
            price_line=_price_line,
            patient_name=patient_name, **ck)

    # 10. NO AUDIO — safety net graceful close (connection issue, not abandoned)
    if outcome == "no_audio":
        return templates.format_no_audio_sms(
            patient_name=patient_name, **ck)

    # 11. REACHED CONFIRMATION — caller got to "shall I go ahead?" but dropped before saying yes
    if outcome == "reached_confirmation":
        return templates.format_reached_confirmation_sms(
            patient_name=patient_name, **ck)

    # 12. ABANDONED — suppressed when caller already made substantial booking progress
    if outcome == "abandoned" and _booking_has_progressed(session, collected):
        logger.info(
            "🛑 abandoned but booking progressed (name=%r reason=%r type=%r) "
            "— suppressing SMS",
            collected.get("name"), collected.get("reason"), collected.get("patient_type"),
        )
        return None

    # 12. ABANDONED — with a clean condition label only (never raw speech)
    if outcome == "abandoned" and condition_label:
        return templates.format_abandoned_booking_sms(
            patient_name    = patient_name,
            condition_label = condition_label,
            **ck,
        )

    # 13. ABANDONED — general (no clean condition extracted)
    if outcome == "abandoned":
        return templates.format_abandoned_booking_sms(
            patient_name=patient_name, **ck)

    # 14. FAQ — GENERAL (no specific topic)
    if outcome == "faq_only":
        return templates.format_general_thankyou_sms(
            patient_name=patient_name, **ck)

    # 15. TECHNICAL FAILURE
    if outcome == "failed":
        return templates.format_technical_issue_sms(
            patient_name=patient_name, **ck)

    # 16. FALLBACK
    return templates.format_general_thankyou_sms(
        patient_name=patient_name, **ck)


# ============================================================================
# DETECTION HELPERS
# ============================================================================

def _recent_user_texts(session: Dict) -> list:
    """
    Lowercased recent CALLER utterances, drawn from BOTH conversation records:
      • session["turns"]                — {"user": ...} (theorem/flow.py)
      • session["conversation_history"] — {"role": "user", "content": ...} (media
        streams / template clinics like jv)
    Without the conversation_history source the price/insurance detectors were
    blind on the media path, so those calls fell back to the generic template.
    """
    texts: list = []
    for turn in (session.get("turns", []) or [])[-8:]:
        if isinstance(turn, dict) and turn.get("user"):
            texts.append(str(turn["user"]).lower())
    for turn in (session.get("conversation_history", []) or [])[-16:]:
        if isinstance(turn, dict) and turn.get("role") == "user" and turn.get("content"):
            texts.append(str(turn["content"]).lower())
    return texts


def _check_price_question(faq_data: list, session: Dict) -> bool:
    """Check if patient asked about pricing."""
    keywords = {"price", "cost", "how much", "fee", "charge", "expensive", "£"}
    for turn in faq_data:
        if isinstance(turn, dict) and any(k in turn.get("question", "").lower() for k in keywords):
            return True
    return any(any(k in t for k in keywords) for t in _recent_user_texts(session))


def _check_insurance_question(faq_data: list, insurance_data: Dict, session: Dict) -> bool:
    """Check if patient asked about insurance."""
    if insurance_data.get("insurer_name"):
        return True
    keywords = {"insurance", "insurer", "bupa", "axa", "aviva", "vitality", "claim", "cover"}
    for turn in faq_data:
        if isinstance(turn, dict) and any(k in turn.get("question", "").lower() for k in keywords):
            return True
    return any(any(k in t for k in keywords) for t in _recent_user_texts(session))


def _check_bupa_mention(faq_data: list, insurance_data: Dict, session: Dict) -> bool:
    """Check if Bupa was specifically mentioned."""
    if ((insurance_data or {}).get("insurer_name") or "").lower() == "bupa":
        return True
    for turn in faq_data:
        if isinstance(turn, dict) and "bupa" in turn.get("question", "").lower():
            return True
    return any("bupa" in t for t in _recent_user_texts(session))


def _booking_has_progressed(session: Dict, collected: Dict) -> bool:
    """
    True if the call reached a meaningful operational stage in any flow.

    Strong single-signal check (reschedule/cancel deep progress):
      rc_appointment_confirmed — caller's existing appointment was found and confirmed
      reschedule_confirmed     — caller verbally confirmed the new reschedule time
      slots_offered            — availability was fetched and days/times offered
      slot_pending_confirmation — a specific slot is awaiting caller confirmation
      slot_confirmed           — slot confirmed by caller, not yet saved to calendar

    Booking-intent soft check (requires ≥2 signals to filter shallow calls where
    Twilio caller-ID pre-populates fields but caller said nothing meaningful).
    """
    # Strong signals — any one is sufficient
    if (
        session.get("rc_appointment_confirmed")    # reschedule/cancel: appt found
        or session.get("reschedule_confirmed")     # reschedule: caller confirmed new time
        or session.get("slots_offered")            # booking: availability offered
        or session.get("slot_pending_confirmation") # booking: specific slot pending confirm
        or session.get("slot_confirmed")           # booking: slot confirmed
    ):
        return True

    # Booking soft signals — require 2+ to avoid shallow-call false positives
    _intent_is_booking = "book" in str(
        session.get("intent") or session.get("last_intent") or ""
    ).lower()
    _name_captured    = bool(collected.get("name") or session.get("name_fragment"))
    _reason_captured  = bool(collected.get("reason"))
    _type_captured    = bool(collected.get("patient_type"))
    _phone_confirmed  = (
        session.get("phone_confirmed") is True
        and bool(collected.get("phone") or session.get("phone_number"))
    )
    active_signals = sum([
        _intent_is_booking,
        _name_captured,
        _reason_captured,
        _type_captured,
        _phone_confirmed,
    ])
    return active_signals >= 2
