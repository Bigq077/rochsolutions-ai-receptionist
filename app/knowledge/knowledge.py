# app/tools/knowledge.py
"""
Theorem Health & Wellness knowledge base.
Contains all clinic information, services, conditions, and policies.
"""

from typing import Dict, List, Optional, Any
import re


# ============================================================================
# IMPROVEMENT 1 & 2: LOCATION-SPECIFIC INFORMATION (CORRECT HOURS)
# ============================================================================

CLINIC_INFO = {
    "name": "Theorem Health & Wellness",
    "tagline": "Just as a mathematical theorem combines principles for a proven solution, Theorem Health & Wellness integrates health and wellness interventions into a holistic path toward your complete well-being.",
    "contact": {
        "phone": "07870 166861",
        "email": "info@theoremhealth.co.uk",
        "website": "www.theoremhealth.co.uk",
    },
}

LOCATIONS = {
    "alcester": {
        "name": "Alcester Clinic",
        "address": "The Greig Sports Center, Kinwarton Road, Alcester, B49 6AD",
        "postcode": "B49 6AD",
        "phone": "07870 166861",
        "hours": {
            "monday":    "8:30am - 9:00pm",
            "tuesday":   "8:30am - 9:00pm",
            "wednesday": "8:30am - 9:00pm",
            "thursday":  "8:30am - 9:00pm",
            "friday":    "8:30am - 9:00pm",
            "saturday":  "Closed",
            "sunday":    "Closed",
        },
        "hours_summary": (
            "The Alcester clinic is open Monday to Friday, "
            "8:30 in the morning until 9 at night. "
            "We're closed on weekends."
        ),
        "parking": "Parking is available at the Greig Sports Center.",
        "directions": (
            "We're located at the Greig Sports Center on Kinwarton Road in Alcester. "
            "There's parking on-site."
        ),
    },
    "redditch": {
        "name": "Redditch Clinic",
        "address": "51 Bromsgrove Road, Redditch, B97 4RH",
        "postcode": "B97 4RH",
        "phone": "07870 166861",
        "hours": {
            "monday":    "9:00am - 5:00pm",
            "tuesday":   "9:00am - 5:00pm",
            "wednesday": "9:00am - 7:00pm",
            "thursday":  "9:00am - 7:00pm",
            "friday":    "9:00am - 5:00pm",
            "saturday":  "9:00am - 5:00pm",
            "sunday":    "Closed",
        },
        "hours_summary": (
            "The Redditch clinic is open Monday to Saturday. "
            "Monday, Tuesday, and Friday — 9am to 5pm. "
            "Wednesday and Thursday — 9am to 7pm. "
            "Saturday — 9am to 5pm. "
            "We're closed on Sundays."
        ),
        "parking": "Street parking is available on Bromsgrove Road.",
        "directions": (
            "We're at 51 Bromsgrove Road in Redditch. "
            "There's street parking nearby."
        ),
    },
}


# ============================================================================
# PRACTITIONERS
# ============================================================================

PRACTITIONERS = {
    "mark": {
        "name": "Mark",
        "full_name": "Mark",
        "credentials": "MSc, BSc (Hons) HCPC, MCSP, AACP, MACS",
        "role": "Founder, Physiotherapist & Prescriber, injection therapy",
        "specialties": [
            "Musculoskeletal physiotherapy",
            "Sports injuries",
            "Chronic pain management",
            "Holistic assessment",
        ],
        "available": {
            "alcester": ["monday", "tuesday", "wednesday", "friday"],
            "redditch": ["thursday"],
        },
    },
    "leanne": {
        "name": "Leanne",
        "full_name": "Leanne",
        "role": "Physiotherapist",
        "specialties": [
            "Musculoskeletal physiotherapy",
            "Women's health",
            "Post-natal rehabilitation",
        ],
        "available": {
            "alcester": ["thursday"],   # Thursday evenings only
            "redditch": [],             # Leanne is not available at Redditch
        },
    },
    # Friday/Saturday coverage - TBC by client
}


# ============================================================================
# PRICING
# ============================================================================

PRICING = {
    "physiotherapy_assessment": {
        "price": "£85",
        "duration": "50 minutes",
        "description": "Comprehensive initial assessment with treatment",
    },
    "physiotherapy_followup": {
        "price": "£85",
        "duration": "50 minutes",
        "description": "Follow-up physiotherapy session",
    },
    "rehabilitation": {
        "price": "£65",
        "duration": "50 minutes",
        "description": "Remedial rehabilitation session",
        "requires": "Initial physiotherapy assessment",
    },
    "psychotherapy": {
        "price": "£85",
        "duration": "50 minutes",
        "description": "Psychotherapy session",
    },
    "prescribing": {
        "price": "£12.50",
        "duration": "Part of physiotherapy session",
        "description": "Prescribing consultation fee",
    },
    "shockwave_therapy": {
        "price": "£45 surcharge",
        "duration": "5-10 minutes as part of physio session",
        "description": "Shockwave therapy surcharge when used",
    },
    "laser_therapy": {
        "price": "£45 surcharge",
        "duration": "5-10 minutes as part of physio session",
        "description": "Class IV laser therapy surcharge when used",
    },
    "acupuncture": {
        "price": "Included",
        "duration": "Part of physiotherapy session",
        "description": "Acupuncture included when used in physio session",
    },
}

PRICING_SUMMARY = (
    "Physiotherapy sessions are £85 for 50 minutes. "
    "Rehabilitation sessions are £65. "
    "Psychotherapy is £85, available at Alcester only. "
    "If we use specialist equipment — shockwave or laser therapy — "
    "there's an additional £45 for that. "
    "Prescribing consultations are £12.50. "
    "Acupuncture is included as part of your physiotherapy session when we use it."
)


# ============================================================================
# POLICIES
# ============================================================================

