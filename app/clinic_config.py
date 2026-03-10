# app/clinic_config.py

from __future__ import annotations

from typing import Dict, Any, Optional
import os


def _hours_tuple(start_hour: float, end_hour: float):
    """
    Working hours are stored as (start_hour, end_hour).
    Supports half-hours using floats (e.g., 8.5 = 08:30).
    """
    return (start_hour, end_hour)


# Map inbound Twilio "To" numbers -> clinic_id
# Format: "+447XXXXXXXXX": "clinic_id"
# Any unrecognised number falls back to "demo" automatically.
TWILIO_TO_CLINIC: Dict[str, str] = {
    "+447367002651": "theorem",       # Theorem Health and Wellness
    "+447366530580": "demo",          # RochSolutions demo line

    # ---------------------------------------------------------------
    # ADD NEW CLIENT HERE
    # 1. Add their Twilio number and a clinic_id (slug, no spaces)
    # 2. Create app/clinics/<clinic_id>/clinic.json + knowledge.md
    # 3. Add their env vars to Render with the same prefix
    # ---------------------------------------------------------------
    # "+44XXXXXXXXXX": "health_for_life",   # Health For Life (coming soon)
}


CLINICS: Dict[str, Dict[str, Any]] = {
    "demo": {
        "display_name": "Roch Solutions",
        "timezone": "Europe/London",
        "calendar_id": "primary",
        "booking_system": "google_calendar",

        # Branding / contact — used in SMS messages and greeting
        "greeting": (
            "Hi there! This is Susie, Roch Solutions' AI receptionist. "
            "How can I help you today?"
        ),
        "sms_name":  "Roch Solutions",
        "phone":     "07366 530580",

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
            "Roch Solutions is located at 12 High Street, Coventry, CV1 — "
            "a two minutes' walk from Coventry Station."
        ),
        "parking": (
            "Paid on-street parking is available nearby, and there is a public car park opposite the clinic."
        ),
        "hours_summary": (
            "We're open Monday to Friday from 8am to 7pm, Saturday from 9am to 2pm, and closed on Sundays."
        ),
        "pricing_summary": (
            "Initial assessment is £65 for 45 minutes. "
            "Follow-up appointments are £45 for 30 minutes. "
            "Sports massage is £40 for 30 minutes or £70 for 60 minutes. "
            "Shockwave therapy sessions are £55."
        ),
        # Structured pricing used by the intake/recommendation booking flow
        "service_prices": {
            "initial_assessment": {
                "label":    "initial physiotherapy assessment",
                "price":    "£65",
                "duration": "45 minutes",
                "blurb": (
                    "We'll do a full assessment, identify the root cause of your problem, "
                    "and start hands-on treatment — all in that first session."
                ),
            },
            "follow_up": {
                "label":    "follow-up physiotherapy session",
                "price":    "£45",
                "duration": "30 minutes",
                "blurb": (
                    "We'll check your progress, adjust the plan, and continue treatment."
                ),
            },
            "sports_massage": {
                "label":    "sports massage",
                "price":    "£40 for 30 minutes or £70 for 60 minutes",
                "duration": "30 or 60 minutes",
                "blurb": (
                    "Deep soft tissue work to reduce muscle tension and support recovery."
                ),
            },
            "shockwave": {
                "label":    "shockwave therapy",
                "price":    "£55 per session",
                "duration": "per session",
                "blurb": (
                    "Targeted acoustic waves to stimulate healing in stubborn tendon conditions. "
                    "Very effective for plantar fasciitis, Achilles, and tennis elbow."
                ),
            },
        },
        "services": [
            "Initial physiotherapy assessment",
            "Follow-up physiotherapy sessions",
            "Sports massage",
            "Shockwave therapy",
            "Rehabilitation and strength programmes",
        ],
        "insurance_note": (
            "We accept Bupa, AXA Health, Vitality, Aviva and WPA. "
            "If you're with another insurer, we offer self-pay and can provide an invoice for reimbursement if your policy allows."
        ),
        "common_insurers": ["Bupa", "AXA Health", "Vitality", "Aviva", "WPA"],

        # policies
        "cancellation_policy": (
            "If you need to cancel or reschedule, please give at least 24 hours' notice to avoid a late cancellation fee."
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

        # Branding / contact — used in SMS messages
        "sms_name": "Theorem Health",
        "phone":    "07870 166861",
        "transfer_phone": "+447870166861",   # E.164 — Twilio dials this for live transfers

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
        # Each entry drives the greeting, location-select, hours, address, parking answers.
        # Clinics with no "locations" key are treated as single-location (demo pattern).
        "locations": [
            {
                "id": "alcester",
                "name": "Alcester",
                "address": (
                    "We're at The Greig Leisure Centre, Kinwarton Road, Alcester, B49 6AD. "
                    "It's a large leisure centre — look for the Everyone Active signage and the big car park out front. "
                    "Alcester sits at the junction of the A46 and the A435, so it's easy to reach from most directions. "
                    "From Stratford-upon-Avon it's about 8 miles, roughly 15 minutes. "
                    "From Redditch about 9 miles, 15 to 20 minutes. "
                    "From Birmingham around 21 miles, roughly 35 to 40 minutes via the M42 and A435. "
                    "From Evesham about 10 miles, around 15 minutes. "
                    "From Warwick about 16 miles, around 20 to 25 minutes. "
                    "The postcode B49 6AD will take you straight there on any satnav."
                ),
                "hours_summary": (
                    "The Alcester clinic is open Monday to Friday, "
                    "eight thirty in the morning until nine at night. "
                    "We're closed on weekends."
                ),
                "parking": (
                    "Parking at the Greig Leisure Centre is completely free, "
                    "with around 80 spaces in the car park right in front of the building. "
                    "There are no time limits, and disabled bays are available close to the entrance."
                ),
                "transport": (
                    "Alcester doesn't have its own train station. "
                    "The nearest stations are Redditch — about 9 miles away, roughly 15 minutes by car — "
                    "and Stratford-upon-Avon, about 8 miles, also around 15 minutes. "
                    "By bus, Route 26 run by Stagecoach connects Stratford-upon-Avon, Alcester, and Redditch. "
                    "Route 247 by Diamond Bus links Redditch, Alcester, and Evesham. "
                    "Buses stop in Alcester town centre, which is a short walk from the Greig Leisure Centre."
                ),
            },
            {
                "id": "redditch",
                "name": "Redditch",
                "address": (
                    "We're at 51 Bromsgrove Road, Redditch, B97 4RH. "
                    "We're on the main Bromsgrove Road — look for us next to Smile Dental Care. "
                    "Bromsgrove Road is the A448, the main road heading out of Redditch town centre toward Bromsgrove. "
                    "From Birmingham it's about 15 miles, roughly 30 minutes via the A441. "
                    "From Alcester about 9 miles, 15 to 20 minutes. "
                    "From Bromsgrove about 7 miles, around 10 minutes. "
                    "From Stratford-upon-Avon about 16 miles, 25 to 30 minutes. "
                    "From Worcester about 17 miles, roughly 30 minutes. "
                    "The postcode B97 4RH is reliable for satnavs."
                ),
                "hours_summary": (
                    "The Redditch clinic is open Monday to Saturday. "
                    "Monday, Tuesday and Friday we're open nine to five. "
                    "Wednesday and Thursday we're open nine to seven. "
                    "And Saturday we're open nine to five. "
                    "We're closed on Sundays."
                ),
                "parking": (
                    "There's street parking on Bromsgrove Road — please check the signs on arrival for any restrictions. "
                    "The Redditch Station car park is on the same road, about a 3 minute walk from the clinic — "
                    "it has around 150 spaces and costs roughly three to four pounds fifty for the day."
                ),
                "transport": (
                    "Redditch train station is on the same road as the clinic — Bromsgrove Road — "
                    "about 5 to 7 minutes' walk away. "
                    "West Midlands Railway runs the Cross-City Line from Birmingham New Street to Redditch "
                    "roughly every 30 minutes, with a journey time of about 35 to 40 minutes from Birmingham. "
                    "Several bus routes serve Bromsgrove Road: Route 52A to Bromsgrove, "
                    "Route 247 to Alcester and Evesham, Route 150 toward Birmingham, "
                    "and Route 512 to Stratford-upon-Avon."
                ),
            },
        ],
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
            "We're open Monday to Friday from 8:30am to 9pm. "
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
            "If the AI can't fully help, direct to website and/or take a message/offer a callback/escalate to staff.",
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
        # Structured pricing used by the intake/recommendation booking flow
        "service_prices": {
            "initial_assessment": {
                "label":    "physiotherapy assessment",
                "price":    "£75",
                "duration": "50 minutes",
                "blurb": (
                    "We'll do a full holistic assessment — physical and emotional — "
                    "identify the root cause, and start treatment in that first session."
                ),
            },
            "follow_up": {
                "label":    "follow-up physiotherapy session",
                "price":    "£75",
                "duration": "50 minutes",
                "blurb": (
                    "We'll check your progress, adjust the plan, and continue treatment."
                ),
            },
            "rehabilitation": {
                "label":    "rehabilitation session",
                "price":    "£65",
                "duration": "50 minutes",
                "blurb": (
                    "Progressive strengthening and movement work to rebuild function and get you back to full activity."
                ),
            },
            "shockwave": {
                "label":    "shockwave therapy",
                "price":    "£45 surcharge when used",
                "duration": "as part of your physio session",
                "blurb": (
                    "Acoustic waves to restart healing in stubborn tendon conditions. "
                    "Very effective for plantar fasciitis, Achilles, and tennis elbow."
                ),
            },
        },
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
        "what_to_bring": "If you can, bring shorts or wear loose clothing — but don't worry if you can't.",

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
                "We're not an emergency service."
            ),
            # Optional: set this to Mark's phone if you want Twilio to forward calls when needed.
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
                    "This is best discussed in person — I'd recommend booking an appointment."
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

        # Contact identity (from Mark's signature)
        "contact_details": {
            "contact_email": "info@theoremhealth.co.uk",
            "company_number": "08116105",
            "owner_signature": "Mark Dyer MSc, BSc (Hons) HCPC, Mcsp, AACP, Macs.",
        },
    },
}


