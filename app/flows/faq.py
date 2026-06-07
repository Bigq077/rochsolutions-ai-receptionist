# app/flows/faq.py

from typing import Optional

# --------------------------------
# Service explanations (pure text)
# --------------------------------

SERVICE_EXPLAIN_TEXT = {
    "physio_assessment": (
        "A physiotherapy assessment is the first appointment where the clinician listens to your symptoms, "
        "asks about your medical history, and examines movement, strength, and flexibility. "
        "They’ll explain what’s likely causing the problem and discuss a treatment plan tailored to you."
    ),

    "follow_up": (
        "A follow-up appointment builds on a previous assessment. "
        "The clinician reviews your progress, adjusts treatment if needed, "
        "and may progress exercises or hands-on therapy."
    ),

    "shockwave": (
        "Shockwave therapy uses sound waves applied to the painful area to help stimulate healing. "
        "It’s often used for long-standing tendon or heel pain, such as plantar fasciitis or Achilles issues. "
        "Some discomfort is normal during treatment, but it’s usually brief. "
        "Your clinician will confirm whether it’s appropriate for your condition."
    ),

    "sports_massage": (
        "Sports massage focuses on muscles and soft tissue to reduce tightness, improve movement, "
        "and support recovery. It can help with muscle soreness, knots, or training-related pain. "
        "Pressure and techniques are adapted to your comfort and needs."
    ),

    "dry_needling": (
        "Dry needling uses very fine needles to target tight muscle bands, sometimes called trigger points. "
        "It can help reduce muscle tension and improve movement. "
        "You may feel a brief twitch response and mild soreness afterwards."
    ),

    "manual_therapy": (
        "Manual therapy involves hands-on techniques such as joint mobilisations or soft tissue work. "
        "It’s used to reduce pain, improve movement, and support rehabilitation alongside exercise."
    ),

    "exercise_rehab": (
        "Exercise rehabilitation focuses on specific exercises to improve strength, flexibility, "
        "and control. These exercises are tailored to your condition and adjusted as you progress."
    ),

    "post_op_rehab": (
        "Post-operative rehabilitation helps you recover safely after surgery. "
        "Treatment focuses on restoring movement, strength, and confidence at the right pace, "
        "following guidance from your surgeon."
    ),

    "back_pain": (
        "Treatment for back pain typically involves a combination of assessment, hands-on treatment, "
        "and exercises. The approach depends on whether the pain is recent, long-standing, "
        "or related to posture, movement, or injury."
    ),

    "neck_pain": (
        "Neck pain treatment may include gentle hands-on techniques, exercises, and advice on posture "
        "and movement. The clinician will tailor treatment to your symptoms and comfort."
    ),

    "running_injury": (
        "Running injury treatment focuses on identifying load, technique, or strength issues. "
        "Rehabilitation may include exercises, hands-on therapy, and guidance on returning to running safely."
    ),

    "workplace_injury": (
        "Treatment for work-related injuries aims to reduce pain, restore movement, "
        "and help you return to work safely. Advice on posture or activity modification may be included."
    ),
}

# --------------------------------
# FAQ answer helper (PURE)
# --------------------------------

def faq_answer(
    intent: str,
    text: Optional[str] = None,
    topic: Optional[str] = None,
) -> str:
    if intent == "FAQ_SERVICE_EXPLAIN":
        if not topic and text:
            # minimal, defensive parsing only
            t = text.lower()
            if "shock" in t:
                topic = "shockwave"
            elif "massage" in t:
                topic = "sports_massage"
            elif "needle" in t:
                topic = "dry_needling"
            elif "follow" in t:
                topic = "follow_up"
            elif "assessment" in t or "first" in t:
                topic = "physio_assessment"
            elif "post" in t and "surgery" in t:
                topic = "post_op_rehab"
            elif "exercise" in t or "rehab" in t:
                topic = "exercise_rehab"
            elif "back" in t:
                topic = "back_pain"
            elif "neck" in t:
                topic = "neck_pain"
            elif "run" in t:
                topic = "running_injury"
            elif "work" in t:
                topic = "workplace_injury"

        return SERVICE_EXPLAIN_TEXT.get(
            topic or "",
            "Sure — which service would you like to know about? "
            "For example: physiotherapy assessment, shockwave therapy, or sports massage."
        )

    # other FAQ intents handled elsewhere
    return "Sorry — I didn’t catch that."