POLICIES = {
    "cancellation": {
        "policy": "24 hours",
        "description": (
            "We have a 24-hour cancellation policy. "
            "Please let us know at least 24 hours before your appointment "
            "if you need to cancel or reschedule. "
            "Otherwise the full session fee applies. "
            "You can reach us on 07870 166861."
        ),
    },
    "late_arrival": {
        "policy": "Please arrive on time",
        "description": (
            "If you're running late, please call us on 07870 166861. "
            "We'll do our best to accommodate you, but we may need to shorten "
            "your session to stay on schedule for other patients."
        ),
    },
    "what_to_bring": {
        "description": (
            "Just wear loose or comfortable clothing if you can. "
            "If you have any scans, reports, or previous medical notes, "
            "please bring those along. "
            "But honestly, don't worry if you haven't — just come as you are."
        ),
    },
    "payment": {
        "methods": ["Card", "Cash", "Bank transfer"],
        "description": (
            "We accept card, cash, or bank transfer. "
            "Payment is due at the end of each session."
        ),
    },
    "privacy": {
        "description": (
            "Your information is treated as strictly confidential "
            "and handled in line with UK GDPR and data protection regulations. "
            "Everything discussed in your sessions stays private."
        ),
    },
}


# ============================================================================
# IMPROVEMENT 5: INSURANCE INFORMATION
# ============================================================================

INSURANCE_INFO = {
    "model": "self-pay with claim-back",
    "bupa_accepted": False,
    "explanation": (
        "We operate as a self-pay clinic, which means you pay for your sessions directly with us. "
        "We don't bill insurance companies directly. "
        "However, many of our patients are able to claim the costs back themselves "
        "through their insurance policy afterwards. "
        "We'll provide you with a detailed receipt and any documentation you need to make that claim. "
        "It's a straightforward process for most insurance policies."
    ),
    "bupa_warning": (
        "I do need to let you know — we're not able to accept Bupa. "
        "So if you're with Bupa, you would need to pay privately."
    ),
    "check_warning": (
        "I want to be upfront — while many insurers do reimburse self-pay physiotherapy, "
        "we can't guarantee your specific policy will cover it. "
        "We'd strongly recommend calling your insurer before your appointment "
        "to confirm your coverage and find out what documentation they need from us."
    ),
    "receipts": (
        "We'll provide you with a detailed receipt after each session "
        "showing the date, treatment provided, and amount paid. "
        "If your insurer needs anything additional — like a clinical report — "
        "we can arrange that too."
    ),
    "accepted_insurers": {
        # Note: All are self-pay + claim back. Bupa specifically not accepted.
        "axa": True,
        "vitality": True,
        "aviva": True,
        "wpa": True,
        "cigna": True,
        "simply_health": True,
        "health_shield": True,
        "benenden": True,
        "nuffield": True,
        "bupa": False,
    },
}


# ============================================================================
# IMPROVEMENT 3 & 6: SERVICE EXPLANATIONS (MARK'S WORDS)
# ============================================================================

