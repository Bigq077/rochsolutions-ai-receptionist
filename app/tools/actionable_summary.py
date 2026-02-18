# app/tools/actionable_summary.py
"""
Build actionable call summaries that Mark can actually use.
Focus on outcomes, not technical details.
"""

from typing import Dict, Any, List
import json
from datetime import datetime


def build_actionable_summary_row(summary: Dict[str, Any]) -> List[Any]:
    """
    Convert full summary into actionable row for Mark.
    
    Columns (15 total):
    1. Summary (human-readable one-liner)
    2. Outcome (booked/abandoned/faq_only/etc)
    3. Booked? (YES/NO)
    4. Patient Name
    5. Phone
    6. Service/Reason (what they wanted)
    7. Asked About Price? (YES/NO)
    8. Has Insurance (YES/NO)
    9. Insurance Company
    10. Manual Followup Needed (YES/NO)
    11. Followup Notes
    12. Call Date
    13. Call Duration (seconds)
    14. Call SID
    15. Full Details (JSON for reference)
    """
    
    meta = summary.get("meta", {}) or {}
    patient = summary.get("patient", {}) or {}
    appt = summary.get("appointment", {}) or {}
    ins = summary.get("insurance", {}) or {}
    handoff = summary.get("handoff", {}) or {}
    faq = summary.get("faq", []) or []
    
    # 1. HUMAN-READABLE SUMMARY
    summary_text = _build_human_summary(summary)
    
    # 2. OUTCOME
    outcome = summary.get("outcome", "unknown")
    
    # 3. BOOKED?
    booked = "YES" if outcome in ("booked", "rescheduled") else "NO"
    
    # 4. PATIENT NAME
    patient_name = patient.get("name", "")
    
    # 5. PHONE
    phone = patient.get("phone", "")
    
    # 6. SERVICE/REASON
    service = appt.get("service") or appt.get("reason") or ""
    
    # 7. ASKED ABOUT PRICE?
    asked_price = _check_price_questions(faq, summary)
    
    # 8. HAS INSURANCE
    has_insurance = "YES" if ins.get("insurer_name") else "NO"
    
    # 9. INSURANCE COMPANY
    insurance_company = ins.get("insurer_name", "")
    
    # 10. MANUAL FOLLOWUP NEEDED
    manual_followup = "YES" if handoff.get("manual_followup_needed") else "NO"
    
    # 11. FOLLOWUP NOTES
    followup_notes = handoff.get("reason", "")
    
    # 12. CALL DATE
    call_date = meta.get("ended_at_utc", "")
    if call_date:
        try:
            dt = datetime.fromisoformat(call_date.replace("Z", "+00:00"))
            call_date = dt.strftime("%Y-%m-%d %H:%M")
        except:
            pass
    
    # 13. CALL DURATION
    duration = meta.get("duration_sec", "")
    
    # 14. CALL SID
    call_sid = meta.get("call_sid", "")
    
    # 15. FULL DETAILS (JSON)
    full_details = json.dumps(summary, ensure_ascii=False)
    if len(full_details) > 45000:
        full_details = full_details[:45000] + "..."
    
    return [
        summary_text,
        outcome,
        booked,
        patient_name,
        phone,
        service,
        asked_price,
        has_insurance,
        insurance_company,
        manual_followup,
        followup_notes,
        call_date,
        duration,
        call_sid,
        full_details,
    ]


def _build_human_summary(summary: Dict[str, Any]) -> str:
    """
    Build a one-line human-readable summary.
    
    Examples:
    - "Sarah booked physiotherapy for lower back pain (Mon 2pm)"
    - "John asked about pricing - needs callback"
    - "Anonymous caller - hung up after 8 seconds"
    """
    patient = summary.get("patient", {}) or {}
    appt = summary.get("appointment", {}) or {}
    outcome = summary.get("outcome", "")
    
    name = patient.get("name") or "Anonymous caller"
    service = appt.get("service") or appt.get("reason")
    slot = appt.get("selected_slot")
    
    # Build summary based on outcome
    if outcome == "booked":
        if service and slot:
            return f"{name} BOOKED {service} ({slot})"
        elif service:
            return f"{name} BOOKED {service}"
        else:
            return f"{name} BOOKED appointment"
    
    elif outcome == "rescheduled":
        if slot:
            return f"{name} RESCHEDULED to {slot}"
        else:
            return f"{name} RESCHEDULED appointment"
    
    elif outcome == "manual_followup":
        handoff_reason = (summary.get("handoff", {}) or {}).get("reason", "")
        if handoff_reason:
            return f"{name} - NEEDS CALLBACK: {handoff_reason}"
        else:
            return f"{name} - NEEDS CALLBACK"
    
    elif outcome == "faq_only":
        faq_topics = _extract_faq_topics(summary.get("faq", []))
        if faq_topics:
            return f"{name} asked about: {faq_topics}"
        else:
            return f"{name} - general questions only"
    
    elif outcome == "abandoned":
        duration = (summary.get("meta", {}) or {}).get("duration_sec")
        try:
            dur_int = int(duration) if duration else 0
            if dur_int < 10:
                return f"{name} - hung up immediately ({dur_int}s)"
            elif dur_int < 30:
                return f"{name} - abandoned after {dur_int}s"
            else:
                return f"{name} - started booking but didn't complete"
        except:
            return f"{name} - abandoned call"
    
    elif outcome == "failed":
        return f"{name} - call failed (technical issue)"
    
    else:
        return f"{name} - {outcome}"


def _check_price_questions(faq_turns: List[Any], summary: Dict[str, Any]) -> str:
    """Check if patient asked about pricing."""
    
    # Check FAQ turns for pricing questions
    for turn in faq_turns:
        if isinstance(turn, dict):
            question = turn.get("question", "").lower()
            if any(word in question for word in ["price", "cost", "how much", "fee", "charge", "expensive"]):
                return "YES"
    
    # Check conversation turns
    convo = summary.get("conversation", {}) or {}
    last_message = (convo.get("last_user_message") or "").lower()
    if any(word in last_message for word in ["price", "cost", "how much", "fee", "charge"]):
        return "YES"
    
    return "NO"


def _extract_faq_topics(faq_turns: List[Any]) -> str:
    """Extract main topics from FAQ questions."""
    topics = []
    
    for turn in faq_turns[:3]:  # Only first 3
        if isinstance(turn, dict):
            question = turn.get("question", "")
            # Extract key words
            q_lower = question.lower()
            if "price" in q_lower or "cost" in q_lower:
                topics.append("pricing")
            elif "hour" in q_lower or "open" in q_lower:
                topics.append("hours")
            elif "location" in q_lower or "address" in q_lower:
                topics.append("location")
            elif "insurance" in q_lower:
                topics.append("insurance")
            elif "service" in q_lower or "treatment" in q_lower:
                topics.append("services")
    
    if topics:
        return ", ".join(topics[:3])
    return "general info"