# ============================================================================
# BOOKING SUBSYSTEM CONFIGURATION
# ============================================================================

# Acuity Scheduling configuration
ACUITY_CONFIG = {
    "theorem": {
        "user_id": os.getenv("ACUITY_USER_ID"),
        "api_key": os.getenv("ACUITY_API_KEY"),
        # Calendar IDs for location-based or practitioner-based routing
        "calendar_ids": {
            "alcester": os.getenv("ACUITY_CALENDAR_ID_ALCESTER"),
            "redditch": os.getenv("ACUITY_CALENDAR_ID_REDDITCH"),
            "mark": os.getenv("ACUITY_CALENDAR_ID_MARK"),
            "leanne": os.getenv("ACUITY_CALENDAR_ID_LEANNE"),
        },
    },
}

# Location definitions for Theorem
THEOREM_LOCATIONS = {
    "alcester": {
        "id": "alcester",
        "name": "Alcester",
        "short_name": "Alcester",
        "address": "Theorem Health and Wellness, The Greig Leisure Centre, Kinwarton Road, Alcester, B49 6AD",
        "acuity_calendar_id": os.getenv("ACUITY_CALENDAR_ID_ALCESTER"),
    },
    "redditch": {
        "id": "redditch",
        "name": "Redditch",
        "short_name": "Redditch",
        "address": "Theorem Health and Wellness, 51 Bromsgrove Road, Redditch, B97 4RH",
        "acuity_calendar_id": os.getenv("ACUITY_CALENDAR_ID_REDDITCH"),
    },
}