SERVICES = {
    "physiotherapy_assessment": {
        "name": "Physiotherapy Assessment",
        "price": "£85",
        "duration": "50 minutes",
        "short": (
            "It's a full 50-minute assessment. "
            "We look at your physical mobility and strength, "
            "but also how you're feeling emotionally. "
            "Pain affects everything, so we take a holistic approach. "
            "By the end, we'll have a clear picture of what's going on "
            "and a treatment plan built around you."
        ),
        "detailed": (
            "So during the assessment, we start by really listening to you. "
            "We want to understand not just the physical side, "
            "but how it's affecting your life day to day. "
            "\n\n"
            "Then we'll do a physical evaluation — looking at your mobility, "
            "strength, and movement patterns. "
            "We'll identify the root cause of the issue, not just the symptoms. "
            "\n\n"
            "From there, we build a treatment plan together. "
            "That might include exercises, manual therapy, or soft tissue work. "
            "If appropriate, we might also bring in things like acupuncture, "
            "shockwave therapy, or laser therapy. "
            "\n\n"
            "It's collaborative. You're involved in every step. "
            "Sessions are 50 minutes and cost £85."
        ),
        "good_for": [
            "First-time patients",
            "New injuries or pain",
            "Longstanding issues",
            "Post-surgery recovery",
            "Anyone unsure where to start",
        ],
    },

    "physiotherapy_followup": {
        "name": "Physiotherapy Follow-up",
        "price": "£85",
        "duration": "50 minutes",
        "short": (
            "Follow-up sessions are where the real progress happens. "
            "We check in on how you're doing, "
            "adjust the plan if needed, "
            "and keep building on what's working. "
            "It's 50 minutes, fully focused on your recovery."
        ),
        "detailed": (
            "Every follow-up starts with a check-in. "
            "How have you been since the last session? "
            "What's improved? What hasn't? "
            "\n\n"
            "We'll reassess your mobility and strength, "
            "then adjust your treatment plan based on where you are now. "
            "No two sessions are the same — it evolves with you. "
            "\n\n"
            "We might refine your exercises, do more manual therapy, "
            "or introduce a new technique if it would help. "
            "\n\n"
            "If at any point we feel you'd benefit from seeing a specialist — "
            "like a surgeon or a GP — we'll help arrange that referral. "
            "If you need imaging like an MRI or ultrasound, "
            "we'll make sure you get the right support to access that too. "
            "\n\n"
            "Sessions are 50 minutes and cost £85."
        ),
        "good_for": [
            "Existing patients continuing treatment",
            "Progress check-ins",
            "Ongoing recovery",
            "Fine-tuning treatment plans",
        ],
    },

    "acupuncture": {
        "name": "Acupuncture",
        "price": "Included in physiotherapy session",
        "duration": "Part of physiotherapy session",
        "short": (
            "Acupuncture uses very fine needles placed at specific points on the body. "
            "The idea is to restore the natural flow of energy — known as Qi — "
            "which promotes healing, reduces pain, and improves overall well-being."
        ),
        "detailed": (
            "It sounds more daunting than it is. "
            "The needles are incredibly fine — much finer than injection needles — "
            "and most people barely feel them going in. "
            "\n\n"
            "The needles are placed at specific points to stimulate your body's natural healing response. "
            "It improves circulation, releases muscle tension, and helps the body rebalance itself. "
            "\n\n"
            "A lot of people find it deeply relaxing — some even drift off during treatment. "
            "\n\n"
            "We often use acupuncture as part of a physiotherapy session, "
            "combining it with other treatments for better results. "
            "It's included within your session when we use it — no extra charge."
        ),
        "good_for": [
            "Pain relief",
            "Muscle tension",
            "Headaches and migraines",
            "Back and neck pain",
            "Stress and anxiety",
            "Sports injuries",
        ],
    },

    "shockwave_therapy": {
        "name": "Shockwave Therapy",
        "price": "£45 surcharge when used",
        "duration": "5-10 minutes as part of physiotherapy session",
        "short": (
            "Shockwave therapy uses targeted sound waves to stimulate healing in the tissue. "
            "It's particularly effective for chronic tendon issues "
            "where the body needs a push to restart the healing process."
        ),
        "detailed": (
            "Think of it as kick-starting the healing process. "
            "\n\n"
            "The shockwave device sends acoustic waves deep into the affected tissue. "
            "This increases blood flow, breaks down scar tissue and calcification, "
            "and triggers the body to produce new, healthy cells. "
            "\n\n"
            "It's especially good for stubborn tendon problems — "
            "like Achilles tendonitis, plantar fasciitis, or tennis elbow — "
            "where other treatments haven't quite worked. "
            "\n\n"
            "The treatment can feel a little intense on the sore area, "
            "but it's quick — usually around five to ten minutes. "
            "Most people see real improvement within a few sessions. "
            "\n\n"
            "When we use it during your physio session, "
            "there's a £45 surcharge for the specialist equipment."
        ),
        "good_for": [
            "Chronic tendon problems",
            "Tennis elbow",
            "Golfer's elbow",
            "Plantar fasciitis",
            "Achilles tendonitis",
            "Shoulder tendon issues",
            "Persistent pain that hasn't responded to other treatments",
        ],
    },

    "laser_therapy": {
        "name": "Class IV Laser Therapy",
        "price": "£45 surcharge when used",
        "duration": "5-10 minutes as part of physiotherapy session",
        "short": (
            "Laser therapy uses powerful laser light to reduce pain, "
            "bring down inflammation, and speed up tissue repair. "
            "It reaches deep into the body and is completely painless."
        ),
        "detailed": (
            "Class IV laser is one of the more advanced tools we have. "
            "\n\n"
            "The laser light penetrates deep into tissue — "
            "much deeper than standard laser treatments. "
            "At that level, it reduces inflammation, increases circulation, "
            "and accelerates the body's own repair process. "
            "\n\n"
            "The treatment itself is very pleasant actually. "
            "The laser head moves slowly over the area and it just feels warm and relaxing. "
            "No pain at all. "
            "\n\n"
            "It's great for acute injuries where you want to heal faster, "
            "and for inflammatory conditions like arthritis. "
            "\n\n"
            "Like shockwave, there's a £45 surcharge when we use it during your session."
        ),
        "good_for": [
            "Inflammation",
            "Acute injuries",
            "Arthritis",
            "Speeding up recovery",
            "Deep tissue pain",
            "Post-surgical healing",
        ],
    },

    "rehabilitation": {
        "name": "Remedial Rehabilitation",
        "price": "£65",
        "duration": "50 minutes",
        "short": (
            "Rehabilitation is about rebuilding — strength, movement, and confidence. "
            "Our rehab instructors work alongside the physio team "
            "to create a programme that gets you back to where you want to be."
        ),
        "detailed": (
            "Physiotherapy gets you on the right track. "
            "Rehabilitation takes you the rest of the way. "
            "\n\n"
            "Our rehabilitation instructors work closely with the physiotherapy team, "
            "so your programme is always coordinated and progressive. "
            "You might see both your physiotherapist and your rehab instructor as part of your care — "
            "that's by design. "
            "\n\n"
            "Sessions focus on rebuilding strength, restoring movement quality, "
            "and building the confidence to get back to normal activity. "
            "\n\n"
            "This is especially important after surgery or serious injury, "
            "where you need structured, expert guidance to rebuild safely. "
            "\n\n"
            "Rehab sessions are 50 minutes and cost £65. "
            "We do ask that you have an initial physio assessment first, "
            "so we can create the right programme for where you are now."
        ),
        "good_for": [
            "Post-surgery recovery",
            "Rebuilding after injury",
            "Returning to sport",
            "Strength and conditioning",
            "Long-term recovery",
        ],
        "requires": "Initial physiotherapy assessment",
    },

    "psychotherapy": {
        "name": "Psychotherapy",
        "price": "£85",
        "duration": "50 minutes",
        "short": (
            "Psychotherapy gives you a safe, confidential space "
            "to explore your thoughts and emotions. "
            "Using techniques like hypnotherapy and spiritual healing, "
            "we help reduce stress, build coping strategies, "
            "and support your overall mental well-being."
        ),
        "detailed": (
            "Sometimes the most important thing is just having a safe space to talk. "
            "\n\n"
            "Our psychotherapy sessions are confidential and completely non-judgmental. "
            "We work with you at your own pace. "
            "\n\n"
            "The approach is flexible. "
            "We might use talking therapy, hypnotherapy, or spiritual healing techniques — "
            "whatever feels right for you and your situation. "
            "\n\n"
            "We work with things like stress, anxiety, depression, trauma, and life transitions. "
            "But also the emotional side of physical pain — "
            "because the two are often more connected than people realise. "
            "\n\n"
            "Sessions are 50 minutes and cost £85."
        ),
        "good_for": [
            "Stress and anxiety",
            "Depression",
            "Trauma",
            "Life changes and transitions",
            "Emotional well-being",
            "The mental impact of chronic pain",
        ],
    },

    "prescribing": {
        "name": "Prescribing",
        "price": "£12.50 consultation fee",
        "duration": "Part of physiotherapy session",
        "short": (
            "Our physiotherapists are qualified independent prescribers. "
            "If medication would help — like pain relief — "
            "they can prescribe that directly, without you needing a separate GP appointment."
        ),
        "detailed": (
            "It's one of those things that makes a real difference in practice. "
            "\n\n"
            "If during your session we feel that medication would support your recovery — "
            "whether that's pain relief, anti-inflammatories, or something else — "
            "our physiotherapists can prescribe that directly. "
            "\n\n"
            "Your treatment plan is always designed with both safety and effectiveness in mind. "
            "We only prescribe when it genuinely makes sense for your situation. "
            "\n\n"
            "The prescribing consultation is £12.50, "
            "and it would usually happen as part of your physio session. "
            "You'd then take the prescription to any pharmacy as normal."
        ),
        "good_for": [
            "Pain management",
            "Anti-inflammatory support",
            "Avoiding a separate GP visit",
            "Comprehensive care in one place",
        ],
    },
}


