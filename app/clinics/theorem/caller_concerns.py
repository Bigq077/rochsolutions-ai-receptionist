"""
Caller-concern layer for Theorem Health (physiotherapy AI receptionist).

Pure-data playbook the receptionist uses to handle real, messy physiotherapy
caller language intelligently: concern category, likely ICP, clinical/conversion
risk, what may/​must-not be said, the safe next step, and the answer style.

Scope / boundaries (deliberate):
- This module holds CALLER UNDERSTANDING + ROUTING + ANSWER PRINCIPLES only.
- It holds NO operational facts (prices, ages, durations, hours). Every such
  fact lives in app/clinics/theorem/canonical.py and is restated nowhere here,
  so the concern layer can never drift from or re-introduce a stale figure.
- It imports nothing heavy at module load (the knowledge import inside
  classify_concern is lazy), so app/prompts/susie_system_prompt.py can import
  build_concern_handling_block() on the hot prompt-build path safely.

Only build_concern_handling_block() is wired into the live prompt today, and it
renders a LEAN subset (red-flags + must-not-say + a few objection scripts). The
full 37-category CALLER_CONCERNS table ships for tests and a future Phase 2
(deterministic per-call concern injection).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


# ===========================================================================
# Controlled vocabularies
# ===========================================================================
INTENT_TYPES = (
    "diagnosis", "self_dx_confirm", "treatment_suitability", "prognosis",
    "price", "booking", "logistics", "escalation",
)

# Where a concern should steer the caller. NEVER auto-book shockwave/laser.
SERVICE_ROUTES = (
    "assessment", "follow_up", "rehab", "acupuncture", "psychotherapy",
    "enquiry_callback", "emergency_redirect", "human_escalation",
)

CLINICAL_RISK = ("low", "medium", "red_flag_screen")
CONVERSION_RISK = ("low", "medium", "high")


# ===========================================================================
# ICP SEGMENTS (the report's 10 buyer groups)
# ===========================================================================
ICP_SEGMENTS: Dict[str, Dict[str, str]] = {
    "older_pain_mobility": {
        "label": "Older pain & mobility (55–80, OA/stiffness/post-op)",
        "win_by": "Sound patient and warm; be clear on access and the first appointment.",
        "lose_by": "Rushing, jargon, or failing to explain the first visit.",
    },
    "active_tendon_sports": {
        "label": "Active tendon & sports (30–60, tendon/shoulder/knee)",
        "win_by": "Explain assessment-first logic and technology options without promising outcomes.",
        "lose_by": "Promising a cure, or refusing to discuss technology at all.",
    },
    "busy_desk_worker": {
        "label": "Busy desk worker (28–55, back/neck/tension)",
        "win_by": "Move fast to availability and location choice.",
        "lose_by": "Long explanations before offering a booking.",
    },
    "teen_athlete_parent": {
        "label": "Teen athlete parent booker (parent calling for a teenager)",
        "win_by": "State the age policy clearly and reassure on the first-appointment format.",
        "lose_by": "Confusing the age policy or sounding unsafe.",
    },
    "existing_patient_admin": {
        "label": "Existing patient — admin & rehab (follow-up/rehab/letters)",
        "win_by": "Recognise the returning-patient pathway cleanly.",
        "lose_by": "Making them re-explain everything.",
    },
    "insurance_reclaimer": {
        "label": "Insurance reclaimer (wants claim-back clarity)",
        "win_by": "Explain self-pay first, claim-back second, and when human help is needed.",
        "lose_by": "Saying insurance is accepted directly when it is not.",
    },
    "persistent_pain_tech_curious": {
        "label": "Persistent pain, tech-curious ('nothing has worked')",
        "win_by": "Explain shockwave/laser are decided clinically, not guaranteed up front.",
        "lose_by": "Overstating success or pre-booking blindly.",
    },
    "holistic_stress_wellbeing": {
        "label": "Holistic stress & wellbeing (acupuncture/psychotherapy)",
        "win_by": "Use calm, credible, non-judgmental language.",
        "lose_by": "Sounding dismissive or mystical without clarity.",
    },
    "rural_convenience": {
        "label": "Rural convenience chooser (decides by travel/clinician/day)",
        "win_by": "Offer the nearest workable option fast.",
        "lose_by": "Not helping them choose between the two clinics.",
    },
    "price_sensitive_comparator": {
        "label": "Price-sensitive comparator (first private enquiry)",
        "win_by": "Explain what the first session includes and what it does not.",
        "lose_by": "Hiding surcharges or sounding evasive.",
    },
}


# ===========================================================================
# SHARED SAFETY + RED FLAGS + OBJECTIONS + ANSWER STYLE
# ===========================================================================
# The non-negotiable "never do" rules (reinforced in the live prompt block).
SAFETY_BOUNDARIES: List[str] = [
    "Never diagnose or name what the caller has.",
    "Never confirm the caller's own self-diagnosis (e.g. 'yes that's sciatica').",
    "Never give specific medication advice (what to take, doses, start/stop).",
    "Never promise recovery, or a specific number of sessions.",
    "Never say shockwave, laser, acupuncture or physio will 'definitely' fix it.",
    "Never interpret scans, X-rays or test results over the phone.",
    "You can give general, non-diagnostic information and recommend an in-person "
    "assessment where appropriate.",
]

# Symptoms that mean STOP and redirect to urgent care. Scoped narrowly: these
# apply only when the caller actually describes them — never to routine aches.
RED_FLAGS: List[str] = [
    "Loss of bladder or bowel control, or numbness around the saddle/groin area.",
    "Numbness, pins-and-needles or weakness down BOTH legs at once.",
    "Rapidly worsening weakness or numbness in a limb.",
    "A fall or injury after which they cannot bear weight, or there is visible deformity.",
    "A swollen, hot, painful calf (possible clot).",
    "Chest pain, breathlessness, or stroke-like symptoms alongside the pain.",
    "Fever or feeling very unwell together with back pain.",
]
RED_FLAG_ACTION = (
    "If the caller describes any of these, do NOT book and do NOT reassure them "
    "it's fine — calmly advise they seek urgent medical help now: call 999 or go "
    "to A&E (or NHS 111 if unsure), then offer to help once they're safe."
)

# Value-led, non-defensive, NUMBER-FREE objection scripts. Prices/ages come from
# the prompt's existing PRICES/POLICIES blocks — never restate them here.
OBJECTION_PLAYBOOK: Dict[str, Dict[str, str]] = {
    "gp_bounce_value": {
        "trigger": "I don't want to pay just to be told to rest / see my GP.",
        "script": "Totally fair concern. The assessment isn't a quick 'go and rest' "
                  "— the physio takes a full history, examines how you move and where "
                  "it's coming from, and you'd usually start hands-on treatment and "
                  "leave with a clear plan in that same first session.",
    },
    "nhs_vs_private": {
        "trigger": "Why go private when the NHS does physio?",
        "script": "The NHS is great, the main differences are speed and continuity "
                  "— you're seen quickly, you see the same physio each time, and you "
                  "get longer one-to-one sessions with the option of more advanced "
                  "treatments if they're appropriate.",
    },
    "surcharge_not_automatic": {
        "trigger": "So if they use laser/shockwave I'm suddenly paying more?",
        "script": "It's never automatic — the surcharge only applies if the physio "
                  "decides shockwave or laser is right for you during the session, "
                  "and they'd always tell you before using it, so there are no "
                  "surprises.",
    },
    "insurance_claimback": {
        "trigger": "I've got Bupa — can you bill them? / Can I claim it back?",
        "script": "We're a self-pay clinic, so we don't bill insurers directly and "
                  "we can't take Bupa directly — you'd pay us and then claim it back "
                  "yourself if your policy allows. We can give you a receipt for that; "
                  "anything to do with codes or pre-authorisation I'd pass to the team.",
    },
    "nothing_worked": {
        "trigger": "I've tried physio elsewhere / nothing has worked.",
        "script": "That's frustrating and more common than you'd think. Without "
                  "guessing why, a fresh assessment looks at it from scratch and the "
                  "physio can bring in different approaches — I can't promise an "
                  "outcome, but a proper reassessment is the sensible next step.",
    },
    "provider_comparison": {
        "trigger": "Why you over a chiropractor / osteopath / sports massage?",
        "script": "They're all valid in their place — our team are chartered, "
                  "HCPC-registered physiotherapists and qualified prescribers, so you "
                  "get an assessment-led plan rather than one fixed technique. The "
                  "best starting point is an assessment so they can tailor it to you.",
    },
    "no_same_day": {
        "trigger": "No same-day? I'm in pain now.",
        "script": "I really hear you. We do ask for at least a day's notice so the "
                  "earliest is tomorrow — if it's severe or you've any urgent warning "
                  "signs, please seek urgent care; otherwise I can get you the very "
                  "first available slot.",
    },
    "no_waitlist": {
        "trigger": "No waiting list? So I just keep ringing?",
        "script": "We don't hold a formal list, but I can book you the next available "
                  "slot now and you're welcome to check back if you'd like something "
                  "sooner — that way you've got something in the diary either way.",
    },
}
# The highest-leverage objections rendered into the live prompt block.
BLOCK_OBJECTION_KEYS = [
    "gp_bounce_value", "nhs_vs_private", "surcharge_not_automatic",
    "insurance_claimback", "nothing_worked",
]

ANSWER_STYLE: Dict[str, Any] = {
    "principles": [
        "Convert: move quickly from vague problem language to the most likely "
        "valid next step (usually a physiotherapy assessment).",
        "Contain: hold the safety boundaries above without sounding cold or robotic.",
        "Calm: sound local, unhurried and human — especially with older, anxious "
        "or distressed callers and carers.",
        "Do the categorisation FOR the caller — don't make them choose between "
        "assessment, follow-up, rehab, acupuncture, shockwave or laser.",
        "Stay within the existing one-to-two-sentence voice rule; don't lecture.",
    ],
    "exemplar_good": (
        "Caller: 'I've blown out my rotator cuff, what should I do?' → "
        "'That sounds really uncomfortable. I can't confirm over the phone whether "
        "it's the rotator cuff, but shoulder problems like that are exactly what the "
        "physio assesses in person — they'd look at your movement and strength and "
        "advise the right treatment. If you've had a major fall, can't lift the arm "
        "at all, or there's visible deformity, it's safer to seek urgent advice "
        "first; otherwise a physiotherapy assessment is the right starting point.'"
    ),
    "exemplar_bad": (
        "Caller: 'I've blown out my rotator cuff, what should I do?' → "
        "'I'm sorry to hear that. Would you like to book an appointment?' "
        "(generic sympathy + jump to booking — avoid this)."
    ),
}


# ===========================================================================
# CALLER CONCERNS — the 37 categories
# ===========================================================================
# Shared base "must not say" used by most clinical concerns.
_BASE_MUST_NOT = [
    "Don't diagnose or name the condition.",
    "Don't confirm the caller's self-diagnosis.",
    "Don't promise recovery or a number of sessions.",
]


def _msk(
    messy: List[str],
    icp: List[str],
    anxiety: str,
    may_say: str,
    next_step: str,
    answer_style: str,
    *,
    clinical_risk: str = "medium",
    conversion_risk: str = "medium",
    intents: Optional[List[str]] = None,
    route: str = "assessment",
    must_not_extra: Optional[List[str]] = None,
    clarify_when: str = "If the caller mentions leg/arm numbness, weakness, or a fall — screen before routing.",
    escalate_when: str = "If any red-flag symptom is described (see RED_FLAGS) → emergency redirect.",
) -> Dict[str, Any]:
    """Builder for the common 'MSK complaint → assessment-first' shape."""
    return {
        "messy_phrases": messy,
        "icp_segments": icp,
        "anxiety": anxiety,
        "intent_types": intents or ["self_dx_confirm", "treatment_suitability", "booking"],
        "clinical_risk": clinical_risk,
        "conversion_risk": conversion_risk,
        "may_say": may_say,
        "must_not_say": _BASE_MUST_NOT + (must_not_extra or []),
        "best_next_step": next_step,
        "service_route": route,
        "clarify_when": clarify_when,
        "escalate_when": escalate_when,
        "answer_style": answer_style,
    }


CALLER_CONCERNS: Dict[str, Dict[str, Any]] = {
    "back_pain": _msk(
        ["my back's gone", "my back is killing me", "done my back in", "bad back"],
        ["busy_desk_worker", "older_pain_mobility"],
        "Is it something serious? Will I just be told to rest?",
        "Physiotherapy is well-suited to back problems; an assessment finds what's "
        "going on and gets you a plan.",
        "Offer a physiotherapy assessment after a quick red-flag check.",
        "'Sounds painful — back problems are exactly what the physio assesses in "
        "person and treats. Shall I get you booked in for an assessment?'",
        clinical_risk="red_flag_screen",
    ),
    "sciatica": _msk(
        ["I think I've got sciatica", "pain shooting down my leg", "it's going down my leg"],
        ["older_pain_mobility", "busy_desk_worker"],
        "I think it's sciatica — what if it's worse than that?",
        "Leg pain like that is something the physio assesses; they'll work out what's "
        "irritating things and what helps.",
        "Red-flag screen, then offer an assessment.",
        "'I can't say over the phone whether it's sciatica, but that kind of leg pain "
        "is just what an assessment is for — as long as there's no numbness in the "
        "saddle area or both legs, an assessment's the right step.'",
        clinical_risk="red_flag_screen",
        must_not_extra=["Don't confirm it is sciatica."],
    ),
    "neck_pain": _msk(
        ["can't turn my neck", "slept funny and my neck's stuck", "stiff neck"],
        ["busy_desk_worker"],
        "Will it sort itself or do I need help?",
        "Neck pain and stiffness are common things the physio sees and treats.",
        "Offer an assessment; mention quick relief is what the first visit aims at.",
        "'That's a really common one — the physio can look at your neck and movement "
        "and get you comfortable. Want me to find you a slot?'",
    ),
    "shoulder_pain": _msk(
        ["my shoulder keeps clicking", "shoulder's been hurting for weeks", "can't lift my arm properly"],
        ["active_tendon_sports", "older_pain_mobility"],
        "Should I be worried about it?",
        "Shoulder problems are exactly what the physio assesses in person.",
        "Offer an assessment; screen if there was a fall or sudden weakness.",
        "'Shoulders are bread-and-butter for the physio — they'd check your movement "
        "and strength and advise. Shall I book you an assessment?'",
        clinical_risk="red_flag_screen",
    ),
    "rotator_cuff": _msk(
        ["I've done my rotator cuff", "blown out my rotator cuff", "torn something in my shoulder"],
        ["active_tendon_sports"],
        "Is it torn? Can Mark sort it?",
        "Suspected rotator-cuff problems are exactly what the physio assesses; they'd "
        "look at movement, strength and symptoms.",
        "Red-flag screen (major fall / can't lift arm / deformity), then assessment.",
        "See ANSWER_STYLE.exemplar_good (the rotator-cuff example).",
        clinical_risk="red_flag_screen",
        must_not_extra=["Don't confirm it is a rotator-cuff tear."],
    ),
    "frozen_shoulder": _msk(
        ["my shoulder's seized up", "think I've got a frozen shoulder", "can hardly move my shoulder"],
        ["older_pain_mobility", "active_tendon_sports"],
        "Will I get the movement back?",
        "Stiff, restricted shoulders are something the physio assesses and works on "
        "gradually.",
        "Offer an assessment.",
        "'I can't label it over the phone, but that loss of movement is just what an "
        "assessment looks at. Shall I get you in?'",
        must_not_extra=["Don't confirm it is frozen shoulder."],
    ),
    "knee_pain": _msk(
        ["my knee's bad", "knee keeps giving way", "can't bend my knee"],
        ["active_tendon_sports", "older_pain_mobility"],
        "Is it something that needs a scan or surgery?",
        "Knee pain is common and the physio assesses what's causing it and what helps.",
        "Screen for a lock/giving-way after trauma, then assessment.",
        "'Knees are really common here — an assessment works out what's going on and "
        "the right plan. Want me to check availability?'",
        clinical_risk="red_flag_screen",
    ),
    "hip_pain": _msk(
        ["my hip's gone", "my hip's really painful", "struggling to walk on my hip"],
        ["older_pain_mobility"],
        "My hip's gone — do I need a physio or A&E?",
        "Hip pain is something the physio assesses; they'll look at movement and "
        "strength.",
        "Screen first: if it followed a fall or they can't weight-bear, redirect to "
        "urgent care; otherwise assessment.",
        "'Let's make sure it's not urgent first — did it follow a fall, and can you "
        "put weight on it? If not, an assessment's the right place to start.'",
        clinical_risk="red_flag_screen",
    ),
    "achilles": _msk(
        ["my Achilles has been bad for months", "pain at the back of my heel", "Achilles is killing me"],
        ["active_tendon_sports", "persistent_pain_tech_curious"],
        "Nothing's helped — is this a shockwave thing?",
        "Long-standing Achilles pain is something the physio assesses; they decide "
        "whether things like shockwave are appropriate.",
        "Offer an assessment; if they ask about shockwave, follow TREATMENT_OVERRIDE.",
        "'Achilles trouble that's dragged on is exactly what an assessment is for — "
        "the physio can see whether something like shockwave would suit. Shall I book "
        "you in?'",
        intents=["treatment_suitability", "self_dx_confirm", "booking"],
    ),
    "plantar_fasciitis": _msk(
        ["I've had plantar fasciitis for ages", "heel pain first thing in the morning", "pain under my foot"],
        ["active_tendon_sports", "older_pain_mobility"],
        "Will laser or shockwave fix it?",
        "Heel and foot pain like that is something the physio sees a lot and assesses.",
        "Offer an assessment; route any laser/shockwave question via TREATMENT_OVERRIDE.",
        "'That's a common one and the physio sees it often — an assessment works out "
        "the right approach for you. Want me to find a slot?'",
        intents=["treatment_suitability", "self_dx_confirm", "booking"],
    ),
    "elbow_tendinopathy": _msk(
        ["I've got tennis elbow", "golfer's elbow", "outside of my elbow's been sore for months"],
        ["active_tendon_sports", "persistent_pain_tech_curious"],
        "Nothing's worked — what can you do differently?",
        "Elbow tendon pain is something the physio assesses and treats.",
        "Offer an assessment; treatment questions via TREATMENT_OVERRIDE.",
        "'Elbow tendon pain is right up the physio's street — an assessment's the "
        "starting point so they can tailor it. Shall I book you in?'",
        intents=["treatment_suitability", "self_dx_confirm", "booking"],
    ),
    "sports_injury": _msk(
        ["I've injured myself playing sport", "done something in training", "tweaked it at the match"],
        ["active_tendon_sports", "teen_athlete_parent"],
        "Will I get back to my sport, and how quickly?",
        "Sports injuries are commonly assessed and treated here.",
        "Offer an assessment; avoid timeline promises.",
        "'We see a lot of sports injuries — an assessment gets you a plan to recover "
        "safely. I can't promise timings, but shall I get you booked?'",
        intents=["prognosis", "treatment_suitability", "booking"],
    ),
    "gym_injury": _msk(
        ["pulled something in the gym", "hurt my back lifting", "did something doing weights"],
        ["active_tendon_sports", "busy_desk_worker"],
        "Should I rest it or push through?",
        "Gym strains are commonly assessed; the physio advises what's right for you.",
        "Offer an assessment; don't give activity/rest advice over the phone.",
        "'I can't advise on rest versus training over the phone, but an assessment "
        "will. Want me to check slots?'",
        intents=["treatment_suitability", "booking"],
        clarify_when="If they ask whether to keep training/rest, route to assessment rather than advise.",
    ),
    "running_injury": _msk(
        ["got a running injury", "knee hurts when I run", "training for a marathon and something's gone"],
        ["active_tendon_sports"],
        "Can I keep running or will I lose my training?",
        "Running injuries are commonly assessed; the physio looks at what's driving it.",
        "Offer an assessment; no training/timeline promises.",
        "'Runners are a big part of what we see — an assessment finds the cause and a "
        "plan. Shall I book you in?'",
        intents=["treatment_suitability", "prognosis", "booking"],
    ),
    "golf_tennis_football_injury": _msk(
        ["hurt myself playing golf", "tennis injury", "did my knee at football"],
        ["active_tendon_sports", "teen_athlete_parent"],
        "Will I be match-fit in time?",
        "Sport-specific injuries are commonly assessed and treated here.",
        "Offer an assessment; avoid promising a return date.",
        "'We treat plenty of golf, tennis and football injuries — an assessment's the "
        "starting point. I can't promise a date, but shall I get you in?'",
        intents=["prognosis", "treatment_suitability", "booking"],
    ),
    "post_op_rehab": _msk(
        ["I'm a few weeks after a knee replacement", "had surgery and need rehab", "post-op physio"],
        ["older_pain_mobility", "existing_patient_admin"],
        "Do you do post-surgery rehab properly?",
        "Post-operative rehabilitation is something we do; bring any hospital letters "
        "to the assessment.",
        "Offer an assessment; suggest bringing relevant paperwork.",
        "'Post-op rehab is a real strength here — an assessment sets the plan, and do "
        "bring any hospital letters. Shall I book you in?'",
        clinical_risk="low",
        conversion_risk="low",
        intents=["treatment_suitability", "booking"],
    ),
    "osteoarthritis_stiffness": _msk(
        ["my knees are getting stiffer and stiffer", "everything aches", "am I too old for physio"],
        ["older_pain_mobility"],
        "Am I too old, or is it too late for this to help?",
        "Physio isn't only for sporty people — stiffness and wear-and-tear are common "
        "reasons people come in, and an assessment looks at what helps.",
        "Reassure warmly, then offer an assessment.",
        "'You're absolutely not too old — a lot of people come in for exactly that. "
        "An assessment looks at what would help keep you moving. Shall I get you in?'",
        clinical_risk="low",
        intents=["self_dx_confirm", "booking"],
    ),
    "chronic_pain": _msk(
        ["I've had this pain for years", "long-term pain that won't shift", "it's always there"],
        ["persistent_pain_tech_curious", "older_pain_mobility"],
        "Will anyone actually take this seriously?",
        "Long-standing pain is something the physio assesses with a whole-person view.",
        "Offer an assessment; warmth first.",
        "'Pain that's hung around for a long time deserves a proper look — an "
        "assessment takes the whole picture into account. Want me to book you in?'",
        clinical_risk="low",
        intents=["self_dx_confirm", "treatment_suitability", "booking"],
    ),
    "nothing_worked": {
        "messy_phrases": ["nothing's worked", "I've tried physio before and it didn't help",
                          "had everything done and still in pain"],
        "icp_segments": ["persistent_pain_tech_curious", "price_sensitive_comparator"],
        "anxiety": "Why would this be any different from everything else I've tried?",
        "intent_types": ["treatment_suitability", "self_dx_confirm", "booking"],
        "clinical_risk": "low",
        "conversion_risk": "high",
        "may_say": "Use OBJECTION_PLAYBOOK['nothing_worked']: a fresh assessment looks at "
                   "it from scratch and can bring in different approaches — no outcome "
                   "promises.",
        "must_not_say": _BASE_MUST_NOT + [
            "Don't criticise the other clinic.",
            "Don't promise you'll succeed where others didn't.",
        ],
        "best_next_step": "Differentiate without over-claiming, then offer an assessment.",
        "service_route": "assessment",
        "clarify_when": "If they name a specific treatment, follow TREATMENT_OVERRIDE.",
        "escalate_when": "If they describe red-flag symptoms → emergency redirect.",
        "answer_style": "'That's frustrating — without guessing why it didn't help, a "
                        "fresh assessment starts from scratch. I can't promise an "
                        "outcome, but shall I get you booked for one?'",
    },
    "shockwave_request": {
        "messy_phrases": ["I need shockwave", "can I just book shockwave", "do you do shockwave"],
        "icp_segments": ["active_tendon_sports", "persistent_pain_tech_curious"],
        "anxiety": "Is the shockwave legit, and is it extra?",
        "intent_types": ["treatment_suitability", "price", "booking"],
        "clinical_risk": "medium",
        "conversion_risk": "high",
        "may_say": "Shockwave is something the physio works with; whether it suits you is "
                   "decided in a session, so the starting point is an assessment. Follow "
                   "the existing TREATMENT_OVERRIDE wording.",
        "must_not_say": _BASE_MUST_NOT + [
            "Don't auto-book a standalone shockwave session as the default.",
            "Don't say shockwave will definitely work.",
        ],
        "best_next_step": "Recommend an assessment first (TREATMENT_OVERRIDE); answer price "
                          "from the prompt's PRICES block if asked.",
        "service_route": "assessment",
        "clarify_when": "If they ask the price/standalone option, give it from PRICES but keep the assessment-first steer.",
        "escalate_when": "If they insist on booking a standalone session with no assessment, treat as enquiry/team handoff.",
        "answer_style": "'Shockwave's something Mark works with — we'd start with an "
                        "assessment so he can check it's right for you. Shall I book one?'",
    },
    "laser_request": {
        "messy_phrases": ["would laser fix plantar fasciitis", "I want the laser treatment",
                          "is the laser any good for tendons"],
        "icp_segments": ["persistent_pain_tech_curious", "active_tendon_sports"],
        "anxiety": "Will the laser definitely speed things up?",
        "intent_types": ["treatment_suitability", "price", "booking"],
        "clinical_risk": "medium",
        "conversion_risk": "high",
        "may_say": "Class IV laser is something the physio works with; suitability is a "
                   "clinical decision in a session. Follow TREATMENT_OVERRIDE.",
        "must_not_say": _BASE_MUST_NOT + [
            "Don't auto-book standalone laser as the default.",
            "Don't say laser will definitely help.",
        ],
        "best_next_step": "Recommend an assessment first; price from PRICES if asked.",
        "service_route": "assessment",
        "clarify_when": "If they ask price/standalone, give it from PRICES, keep the assessment-first steer.",
        "escalate_when": "If they want a standalone session with no assessment, treat as enquiry/team handoff.",
        "answer_style": "'Laser's part of what the physio does — I can't say whether it'd "
                        "suit you, that's decided at an assessment. Shall I get you one?'",
    },
    "massage_request": {
        "messy_phrases": ["I just need a massage", "can I get a sports massage", "I only want a rub down"],
        "icp_segments": ["busy_desk_worker", "holistic_stress_wellbeing"],
        "anxiety": "Do I really need a whole assessment, or just a massage?",
        "intent_types": ["treatment_suitability", "booking"],
        "clinical_risk": "low",
        "conversion_risk": "medium",
        "may_say": "Clarify what they want to solve. Hands-on/soft-tissue work is part of "
                   "physiotherapy; for relaxation there's the wellness massage (enquiry). "
                   "Don't dismiss them.",
        "must_not_say": ["Don't dismiss the request.", "Don't diagnose."],
        "best_next_step": "Ask whether it's for a specific pain/injury (→ assessment) or "
                          "relaxation (→ wellness enquiry).",
        "service_route": "assessment",
        "clarify_when": "Always clarify the goal — pain/injury vs relaxation — before routing.",
        "escalate_when": "If purely a wellness-massage enquiry, route to enquiry/callback.",
        "answer_style": "'Happy to help — is it for a specific niggle or injury, or more "
                        "for relaxation? That tells me whether an assessment or our "
                        "wellness side is the better fit.'",
    },
    "stress_tension": {
        "messy_phrases": ["I'm so stressed and tense all the time", "I can't relax in my body",
                          "it's all in my shoulders and neck"],
        "icp_segments": ["holistic_stress_wellbeing", "busy_desk_worker"],
        "anxiety": "Is this professional or a bit 'woo'? Where do I even start?",
        "intent_types": ["treatment_suitability", "booking"],
        "clinical_risk": "low",
        "conversion_risk": "medium",
        "may_say": "Theorem takes a whole-person view; options include physiotherapy for "
                   "physical tension, acupuncture, or psychotherapy. Keep it credible and "
                   "non-judgmental.",
        "must_not_say": ["Don't act as a therapist or give clinical/psychological advice.",
                         "Don't diagnose."],
        "best_next_step": "Ask one clarifying question (mainly physical tension vs emotional) "
                          "and route gently.",
        "service_route": "enquiry_callback",
        "clarify_when": "Clarify whether the main concern is physical tension or emotional wellbeing.",
        "escalate_when": "If the caller is in distress or mentions crisis, hand to a human warmly.",
        "answer_style": "'That sounds draining. Is it mostly the physical tension, or more "
                        "how you're feeling in yourself? That helps me point you to the "
                        "right starting place.'",
    },
    "headaches_tension": _msk(
        ["I keep getting headaches", "tension headaches from my neck", "headaches and a tight neck"],
        ["busy_desk_worker"],
        "Is it coming from my neck, and can you help?",
        "Many tension-type headaches relate to the neck, which the physio can assess.",
        "Offer an assessment; don't diagnose the headache.",
        "'Headaches often tie back to the neck, which the physio can look at — I "
        "can't say what's causing yours, but an assessment's a good step. Shall I "
        "book you?'",
        clinical_risk="medium",
        intents=["self_dx_confirm", "treatment_suitability", "booking"],
    ),
    "acupuncture_interest": {
        "messy_phrases": ["do you do acupuncture", "is acupuncture for pain or stress",
                          "I've never had acupuncture, does it hurt"],
        "icp_segments": ["holistic_stress_wellbeing", "active_tendon_sports"],
        "anxiety": "Does it hurt, and is it legit?",
        "intent_types": ["treatment_suitability", "price", "booking"],
        "clinical_risk": "low",
        "conversion_risk": "low",
        "may_say": "Acupuncture is offered (physio-led); for a specific pain it usually "
                   "follows an assessment, for general wellbeing it can be discussed. Keep "
                   "reassurance balanced and non-alarmist.",
        "must_not_say": ["Don't promise it will work.", "Don't diagnose."],
        "best_next_step": "Clarify pain vs wellbeing; route to assessment (pain) or "
                          "acupuncture/enquiry.",
        "service_route": "acupuncture",
        "clarify_when": "Clarify whether it's for a specific pain or general wellbeing.",
        "escalate_when": "Pricing/availability edge cases → enquiry/callback.",
        "answer_style": "'Yes, we offer acupuncture. Is it for a specific pain or more for "
                        "general wellbeing? For pain we'd usually start with an "
                        "assessment so it's tailored.'",
    },
    "psychotherapy_wellbeing": {
        "messy_phrases": ["I've had a rough few months", "I think I need to talk to someone",
                          "is the psychotherapy proper counselling"],
        "icp_segments": ["holistic_stress_wellbeing"],
        "anxiety": "Is this credible, and is it face to face?",
        "intent_types": ["treatment_suitability", "logistics", "booking"],
        "clinical_risk": "low",
        "conversion_risk": "medium",
        "may_say": "Psychotherapy is offered in-person and provides a confidential space; "
                   "describe it plainly and credibly.",
        "must_not_say": ["Don't act as a counsellor or give therapeutic/clinical advice.",
                         "Don't diagnose mental-health conditions."],
        "best_next_step": "Confirm it's offered and in-person; route to booking/enquiry gently.",
        "service_route": "psychotherapy",
        "clarify_when": "If unsure whether physio or psychotherapy fits, ask the main concern.",
        "escalate_when": "If the caller is in crisis or distress, hand to a human warmly; signpost urgent help if needed.",
        "answer_style": "'Sorry you've had a tough time. Our psychotherapy is a "
                        "confidential, in-person space to talk things through — would you "
                        "like me to help you get started?'",
    },
    "medication_prescribing": {
        "messy_phrases": ["can Mark prescribe painkillers", "what anti-inflammatory should I take",
                          "can you tell me what to take for the pain"],
        "icp_segments": ["older_pain_mobility", "busy_desk_worker"],
        "anxiety": "Can you just tell me what to take?",
        "intent_types": ["diagnosis", "treatment_suitability", "booking"],
        "clinical_risk": "red_flag_screen",
        "conversion_risk": "low",
        "may_say": "The physios are qualified prescribers, so prescribing can form part of "
                   "care — but no specific medication advice can be given over the phone.",
        "must_not_say": ["Never advise what medication to take, doses, or to start/stop anything.",
                         "Don't diagnose."],
        "best_next_step": "Explain prescribing exists within care; for what-to-take-now, "
                          "redirect to pharmacist/GP; offer an assessment.",
        "service_route": "assessment",
        "clarify_when": "Separate 'do you prescribe?' (yes, in care) from 'what should I take?' (cannot advise).",
        "escalate_when": "If asking to stop/start meds or in acute distress → redirect to pharmacist/GP/urgent care.",
        "answer_style": "'Our physios are prescribers, so that can be part of your care — "
                        "but I can't advise what to take over the phone. For something "
                        "now, a pharmacist or GP is best; shall I book you an assessment?'",
    },
    "insurance_bupa": {
        "messy_phrases": ["I've got Bupa, do you take it", "can I claim this back",
                          "I'm with AXA, do you take insurance"],
        "icp_segments": ["insurance_reclaimer", "price_sensitive_comparator"],
        "anxiety": "Will I get my money back? Will you bill them?",
        "intent_types": ["price", "logistics", "booking"],
        "clinical_risk": "low",
        "conversion_risk": "high",
        "may_say": "Use OBJECTION_PLAYBOOK['insurance_claimback']: self-pay, no direct Bupa, "
                   "claim back yourself if your policy allows; receipts available.",
        "must_not_say": ["Never say insurance is billed/accepted directly.",
                         "Don't guarantee a policy will reimburse.",
                         "Don't invent codes or pre-authorisation details."],
        "best_next_step": "Explain self-pay then claim-back; hand codes/forms to the team; "
                          "offer an assessment.",
        "service_route": "assessment",
        "clarify_when": "If they ask about codes, pre-auth, or forms, route to the team rather than guess.",
        "escalate_when": "Insurer codes, referral numbers, pre-authorisation, bespoke paperwork → human handoff.",
        "answer_style": "'We're self-pay so we don't bill insurers directly and can't take "
                        "Bupa directly — you'd pay us and claim it back if your policy "
                        "allows, and we'll give you a receipt. Shall I get you booked?'",
    },
    "price_objection": {
        "messy_phrases": ["I don't want to pay just to be told to rest", "is it worth the money",
                          "I don't want to waste money on this"],
        "icp_segments": ["price_sensitive_comparator", "busy_desk_worker"],
        "anxiety": "Will I get value, or be sent away / upsold?",
        "intent_types": ["price", "treatment_suitability", "booking"],
        "clinical_risk": "low",
        "conversion_risk": "high",
        "may_say": "Use OBJECTION_PLAYBOOK['gp_bounce_value'] and ['surcharge_not_automatic']: "
                   "make the first session tangible; surcharges aren't automatic.",
        "must_not_say": ["Don't get defensive.", "Don't hide surcharges.", "Don't diagnose."],
        "best_next_step": "Explain what the assessment includes (value), then offer it.",
        "service_route": "assessment",
        "clarify_when": "If worried about extras, explain when a surcharge would and wouldn't apply.",
        "escalate_when": "n/a (handle conversationally).",
        "answer_style": "'Fair enough — the first session isn't a quick 'go and rest'. "
                        "You get a full assessment, hands-on treatment and a clear plan in "
                        "that same visit. Shall I find you a slot?'",
    },
    "nhs_vs_private": {
        "messy_phrases": ["why go private when the NHS does physio", "what's your advantage over the NHS",
                          "are you just a private version of NHS physio"],
        "icp_segments": ["price_sensitive_comparator", "busy_desk_worker"],
        "anxiety": "Is private actually worth it over the NHS?",
        "intent_types": ["treatment_suitability", "price", "booking"],
        "clinical_risk": "low",
        "conversion_risk": "high",
        "may_say": "Use OBJECTION_PLAYBOOK['nhs_vs_private']: speed, continuity, longer "
                   "one-to-one sessions, broader options. Be honest, not disparaging.",
        "must_not_say": ["Don't run down the NHS.", "Don't promise better outcomes."],
        "best_next_step": "Differentiate honestly, then offer an assessment.",
        "service_route": "assessment",
        "clarify_when": "n/a.",
        "escalate_when": "n/a.",
        "answer_style": "'The NHS is great — the difference is mainly speed and continuity: "
                        "seen quickly, same physio each time, longer sessions. Want me to "
                        "check what's available?'",
    },
    "provider_comparison": {
        "messy_phrases": ["why you over a chiropractor", "what's the difference vs an osteopath",
                          "my mate said just get a sports massage"],
        "icp_segments": ["price_sensitive_comparator", "active_tendon_sports"],
        "anxiety": "Am I picking the right type of professional?",
        "intent_types": ["treatment_suitability", "booking"],
        "clinical_risk": "low",
        "conversion_risk": "medium",
        "may_say": "Use OBJECTION_PLAYBOOK['provider_comparison']: chartered HCPC physios and "
                   "prescribers; assessment-led, not one fixed technique. Stay factual.",
        "must_not_say": ["Don't disparage other professions.", "Don't diagnose."],
        "best_next_step": "Position physio's assessment-led model, then offer an assessment.",
        "service_route": "assessment",
        "clarify_when": "n/a.",
        "escalate_when": "n/a.",
        "answer_style": "'They've all got their place — our team are chartered physios and "
                        "prescribers, so you get an assessment-led plan rather than one "
                        "fixed technique. Shall I book you an assessment?'",
    },
    "existing_followup": {
        "messy_phrases": ["I'm due back, do I book a follow-up", "Mark asked me to book rehab next",
                          "I saw Leanne before and want to come back"],
        "icp_segments": ["existing_patient_admin"],
        "anxiety": "Will you sort this properly without me re-explaining everything?",
        "intent_types": ["booking", "logistics"],
        "clinical_risk": "low",
        "conversion_risk": "low",
        "may_say": "Recognise the returning pathway; route to follow-up or rehab as they "
                   "describe. If it's a brand-new problem or a long gap, it may be a fresh "
                   "assessment (per policy).",
        "must_not_say": ["Don't paraphrase clinical advice the clinician gave.",
                         "Don't re-diagnose."],
        "best_next_step": "Confirm purpose, route to follow-up/rehab; clarify new-vs-returning if unsure.",
        "service_route": "follow_up",
        "clarify_when": "If it's a new problem or a long gap since the last visit, clarify whether it's a fresh assessment.",
        "escalate_when": "Booking-record lookups (which clinician/site/date) → use the lookup tool or human handoff.",
        "answer_style": "'Welcome back — sounds like a follow-up. Let me get that sorted "
                        "for you; was it with Mark or Leanne you saw?'",
    },
    "report_letter": {
        "messy_phrases": ["can I get a letter for work", "do you do insurance reports",
                          "I need a note for occupational health"],
        "icp_segments": ["existing_patient_admin", "insurance_reclaimer", "busy_desk_worker"],
        "anxiety": "Is this going to be a hassle, and how long will it take?",
        "intent_types": ["logistics", "escalation"],
        "clinical_risk": "medium",
        "conversion_risk": "low",
        "may_say": "Letters/reports can be arranged but are a clinician/admin task — don't "
                   "promise turnaround or fees.",
        "must_not_say": ["Don't promise a turnaround time or fee.",
                         "Don't provide diagnosis/prognosis content for a letter.",
                         "Don't give work-restriction (fit-note style) advice."],
        "best_next_step": "Take details and hand to the team for a call-back.",
        "service_route": "human_escalation",
        "clarify_when": "n/a — route to the team.",
        "escalate_when": "Always: letters/reports are a manual clinician/admin pathway.",
        "answer_style": "'We can usually help with letters, but it's handled by the team "
                        "rather than something I can promise timings on — can I take your "
                        "details and get someone to call you back?'",
    },
    "home_visit": {
        "messy_phrases": ["do you do home visits", "can someone come to my dad", "my mum's housebound"],
        "icp_segments": ["older_pain_mobility"],
        "anxiety": "Can you actually come to us, and how does it work?",
        "intent_types": ["logistics", "booking", "escalation"],
        "clinical_risk": "medium",
        "conversion_risk": "low",
        "may_say": "Home visits are offered but arranged directly by the team (phone/email), "
                   "not booked like a standard appointment.",
        "must_not_say": ["Don't book it as a standard slot.",
                         "Don't quote home-visit price/area/lead-time (not confirmed)."],
        "best_next_step": "Explain it's a direct arrangement; take details for a call-back.",
        "service_route": "enquiry_callback",
        "clarify_when": "If the person is acutely unwell or had a fall and can't get up, screen for emergency.",
        "escalate_when": "Always route to the team; if urgent/emergency, redirect to urgent care.",
        "answer_style": "'We do offer home visits, but they're arranged directly with the "
                        "team rather than booked online — can I take a few details and get "
                        "someone to call you back to sort it?'",
    },
    "teen_booking": {
        "messy_phrases": ["my daughter's hurt her ankle playing netball", "can I book my teenager in",
                          "my lad's done his knee at football"],
        "icp_segments": ["teen_athlete_parent"],
        "anxiety": "Are you good with teenagers, and can I stay in the room?",
        "intent_types": ["booking", "logistics"],
        "clinical_risk": "medium",
        "conversion_risk": "medium",
        "may_say": "Teenagers at or above the clinic's minimum age can be seen (state the "
                   "policy from the prompt, don't invent it). Reassure on the first-visit "
                   "format; a parent attending is a support-person question.",
        "must_not_say": ["Don't confirm the injury/condition.",
                         "Don't promise a return-to-sport date.",
                         "Don't state the age threshold from memory — use the prompt's policy."],
        "best_next_step": "Confirm eligibility per the age policy, then offer an assessment.",
        "service_route": "assessment",
        "clarify_when": "Confirm the teenager's age against the policy before booking.",
        "escalate_when": "If below the minimum age → see 'underage'. Support-person/chaperone specifics → team.",
        "answer_style": "'We can see teenagers from our minimum age — happy to get an "
                        "assessment booked. Can I check how old they are so I'm sure we're "
                        "good to go?'",
    },
    "underage": {
        "messy_phrases": ["my son is younger than that, can you make an exception",
                          "he's not quite old enough but can you fit him in",
                          "can you see a little one"],
        "icp_segments": ["teen_athlete_parent"],
        "anxiety": "Why won't you see my child — where do I go?",
        "intent_types": ["booking", "escalation"],
        "clinical_risk": "medium",
        "conversion_risk": "low",
        "may_say": "Below the clinic's minimum age, they can't be booked here; advise "
                   "contacting the clinic directly and/or the GP about paediatric physio. "
                   "Hold the boundary kindly.",
        "must_not_say": ["Don't bend or make an exception to the age policy.",
                         "Don't diagnose the child.",
                         "Don't state the threshold number from memory — use the prompt's policy."],
        "best_next_step": "Decline kindly within policy and redirect to GP/paediatric physio.",
        "service_route": "human_escalation",
        "clarify_when": "Edge cases (e.g. turning the minimum age soon) → confirm with the team rather than improvise.",
        "escalate_when": "Any pressure for an exception → hold policy; edge cases → team.",
        "answer_style": "'I'm sorry, they're under the age we're able to see here — for "
                        "little ones I'd contact the clinic directly and ask your GP about "
                        "a paediatric physio referral.'",
    },
    "red_flag_urgent": {
        "messy_phrases": ["I've gone numb around my bottom", "I can't control my bladder since my back went",
                          "both my legs have gone numb", "I've fallen and can't put weight on it",
                          "my calf's swollen and hot", "I've got chest pain with it"],
        "icp_segments": ["older_pain_mobility", "busy_desk_worker"],
        "anxiety": "Is this serious — do I need to be seen now?",
        "intent_types": ["escalation", "diagnosis"],
        "clinical_risk": "red_flag_screen",
        "conversion_risk": "low",
        "may_say": "Calmly advise urgent medical help now (999/A&E, or NHS 111 if unsure). "
                   "Do not book and do not reassure it's nothing.",
        "must_not_say": ["Don't book a physio appointment instead of redirecting.",
                         "Don't reassure them it's fine.",
                         "Don't diagnose or interpret the symptom."],
        "best_next_step": "Emergency redirect (see RED_FLAGS / RED_FLAG_ACTION).",
        "service_route": "emergency_redirect",
        "clarify_when": "If genuinely unsure whether it's a red flag, err on the side of urgent advice.",
        "escalate_when": "Always: redirect to 999/A&E/111 immediately.",
        "answer_style": "'That's something that needs checking urgently rather than physio "
                        "— please call 999 or go to A&E now (or 111 if you're unsure). Once "
                        "you're seen and safe, we're here to help.'",
    },
}


# The exact concern keys the system must support (the user's required list).
REQUIRED_CONCERN_KEYS: List[str] = list(CALLER_CONCERNS.keys())


# ===========================================================================
# Lightweight classifier (reuses existing condition detection; tests + Phase 2)
# ===========================================================================
# Direct keyword → concern map. Red flags are checked FIRST and win.
_RED_FLAG_KEYWORDS = (
    "saddle", "bladder", "bowel", "both legs", "numb all over", "can't weight",
    "cant weight", "can't put weight", "cannot bear weight", "swollen calf",
    "hot calf", "chest pain", "numbness round", "numb around", "wet myself",
)
_CONCERN_KEYWORDS: Dict[str, List[str]] = {
    "red_flag_urgent": list(_RED_FLAG_KEYWORDS),
    "sciatica": ["sciatica", "down my leg", "shooting down", "leg pain"],
    "rotator_cuff": ["rotator cuff", "rotator"],
    "frozen_shoulder": ["frozen shoulder", "seized up shoulder"],
    "shoulder_pain": ["shoulder"],
    "neck_pain": ["neck"],
    "achilles": ["achilles"],
    "plantar_fasciitis": ["plantar", "heel pain", "bottom of my foot", "under my foot"],
    "elbow_tendinopathy": ["tennis elbow", "golfer's elbow", "golfers elbow", "elbow"],
    "knee_pain": ["knee"],
    "hip_pain": ["hip"],
    "post_op_rehab": ["after surgery", "post-op", "post op", "replacement", "operation"],
    "osteoarthritis_stiffness": ["arthritis", "stiffer", "stiffness", "wear and tear"],
    "headaches_tension": ["headache", "migraine"],
    "back_pain": ["back's gone", "backs gone", "bad back", "my back", "lower back", "back pain"],
    "running_injury": ["running", "runner", "marathon", "jogging"],
    "gym_injury": ["gym", "lifting weights", "in the gym", "deadlift"],
    "golf_tennis_football_injury": ["golf", "tennis", "football", "rugby", "netball"],
    "sports_injury": ["playing sport", "sports injury", "at training", "at the match"],
    "shockwave_request": ["shockwave", "shock wave"],
    "laser_request": ["laser", "mls"],
    "massage_request": ["massage", "rub down", "deep tissue"],
    "acupuncture_interest": ["acupuncture", "needles", "auricular"],
    "psychotherapy_wellbeing": ["psychotherapy", "counselling", "talk to someone", "emotional"],
    "stress_tension": ["stressed", "stress", "tense", "can't relax", "burnout"],
    "medication_prescribing": ["prescribe", "painkiller", "anti-inflammatory", "medication", "tablets"],
    "insurance_bupa": ["bupa", "insurance", "insurer", "claim", "axa", "aviva", "wpa", "vitality"],
    "nhs_vs_private": ["nhs", "why private", "go private"],
    "provider_comparison": ["chiropractor", "osteopath", "osteo", "sports therapy", "sports massage"],
    "price_objection": ["waste money", "worth the money", "worth it", "just to be told", "too expensive"],
    "report_letter": ["letter", "report", "occupational health", "note for work", "sick note", "fit note"],
    "home_visit": ["home visit", "come to my", "housebound", "can't get out", "visit at home"],
    "teen_booking": ["my son", "my daughter", "my teenager", "my lad", "my girl", "teenager"],
    "underage": ["make an exception", "not quite old enough", "too young", "little one", "under your"],
    "nothing_worked": ["nothing's worked", "nothing has worked", "didn't help", "tried everything",
                       "didn't do much", "hasn't worked", "still in pain"],
    "chronic_pain": ["for years", "long term", "long-term", "always there", "won't shift", "months and months"],
}

# Map app.knowledge.knowledge.identify_condition() ids → concern keys (fallback).
_CONDITION_TO_CONCERN: Dict[str, str] = {
    "lower_back_pain": "back_pain", "upper_back_pain": "back_pain",
    "disc_problems": "back_pain", "sciatica": "sciatica",
    "neck_pain": "neck_pain", "whiplash": "neck_pain",
    "shoulder_pain": "shoulder_pain", "frozen_shoulder": "frozen_shoulder",
    "rotator_cuff": "rotator_cuff", "tennis_elbow": "elbow_tendinopathy",
    "golfers_elbow": "elbow_tendinopathy", "knee_pain": "knee_pain",
    "meniscus_injury": "knee_pain", "runners_knee": "running_injury",
    "it_band_syndrome": "running_injury", "hip_pain": "hip_pain",
    "ankle_pain": "sports_injury", "plantar_fasciitis": "plantar_fasciitis",
    "achilles_tendonitis": "achilles", "sports_injury": "sports_injury",
    "running_injury": "running_injury", "muscle_strain": "gym_injury",
    "post_surgery": "post_op_rehab", "joint_replacement": "post_op_rehab",
    "acl_injury": "post_op_rehab", "chronic_pain": "chronic_pain",
    "arthritis": "osteoarthritis_stiffness", "headaches": "headaches_tension",
    "migraines": "headaches_tension", "stress_anxiety": "stress_tension",
    "depression": "psychotherapy_wellbeing", "trauma_ptsd": "psychotherapy_wellbeing",
}


def classify_concern(text: Optional[str]) -> Optional[str]:
    """
    Best-effort map a (messy) caller utterance to a concern key.

    Order: red flags first, then longest direct-keyword match, then a fallback to
    the existing knowledge-base condition identifier. Returns None if nothing
    matches. Pure-ish: the knowledge import is lazy so this module stays
    dependency-free at import time.
    """
    if not text or not isinstance(text, str):
        return None
    t = text.lower()

    # 1) Priority tiers: red flags first, then named-treatment requests — a named
    #    treatment (shockwave/laser/massage/acupuncture) is a treatment-suitability
    #    question that must route via TREATMENT_OVERRIDE, so it wins over a body
    #    part the caller also mentions ("would laser fix plantar fasciitis").
    for key in ("red_flag_urgent", "shockwave_request", "laser_request",
                "massage_request", "acupuncture_interest"):
        for kw in _CONCERN_KEYWORDS.get(key, []):
            if kw in t:
                return key

    # 2) Longest direct keyword match across the remaining concerns.
    _priority = {"red_flag_urgent", "shockwave_request", "laser_request",
                 "massage_request", "acupuncture_interest"}
    best_key: Optional[str] = None
    best_len = 0
    for key, kws in _CONCERN_KEYWORDS.items():
        if key in _priority:
            continue
        for kw in kws:
            if kw in t and len(kw) > best_len:
                best_key, best_len = key, len(kw)
    if best_key:
        return best_key

    # 3) Fallback: reuse the existing condition identifier.
    try:
        from app.knowledge.knowledge import identify_condition
        cond = identify_condition(t)
    except Exception:
        cond = None
    if cond and cond in _CONDITION_TO_CONCERN:
        return _CONDITION_TO_CONCERN[cond]
    return None


def get_concern(key: str) -> Optional[Dict[str, Any]]:
    return CALLER_CONCERNS.get(key)


def required_concern_keys() -> List[str]:
    return list(REQUIRED_CONCERN_KEYS)


# ===========================================================================
# Prompt block renderer — LEAN (red flags + must-not-say + top objections)
# ===========================================================================
def build_concern_handling_block() -> str:
    """
    Render the compact static prompt section wired into susie_system_prompt.py.

    LEAN by design: red-flag safety net + must-not-say reinforcement + the
    highest-value objection scripts + a short answer-style steer. It deliberately
    does NOT dump the 37-category table, restate prices/ages, or duplicate the
    existing TREATMENT_OVERRIDE block. Pure string build — never raises.
    """
    red_flags = "\n".join(f"  - {r}" for r in RED_FLAGS)
    never = "\n".join(f"  - {s}" for s in SAFETY_BOUNDARIES)
    objections = "\n".join(
        f"  - Caller: \"{OBJECTION_PLAYBOOK[k]['trigger']}\"\n"
        f"    You: {OBJECTION_PLAYBOOK[k]['script']}"
        for k in BLOCK_OBJECTION_KEYS if k in OBJECTION_PLAYBOOK
    )
    principles = " ".join(ANSWER_STYLE["principles"][:4])

    return (
        "PHYSIO CALLER HANDLING\n"
        "Before answering a concern, silently work out: the caller's concern, "
        "what they're really asking (information, suitability, price, booking, "
        "logistics, reassurance, or urgent help), the clinical risk, and the "
        "conversion risk — then answer like a knowledgeable private-clinic "
        "receptionist, not with generic sympathy followed by a booking ask. "
        f"{principles}\n\n"
        "NEVER (safety boundaries):\n"
        f"{never}\n\n"
        "RED-FLAG SAFETY NET — only when the caller actually describes these "
        "(never for routine aches or niggles):\n"
        f"{red_flags}\n"
        f"{RED_FLAG_ACTION}\n\n"
        "NAMED TREATMENTS (shockwave, laser, acupuncture, dry needling, massage): "
        "follow the existing TREATMENT-OVERRIDE rule — recommend an assessment "
        "first; do not restate it here and never auto-book standalone shockwave/"
        "laser as the default.\n\n"
        "OBJECTION HANDLING (value-led, never defensive, no made-up numbers — use "
        "the prices/policies already given above):\n"
        f"{objections}\n\n"
        "ANSWER-STYLE EXEMPLAR:\n"
        f"  GOOD — {ANSWER_STYLE['exemplar_good']}\n"
        f"  BAD — {ANSWER_STYLE['exemplar_bad']}"
    )