# Practitioner definitions for Theorem
THEOREM_PRACTITIONERS = {
    "mark": {
        "id": "mark",
        "name": "Mark",
        "full_name": "Mark Dyer",
        "title": "MSc, BSc (Hons) HCPC, Mcsp, AACP, Macs",
        "role": "Physiotherapist & Prescriber",
        "available_days": ["mon", "tue", "wed"],
        "acuity_calendar_id": os.getenv("ACUITY_CALENDAR_ID_MARK"),
    },
    "leanne": {
        "id": "leanne",
        "name": "Leanne",
        "role": "Physiotherapist",
        "available_days": ["thu"],
        "acuity_calendar_id": os.getenv("ACUITY_CALENDAR_ID_LEANNE"),
    },
}

# Appointment type mappings (connects to Acuity appointment type IDs)
THEOREM_APPOINTMENT_TYPES = {
    "physio_assessment": {
        "id": "physio_assessment",
        "name": "Physiotherapy Assessment",
        "duration_minutes": 50,
        "price_gbp": 75.00,
        "description": (
            "Holistic assessment including physical mobility, strength, and emotional well-being. "
            "We'll identify the issue and create a tailored treatment plan."
        ),
        "category": "physiotherapy",
        "new_patients": True,
        "returning_patients": True,
        # This will be populated from Acuity after first sync
        "acuity_appointment_type_id": None,
    },
    "physio_followup": {
        "id": "physio_followup",
        "name": "Physiotherapy Follow-up",
        "duration_minutes": 50,
        "price_gbp": 75.00,
        "description": (
            "Progress tracking and treatment plan adjustment. "
            "We'll fine-tune your interventions for optimal recovery."
        ),
        "category": "physiotherapy",
        "new_patients": False,
        "returning_patients": True,
        "acuity_appointment_type_id": None,
    },
    "remedial_rehab": {
        "id": "remedial_rehab",
        "name": "Remedial Rehabilitation",
        "duration_minutes": 50,
        "price_gbp": 65.00,
        "description": "Expert rehabilitation instruction for ongoing recovery with our rehabilitation instructors.",
        "category": "rehabilitation",
        "new_patients": False,
        "returning_patients": True,
        "acuity_appointment_type_id": None,
    },
    "rehab_pt": {
        "id": "rehab_pt",
        "name": "Rehabilitation PT",
        "duration_minutes": 50,
        "price_gbp": 65.00,
        "description": "Personal training focused on rehabilitation and strength building.",
        "category": "rehabilitation",
        "new_patients": False,
        "returning_patients": True,
        "acuity_appointment_type_id": None,
    },
    "prescribing": {
        "id": "prescribing",
        "name": "Prescribing Consultation",
        "duration_minutes": 20,
        "price_gbp": 12.50,
        "description": "Medication prescription service with our qualified prescribers.",
        "category": "prescribing",
        "new_patients": False,
        "returning_patients": True,
        "acuity_appointment_type_id": None,
    },
    "acupuncture": {
        "id": "acupuncture",
        "name": "Acupuncture",
        "duration_minutes": 50,
        "price_gbp": 75.00,
        "description": "Fine needles placed at specific points to balance energy flow and promote healing.",
        "category": "physiotherapy",
        "new_patients": False,
        "returning_patients": True,
        "acuity_appointment_type_id": None,
    },
    "psychotherapy": {
        "id": "psychotherapy",
        "name": "Psychotherapy",
        "duration_minutes": 50,
        "price_gbp": 75.00,
        "description": (
            "Safe space to explore thoughts and emotions using techniques "
            "including hypnotherapy and spiritual healing."
        ),
        "category": "psychotherapy",
        "new_patients": True,
        "returning_patients": True,
        "acuity_appointment_type_id": None,
    },
}