# ============================================================================
# IMPROVEMENT 4: CONDITIONS → TREATMENTS (50+ CONDITIONS)
# ============================================================================

CONDITIONS = {
    # --- BACK PAIN ---
    "lower_back_pain": {
        "keywords": ["lower back", "lumbar", "lumbago", "base of spine", "lower spine"],
        "treatments": ["physiotherapy", "acupuncture", "shockwave_therapy", "laser_therapy"],
        "primary": "physiotherapy",
        "recommendation": (
            "Lower back pain is one of the most common issues we see, and we have excellent results treating it. "
            "I'd recommend starting with a physiotherapy assessment to identify what's causing the pain — "
            "whether it's muscular, postural, or something else. "
            "Acupuncture can be very effective for immediate pain relief. "
            "If you've had the pain for a while, shockwave therapy might help break the cycle. "
            "Laser therapy is also great for reducing inflammation."
        ),
    },
    "upper_back_pain": {
        "keywords": ["upper back", "thoracic", "between shoulder blades", "middle back"],
        "treatments": ["physiotherapy", "acupuncture"],
        "primary": "physiotherapy",
        "recommendation": (
            "Upper back pain often comes from posture, tension, or how you're moving. "
            "Physiotherapy can assess the cause and work on loosening tight muscles, "
            "improving your posture, and strengthening the right areas. "
            "Acupuncture is excellent for releasing muscle tension in the upper back."
        ),
    },
    "sciatica": {
        "keywords": ["sciatica", "sciatic", "shooting down leg", "nerve pain leg", "down my leg"],
        "treatments": ["physiotherapy", "acupuncture", "laser_therapy"],
        "primary": "physiotherapy",
        "recommendation": (
            "Sciatica can be really painful and debilitating. The good news is it usually responds "
            "well to physiotherapy. We'll assess what's irritating the nerve and create a plan to "
            "reduce the pressure and pain. Acupuncture can help with nerve pain, and laser therapy "
            "can reduce inflammation around the nerve."
        ),
    },
    "disc_problems": {
        "keywords": ["disc", "disk", "herniated", "bulging", "slipped disc", "prolapsed"],
        "treatments": ["physiotherapy", "laser_therapy", "rehabilitation"],
        "primary": "physiotherapy",
        "recommendation": (
            "Disc issues need careful assessment and treatment. Physiotherapy can help reduce "
            "pressure on the disc and strengthen the supporting muscles. Laser therapy can help reduce "
            "inflammation, and our rehabilitation programmes are excellent for long-term recovery."
        ),
    },

    # --- NECK & SHOULDER ---
    "neck_pain": {
        "keywords": ["neck", "cervical", "stiff neck", "neck ache", "can't turn head"],
        "treatments": ["physiotherapy", "acupuncture", "laser_therapy"],
        "primary": "physiotherapy",
        "recommendation": (
            "Neck pain can really affect your day-to-day life. Physiotherapy can assess whether "
            "it's coming from your posture, muscles, joints, or a combination. "
            "Acupuncture is particularly good for neck muscle tension. "
            "Laser therapy can reduce inflammation in the neck joints."
        ),
    },
    "whiplash": {
        "keywords": ["whiplash", "car accident", "rear-ended", "collision injury"],
        "treatments": ["physiotherapy", "laser_therapy", "acupuncture"],
        "primary": "physiotherapy",
        "recommendation": (
            "Whiplash injuries need proper treatment to heal correctly. "
            "Physiotherapy can help restore normal movement, reduce pain, and rebuild strength safely. "
            "Laser therapy is excellent for reducing inflammation from whiplash injuries. "
            "Acupuncture can help manage the pain during recovery."
        ),
    },
    "shoulder_pain": {
        "keywords": ["shoulder", "rotator", "shoulder blade", "can't lift arm"],
        "treatments": ["physiotherapy", "shockwave_therapy", "laser_therapy", "acupuncture"],
        "primary": "physiotherapy",
        "recommendation": (
            "Shoulder pain can have various causes — from rotator cuff issues to frozen shoulder. "
            "Physiotherapy will assess exactly what's going on. "
            "If you have a tendon problem, shockwave therapy is very effective. "
            "Laser therapy helps reduce inflammation. "
            "Acupuncture can provide excellent pain relief."
        ),
    },
    "frozen_shoulder": {
        "keywords": ["frozen shoulder", "adhesive capsulitis", "can't move shoulder", "shoulder stuck"],
        "treatments": ["physiotherapy", "acupuncture", "laser_therapy"],
        "primary": "physiotherapy",
        "recommendation": (
            "Frozen shoulder can be frustrating, but with the right treatment we can help restore movement "
            "and reduce pain. Physiotherapy uses specific techniques to gradually improve your range of motion. "
            "Acupuncture can help manage the pain. Laser therapy can reduce inflammation and speed up healing."
        ),
    },
    "rotator_cuff": {
        "keywords": ["rotator cuff", "supraspinatus", "shoulder tendon", "shoulder weakness"],
        "treatments": ["physiotherapy", "shockwave_therapy", "laser_therapy", "rehabilitation"],
        "primary": "physiotherapy",
        "recommendation": (
            "Rotator cuff injuries need expert care to heal properly. Physiotherapy can assess the extent "
            "and create a progressive strengthening program. Shockwave therapy is excellent for rotator cuff "
            "tendon issues. Our rehabilitation programmes can help you build back full strength."
        ),
    },

    # --- ARM, ELBOW, WRIST, HAND ---
    "tennis_elbow": {
        "keywords": ["tennis elbow", "lateral epicondylitis", "outside elbow", "elbow tendon"],
        "treatments": ["physiotherapy", "shockwave_therapy", "laser_therapy"],
        "primary": "physiotherapy",
        "recommendation": (
            "Tennis elbow is very common — and we see it a lot! "
            "Physiotherapy can help identify what's causing it and strengthen the area properly. "
            "Shockwave therapy has an excellent success rate for tennis elbow. "
            "Laser therapy also works well for reducing inflammation."
        ),
    },
    "golfers_elbow": {
        "keywords": ["golfer's elbow", "golfers elbow", "medial epicondylitis", "inside elbow"],
        "treatments": ["physiotherapy", "shockwave_therapy", "laser_therapy"],
        "primary": "physiotherapy",
        "recommendation": (
            "Golfer's elbow affects the inside of the elbow and can be stubborn without proper treatment. "
            "Physiotherapy will address the cause and work on strengthening. "
            "Shockwave therapy is particularly effective for this type of tendon problem."
        ),
    },
    "wrist_pain": {
        "keywords": ["wrist", "wrist pain", "carpal", "weak wrist"],
        "treatments": ["physiotherapy", "laser_therapy", "acupuncture"],
        "primary": "physiotherapy",
        "recommendation": (
            "Wrist pain can really interfere with daily activities. Physiotherapy can assess whether "
            "it's coming from the wrist joint, tendons, or even referred from your neck or arm. "
            "Laser therapy is effective for wrist inflammation. Acupuncture can help with pain management."
        ),
    },
    "carpal_tunnel": {
        "keywords": ["carpal tunnel", "tingling fingers", "numbness hand", "median nerve"],
        "treatments": ["physiotherapy", "laser_therapy", "acupuncture"],
        "primary": "physiotherapy",
        "recommendation": (
            "Carpal tunnel syndrome causes tingling and numbness from nerve compression in the wrist. "
            "Physiotherapy can help reduce the pressure on the nerve. "
            "Laser therapy can reduce inflammation around the nerve. "
            "In severe cases, we can discuss whether you need a referral."
        ),
    },

    # --- HIP, KNEE, LEG ---
    "hip_pain": {
        "keywords": ["hip", "hip pain", "groin", "hip joint", "hip flexor"],
        "treatments": ["physiotherapy", "laser_therapy", "rehabilitation", "acupuncture"],
        "primary": "physiotherapy",
        "recommendation": (
            "Hip pain can come from the joint itself, surrounding muscles, or even your back. "
            "Physiotherapy will assess the true cause and work on improving your hip movement and strength. "
            "Laser therapy can help with inflammation. Our rehabilitation programmes are excellent for building hip strength."
        ),
    },
    "knee_pain": {
        "keywords": ["knee", "kneecap", "patella", "knee joint", "can't bend knee"],
        "treatments": ["physiotherapy", "laser_therapy", "rehabilitation", "acupuncture"],
        "primary": "physiotherapy",
        "recommendation": (
            "Knee pain is very common and can be caused by various things. "
            "Physiotherapy can identify the problem and work on strengthening the muscles that support your knee. "
            "Laser therapy is excellent for reducing knee inflammation. "
            "Our rehabilitation programmes can help build strong, stable knees."
        ),
    },
    "meniscus_injury": {
        "keywords": ["meniscus", "cartilage knee", "knee twist", "torn cartilage"],
        "treatments": ["physiotherapy", "laser_therapy", "rehabilitation"],
        "primary": "physiotherapy",
        "recommendation": (
            "Meniscus injuries can often be managed very well with physiotherapy, sometimes avoiding "
            "the need for surgery. We'll assess the injury and work on reducing pain and restoring function. "
            "Our rehabilitation programmes will help rebuild knee strength and stability."
        ),
    },
    "runners_knee": {
        "keywords": ["runner's knee", "runners knee", "patellofemoral", "front knee pain"],
        "treatments": ["physiotherapy", "laser_therapy", "rehabilitation"],
        "primary": "physiotherapy",
        "recommendation": (
            "Runner's knee is very common in runners but also people who do squats or stairs a lot. "
            "Physiotherapy can identify the biomechanical issues causing it and work on correcting them. "
            "Our rehabilitation programmes will strengthen the right muscles to prevent it coming back."
        ),
    },
    "it_band_syndrome": {
        "keywords": ["it band", "ITB", "iliotibial band", "outside knee pain", "hip to knee pain"],
        "treatments": ["physiotherapy", "laser_therapy", "rehabilitation"],
        "primary": "physiotherapy",
        "recommendation": (
            "IT band syndrome is common in runners and cyclists. Physiotherapy can work on releasing "
            "the tight band, improving your movement patterns, and strengthening supporting muscles. "
            "Our rehabilitation programmes help prevent it returning."
        ),
    },
    "ankle_pain": {
        "keywords": ["ankle", "ankle pain", "sprained ankle", "twisted ankle", "rolled ankle"],
        "treatments": ["physiotherapy", "laser_therapy", "rehabilitation"],
        "primary": "physiotherapy",
        "recommendation": (
            "Ankle injuries need proper treatment to heal correctly and prevent chronic instability. "
            "Physiotherapy will assess the damage, reduce swelling and pain, and work on restoring "
            "full movement and strength. Our rehabilitation programmes help build a strong, stable ankle."
        ),
    },

    # --- FOOT ---
    "plantar_fasciitis": {
        "keywords": ["plantar fasciitis", "heel pain", "foot arch pain", "pain bottom foot"],
        "treatments": ["physiotherapy", "shockwave_therapy", "laser_therapy"],
        "primary": "physiotherapy",
        "recommendation": (
            "Plantar fasciitis can be really painful, especially first thing in the morning. "
            "Physiotherapy can help with stretching, strengthening, and advice on footwear. "
            "Shockwave therapy has an excellent success rate for plantar fasciitis — it's one of the "
            "best treatments available."
        ),
    },
    "achilles_tendonitis": {
        "keywords": ["achilles", "achilles tendon", "back ankle pain", "heel cord"],
        "treatments": ["physiotherapy", "shockwave_therapy", "laser_therapy", "rehabilitation"],
        "primary": "physiotherapy",
        "recommendation": (
            "Achilles tendonitis needs careful management to heal properly without becoming chronic. "
            "Physiotherapy can work on loading the tendon correctly to stimulate healing. "
            "Shockwave therapy is particularly effective for achilles problems. "
            "Our rehabilitation programmes help you return to running or sport safely."
        ),
    },
    "foot_pain": {
        "keywords": ["foot pain", "sore feet", "feet hurt", "foot ache"],
        "treatments": ["physiotherapy", "laser_therapy", "acupuncture"],
        "primary": "physiotherapy",
        "recommendation": (
            "Foot pain can have many causes. Physiotherapy can assess what's causing your foot pain. "
            "Laser therapy works well for foot inflammation. Acupuncture can provide pain relief."
        ),
    },

    # --- SPORTS INJURIES ---
    "sports_injury": {
        "keywords": ["sports", "playing", "football", "rugby", "cricket", "gym injury", "training"],
        "treatments": ["physiotherapy", "rehabilitation", "shockwave_therapy", "laser_therapy"],
        "primary": "physiotherapy",
        "recommendation": (
            "Sports injuries need expert assessment and treatment to get you back playing safely. "
            "Physiotherapy will evaluate the injury and reduce pain and swelling. "
            "Our rehabilitation programmes are specifically designed to rebuild strength and get you match-fit."
        ),
    },
    "running_injury": {
        "keywords": ["running", "runner", "jogging", "marathon", "running pain"],
        "treatments": ["physiotherapy", "rehabilitation", "laser_therapy", "shockwave_therapy"],
        "primary": "physiotherapy",
        "recommendation": (
            "Running injuries often come from biomechanical issues or overtraining. "
            "Physiotherapy can assess your running technique, identify weaknesses, and create "
            "a progressive return-to-running plan."
        ),
    },
    "muscle_strain": {
        "keywords": ["pulled muscle", "muscle strain", "torn muscle", "muscle tear"],
        "treatments": ["physiotherapy", "laser_therapy", "rehabilitation"],
        "primary": "physiotherapy",
        "recommendation": (
            "Muscle strains need proper treatment to heal correctly and prevent re-injury. "
            "Physiotherapy can assess the extent and create a progressive recovery plan. "
            "Laser therapy can speed up the healing process."
        ),
    },

    # --- POST-SURGICAL ---
    "post_surgery": {
        "keywords": ["surgery", "operation", "post-op", "after surgery", "had surgery"],
        "treatments": ["physiotherapy", "rehabilitation", "laser_therapy"],
        "primary": "physiotherapy",
        "recommendation": (
            "Post-surgical rehabilitation is crucial for a successful recovery. "
            "Physiotherapy will work with you to safely restore movement and rebuild strength. "
            "Our rehabilitation programmes are designed specifically for post-surgical recovery."
        ),
    },
    "joint_replacement": {
        "keywords": ["joint replacement", "hip replacement", "knee replacement", "new hip", "new knee"],
        "treatments": ["physiotherapy", "rehabilitation"],
        "primary": "physiotherapy",
        "recommendation": (
            "Recovery from joint replacement surgery requires expert physiotherapy and rehabilitation. "
            "We'll work with you through all the stages — from early post-op through to full function."
        ),
    },
    "acl_injury": {
        "keywords": ["ACL", "anterior cruciate", "knee ligament", "ACL tear", "ACL reconstruction"],
        "treatments": ["physiotherapy", "rehabilitation", "laser_therapy"],
        "primary": "physiotherapy",
        "recommendation": (
            "ACL injuries — whether treated surgically or conservatively — need expert rehabilitation. "
            "Physiotherapy will work on reducing swelling, restoring range of motion, and rebuilding strength."
        ),
    },

    # --- CHRONIC CONDITIONS ---
    "chronic_pain": {
        "keywords": ["chronic", "persistent", "months", "years", "constant pain", "ongoing pain"],
        "treatments": ["physiotherapy", "acupuncture", "shockwave_therapy", "psychotherapy"],
        "primary": "physiotherapy",
        "recommendation": (
            "Chronic pain can be really challenging and often needs a holistic approach. "
            "Physiotherapy can help address the physical aspects and teach pain management strategies. "
            "Acupuncture is excellent for ongoing pain relief. "
            "Sometimes chronic pain has psychological components — our psychotherapy service can support you with that too."
        ),
    },
    "arthritis": {
        "keywords": ["arthritis", "osteoarthritis", "arthritic", "joint wear", "rheumatoid"],
        "treatments": ["physiotherapy", "laser_therapy", "acupuncture", "rehabilitation"],
        "primary": "physiotherapy",
        "recommendation": (
            "While we can't cure arthritis, we can definitely help you manage it better. "
            "Physiotherapy can work on maintaining joint mobility and strengthening supporting muscles. "
            "Laser therapy is excellent for reducing arthritis inflammation."
        ),
    },
    "fibromyalgia": {
        "keywords": ["fibromyalgia", "chronic fatigue", "widespread pain", "tender points"],
        "treatments": ["physiotherapy", "acupuncture", "psychotherapy"],
        "primary": "physiotherapy",
        "recommendation": (
            "Fibromyalgia requires a gentle, understanding approach. Physiotherapy can help with "
            "graded exercise programmes and pacing strategies. "
            "Our psychotherapy service can help with the emotional impact of living with chronic pain."
        ),
    },

    # --- WORK-RELATED ---
    "rsi": {
        "keywords": ["RSI", "repetitive strain", "overuse", "repetitive", "typing pain"],
        "treatments": ["physiotherapy", "laser_therapy", "acupuncture"],
        "primary": "physiotherapy",
        "recommendation": (
            "RSI needs treatment but also changes to prevent it recurring. "
            "Physiotherapy can treat the current injury and give you advice on ergonomics and work setup."
        ),
    },
    "desk_pain": {
        "keywords": ["desk", "computer", "office", "sitting", "desk job", "screen work"],
        "treatments": ["physiotherapy", "acupuncture"],
        "primary": "physiotherapy",
        "recommendation": (
            "Pain from desk work is incredibly common. "
            "Physiotherapy can assess your posture, give you exercises to counteract sitting, "
            "and advise on your desk ergonomics."
        ),
    },
    "posture_problems": {
        "keywords": ["posture", "slouching", "hunched", "rounded shoulders", "bad posture"],
        "treatments": ["physiotherapy", "acupuncture", "rehabilitation"],
        "primary": "physiotherapy",
        "recommendation": (
            "Poor posture can lead to all sorts of pain. Physiotherapy can assess your posture, "
            "work on releasing tight areas, and strengthen the muscles that support good posture."
        ),
    },

    # --- HEADACHES & JAW ---
    "headaches": {
        "keywords": ["headache", "headaches", "head pain", "tension headache"],
        "treatments": ["physiotherapy", "acupuncture"],
        "primary": "physiotherapy",
        "recommendation": (
            "Many headaches actually come from neck tension or jaw problems. "
            "Physiotherapy can assess whether your headaches are coming from your neck, jaw, or posture. "
            "Acupuncture is particularly effective for headache relief."
        ),
    },
    "migraines": {
        "keywords": ["migraine", "migraines", "severe headache", "visual headache"],
        "treatments": ["physiotherapy", "acupuncture"],
        "primary": "physiotherapy",
        "recommendation": (
            "While migraines can have many triggers, neck problems and muscle tension can contribute. "
            "Acupuncture has good evidence for reducing migraine frequency and severity."
        ),
    },
    "tmj_jaw_pain": {
        "keywords": ["jaw", "TMJ", "temporomandibular", "jaw clicking", "jaw pain", "can't open mouth"],
        "treatments": ["physiotherapy", "acupuncture", "laser_therapy"],
        "primary": "physiotherapy",
        "recommendation": (
            "TMJ and jaw problems can be really uncomfortable. "
            "Physiotherapy can work on the jaw joint, surrounding muscles, and neck. "
            "Acupuncture is excellent for jaw tension."
        ),
    },

    # --- PREGNANCY & POST-NATAL ---
    "pregnancy_pain": {
        "keywords": ["pregnant", "pregnancy", "expecting", "baby", "maternity"],
        "treatments": ["physiotherapy", "acupuncture"],
        "primary": "physiotherapy",
        "recommendation": (
            "Pregnancy can put a lot of strain on your body, especially your back and pelvis. "
            "Physiotherapy can help manage pregnancy-related pain and prepare your body for birth. "
            "Acupuncture is safe during pregnancy and can help with pain relief."
        ),
    },
    "post_natal": {
        "keywords": ["post-natal", "postnatal", "after birth", "new mum", "pelvic floor"],
        "treatments": ["physiotherapy", "rehabilitation"],
        "primary": "physiotherapy",
        "recommendation": (
            "Post-natal recovery is so important but often overlooked. Physiotherapy can help with "
            "pelvic floor recovery, diastasis recti, and any pain from birth."
        ),
    },

    # --- BALANCE & ELDERLY ---
    "balance_problems": {
        "keywords": ["balance", "dizzy", "unsteady", "vertigo", "falls", "falling"],
        "treatments": ["physiotherapy", "rehabilitation"],
        "primary": "physiotherapy",
        "recommendation": (
            "Balance problems can be concerning and increase fall risk. Physiotherapy can assess your "
            "balance, work on strengthening and coordination. "
            "Our rehabilitation programmes include specific balance training."
        ),
    },

    # --- MENTAL HEALTH ---
    "stress_anxiety": {
        "keywords": ["stress", "anxiety", "anxious", "worried", "overwhelmed", "panic"],
        "treatments": ["psychotherapy", "acupuncture"],
        "primary": "psychotherapy",
        "recommendation": (
            "I'm glad you're reaching out about this. Our psychotherapy service provides a safe, "
            "confidential space to explore what you're experiencing. "
            "Interestingly, acupuncture can also be very calming and help with anxiety symptoms."
        ),
    },
    "depression": {
        "keywords": ["depression", "depressed", "low mood", "can't cope", "hopeless"],
        "treatments": ["psychotherapy"],
        "primary": "psychotherapy",
        "recommendation": (
            "Thank you for sharing this with me. Our psychotherapy service offers a supportive, "
            "non-judgmental environment to work through what you're experiencing."
        ),
    },
    "sleep_problems": {
        "keywords": ["sleep", "insomnia", "can't sleep", "sleep problems", "tired"],
        "treatments": ["psychotherapy", "acupuncture"],
        "primary": "psychotherapy",
        "recommendation": (
            "Sleep problems can really affect everything else. Psychotherapy can help explore what's "
            "affecting your sleep. Acupuncture can also be very helpful for sleep issues."
        ),
    },
    "trauma_ptsd": {
        "keywords": ["trauma", "PTSD", "traumatic", "flashback", "nightmares"],
        "treatments": ["psychotherapy"],
        "primary": "psychotherapy",
        "recommendation": (
            "That sounds really difficult, and you don't have to face it alone. "
            "Our psychotherapy service provides a safe space "
            "to work through traumatic experiences at your own pace."
        ),
    },

    # --- PREVENTION ---
    "injury_prevention": {
        "keywords": ["prevent injury", "prevention", "avoid injury", "prehab"],
        "treatments": ["physiotherapy", "rehabilitation"],
        "primary": "physiotherapy",
        "recommendation": (
            "It's great that you're thinking about prevention! Physiotherapy can assess any weaknesses "
            "or imbalances. Our rehabilitation programmes are excellent for building strength and resilience."
        ),
    },
    "return_to_sport": {
        "keywords": ["return to sport", "get back playing", "ready to play", "fitness test"],
        "treatments": ["physiotherapy", "rehabilitation"],
        "primary": "physiotherapy",
        "recommendation": (
            "Returning to sport after injury needs proper assessment to make sure you're ready. "
            "Physiotherapy will test your strength, movement, and sport-specific function."
        ),
    },
}


