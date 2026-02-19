# app/tools/actionable_summary.py
"""
Build actionable call summaries for Mark.
Extracts data directly from session to ensure accurate Google Sheets reporting.
"""

from typing import Dict, Any, List
import json
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


def build_actionable_summary_row(summary: Dict[str, Any]) -> List[Any]:
    """
    Convert summary into actionable 15-column row for Mark's Google Sheet.
    
    Columns:
    1. Summary - One-line human readable
    2. Outcome - booked/abandoned/faq_only/manual_followup/failed
    3. Booked? - YES/NO
    4. Patient Name
    5. Phone
    6. Service/Reason - What they wanted help with
    7. Asked About Price? - YES/NO
    8. Has Insurance - YES/NO
    9. Insurance Company - Name or empty
    10. Manual Followup Needed - YES/NO
    11. Followup Notes - Why followup needed
    12. Call Date - Formatted timestamp
    13. Call Duration - Seconds
    14. Call SID - Twilio identifier
    15. Full Details - Complete JSON for reference
    """
    
    # Get raw session data (most reliable source)
    raw_session = summary.get("_raw_session", {})
    
    # Collected data from the conversation
    collected = raw_session.get("collected", {}) or {}
    
    # Meta data about the call
    meta = summary.get("meta", {}) or {}
    
    # Summary sections
    patient_info = summary.get("patient", {}) or {}
    appt_info = summary.get("appointment", {}) or {}
    insurance_info = summary.get("insurance", {}) or {}
    handoff_info = summary.get("handoff", {}) or {}
    
    # =================================================================
    # EXTRACT DATA WITH FALLBACKS
    # =================================================================
    
    # PATIENT NAME - Try multiple sources
    patient_name = (
        collected.get("name") or 
        patient_info.get("name") or 
        ""
    )
    
    # PHONE - Try multiple sources, filter out Twilio client IDs
    phone = (
        collected.get("phone") or 
        patient_info.get("phone") or 
        meta.get("from") or 
        ""
    )
    if phone and phone.startswith("client:"):
        phone = ""  # Twilio client, not real phone
    
    # OUTCOME
    outcome = summary.get("outcome", "unknown")
    
    # BOOKED?
    booked = "YES" if outcome in ("booked", "rescheduled") else "NO"
    
    # SERVICE/REASON - What they called about
    service = (
        collected.get("reason") or 
        appt_info.get("reason") or 
        appt_info.get("service") or 
        ""
    )
    
    # INSURANCE
    insurer = (
        collected.get("insurer") or 
        insurance_info.get("insurer_name") or 
        ""
    )
    has_insurance = "YES" if insurer else "NO"
    
    # ASKED ABOUT PRICE?
    asked_price = _detect_price_question(summary, raw_session)
    
    # MANUAL FOLLOWUP
    manual_followup = "YES" if handoff_info.get("manual_followup_needed") else "NO"
    followup_notes = handoff_info.get("reason", "")
    
    # CALL METADATA
    call_date = meta.get("ended_at_utc", "")
    if call_date:
        try:
            dt = datetime.fromisoformat(call_date.replace("Z", "+00:00"))
            call_date = dt.strftime("%Y-%m-%d %H:%M")
        except:
            pass
    
    duration = meta.get("duration_sec", "")
    call_sid = meta.get("call_sid", "")
    
    # HUMAN-READABLE SUMMARY
    summary_text = _build_summary_text(
        outcome=outcome,
        name=patient_name,
        service=service,
        duration=duration,
        followup_notes=followup_notes,
    )
    
    # FULL DETAILS (JSON)
    full_details = json.dumps(summary, ensure_ascii=False)
    if len(full_details) > 45000:  # Google Sheets cell limit
        full_details = full_details[:45000] + "...truncated"
    
    # =================================================================
    # BUILD ROW
    # =================================================================
    
    row = [
        summary_text,           # 1
        outcome,                # 2
        booked,                 # 3
        patient_name,           # 4
        phone,                  # 5
        service,                # 6
        asked_price,            # 7
        has_insurance,          # 8
        insurer,                # 9
        manual_followup,        # 10
        followup_notes,         # 11
        call_date,              # 12
        duration,               # 13
        call_sid,               # 14
        full_details,           # 15
    ]
    
    # Log for debugging
    logger.info(
        f"📊 Summary row built - Name: {patient_name or 'None'}, "
        f"Phone: {'Yes' if phone else 'No'}, "
        f"Outcome: {outcome}, "
        f"Service: {service or 'None'}"
    )
    
    return row


def _build_summary_text(
    outcome: str, 
    name: str, 
    service: str, 
    duration: str, 
    followup_notes: str
) -> str:
    """Build one-line human-readable summary for Mark."""
    
    display_name = name if name else "Anonymous caller"
    
    if outcome == "booked":
        if service:
            return f"✅ {display_name} BOOKED {service}"
        return f"✅ {display_name} BOOKED appointment"
    
    elif outcome == "rescheduled":
        return f"🔄 {display_name} RESCHEDULED appointment"
    
    elif outcome == "manual_followup":
        if followup_notes:
            return f"📞 {display_name} - NEEDS CALLBACK: {followup_notes}"
        return f"📞 {display_name} - NEEDS CALLBACK"
    
    elif outcome == "faq_only":
        if service:
            return f"❓ {display_name} - asked about {service} but didn't book"
        return f"❓ {display_name} - asked questions but didn't book"
    
    elif outcome == "abandoned":
        try:
            dur = int(duration) if duration else 0
            if dur < 10:
                return f"❌ {display_name} - hung up immediately ({dur}s)"
            elif service:
                return f"⚠️ {display_name} - started booking {service} but didn't complete"
            else:
                return f"⚠️ {display_name} - abandoned booking"
        except:
            return f"⚠️ {display_name} - abandoned call"
    
    elif outcome == "failed":
        return f"🔧 {display_name} - call failed (technical issue)"
    
    else:
        return f"{display_name} - {outcome}"


def _detect_price_question(summary: Dict, session: Dict) -> str:
    """Detect if patient asked about pricing."""
    
    price_keywords = ["price", "cost", "how much", "fee", "charge", "£", "pound", "expensive"]
    
    # Check FAQ turns in summary
    faq = summary.get("faq", []) or []
    for item in faq:
        if isinstance(item, dict):
            question = item.get("question", "").lower()
            if any(keyword in question for keyword in price_keywords):
                return "YES"
    
    # Check conversation turns in raw session
    turns = session.get("turns", []) or []
    for turn in turns[-5:]:  # Last 5 turns
        if isinstance(turn, dict):
            user_msg = turn.get("user", "").lower()
            if any(keyword in user_msg for keyword in price_keywords):
                return "YES"
    
    return "NO"
