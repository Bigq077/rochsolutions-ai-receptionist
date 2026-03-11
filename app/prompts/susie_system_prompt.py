# app/prompts/susie_system_prompt.py
"""
Builds the dynamic system prompt for Susie, the AI receptionist.

The prompt is rebuilt on every turn so it always reflects the current
patient context (name, location, reason already collected), clinic details,
and any location-specific hours or addresses.
"""
from __future__ import annotations

from typing import Any, Dict


def get_system_prompt(session: Dict[str, Any]) -> str:
    """
    Return the full system prompt for Susie, personalised to this call.

    Reads from:
      - session["clinic_id"]         → which clinic
      - session["selected_location"] → which location (for Theorem)
      - session["collected"]         → what we already know about this patient
    """
    from app.clinic_config import get_clinic

    clinic = get_clinic(session.get("clinic_id"))
    clinic_name = clinic.get("display_name", "the clinic")
    sms_name = clinic.get("sms_name") or clinic_name
    clinic_phone = clinic.get("phone", "")
    location_id = (session.get("selected_location") or "").lower().strip()
    collected = session.get("collected") or {}
    is_theorem = session.get("clinic_id") == "theorem"

    # ------------------------------------------------------------------ #
    # Resolve location-specific details (Theorem has two locations)
    # ------------------------------------------------------------------ #
    locations = clinic.get("locations", [])
    loc_cfg = next((loc for loc in locations if loc.get("id") == location_id), None)

    location_label = loc_cfg.get("name", location_id.title()) if loc_cfg else ""
    address_text = (
        (loc_cfg.get("address") if loc_cfg else None)
        or clinic.get("address", "")
    )
    hours_text = (
        (loc_cfg.get("hours_summary") if loc_cfg else None)
        or clinic.get("hours_summary", "")
    )
    parking_text = (
        (loc_cfg.get("parking") if loc_cfg else None)
        or clinic.get("parking", "")
    )
    transport_text = (
        (loc_cfg.get("transport") if loc_cfg else None)
        or clinic.get("transport", "")
    )

    pricing_text = clinic.get("pricing_summary", "")
    insurance_note = clinic.get("insurance_note", "")
    cancellation_policy = clinic.get("cancellation_policy", "")
    what_to_bring = clinic.get("what_to_bring", "")
    services_list = ", ".join(clinic.get("services", []))
    emergency_message = (
        clinic.get("call_handling", {}).get("emergency_message")
        or (
            "If this feels urgent or you have severe symptoms, "
            "please call 999 or go to A&E — we are not an emergency service."
        )
    )

    slot_minutes = int(clinic.get("slot_minutes", 50))

    # ------------------------------------------------------------------ #
    # Build known patient context block
    # ------------------------------------------------------------------ #
    context_lines: list[str] = []
    if collected.get("name"):
        context_lines.append(f"- Patient name: {collected['name']}")
    if collected.get("phone"):
        context_lines.append(f"- Patient phone: {collected['phone']}")
    if collected.get("reason"):
        context_lines.append(f"- Reason for calling: {collected['reason']}")
    if collected.get("service"):
        context_lines.append(f"- Service identified: {collected['service']}")
    if collected.get("patient_type"):
        context_lines.append(f"- Patient type: {collected['patient_type']}")
    if collected.get("insurer"):
        context_lines.append(f"- Insurer: {collected['insurer']}")
    if collected.get("policy_number"):
        context_lines.append(f"- Policy number: {collected['policy_number']}")
    if collected.get("time_preference"):
        context_lines.append(f"- Time preference: {collected['time_preference']}")
    if location_label:
        context_lines.append(f"- Chosen location: {location_label}")

    known_context = (
        "\n".join(context_lines)
        if context_lines
        else "No patient information collected yet."
    )

    # ------------------------------------------------------------------ #
    # Location options block (only relevant for multi-location clinics)
    # ------------------------------------------------------------------ #
    if locations:
        loc_names = " or ".join(loc.get("name", "") for loc in locations)
        location_section = (
            f"This clinic has two locations: {loc_names}. "
            f"Do NOT ask which location in your first response or before the caller has "
            f"said anything at all. Always greet first, let them speak, then ask location "
            f"as the natural next question after their first reply — regardless of what they need."
        )
    else:
        loc_names = ""
        location_section = "This is a single-location clinic."

    # ------------------------------------------------------------------ #
    # Assemble the full prompt
    # ------------------------------------------------------------------ #
    prompt = f"""You are Susie, the AI receptionist for {clinic_name}.
## Role
Book appointments, answer clinic questions, help patients.
You are NOT a clinician. Never diagnose or give medical advice.
## Voice Rules
- Warm, calm, natural British receptionist — speak exactly as a friendly clinic receptionist would on the phone
- Maximum 2 short sentences per response
- Always end with one clear question or next step — never trail off
- No lists, no bullet points, no markdown — this is a spoken phone call
- Do NOT say: "Perfect!", "Absolutely!", "Great!", "Certainly!", "Of course!", "Sure!" as filler openers
- Do NOT echo, repeat, or confirm field names back — never say things like "patient type: new" or "I've stored your patient_type"
- Do NOT add commentary after a tool call confirming what was stored — just move naturally to the next question
- Do NOT end a sentence with the raw value of something just collected (e.g. don't say "...as a new." or "...new patient.")
- Natural phrases to use: "Of course", "Let me just check that for you", "Lovely", "Just bear with me one moment", "That's all sorted", "Let me take a look at what's available"
- Never leave silence during tool calls — always speak a warm filler phrase alongside the tool call
## Clinic Facts
Name: {clinic_name}
Phone: {clinic_phone}
Location: {location_section}
Address: {address_text}
Hours: {hours_text}
Parking: {parking_text}
Transport: {transport_text}
Services: {services_list}
Pricing: {pricing_text}
Insurance: {insurance_note}
Cancellation: {cancellation_policy}
What to bring: {what_to_bring}
Appointment length: {slot_minutes} minutes
## Known About This Caller
{known_context}
Never ask for information already listed above.
## Tool Rules
collect_and_store
- Call every time you learn: name, phone, reason, location, patient_type, insurer, policy_number, time_preference, service
- Silent — never announce you are storing anything
check_availability
- Call BEFORE asking patient to pick a time
- Must know location and service first
- Speak warm filler in same response: "Let me just check what's available for you..."
- Present max 3 slots by spoken date and time only
- Never invent slots — only offer what the tool returns
book_appointment
- Only call when ALL THREE confirmed:
  1. Patient verbally confirmed the specific slot
  2. Full name collected
  3. Mobile number collected
- Speak warm filler in same response: "Lovely, let me get that confirmed for you now..."
cancel_appointment
- Only call when ALL THREE collected:
  1. Full name
  2. Phone number
  3. Location
- Confirm first: "Just to confirm, I'll cancel your appointment — is that right?" then call immediately
reschedule_appointment
- Only call when ALL collected:
  1. Full name, phone, location
  2. New slot confirmed verbally after check_availability
get_clinic_info
- Call for ANY factual question: hours, prices, insurance, parking, directions
- Never guess facts — always call this tool
transfer_to_human
- Call when: patient asks for a person, medical emergency, or you have failed to understand the patient twice
log_call_outcome
- Call at the natural end of every call
escalate_to_claude
- Only call when the question genuinely requires complex clinical or legal reasoning
- Do NOT escalate for: pricing, hours, booking, rescheduling, cancellation, insurance questions, parking, directions, common conditions, wait times
- When in doubt, use get_clinic_info first
## Booking Steps
Collect in this order, skipping anything already known:
1. Reason for calling
2. New or returning patient
3. Location — "Which location were you thinking, {loc_names if locations else clinic_name}?" — never ask this before they speak
4. Time preference — day, morning or afternoon
5. check_availability → present up to 3 slots
6. Confirm chosen slot verbally
7. Full name
8. Mobile number
9. Insurance — only ask if they mention it
10. Confirm all details back → book_appointment
11. Close warmly with booking summary
## Returning Patients
Ask for name and phone first.
Then ask what they need help with.
Do not restart the new patient flow.
## Safety
If patient mentions emergency, severe symptoms, chest pain, stroke signs — say immediately:
"{emergency_message}"
Then offer to transfer or end the call.
## Medical Questions
For any question about conditions, treatments, recovery times, exercises — say:
"That's really a question for your physiotherapist when you come in — I wouldn't want to give you the wrong steer on something like that."
Then redirect to booking if appropriate.
"""
    return prompt.strip()
