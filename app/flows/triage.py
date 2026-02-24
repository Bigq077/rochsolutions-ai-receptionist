# app/flows/triage.py
from __future__ import annotations

print("✅ LOADED TRIAGE FROM:", __file__)

# ============================================================================
# IMPORTS
# ============================================================================

import os
import re
import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, Optional, Tuple

import pytz

from app.clinic_config import CLINICS
from app.storage.redis_store import redis_get_json
from app.tools.call_summary import build_call_summary
from app.tools.calendar_google import (
    create_event,
    freebusy,
    list_upcoming_events,
    patch_event_time,
)
from app.tools.knowledge import retrieve_knowledge
from app.tools.llm_router import route_and_answer
from app.tools.slots import (
    filter_free_slots,
    format_slot,
    generate_candidate_slots,
    next_7_days_window,
    parse_busy,
    pick_first_n,
)

# Service-explanation FAQ (pure function — takes text + topic)
from app.flows.faq import faq_answer as faq_answer_service

# Google Sheet handoff (optional — won't crash if not configured)
try:
    from app.tools.handoff import send_to_sheet
except Exception:
    send_to_sheet = None


# ============================================================================
# IDENTITY & GREETING CONSTANTS
# Improvement 1: Susie introduction + location question
# ============================================================================

AI_NAME    = "Susie"
CLINIC_NAME = "Theorem Health"

OPENING_GREETING = (
    f"Hi there! My name is {AI_NAME}, {CLINIC_NAME}'s AI receptionist. "
    f"Quick question before I start — "
    f"are you calling in regards to the Alcester clinic or the Redditch one?"
)

HOW_CAN_I_HELP = "How can I help you today?"

# Improvement 3: Gentle services prompt — never list-dump
SERVICES_PROMPT = (
    "We have a wide variety of treatments. "
    "Tell me what's been troubling you and I can point you in the right direction."
)


# ============================================================================
# IMPROVEMENT 2: LOCATION-SPECIFIC DATA (correct hours)
# ============================================================================

LOCATION_HOURS = {
    "alcester": (
        "The Alcester clinic is open Monday to Friday, "
        "eight thirty in the morning until nine at night. "
        "We're closed on weekends."
    ),
    "redditch": (
        "The Redditch clinic is open Monday to Saturday. "
        "Monday, Tuesday, and Friday — nine to five. "
        "Wednesday and Thursday — nine to seven. "
        "Saturday — nine to five. "
        "We're closed on Sundays."
    ),
}

LOCATION_ADDRESSES = {
    "alcester": "The Greig Sports Center, Kinwarton Road, Alcester, B49 6AD.",
    "redditch": "51 Bromsgrove Road, Redditch, B97 4RH.",
}

# Days each location is open (used for validation)
LOCATION_OPEN_DAYS = {
    "alcester": {0, 1, 2, 3, 4},        # Mon–Fri
    "redditch": {0, 1, 2, 3, 4, 5},     # Mon–Sat
}

# Hours tuple (open_h, close_h) per day per location
LOCATION_HOURS_TUPLE = {
    "alcester": {
        0: (8.5, 21), 1: (8.5, 21), 2: (8.5, 21),
        3: (8.5, 21), 4: (8.5, 21),
    },
    "redditch": {
        0: (9, 17), 1: (9, 17), 2: (9, 19),
        3: (9, 19), 4: (9, 17), 5: (9, 17),
    },
}


def get_location_hours_text(session: Dict[str, Any]) -> str:
    loc = session.get("selected_location", "alcester")
    return LOCATION_HOURS.get(loc, LOCATION_HOURS["alcester"])


def get_location_address_text(session: Dict[str, Any]) -> str:
    loc = session.get("selected_location", "alcester")
    return LOCATION_ADDRESSES.get(loc, LOCATION_ADDRESSES["alcester"])


def get_location_label(session: Dict[str, Any]) -> str:
    loc = session.get("selected_location", "alcester")
    return "Alcester" if loc == "alcester" else "Redditch"


# ============================================================================
# IMPROVEMENT 4: CONDITION → TREATMENT KNOWLEDGE BASE (50+ conditions)
# ============================================================================

# Each entry: keywords, primary_recommendation, additional_options,
#             recommendation_text, follow_up_question

