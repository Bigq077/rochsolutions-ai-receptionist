# app/clinic_config.py

from __future__ import annotations

from typing import Dict, Any, Optional


def _hours_tuple(start_hour: float, end_hour: float):
    """
    Working hours are stored as (start_hour, end_hour).
    Supports half-hours using floats (e.g., 8.5 = 08:30).
    """
    return (start_hour, end_hour)


# Map inbound Twilio "To" numbers -> clinic_id
# Fill these with your actual Twilio numbers (E.164 format).
TWILIO_TO_CLINIC: Dict[str, str] = {
    # Example:
    # "+441234567890": "theorem",
    # "+1XXXXXXXXXXX": "demo",
}


CLINICS: Dict[str, Dict[str, Any]] = {
    "demo": {
        "display_name": "Roch Physio Clinic",
        "timezone": "Europe/London",
        "calendar_id": "primary",
        "booking_system": "google_calendar",

        # booking rules
        "slot_minutes": 30,
        "days_ahead": 7,
        "working_hours": {
            "mon": _hours_tuple(8, 19),
            "tue": _hours_tuple(8, 19),
            "wed": _hours_tuple(8, 19),
            "thu": _hours_tuple(8, 19),
            "fri": _hours_tuple(8, 19),
            "sat": _hours_tuple(9, 14),
            "sun": None,
        },

        # FAQ / info
        "address": (
            "Roch Physio is located at 12 High Street, Coventry, CV1 — "
            "a two minutes’ walk from Coventry Station."
        ),
        "parking": (
            "Paid on-street parking is available nearby, and there is a public car park opposite the clinic."
        ),
        "hours_summary": (
            "We’re open Monday to Friday from 8am to 7pm, Saturday from 9am to 2pm, and closed on Sundays."
        ),
        "pricing_summary": (
            "Initial assessment is £65 for 45 minutes. "
            "Follow-up appointments are £45 for 30 minutes. "
            "Sports massage is £40 for 30 minutes or £70 for 60 minutes. "
            "Shockwave therapy sessions are £55."
        ),
        "services": [
            "Initial physiotherapy assessment",
            "Follow-up physiotherapy sessions",
            "Sports massage",
            "Shockwave therapy",
            "Rehabilitation and strength programmes",
        ],
        "insurance_note": (
            "We accept Bupa, AXA Health, Vitality, Aviva and WPA. "
            "If you’re with another insurer, we offer self-pay and can provide an invoice for reimbursement if your policy allows."
        ),
        "common_insurers": ["Bupa", "AXA Health", "Vitality", "Aviva", "WPA"],

        # policies
        "cancellation_policy": (
            "If you need to cancel or reschedule, please give at least 24 hours’ notice to avoid a late cancellation fee."
        ),
        "what_to_bring": (
            "Please wear comfortable clothing and bring any relevant scans, reports, or referral letters if you have them."
        ),
    },

    # -----------------------
    # THEOREM CLINIC (MARK DYER)
    # -----------------------
    "theorem": {
        "display_name": "Theorem Health and Wellness",
        "timezone": "Europe/London",

        # Booking system / routing
        "booking_system": "acuity",
        "calendar_id": None,

        # Core booking rules (from Mark)
        "slot_minutes": 50,
        "days_ahead": 180,  # up to 6 months in advance
        "working_hours": {
            "mon": _hours_tuple(8.5, 21),
            "tue": _hours_tuple(8.5, 21),
            "wed": _hours_tuple(8.5, 21),
            "thu": _hours_tuple(8.5, 21),
            "fri": _hours_tuple(8.5, 21),
            "sat": None,
            "sun": None,
        },

        # Locations
        "addresses": [
            "Theorem Health and Wellness, The Greig Sports Center, Kinwarton Road, Alcester, B49 6AD",
            "Theorem Health and Wellness, 51 Bromsgrove Road, Redditch, B97 4RH",
        ],
        "address": (
            "Theorem Health and Wellness has two locations: "
            "The Greig Sports Center, Kinwarton Road, Alcester, B49 6AD; "
            "and 51 Bromsgrove Road, Redditch, B97 4RH."
        ),

        # Hours summary
        "hours_summary": (
            "We’re open Monday to Friday from 8:30am to 9pm. "
            "Currently no weekend appointments. Closed on all UK bank holidays."
        ),
        "holiday_closures": ["All UK bank holidays"],

        # Practitioner preferences
        "practitioner_days": {
            "Mark": ["mon", "tue", "wed"],
            "Leanne": ["thu"],
        },

        # Booking preferences / constraints
        "booking_notes": [
            "Preferred: book directly into Acuity where possible.",
            "Patients can write a short narrative during booking if they need to explain.",
            "Some clients request a specific practitioner (Mark or Leanne).",
            "Mark works Monday/Tuesday/Wednesday. Leanne works Thursday.",
            "Bookings are separated by clinic location in Acuity.",
            "Insurance referrals always require manual approval.",
            "If the AI can’t fully help, direct to website and/or take a message/offer a callback/escalate to staff.",
        ],

        # Services (from Mark)
        "services": [
            "Physiotherapy assessment (holistic approach: mobility/strength + emotional well-being lens)",
            "Physiotherapy follow-up sessions (progress tracking + plan refinement; referrals/imaging support where appropriate)",
            "Prescribing (qualified prescribers; e.g., analgesia when appropriate)",
            "Remedial rehabilitation with rehabilitation instructors (coordinated care)",
            "Shockwave therapy (targeted sound waves to stimulate healing; often tendon issues)",
            "Class IV laser therapy (pain relief, reduce inflammation, speed tissue repair)",
            "Acupuncture",
            "Psychotherapy (safe space; can include hypnotherapy and spiritual healing techniques)",
        ],

        # Pricing & policies (from Mark)
        "pricing_summary": (
            "Physio sessions are £75 (50 minutes). Rehab sessions are £65. Prescribing is £12.50. "
            "Laser and shockwave may add a £45 surcharge when specialist equipment is used."
        ),
        "pricing_details": {
            "physio_session_gbp": 75.0,
            "rehab_session_gbp": 65.0,
            "prescribing_gbp": 12.50,
            "specialist_equipment_surcharge_gbp": 45.0,
            "notes": [
                "Clients are sent a text message with fees.",
                "Surcharges apply when specialist equipment (laser/shockwave) is used.",
            ],
        },

        "cancellation_policy": "24 hours cancellation policy — otherwise the full fee is charged.",
        "what_to_bring": "If you can, bring shorts or wear loose clothing — but don’t worry if you can’t.",

        # Insurance (from Mark)
        "insurance_note": (
            "Theorem generally operates as self-pay: patients pay the fees and may claim back themselves if their policy allows. "
            "Bupa is not accepted. Insurance referrals require manual approval."
        ),
        "common_insurers": [],
        "not_accepted_insurers": ["Bupa"],

        # Call handling (from Mark)
        "call_handling": {
            "if_cant_help": ["Direct to website", "Take a message", "Offer a call-back", "Escalate to staff"],
            "immediate_defer_to_human_for": ["Emergencies"],
            "emergency_message": (
                "If this feels urgent or you have severe symptoms, please call 999 (or go to A&E). "
                "We’re not an emergency service."
            ),
            # Optional: set this to Mark’s phone if you want Twilio to forward calls when needed.
            # If you leave it None, your logic can just take a message instead.
            "escalation_forward_to_phone": None,
        },

        # Avatar preferences (from Mark)
        "avatar_preferences": {
            "clinic_avatar": {
                "modelled_on_owner": True,
                "owner_name": "Mark",
                "use_owner_voice_for_website_avatar": True,
                "use_neutral_voice_for_phone": True,
                "phone_voice_description": "Neutral female, family-friendly voice for calls.",
                "conversation_style": "Natural back-and-forth; detailed when appropriate.",
                "tone": ["friendly", "reassuring", "empathetic"],
                "must_say_sometimes": [
                    "This is best discussed in person — I’d recommend booking an appointment."
                ],
                "boundaries": [
                    "Informational, not diagnostic",
                    "Avoid medical advice",
                    "Emphasise assessment before conclusions",
                    "Emergencies: advise calling 999 / A&E",
                ],
            },
            "rehab_avatar": {
                "enabled": True,
                "separate_avatar": True,
                "focus": "Scaled versions; nothing complex.",
                "allowed": [
                    "Explain exercises step-by-step",
                    "Explain why an exercise is prescribed",
                    "Answer general 'am I doing this right?' questions safely",
                    "General rehab guidance between sessions",
                ],
                "boundaries": [
                    "No diagnosis",
                    "No changes to prescribed plan without clinician input",
                    "Encourage contacting the clinic if pain worsens or symptoms change",
                ],
            },
        },

        # Brand / positioning (from Mark)
        "brand_positioning": {
            "theorem_statement": (
                "Just as a mathematical theorem combines principles for a proven solution, "
                "Theorem Health & Wellness integrates health and wellness interventions into a holistic path toward your complete well-being."
            ),
            "care_philosophy": (
                "Mark and the team are passionate about client care and helping people get back to normal life "
                "pain free and stress free as much as able to do so."
            ),
        },

        # Reporting preferences (from Mark)
        "reporting": {
            "google_sheets_call_summaries": True,
            "missed_call_tracking": True,
            "notifications_for_manual_followups": True,
            "notes": "Wants visibility on the above; open to learning what else is possible.",
        },

        # Contact identity (from Mark’s signature)
        "contact_details": {
            "contact_email": "info@theoremhealth.co.uk",
            "company_number": "08116105",
            "owner_signature": "Mark Dyer MSc, BSc (Hons) HCPC, Mcsp, AACP, Macs.",
        },
    },
}


def get_clinic(clinic_id: Optional[str]) -> Dict[str, Any]:
    """
    Safe getter: defaults to demo if unknown.
    """
    cid = (clinic_id or "demo").strip().lower()
    return CLINICS.get(cid, CLINICS["demo"])


def clinic_id_from_twilio_to(to_number: Optional[str]) -> str:
    """
    Resolve clinic_id from Twilio inbound 'To' number.
    Defaults to 'demo' if not mapped.
    """
    key = (to_number or "").strip()
    return TWILIO_TO_CLINIC.get(key, "demo")