# Specialist equipment surcharges (applied during session, not at booking)
THEOREM_SURCHARGES = {
    "shockwave": {
        "id": "shockwave",
        "name": "Shockwave Therapy",
        "amount_gbp": 45.00,
        "description": "Targeted sound waves to stimulate healing, especially for chronic tendon issues.",
        "applied_at": "session",  # Not selected at booking, determined during assessment
    },
    "laser": {
        "id": "laser",
        "name": "Class IV Laser Therapy",
        "amount_gbp": 45.00,
        "description": "Powerful laser light to alleviate pain, reduce inflammation, and speed tissue repair.",
        "applied_at": "session",
    },
}

# UK Bank Holidays 2025-2026 (update annually)
UK_BANK_HOLIDAYS = [
    "2025-01-01",  # New Year's Day
    "2025-04-18",  # Good Friday
    "2025-04-21",  # Easter Monday
    "2025-05-05",  # Early May Bank Holiday
    "2025-05-26",  # Spring Bank Holiday
    "2025-08-25",  # Summer Bank Holiday
    "2025-12-25",  # Christmas Day
    "2025-12-26",  # Boxing Day
    "2026-01-01",  # New Year's Day 2026
    "2026-04-03",  # Good Friday 2026
    "2026-04-06",  # Easter Monday 2026
    "2026-05-04",  # Early May Bank Holiday 2026
    "2026-05-25",  # Spring Bank Holiday 2026
    "2026-08-31",  # Summer Bank Holiday 2026
    "2026-12-25",  # Christmas Day 2026
    "2026-12-28",  # Boxing Day observed 2026
]


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def get_clinic(clinic_id: Optional[str]) -> Dict[str, Any]:
    """
    Safe getter: defaults to demo if unknown.
    Injects 'clinic_id' into the returned dict so downstream code
    (e.g. knowledge retrieval) can identify the clinic.
    """
    cid = (clinic_id or "demo").strip().lower()
    clinic = dict(CLINICS.get(cid, CLINICS["demo"]))
    clinic["clinic_id"] = cid
    return clinic