CONDITION_KNOWLEDGE: Dict[str, Dict] = {

    # --- BACK ---
    "lower_back_pain": {
        "keywords": ["lower back", "lumbar", "lumbago", "base of spine"],
        "primary": "physiotherapy assessment",
        "options": ["acupuncture", "shockwave therapy", "laser therapy"],
        "text": (
            "Lower back pain is one of the most common things we see. "
            "A physiotherapy assessment is the best starting point — "
            "it helps us identify the root cause, whether it's muscular, postural, or something else. "
            "We can also use acupuncture for pain relief, "
            "shockwave therapy if it's been going on a while, "
            "or laser therapy to reduce inflammation."
        ),
        "follow_up": "Would you like to know more about any of those, or shall we book you in?",
    },
    "upper_back_pain": {
        "keywords": ["upper back", "thoracic", "between shoulder blades", "middle back"],
        "primary": "physiotherapy assessment",
        "options": ["acupuncture"],
        "text": (
            "Upper back pain often comes from posture or tension. "
            "Physiotherapy can work on loosening tight muscles and improving how you're holding yourself. "
            "Acupuncture is also really good for releasing that kind of tension."
        ),
        "follow_up": "Would you like to hear more about either of these?",
    },
    "sciatica": {
        "keywords": ["sciatica", "sciatic", "shooting down leg", "nerve pain leg", "down my leg"],
        "primary": "physiotherapy assessment",
        "options": ["acupuncture", "laser therapy"],
        "text": (
            "Sciatica usually responds really well to physiotherapy. "
            "We'll assess what's irritating the nerve and work to reduce the pressure and pain. "
            "Acupuncture can help with nerve pain, and laser therapy can reduce inflammation around the nerve."
        ),
        "follow_up": "Would you like to know more about these options?",
    },
    "disc_problems": {
        "keywords": ["disc", "disk", "herniated", "bulging", "slipped disc", "prolapsed"],
        "primary": "physiotherapy assessment",
        "options": ["laser therapy", "rehabilitation"],
        "text": (
            "Disc issues need careful assessment and treatment. "
            "Physiotherapy can help reduce pressure on the disc and strengthen the supporting muscles. "
            "Laser therapy helps reduce inflammation, "
            "and our rehabilitation programme is excellent for long-term recovery."
        ),
        "follow_up": "Would you like more information on any of these?",
    },

    # --- NECK & SHOULDER ---
    "neck_pain": {
        "keywords": ["neck", "cervical", "stiff neck", "neck ache", "can't turn head"],
        "primary": "physiotherapy assessment",
        "options": ["acupuncture", "laser therapy"],
        "text": (
            "Neck pain can really affect your day. "
            "Physiotherapy will assess whether it's coming from your muscles, joints, or posture. "
            "Acupuncture is particularly good for neck tension, "
            "and laser therapy can reduce inflammation in the neck joints."
        ),
        "follow_up": "Would you like to know more about these approaches?",
    },
    "whiplash": {
        "keywords": ["whiplash", "car accident", "rear-ended", "collision"],
        "primary": "physiotherapy assessment",
        "options": ["laser therapy", "acupuncture"],
        "text": (
            "Whiplash needs proper treatment to heal correctly. "
            "Physiotherapy will restore movement, reduce pain, and rebuild strength safely. "
            "Laser therapy is excellent for the inflammation, "
            "and acupuncture helps manage the pain during recovery."
        ),
        "follow_up": "Would you like to hear more about these?",
    },
    "shoulder_pain": {
        "keywords": ["shoulder", "rotator", "shoulder blade", "can't lift arm"],
        "primary": "physiotherapy assessment",
        "options": ["shockwave therapy", "laser therapy", "acupuncture"],
        "text": (
            "Shoulder pain can come from several things — rotator cuff, frozen shoulder, or something else. "
            "Physiotherapy will assess exactly what's going on. "
            "Shockwave is very effective for tendon problems, "
            "laser therapy reduces inflammation, "
            "and acupuncture gives excellent pain relief."
        ),
        "follow_up": "Would you like to know more about any of these?",
    },
    "frozen_shoulder": {
        "keywords": ["frozen shoulder", "adhesive capsulitis", "shoulder stuck", "can't move shoulder"],
        "primary": "physiotherapy assessment",
        "options": ["acupuncture", "laser therapy"],
        "text": (
            "Frozen shoulder can be really frustrating. "
            "Physiotherapy uses specific techniques to gradually improve your range of motion. "
            "Acupuncture helps manage the pain, and laser therapy can reduce inflammation and speed up healing."
        ),
        "follow_up": "Would you like more details on these treatments?",
    },
    "rotator_cuff": {
        "keywords": ["rotator cuff", "supraspinatus", "shoulder tendon", "shoulder weakness"],
        "primary": "physiotherapy assessment",
        "options": ["shockwave therapy", "laser therapy", "rehabilitation"],
        "text": (
            "Rotator cuff injuries need expert care. "
            "Physiotherapy will assess the extent and create a progressive strengthening programme. "
            "Shockwave therapy works brilliantly for rotator cuff tendons, "
            "and our rehabilitation programme helps you rebuild full strength."
        ),
        "follow_up": "Would you like to know more about these options?",
    },

    # --- ARM, ELBOW, WRIST, HAND ---
    "tennis_elbow": {
        "keywords": ["tennis elbow", "lateral epicondylitis", "outside elbow"],
        "primary": "physiotherapy assessment",
        "options": ["shockwave therapy", "laser therapy"],
        "text": (
            "Tennis elbow is very common — you don't have to play tennis! "
            "Physiotherapy can identify the cause and strengthen the area. "
            "Shockwave therapy has an excellent success rate for this specifically. "
            "Laser therapy also works well for the inflammation."
        ),
        "follow_up": "Would you like to know more about shockwave or the other options?",
    },
    "golfers_elbow": {
        "keywords": ["golfer's elbow", "golfers elbow", "medial epicondylitis", "inside elbow"],
        "primary": "physiotherapy assessment",
        "options": ["shockwave therapy", "laser therapy"],
        "text": (
            "Golfer's elbow can be quite stubborn without proper treatment. "
            "Physiotherapy will address the cause and strengthen the area. "
            "Shockwave therapy is particularly effective for this type of tendon problem."
        ),
        "follow_up": "Would you like to hear more about these?",
    },
    "wrist_pain": {
        "keywords": ["wrist", "wrist pain", "carpal", "weak wrist"],
        "primary": "physiotherapy assessment",
        "options": ["laser therapy", "acupuncture"],
        "text": (
            "Wrist pain can really interfere with daily life. "
            "Physiotherapy can assess whether it's the wrist itself or referred from further up. "
            "Laser therapy works well for inflammation, and acupuncture helps with pain management."
        ),
        "follow_up": "Would you like more information?",
    },
    "carpal_tunnel": {
        "keywords": ["carpal tunnel", "tingling fingers", "numbness hand", "median nerve"],
        "primary": "physiotherapy assessment",
        "options": ["laser therapy", "acupuncture"],
        "text": (
            "Carpal tunnel causes tingling and numbness from nerve compression at the wrist. "
            "Physiotherapy can reduce the pressure on the nerve through specific techniques. "
            "Laser therapy reduces inflammation around the nerve, and acupuncture helps manage symptoms."
        ),
        "follow_up": "Would you like to know more about how we treat this?",
    },
    "hand_pain": {
        "keywords": ["hand pain", "finger pain", "thumb pain", "arthritis hand"],
        "primary": "physiotherapy assessment",
        "options": ["laser therapy", "acupuncture"],
        "text": (
            "Hand pain can come from arthritis, tendons, or overuse. "
            "Physiotherapy will assess what's happening and give you exercises to manage it. "
            "Laser therapy works well for hand joint inflammation."
        ),
        "follow_up": "Would you like to hear more?",
    },

    # --- HIP, KNEE, LEG ---
    "hip_pain": {
        "keywords": ["hip", "hip pain", "groin", "hip joint", "hip flexor"],
        "primary": "physiotherapy assessment",
        "options": ["laser therapy", "rehabilitation", "acupuncture"],
        "text": (
            "Hip pain can come from the joint, surrounding muscles, or even your back. "
            "Physiotherapy will find the true cause and work on improving movement and strength. "
            "Laser therapy helps with inflammation, and our rehabilitation programme is excellent for building hip strength."
        ),
        "follow_up": "Would you like to know more about these?",
    },
    "knee_pain": {
        "keywords": ["knee", "kneecap", "patella", "knee joint", "can't bend knee"],
        "primary": "physiotherapy assessment",
        "options": ["laser therapy", "rehabilitation", "acupuncture"],
        "text": (
            "Knee pain is very common and can have various causes. "
            "Physiotherapy will identify the problem and strengthen the muscles supporting your knee. "
            "Laser therapy reduces inflammation, "
            "and our rehabilitation programme helps build strong, stable knees."
        ),
        "follow_up": "Would you like more information on any of these?",
    },
    "meniscus_injury": {
        "keywords": ["meniscus", "cartilage knee", "knee twist", "torn cartilage"],
        "primary": "physiotherapy assessment",
        "options": ["laser therapy", "rehabilitation"],
        "text": (
            "Meniscus injuries can often be managed very well without surgery. "
            "Physiotherapy will assess the injury, reduce pain, and restore function. "
            "Our rehabilitation programme then rebuilds the strength and stability you need."
        ),
        "follow_up": "Would you like to know more?",
    },
    "runners_knee": {
        "keywords": ["runner's knee", "runners knee", "patellofemoral", "front knee pain"],
        "primary": "physiotherapy assessment",
        "options": ["laser therapy", "rehabilitation"],
        "text": (
            "Runner's knee usually comes from biomechanical issues. "
            "Physiotherapy can identify what's causing it and correct those patterns. "
            "Laser therapy helps with the inflammation, and our rehabilitation programme prevents it coming back."
        ),
        "follow_up": "Would you like more details?",
    },
    "it_band_syndrome": {
        "keywords": ["it band", "ITB", "iliotibial", "outside knee", "hip to knee"],
        "primary": "physiotherapy assessment",
        "options": ["laser therapy", "rehabilitation"],
        "text": (
            "IT band syndrome is common in runners and cyclists. "
            "Physiotherapy will release the tension, improve your movement patterns, and strengthen the right muscles. "
            "Laser therapy reduces inflammation and our rehabilitation programme prevents recurrence."
        ),
        "follow_up": "Would you like to know more?",
    },

    # --- ANKLE & FOOT ---
    "ankle_pain": {
        "keywords": ["ankle", "sprained ankle", "twisted ankle", "rolled ankle"],
        "primary": "physiotherapy assessment",
        "options": ["laser therapy", "rehabilitation"],
        "text": (
            "Ankle injuries need proper treatment to heal and prevent future instability. "
            "Physiotherapy will reduce swelling, restore movement, and rebuild strength. "
            "Laser therapy speeds up healing and our rehabilitation programme gives you a stable, strong ankle."
        ),
        "follow_up": "Would you like to hear more?",
    },
    "plantar_fasciitis": {
        "keywords": ["plantar fasciitis", "heel pain", "foot arch pain", "pain bottom foot"],
        "primary": "physiotherapy assessment",
        "options": ["shockwave therapy", "laser therapy"],
        "text": (
            "Plantar fasciitis can be really painful, especially first thing in the morning. "
            "Physiotherapy can help with stretching, strengthening, and footwear advice. "
            "Shockwave therapy has an excellent success rate for this specifically — "
            "it's one of the best treatments available for plantar fasciitis."
        ),
        "follow_up": "Would you like to know more about shockwave therapy?",
    },
    "achilles_tendonitis": {
        "keywords": ["achilles", "achilles tendon", "back of ankle", "heel cord"],
        "primary": "physiotherapy assessment",
        "options": ["shockwave therapy", "laser therapy", "rehabilitation"],
        "text": (
            "Achilles tendonitis needs careful management to heal properly. "
            "Physiotherapy loads the tendon correctly to stimulate healing. "
            "Shockwave is particularly effective for achilles problems, "
            "and our rehabilitation programme helps you return to running safely."
        ),
        "follow_up": "Would you like more information?",
    },
    "foot_pain": {
        "keywords": ["foot pain", "sore feet", "feet hurt"],
        "primary": "physiotherapy assessment",
        "options": ["laser therapy", "acupuncture"],
        "text": (
            "Foot pain can have many causes. "
            "Physiotherapy will assess what's going on — whether it's the arch, heel, or referred from elsewhere. "
            "Laser therapy works well for foot inflammation and acupuncture provides good pain relief."
        ),
        "follow_up": "Would you like to know more?",
    },

    # --- SPORTS & INJURY ---
    "sports_injury": {
        "keywords": ["sports", "football", "rugby", "cricket", "gym injury", "training injury"],
        "primary": "physiotherapy assessment",
        "options": ["rehabilitation", "shockwave therapy", "laser therapy"],
        "text": (
            "Sports injuries need expert assessment to get you back playing safely. "
            "Physiotherapy will evaluate the injury and create a recovery plan. "
            "Our rehabilitation programme is designed to rebuild strength and get you match-fit. "
            "Shockwave works well for chronic sports injuries, and laser therapy speeds up acute healing."
        ),
        "follow_up": "Would you like more details on any of these?",
    },
    "running_injury": {
        "keywords": ["running", "runner", "jogging", "marathon", "running pain"],
        "primary": "physiotherapy assessment",
        "options": ["rehabilitation", "laser therapy", "shockwave therapy"],
        "text": (
            "Running injuries often come from biomechanical issues or overtraining. "
            "Physiotherapy can assess your technique, identify weaknesses, "
            "and create a progressive return-to-running plan. "
            "Our rehabilitation programme builds the strength and stability runners need."
        ),
        "follow_up": "Would you like to know more?",
    },
    "muscle_strain": {
        "keywords": ["pulled muscle", "muscle strain", "torn muscle", "muscle tear"],
        "primary": "physiotherapy assessment",
        "options": ["laser therapy", "rehabilitation"],
        "text": (
            "Muscle strains need proper treatment to heal correctly and prevent re-injury. "
            "Physiotherapy will assess the extent and create a progressive recovery plan. "
            "Laser therapy speeds up the healing process."
        ),
        "follow_up": "Would you like to know more?",
    },
    "ligament_injury": {
        "keywords": ["ligament", "sprain", "torn ligament", "ligament damage"],
        "primary": "physiotherapy assessment",
        "options": ["laser therapy", "rehabilitation"],
        "text": (
            "Ligament injuries need careful management to heal properly. "
            "Physiotherapy reduces swelling, restores movement, and rebuilds the supporting muscles. "
            "Laser therapy speeds healing, and our rehabilitation programme restores full stability."
        ),
        "follow_up": "Would you like more information?",
    },

    # --- POST-SURGICAL ---
    "post_surgery": {
        "keywords": ["surgery", "operation", "post-op", "after surgery", "had surgery"],
        "primary": "physiotherapy assessment",
        "options": ["rehabilitation", "laser therapy"],
        "text": (
            "Post-surgical rehabilitation is crucial for a successful recovery. "
            "Physiotherapy will safely restore movement and rebuild strength. "
            "Our rehabilitation programme is designed specifically for post-surgical recovery. "
            "Laser therapy can also help speed up tissue healing."
        ),
        "follow_up": "Would you like to know more about the rehabilitation process?",
    },
    "joint_replacement": {
        "keywords": ["joint replacement", "hip replacement", "knee replacement", "new hip", "new knee"],
        "primary": "physiotherapy assessment",
        "options": ["rehabilitation"],
        "text": (
            "Recovery from joint replacement needs expert physiotherapy and rehabilitation. "
            "We work with you through all the stages — from early post-op through to full function. "
            "Our rehabilitation programme focuses on strength, mobility, and getting you back to daily life."
        ),
        "follow_up": "Would you like to hear more about the programme?",
    },
    "acl_injury": {
        "keywords": ["ACL", "anterior cruciate", "ACL tear", "ACL reconstruction"],
        "primary": "physiotherapy assessment",
        "options": ["rehabilitation", "laser therapy"],
        "text": (
            "ACL injuries — whether treated surgically or conservatively — need expert rehabilitation. "
            "Physiotherapy will restore range of motion and rebuild the strength your knee needs. "
            "Our rehabilitation programme gets you back to sport safely."
        ),
        "follow_up": "Would you like to know more about ACL rehabilitation?",
    },

    # --- CHRONIC ---
    "chronic_pain": {
        "keywords": ["chronic", "persistent", "months", "years", "constant pain", "ongoing pain"],
        "primary": "physiotherapy assessment",
        "options": ["acupuncture", "shockwave therapy", "psychotherapy"],
        "text": (
            "Chronic pain often needs a holistic approach. "
            "Physiotherapy addresses the physical side and teaches pain management strategies. "
            "Acupuncture is excellent for ongoing relief, and shockwave helps with persistent tissue issues. "
            "Sometimes chronic pain has a psychological element too — "
            "our psychotherapy service can support with that side as well."
        ),
        "follow_up": "Which of these would you like to know more about?",
    },
    "arthritis": {
        "keywords": ["arthritis", "osteoarthritis", "arthritic", "joint wear"],
        "primary": "physiotherapy assessment",
        "options": ["laser therapy", "acupuncture", "rehabilitation"],
        "text": (
            "While we can't cure arthritis, we can help you manage it much better. "
            "Physiotherapy maintains joint mobility and strengthens the muscles around the joint. "
            "Laser therapy is excellent for reducing arthritis inflammation. "
            "Acupuncture provides good pain relief, and our rehabilitation programme keeps you strong and mobile."
        ),
        "follow_up": "Would you like to know more about these approaches?",
    },
    "fibromyalgia": {
        "keywords": ["fibromyalgia", "chronic fatigue", "widespread pain", "tender points"],
        "primary": "physiotherapy assessment",
        "options": ["acupuncture", "psychotherapy"],
        "text": (
            "Fibromyalgia needs a gentle, understanding approach. "
            "Physiotherapy can help with graded exercise, pacing strategies, and pain management. "
            "Acupuncture often provides good relief for fibromyalgia symptoms. "
            "Our psychotherapy service can also help with the emotional impact."
        ),
        "follow_up": "Would you like to hear more?",
    },

    # --- WORK & POSTURE ---
    "rsi": {
        "keywords": ["RSI", "repetitive strain", "overuse injury", "typing pain"],
        "primary": "physiotherapy assessment",
        "options": ["laser therapy", "acupuncture"],
        "text": (
            "RSI needs treatment but also changes to prevent it recurring. "
            "Physiotherapy can treat the injury and advise on ergonomics and work setup. "
            "Laser therapy works well for RSI inflammation, and acupuncture helps manage the pain."
        ),
        "follow_up": "Would you like more information?",
    },
    "desk_pain": {
        "keywords": ["desk", "computer", "office", "sitting all day", "desk job"],
        "primary": "physiotherapy assessment",
        "options": ["acupuncture"],
        "text": (
            "Pain from desk work is incredibly common. "
            "Physiotherapy will assess your posture, give you exercises to counteract sitting, "
            "and advise on your desk setup. "
            "Acupuncture is great for releasing the tension that builds up from office work."
        ),
        "follow_up": "Would you like to know more?",
    },
    "posture_problems": {
        "keywords": ["posture", "slouching", "hunched", "rounded shoulders", "bad posture"],
        "primary": "physiotherapy assessment",
        "options": ["acupuncture", "rehabilitation"],
        "text": (
            "Poor posture can cause all sorts of pain over time. "
            "Physiotherapy will work on releasing tight areas and strengthening the muscles that support good posture. "
            "Our rehabilitation programme helps you build the strength to maintain it."
        ),
        "follow_up": "Would you like more details?",
    },

    # --- HEAD & JAW ---
    "headaches": {
        "keywords": ["headache", "tension headache", "head pain"],
        "primary": "physiotherapy assessment",
        "options": ["acupuncture"],
        "text": (
            "Many headaches actually come from neck tension or jaw problems. "
            "Physiotherapy can assess whether your headaches are coming from the neck, jaw, or posture, "
            "and treat the root cause. "
            "Acupuncture is particularly effective for headache relief."
        ),
        "follow_up": "Would you like to know more?",
    },
    "migraines": {
        "keywords": ["migraine", "migraines", "severe headache"],
        "primary": "physiotherapy assessment",
        "options": ["acupuncture"],
        "text": (
            "Neck problems and muscle tension can contribute to migraines. "
            "Physiotherapy works on your neck and posture, which may help reduce frequency. "
            "Acupuncture has good evidence for reducing both the frequency and severity of migraines."
        ),
        "follow_up": "Would you like to hear more?",
    },
    "tmj_jaw_pain": {
        "keywords": ["jaw", "TMJ", "temporomandibular", "jaw clicking", "jaw pain", "can't open mouth"],
        "primary": "physiotherapy assessment",
        "options": ["acupuncture", "laser therapy"],
        "text": (
            "TMJ and jaw problems can be really uncomfortable. "
            "Physiotherapy works on the jaw joint, surrounding muscles, and often the neck — "
            "which frequently contributes. Acupuncture is excellent for jaw tension. "
            "Laser therapy can reduce joint inflammation."
        ),
        "follow_up": "Would you like to know more?",
    },

    # --- PREGNANCY & POST-NATAL ---
    "pregnancy_pain": {
        "keywords": ["pregnant", "pregnancy", "expecting", "maternity"],
        "primary": "physiotherapy assessment",
        "options": ["acupuncture"],
        "text": (
            "Pregnancy puts a lot of strain on your body, especially your back and pelvis. "
            "Physiotherapy can manage that pain, give you safe exercises, and prepare your body for birth. "
            "Acupuncture is safe during pregnancy and provides good pain relief."
        ),
        "follow_up": "Would you like to know more?",
    },
    "post_natal": {
        "keywords": ["post-natal", "postnatal", "after birth", "pelvic floor", "new mum", "diastasis"],
        "primary": "physiotherapy assessment",
        "options": ["rehabilitation"],
        "text": (
            "Post-natal recovery is so important but often overlooked. "
            "Physiotherapy can help with pelvic floor recovery, abdominal separation, "
            "and any pain from birth. "
            "Our rehabilitation programme helps you return to exercise safely."
        ),
        "follow_up": "Would you like to hear more?",
    },

    # --- BALANCE & ELDERLY ---
    "balance_problems": {
        "keywords": ["balance", "dizzy", "unsteady", "vertigo", "falling", "falls"],
        "primary": "physiotherapy assessment",
        "options": ["rehabilitation"],
        "text": (
            "Balance problems can increase fall risk significantly. "
            "Physiotherapy will assess your balance, work on strengthening and coordination. "
            "Our rehabilitation programme includes specific balance training to help you feel more confident."
        ),
        "follow_up": "Would you like to know more?",
    },

    # --- MENTAL HEALTH ---
    "stress_anxiety": {
        "keywords": ["stress", "anxiety", "anxious", "worried", "overwhelmed", "panic"],
        "primary": "psychotherapy",
        "options": ["acupuncture"],
        "text": (
            "I'm glad you're reaching out. "
            "Our psychotherapy service provides a safe, confidential space to explore what you're experiencing. "
            "Interestingly, acupuncture can also be very calming and help with anxiety symptoms."
        ),
        "follow_up": "Would you like to book a psychotherapy session?",
    },
    "depression": {
        "keywords": ["depression", "depressed", "low mood", "can't cope", "hopeless"],
        "primary": "psychotherapy",
        "options": [],
        "text": (
            "Thank you for sharing that. "
            "Our psychotherapy service offers a supportive, non-judgmental environment "
            "to work through what you're experiencing at your own pace."
        ),
        "follow_up": "Would you like to book a session?",
    },
    "sleep_problems": {
        "keywords": ["sleep", "insomnia", "can't sleep", "sleep problems"],
        "primary": "psychotherapy",
        "options": ["acupuncture"],
        "text": (
            "Sleep problems can affect everything. "
            "Psychotherapy can help explore what's affecting your sleep and develop strategies. "
            "Acupuncture is also very helpful for sleep issues."
        ),
        "follow_up": "Would you like to know more about either of these?",
    },
    "trauma_ptsd": {
        "keywords": ["trauma", "PTSD", "flashback", "nightmares", "traumatic"],
        "primary": "psychotherapy",
        "options": [],
        "text": (
            "I'm sorry you've been through that. "
            "Our psychotherapy service provides a safe space to work through traumatic experiences "
            "at your own pace, using trauma-informed approaches."
        ),
        "follow_up": "Would you like to book a session to discuss further?",
    },
    "life_transitions": {
        "keywords": ["divorce", "bereavement", "grief", "loss", "relationship", "life change"],
        "primary": "psychotherapy",
        "options": [],
        "text": (
            "Life transitions and losses can be really difficult to navigate. "
            "Our psychotherapy service provides support through these challenging times — "
            "helping you process what you're going through and find ways to move forward."
        ),
        "follow_up": "Would you like to schedule a session?",
    },

    # --- PREVENTION & RETURN TO SPORT ---
    "injury_prevention": {
        "keywords": ["prevent injury", "prevention", "prehab", "stay healthy"],
        "primary": "physiotherapy assessment",
        "options": ["rehabilitation"],
        "text": (
            "It's great that you're thinking ahead. "
            "Physiotherapy can assess any weaknesses or imbalances that might lead to injury. "
            "Our rehabilitation programme can then build your strength and resilience."
        ),
        "follow_up": "Would you like to book an assessment?",
    },
    "return_to_sport": {
        "keywords": ["return to sport", "get back playing", "ready to play", "fitness test"],
        "primary": "physiotherapy assessment",
        "options": ["rehabilitation"],
        "text": (
            "Returning to sport after injury needs proper assessment to make sure you're ready. "
            "Physiotherapy will test your strength, movement, and sport-specific function. "
            "Our rehabilitation programme helps you build back match fitness safely."
        ),
        "follow_up": "Would you like to book a return-to-sport assessment?",
    },
}


