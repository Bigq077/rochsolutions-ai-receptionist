# app/clinic_config.py

from __future__ import annotations

from typing import Dict, Any, Optional
import os
import json
import copy as _copy
from pathlib import Path


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
    "+447367002651": "jv_v1",         # Joint Venture Physiotherapy (Bolton) — reassigned from Theorem's retired legacy-pipeline line (confirmed retired 2026-06-23)
    "+447426779875": "theorem",       # Theorem Health and Wellness (Media Streams pipeline)
    "+447366530580": "theorem_v2",    # Theorem test line — two-clinic guards active
    "+447380841468": "theorem_v3",    # Theorem v3 line — copy of theorem_v2

    # Vital Edge Therapy (Kingston) — provisional booking model.
    # TBC: the inbound Twilio number that forwards to Susie is not yet
    # provisioned (the call rings Jonathan's mobile +447545862307 first, then
    # falls back to the AI). Replace the placeholder below with the real Twilio
    # "To" number before go-live, then uncomment.
    # "+44XXXXXXXXXX": "vital_edge",

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
        "sms_name": "Theorem Health and Wellness",
        "phone":    "07870 166861",
        "transfer_phone": "+447870166861",   # E.164 — Twilio dials this for live transfers

        # Booking system / routing
        "booking_system": "acuity",
        "calendar_id": None,

        # Use streamlined fast-track booking flow (skips duration + time-preference questions)
        "fast_booking": True,

        # Core booking rules (from Mark)
        "slot_minutes": 40,          # standard follow-up; new patient assessment = 50 mins
        "days_ahead": 180,  # up to 6 months in advance
        # Per-location working hours (used by booking system for slot filtering)
        "working_hours": {
            "mon": _hours_tuple(9, 19),
            "tue": _hours_tuple(9, 19),
            "wed": _hours_tuple(9, 19),
            "thu": _hours_tuple(9, 19),
            "fri": _hours_tuple(9, 19),
            "sat": None,
            "sun": None,
        },
        # Per-location working hours (Redditch only open Mon + Thu)
        "location_working_hours": {
            "alcester": {
                "mon": _hours_tuple(9, 19),   # Mark
                "tue": _hours_tuple(9, 19),   # Mark
                "wed": _hours_tuple(9, 19),   # Mark
                "thu": _hours_tuple(9, 19),   # Leanne
                "fri": _hours_tuple(9, 19),   # Leanne
                "sat": None,
                "sun": None,
            },
            "redditch": {
                "mon": _hours_tuple(9, 14),   # Leanne (last slot 1pm)
                "tue": None,
                "wed": None,
                "thu": _hours_tuple(9, 14),   # Mark (last slot 1pm)
                "fri": None,
                "sat": None,
                "sun": None,
            },
        },

        # Locations
        # Each entry drives the greeting, location-select, hours, address, parking answers.
        # Clinics with no "locations" key are treated as single-location (demo pattern).
        "locations": [
            {
                "id": "alcester",
                "name": "Alcester",
                "address": (
                    "We're at The Greig Leisure Centre, Kinwarton Road, Awlstuh, B49 6AD. "
                    "It's a large leisure centre — look for the Everyone Active signage and the big car park out front. "
                    "Awlstuh sits at the junction of the A46 and the A435, so it's easy to reach from most directions. "
                    "From Stratford-upon-Avon it's about 8 miles, roughly 15 minutes. "
                    "From Redditch about 9 miles, 15 to 20 minutes. "
                    "From Birmingham around 21 miles, roughly 35 to 40 minutes via the M42 and A435. "
                    "From Evesham about 10 miles, around 15 minutes. "
                    "From Warwick about 16 miles, around 20 to 25 minutes. "
                    "The postcode B49 6AD will take you straight there on any satnav."
                ),
                "hours_summary": (
                    "The Awlstuh clinic is open Monday to Friday, "
                    "nine in the morning until seven in the evening. "
                    "Last appointment is at six. We're closed on weekends."
                ),
                "parking": (
                    "Parking at the Greig Leisure Centre is completely free, "
                    "with around 80 spaces in the car park right in front of the building. "
                    "There are no time limits, and disabled bays are available close to the entrance."
                ),
                "transport": (
                    "Awlstuh doesn't have its own train station. "
                    "The nearest stations are Redditch — about 9 miles away, roughly 15 minutes by car — "
                    "and Stratford-upon-Avon, about 8 miles, also around 15 minutes. "
                    "By bus, Route 26 run by Stagecoach connects Stratford-upon-Avon, Awlstuh, and Redditch. "
                    "Route 247 by Diamond Bus links Redditch, Awlstuh, and Evesham. "
                    "Buses stop in Awlstuh town centre, which is a short walk from the Greig Leisure Centre."
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
                    "From Awlstuh about 9 miles, 15 to 20 minutes. "
                    "From Bromsgrove about 7 miles, around 10 minutes. "
                    "From Stratford-upon-Avon about 16 miles, 25 to 30 minutes. "
                    "From Worcester about 17 miles, roughly 30 minutes. "
                    "The postcode B97 4RH is reliable for satnavs."
                ),
                "hours_summary": (
                    "The Redditch clinic is open on Mondays and Thursdays only, "
                    "nine in the morning until two in the afternoon. "
                    "Last appointment is at one. We're closed all other days."
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
                    "Route 247 to Awlstuh and Evesham, Route 150 toward Birmingham, "
                    "and Route 512 to Stratford-upon-Avon."
                ),
            },
        ],
        "addresses": [
            "Theorem Health and Wellness, The Greig Leisure Centre, Kinwarton Road, Alcester, B49 6AD",
            "Theorem Health and Wellness, 51 Bromsgrove Road, Redditch, B97 4RH",
        ],
        "address": (
            "Theorem Health and Wellness has two locations: "
            "The Greig Leisure Centre, Kinwarton Road, Awlstuh, B49 6AD; "
            "and 51 Bromsgrove Road, Redditch, B97 4RH."
        ),

        # Hours summary
        "hours_summary": (
            "Our Awlstuh clinic is open Monday to Friday, nine in the morning until seven in the evening — "
            "last appointment at six. Closed weekends. "
            "Our Redditch clinic is open Mondays and Thursdays only, nine to two — last appointment at one. "
            "Both clinics are closed on all UK bank holidays."
        ),
        "holiday_closures": ["All UK bank holidays"],

        # Practitioner preferences (location-keyed)
        "practitioner_days": {
            "alcester": {
                "Mark":   ["mon", "tue", "wed"],
                "Leanne": ["thu", "fri"],
            },
            "redditch": {
                "Mark":   ["thu"],
                "Leanne": ["mon"],
            },
        },

        # Patient policies
        "patient_policies": {
            "new_patient_threshold_years": 2,        # 2+ years since last visit = treated as new
            "different_injury_requires_new_assessment": True,
            "records_follow_patient_across_locations": True,
            # Authoritative age policy: clinic sees patients aged 15+.
            # 15, 16, 17 allowed; under 15 disallowed.
            "no_children": False,
            "min_patient_age": 15,
        },

        # Payment
        "payment_methods": ["cash", "debit card", "credit card", "online via Stripe"],
        "payment_timing": "prior to or directly after treatment",
        "package_payment": "Packages can be invoiced; payment due within 7 days of invoice date.",
        "late_payment_charge": True,
        "deposit_required": False,
        "payment_plans": False,
        "cancellation_fee_pct": 75,            # 75% of session fee if <24h notice
        "cancellation_notice_hours": 24,       # minimum notice to avoid fee
        "reschedule_notice_hours": 24,         # rescheduling with <24h notice = treated as cancellation
        "same_day_booking_allowed": False,     # minimum 24h notice required
        "no_waitlist": True,
        "no_slots_message": (
            "It looks like we don't have any availability in the next 30 days. "
            "We'll send you a text message as soon as a slot opens up."
        ),

        # Booking preferences / constraints
        "booking_notes": [
            "Preferred: book directly into Acuity where possible.",
            "Patients can write a short narrative during booking if they need to explain.",
            "Callers may request a specific practitioner (Mark or Leanne) — honour that preference.",
            "All conditions use the same appointment type in Acuity regardless of presenting complaint.",
            "Alcester: Mark Mon/Tue/Wed, Leanne Thu/Fri. Redditch: Leanne Mon, Mark Thu.",
            "Bookings are separated by clinic location in Acuity.",
            "Insurance referrals always require manual approval.",
            "Adults only — no paediatric patients.",
            "If the AI can't fully help, direct to website and/or take a message/offer a callback/escalate to staff.",
        ],

        # Services (from website)
        "services": [
            "Physiotherapy assessment (holistic approach: mobility/strength + emotional well-being lens)",
            "Physiotherapy follow-up sessions (progress tracking + plan refinement; referrals/imaging support where appropriate)",
            "Prescribing (qualified prescribers; e.g., analgesia when appropriate)",
            "Remedial rehabilitation with rehabilitation instructors (coordinated care)",
            "Shockwave therapy (targeted sound waves to stimulate healing; often tendon issues)",
            "Class IV Laser therapy (pain relief, reduce inflammation, speed tissue repair)",
            "Acupuncture",
            "Psychotherapy (safe space; can include hypnotherapy and spiritual healing techniques)",
            "Reiki and Energy Healing (one-hour sessions)",
            "Wellness and stress relief massage with In-light Therapy (one-hour sessions)",
            "Auricular acupuncture for stress relief (one-hour sessions)",
        ],

        # Full service descriptions — used by AI to answer "what does X involve?"
        "service_descriptions": {
            "Physiotherapy Assessment": (
                "During our physiotherapy assessment, which typically runs around 50 minutes to an hour, "
                "we take a holistic approach. We evaluate mobility and strength and also consider emotional "
                "well-being through a psychotherapeutic lens. We identify the injury or issue and "
                "collaboratively formulate a tailored treatment plan, which may include rehabilitation "
                "exercises, manual therapy and soft tissue work. We can incorporate advanced interventions "
                "such as shockwave therapy, acupuncture, or Class IV Laser therapy for a personalised, "
                "comprehensive path to recovery or management."
            ),
            "Physiotherapy Follow-up Session": (
                "In our physiotherapy follow-up sessions, we track progress and adjust your treatment plan "
                "as needed. We re-evaluate mobility, strength and overall well-being, ensuring interventions "
                "— exercises, manual therapy, or advanced techniques — are fine tuned for optimal recovery. "
                "Each follow-up is a collaborative checkpoint with continued support and refinement to keep "
                "you steadily on track. If we determine you need a specialist such as a surgeon or GP, we "
                "facilitate referral and support onward care. If advanced imaging is needed, such as an MRI "
                "or ultrasound, we help ensure a smooth path to the best possible treatment."
            ),
            "Prescribing": (
                "As qualified prescribers, our physiotherapists can prescribe medications when appropriate. "
                "If you require something like analgesia to manage pain, we can facilitate that as part of "
                "your care. Treatment plans are designed with safety and effectiveness in mind."
            ),
            "Remedial Rehabilitation": (
                "Alongside our physiotherapists, we have expert rehabilitation instructors to guide your "
                "ongoing recovery. You may see both your physiotherapist and our rehabilitation instructor "
                "in tandem, ensuring coordinated care and optimal results."
            ),
            "Shockwave Therapy": (
                "Shockwave therapy uses targeted sound waves to stimulate healing in tissues, helping reduce "
                "pain and promote recovery — especially for chronic conditions like tendon issues."
            ),
            "Class IV Laser Therapy": (
                "Class IV Laser Therapy uses powerful laser light to alleviate pain, reduce inflammation, "
                "and speed up tissue repair — reaching deep into your body for advanced healing."
            ),
            "Acupuncture": (
                "Acupuncture is an ancient practice where fine needles are placed at specific points to help "
                "balance and stimulate the flow of energy throughout the body. By restoring this natural flow, "
                "it can promote healing, reduce pain, and improve overall well-being."
            ),
            "Psychotherapy": (
                "Psychotherapy provides a safe space to explore thoughts and emotions. Using techniques such "
                "as hypnotherapy and spiritual healing, it aims to reduce stress, enhance mental well-being, "
                "and foster coping strategies — empowering clients to navigate challenges and achieve balance."
            ),
            "Reiki and Energy Healing": (
                "Reiki and energy healing are hour-long holistic treatments that work with the body's "
                "natural energy to promote relaxation, reduce stress, and support overall well-being."
            ),
            "Wellness and Stress Relief Massage": (
                "A one-hour wellness and stress relief massage, which can include In-light Therapy — "
                "a gentle light-based treatment used alongside massage to support relaxation and recovery."
            ),
            "Auricular Acupuncture": (
                "Auricular acupuncture uses fine needles placed at specific points on the ear to help "
                "relieve stress and promote calm. Sessions are one hour."
            ),
        },

        # Pricing & policies (from website, updated Oct 2021)
        "pricing_summary": (
            "New patient or new condition assessment: £75 for 50 minutes. "
            "Standard follow-up appointments: £75 for 40 minutes. "
            "Rehabilitation sessions: £65 for 50 minutes. "
            "Prescribing consultations: £12.50. "
            "Standalone shockwave or Class IV Laser session: £120 for 30 minutes. "
            "If shockwave or Class IV Laser is used within a standard session, a £45 surcharge applies. "
            "Package of 4 x shockwave and Class IV Laser Therapy: £420. "
            "Advanced one-hour treatments (Reiki, Energy Healing, Hypnotherapy, Wellness Massage, "
            "Auricular Acupuncture) are also available — enquire for pricing."
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
            "new_patient_assessment_gbp": 75.0,
            "new_patient_duration_mins": 50,
            "standard_followup_gbp": 75.0,
            "standard_followup_duration_mins": 40,
            "rehab_session_gbp": 65.0,
            "rehab_duration_mins": 50,
            "prescribing_gbp": 12.50,
            "specialist_equipment_surcharge_gbp": 45.0,
            "standalone_shockwave_laser_gbp": 120.0,
            "standalone_shockwave_laser_duration_mins": 30,
            "package_4x_shockwave_laser_gbp": 420.0,
            "package_validity_months": 6,
            "package_cooling_off_days": 14,
            "notes": [
                "£45 surcharge added to session bill if shockwave or Class IV Laser is used within a standard session.",
                "Standalone shockwave/laser: £120 for 30 minutes.",
                "Package of 4x shockwave and Class IV Laser: £420 (non-transferable, 6-month validity).",
                "Invoices raised immediately after consultation.",
                "Packages invoiced; due within 7 days.",
                "Late/non-payment charges apply.",
                "Cancellation with <24h notice: 75% of session fee charged.",
            ],
        },

        "cancellation_policy": (
            "We require at least 24 hours' notice to cancel or rearrange an appointment. "
            "If you cancel with less than 24 hours' notice, or don't attend, "
            "a charge of 75% of the session fee applies. "
            "For prepaid or package appointments cancelled at short notice, "
            "the 75% penalty is deducted from your credit balance. "
            "Packages are non-transferable, valid for 6 months from purchase, "
            "and have a 2-week cooling-off period. "
            "Please refer to our website for full terms and conditions."
        ),
        "what_to_bring": "If you can, bring shorts or wear loose clothing — but don't worry if you can't.",
        "arrival_note": "Please aim to arrive 5 to 10 minutes before your appointment.",

        # Insurance (from Mark)
        "insurance_note": (
            "We operate on a self-pay basis — patients pay directly and may claim back from their insurer if their policy allows. "
            "We don't work directly with any insurers. Bupa is not accepted."
        ),
        "common_insurers": [],
        "not_accepted_insurers": ["Bupa"],
        "insurance_model": "self-pay",

        # Accessibility
        "accessibility": {
            "wheelchair_accessible": True,
            "both_locations": True,
        },

        # Remote consultations
        "remote_consultations": False,  # In-person only — no video or phone appointments

        # Reports and letters
        "reports_and_letters": {
            "available": True,
            "types": ["GP letters", "medico-legal reports", "discharge summaries", "insurance letters"],
            "standard_fee": None,   # No standard fee — discuss with Mark
            "note": "We'll write any report requested. No standard fee — enquire with the team.",
        },

        # Between-session support
        "between_sessions": {
            "contact_methods": ["phone", "email"],
            "callback_aim_days": "1–2 business days",
            "exercise_pain_contact": "Contact Mark directly by phone or message.",
            "post_discharge_return": "Patients can return at any time after discharge — just call to book.",
        },

        # Complaints
        "complaints": {
            "handler": "Mark",
            "instruction": "Please raise any concerns or complaints directly with Mark.",
        },

        # Call handling (from Mark)
        "call_handling": {
            "if_cant_help": ["Direct to website", "Take a message", "Offer a call-back", "Escalate to staff"],
            "immediate_defer_to_human_for": ["Emergencies"],
            "emergency_message": (
                "If this feels urgent or you have severe symptoms, please call 999 (or go to A&E). "
                "We're not an emergency service."
            ),
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

        # -----------------------------------------------------------------------
        # FAQ — spoken answers Susie can read verbatim or paraphrase.
        # Keyed by the topic string used in get_clinic_info tool calls.
        # Goal: answer every common mid-flow caller question without falling back
        # to the semantic sidebar or Claude's training knowledge.
        # -----------------------------------------------------------------------
        "faq": {

            # ── What is a physiotherapy assessment? ────────────────────────────
            "what_is_assessment": (
                "The assessment is a 50-minute one-to-one session with your physiotherapist. "
                "They'll go through your full history — what's hurting, how long it's been going on, "
                "and how it's affecting your daily life. "
                "Then they'll do a hands-on physical examination — looking at your posture, movement, "
                "and strength — and they may also explore any stress or lifestyle factors that could "
                "be contributing. "
                "By the end of that first session, you'll have a clear diagnosis, a personalised "
                "treatment plan, and you'll usually start hands-on treatment in that very same appointment. "
                "The cost is £75 for the 50 minutes."
            ),

            # ── Do I need a GP referral? ────────────────────────────────────────
            "gp_referral": (
                "No — you don't need a GP referral at all. "
                "You can self-refer and book directly with us by phone or online. "
                "If you do have a referral letter or any relevant scan results, do bring them along — "
                "they can be useful — but they're not required to book."
            ),

            # ── How many sessions will I need? ─────────────────────────────────
            "how_many_sessions": (
                "Most patients need somewhere between four and six sessions — "
                "that's the typical range for most musculoskeletal conditions. "
                "Six sessions is generally the point at which we reassess the plan together "
                "and decide what comes next. "
                "That said, it depends on what you're dealing with, how long you've had it, "
                "and how your body responds — some people feel a big difference in just a couple "
                "of sessions, others with more complex or long-standing issues may need more. "
                "After your first assessment you'll have a much clearer picture."
            ),

            # ── What conditions do you treat? ──────────────────────────────────
            "conditions_treated": (
                "We treat a really wide range of musculoskeletal and soft-tissue conditions. "
                "That includes back pain and sciatica, neck pain, shoulder pain — including frozen shoulder "
                "and rotator cuff issues — hip and knee problems, sports injuries, ankle sprains, "
                "plantar fasciitis, Achilles tendon issues, tennis elbow and golfer's elbow, "
                "post-operative rehabilitation, and general muscle strains and joint stiffness. "
                "We also offer acupuncture, psychotherapy, and a prescribing service. "
                "If you're not sure whether your condition is something we can help with, "
                "the best thing to do is book an assessment and let the physiotherapist advise you directly."
            ),

            # ── Tell me about your practitioners ───────────────────────────────
            "practitioners": (
                "We have two chartered physiotherapists. "
                "Mark Dyer is the founder — he holds an MSc and is HCPC-registered. "
                "He works at Awlstuh on Mondays, Tuesdays, and Wednesdays, "
                "and at Redditch on Thursdays. "
                "Leanne is also a chartered physiotherapist and HCPC-registered. "
                "She works at Awlstuh on Thursdays and Fridays, "
                "and at Redditch on Mondays. "
                "Both are qualified prescribers. "
                "If you'd like to see a specific practitioner, just let me know and I'll look for "
                "slots with them."
            ),

            # ── Alcester vs Redditch — which should I choose? ──────────────────
            "location_comparison": (
                "Both clinics offer exactly the same services and the same team. "
                "The main differences are availability and access. "
                "Awlstuh is open Monday to Friday, so you have a lot more choice of days. "
                "It's at the Greig Leisure Centre, which has free parking right outside — "
                "around 80 spaces. "
                "Redditch is only open Mondays and Thursdays, "
                "but it's very convenient if you're near Redditch town centre — "
                "the train station is about a 5 to 7 minute walk from the clinic. "
                "If you just want the most availability, Awlstuh is the better bet. "
                "If Redditch suits your location better and Monday or Thursday works for you, "
                "that's a perfectly good option too."
            ),

            # ── What is shockwave therapy? ─────────────────────────────────────
            "shockwave_description": (
                "Shockwave therapy uses high-energy acoustic sound waves directed at a specific area "
                "of the body. "
                "It stimulates the body's natural healing response, increases blood flow, "
                "and helps break down calcification in stubborn chronic conditions. "
                "It's particularly effective for plantar fasciitis, Achilles tendinopathy, "
                "tennis elbow, calcific shoulder, and other tendon problems that haven't "
                "responded to conventional treatment. "
                "It's not a separate booking — the physiotherapist will decide during your session "
                "whether shockwave is appropriate for you, and it's then used as part of that appointment. "
                "If it's used, a £45 surcharge is added to your session fee."
            ),

            # ── What is MLS Laser therapy? ─────────────────────────────────────
            "laser_description": (
                "MLS stands for Multiwave Locked System. "
                "It's a medical-grade laser that uses two wavelengths of light simultaneously "
                "to reduce pain and inflammation and speed up tissue repair. "
                "It's completely non-invasive and painless — most patients feel a gentle warmth. "
                "It works well for soft tissue injuries, joint pain, tendon issues, and helping "
                "the body recover faster from acute injuries. "
                "Like shockwave, it's decided on during the session rather than booked in advance, "
                "and a £45 surcharge applies if it's used."
            ),

            # ── What is acupuncture? ───────────────────────────────────────────
            "acupuncture_description": (
                "Our physiotherapy-led acupuncture uses fine sterile needles placed at specific "
                "points on the body to stimulate the nervous system, reduce pain, and promote healing. "
                "It's commonly used alongside physiotherapy for musculoskeletal conditions — "
                "particularly chronic pain, muscle tension, headaches, and conditions that haven't "
                "fully responded to other treatments. "
                "Sessions are 50 minutes and cost £75. "
                "You'd normally start with a physiotherapy assessment first, "
                "and acupuncture may then be incorporated into your treatment plan."
            ),

            # ── What is psychotherapy? ─────────────────────────────────────────
            "psychotherapy_description": (
                "Our psychotherapy service provides a confidential, safe space to explore "
                "thoughts, feelings, and emotional well-being. "
                "Techniques can include cognitive approaches, hypnotherapy, and spiritual healing "
                "where appropriate. "
                "At Theorem the approach is holistic — physical and emotional health are seen as "
                "connected — so psychotherapy can be offered alongside physiotherapy, or as a "
                "standalone service. "
                "Sessions are 50 minutes and cost £75. "
                "You can book directly — no referral needed."
            ),

            # ── Do you do home visits? ─────────────────────────────────────────
            "home_visits": (
                "Yes — we do offer home visits. "
                "To arrange one, the best thing to do is get in touch directly by phone or "
                "email at info@theoremhealth.co.uk and the team will sort out the details with you."
            ),

            # ── Can I bring someone with me? ───────────────────────────────────
            "bring_someone": (
                "Yes, absolutely — you're welcome to bring someone with you to your appointment. "
                "Just let us know when you book if you'd like to mention it."
            ),

            # ── Do you offer packages or block booking discounts? ─────────────
            "packages_discounts": (
                "Yes — we do have one package available: four sessions of combined shockwave "
                "and Class IV Laser Therapy for £420, which works out cheaper than booking them individually. "
                "Packages are valid for six months from the date of purchase and are non-transferable. "
                "There's a two-week cooling-off period if you change your mind. "
                "Other than that, individual sessions are priced as standard — "
                "there aren't any general block booking discounts."
            ),

            # ── Can I book online? ─────────────────────────────────────────────
            "online_booking": (
                "Yes — you can book online at theoremhealth.co.uk, or you can book right now "
                "over the phone with me. "
                "Booking by phone is usually the quickest way to get your preferred slot sorted."
            ),

            # ── Do you offer online or telephone consultations? ────────────────
            "online_consultations": (
                "No — all our appointments are in-person only, at either our Awlstuh or "
                "Redditch clinic. "
                "We don't currently offer video or telephone consultations."
            ),

            # ── Do you see children? ───────────────────────────────────────────
            "children_policy": (
                "Yes — we see patients aged 15 and over. "
                "For anyone under 15, we'd recommend speaking to your GP about a paediatric "
                "physiotherapy referral."
            ),

            # ── What happens on my first visit? ───────────────────────────────
            "first_visit": (
                "On the day, try to arrive 5 to 10 minutes early to settle in and complete "
                "any paperwork. "
                "Wear or bring comfortable, loose clothing if you can — particularly shorts or "
                "joggers if it's a lower-body issue — though don't worry if that's not possible. "
                "If you have any relevant scan results, X-rays, MRI reports, or referral letters, "
                "bring those too. "
                "Your physiotherapist will take a full history, carry out a physical examination, "
                "and in most cases begin treatment in that first session."
            ),

            # ── Can you explain the surcharge? ────────────────────────────────
            "surcharge_explained": (
                "The base session fee is £75 — that covers the full physiotherapy consultation "
                "and any hands-on treatment. "
                "A £45 surcharge applies on top if your physiotherapist uses shockwave therapy "
                "or Class IV Laser during that session — that's decided in the room, not at booking, "
                "and you'll always be told before it's applied. "
                "Alternatively, if you want shockwave or laser as a standalone treatment, "
                "that's a separate 30-minute booking at £120. "
                "There's also a package of four combined shockwave and laser sessions for £420 "
                "if you need a course of treatment."
            ),

            # ── Is there a waiting list? ───────────────────────────────────────
            "waitlist": (
                "No — we don't have a waiting list. Appointments are booked directly as slots "
                "become available. "
                "If there's nothing that suits you in the near future, I'd suggest booking the "
                "next available slot and calling back to move it forward if something opens up."
            ),

            # ── What's your website? ───────────────────────────────────────────
            "website": (
                "Our website is theoremhealth.co.uk — you'll find information about all our "
                "services, the team, and you can also book appointments there."
            ),

            # ── Prescribing service ────────────────────────────────────────────
            "prescribing_service": (
                "Both Mark and Leanne are qualified prescribers. "
                "If during your physiotherapy assessment or follow-up it's appropriate to prescribe "
                "medication — for example, analgesia to support your recovery — they can do that "
                "directly without you needing to go back to your GP. "
                "A prescribing consultation is £12.50."
            ),

            # ── Rehabilitation sessions ────────────────────────────────────────
            "rehabilitation": (
                "Our rehabilitation sessions are £65 for 50 minutes and are run by our "
                "rehabilitation instructors. "
                "They're separate from physiotherapy sessions and focus on progressive strengthening "
                "and movement work — helping you rebuild function and get back to full activity. "
                "They're typically recommended after the initial assessment phase, once the "
                "physiotherapist has established a plan."
            ),

            # ── Between-session support ────────────────────────────────────────
            "between_sessions_support": (
                "If you have a question or concern between sessions — for example, if an exercise "
                "is causing pain or something has changed — you're welcome to contact us by "
                "phone or email. "
                "The team aims to get back to you within one to two business days. "
                "If your pain significantly worsens or you develop new symptoms, it's always worth "
                "getting in touch sooner rather than waiting for your next appointment."
            ),

            # ── Reports, letters, medico-legal ────────────────────────────────
            "reports_letters": (
                "Yes — we can provide GP letters, discharge summaries, medico-legal reports, "
                "and insurance letters. "
                "There's no standard fee for these — it depends on what's needed. "
                "If you require any written report or letter, please ask at your appointment or "
                "email info@theoremhealth.co.uk and the team will advise you."
            ),

            # ── Insurance / can I claim back? ─────────────────────────────────
            "insurance_claim": (
                "We operate on a self-pay basis — you pay directly and your insurer isn't "
                "invoiced by us. "
                "However, many patients do claim back from their private health insurer after "
                "paying — it depends on the terms of your policy. "
                "We can provide a receipt or invoice for you to submit to your insurer. "
                "We don't work with Bupa directly."
            ),

            # ── Accessibility ──────────────────────────────────────────────────
            "accessibility": (
                "Both our Awlstuh and Redditch clinics are wheelchair accessible. "
                "If you have any specific requirements or need to discuss access before your visit, "
                "please let us know when you book."
            ),

            # ── How do I contact you after calling? ───────────────────────────
            "contact_after_call": (
                "You can reach us by phone on this number, or by email at info@theoremhealth.co.uk. "
                "We aim to respond to emails within one to two business days."
            ),

            # ── How do I pay? ──────────────────────────────────────────────────
            "payment_methods": (
                "We accept cash, debit card, credit card, and online payment via Stripe. "
                "Payment is taken prior to or directly after your treatment. "
                "If you're on a package, we'll raise an invoice and payment is due within 7 days."
            ),

            # ── Can I book for today? ──────────────────────────────────────────
            "same_day_booking": (
                "We do ask for at least 24 hours' notice for new bookings, so same-day appointments "
                "aren't available. "
                "The earliest I can book you in is tomorrow. "
                "If you need to be seen urgently, I'd recommend calling the team directly at "
                "info@theoremhealth.co.uk or checking the website for any last-minute availability."
            ),

            # ── What's the difference between physio and rehab sessions? ───────
            "physio_vs_rehab_difference": (
                "A physiotherapy session is with Mark or Leanne — a qualified chartered physiotherapist. "
                "It covers assessment, diagnosis, hands-on treatment, and planning. "
                "Your first assessment is 50 minutes; follow-up sessions are 40 minutes. "
                "Both are £75. "
                "A rehabilitation session is with one of our rehabilitation instructors — "
                "it's more exercise and movement focused, designed to rebuild your strength and function "
                "once the treatment plan is established. "
                "Those are £65 for 50 minutes. "
                "Typically you'd start with a physiotherapy assessment, and rehab sessions "
                "come later as part of your recovery programme."
            ),

            # ── Am I a new patient or returning? ──────────────────────────────
            "new_vs_returning": (
                "If it's been less than two years since your last visit, you're treated as a "
                "returning patient and can book a follow-up session. "
                "If it's been two years or more, or if you're coming in for a completely different "
                "problem to the one you were seen for before, we'd ask you to start with a fresh "
                "assessment — that way your physiotherapist can get a full picture of where things "
                "are now. "
                "If you're not sure which applies to you, just let me know and I can check."
            ),

            # ── What if I'm running late? ──────────────────────────────────────
            "running_late": (
                "If you think you're going to be late, please give us a call as soon as you can. "
                "We'll do our best to accommodate you, but depending on how late it is "
                "we may need to shorten the session or ask you to rebook to avoid affecting "
                "the next patient. "
                "The team's contact number is 07870 166861."
            ),

            # ── What should I expect after my first session? ───────────────────
            "what_to_expect_after": (
                "It's completely normal to feel a little sore or achy for 24 to 48 hours after "
                "your first session — especially if hands-on treatment or soft tissue work was involved. "
                "That's a normal response and usually settles quickly. "
                "Your physiotherapist will likely give you some exercises or advice to follow at home "
                "between sessions. "
                "If your pain significantly worsens, you develop new symptoms, or something doesn't "
                "feel right, don't wait — contact the clinic and they'll advise you."
            ),

            # ── Are your physiotherapists qualified? ───────────────────────────
            "qualifications": (
                "Yes — both physiotherapists are fully qualified and HCPC-registered, which is the "
                "statutory regulator for physiotherapists in the UK. "
                "Mark Dyer holds an MSc and is a member of the Chartered Society of Physiotherapy, "
                "the Acupuncture Association of Chartered Physiotherapists, and the Academy of "
                "Clinical Science. "
                "Leanne holds a BSc Honours in Physiotherapy and is also HCPC-registered and a "
                "member of the Chartered Society of Physiotherapy. "
                "Both are qualified prescribers."
            ),

            # ── I've been discharged — can I come back? ────────────────────────
            "returning_after_discharge": (
                "Absolutely — you're always welcome to come back after discharge. "
                "Just call us to book and we'll get you back in. "
                "If it's been two years or more, or you're coming in for a new problem, "
                "we'd usually start with a fresh assessment."
            ),

            # ── I have a work-related injury — can you help? ───────────────────
            "work_injury": (
                "Yes — we treat work-related injuries in exactly the same way as any other "
                "musculoskeletal condition. "
                "We operate on a self-pay basis, so you'd pay directly and then claim back "
                "through your employer or their insurer if your policy allows. "
                "We can provide a receipt or invoice to support any claim you need to make."
            ),

            # ── Can I bring someone with me? ───────────────────────────────────
            "can_bring_someone": (
                "Yes — you're welcome to bring a companion, partner, or carer with you. "
                "They can wait in the waiting area, or if you'd like them in the room with you "
                "during the session, just mention it to your physiotherapist on the day "
                "and they'll be happy to accommodate that where possible."
            ),

            # ── Where do I go inside the Greig Leisure Centre? ────────────────
            "location_inside_greig": (
                "Just walk through the main entrance of the Greig Leisure Centre and "
                "Theorem Health will be indicated from inside — you'll see the signage "
                "directing you from the entrance."
            ),

            # ── Do you offer packages or discounts? ────────────────────────────
            "packages_discounts": (
                "Yes — we have one package: four sessions of combined shockwave and "
                "Class IV Laser Therapy for £420. "
                "Packages are valid for six months and are non-transferable. "
                "Other than that, sessions are individually priced — "
                "there aren't general block booking discounts."
            ),

            # ── Advanced treatments ────────────────────────────────────────────
            "advanced_treatments": (
                "We also offer a range of one-hour advanced treatments: "
                "Reiki and Energy Healing, Wellness and Stress Relief Massage with In-light Therapy, "
                "and Auricular Acupuncture for stress relief. "
                "These are separate from our physiotherapy services. "
                "For pricing and availability, get in touch by phone or email "
                "at info@theoremhealth.co.uk."
            ),
        },
    },
}