# ============================================================================
# KNOWLEDGE RETRIEVAL FUNCTION
# ============================================================================

def retrieve_knowledge(query: str, clinic: Optional[Dict] = None) -> str:
    """
    Retrieve relevant knowledge for a given query.
    Returns formatted knowledge string to be used in LLM context.
    """
    if not query:
        return ""

    query_lower = query.lower()
    results = []

    # Location-specific info (hours, address, parking)
    for loc_id, loc_data in LOCATIONS.items():
        if loc_id in query_lower or loc_data["name"].lower() in query_lower:
            results.append(f"LOCATION: {loc_data['name']}")
            results.append(f"Address: {loc_data['address']}")
            results.append(f"Hours: {loc_data['hours_summary']}")
            results.append(f"Parking: {loc_data['parking']}")
            results.append("")

    # Hours queries
    if any(kw in query_lower for kw in ["hour", "open", "close", "when", "time"]):
        if "alcester" in query_lower:
            results.append(f"ALCESTER HOURS: {LOCATIONS['alcester']['hours_summary']}")
        elif "redditch" in query_lower:
            results.append(f"REDDITCH HOURS: {LOCATIONS['redditch']['hours_summary']}")
        else:
            results.append("OPENING HOURS:")
            results.append(f"Alcester: {LOCATIONS['alcester']['hours_summary']}")
            results.append(f"Redditch: {LOCATIONS['redditch']['hours_summary']}")
        results.append("")

    # Pricing
    if any(kw in query_lower for kw in ["price", "cost", "how much", "fee", "charge"]):
        results.append("PRICING:")
        results.append(PRICING_SUMMARY)
        results.append("")

    # Insurance
    if any(kw in query_lower for kw in ["insurance", "insured", "claim", "bupa", "axa", "vitality"]):
        results.append("INSURANCE:")
        results.append(INSURANCE_INFO["explanation"])
        results.append(INSURANCE_INFO["bupa_warning"])
        results.append(INSURANCE_INFO["check_warning"])
        results.append("")

    # Services
    for service_id, service_data in SERVICES.items():
        service_keywords = [
            service_data["name"].lower(),
            service_id.replace("_", " "),
        ]
        if any(kw in query_lower for kw in service_keywords):
            results.append(f"SERVICE: {service_data['name']}")
            results.append(service_data["short"])
            results.append(f"Price: {service_data['price']}")
            results.append(f"Duration: {service_data['duration']}")
            results.append("")

    # Conditions
    for condition_id, condition_data in CONDITIONS.items():
        if any(kw in query_lower for kw in condition_data["keywords"]):
            results.append(f"CONDITION: {condition_id.replace('_', ' ').title()}")
            results.append(f"Recommended: {condition_data['primary']}")
            results.append(condition_data["recommendation"])
            results.append("")

    # Policies
    if any(kw in query_lower for kw in ["cancel", "policy", "late", "bring", "wear", "privacy"]):
        if "cancel" in query_lower:
            results.append("CANCELLATION POLICY:")
            results.append(POLICIES["cancellation"]["description"])
            results.append("")
        if any(kw in query_lower for kw in ["bring", "wear", "first visit"]):
            results.append("WHAT TO BRING:")
            results.append(POLICIES["what_to_bring"]["description"])
            results.append("")
        if "privacy" in query_lower or "data" in query_lower:
            results.append("PRIVACY:")
            results.append(POLICIES["privacy"]["description"])
            results.append("")

    # Contact info
    if any(kw in query_lower for kw in ["phone", "contact", "email", "reach", "call"]):
        results.append("CONTACT INFORMATION:")
        results.append(f"Phone: {CLINIC_INFO['contact']['phone']}")
        results.append(f"Email: {CLINIC_INFO['contact']['email']}")
        results.append("")

    return "\n".join(results) if results else ""