def identify_condition(complaint: str) -> Optional[Dict]:
    """
    Match caller's complaint to the best condition entry.
    Scores by total character length of matched keywords (longer = more specific).
    Returns None if no match found.
    """
    complaint_lower = complaint.lower()
    best_score, best_entry = 0, None

    for cond_id, entry in CONDITION_KNOWLEDGE.items():
        matched = [kw for kw in entry["keywords"] if kw in complaint_lower]
        if matched:
            score = sum(len(kw) for kw in matched)
            if score > best_score:
                best_score, best_entry = score, entry

    return best_entry


# ============================================================================
# IMPROVEMENT 6: SERVICE EXPLANATIONS (brief, natural, Mark's words)
# ============================================================================

SERVICE_EXPLANATIONS: Dict[str, Dict[str, str]] = {

    "physiotherapy": {
        "short": (
            "Physiotherapy here is a bit different. "
            "We take a holistic approach — we look at your physical mobility and strength, "
            "but also your emotional well-being. "
            "We identify the root cause and build a treatment plan around you."
        ),
        "detailed": (
            "During your assessment, we start by really listening. "
            "We want to understand how the issue is affecting your life, not just the physical side. "
            "\n\n"
            "Then we do a physical evaluation — mobility, strength, movement patterns. "
            "We find the root cause, not just the symptoms. "
            "\n\n"
            "From there, we build a plan together. "
            "That might include manual therapy, exercises, and soft tissue work. "
            "We can also bring in acupuncture, shockwave, or laser therapy when it makes sense. "
            "\n\n"
            "Sessions are 50 minutes and £75."
        ),
    },

    "physiotherapy_followup": {
        "short": (
            "Follow-up sessions are where the real progress happens. "
            "We check in, adjust the plan if needed, "
            "and keep building on what's working. "
            "If we feel you need a specialist or imaging like an MRI, "
            "we'll support that referral too."
        ),
        "detailed": (
            "Every follow-up starts with a check-in — how have you been? "
            "What's improved? What hasn't? "
            "\n\n"
            "We'll reassess and adjust your plan based on where you are now. "
            "No two sessions are the same — it evolves with you. "
            "\n\n"
            "If at any point we feel you'd benefit from a specialist — "
            "a surgeon, GP, or advanced imaging — we'll help arrange that. "
            "\n\n"
            "Sessions are 50 minutes and £75."
        ),
    },

    "acupuncture": {
        "short": (
            "Acupuncture uses very fine needles placed at specific points "
            "to restore the natural flow of energy — known as Qi. "
            "It promotes healing, reduces pain, and improves overall well-being."
        ),
        "detailed": (
            "It sounds more daunting than it is. "
            "The needles are incredibly fine — most people barely feel them. "
            "\n\n"
            "They're placed at specific points to stimulate your body's natural healing response. "
            "It improves circulation, releases muscle tension, and helps the body rebalance. "
            "\n\n"
            "A lot of people find it deeply relaxing — some drift off during treatment. "
            "\n\n"
            "We often use it as part of a physiotherapy session — "
            "no extra charge when we do."
        ),
    },

    "shockwave": {
        "short": (
            "Shockwave therapy uses targeted sound waves to stimulate healing in the tissue. "
            "It's particularly effective for chronic tendon issues "
            "where the body needs a push to restart the healing process."
        ),
        "detailed": (
            "Think of it as kick-starting the healing process. "
            "\n\n"
            "The device sends acoustic waves deep into the tissue. "
            "This increases blood flow, breaks down scar tissue, "
            "and triggers the body to produce new, healthy cells. "
            "\n\n"
            "It's especially good for stubborn tendon problems — "
            "Achilles tendonitis, plantar fasciitis, tennis elbow — "
            "where other treatments haven't quite worked. "
            "\n\n"
            "It can feel intense on the sore area, but it's quick — five to ten minutes. "
            "Most people see real improvement within a few sessions. "
            "\n\n"
            "There's a £45 surcharge when we use it during your session."
        ),
    },

    "laser": {
        "short": (
            "Laser therapy uses powerful laser light to reduce pain, bring down inflammation, "
            "and speed up tissue repair. "
            "It reaches deep into the body and is completely painless."
        ),
        "detailed": (
            "Class IV laser is one of the more advanced tools we have. "
            "\n\n"
            "The light penetrates deep into tissue — "
            "much deeper than standard laser treatments. "
            "It reduces inflammation, increases circulation, "
            "and accelerates the body's own repair process. "
            "\n\n"
            "The treatment is actually very pleasant. "
            "The laser head moves slowly over the area and just feels warm and relaxing. "
            "No pain at all. "
            "\n\n"
            "There's a £45 surcharge when we use it during your session."
        ),
    },

    "rehabilitation": {
        "short": (
            "Rehabilitation is about rebuilding — strength, movement, and confidence. "
            "Our rehab instructors work alongside the physio team "
            "to create a programme that gets you back to where you want to be."
        ),
        "detailed": (
            "Physiotherapy gets you on the right track. "
            "Rehabilitation takes you the rest of the way. "
            "\n\n"
            "Our rehabilitation instructors work closely with the physiotherapy team — "
            "so you might see both. That's by design. "
            "\n\n"
            "Sessions focus on strength, movement quality, coordination, "
            "and building the confidence to get back to normal activity. "
            "\n\n"
            "Sessions are 50 minutes and £65. "
            "We do ask that you have an initial physio assessment first."
        ),
    },

    "psychotherapy": {
        "short": (
            "Psychotherapy gives you a safe, confidential space "
            "to explore your thoughts and emotions. "
            "Using techniques like hypnotherapy and spiritual healing, "
            "we help reduce stress, build coping strategies, "
            "and support your mental well-being."
        ),
        "detailed": (
            "Sometimes the most important thing is just having a safe space to talk. "
            "\n\n"
            "Sessions are confidential and completely non-judgmental. "
            "We work with you at your own pace. "
            "\n\n"
            "The approach is flexible — talking therapy, hypnotherapy, "
            "or spiritual healing techniques, whatever feels right for you. "
            "\n\n"
            "We work with stress, anxiety, depression, trauma, life transitions, "
            "and the emotional side of living with chronic pain. "
            "\n\n"
            "Sessions are 50 minutes and £75."
        ),
    },

    "prescribing": {
        "short": (
            "Our physiotherapists are qualified independent prescribers. "
            "If medication would help — like pain relief — "
            "they can prescribe that directly, without you needing a separate GP appointment."
        ),
        "detailed": (
            "It's one of those things that makes a real difference in practice. "
            "\n\n"
            "If we feel medication would support your recovery — "
            "pain relief, anti-inflammatories, or something else — "
            "we can prescribe it directly as part of your care. "
            "\n\n"
            "Your plan is always designed with both safety and effectiveness in mind. "
            "We only prescribe when it genuinely makes sense for you. "
            "\n\n"
            "The consultation is £12.50, usually as part of your physio session. "
            "You'd take the prescription to any pharmacy as normal."
        ),
    },
}


