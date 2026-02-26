# app/flows/triage.py
from __future__ import annotations

print("✅ LOADED TRIAGE FROM:", __file__)

# ============================================================================
# IMPORTS
# ============================================================================

import logging
import os
import re
import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

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
def _insurance_explain(clinic_name: str) -> str:
    return (
        f"So, here's how it works at {clinic_name}. "
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

    # Longest phrases first to prevent partial matches
    fillers = [
        "my insurance provider is", "my insurance company is", "i am covered by",
        "i'm covered by", "i am insured with", "i'm insured with",
        "i am insured by", "i'm insured by", "my insurer is", "i am with",
        "my insurance is", "i have insurance with", "i'm with", "i've got",
        "i have", "covered by", "insured by", "insured with", "it's", "with",
    ]
    cleaned = speech_lower
    for f in fillers:
        if cleaned.startswith(f):
            cleaned = cleaned[len(f):].strip()
            break
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
BOOK_INTAKE         = "BOOK_INTAKE"    # follow-up clinical question about the condition
BOOK_RECOMMEND      = "BOOK_RECOMMEND" # present recommended service + price, get confirmation
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

# Fallback state — entered when the system can't complete a flow normally
# (e.g. repeated technical errors, call drop risk). Reads back collected
# info, asks "say yes" to pass it on to the clinic team manually.
MANUAL_CAPTURE      = "MANUAL_CAPTURE"

BOOKING_STATES = {
    BOOK_PATIENT_TYPE, BOOK_REASON, BOOK_INTAKE, BOOK_RECOMMEND,
    BOOK_TIME_PREF, BOOK_PICK_SLOT, BOOK_NAME, BOOK_PHONE, BOOK_CONFIRM,
}

RESCH_STATES = {
    RESCH_NAME, RESCH_ORIGINAL, RESCH_PHONE_FALLBACK,
    RESCH_NEW_PREF, RESCH_PICK_SLOT, RESCH_CONFIRM,
}

# States that get mid-flow switch protection (excludes TRIAGE and ASK_LOCATION)
_SWITCHABLE_BOOKING = BOOKING_STATES - {BOOK_PATIENT_TYPE}  # BOOK_PATIENT_TYPE already has its own guard
_SWITCHABLE_RESCH   = RESCH_STATES
_SWITCHABLE_ALL     = _SWITCHABLE_BOOKING | _SWITCHABLE_RESCH

# Option 3: explicit "I want to switch" phrases — unambiguous, always trigger a flow switch
_EXPLICIT_SWITCH_PHRASES = [
    "actually i want to", "actually i need to", "actually i would like to",
    "actually can i", "actually could i", "wait i want to", "wait i need to",
    "let me reschedule instead", "i want to reschedule instead", "reschedule instead",
    "instead of booking", "i want to cancel instead", "actually cancel",
    "forget the booking", "forget this booking", "never mind the booking",
    "i changed my mind", "changed my mind", "different thing", "something different",
]


def _is_explicit_switch(text: str) -> bool:
    """Return True if the user used an unambiguous 'I want to switch flow' phrase."""
    t = _norm(text)
    return any(phrase in t for phrase in _EXPLICIT_SWITCH_PHRASES)


def _soft_reset_session(session: Dict[str, Any]) -> Dict[str, Any]:
    """
    Clear flow-specific data but KEEP personal info already given by the caller:
    name, phone, patient_type, insurer, policy_number, location.
    Used when switching flows mid-conversation so the caller doesn't have to
    repeat themselves.
    """
    collected = session.get("collected", {}) or {}
    keep_keys = {"name", "phone", "patient_type", "insurer", "policy_number"}
    session["collected"] = {k: v for k, v in collected.items() if k in keep_keys}
    session[LAST_OFFERED_SLOTS_KEY] = None
    session[SELECTED_SLOT_KEY] = None
    for k in (
        "resch_event_id", "resch_event_summary", SLOT_LABELS_KEY,
        SELECTED_SLOT_LABEL_KEY, "manual_booking", "manual_reason",
        "manual_reschedule", "pending_intent", "pt_type_tries",
    ):
        session.pop(k, None)
    return session


def _build_manual_capture_message(session: Dict[str, Any], clinic: Dict[str, Any]) -> str:
    """
    Build a natural-language summary of everything collected so far,
    read back to the caller before manual handoff.
    Covers booking, reschedule, general enquiry, and condition-only calls.
    """
    collected  = session.get("collected", {}) or {}
    pre_state  = session.get("pre_error_state", session.get("state", "TRIAGE"))

    name         = (collected.get("name") or "").strip()
    reason       = (collected.get("reason") or "").strip()
    time_pref    = (collected.get("time_pref") or "").strip()
    selected     = (
        session.get("selected_slot_label") or
        session.get(SELECTED_SLOT_LABEL_KEY) or
        collected.get("selected_slot") or ""
    )
    insurer      = (collected.get("insurer") or "").strip()
    patient_type = (collected.get("patient_type") or "").upper()

    # Determine what the caller was trying to do
    if any(pre_state.startswith(p) for p in ("BOOK", "RESCH")):
        action = "reschedule" if pre_state.startswith("RESCH") else "book an appointment"
    elif any(pre_state.startswith(p) for p in ("INS",)):
        action = "ask about insurance"
    else:
        action = "get in touch"

    parts: list = []

    # Opening
    if action == "book an appointment":
        opener = "Okay — I've got you looking to book an appointment"
        if reason:
            opener += f" for {reason}"
        if selected:
            opener += f" at {selected}"
        elif time_pref:
            opener += f" around {time_pref}"
        parts.append(opener)
    elif action == "reschedule":
        opener = "Okay — I've got you looking to reschedule your appointment"
        if selected:
            opener += f" to {selected}"
        elif time_pref:
            opener += f" to around {time_pref}"
        parts.append(opener)
    elif reason:
        parts.append(f"Okay — I've got a note that you called about {reason}")
    else:
        parts.append("Okay — I've got a note that you were in touch with us")

    # Personal details
    details: list = []
    if name:
        details.append(f"your name is {name}")
    if patient_type == "NEW":
        details.append("you're a new patient")
    elif patient_type == "RETURNING":
        details.append("you're a returning patient")
    if insurer:
        details.append(f"you're with {insurer}")
    if details:
        parts.append(", ".join(details))

    body = ". ".join(parts).strip()
    if not body.endswith("."):
        body += "."

    return (
        f"{body} "
        "Say yes and I'll pass all of that on to the clinic team right now — "
        "they'll be in touch to confirm everything."
    )


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
    """Return E.164 format. UK mobiles 07xxx → +447xxx."""
    digits = _digits_only(phone)
    if digits.startswith("07") and len(digits) == 11:
        return "+44" + digits[1:]
    if digits.startswith("44") and 11 <= len(digits) <= 13:
        return "+" + digits
    if digits.startswith("0") and 10 <= len(digits) <= 11:
        # Generic UK landline / other format
        return "+44" + digits[1:]
    if digits and not phone.strip().startswith("+"):
        return digits   # leave as-is if we can't determine country
    return digits


def is_valid_phone(phone: str) -> bool:
    p = _digits_only(phone)
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
    for p in (
        "returning", "existing", "been before", "been here before",
        "come here before", "came before", "been a patient", "previous patient",
        "already a patient", "already been", "been with you", "visited before",
        "follow up", "followup", "follow-up", "seen you before",
        "seen before", "came here", "i have been", "i ve been",
    ):
        if p in t:
            return "RETURNING"
    for p in (
        "new", "first time", "first visit", "never been", "initial",
        "brand new", "first appointment", "haven t been", "have not been",
        "never visited", "my first", "haven t visited",
    ):
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
    # Word → number map.
    # "free" and "tree" are common Twilio STT mishearings of "three" in British English.
    # Longer/more specific phrases are checked first (order matters — iterate longest first).
    word_map = [
        # Two
        ("option two",   2), ("number two",   2),
        # Three / STT variants
        ("option three", 3), ("number three", 3), ("option free",  3),
        ("number free",  3), ("last one",     3),
        # Single words — checked after phrases
        ("first",  1), ("one",   1),
        ("second", 2), ("two",   2),
        ("third",  3), ("three", 3),
        ("free",   3),  # British STT mishearing of "three"
        ("tree",   3),  # Another STT variant
        ("last",   3),  # "the last one"
    ]
    for w, n in word_map:
        if re.search(rf"\b{re.escape(w)}\b", t):
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
        clinic_name = clinic.get("name", "the clinic")
        return (
            f"{_insurance_explain(clinic_name)} "
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
            "Now — what would you like to book? "
            "For example, you could say lower back pain, sports injury, or physiotherapy."
        )
    if state == BOOK_INTAKE:
        return (
            "Just to follow up — how long has that been going on, "
            "and is it constant or does it come and go?"
        )
    if state == BOOK_RECOMMEND:
        return "Shall I go ahead and book you in for that?"
    if state == TRIAGE:
        return (
            "What would you like to do — book an appointment, "
            "ask about a treatment, or something else?"
        )
    return "What would you like to do next?"


# ============================================================================
# INTAKE / RECOMMENDATION HELPERS
# ============================================================================

def _intake_question_for_condition(reason: str) -> Optional[str]:
    """
    Return a targeted clinical follow-up question based on the patient's stated reason,
    or None if the condition doesn't match any known category.
    When None is returned, the caller skips BOOK_INTAKE and goes straight to
    a generic physiotherapy assessment recommendation.
    """
    r = reason.lower()

    # Tendon / repetitive strain
    if any(kw in r for kw in [
        "tendon", "tendinopathy", "tendinitis", "plantar", "achilles",
        "tennis elbow", "golfer", "heel", "rotator cuff",
    ]):
        return (
            "How long has that been bothering you? "
            "And has it been getting gradually worse, or did it come on suddenly?"
        )

    # Acute sports / trauma
    if any(kw in r for kw in [
        "sprain", "strain", "pulled", "torn", "tear", "injury", "accident",
        "twisted", "rolled ankle", "fall", "sport", "football", "running",
        "gym", "training",
    ]):
        return (
            "Was this from a specific incident, or has it built up over time? "
            "And how long ago did it happen?"
        )

    # Fracture / broken bone / cast recovery
    if any(kw in r for kw in [
        "broken", "fracture", "fractured", "cast", "break", "broke",
        "cracked", "stress fracture",
    ]):
        return (
            "How long ago did that happen, "
            "and are you still in a cast or have you been cleared to start moving?"
        )

    # Back pain
    if any(kw in r for kw in [
        "back", "lumbar", "spine", "disc", "sciatica", "sciatic",
        "lower back", "upper back",
    ]):
        return (
            "How long have you had this? "
            "And is it constant, or does it come and go?"
        )

    # Neck / shoulder
    if any(kw in r for kw in [
        "neck", "cervical", "shoulder", "frozen shoulder", "whiplash",
    ]):
        return (
            "How long has this been going on? "
            "And does it affect your sleep or daily movement?"
        )

    # Hip / knee / joint
    if any(kw in r for kw in [
        "hip", "knee", "joint", "arthritis", "osteoarthritis", "cartilage",
        "kneecap", "patella",
    ]):
        return (
            "Is this pain on movement, at rest, or both? "
            "And how long have you been dealing with it?"
        )

    # Post-surgery / rehab
    if any(kw in r for kw in [
        "surgery", "operation", "post-op", "post op", "rehab",
        "recovery", "replacement", "reconstruction",
    ]):
        return (
            "How long ago was the surgery, and have you had any physiotherapy since then?"
        )

    # Headache / migraine / jaw
    if any(kw in r for kw in [
        "headache", "migraine", "jaw", "tmj", "dizziness", "vertigo",
    ]):
        return (
            "How long have you been getting these, and how often would you say they occur?"
        )

    # Unknown condition — caller will skip BOOK_INTAKE entirely
    return None


def _build_recommendation(
    reason: str,
    intake_answer: str,
    patient_type: str,
    clinic: dict,
) -> tuple:
    """
    Build a spoken recommendation and resolve the service key.

    Returns: (speech: str, service_key: str)
    - speech     → what the AI says to the patient
    - service_key → key into clinic["service_prices"], or "initial_assessment" as default
    """
    r = (reason or "").lower()
    a = (intake_answer or "").lower()
    prices = clinic.get("service_prices", {})

    # --- Decide service key ---
    # Shockwave-appropriate: chronic tendon conditions (>6-8 weeks)
    chronic_signals = [
        "months", "years", "long time", "a while", "6 weeks", "8 weeks",
        "chronic", "ongoing", "for a year", "for years", "kept coming back",
    ]
    tendon_signals = [
        "tendon", "tendinopathy", "plantar", "achilles", "tennis elbow",
        "golfer", "heel spur", "rotator",
    ]
    is_tendon   = any(kw in r for kw in tendon_signals)
    is_chronic  = any(kw in a for kw in chronic_signals) or any(kw in r for kw in chronic_signals)
    is_returning = (patient_type or "").upper() == "RETURNING"
    is_post_op  = any(kw in r for kw in ["surgery", "post-op", "post op", "operation", "reconstruction", "replacement"])

    if is_post_op and "rehabilitation" in prices:
        service_key = "rehabilitation"
    elif is_returning and "follow_up" in prices:
        service_key = "follow_up"
    elif is_tendon and is_chronic and "shockwave" in prices:
        service_key = "shockwave"
    else:
        service_key = "initial_assessment"

    # --- Look up price info ---
    svc = prices.get(service_key) or prices.get("initial_assessment") or {}
    label    = svc.get("label",    "physiotherapy assessment")
    price    = svc.get("price",    "")
    duration = svc.get("duration", "")
    blurb    = svc.get("blurb",    "")

    # --- Build speech ---
    # Opening: acknowledge what they said
    if is_tendon and is_chronic:
        opener = (
            "Thanks for telling me that. "
            "For a condition like this that's been going on a while, "
        )
    elif is_post_op:
        opener = (
            "Thanks for that. "
            "After surgery, "
        )
    elif is_returning:
        opener = "Welcome back. "
    else:
        opener = "Thanks for that. "

    # Core recommendation
    price_clause = f", which is {price}" if price else ""
    duration_clause = f" — {duration}" if duration else ""
    blurb_clause = f" {blurb}" if blurb else ""

    speech = (
        f"{opener}"
        f"I'd recommend our {label}{price_clause}{duration_clause}. "
        f"{blurb_clause} "
        f"Would you like me to book you in for that?"
    ).strip()

    return speech, service_key


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
    # MID-FLOW INTENT SWITCH  (Option 1 + 3)
    # Catches the user changing their mind mid-booking or mid-reschedule.
    # Runs before any state handler so every deep state gets protection.
    # ------------------------------------------------------------------
    if state in _SWITCHABLE_ALL:
        t_norm = _norm(user_said)

        # Option 3 — explicit switch phrase (unambiguous)
        is_explicit = _is_explicit_switch(user_said)

        # Option 1 — centralized intent guard (conflicting major intent detected)
        in_booking = state in _SWITCHABLE_BOOKING
        in_resch   = state in _SWITCHABLE_RESCH
        conflicts  = (
            (in_booking and intent in ("RESCHEDULE", "CANCEL", "HUMAN")) or
            (in_resch   and intent in ("BOOK",       "CANCEL", "HUMAN"))
        )

        if is_explicit or conflicts:
            # Determine target flow — explicit phrases need keyword inspection,
            # non-explicit can rely on intent directly.
            target = intent  # default: use detect_intent result

            if is_explicit and target not in ("RESCHEDULE", "CANCEL", "BOOK", "HUMAN"):
                # detect_intent returned OTHER/FAQ — infer target from keywords
                if _contains_any(t_norm, ["reschedule", "rebook", "move", "change"]):
                    target = "RESCHEDULE"
                elif _contains_any(t_norm, ["cancel"]):
                    target = "CANCEL"
                elif _contains_any(t_norm, ["book", "appointment", "new booking"]):
                    target = "BOOK"
                elif _contains_any(t_norm, ["human", "person", "someone", "transfer", "speak"]):
                    target = "HUMAN"
                else:
                    # Explicit switch but target unclear — surface to TRIAGE
                    session = _soft_reset_session(session)
                    session["state"] = TRIAGE
                    return _say(
                        "No problem — what would you like to do instead? "
                        "I can help you book, reschedule, or answer a question.",
                        session, tone="ack",
                    )

            # Perform the soft reset (keeps name / phone / patient_type / insurer)
            session = _soft_reset_session(session)

            if target == "HUMAN":
                session["request_transfer"] = True
                return _say(
                    "Of course — let me put you through to someone right now.",
                    session, tone="none",
                )

            if target == "RESCHEDULE":
                session["state"] = RESCH_NAME
                return _say(
                    "No problem — let's get that sorted. "
                    "Can I take your full name for the existing appointment?",
                    session, tone="ack",
                )

            if target == "CANCEL":
                session["state"] = TRIAGE
                return _say(
                    "Of course — can I take your full name and the date and time "
                    "of the appointment you'd like to cancel?",
                    session, tone="ack",
                )

            if target == "BOOK":
                # Switch from reschedule flow back to new booking
                clinic_obj = get_clinic(session)
                locations  = clinic_obj.get("locations", [])
                if locations and not session.get("location_selected"):
                    loc_names = " or ".join(loc["name"] for loc in locations)
                    session["pending_intent"] = "BOOK"
                    session["state"]          = ASK_LOCATION
                    return _say(
                        f"Of course — are you looking to book at our {loc_names} clinic?",
                        session,
                    )
                session["state"] = BOOK_PATIENT_TYPE
                return _say(
                    "Of course — are you a new patient or have you been here before?",
                    session, tone="ack",
                )

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
        # This state is now a pass-through — we go straight to INS_COLLECT_INSURER
        session["state"] = INS_COLLECT_INSURER
        return _say(
            "Which insurance company are you with? "
            "For example, AXA, Vitality, Aviva, or WPA.",
            session,
        )

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
            f"Got it — I've made a note that you're with {insurer_name}. "
            "Do you have your policy number handy? "
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

        # ── FAST-PATH INTERCEPTS ──────────────────────────────────────
        # These are caught by detect_intent() synchronously (no LLM).
        # Handle them here immediately so we never hit the slow LLM call
        # for the most common actions.

        # Book intercept — most common action, must be instant
        if intent == "BOOK":
            locations = clinic.get("locations", [])
            if locations and not session.get("location_selected"):
                loc_names = " or ".join(loc["name"] for loc in locations)
                session["pending_intent"] = "BOOK"
                session["state"] = ASK_LOCATION
                return _say(
                    f"Of course! Are you looking to book at our {loc_names} clinic?",
                    session,
                )
            # Single-location / demo clinic — skip location step
            session["state"] = BOOK_PATIENT_TYPE
            return _say(
                "Of course! Are you a new patient or a returning patient?",
                session, tone="ack",
            )

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
            clinic_name = clinic.get("name", "the clinic")

            # Check if Bupa mentioned directly
            if "bupa" in user_said.lower():
                session["insurance_info"]["provider"] = "Bupa"
                session["insurance_info"]["is_bupa"]  = True
                session["state"] = INS_BUPA_RESPONSE
                return _say(INSURANCE_BUPA_RESPONSE, session, tone="none")

            # Go straight to collecting the insurer — no "does that make sense?" pause
            session["state"] = INS_COLLECT_INSURER
            return _say(
                f"Of course. {_insurance_explain(clinic_name)} "
                f"{INSURANCE_BUPA_WARNING} "
                "Which insurance company are you with? "
                "For example, AXA, Vitality, Aviva, or WPA.",
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
        # Note: BOOK is handled early (fast-path intercept above) so it never reaches here.
        if intent == "RESCHEDULE":
            session = _reset_to_triage(session)
            session["pending_intent"] = "RESCHEDULE"
            session["state"] = ASK_LOCATION
            return _say("Are you calling about our Alcester or Redditch clinic?", session)

        if intent == "FAQ_SERVICE_EXPLAIN":
            topic = detect_service_topic(user_said)
            answer = faq_answer_service("FAQ_SERVICE_EXPLAIN", text=user_said, topic=topic)
            return _say(answer, session, tone="none")

        if intent == "FAQ_INSURANCE":
            session["pending_intent"] = "FAQ_INSURANCE"
            session["state"] = ASK_LOCATION
            return _say("Are you calling about our Alcester or Redditch clinic?", session)

        if intent == "CANCEL":
            session["pending_intent"] = "CANCEL"
            session["state"] = ASK_LOCATION
            return _say("Are you calling about our Alcester or Redditch clinic?", session)

        if intent == "HUMAN":
            # Caller explicitly wants a person — transfer immediately
            _attempt_send_to_sheet(collected, user_said, session, "TRANSFER_REQUEST")
            session["request_transfer"] = True
            return _say(
                "Of course — let me put you through to the team right now. Please hold.",
                session,
            )

        if intent.startswith("FAQ_"):
            return _say(faq_answer(intent, clinic, session), session)

        # Final fallback — tier-based escalation for confused/off-topic input
        miss = int(session.get("miss_count", 0)) + 1
        session["miss_count"] = miss

        if miss <= 1:
            # Tier 1: re-introduce with services list
            _clinic_name = clinic.get("sms_name") or clinic.get("display_name", "the clinic")
            return _say(
                f"Hello — this is Susie, {_clinic_name}'s AI receptionist. "
                f"I can help you with: booking an appointment, "
                f"information about our treatments and pricing, "
                f"insurance questions, "
                f"or details about our opening hours and location. "
                f"What can I help you with today?",
                session,
            )

        if miss == 2:
            # Tier 2: explicit spoken menu
            return _say(
                "Let me give you a quick menu. "
                "Say book to book an appointment. "
                "Say treatments for information about our services and prices. "
                "Or say transfer and I'll put you straight through to the team. "
                "What would you like?",
                session,
            )

        # Tier 3: live transfer
        _attempt_send_to_sheet(collected, user_said, session, "TRANSFER_ESCALATION")
        session["request_transfer"] = True
        return _say(
            "Not a problem — let me put you through to the team right now. Please hold.",
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
            # datetime already imported at module level — no local import needed

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

        tries = int(session.get("pt_type_tries", 0)) + 1
        session["pt_type_tries"] = tries
        if tries >= 2:
            # After two failed attempts, default to new patient and move on
            collected["patient_type"] = "NEW"
            session["pt_type_tries"]  = 0
            session["state"]          = BOOK_REASON
            return _say("No problem — I'll put you down as a new patient. What's the appointment for?", session, tone="ack")
        return _say("Just to confirm — are you a new patient or have you been here before? You can say new or returning.", session, tone="checking")

    if state == BOOK_REASON:
        lower = (user_said or "").lower()

        # Service info question mid-booking → detour, then come back to BOOK_REASON
        if any(q in lower for q in ["what is", "what's", "tell me more", "how does", "explain", "does it hurt"]):
            svc_key = extract_service_from_question(user_said)
            answer  = get_service_explanation(svc_key or "physiotherapy", "detailed")
            return _say(f"{answer} {resume_prompt_for_state(BOOK_REASON)}", session)

        # Save reason and ask a clinical intake follow-up question
        collected["reason"] = (user_said or "").strip()
        intake_q = _intake_question_for_condition(collected["reason"])

        if intake_q is None:
            # Unknown condition — skip BOOK_INTAKE, go straight to a generic recommendation
            clinic_obj   = get_clinic(session)
            svc          = clinic_obj.get("service_prices", {}).get("initial_assessment", {})
            price        = svc.get("price", "")
            price_clause = f", which is {price}" if price else ""
            collected["service"] = "initial_assessment"
            session["state"]     = BOOK_RECOMMEND
            return _say(
                f"That sounds like something our physiotherapist can definitely help with. "
                f"A physiotherapy assessment would be the best starting point{price_clause} — "
                f"we'll assess where you are and put together a plan from there. "
                f"Would you like me to book you in for that?",
                session, tone="none",
            )

        session["state"] = BOOK_INTAKE
        return _say(intake_q, session, tone="checking")

    if state == BOOK_INTAKE:
        collected["intake_answer"] = (user_said or "").strip()

        # Build a service recommendation based on reason + intake answer
        patient_type = collected.get("patient_type", "")
        clinic_obj   = get_clinic(session)
        speech, service_key = _build_recommendation(
            reason       = collected.get("reason", ""),
            intake_answer= collected["intake_answer"],
            patient_type = patient_type,
            clinic       = clinic_obj,
        )
        collected["service"] = service_key
        session["state"]     = BOOK_RECOMMEND
        return _say(speech, session, tone="none")

    if state == BOOK_RECOMMEND:
        lower = (user_said or "").lower()

        # Patient asks about price / cost
        if any(kw in lower for kw in ["price", "cost", "how much", "fee", "charge", "expensive", "£"]):
            clinic_obj = get_clinic(session)
            prices     = clinic_obj.get("service_prices", {})
            svc_key    = collected.get("service", "initial_assessment")
            svc        = prices.get(svc_key) or prices.get("initial_assessment") or {}
            price      = svc.get("price", "")
            label      = svc.get("label", "that service")
            duration   = svc.get("duration", "")

            if price:
                duration_clause = f" — {duration}" if duration else ""
                price_info = (
                    f"The {label} is {price}{duration_clause}. "
                    f"Would you like me to go ahead and book you in for that?"
                )
            else:
                price_info = (
                    "I don't have exact pricing to hand right now — "
                    "the team will confirm that with you. "
                    "Shall I go ahead and book you in?"
                )
            return _say(price_info, session, tone="checking")

        # Patient asks more about the service
        if any(q in lower for q in ["what is", "what's", "tell me more", "how does", "explain", "what happens", "what does"]):
            svc_key = collected.get("service", "physiotherapy")
            # Map our service_key to SERVICE_EXPLANATIONS key
            explanation_map = {
                "initial_assessment": "physiotherapy",
                "follow_up":          "physiotherapy_followup",
                "rehabilitation":     "rehabilitation",
                "shockwave":          "shockwave",
                "sports_massage":     "sports_massage",
            }
            explanation_key = explanation_map.get(svc_key, "physiotherapy")
            answer = get_service_explanation(explanation_key, "detailed")
            return _say(
                f"{answer} Shall I go ahead and book you in?",
                session, tone="none",
            )

        # Patient says no / wants something different
        if is_no(lower):
            # Let them re-state what they want
            session["state"] = BOOK_REASON
            return _say(
                "No problem. What would you like to book instead? "
                "For example, you can say physiotherapy, sports massage, or rehabilitation.",
                session, tone="checking",
            )

        # Patient confirms (yes / ok / yeah / go ahead / sounds good)
        if is_yes(lower) or any(kw in lower for kw in ["ok", "yeah", "sure", "go ahead", "sounds good", "perfect", "book"]):
            session["state"] = BOOK_TIME_PREF
            return _say("Great. What day or time would suit you?", session)

        # Fallback — didn't match yes/no/price/info
        return _say(
            "Sorry — shall I go ahead and book that for you? Just say yes or no.",
            session, tone="checking",
        )

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
            event = None  # initialise so except block can safely reference it
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
                    "Booked via AI receptionist.",
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

                        _sms_clinic = get_clinic(session)
                        # Resolve service label from service_prices if we have a key
                        _svc_key = collected.get("service", "initial_assessment")
                        _svc_label = (
                            _sms_clinic.get("service_prices", {})
                            .get(_svc_key, {})
                            .get("label", "physiotherapy")
                        )
                        await send_booking_confirmation(
                            patient_phone=collected["phone"],
                            patient_name=collected["name"].split()[0],
                            appointment_time=start,
                            location=get_location_label(session),
                            service=_svc_label,
                            practitioner="Mark",
                            is_new_patient=(collected.get("patient_type") == "NEW"),
                            has_insurance=bool(collected.get("insurer")),
                            insurer=collected.get("insurer"),
                            clinic_name=_sms_clinic.get("sms_name") or _sms_clinic.get("display_name"),
                            clinic_phone=_sms_clinic.get("phone"),
                        )
                        logger.info(f"✅ Booking confirmation SMS sent to {collected['phone']}")
                    except Exception as sms_err:
                        logger.error(f"⚠️ Failed to send booking SMS: {sms_err}")
                        # Never fail the booking just because SMS failed

                    session = _reset_to_triage(session)
                    return _say(
                        f"Confirmed — you're booked for {label}. We look forward to seeing you.",
                        session,
                    )

                # create_event returned but no ID — treat as a failure
                raise RuntimeError("create_event returned no event ID")

            except Exception as booking_err:
                logger.error(f"BOOKING CREATE EVENT ERROR: {booking_err!r}")

                fail_count = int(session.get("booking_fail_count", 0)) + 1
                session["booking_fail_count"] = fail_count

                if fail_count >= 2:
                    # Second failure → switch to manual, ask for name + phone
                    session["manual_booking"] = True
                    session["manual_reason"]  = "calendar_error"
                    session["state"]          = BOOK_NAME
                    # Keep collected so name/phone we already have isn't lost
                    collected.pop("name", None)
                    collected.pop("phone", None)
                    return _say(
                        "I'm sorry about that — the calendar isn't cooperating. "
                        "Could you give me your full name and phone number "
                        "and I'll write it down for the clinic to call you back?",
                        session,
                    )

                # First failure → stay at BOOK_CONFIRM and retry
                session["state"] = BOOK_CONFIRM
                return _say(
                    "Something went wrong on my end — let me try that again. "
                    "Say yes to confirm your booking for "
                    f"{label}, or no to cancel.",
                    session,
                )

        # No tokens or no chosen slot — fall back to manual
        session["manual_booking"] = True
        session["manual_reason"]  = "no_calendar_tokens"
        session["state"]          = BOOK_NAME
        return _say(
            "I don't have calendar access right now. "
            "Could you give me your full name and I'll log a booking request for the clinic?",
            session,
        )

    # ── MANUAL CAPTURE ───────────────────────────────────────────────────────
    # Fallback state entered after repeated errors or unexpected call drops.
    # Reads back collected info and asks caller to confirm so we can hand off.

    if state == MANUAL_CAPTURE:
        if is_yes(user_said):
            # Mark for manual follow-up
            session["manual_followup_needed"] = True
            session["manual_followup_reason"] = session.get(
                "pre_error_state", "system_error"
            )
            session["manual_capture_confirmed"] = True
            session = _reset_to_triage(session)
            clinic_phone = clinic.get("phone", "")
            outro = (
                f" Someone from the team will call you back shortly. "
                f"If it's urgent, you can also reach us on {clinic_phone}."
                if clinic_phone else
                " Someone from the team will call you back shortly."
            )
            return _say(
                "Perfect — I've passed all of that on to the clinic team."
                + outro,
                session,
            )

        if is_no(user_said):
            session = _reset_to_triage(session)
            return _say(
                "No problem — is there anything else I can help you with?",
                session,
            )

        # Unclear response — re-read the summary and prompt again
        capture_msg = _build_manual_capture_message(session, clinic)
        return _say(capture_msg, session)


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