# ============================================================================
# CONVENIENCE FUNCTIONS
# ============================================================================

def get_location_info(location_id: str) -> Optional[Dict]:
    """Get full location information by ID."""
    return LOCATIONS.get(location_id)


def get_service_info(service_id: str) -> Optional[Dict]:
    """Get full service information by ID."""
    return SERVICES.get(service_id)


def get_condition_info(condition_id: str) -> Optional[Dict]:
    """Get full condition information by ID."""
    return CONDITIONS.get(condition_id)


def identify_condition(complaint: str) -> Optional[str]:
    """
    Identify condition from patient complaint.
    Returns condition_id or None.
    """
    complaint_lower = complaint.lower()
    best_score, best_id = 0, None

    for cond_id, cond_data in CONDITIONS.items():
        matched = [kw for kw in cond_data["keywords"] if kw in complaint_lower]
        if matched:
            score = sum(len(kw) for kw in matched)
            if score > best_score:
                best_score, best_id = score, cond_id

    return best_id


def get_pricing_summary() -> str:
    """Get the full pricing summary."""
    return PRICING_SUMMARY


def get_insurance_explanation() -> str:
    """Get the full insurance explanation."""
    return INSURANCE_INFO["explanation"]


def get_cancellation_policy() -> str:
    """Get the cancellation policy."""
    return POLICIES["cancellation"]["description"]
