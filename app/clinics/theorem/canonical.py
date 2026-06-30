"""
Canonical source of truth for Theorem Health and Wellness.

This module is the SINGLE place clinic facts live. Prompts, FAQs, booking
logic, SMS templates and tests should read from here rather than hard-coding
values. It is pure data — it imports nothing from the rest of the app, so any
module can safely import it without circular-import risk.

Scope (this task): create the canonical layer + low-risk wiring + tests.
Conversation logic is NOT rewritten here; consumers are migrated incrementally.

Operational source of truth: Mark's latest edited onboarding document
(applied 2026-06-29, commit 9ea69b9). Where the live codebase still disagrees
with this object, the disagreement is recorded in ``KNOWN_CONFLICTS`` and
surfaced by tests rather than silently resolved.

Conventions
-----------
- Prices are integers/floats in GBP. ``PRICES`` is the one canonical table.
- ``human_confirmation_required: True`` marks a fact Mark must still confirm.
  Do NOT silently change these without sign-off.
- Spelling note: data uses the real spelling "Alcester". The phonetic form
  "Awlstuh" is a TTS-only pronunciation hint (see ``STT_PRONUNCIATION``).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


SCHEMA_VERSION = 1
SOURCE_OF_TRUTH = "Mark Dyer edited onboarding document (applied 2026-06-29)"


# ===========================================================================
# 1. CLINIC IDENTITY
# ===========================================================================
IDENTITY: Dict[str, Any] = {
    "display_name": "Theorem Health and Wellness",
    "brand_names": [
        "Theorem Health and Wellness",
        "Theorem - Health and Wellness",
        "Theorem Health",
        "Theorem Wellness",
    ],
    # Inbound line callers ring (Twilio "To" → clinic_id theorem_v3).
    "inbound_phone_e164": "+447380841468",
    # General/contact number quoted to callers and used for live transfer.
    "contact_phone_spoken": "07870 166861",
    "transfer_phone_e164": "+447870166861",
    "contact_email": "info@theoremhealth.co.uk",
    "websites": [
        "https://theoremhealth.co.uk/",
        "https://www.theoremwellness.co.uk",
    ],
    "company_number": "08116105",
    "owner_name": "Mark Dyer",
    "owner_signature": "Mark Dyer MSc, BSc (Hons) HCPC, Mcsp, AACP, Macs.",
    "theorem_statement": (
        "Just as a mathematical theorem combines principles for a proven "
        "solution, Theorem Health & Wellness integrates health and wellness "
        "interventions into a holistic path toward your complete well-being."
    ),
}


# ===========================================================================
# 2. LOCATIONS
# ===========================================================================
LOCATIONS: Dict[str, Dict[str, Any]] = {
    "alcester": {
        "id": "alcester",
        "name": "Alcester",
        "venue_name": "The Greig Leisure Centre",
        "address_full": (
            "Theorem Health and Wellness, The Greig Leisure Centre, "
            "Kinwarton Road, Alcester, B49 6AD"
        ),
        "postcode": "B49 6AD",
        # Arrival/signage wording (Mark's latest edit).
        "arrival_note": (
            "Look for the Everyone Active sport centre and Theorem Health "
            "signage and the large car park out front."
        ),
        "opening_hours": {
            "monday": "08:30-21:00",
            "tuesday": "08:30-21:00",
            "wednesday": "08:30-21:00",
            "thursday": "08:30-21:00",
            "friday": "08:30-21:00",
            "saturday": None,
            "sunday": None,
        },
        "last_appointment": "18:00",
        "parking": (
            "Free on-site car park with around 80 spaces directly in front of "
            "the building; no time limits; disabled bays near the entrance."
        ),
    },
    "redditch": {
        "id": "redditch",
        "name": "Redditch",
        "venue_name": "Redditch Clinic",
        "address_full": (
            "Theorem Health and Wellness, 51 Bromsgrove Road, Redditch, B97 4RH"
        ),
        "postcode": "B97 4RH",
        "arrival_note": "On the main Bromsgrove Road — next to Smile Dental Care.",
        # Public clinic opening hours. NOTE: practitioner cover is separate —
        # see PRACTITIONERS and KNOWN_CONFLICTS (Redditch Monday staffing).
        "opening_hours": {
            "monday": "09:00-17:00",
            "tuesday": "09:00-17:00",
            "wednesday": "09:00-19:00",
            "thursday": "09:00-19:00",
            "friday": "09:00-17:00",
            "saturday": "09:00-17:00",
            "sunday": None,
        },
        "parking": (
            "Street parking on Bromsgrove Road (check signs on arrival). "
            "Redditch Station car park is ~3 minutes' walk."
        ),
    },
}

HOLIDAY_CLOSURES: List[str] = ["All UK bank holidays"]


# ===========================================================================
# 3. PRACTITIONERS  (single canonical schedule)
# ===========================================================================
# Day keys: mon/tue/wed/thu/fri/sat/sun. location_days is authoritative.
PRACTITIONERS: Dict[str, Dict[str, Any]] = {
    "mark": {
        "id": "mark",
        "name": "Mark",
        "full_name": "Mark Dyer",
        "role": "Founder, Physiotherapist & Prescriber, injection therapy",
        "credentials": "MSc, BSc (Hons) HCPC, MCSP, AACP, MACS",
        "prescribes": True,
        "location_days": {
            "alcester": ["mon", "tue", "wed", "fri"],
            "redditch": ["thu"],
        },
        "notes": "Some clients specifically request Mark. Complaints handled by Mark.",
    },
    "leanne": {
        "id": "leanne",
        "name": "Leanne",
        "full_name": "Leanne",
        "role": "Chartered Physiotherapist & Prescriber",
        "credentials": "BSc (Hons) Physiotherapy, HCPC, CSP",
        "prescribes": True,
        "location_days": {
            "alcester": ["thu"],   # Thursday evenings only
            "redditch": [],        # Not available at Redditch
        },
        "notes": "Some clients specifically request Leanne. Alcester Thursday evenings only.",
    },
}

# Plain-English summary that every "weekly availability at a glance" answer
# must match. (Single canonical phrasing.)
PRACTITIONER_SUMMARY = (
    "Alcester: Mark Mon/Tue/Wed/Fri; Leanne Thursday evenings. "
    "Redditch: Mark Thu. Leanne is not available at Redditch."
)


# ===========================================================================
# 4 & 5. SERVICES + PRICES  (single canonical pricing table)
# ===========================================================================
# routing flags:
#   decided_in_session=True  → clinician decides during a session; must NOT be
#                              the default booking route (book an assessment).
#   enquiry_only=True        → no confirmed price; never invent one.
SERVICES: Dict[str, Dict[str, Any]] = {
    "physio_assessment": {
        "name": "Physiotherapy Assessment",
        "duration_minutes": 50,
        "price_gbp": 85.00,
        "for_patients": ["new", "returning"],
        "locations": ["alcester", "redditch"],
        "default_booking_route": True,
        "description": (
            "Holistic first appointment (around 50 minutes): physical mobility "
            "and strength plus emotional well-being through a psychotherapeutic "
            "lens. Diagnosis and a tailored treatment plan, usually starting "
            "treatment in the same session."
        ),
    },
    "physio_followup": {
        "name": "Physiotherapy Follow-up",
        "duration_minutes": 40,
        "price_gbp": 85.00,
        "for_patients": ["returning"],
        "locations": ["alcester", "redditch"],
        "description": "Progress tracking and treatment-plan adjustment.",
    },
    "remedial_rehab": {
        "name": "Remedial Rehabilitation",
        "duration_minutes": 50,
        "price_gbp": 65.00,
        "for_patients": ["returning"],
        "locations": ["alcester", "redditch"],
        "description": (
            "Progressive, exercise-based recovery with rehabilitation "
            "instructors, alongside physiotherapy. Assessment first."
        ),
    },
    "prescribing": {
        "name": "Prescribing Consultation",
        "duration_minutes": 20,
        "price_gbp": 12.50,
        "for_patients": ["returning"],
        "locations": ["alcester", "redditch"],
        "description": "Qualified prescribers can prescribe e.g. analgesia as part of care.",
    },
    "acupuncture": {
        "name": "Acupuncture",
        "duration_minutes": 50,
        "price_gbp": 85.00,
        "for_patients": ["new", "returning"],
        "locations": ["alcester", "redditch"],
        "description": "Physiotherapy-led acupuncture; usually start with an assessment.",
    },
    "psychotherapy": {
        "name": "Psychotherapy",
        "duration_minutes": 50,
        "price_gbp": 85.00,
        "for_patients": ["new", "returning"],
        "locations": ["alcester"],   # Alcester only
        "referral_required": False,
        "description": (
            "Confidential space to explore thoughts and emotions; can include "
            "hypnotherapy and spiritual healing. Standalone or alongside physio."
        ),
    },
    "standalone_shockwave_laser": {
        "name": "Standalone Shockwave or Class IV Laser",
        "duration_minutes": 30,
        "price_gbp": 130.00,
        "for_patients": ["new", "returning"],
        "locations": ["alcester", "redditch"],
        "description": "Standalone 30-minute shockwave or Class IV Laser session.",
    },
    "wellness_massage": {
        "name": "Wellness and Stress Relief Massage",
        "duration_minutes": 60,
        "price_gbp": 85.00,
        "for_patients": ["new", "returning"],
        "locations": ["alcester"],   # Alcester only
        "description": (
            "One-hour wellness and stress relief massage; may include In-light "
            "Therapy, a gentle light-based treatment to support relaxation and "
            "recovery."
        ),
    },
    # ── Decided-in-session interventions (NOT a default booking route) ──────
    "shockwave_therapy": {
        "name": "Shockwave Therapy",
        "in_session_surcharge_gbp": 45.00,
        "decided_in_session": True,
        "default_booking_route": False,
        "locations": ["alcester", "redditch"],
        "description": (
            "Targeted acoustic waves for chronic tendon issues. Decided during a "
            "session, not booked as the default route; a £45 surcharge applies "
            "when used. Standalone option: see standalone_shockwave_laser."
        ),
    },
    "class_iv_laser": {
        "name": "Class IV Laser Therapy",
        "in_session_surcharge_gbp": 45.00,
        "decided_in_session": True,
        "default_booking_route": False,
        "locations": ["alcester", "redditch"],
        "description": (
            "Deep-penetrating laser to reduce pain/inflammation and speed "
            "repair. Decided during a session; £45 surcharge when used. "
            "Standalone option: see standalone_shockwave_laser."
        ),
    },
    # ── Enquiry-only advanced treatments (never invent a price) ─────────────
    "reiki_energy_healing": {
        "name": "Reiki and Energy Healing",
        "duration_minutes": 60,
        "enquiry_only": True,
        "price_gbp": None,
        "locations": ["alcester", "redditch"],
        "description": "One-hour holistic treatment. Pricing/availability: enquire by phone or email.",
    },
    "auricular_acupuncture": {
        "name": "Auricular Acupuncture",
        "duration_minutes": 60,
        "enquiry_only": True,
        "price_gbp": None,
        "locations": ["alcester", "redditch"],
        "description": "Ear acupuncture for stress relief. Pricing/availability: enquire.",
    },
    "hypnotherapy": {
        "name": "Hypnotherapy",
        "enquiry_only": True,
        "price_gbp": None,
        "locations": ["alcester", "redditch"],
        "description": "Advanced treatment. Pricing/availability: enquire by phone or email.",
    },
}

# Canonical packages / surcharges.
PACKAGES: Dict[str, Dict[str, Any]] = {
    "shockwave_laser_4": {
        "name": "Package of 4 combined shockwave and Class IV Laser sessions",
        "price_gbp": 468.00,
        "sessions": 4,
        "terms": "Non-transferable; valid 6 months from purchase; 14-day (2-week) cooling-off period.",
    },
}

SURCHARGES: Dict[str, Dict[str, Any]] = {
    "specialist_equipment": {
        "name": "Shockwave / Class IV Laser specialist equipment surcharge",
        "price_gbp": 45.00,
        "applied_at": "session",
        "description": "Added to the session bill when shockwave or Class IV Laser is used within a normal session.",
    },
}

# Flat {service_id: price} table for fast lookup / cross-checks.
# None means enquiry-only (no confirmed price).
PRICES: Dict[str, Optional[float]] = {sid: svc.get("price_gbp") for sid, svc in SERVICES.items()}
PRICES["package_shockwave_laser_4"] = PACKAGES["shockwave_laser_4"]["price_gbp"]
PRICES["specialist_equipment_surcharge"] = SURCHARGES["specialist_equipment"]["price_gbp"]

NEW_VS_RETURNING_PRICE_DIFFERENCE = "No difference — both physiotherapy sessions are £85."


# ===========================================================================
# 6. BOOKING POLICIES
# ===========================================================================
BOOKING_POLICIES: Dict[str, Any] = {
    "platform": "Acuity Scheduling",
    "booking_window_days": 180,          # up to 6 months in advance
    "same_day_allowed": False,           # never book same-day
    "minimum_notice_hours": 24,
    "earliest_bookable": "tomorrow",
    "urgent_advice": (
        "If urgent, contact the team directly or check the website for "
        "last-minute availability — the AI must not book same-day."
    ),
    "cancellation_notice_hours": 24,
    "reschedule_notice_hours": 24,       # reschedule under 24h = treated as cancellation
    # Late-cancellation / no-show = full session fee (100%). Confirmed and
    # reconciled across all live sources on 2026-06-29 (clinic_config previously
    # said 75%; now aligned). See KNOWN_CONFLICTS["cancellation_fee_pct"].
    "late_cancellation_fee_pct": 100,
    "no_show_fee_pct": 100,
    "no_waitlist": True,
    "records_follow_patient_across_locations": True,
    "new_patient_threshold_years": 2,    # 2+ years since last visit = treat as new
    "different_injury_requires_new_assessment": True,
    "payment_methods": ["cash", "debit card", "credit card", "online via Stripe"],
    "package_terms": "Non-transferable; valid 6 months; 14-day (2-week) cooling-off period.",
}


# ===========================================================================
# 7. INSURANCE POLICIES
# ===========================================================================
INSURANCE: Dict[str, Any] = {
    "model": "self-pay",
    "explanation": (
        "Self-pay clinic — patients pay directly and may claim back from their "
        "insurer if their policy allows. We don't bill insurers directly."
    ),
    "accepted_insurers": [],
    "not_accepted": ["Bupa"],
    "referrals_require_manual_approval": True,
    "gp_referral_required": False,
}


# ===========================================================================
# 8. SAFETY BOUNDARIES
# ===========================================================================
SAFETY: Dict[str, Any] = {
    "informational_not_diagnostic": True,
    "emphasise_assessment_before_conclusions": True,
    "emergency_keywords": [
        "chest pain", "difficulty breathing", "shortness of breath",
        "stroke", "severe head injury", "loss of consciousness",
        "numbness down one side", "sudden vision loss",
    ],
    "emergency_message": (
        "If this feels urgent or you have severe symptoms, please call 999 or "
        "go to A&E — we're not an emergency service."
    ),
    "clinical_question_deflection": (
        "That's really one for the physiotherapist when you come in — I "
        "wouldn't want to point you wrong on something like that."
    ),
}


# ===========================================================================
# 9. ESCALATION RULES
# ===========================================================================
ESCALATION: Dict[str, Any] = {
    "transfer_phone_e164": IDENTITY["transfer_phone_e164"],
    # Transfer to a human ONLY in these exact situations.
    "transfer_only_when": [
        "Caller explicitly asks to speak to a person / member of staff",
        "Medical emergency mentioned",
        "Three consecutive failed understanding attempts",
    ],
    "never_offer_transfer_unprompted": True,
    "complaints_handler": "Mark",
    "complaints_instruction": "Raise any concerns or complaints directly with Mark.",
    "if_cant_help": ["Direct to website", "Take a message", "Offer a call-back", "Escalate to staff"],
}


# ===========================================================================
# 10. SMS RULES
# ===========================================================================
SMS: Dict[str, Any] = {
    "sms_name": "Theorem Health and Wellness",
    "sms_name_short": "Theorem Health",
    "send_booking_confirmation": True,
    "fees_sent_by_text": True,
    "phone_readback_digit_by_digit": True,
    "phone_readback_example": "So that's 0 7 8 7 0 1 6 6 8 6 1 — is that correct?",
    "callback_aim": "1-2 business days",
}


# ===========================================================================
# 11. STT / PRONUNCIATION VARIANTS
# ===========================================================================
# TTS pronunciation hints (how Susie should SAY a word). Mirrors
# config/pronunciation_dict.json.
STT_PRONUNCIATION: Dict[str, Any] = {
    "tts_say": {
        "Alcester": "Awlstuh",
    },
    # Representative spoken forms callers use. The authoritative recogniser is
    # app/media_streams/location_resolver.py (_HARD_ALC / _HARD_RED / soft sets);
    # this list is the human-readable canonical subset, not the full matcher.
    "alcester_spoken_variants": [
        "alcester", "alchester", "alster", "alcesta", "alkester",
        "ancester", "alcaster",
    ],
    "redditch_spoken_variants": [
        "redditch", "reddit", "red itch", "reditch", "reddich", "raditch",
    ],
    "stt_recogniser_module": "app.media_streams.location_resolver",
    "number_readback_rule": "Always read phone numbers back one digit at a time with spaces.",
}


# ===========================================================================
# 12. AGE / CHILDREN POLICY  (human-confirmation flagged — do NOT guess)
# ===========================================================================
# Mark's latest edited document states patients aged 7 and over. Earlier
# materials said 15+. This module records 7 as the operative value AND flags it
# for human confirmation because the historical inconsistency is unresolved.
AGE_POLICY: Dict[str, Any] = {
    "min_patient_age": 7,
    "under_min_guidance": (
        "For children under 7, contact the clinic directly and/or speak to your "
        "GP about a paediatric physiotherapy referral."
    ),
    "faq_answer": (
        "We see patients aged 7 and over. For anyone under 7, please contact the "
        "clinic directly and we'd also recommend speaking to your GP about a "
        "paediatric physiotherapy referral."
    ),
    # TODO(human-confirm): confirm 7+ with Mark in writing. Earlier docs said
    # 15+. Until confirmed, treat this value as provisional.
    "human_confirmation_required": True,
    "confirmation_note": (
        "Mark's latest doc says 7+; earlier materials said 15+. Awaiting written "
        "sign-off before treating as final."
    ),
}


# ===========================================================================
# KNOWN CONFLICTS  (old/duplicated values that still disagree with canonical)
# ===========================================================================
# Tests assert these are tracked rather than silently resolved. Each entry:
#   field, canonical value, the conflicting source, and resolution status.
KNOWN_CONFLICTS: Dict[str, Dict[str, Any]] = {
    "cancellation_fee_pct": {
        "canonical": 100,
        "conflicting_value": 75,
        "conflicting_source": "app/clinic_config.py CLINICS['theorem'] cancellation_fee_pct / cancellation_policy text",
        "note": "RESOLVED 2026-06-29: confirmed 100% (full fee) and reconciled clinic_config + susie prompt to match.",
        "resolved": True,
        "human_confirmation_required": False,
    },
    "min_patient_age": {
        "canonical": 7,
        "conflicting_value": 15,
        "conflicting_source": "Historical onboarding materials / earlier prompt revisions",
        "note": "7+ applied per latest doc; 15+ appeared in older materials. Confirm with Mark.",
        "resolved": False,
        "human_confirmation_required": True,
    },
    "alcester_opening_hours": {
        "canonical": "08:30-21:00 (per knowledge base / media-streams config)",
        "conflicting_value": "09:00-19:00, last appointment 18:00 (clinic_config + live susie prompt)",
        "conflicting_source": "app/clinic_config.py working_hours/hours_summary; app/prompts/susie_system_prompt.py CLINIC block",
        "note": "Building open-hours vs bookable-slot hours may be conflated. Reference doc did not restate Alcester hours. Confirm with Mark which the AI should quote.",
        "resolved": False,
        "human_confirmation_required": True,
    },
    "redditch_opening_hours": {
        "canonical": "Mon-Sat (Mon/Tue/Fri 09-17, Wed/Thu 09-19, Sat 09-17) per knowledge base / clinic.json",
        "conflicting_value": "'Mondays and Thursdays only, 09-14' (clinic_config) and 'Thursday only' (susie prompt)",
        "conflicting_source": "app/clinic_config.py location hours/FAQs; app/prompts/susie_system_prompt.py",
        "note": "Building open-hours (full week) vs practitioner availability (now Mark Thu only, Leanne gone) are conflated across files. Confirm with Mark what hours/days the AI should state for Redditch.",
        "resolved": False,
        "human_confirmation_required": True,
    },
}


# Single canonical object aggregating every category.
CANONICAL: Dict[str, Any] = {
    "schema_version": SCHEMA_VERSION,
    "source_of_truth": SOURCE_OF_TRUTH,
    "identity": IDENTITY,
    "locations": LOCATIONS,
    "holiday_closures": HOLIDAY_CLOSURES,
    "practitioners": PRACTITIONERS,
    "practitioner_summary": PRACTITIONER_SUMMARY,
    "services": SERVICES,
    "packages": PACKAGES,
    "surcharges": SURCHARGES,
    "prices": PRICES,
    "new_vs_returning_price_difference": NEW_VS_RETURNING_PRICE_DIFFERENCE,
    "booking_policies": BOOKING_POLICIES,
    "insurance": INSURANCE,
    "safety": SAFETY,
    "escalation": ESCALATION,
    "sms": SMS,
    "stt_pronunciation": STT_PRONUNCIATION,
    "age_policy": AGE_POLICY,
    "known_conflicts": KNOWN_CONFLICTS,
}

# Top-level categories every consumer can rely on existing.
REQUIRED_CATEGORIES: List[str] = [
    "identity", "locations", "practitioners", "services", "prices",
    "booking_policies", "insurance", "safety", "escalation", "sms",
    "stt_pronunciation", "age_policy",
]


# ---------------------------------------------------------------------------
# Accessors (thin, dependency-free)
# ---------------------------------------------------------------------------
def get_canonical() -> Dict[str, Any]:
    """Return the full canonical clinic object."""
    return CANONICAL


def get_price(service_id: str) -> Optional[float]:
    """Canonical GBP price for a service id; None if enquiry-only/unknown."""
    return PRICES.get(service_id)


def get_service(service_id: str) -> Optional[Dict[str, Any]]:
    return SERVICES.get(service_id)


def practitioner_days(practitioner_id: str, location_id: str) -> List[str]:
    """Canonical day list for a practitioner at a location ([] if none)."""
    prac = PRACTITIONERS.get(practitioner_id, {})
    return list(prac.get("location_days", {}).get(location_id, []))


def is_enquiry_only(service_id: str) -> bool:
    return bool(SERVICES.get(service_id, {}).get("enquiry_only"))


def is_decided_in_session(service_id: str) -> bool:
    return bool(SERVICES.get(service_id, {}).get("decided_in_session"))


def unconfirmed_facts() -> List[str]:
    """Field names still awaiting human confirmation (age policy + conflicts)."""
    out: List[str] = []
    if AGE_POLICY.get("human_confirmation_required"):
        out.append("age_policy.min_patient_age")
    for field, meta in KNOWN_CONFLICTS.items():
        if meta.get("human_confirmation_required") and not meta.get("resolved"):
            out.append(f"known_conflicts.{field}")
    return out