def clinic_id_from_twilio_to(to_number: Optional[str]) -> str:
    """
    Resolve clinic_id from Twilio inbound 'To' number.
    Defaults to 'demo' if not mapped.
    """
    key = (to_number or "").strip()
    return TWILIO_TO_CLINIC.get(key, "demo")


def get_acuity_config(clinic_id: str = "theorem") -> dict:
    """Get Acuity configuration for clinic."""
    return ACUITY_CONFIG.get(clinic_id, ACUITY_CONFIG.get("theorem", {}))


def get_theorem_location(location_id: str) -> dict:
    """Get location configuration."""
    return THEOREM_LOCATIONS.get(location_id)


def get_theorem_practitioner(practitioner_id: str) -> dict:
    """Get practitioner configuration."""
    return THEOREM_PRACTITIONERS.get(practitioner_id)


def get_theorem_appointment_type(type_id: str) -> dict:
    """Get appointment type configuration."""
    return THEOREM_APPOINTMENT_TYPES.get(type_id)


def is_practitioner_available_on_day(practitioner_id: str, day_abbrev: str) -> bool:
    """
    Check if practitioner works on given day.
    
    Args:
        practitioner_id: "mark" or "leanne"
        day_abbrev: "mon", "tue", "wed", "thu", "fri", "sat", "sun"
    
    Returns:
        True if practitioner is available
    """
    prac = THEOREM_PRACTITIONERS.get(practitioner_id)
    if not prac:
        return False
    return day_abbrev in prac.get("available_days", [])


def calculate_appointment_price(
    appointment_type_id: str,
    include_surcharges: list = None,
) -> float:
    """
    Calculate appointment price including optional surcharges.
    
    Args:
        appointment_type_id: Base appointment type
        include_surcharges: List of surcharge IDs (e.g., ["shockwave", "laser"])
    
    Returns:
        Total price in GBP
    """
    apt_type = THEOREM_APPOINTMENT_TYPES.get(appointment_type_id)
    if not apt_type:
        return 0.0
    
    total = apt_type["price_gbp"]
    
    if include_surcharges:
        for surcharge_id in include_surcharges:
            surcharge = THEOREM_SURCHARGES.get(surcharge_id)
            if surcharge:
                total += surcharge["amount_gbp"]
    
    return total


def get_insurance_guidance() -> str:
    """Get insurance guidance for Theorem."""
    clinic = CLINICS.get("theorem", {})
    return clinic.get("insurance_note", "")


def get_cancellation_policy(clinic_id: str = "theorem") -> str:
    """Get cancellation policy."""
    clinic = CLINICS.get(clinic_id, {})
    return clinic.get("cancellation_policy", "")