# theorem_v2: identical to theorem but with location guards active.
# Mapped to test number +447366530580. Switch live number here once verified.
CLINICS["theorem_v2"] = _copy.deepcopy(CLINICS["theorem"])

# theorem_v3: identical to theorem_v2 — mapped to +447380841468.
CLINICS["theorem_v3"] = _copy.deepcopy(CLINICS["theorem_v2"])


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

ACUITY_CONFIG["theorem_v2"] = ACUITY_CONFIG["theorem"]
ACUITY_CONFIG["theorem_v3"] = ACUITY_CONFIG["theorem"]

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
        "prescribes": True,
        # Flat union for filter_slots_by_practitioner_availability
        "available_days": ["mon", "tue", "wed", "thu"],
        # Per-location breakdown
        "location_days": {
            "alcester": ["mon", "tue", "wed"],
            "redditch": ["thu"],
        },
        "acuity_calendar_id": os.getenv("ACUITY_CALENDAR_ID_MARK"),
    },
    "leanne": {
        "id": "leanne",
        "name": "Leanne",
        "full_name": "Leanne",
        "title": "BSc (Hons) Physiotherapy, Level 3 Extended Diploma in Sports and Exercise Sciences, HCPC, CSP",
        "role": "Chartered Physiotherapist & Prescriber",
        "prescribes": True,
        # Flat union for filter_slots_by_practitioner_availability
        "available_days": ["mon", "thu", "fri"],
        # Per-location breakdown
        "location_days": {
            "alcester": ["thu", "fri"],
            "redditch": ["mon"],
        },
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
        "name": "MLS Laser Therapy",
        "amount_gbp": 45.00,
        "description": "MLS Laser therapy to alleviate pain, reduce inflammation, and speed tissue repair.",
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

# ──────────────────────────────────────────────────────────────────────────
# Data-driven clinics: load operational + prompt config from
# app/clinics/<clinic_id>/clinic.json for clinics NOT in the legacy CLINICS
# dict.  Onboarding a new clinic therefore edits one JSON file (plus the
# Twilio number map) — no Python.  Legacy clinics (demo/theorem*) are
# unaffected: get_clinic() short-circuits on `cid in CLINICS` before any
# disk access.
# ──────────────────────────────────────────────────────────────────────────
_CLINICS_DIR = Path(__file__).resolve().parent / "clinics"
_CLINIC_JSON_CACHE: Dict[str, Any] = {}   # cid -> {"mtime": float, "clinic": dict}


def _hhmm_to_float(value: Any) -> Optional[float]:
    """'16:30' -> 16.5 ; None/closed -> None."""
    if not value or not isinstance(value, str):
        return None
    try:
        h, m = value.split(":")
        return int(h) + int(m) / 60.0
    except Exception:
        return None


def _working_hours_to_tuples(wh: Dict[str, Any], slot_minutes: int) -> Dict[str, Any]:
    """
    Convert {day: {open, last_appointment}} to {day: (start, end)} where
    end = last_appointment + slot_minutes.  is_within_working_hours() uses
    `start <= t < end`, so the buffer keeps the LAST appointment bookable.
    Closed days (None / "Closed") map to None.
    """
    buffer = (slot_minutes or 40) / 60.0
    out: Dict[str, Any] = {}
    for day, spec in (wh or {}).items():
        if not spec or (isinstance(spec, str) and spec.strip().lower() == "closed"):
            out[day] = None
            continue
        start = _hhmm_to_float(spec.get("open"))
        last = _hhmm_to_float(spec.get("last_appointment") or spec.get("close"))
        if start is None or last is None:
            out[day] = None
            continue
        out[day] = (start, last + buffer)
    return out


def _map_json_to_clinic_contract(loaded: Dict[str, Any]) -> Dict[str, Any]:
    """
    Return the full clinic.json dict with the legacy 'clinic contract' keys
    (read by get_clinic callers: booking, SMS, prompts) flattened on top, so
    the SAME dict serves both the booking subsystem and the prompt template.
    """
    clinic = dict(loaded)  # keep services/faq/prompt_facts/etc. for the template
    op = loaded.get("operational", {}) or {}
    slot_minutes = int(op.get("slot_minutes", 40))

    clinic["display_name"] = loaded.get("clinic_name", loaded.get("display_name", "the clinic"))
    clinic["timezone"] = op.get("timezone", "Europe/London")
    clinic["sms_name"] = op.get("sms_name", clinic["display_name"])
    clinic["phone"] = op.get("phone", loaded.get("primary_phone", ""))
    if op.get("transfer_phone"):
        clinic["transfer_phone"] = op["transfer_phone"]
    clinic["booking_system"] = op.get("booking_system", "manual_handoff")
    clinic["calendar_id"] = op.get("calendar_id")
    clinic["digest"] = op.get("digest", {})  # end-of-day booking digest config
    clinic["allow_same_day"] = bool(op.get("allow_same_day", False))
    clinic["slot_minutes"] = slot_minutes
    # Slot-offering increment (spacing between offered start times). Defaults to
    # slot_minutes; set higher (e.g. 60) to offer hourly slots even though the
    # appointment itself is shorter.
    clinic["slot_increment_minutes"] = int(op.get("slot_increment_minutes", slot_minutes))
    clinic["days_ahead"] = int(op.get("days_ahead", 60))
    clinic["working_hours"] = _working_hours_to_tuples(op.get("working_hours", {}), slot_minutes)

    # Some callers expect per-location hours keyed by location id.
    primary = op.get("primary_location") or (
        (loaded.get("locations") or [{}])[0].get("location_id")
    )
    if primary:
        clinic.setdefault("location_working_hours", {})[primary] = clinic["working_hours"]
    return clinic


def _load_clinic_json(cid: str) -> Optional[Dict[str, Any]]:
    """Load + mtime-cache app/clinics/<cid>/clinic.json. None if absent."""
    path = _CLINICS_DIR / cid / "clinic.json"
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    cached = _CLINIC_JSON_CACHE.get(cid)
    if cached and cached["mtime"] == mtime:
        return cached["clinic"]
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    clinic = _map_json_to_clinic_contract(loaded)
    _CLINIC_JSON_CACHE[cid] = {"mtime": mtime, "clinic": clinic}
    return clinic


def get_clinic(clinic_id: Optional[str]) -> Dict[str, Any]:
    """
    Safe getter. Resolution order:
      1. Legacy CLINICS dict (demo/theorem*) — untouched.
      2. Data-driven app/clinics/<id>/clinic.json (new clinics, e.g. jv_v1).
      3. Fallback to demo.
    Injects 'clinic_id' into the returned dict so downstream code
    (e.g. knowledge retrieval) can identify the clinic.
    """
    cid = (clinic_id or "demo").strip().lower()
    if cid in CLINICS:
        clinic = dict(CLINICS[cid])
        clinic["clinic_id"] = cid
        return clinic
    loaded = _load_clinic_json(cid)
    if loaded is not None:
        clinic = dict(loaded)
        clinic["clinic_id"] = cid
        return clinic
    clinic = dict(CLINICS["demo"])
    clinic["clinic_id"] = cid
    return clinic


def is_freeform_clinic(clinic_id: Optional[str]) -> bool:
    """
    True if the clinic runs the free-form LLM loop (the prompt is the brain,
    no FlowEngine state machine): theorem_v3, or any template_v1 clinic.

    Used by the media_streams runtime to route the call. theorem_v3 is matched
    by its literal id (it has no clinic.json / prompt_engine — get_clinic falls
    back to demo for it), so theorem's routing is byte-identical; template
    clinics enter the same loop via their prompt_engine flag.
    """
    cid = (clinic_id or "").strip().lower()
    if cid == "theorem_v3":
        return True
    try:
        return get_clinic(cid).get("prompt_engine") == "template_v1"
    except Exception:
        return False


def single_location_template(clinic_id: Optional[str]) -> Optional[str]:
    """
    For a single-site template clinic, return its one location_id (lowercased);
    otherwise None. Lets the runtime auto-confirm the only site and skip the
    two-clinic location gate. None for theorem_v3 / multi-site / non-template.
    """
    cid = (clinic_id or "").strip().lower()
    if cid == "theorem_v3":
        return None
    try:
        c = get_clinic(cid)
    except Exception:
        return None
    if c.get("prompt_engine") != "template_v1":
        return None
    locs = c.get("locations") or []
    if len(locs) > 1:
        return None
    if locs:
        return (locs[0].get("location_id") or "").strip().lower() or None
    return ((c.get("operational") or {}).get("primary_location") or "").strip().lower() or None


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


def is_practitioner_available_on_day(
    practitioner_id: str,
    day_abbrev: str,
    location_id: str = None,
) -> bool:
    """
    Check if practitioner works on given day, optionally at a specific location.

    Args:
        practitioner_id: "mark" or "leanne"
        day_abbrev: "mon", "tue", "wed", "thu", "fri", "sat", "sun"
        location_id: "alcester" or "redditch" (optional; if omitted uses flat union)

    Returns:
        True if practitioner is available
    """
    prac = THEOREM_PRACTITIONERS.get(practitioner_id)
    if not prac:
        return False
    if location_id:
        location_days = prac.get("location_days", {})
        return day_abbrev in location_days.get(location_id, [])
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

