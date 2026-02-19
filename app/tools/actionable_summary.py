# app/tools/actionable_summary.py - SIMPLE VERSION
"""
Build actionable call summaries directly from session data.
This version is simpler and more reliable.
"""

from typing import Dict, Any, List
import json
from datetime import datetime


def build_actionable_summary_row(summary: Dict[str, Any]) -> List[Any]:
    """
    Convert summary into actionable row for Mark.
    Works directly with the data structure from build_call_summary.
    """
    
    # Get the RAW session data if available (more reliable)
    # build_call_summary should pass the original session in summary["_raw_session"]
    raw_session = summary.get("_raw_session", {})
    
    # Collected data from conversation
    collected = raw_session.get("collected", {}) or summary.get("patient", {}) or {}
    
    # Meta data about the call
    meta = summary.get("meta", {}) or {}
    
    # =================================================================
    # EXTRACT DATA WITH MULTIPLE FALLBACKS
    # =================================================================
    
    # PATIENT NAME
    patient_name = (
        collected.get("name") or
        summary.get("patient", {}).get("name") or
        ""
    )
    
    # PHONE NUMBER
    phone = (
        collected.get("phone") or
        summary.get("patient", {}).get("phone") or
        meta.get("from") or
        ""
    )
    # Skip if it's a Twilio client ID
    if phone and phone.startswith("client:"):
        phone = ""
    
    # OUTCOME
    outcome = summary.get("outcome", "unknown")
    
    # BOOKED?
    booked = "YES" if outcome in ("booked", "rescheduled") else "NO"
    
    # SERVICE/REASON
    service = (
        collected.get("reason") or
        summary.get("appointment", {}).get("reason") or
        summary.get("appointment", {}).get("service") or
        ""
    )
    
    # INSURANCE
    insurer = (
        collected.get("insurer") or
        summary.get("insurance", {}).get("insurer_name") or
        ""
    )
    has_insurance = "YES" if insurer else "NO"
    
    # MANUAL FOLLOWUP
    handoff = summary.get("handoff", {}) or {}
    manual_followup = "YES" if handoff.get("manual_followup_needed") else "NO"
    followup_notes = handoff.get("reason", "")
    
    # ASKED ABOUT PRICE?
    asked_price = _detect_price_question(summary, raw_session)
    
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
    
    # HUMAN SUMMARY
    summary_text = _build_summary_text(
        outcome=outcome,
        name=patient_name,
        service=service,
        duration=duration,
        handoff_notes=followup_notes,
    )
    
    # FULL DETAILS
    full_details = json.dumps(summary, ensure_ascii=False)
    if len(full_details) > 45000:
        full_details = full_details[:45000] + "..."
    
    # =================================================================
    # RETURN ROW (15 columns)
    # =================================================================
    
    return [
        summary_text,           # 1. Summary
        outcome,                # 2. Outcome
        booked,                 # 3. Booked?
        patient_name,           # 4. Patient Name
        phone,                  # 5. Phone
        service,                # 6. Service/Reason
        asked_price,            # 7. Asked About Price?
        has_insurance,          # 8. Has Insurance
        insurer,                # 9. Insurance Company
        manual_followup,        # 10. Manual Followup Needed
        followup_notes,         # 11. Followup Notes
        call_date,              # 12. Call Date
        duration,               # 13. Call Duration
        call_sid,               # 14. Call SID
        full_details,           # 15. Full Details (JSON)
    ]


def _build_summary_text(outcome: str, name: str, service: str, duration: str, handoff_notes: str) -> str:
    """Build human-readable one-liner."""
    
    display_name = name if name else "Anonymous caller"
    
    if outcome == "booked":
        if service:
            return f"{display_name} BOOKED {service}"
        return f"{display_name} BOOKED appointment"
    
    elif outcome == "rescheduled":
        return f"{display_name} RESCHEDULED appointment"
    
    elif outcome == "manual_followup":
        if handoff_notes:
            return f"{display_name} - NEEDS CALLBACK: {handoff_notes}"
        return f"{display_name} - NEEDS CALLBACK"
    
    elif outcome == "faq_only":
        return f"{display_name} - asked questions but didn't book"
    
    elif outcome == "abandoned":
        try:
            dur = int(duration) if duration else 0
            if dur < 10:
                return f"{display_name} - hung up immediately ({dur}s)"
            elif service:
                return f"{display_name} - started booking {service} but didn't complete"
            else:
                return f"{display_name} - abandoned booking"
        except:
            return f"{display_name} - abandoned call"
    
    elif outcome == "failed":
        return f"{display_name} - call failed (technical issue)"
    
    else:
        return f"{display_name} - {outcome}"


def _detect_price_question(summary: Dict, session: Dict) -> str:
    """Detect if they asked about pricing."""
    
    price_words = ["price", "cost", "how much", "fee", "charge", "£"]
    
    # Check FAQ
    faq = summary.get("faq", []) or []
    for item in faq:
        if isinstance(item, dict):
            q = item.get("question", "").lower()
            if any(word in q for word in price_words):
                return "YES"
    
    # Check conversation turns
    turns = session.get("turns", []) or []
    for turn in turns[-5:]:  # Last 5 turns
        if isinstance(turn, dict):
            user_msg = turn.get("user", "").lower()
            if any(word in user_msg for word in price_words):
                return "YES"
    
    return "NO"
