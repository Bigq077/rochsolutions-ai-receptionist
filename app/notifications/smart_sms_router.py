# app/notifications/smart_sms_router.py
"""
Smart SMS routing system - sends the right message for every call type.
Analyzes call outcome and conversation to choose optimal template.
"""

import logging
from typing import Dict, Any, Optional
from app.notifications.booking_sms import send_sms
from app.notifications import templates

logger = logging.getLogger(__name__)


async def send_smart_followup_sms(
    session: Dict[str, Any],
    summary: Dict[str, Any],
) -> bool:
    """
    Route to appropriate SMS template based on call context.
    """
    
    # Extract key data
    collected = session.get("collected", {}) or {}
    
    # ✅ FIX: Get phone from Twilio metadata (the number they're calling FROM)
    # This is automatically available, they don't need to tell us
    patient_phone = (
        session.get("twilio_from", "") or  # Caller's phone number
        collected.get("phone", "")          # Fallback to collected if available
    )
    
    # Clean Twilio client IDs (if any)
    if patient_phone and patient_phone.startswith("client:"):
        patient_phone = ""
    
    # Get first name safely (handle empty names)
    name = collected.get("name", "") or ""
    patient_name = name.split()[0] if name.strip() else ""
    
    outcome = summary.get("outcome", "")
    meta_data = summary.get("meta", {}) or {}
    insurance_data = summary.get("insurance", {}) or {}
    handoff_data = summary.get("handoff", {}) or {}
    faq_data = summary.get("faq", []) or []
    
    # Get duration
    duration = 0
    try:
        duration = int(meta_data.get("duration_sec", 0) or 0)
    except:
        pass
    
    # =================================================================
    # FILTER RULES
    # =================================================================
    
    # RULE 1: No phone number = can't send SMS
    if not patient_phone:
        logger.info("📵 No phone number - skipping SMS")
        return False
    
    # RULE 2: Call too short (<15s) = probably wrong number
    if duration < 15:
        logger.info(f"⏱️  Call too short ({duration}s) - skipping SMS")
        return False
    
    # RULE 3: Already booked = skip for now (will add with Acuity)
    if outcome == "booked":
        logger.info("✅ Booked - SMS will be added with Acuity integration")
        return False
    
    # =================================================================
    # CHOOSE TEMPLATE
    # =================================================================
    
    message = _choose_template(
        outcome=outcome,
        patient_name=patient_name,
        collected=collected,
        insurance_data=insurance_data,
        handoff_data=handoff_data,
        faq_data=faq_data,
        session=session,
    )
    
    if not message:
        logger.warning(f"⚠️  No template found for outcome: {outcome}")
        return False
    
    # =================================================================
    # SEND SMS
    # =================================================================
    
    try:
        await send_sms(to=patient_phone, message=message)
        logger.info(f"✅ Smart SMS sent to {patient_phone}: {outcome}")
        return True
    except Exception as e:
        logger.error(f"❌ Failed to send SMS: {e}", exc_info=True)
        return False