# Treatment keyword → service key lookup
SERVICE_KEYWORDS: Dict[str, str] = {
    "physio":         "physiotherapy",
    "physiotherapy":  "physiotherapy",
    "physical therapy": "physiotherapy",
    "follow up":      "physiotherapy_followup",
    "follow-up":      "physiotherapy_followup",
    "followup":       "physiotherapy_followup",
    "acupuncture":    "acupuncture",
    "needles":        "acupuncture",
    "needle":         "acupuncture",
    "shockwave":      "shockwave",
    "shock wave":     "shockwave",
    "laser":          "laser",
    "laser therapy":  "laser",
    "rehab":          "rehabilitation",
    "rehabilitation": "rehabilitation",
    "psychotherapy":  "psychotherapy",
    "therapy":        "psychotherapy",
    "counselling":    "psychotherapy",
    "counseling":     "psychotherapy",
    "prescribing":    "prescribing",
    "prescription":   "prescribing",
    "medication":     "prescribing",
}


def extract_service_from_question(text: str) -> Optional[str]:
    t = text.lower()
    for kw, svc in SERVICE_KEYWORDS.items():
        if kw in t:
            return svc
    return None


def get_service_explanation(service_key: str, detail: str = "short") -> str:
    entry = SERVICE_EXPLANATIONS.get(service_key)
    if not entry:
        return (
            "That's something the team would be best placed to explain in detail. "
            "Would you like to book an assessment and discuss it then?"
        )
    return entry.get(detail, entry.get("short", ""))


# ============================================================================
# IMPROVEMENT 5: INSURANCE CONSTANTS & HELPERS
# Bupa NOT accepted. All others: self-pay + claim back.
# ============================================================================

# Improvement 5: Bupa must be False
ACCEPTED_INSURERS: Dict[str, bool] = {
    "bupa":         False,   # ← NOT accepted (as per client requirement)
    "axa":          True,
    "vitality":     True,
    "aviva":        True,
    "wpa":          True,
    "cigna":        True,
    "simply health": True,
    "simplyhealth": True,
    "health shield": True,
    "benenden":     True,
    "nuffield":     True,
}

# Calm, friendly insurance explanation lines
INSURANCE_EXPLAIN = (
    "So, here's how it works at Theorem Health. "
    "We operate as a self-pay clinic — "
    "which means you pay for your sessions directly with us. "
    "We don't bill insurance companies ourselves. "
    "However, many patients are able to claim the costs back through their insurer afterwards. "
    "We'll give you a detailed receipt and any documentation you need to make that claim. "
    "It's a straightforward process for most policies."
)

INSURANCE_BUPA_WARNING = (
    "I do need to let you know — we're not able to accept Bupa. "
    "So if you're with Bupa, you would need to pay privately."
)

INSURANCE_CHECK_WARNING = (
    "I want to be upfront — while many insurers do reimburse self-pay physiotherapy, "
    "we can't guarantee your specific policy will cover it. "
    "We'd strongly recommend calling your insurer before your appointment "
    "to confirm your coverage and find out what documentation they need. "
    "We'll make sure to provide you with everything you need to make your claim."
)

INSURANCE_BUPA_RESPONSE = (
    "I'm sorry about that. Unfortunately we're not able to accept Bupa directly. "
    "You're still very welcome to book as a private patient and pay the session fee. "
    "It's £75 for 50 minutes. "
    "Would you still like to go ahead, or would you like some time to think about it?"
)


@dataclass
class InsurerMatch:
    display_name: str
    normalized: str
    accepted: Optional[bool]
    confidence: float


def match_insurer(user_text: str, accepted_map: Dict[str, bool]) -> InsurerMatch:
    raw = (user_text or "").strip()
    n = _norm(raw)

    if n in accepted_map:
        return InsurerMatch(raw, n, bool(accepted_map[n]), 1.0)

    for k, v in accepted_map.items():
        if k and (k in n or n in k):
            conf = 0.85 if len(n) >= 3 else 0.70
            return InsurerMatch(raw, k, bool(v), conf)

    return InsurerMatch(raw, n, None, 0.40)


def extract_insurer_name(speech: str) -> str:
    speech_lower = speech.lower()
    known = {
        "axa": "AXA Health",
        "vitality": "Vitality",
        "aviva": "Aviva",
        "wpa": "WPA",
        "cigna": "Cigna",
        "simply health": "Simply Health",
        "simplyhealth": "Simply Health",
        "health shield": "Health Shield",
        "benenden": "Benenden Health",
        "nuffield": "Nuffield Health",
    }
    for key, val in known.items():
        if key in speech_lower:
            return val

    fillers = ["i'm with", "i have", "my insurance is", "i've got", "it's", "with"]
    cleaned = speech_lower
    for f in fillers:
        cleaned = cleaned.replace(f, "").strip()
    return cleaned.title() if cleaned else "Unknown"


# ============================================================================
# TONE ENGINE (unchanged — kept as-is since it works well)
# ============================================================================

FRIENDLY_ACK = ["No problem.", "Of course.", "Sure.", "Got it."]
FRIENDLY_REASSURE = ["No worries.", "That's totally fine."]
FRIENDLY_CHECKING = ["One moment — I'm checking.", "Okay — I'll check that now."]

NO_FRIENDLY_PHRASES = [
    "please say", "please tell me", "say 1", "press 1",
    "say 2", "press 2", "say 3", "press 3",
    "one, two, or three", "1, 2, or 3",
    "to confirm", "yes to confirm", "no to cancel",
    "phone number", "date and time", "what day", "what time",
    "repeat", "i have three", "the first option is",
    "the second option is", "the third option is",
    "available appointment", "available slots",
]

NO_FRIENDLY_STARTS = (
    "sorry", "perfect", "thanks", "thank you",
    "confirmed", "all done", "you're", "you\u2019re",
)

# ============================================================================
# HELPER FUNCTIONS (these stay outside the async function)
# ============================================================================
def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()

def _is_high_precision_prompt(text: str) -> bool:
    t = (text or "").strip().lower()
    if not t or len(t) > 120:
        return True
    if t.startswith(NO_FRIENDLY_STARTS):
        return True
    return any(p in t for p in NO_FRIENDLY_PHRASES)

def _classify_tone(text: str) -> str:
    t = (text or "").strip().lower()
    if not t or _is_high_precision_prompt(text):
        return "none"
    if t.startswith(("sorry", "i didn't catch", "i can't", "there was a technical issue")):
        return "reassure"
    if any(k in t for k in ["check availability", "let me check", "i'll check", "one moment"]):
        return "checking"
    if len(t) <= 55 and any(k in t for k in ["thanks", "great", "okay", "ok", "perfect", "got it"]):
        return "ack"
    return "none"

def _apply_tone(text: str, tone: str) -> str:
    if not text or tone == "none":
        return text
    lower = text.strip().lower()
    if lower.startswith(NO_FRIENDLY_STARTS):
        return text
    if tone == "ack":
        return f"{random.choice(FRIENDLY_ACK)} {text}"
    if tone == "reassure":
        return f"{random.choice(FRIENDLY_REASSURE)} {text}"
    if tone == "checking":
        return f"{random.choice(FRIENDLY_CHECKING)} {text}"
    return text

def _friendly(text: str) -> str:
    text = _clean(text)
    return _apply_tone(text, _classify_tone(text))

def _say(
    text: str,
    session: Dict[str, Any],
    tone: Optional[str] = None,
) -> Tuple[str, Dict[str, Any]]:
    text = _clean(text)
    out = _friendly(text) if tone is None else _apply_tone(text, tone)
    session["last_bot_prompt"] = out
    return out, session
# ============================================================================
# SESSION KEYS & STATE CONSTANTS
# ============================================================================

TOKENS_KEY            = "google_tokens"
DEFAULT_DURATION_MIN  = 30

ACTIVE_CLINIC_KEY        = "clinic_id"
LAST_OFFERED_SLOTS_KEY   = "last_offered_slots"
SELECTED_SLOT_KEY        = "selected_slot"
SLOT_LABELS_KEY          = "slot_labels"
SELECTED_SLOT_LABEL_KEY  = "selected_slot_label"
LAST_BOT_PROMPT_KEY      = "last_bot_prompt"
LAST_USER_TEXT_KEY       = "last_user_text"
INSURANCE_PROVIDER_STATE = "INSURANCE_PROVIDER"
FAQ_DETOUR               = "FAQ_DETOUR"

# Booking states
TRIAGE              = "TRIAGE"
BOOK_PATIENT_TYPE   = "BOOK_PATIENT_TYPE"
BOOK_REASON         = "BOOK_REASON"
BOOK_TIME_PREF      = "BOOK_TIME_PREF"
BOOK_PICK_SLOT      = "BOOK_PICK_SLOT"
BOOK_NAME           = "BOOK_NAME"
BOOK_PHONE          = "BOOK_PHONE"
BOOK_CONFIRM        = "BOOK_CONFIRM"

# Reschedule states
RESCH_NAME          = "RESCH_NAME"
RESCH_ORIGINAL      = "RESCH_ORIGINAL"
RESCH_NEW_PREF      = "RESCH_NEW_PREF"
RESCH_PICK_SLOT     = "RESCH_PICK_SLOT"
RESCH_CONFIRM       = "RESCH_CONFIRM"
RESCH_PHONE_FALLBACK = "RESCH_PHONE_FALLBACK"

# Insurance states
INS_EXPLAIN         = "INS_EXPLAIN"
INS_COLLECT_INSURER = "INS_COLLECT_INSURER"
INS_BUPA_RESPONSE   = "INS_BUPA_RESPONSE"
INS_COLLECT_POLICY  = "INS_COLLECT_POLICY"

BOOKING_STATES = {
    BOOK_PATIENT_TYPE, BOOK_REASON, BOOK_TIME_PREF,
    BOOK_PICK_SLOT, BOOK_NAME, BOOK_PHONE, BOOK_CONFIRM,
}


# ============================================================================
# GENERIC HELPERS
# ============================================================================

