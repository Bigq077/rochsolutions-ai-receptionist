# app/tools/actionable_summary.py
"""
Build actionable call summaries for Mark.
FIXED: Correct column alignment and data extraction.
"""

from typing import Dict, Any, List
import json
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


def build_actionable_summary_row(summary: Dict[str, Any]) -> List[Any]:
    """
    Convert summary into 15-column row for Google Sheets.
    
    Columns (EXACT ORDER):
    1. Summary (human-readable)
    2. Outcome (abandoned/booked/faq_only/manual_followup)
    3. Booked? (YES/NO)
    4. Patient Name
    5. Phone
    6. Service/Reason
    7. Asked About Price? (YES/NO)
    8. Has Insurance (YES/NO)
    9. Insurance Company
    10. Manual Followup (YES/NO)
    11. Followup Notes
    12. Call Date
    13. Call Duration (seconds)
    14. Call SID
    15. Full Details (JSON)
    """
    
    # Get raw session data
    raw_session = summary.get("_raw_session", {})
    
    # Extract sections
    collected = raw_session.get("collected", {}) or {}
    meta = summary.get("meta", {}) or {}
    patient_info = summary.get("patient", {}) or {}
    appt_info = summary.get("appointment", {}) or {}
    insurance_info = summary.get("insurance", {}) or {}
    handoff_info = summary.get("handoff", {}) or {}
    
    # =================================================================
    # COLUMN 1: OUTCOME (most important - extract first!)
    # =================================================================
    outcome = summary.get("outcome", "unknown")
    
    # =================================================================
    # COLUMN 2: BOOKED?
    # =================================================================
    booked = "YES" if outcome in ("booked", "rescheduled") else "NO"
    
    # =================================================================
    # COLUMN 3: PATIENT NAME
    # =================================================================
    patient_name = (
        collected.get("name") or 
        patient_info.get("name") or 
        ""
    )
    
    # =================================================================
    # COLUMN 4: PHONE (from Twilio caller ID)
    # =================================================================
    phone = (
        raw_session.get("twilio_from") or  # Caller ID from Twilio
        collected.get("phone") or 
        patient_info.get("phone") or 
        meta.get("from") or 
        ""
    )
    # Filter out Twilio client IDs
    if phone and phone.startswith("client:"):
        phone = ""
    
    # =================================================================
    # COLUMN 5: SERVICE/REASON
    # =================================================================
    service = (
        collected.get("reason") or 
        appt_info.get("reason") or 
        appt_info.get("service") or 
        ""
    )
    
    # =================================================================
    # COLUMN 6: ASKED ABOUT PRICE?
    # =================================================================
    asked_price = _detect_price_question(summary, raw_session)
    
    # =================================================================
    # COLUMN 7: HAS INSURANCE
    # =================================================================
    insurer = (
        collected.get("insurer") or 
        insurance_info.get("insurer_name") or 
        ""
    )
    has_insurance = "YES" if insurer else "NO"
    
    # =================================================================
    # COLUMN 8: INSURANCE COMPANY
    # =================================================================
    insurance_company = insurer
    
    # =================================================================
    # COLUMN 9: MANUAL FOLLOWUP NEEDED
    # =================================================================
    manual_followup = "YES" if handoff_info.get("manual_followup_needed") else "NO"
    
    # =================================================================
    # COLUMN 10: FOLLOWUP NOTES
    # =================================================================
    followup_notes = handoff_info.get("reason", "")
    
    # =================================================================
    # COLUMN 11: CALL DATE
    # =================================================================
    call_date = meta.get("ended_at_utc", "")
    if call_date:
        try:
            dt = datetime.fromisoformat(call_date.replace("Z", "+00:00"))
            call_date = dt.strftime("%Y-%m-%d %H:%M")
        except:
            pass
    
    # =================================================================
    # COLUMN 12: CALL DURATION (seconds)
    # =================================================================
    duration = meta.get("duration_sec", "") or raw_session.get("twilio_duration_sec", "")
    
    # =================================================================
    # COLUMN 13: CALL SID
    # =================================================================
    call_sid = meta.get("call_sid", "") or raw_session.get("call_sid", "")
    
    # =================================================================
    # COLUMN 14: HUMAN-READABLE SUMMARY (build at end)
    # =================================================================
    summary_text = _build_summary_text(
        outcome=outcome,
        name=patient_name,
        service=service,
        duration=duration,
        followup_notes=followup_notes,
    )
    
    # =================================================================
    # COLUMN 15: FULL DETAILS (JSON)
    # =================================================================
    full_details = json.dumps(summary, ensure_ascii=False)
    if len(full_details) > 45000:
        full_details = full_details[:45000] + "...truncated"
    
    # =================================================================
    # BUILD ROW - EXACT ORDER MATTERS!
    # =================================================================
    
    row = [
        summary_text,           # 1. Summary
        outcome,                # 2. Outcome
        booked,                 # 3. Booked?
        patient_name,           # 4. Patient Name
        phone,                  # 5. Phone
        service,                # 6. Service/Reason
        asked_price,            # 7. Asked About Price?
        has_insurance,          # 8. Has Insurance
        insurance_company,      # 9. Insurance Company
        manual_followup,        # 10. Manual Followup Needed
        followup_notes,         # 11. Followup Notes
        call_date,              # 12. Call Date
        duration,               # 13. Call Duration
        call_sid,               # 14. Call SID
        full_details,           # 15. Full Details
    ]
    
    # Debug log
    logger.info(
        f"📊 Summary row built - "
        f"Outcome: {outcome}, "
        f"Name: {patient_name or 'None'}, "
        f"Phone: {'Yes' if phone else 'No'}, "
        f"Service: {service or 'None'}, "
        f"Duration: {duration}s"
    )
    
    return row


def _build_summary_text(
    outcome: str,
    name: str,
    service: str,
    duration: str,
    followup_notes: str,
) -> str:
    """Build one-line human-readable summary."""
    
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
            elif dur < 30:
                return f"⚠️ {display_name} - abandoned after {dur}s"
            elif service:
                return f"⚠️ {display_name} - started booking {service} but abandoned ({dur}s)"
            else:
                return f"⚠️ {display_name} - abandoned call ({dur}s)"
        except:
            return f"⚠️ {display_name} - abandoned call"
    
    elif outcome == "failed":
        return f"🔧 {display_name} - call failed (technical issue)"
    
    else:
        return f"{display_name} - {outcome}"


def _detect_price_question(summary: Dict, session: Dict) -> str:
    """Detect if patient asked about pricing."""
    
    price_keywords = ["price", "cost", "how much", "fee", "charge", "£", "pound", "expensive"]
    
    # Check FAQ
    faq = summary.get("faq", []) or []
    for item in faq:
        if isinstance(item, dict):
            question = item.get("question", "").lower()
            if any(keyword in question for keyword in price_keywords):
                return "YES"
    
    # Check conversation turns
    turns = session.get("turns", []) or []
    for turn in turns[-5:]:  # Last 5 turns
        if isinstance(turn, dict):
            user_msg = turn.get("user", "").lower()
            if any(keyword in user_msg for keyword in price_keywords):
                return "YES"
    
    return "NO"