def _choose_template(
    outcome: str,
    patient_name: str,
    collected: Dict,
    insurance_data: Dict,
    handoff_data: Dict,
    faq_data: list,
    session: Dict,
) -> Optional[str]:
    """Choose the right template based on call outcome."""
    
    reason = collected.get("reason", "")
    location = session.get("selected_location", "")
    insurer = insurance_data.get("insurer_name", "")
    
    # Detect what they asked about
    asked_about_price = _check_price_question(faq_data, session)
    asked_about_insurance = _check_insurance_question(faq_data, insurance_data, session)
    bupa_mentioned = _check_bupa_mention(faq_data, insurance_data)
    
    # =================================================================
    # ROUTING LOGIC
    # =================================================================
    
    # 1. ABANDONED BOOKING
    if outcome == "abandoned":
        return templates.format_abandoned_booking_sms(
            patient_name=patient_name,
            reason=reason,
            location=location,
        )
    
    # 2. FAQ ONLY - PRICE FOCUSED
    elif outcome == "faq_only" and asked_about_price:
        return templates.format_price_inquiry_sms(
            patient_name=patient_name,
            service=reason or None,
        )
    
    # 3. FAQ ONLY - INSURANCE FOCUSED
    elif outcome == "faq_only" and asked_about_insurance:
        return templates.format_insurance_inquiry_sms(
            patient_name=patient_name,
            insurer=insurer or None,
            bupa_mentioned=bupa_mentioned,
        )
    
    # 4. FAQ ONLY - GENERAL
    elif outcome == "faq_only":
        topics = _extract_faq_topics(faq_data)
        return templates.format_info_only_sms(
            patient_name=patient_name,
            topics=topics,
        )
    
    # 5. MANUAL FOLLOWUP - NO SUITABLE TIME
    elif outcome == "manual_followup" and "time" in handoff_data.get("reason", "").lower():
        return templates.format_no_suitable_time_sms(
            patient_name=patient_name,
            reason=reason,
        )
    
    # 6. MANUAL FOLLOWUP - RESCHEDULE
    elif outcome == "manual_followup" and any(
        word in handoff_data.get("reason", "").lower() 
        for word in ["reschedule", "change", "move"]
    ):
        return templates.format_reschedule_request_sms(patient_name=patient_name)
    
    # 7. MANUAL FOLLOWUP - CANCELLATION
    elif outcome == "manual_followup" and any(
        word in handoff_data.get("reason", "").lower() 
        for word in ["cancel", "cancellation"]
    ):
        return templates.format_cancellation_request_sms(patient_name=patient_name)
    
    # 8. MANUAL FOLLOWUP - GENERAL
    elif outcome == "manual_followup":
        return templates.format_callback_confirmation(patient_name=patient_name)
    
    # 9. TECHNICAL FAILURE
    elif outcome == "failed":
        return templates.format_technical_issue_sms(patient_name=patient_name)
    
    # 10. FALLBACK
    else:
        return templates.format_general_thankyou_sms(patient_name=patient_name)


def _check_price_question(faq_data: list, session: Dict) -> bool:
    """Check if patient asked about pricing."""
    price_keywords = ["price", "cost", "how much", "fee", "charge", "expensive", "£"]
    
    # Check FAQ
    for turn in faq_data:
        if isinstance(turn, dict):
            question = turn.get("question", "").lower()
            if any(keyword in question for keyword in price_keywords):
                return True
    
    # Check conversation
    turns = session.get("turns", []) or []
    for turn in turns[-5:]:
        if isinstance(turn, dict):
            user_msg = turn.get("user", "").lower()
            if any(keyword in user_msg for keyword in price_keywords):
                return True
    
    return False


def _check_insurance_question(faq_data: list, insurance_data: Dict, session: Dict) -> bool:
    """Check if patient asked about insurance."""
    insurance_keywords = ["insurance", "insurer", "bupa", "axa", "aviva", "vitality", "claim", "cover"]
    
    # If they provided insurance info, they asked about it
    if insurance_data.get("insurer_name"):
        return True
    
    # Check FAQ
    for turn in faq_data:
        if isinstance(turn, dict):
            question = turn.get("question", "").lower()
            if any(keyword in question for keyword in insurance_keywords):
                return True
    
    # Check conversation
    turns = session.get("turns", []) or []
    for turn in turns[-5:]:
        if isinstance(turn, dict):
            user_msg = turn.get("user", "").lower()
            if any(keyword in user_msg for keyword in insurance_keywords):
                return True
    
    return False


def _check_bupa_mention(faq_data: list, insurance_data: Dict) -> bool:
    """Check if Bupa was specifically mentioned."""
    if insurance_data.get("insurer_name", "").lower() == "bupa":
        return True
    
    for turn in faq_data:
        if isinstance(turn, dict):
            question = turn.get("question", "").lower()
            if "bupa" in question:
                return True
    
    return False


def _extract_faq_topics(faq_data: list) -> Optional[str]:
    """Extract main topics from FAQ questions."""
    topics = []
    
    for turn in faq_data[:3]:  # First 3 questions
        if isinstance(turn, dict):
            question = turn.get("question", "").lower()
            
            if any(word in question for word in ["price", "cost", "how much"]):
                if "pricing" not in topics:
                    topics.append("pricing")
            
            elif any(word in question for word in ["hour", "open", "when"]):
                if "hours" not in topics:
                    topics.append("hours")
            
            elif any(word in question for word in ["where", "location", "address"]):
                if "location" not in topics:
                    topics.append("location")
            
            elif "insurance" in question:
                if "insurance" not in topics:
                    topics.append("insurance")
            
            elif any(word in question for word in ["service", "treatment", "help with"]):
                if "services" not in topics:
                    topics.append("services")
    
    if topics:
        return ", ".join(topics[:3])
    
    return None