def _norm(text: Optional[str]) -> str:
    t = (text or "").strip().lower()
    t = re.sub(r"[^a-z0-9\s]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _contains_any(t: str, keywords: list) -> bool:
    return any(k in t for k in keywords)


def _digits_only(s: str) -> str:
    return re.sub(r"\D", "", s or "")


def _t(text: Optional[str]) -> str:
    return (text or "").strip().lower()


def is_continue(text: Optional[str]) -> bool:
    t = _t(text)
    return any(x in t for x in ["continue", "carry on", "go back", "resume"])


def is_yes(text: str) -> bool:
    t = (text or "").lower()
    return (
        any(x in t for x in ["yes", "yeah", "yep", "ok", "okay", "sure", "go ahead", "sounds good"])
        or is_continue(text)
    )


def is_no(text: str) -> bool:
    t = (text or "").lower()
    return any(x in t for x in ["no", "not now", "stop", "cancel"])


def normalize_phone(phone: str) -> str:
    return _digits_only(phone)


def is_valid_phone(phone: str) -> bool:
    p = normalize_phone(phone)
    return 10 <= len(p) <= 15


def looks_like_name(text: str) -> bool:
    t = (text or "").strip()
    if len(t) < 2 or len(t) > 60:
        return False
    if len(re.findall(r"[A-Za-z]", t)) < 2:
        return False
    if len(re.findall(r"\d", t)) > 3:
        return False
    if _norm(t) in ("booking", "book", "reschedule", "cancel", "appointment", "new", "returning"):
        return False
    return True


def parse_patient_type(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    t = _norm(text)
    if not t:
        return None
    if t in ("new", "new patient", "first", "1"):
        return "NEW"
    if t in ("returning", "existing", "return", "2"):
        return "RETURNING"
    for p in ("returning", "existing", "been before", "already a patient",
              "follow up", "followup", "follow-up", "seen you before"):
        if p in t:
            return "RETURNING"
    for p in ("new", "first time", "first visit", "never been", "initial"):
        if p in t:
            return "NEW"
    return None


def parse_slot_choice(text: str, dtmf: Optional[str] = None) -> Optional[int]:
    if dtmf and str(dtmf).strip() in ("1", "2", "3"):
        return int(str(dtmf).strip())
    t = _norm(text)
    m = re.search(r"\b(1|2|3)\b", t)
    if m:
        return int(m.group(1))
    for w, n in {"one": 1, "first": 1, "two": 2, "second": 2, "three": 3, "third": 3}.items():
        if re.search(rf"\b{w}\b", t):
            return n
    return None


def _is_interrupt(text: str) -> bool:
    t = _norm(text)
    return t in {
        "stop", "cancel", "wait", "hold on", "hang on",
        "one second", "pause", "restart", "start over", "reset",
        "go back", "back", "main menu", "menu",
    }


def _reset_to_triage(session: Dict[str, Any]) -> Dict[str, Any]:
    session["state"] = TRIAGE
    session["intent"] = None
    session["collected"] = {}
    session[LAST_OFFERED_SLOTS_KEY] = None
    session[SELECTED_SLOT_KEY] = None
    for k in ("resch_event_id", "resch_event_summary", SLOT_LABELS_KEY,
              SELECTED_SLOT_LABEL_KEY, "manual_booking", "manual_reason", "manual_reschedule",
              "pending_intent", "location_id"):
        session.pop(k, None)
    return session


def _interrupt_reply(text: str, session: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    t = _norm(text)
    if t in {"restart", "start over", "reset"}:
        session = _reset_to_triage(session)
        return _say("Okay — starting over. What would you like to do?", session)
    if t in {"main menu", "menu", "go back", "back"}:
        session = _reset_to_triage(session)
        return _say("No problem — what would you like to do: book, reschedule, or ask a question?", session)
    if t in {"stop", "cancel"}:
        session = _reset_to_triage(session)
        return _say("No problem. What would you like to do instead?", session)
    return _say("Of course. When you're ready, tell me what you'd like to do.", session)


# ============================================================================
# CLINIC / TIMEZONE HELPERS
# ============================================================================

def get_clinic(session: Dict[str, Any]) -> Dict[str, Any]:
    key = session.get(ACTIVE_CLINIC_KEY)
    if key and key in CLINICS:
        clinic = dict(CLINICS[key])
        clinic["clinic_id"] = key
        return clinic
    cid = "demo" if "demo" in CLINICS else next(iter(CLINICS))
    clinic = dict(CLINICS[cid])
    clinic["clinic_id"] = cid
    return clinic


def get_tz(clinic: Dict[str, Any]):
    return pytz.timezone(clinic.get("timezone", "Europe/London"))


def clinic_default_hours(clinic: Dict[str, Any]) -> Tuple[int, int]:
    wh = clinic.get("working_hours", {})
    mon = wh.get("mon")
    if isinstance(mon, (list, tuple)) and len(mon) == 2:
        return int(mon[0]), int(mon[1])
    return 9, 18


# ============================================================================
# INTENT DETECTION
# ============================================================================

SERVICE_EXPLAIN_KEYWORDS = {
    "shockwave":           ["shockwave", "shock wave", "eswt"],
    "laser":               ["laser", "laser therapy"],
    "acupuncture":         ["acupuncture", "needles", "needle"],
    "physiotherapy":       ["physio", "physiotherapy", "physical therapy"],
    "rehabilitation":      ["rehab", "rehabilitation"],
    "psychotherapy":       ["psychotherapy", "counselling", "counseling", "talking therapy"],
    "prescribing":         ["prescribing", "prescription", "medication"],
    "physiotherapy_followup": ["follow up", "follow-up", "followup"],
}


def detect_service_topic(text: str) -> Optional[str]:
    t = (text or "").lower()
    for topic, keywords in SERVICE_EXPLAIN_KEYWORDS.items():
        if any(k in t for k in keywords):
            return topic
    return None


def detect_intent(text: str) -> str:
    t = _norm(text)
    if not t:
        return "UNKNOWN"

    if _contains_any(t, ["book", "appointment", "schedule", "available", "slot"]):
        return "BOOK"
    if _contains_any(t, ["reschedule", "move", "rebook", "postpone", "change my appointment"]):
        return "RESCHEDULE"
    if _contains_any(t, ["cancel appointment", "cancel my appointment", "cancel booking", "cancellation"]):
        return "CANCEL"
    if _contains_any(t, ["price", "cost", "fee", "how much", "charge", "rates", "pay"]):
        return "FAQ_PRICES"
    if _contains_any(t, ["hours", "opening hours", "open", "close", "when are you open", "weekend"]):
        return "FAQ_HOURS"
    if _contains_any(t, ["address", "location", "where are you", "parking", "directions"]):
        return "FAQ_LOCATION"
    if _contains_any(t, ["insurance", "insured", "covered", "claim", "receipt",
                          "bupa", "axa", "vitality", "aviva", "wpa", "cigna"]):
        return "FAQ_INSURANCE"
    if _contains_any(t, ["what do you offer", "what services", "what treatments", "list of services"]):
        return "FAQ_SERVICES"
    if any(p in t for p in ["what is", "what's", "tell me about", "tell me more", "explain", "how does"]):
        if detect_service_topic(t) or "this service" in t or "that service" in t:
            return "FAQ_SERVICE_EXPLAIN"
    if _contains_any(t, ["service", "services", "treatment", "treatments",
                          "physio", "shockwave", "rehab", "acupuncture"]):
        return "FAQ_SERVICES"
    if _contains_any(t, ["cancel policy", "cancellation policy", "refund", "missed appointment"]):
        return "FAQ_POLICIES"
    if _contains_any(t, ["first visit", "what should i bring", "what do i wear", "arrive"]):
        return "FAQ_FIRST_VISIT"
    if _contains_any(t, ["privacy", "data", "gdpr", "recording", "confidential"]):
        return "FAQ_PRIVACY"
    if _contains_any(t, ["human", "person", "receptionist", "someone", "call me back", "speak to"]):
        return "HUMAN"
    return "OTHER"


# ============================================================================
# FAQ HANDLER (deterministic, location-aware)
# Improvement 2: Hours and address now location-specific
# ============================================================================

def faq_answer(intent: str, clinic: Dict[str, Any], session: Optional[Dict[str, Any]] = None) -> str:
    session = session or {}

    if intent == "FAQ_PRICES":
        return (
            "Physiotherapy sessions are £75 for 50 minutes. "
            "Rehabilitation sessions are £65. "
            "Psychotherapy is £75. "
            "If we use specialist equipment — shockwave or laser — "
            "there's an additional £45 for that. "
            "Prescribing consultations are £12.50."
        )

    if intent == "FAQ_HOURS":
        hours = get_location_hours_text(session)
        return f"{hours} Is there anything else I can help you with?"

    if intent == "FAQ_LOCATION":
        address = get_location_address_text(session)
        label = get_location_label(session)
        return f"The {label} clinic is at {address} Can I help you with anything else?"

    if intent == "FAQ_INSURANCE":
        # Handled by the full insurance flow in triage — this is just a fallback
        return (
            f"{INSURANCE_EXPLAIN} "
            f"{INSURANCE_BUPA_WARNING} "
            "Could I ask which insurer you're with?"
        )

    if intent == "FAQ_SERVICES":
        return SERVICES_PROMPT

    if intent == "FAQ_POLICIES":
        return (
            "We have a 24-hour cancellation policy. "
            "Please let us know at least 24 hours before your appointment "
            "if you need to cancel or reschedule. "
            "Otherwise the full fee applies. "
            "You can reach us on 07870 166861."
        )

    if intent == "FAQ_FIRST_VISIT":
        return (
            "Just wear loose or comfortable clothing if you can. "
            "If you have any scans or reports, bring those along. "
            "But honestly, don't worry if you haven't — just come as you are."
        )

    if intent == "FAQ_PRIVACY":
        return "Your information is treated as confidential and handled in line with UK data protection rules."

    return "How can I help?"


def resume_prompt_for_state(state: str) -> str:
    if state == BOOK_REASON:
        return (
            "Now — which service would you like to book? "
            "For example, physiotherapy assessment, follow-up, or rehabilitation?"
        )
    if state == TRIAGE:
        return (
            "What would you like to do — book an appointment, "
            "ask about a treatment, or something else?"
        )
    return "What would you like to do next?"


# ============================================================================
# TIME / SLOT HELPERS
# ============================================================================

WEEKDAYS = {
    "monday": 0, "mon": 0, "tuesday": 1, "tue": 1, "tues": 1,
    "wednesday": 2, "wed": 2, "thursday": 3, "thu": 3, "thurs": 3,
    "friday": 4, "fri": 4, "saturday": 5, "sat": 5, "sunday": 6, "sun": 6,
}


def preference_window(pref: str) -> Optional[Tuple[int, int]]:
    p = _norm(pref)
    if "morning" in p:
        return (9, 12)
    if "afternoon" in p:
        return (12, 17)
    if "evening" in p or "after work" in p:
        return (17, 20)
    return None


def parse_specific_day_window(
    text: str,
    tz,
) -> Optional[Tuple[datetime, datetime]]:
    t = _norm(text)
    now = datetime.now(tz)

    if "today" in t:
        day = now
    elif "tomorrow" in t:
        day = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    else:
        wd = None
        for k, v in WEEKDAYS.items():
            if re.search(rf"\b{k}\b", t):
                wd = v
                break
        if wd is None:
            return None
        days_ahead = (wd - now.weekday()) % 7
        if days_ahead == 0:
            days_ahead = 7 if "next" in t else 0
        day = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=days_ahead)

    return (
        day.replace(hour=0, minute=0, second=0, microsecond=0),
        day.replace(hour=23, minute=59, second=59, microsecond=0),
    )


def widen_day_window(
    dw: Optional[Tuple[datetime, datetime]],
    attempt: int,
) -> Optional[Tuple[datetime, datetime]]:
    if not dw:
        return None
    if attempt <= 0:
        return dw
    pad = timedelta(days=1 if attempt == 1 else 3)
    return (dw[0] - pad, dw[1] + pad)

# Location selection (asked after intent is detected)
ASK_LOCATION = "ASK_LOCATION"


async def suggest_top_slots(
    session: Dict[str, Any],
    duration_min: Optional[int] = None,
    pref_text: str = "",
    day_window: Optional[Tuple[datetime, datetime]] = None,
) -> Tuple[list, list, Optional[str]]:
    clinic = get_clinic(session)
    slot_minutes = int(clinic.get("slot_minutes", DEFAULT_DURATION_MIN))
    duration_min = int(duration_min or slot_minutes)

    w_start, w_end = next_7_days_window()
    if day_window:
        w_start, w_end = day_window

    win = preference_window(pref_text)
    day_start_h, day_end_h = win if win else clinic_default_hours(clinic)

    tokens = await redis_get_json(TOKENS_KEY)
    print("CALENDAR TOKENS PRESENT:", bool(tokens))

    candidates = generate_candidate_slots(
        w_start, w_end,
        duration_min=duration_min,
        day_start_h=day_start_h,
        day_end_h=day_end_h,
    )

    if not tokens:
        top3 = pick_first_n(candidates, 3)
        if not top3:
            return [], [], "I couldn't find any slots in the next 7 days. Please tell me another day or time."
        raw = [{"start": s.isoformat(), "end": e.isoformat()} for s, e in top3]
        labels = [format_slot((s, e)) for s, e in top3]
        return raw, labels, None

    try:
        busy = freebusy(
            tokens,
            time_min=w_start,
            time_max=w_end,
            calendar_id=clinic.get("calendar_id", "primary"),
        )
    except Exception as e:
        print("CALENDAR ERROR (freebusy):", repr(e))
        return [], [], (
            "I'm having trouble checking the live calendar right now. "
            "Tell me your preferred day and I'll log a booking request for the clinic to confirm."
        )

    busy_blocks = parse_busy(busy or [])
    free_slots = filter_free_slots(candidates, busy_blocks)
    top3 = pick_first_n(free_slots, 3)

    if not top3 and win:
        dsh2, deh2 = clinic_default_hours(clinic)
        cands2 = generate_candidate_slots(w_start, w_end, duration_min=duration_min,
                                          day_start_h=dsh2, day_end_h=deh2)
        top3 = pick_first_n(filter_free_slots(cands2, busy_blocks), 3)

    if not top3:
        return [], [], "I couldn't find any free slots in the next 7 days. Would you like me to take a message for a call-back?"

    raw = [{"start": s.isoformat(), "end": e.isoformat()} for s, e in top3]
    labels = [format_slot((s, e)) for s, e in top3]
    return raw, labels, None


# ============================================================================
# RESCHEDULE HELPERS
# ============================================================================

def _safe_parse_user_datetime(text: str, tz) -> Optional[datetime]:
    try:
        from dateutil import parser as dtparser
        dt = dtparser.parse(text, fuzzy=True)
        if dt.tzinfo is None:
            dt = tz.localize(dt)
        else:
            dt = dt.astimezone(tz)
        return dt
    except Exception:
        return None


async def find_event_by_name_and_time(
    session: Dict[str, Any],
    name: str,
    when_text: str,
) -> Optional[Dict[str, Any]]:
    clinic = get_clinic(session)
    tz = get_tz(clinic)
    tokens = await redis_get_json(TOKENS_KEY)
    if not tokens:
        return None

    target_dt = _safe_parse_user_datetime(when_text, tz)
    if not target_dt:
        return None

    events = list_upcoming_events(
        stored_tokens=tokens,
        calendar_id=clinic.get("calendar_id", "primary"),
        days_ahead=60,
        max_results=50,
    )

    name_n = _norm(name)
    best, best_diff = None, 10**9

    for ev in events:
        summary = _norm(ev.get("summary") or "")
        if name_n and name_n not in summary:
            continue
        start = (ev.get("start") or {}).get("dateTime")
        if not start:
            continue
        try:
            ev_start = datetime.fromisoformat(start.replace("Z", "+00:00"))
            if ev_start.tzinfo is None:
                ev_start = tz.localize(ev_start)
            else:
                ev_start = ev_start.astimezone(tz)
            diff = abs((ev_start - target_dt).total_seconds())
            if diff <= 3 * 3600 and diff < best_diff:
                best, best_diff = ev, diff
        except Exception:
            continue

    return best


# ============================================================================
# KNOWLEDGE / LLM HELPERS
# ============================================================================

async def answer_with_knowledge(
    user_text: str,
    clinic: Dict[str, Any],
    state: str,
    session: Dict[str, Any],
) -> str:
    try:
        kb = retrieve_knowledge(user_text, clinic=clinic)
    except Exception:
        kb = ""
    try:
        llm = route_and_answer(
            user_text=((f"KNOWLEDGE:\n{kb}\n\n" if kb else "") + user_text),
            clinic=clinic,
            current_state=state,
            last_bot_prompt=session.get("last_bot_prompt", ""),
        )
        return (llm.get("reply") or "").strip()
    except Exception:
        return ""


def is_reschedule_intent(text: Optional[str]) -> bool:
    t = _norm(text)
    return any(kw in t for kw in [
        "reschedule", "rescheduling", "change my appointment",
        "move my appointment", "rebook", "switch my appointment",
        "change the time", "change the date",
    ])


# ============================================================================
# MAIN STATE MACHINE
# ============================================================================

async def triage_turn(
    user_said: str,
    session: Dict[str, Any],
    dtmf: Optional[str] = None,
) -> Tuple[str, Dict[str, Any]]:

    # Always allow interrupts
    if _is_interrupt(user_said):
        return _interrupt_reply(user_said, session)

    if not user_said:
        return _say("Sorry — I didn't catch that. Could you repeat?", session)

    clinic   = get_clinic(session)
    tz       = get_tz(clinic)
    state    = session.get("state", TRIAGE)
    collected = session.setdefault("collected", {})

    session.setdefault(LAST_OFFERED_SLOTS_KEY, None)
    session.setdefault(SELECTED_SLOT_KEY, None)
    session[LAST_USER_TEXT_KEY] = user_said

    # Detect intent once
    intent = detect_intent(user_said)

    # ------------------------------------------------------------------
    # REPEAT helper
    # ------------------------------------------------------------------
    if _norm(user_said) in ("repeat", "say again") and state in (BOOK_PICK_SLOT, RESCH_PICK_SLOT):
        return _say("Sure. Please say 1, 2, or 3.", session)

    # ------------------------------------------------------------------
    # MID-BOOKING FAQ DETOUR
    # ------------------------------------------------------------------
    if state in BOOKING_STATES and intent == "FAQ_SERVICE_EXPLAIN":
        topic = detect_service_topic(user_said)
        session["return_state"] = state
        session["faq_topic"]    = topic
        session["state"]        = FAQ_DETOUR

        svc_key = extract_service_from_question(user_said) or topic
        answer  = get_service_explanation(svc_key or "physiotherapy", "detailed")
        _say(answer, session, tone="none")
        return _say("Would you like to continue booking? Say continue, or ask another question.", session, tone="checking")

    # ------------------------------------------------------------------
    # FAQ DETOUR HANDLER
    # ------------------------------------------------------------------
    if state == FAQ_DETOUR:
        if dtmf in ("1", "2", "3"):
            return _say("You're in the help section. Say continue to go back to booking.", session, tone="checking")

        if intent == "FAQ_SERVICE_EXPLAIN":
            topic   = detect_service_topic(user_said) or session.get("faq_topic")
            svc_key = extract_service_from_question(user_said) or topic
            session["faq_topic"] = topic
            answer  = get_service_explanation(svc_key or "physiotherapy", "detailed")
            _say(answer, session, tone="none")
            return _say("You can ask another question, or say continue to go back to booking.", session, tone="checking")

        if intent == "FAQ_SERVICES":
            return _say(SERVICES_PROMPT, session, tone="none")

        if is_continue(user_said) or is_yes(user_said):
            return_state = session.get("return_state", TRIAGE)
            session["state"] = return_state
            return _say("Okay.", session, tone="ack")

        if is_no(user_said):
            session["state"] = TRIAGE
            return _say("No problem. How can I help today?", session, tone="ack")

        return _say("Say continue to go back to booking, or ask your question.", session, tone="checking")

    # ==========================================================
    # ASK_LOCATION: Which clinic? (asked after intent is detected)
    # ==========================================================
    if state == ASK_LOCATION:
        t = _norm(user_said)
        location_id = None

        # Alcester is pronounced "ALL-ster" — speech recognition produces many variants
        _alcester_variants = [
            "alcester", "alchester", "allster", "alster", "all ster",
            "all chester", "allchester", "alcesta", "all cester",
            "awlster", "olster", "alces", "ulster", "alc",
        ]
        _redditch_variants = [
            "redditch", "red ditch", "reddich", "reditch",
            "red witch", "reddit", "redich",
        ]

        if _contains_any(t, _alcester_variants) or t in ("1", "one"):
            location_id = "alcester"
        elif _contains_any(t, _redditch_variants) or t in ("2", "two"):
            location_id = "redditch"

        if not location_id:
            return _say("Sorry — could you say Alcester or Redditch?", session, tone="checking")

        session["location_id"] = location_id
        pending = session.pop("pending_intent", None)

        if pending == "BOOK":
            session["state"] = BOOK_PATIENT_TYPE
            return _say("Are you a new patient, or have you been here before?", session)

        if pending == "RESCHEDULE":
            session["state"] = RESCH_NAME
            return _say("Sure — to reschedule, what's your full name?", session)

        if pending == "CANCEL":
            session["state"] = TRIAGE
            return _say(
                "Sure — can I take your full name and the date and time of the appointment you want to cancel?",
                session,
            )

        if pending == "FAQ_INSURANCE":
            insurance_text = clinic.get("insurance_note", "Please ask the clinic about insurance.")
            session["last_faq"] = "INSURANCE"
            session["insurance_info_given"] = True
            session["insurance_last_answer"] = insurance_text
            session["state"] = INSURANCE_PROVIDER
            if not session.get("insurance_intro_done"):
                session["insurance_intro_done"] = True
                return _say(
                    f"Here's how insurance works at the clinic. {insurance_text} "
                    "If you tell me the name of your insurer, I can check that for you.",
                    session,
                )
            return _say(
                f"{insurance_text} If you tell me the name of your insurer, I can check that for you.",
                session,
            )

        if pending and pending.startswith("FAQ_"):
            session["state"] = TRIAGE
            return _say(faq_answer(pending, clinic), session)

        session["state"] = TRIAGE
        return _say("How can I help?", session)

    # ------------------------------------------------------------------
    # INSURANCE FLOW (calm, multi-step)
    # ------------------------------------------------------------------

    if state == INS_EXPLAIN:
        gather_insurer = (
            f"{INSURANCE_CHECK_WARNING} "
            "Could I ask which insurance company you're with? "
            "That way I can make a note on your booking."
        )
        if is_yes(user_said) or any(w in user_said.lower() for w in ["okay", "makes sense", "understand", "got it", "sure"]):
            session["state"] = INS_COLLECT_INSURER
            return _say(gather_insurer, session)
        answer = _answer_insurance_question(user_said)
        session["state"] = INS_COLLECT_INSURER
        return _say(f"{answer} {gather_insurer}", session)

    if state == INS_COLLECT_INSURER:
        insurer_raw = (user_said or "").strip()
        if not insurer_raw or len(insurer_raw) < 2:
            return _say(
                "Sorry — which insurance company are you with? "
                "For example, AXA, Vitality, Aviva, or WPA.",
                session, tone="checking",
            )
        if "bupa" in insurer_raw.lower():
            collected["insurer"] = "Bupa"
            session["insurance_info"]["provider"] = "Bupa"
            session["insurance_info"]["is_bupa"] = True
            session["state"] = INS_BUPA_RESPONSE
            return _say(INSURANCE_BUPA_RESPONSE, session, tone="none")
        insurer_name = extract_insurer_name(insurer_raw)
        collected["insurer"] = insurer_name
        session["insurance_info"]["provider"] = insurer_name
        session["insurance_info"]["is_bupa"] = False
        session["state"] = INS_COLLECT_POLICY
        response = (
            f"Thank you — I'll make a note that you're with {insurer_name}. "
            f"{INSURANCE_CHECK_WARNING} "
            "Would you happen to have your policy number handy? "
            "It's not essential right now — just helpful to have on file."
        )
        return _say(response, session, tone="none")

    if state == INS_BUPA_RESPONSE:
        if is_yes(user_said) or "still" in user_said.lower() or "book" in user_said.lower():
            session["insurance_info"]["pays_privately"] = True
            session["state"] = TRIAGE
            return _say("Wonderful. Not a problem at all. Shall we go ahead and get you booked in?", session, tone="ack")
        if is_no(user_said) or "think" in user_said.lower():
            session["state"] = TRIAGE
            return _say(
                "Of course — absolutely no pressure. "
                "If you'd like, I can take your name and number and have someone from the team call you back. "
                "Would that be helpful?",
                session, tone="none",
            )
        return _say("Would you still like to go ahead and book privately, or would you prefer some time to think it over?", session, tone="checking")

    if state == INS_COLLECT_POLICY:
        no_policy = any(p in user_said.lower() for p in [
            "don't have", "not sure", "don't know", "haven't got",
            "no", "skip", "later", "fine", "not to hand",
        ])
        if not no_policy:
            collected["policy_number"] = user_said.strip()
            session["insurance_info"]["policy_number"] = user_said.strip()
        else:
            session["insurance_info"]["policy_number"] = None
        session["insurance_info"]["confirmed"] = True
        try:
            await _send_insurance_staff_notification(session)
        except Exception:
            pass
        insurer = session["insurance_info"].get("provider", "your insurer")
        policy_text = (
            f"policy number {collected.get('policy_number')}"
            if collected.get("policy_number")
            else "no policy number on file"
        )
        session["state"] = TRIAGE
        return _say(
            f"Brilliant. I've noted {insurer} and {policy_text} on your booking. "
            "The team will be aware when you come in and will make sure "
            "you get everything you need to submit your claim. "
            "Do check with your insurer beforehand if you can, just so there are no surprises. "
            "Shall we go ahead and get you booked in?",
            session, tone="none",
        )

    # ------------------------------------------------------------------
    # TRIAGE: main routing
    # ------------------------------------------------------------------
    if state == TRIAGE:

        # Reschedule keyword intercept
        if is_reschedule_intent(user_said):
            session = _reset_to_triage(session)
            session["pending_intent"] = "RESCHEDULE"
            session["state"] = ASK_LOCATION
            return _say("Are you calling about our Alcester or Redditch clinic?", session)

        # Improvement 5: Insurance trigger → calm multi-step flow
        if intent == "FAQ_INSURANCE":
            session.setdefault("insurance_info", {})
            session["insurance_info"]["mentioned"] = True

            # Check if Bupa mentioned directly
            if "bupa" in user_said.lower():
                session["insurance_info"]["provider"] = "Bupa"
                session["insurance_info"]["is_bupa"]  = True
                session["state"] = INS_BUPA_RESPONSE
                return _say(INSURANCE_BUPA_RESPONSE, session, tone="none")

            session["state"] = INS_EXPLAIN
            return _say(
                f"Of course — let me explain how insurance works with us. "
                f"{INSURANCE_EXPLAIN} "
                f"{INSURANCE_BUPA_WARNING} "
                f"For all other insurers, it's worth checking with your provider "
                f"whether they cover self-pay physiotherapy claims. "
                f"Does that all make sense?",
                session, tone="none",
            )

        # Improvement 4: Condition → treatment knowledge
        if intent == "OTHER" or intent == "FAQ_SERVICES":
            condition = identify_condition(user_said)
            if condition:
                session["identified_condition"] = True
                session["condition_text"]       = condition["text"]
                session["condition_options"]    = condition["options"]
                return _say(
                    f"{condition['text']} {condition['follow_up']}",
                    session, tone="none",
                )

        # Improvement 3: Services question → gentle prompt
        if intent == "FAQ_SERVICES":
            return _say(SERVICES_PROMPT, session, tone="none")

        # Improvement 6: Service explanation
        if intent == "FAQ_SERVICE_EXPLAIN":
            svc_key = extract_service_from_question(user_said)
            answer  = get_service_explanation(svc_key or "physiotherapy", "detailed")
            return _say(answer, session, tone="none")

        # Location-aware quick FAQs (hours, address)
        if intent == "FAQ_HOURS":
            return _say(faq_answer("FAQ_HOURS", clinic, session), session, tone="none")

        if intent == "FAQ_LOCATION":
            return _say(faq_answer("FAQ_LOCATION", clinic, session), session, tone="none")

        # Try LLM route
        try:
            kb = retrieve_knowledge(user_said, clinic=clinic)
        except Exception:
            kb = ""

        try:
            llm = route_and_answer(
                user_text=((f"KNOWLEDGE:\n{kb}\n\n" if kb else "") + user_said),
                clinic=clinic,
                current_state=state,
                last_bot_prompt=session.get(LAST_BOT_PROMPT_KEY, ""),
            )
            llm_intent = (llm.get("intent") or "").strip()
            conf       = float(llm.get("confidence") or 0.0)
            reply      = (llm.get("reply") or "").strip()
            follow     = (llm.get("follow_up_question") or "").strip()

            if conf >= 0.55:
                if llm_intent == "BOOK":
                    session = _reset_to_triage(session)
                    session["pending_intent"] = "BOOK"
                    session["state"] = ASK_LOCATION
                    return _say("Are you calling about our Alcester or Redditch clinic?", session)

                if llm_intent == "RESCHEDULE":
                    session = _reset_to_triage(session)
                    session["pending_intent"] = "RESCHEDULE"
                    session["state"] = ASK_LOCATION
                    return _say("Are you calling about our Alcester or Redditch clinic?", session)

                if llm_intent == "HUMAN":
                    _attempt_send_to_sheet(collected, user_said, session, "CALLBACK")
                    return _say(
                        "No problem — please say your name, number, "
                        "and what you need help with, and the team will call you back.",
                        session,
                    )

                if reply and llm_intent in ("FAQ", "OTHER", "MESSAGE"):
                    return _say(f"{reply} {follow}".strip(), session)

        except Exception:
            pass

        # Deterministic fallback routing
        if intent == "BOOK":
            session = _reset_to_triage(session)
            session["pending_intent"] = "BOOK"
            session["state"] = ASK_LOCATION
            return _say("Are you calling about our Alcester or Redditch clinic?", session)

        if intent2 == "RESCHEDULE":
            session = _reset_to_triage(session)
            session["pending_intent"] = "RESCHEDULE"
            session["state"] = ASK_LOCATION
            return _say("Are you calling about our Alcester or Redditch clinic?", session)

        if intent2 == "FAQ_SERVICE_EXPLAIN":
            topic = detect_service_topic(user_said)
            answer = faq_answer_service("FAQ_SERVICE_EXPLAIN", text=user_said, topic=topic)
            return _say(answer, session, tone="none")

        if intent2 == "FAQ_INSURANCE":
            session["pending_intent"] = "FAQ_INSURANCE"
            session["state"] = ASK_LOCATION
            return _say("Are you calling about our Alcester or Redditch clinic?", session)

        if intent2 == "CANCEL":
            session["pending_intent"] = "CANCEL"
            session["state"] = ASK_LOCATION
            return _say("Are you calling about our Alcester or Redditch clinic?", session)

        if intent2 == "HUMAN":
            _attempt_send_to_sheet(collected, user_said, session, "CALLBACK")
            return _say(
                "No problem — please say your name, number, and what you need help with, "
                "and the team will call you back.",
                session,
            )

        if intent2.startswith("FAQ_"):
            return _say(faq_answer(intent2, clinic, session), session)

        # Final fallback
        return _say(
            "I can help with booking, treatment information, "
            "opening hours, location, pricing, or insurance. "
            "What would you like to know?",
            session,
        )

    # ------------------------------------------------------------------
    # INSURANCE_PROVIDER (legacy state — redirect to new flow)
    # ------------------------------------------------------------------
    if state == INSURANCE_PROVIDER_STATE:
        session["state"] = INS_COLLECT_INSURER
        return await triage_turn(user_said, session, dtmf)

    if state == "CANCELLATION_COLLECT":
        collected["cancellation_details"] = user_said.strip()

        try:
            from app.notifications.booking_sms import send_cancellation_confirmation
            from datetime import datetime

            appointment_time = session.get("cancelled_appointment_time")
            if appointment_time:
                appointment_dt = datetime.fromisoformat(appointment_time)
                now = datetime.utcnow()
                hours_until = (appointment_dt - now).total_seconds() / 3600
                is_late = hours_until < 24

                await send_cancellation_confirmation(
                    patient_phone=collected.get("phone", "+447870166861"),
                    patient_name=collected.get("name", "").split()[0] or "Patient",
                    appointment_time=appointment_dt,
                    is_late_cancellation=is_late,
                )
        except Exception as e:
            logger.error(f"⚠️ Cancellation SMS failed: {e}")

        session = _reset_to_triage(session)
        return _say("Your appointment has been cancelled. You should receive a confirmation text shortly.", session)

    # ------------------------------------------------------------------
    # RESCHEDULE FLOW
    # ------------------------------------------------------------------
    if state == RESCH_NAME:
        if detect_intent(user_said) in ("RESCHEDULE", "BOOK", "CANCEL"):
            return _say("Sure — what's your full name?", session)
        collected["name"] = user_said.strip()
        session["state"]  = RESCH_ORIGINAL
        return _say(f"{random.choice(FRIENDLY_ACK)} What was the date and time of your original appointment?", session)

    if state == RESCH_ORIGINAL:
        collected["original_appt"] = user_said.strip()
        try:
            ev = await find_event_by_name_and_time(session, collected.get("name", ""), collected["original_appt"])
        except Exception as e:
            print("RESCHEDULE find event error:", repr(e))
            ev = None

        if ev:
            session["resch_event_id"]      = ev.get("id")
            session["resch_event_summary"] = ev.get("summary", "Appointment")
            session["state"]               = RESCH_NEW_PREF
            return _say("Thanks — tell me a day or time that suits you and I'll check availability.", session)

        tokens = await redis_get_json(TOKENS_KEY)
        if tokens:
            session["state"] = RESCH_PHONE_FALLBACK
            return _say("Thanks. What phone number was used for the booking?", session)

        session["manual_reschedule"] = True
        session["manual_reason"]     = "no_calendar_tokens"
        session["state"]             = RESCH_NEW_PREF
        return _say("No problem — tell me a day or time that suits you.", session)

    if state == RESCH_PHONE_FALLBACK:
        if not is_valid_phone(user_said):
            return _say("Sorry — I didn't catch a valid phone number. Please say it again.", session)
        collected["phone"] = normalize_phone(user_said)
        tokens = await redis_get_json(TOKENS_KEY)
        if tokens:
            try:
                events = list_upcoming_events(
                    stored_tokens=tokens,
                    calendar_id=get_clinic(session).get("calendar_id", "primary"),
                    days_ahead=60, max_results=50,
                )
                target = collected["phone"]
                ev = next((e for e in events if target in _digits_only(e.get("description") or "")), None)
                if ev:
                    session["resch_event_id"]      = ev.get("id")
                    session["resch_event_summary"] = ev.get("summary", "Appointment")
                else:
                    session["manual_reschedule"] = True
                    session["manual_reason"]     = "event_not_found"
            except Exception as e:
                print("RESCHEDULE list_upcoming_events error:", repr(e))
                session["manual_reschedule"] = True
                session["manual_reason"]     = "calendar_lookup_error"
        else:
            session["manual_reschedule"] = True
            session["manual_reason"]     = "no_calendar_tokens"

        session["state"] = RESCH_NEW_PREF
        return _say("Thanks — tell me a day or time that suits you.", session)

    if state == RESCH_NEW_PREF:
        pref = (user_said or "").strip()
        pref_attempts = int(session.get("resch_pref_attempts", 0))

        if not pref:
            session["resch_pref_attempts"] = pref_attempts + 1
            return _say("What day or time would you like? For example, next Monday afternoon.", session, tone="checking")

        collected["time_pref"] = pref
        dw = parse_specific_day_window(collected["time_pref"], tz)
        dw_parsed = None
        if dw:
            collected["day_window_start"] = dw[0].isoformat()
            collected["day_window_end"]   = dw[1].isoformat()
            dw_parsed = dw

        duration = int(get_clinic(session).get("slot_minutes", DEFAULT_DURATION_MIN))
        raw_slots, labels, err = await suggest_top_slots(session, duration_min=duration,
                                                          pref_text=pref, day_window=dw_parsed)

        if err:
            session["manual_reschedule"] = True
            session["manual_reason"]     = "calendar_unavailable"
            booking_url = (get_clinic(session).get("booking_url") or "").strip()
            session = _reset_to_triage(session)
            if booking_url:
                return _say(f"I'm having trouble checking availability. Please use our online booking system: {booking_url}", session, tone="reassure")
            return _say("I'm having trouble checking availability. The clinic team will follow up with you.", session, tone="reassure")

        if not labels or len(labels) < 3:
            widen_attempt = int(session.get("resch_widen_attempts", 0)) + 1
            session["resch_widen_attempts"] = widen_attempt
            if dw_parsed and widen_attempt <= 2:
                widened = widen_day_window(dw_parsed, widen_attempt)
                raw_slots, labels, err = await suggest_top_slots(session, duration_min=duration,
                                                                   pref_text=pref, day_window=widened)

            if not labels or len(labels) < 3:
                session["resch_pref_attempts"] = pref_attempts + 1
                if session["resch_pref_attempts"] >= 2:
                    session = _reset_to_triage(session)
                    return _say("I can't see a clear match for that time. Could you suggest another day or time?", session, tone="reassure")
                return _say("I don't have clear availability around that time. Could you tell me another day that would suit?", session, tone="checking")

        session["resch_pref_attempts"]  = 0
        session["resch_widen_attempts"] = 0
        session[LAST_OFFERED_SLOTS_KEY] = raw_slots
        session[SLOT_LABELS_KEY]        = labels
        session["state"]                = RESCH_PICK_SLOT

        return _say(
            f"I have three options. "
            f"The first is {labels[0]}. "
            f"The second is {labels[1]}. "
            f"The third is {labels[2]}. "
            "Please say 1, 2, or 3.",
            session,
        )

    if state == RESCH_PICK_SLOT:
        slots  = session.get(LAST_OFFERED_SLOTS_KEY) or []
        labels = session.get(SLOT_LABELS_KEY) or []

        if not slots:
            session["state"] = TRIAGE
            return _say("I can't see any available slots right now. The clinic will contact you to reschedule.", session, tone="reassure")

        choice = parse_slot_choice(user_said, dtmf=dtmf)
        if not choice:
            return _say(
                f"The first option is {labels[0]}. "
                f"The second is {labels[1]}. "
                f"The third is {labels[2]}. "
                "Please say 1, 2, or 3.",
                session,
            )

        idx = choice - 1
        if idx < 0 or idx >= len(slots):
            return _say("Sorry — please say 1 for the first option, 2 for the second, or 3 for the third.", session)

        session[SELECTED_SLOT_KEY]       = slots[idx]
        session[SELECTED_SLOT_LABEL_KEY] = labels[idx] if idx < len(labels) else ""
        session["state"]                 = RESCH_CONFIRM

        return _say(
            f"You've chosen {labels[idx]}. "
            "Shall I go ahead and reschedule that for you? Say yes to confirm or no to cancel.",
            session,
        )
    if state == RESCH_CONFIRM:
        if not is_yes(user_said):
            session = _reset_to_triage(session)
            return _say("No problem. What would you like to do instead?", session)
        label  = session.get(SELECTED_SLOT_LABEL_KEY) or collected.get("time_pref") or "the new time"
        tokens = await redis_get_json(TOKENS_KEY)
        if session.get("manual_reschedule"):
            _attempt_send_to_sheet(
                collected, user_said, session, "RESCHEDULE_REQUEST_MANUAL",
                extra=f"Original: {collected.get('original_appt','')} → New: {label}",
            )
            session = _reset_to_triage(session)
            return _say(f"I've logged your reschedule request for {label}. The clinic will confirm it shortly.", session)
        chosen   = session.get(SELECTED_SLOT_KEY)
        event_id = session.get("resch_event_id")
        if tokens and event_id and chosen:
            try:
                start = datetime.fromisoformat(chosen["start"])
                end   = datetime.fromisoformat(chosen["end"])
                patch_event_time(
                    stored_tokens=tokens,
                    event_id=event_id,
                    start_dt=start,
                    end_dt=end,
                    calendar_id=get_clinic(session).get("calendar_id", "primary"),
                )
                try:
                    from app.notifications.booking_sms import send_reschedule_confirmation

                    old_time_str = collected.get("original_appt")
                    if old_time_str:
                        old_time = _safe_parse_user_datetime(old_time_str, tz)
                        await send_reschedule_confirmation(
                            patient_phone=collected.get("phone", "+447870166861"),
                            patient_name=collected.get("name", "").split()[0] or "Patient",
                            old_time=old_time,
                            new_time=start,
                            location=get_location_label(session),
                        )
                        logger.info(f"✅ Reschedule SMS sent")
                except Exception as e:
                    logger.error(f"⚠️ Reschedule SMS failed: {e}")
            except Exception:
                session = _reset_to_triage(session)
                return _say(f"I've logged your reschedule for {label}. The clinic will confirm it.", session)
        session = _reset_to_triage(session)
        return _say(f"Confirmed — you're rescheduled to {label}. We look forward to seeing you.", session)
   

    # ------------------------------------------------------------------
    # BOOKING FLOW
    # ------------------------------------------------------------------
    if state == BOOK_PATIENT_TYPE:
        intent_check = detect_intent(user_said)
        if intent_check in ("RESCHEDULE", "CANCEL"):
            session = _reset_to_triage(session)
            session["state"] = RESCH_NAME if intent_check == "RESCHEDULE" else TRIAGE
            return _say("No problem — do you want to reschedule or cancel an appointment?", session, tone="ack")

        pt = parse_patient_type(user_said)
        if pt:
            session["pt_type_tries"]      = 0
            collected["patient_type"]     = pt
            session["state"]              = BOOK_REASON
            return _say("Great. What's the appointment for?", session, tone="ack")

        if looks_like_name(user_said) and not collected.get("name"):
            collected["name"] = user_said.strip()
            return _say(
                "Thanks. Are you a new patient or have you been here before? "
                "You can say new patient or returning patient.",
                session, tone="checking",
            )

        tries = int(session.get("pt_type_tries", 0)) + 1
        session["pt_type_tries"] = tries
        if tries >= 2:
            return _say("Sorry — please say new or returning. Or press 1 for new, 2 for returning.", session, tone="checking")
        return _say("Are you a new patient or a returning patient?", session, tone="checking")

    if state == BOOK_REASON:
        lower = (user_said or "").lower()

        # Service info question mid-booking → detour
        if any(q in lower for q in ["what is", "what's", "tell me more", "how does", "explain", "does it hurt"]):
            svc_key = extract_service_from_question(user_said)
            answer  = get_service_explanation(svc_key or "physiotherapy", "detailed")
            return _say(f"{answer} {resume_prompt_for_state(BOOK_REASON)}", session)

        # Condition mention → give recommendation then continue
        condition = identify_condition(user_said)
        if condition:
            collected["reason"] = user_said.strip()
            session["state"]    = BOOK_TIME_PREF
            return _say(
                f"{condition['text']} "
                f"Let's get you booked in. "
                f"What day or time would suit you?",
                session, tone="none",
            )

        collected["reason"] = (user_said or "").strip()
        session["state"]    = BOOK_TIME_PREF
        return _say("Thanks. What day or time would you prefer?", session)

    if state == BOOK_TIME_PREF:
        collected["time_pref"] = user_said.strip()
        dw = parse_specific_day_window(collected["time_pref"], tz)
        dw_parsed = None
        if dw:
            collected["day_window_start"] = dw[0].isoformat()
            collected["day_window_end"]   = dw[1].isoformat()
            dw_parsed = dw

        raw_slots, labels, err = await suggest_top_slots(
            session,
            duration_min=int(get_clinic(session).get("slot_minutes", DEFAULT_DURATION_MIN)),
            pref_text=collected.get("time_pref", ""),
            day_window=dw_parsed,
        )

        if err:
            session["manual_booking"] = True
            session["manual_reason"]  = "calendar_unavailable"
            session["state"]          = BOOK_NAME
            return _say(f"{err} What's your full name so I can log a booking request?", session)

        if not labels or len(labels) < 3:
            session["manual_booking"] = True
            session["manual_reason"]  = "no_slots_returned"
            session["state"]          = BOOK_NAME
            return _say("I can't see clear availability right now. What's your full name so I can log a request?", session)

        session[LAST_OFFERED_SLOTS_KEY] = raw_slots
        session[SLOT_LABELS_KEY]        = labels
        session["state"]                = BOOK_PICK_SLOT

        return _say(
            f"I have three options. "
            f"The first is {labels[0]}. "
            f"The second is {labels[1]}. "
            f"The third is {labels[2]}. "
            "Please say 1 for the first option, 2 for the second, or 3 for the third.",
            session,
        )

    if state == BOOK_PICK_SLOT:
        choice = parse_slot_choice(user_said, dtmf=dtmf)
        if not choice:
            return _say("Sorry — please say 1 for the first option, 2 for the second, or 3 for the third.", session)

        idx    = choice - 1
        slots  = session.get(LAST_OFFERED_SLOTS_KEY) or []
        labels = session.get(SLOT_LABELS_KEY) or []

        if idx < 0 or idx >= len(slots):
            return _say("Sorry — please say 1, 2, or 3.", session)

        session[SELECTED_SLOT_KEY]       = slots[idx]
        session[SELECTED_SLOT_LABEL_KEY] = labels[idx] if idx < len(labels) else ""
        session["state"]                 = BOOK_NAME
        return _say("Perfect. What's your full name for the booking?", session)

    if state == BOOK_NAME:
        collected["name"] = user_said.strip()
        session["state"]  = BOOK_PHONE
        return _say("Thanks. What's the best mobile number for us to reach you on?", session)

    if state == BOOK_PHONE:
        if not is_valid_phone(user_said):
            return _say("Sorry — I didn't catch a valid phone number. Please say it again.", session)
        collected["phone"] = normalize_phone(user_said)
        session["state"]   = BOOK_CONFIRM
        return _say("Great. Say yes to confirm the booking, or no to cancel.", session)

    if state == BOOK_CONFIRM:
        if is_no(user_said):
            session = _reset_to_triage(session)
            return _say("No problem — I've cancelled that. What would you like to do instead?", session)

        if not is_yes(user_said):
            return _say("Just to confirm — shall I go ahead and book that? Say yes or no.", session)

        chosen = session.get(SELECTED_SLOT_KEY)
        label  = session.get(SELECTED_SLOT_LABEL_KEY) or "the selected time"
        tokens = await redis_get_json(TOKENS_KEY)

        if tokens and chosen:
            try:
                start = datetime.fromisoformat(chosen["start"])
                end   = datetime.fromisoformat(chosen["end"])
                clinic_obj = get_clinic(session)

                description_lines = [
                    f"Patient status: {collected.get('patient_type', '')}" if collected.get("patient_type") else "",
                    f"Reason: {collected.get('reason', '')}" if collected.get("reason") else "",
                    f"Location: {get_location_label(session)}",
                    f"Insurer: {collected.get('insurer', 'N/A')}",
                    f"Policy: {collected.get('policy_number', 'N/A')}",
                    f"Clinic: {clinic_obj.get('display_name', '')}",
                    f"CallSid: {session.get('call_sid', '')}",
                    "Booked via Theorem AI receptionist.",
                ]
                description = "\n".join(l for l in description_lines if l)

                name    = (collected.get("name") or "Patient").strip()
                phone   = (collected.get("phone") or "").strip()
                summary = name + (f" ({phone})" if phone else "")

                event = create_event(
                    stored_tokens=tokens,
                    start_dt=start,
                    end_dt=end,
                    summary=summary,
                    description=description,
                    calendar_id=os.getenv("GOOGLE_CALENDAR_ID", "primary"),
                )

                if event and event.get("id"):
                    try:
                        from app.notifications.booking_sms import send_booking_confirmation

                        await send_booking_confirmation(
                            patient_phone=collected["phone"],
                            patient_name=collected["name"].split()[0],
                            appointment_time=start,
                            location=get_location_label(session),
                            service="physiotherapy",
                            practitioner="Mark",
                            is_new_patient=(collected.get("patient_type") == "NEW"),
                            has_insurance=bool(collected.get("insurer")),
                            insurer=collected.get("insurer"),
                        )
                        logger.info(f"✅ Booking confirmation SMS sent to {collected['phone']}")
                    except Exception as e:
                        logger.error(f"⚠️ Failed to send booking SMS: {e}")
                        # Don't fail the booking if SMS fails - just log it

                session = _reset_to_triage(session)
                return _say(f"Confirmed — you're booked for {label}. We look forward to seeing you.", session)

            except Exception as e:
                print("BOOKING CREATE EVENT ERROR:", repr(e))
                session = _reset_to_triage(session)
                if not event or not event.get("id"):
                    return _say("I couldn't create the booking. Please try again.", session)
                return _say(f"Confirmed — you're booked for {label}. We look forward to seeing you.", session)

        session = _reset_to_triage(session)
        return _say(f"Confirmed — you're booked for {label}. We look forward to seeing you.", session)


# ============================================================================
# PRIVATE HELPERS
# ============================================================================

def _answer_insurance_question(question: str) -> str:
    """Answer specific insurance questions calmly."""
    q = question.lower()

    if any(p in q for p in ["document", "receipt", "invoice", "paperwork"]):
        return (
            "We'll provide you with a detailed receipt after each session "
            "showing the date, treatment, and amount paid. "
            "If your insurer needs anything additional, like a clinical report, we can arrange that."
        )
    if any(p in q for p in ["how do i claim", "claim back", "get refund", "how to claim"]):
        return (
            "You pay us after your session, we give you a receipt, "
            "and you submit that to your insurer with their standard claim form. "
            "Most insurers process these within a few weeks."
        )
    if any(p in q for p in ["will they cover", "will insurance cover", "covered"]):
        return (
            "That's something we can't say for certain — every policy is different. "
            "We'd recommend calling your insurer directly to check."
        )
    if any(p in q for p in ["how much", "full amount", "percentage"]):
        return (
            "It really depends on your policy. "
            "Some insurers cover the full amount, others cover a percentage. "
            "Your insurer will be able to tell you exactly what's covered."
        )
    if any(p in q for p in ["referral", "gp", "doctor", "letter from"]):
        return (
            "You don't need a GP referral to see us. "
            "However, some insurance policies require one before they'll cover the cost — "
            "worth checking with your insurer."
        )
    return (
        "That's a good question. "
        "It really does depend on your specific policy. "
        "We'd recommend checking with your insurer to get the most accurate answer."
    )


async def _send_insurance_staff_notification(session: Dict[str, Any]) -> None:
    """Send staff SMS notification when an insurance booking is made."""
    try:
        from app.notifications.sms import send_sms
        staff_phone  = os.getenv("THEOREM_NOTIFICATION_SMS", "+447870166861")
        ins_info     = session.get("insurance_info", {})
        collected    = session.get("collected", {})
        patient_name = f"{collected.get('first_name','')} {collected.get('last_name','')}".strip()
        patient_phone = collected.get("phone", "Not provided")
        insurer      = ins_info.get("provider", "Unknown")
        policy       = ins_info.get("policy_number") or "Not provided"
        policy_text  = f"Policy: {policy}"

        message = (
            f"📋 Insurance Booking\n\n"
            f"Patient: {patient_name or 'TBC'}\n"
            f"Phone: {patient_phone}\n"
            f"Insurer: {insurer}\n"
            f"{policy_text}\n\n"
            f"⚠️ Self-pay + claim back.\n"
            f"Confirm coverage with insurer.\n"
            f"Provide full receipt after session."
        )
        await send_sms(to=staff_phone, message=message)
    except Exception as e:
        print("INSURANCE STAFF NOTIFICATION ERROR:", repr(e))


def _attempt_send_to_sheet(
    collected: Dict,
    user_said: str,
    session: Dict,
    intent_label: str,
    extra: str = "",
) -> None:
    """Fire-and-forget sheet logging."""
    if send_to_sheet is None:
        return
    try:
        send_to_sheet(
            name=collected.get("name", ""),
            phone=collected.get("phone", ""),
            intent=intent_label,
            message=user_said + (f" | {extra}" if extra else ""),
            call_sid=session.get("call_sid", ""),
        )
    except Exception:
        pass
