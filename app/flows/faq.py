# app/flows/faq.py

from typing import Optional

# --------------------------------
# Service explanations (pure text)
# --------------------------------

SERVICE_EXPLAIN_TEXT = {
    "shockwave": (
        "Shockwave therapy uses sound waves applied to the painful area to help stimulate healing. "
        "It’s often used for stubborn tendon or heel pain, like plantar fasciitis or Achilles issues. "
        "Most people feel some discomfort during treatment, but it’s usually tolerable. "
        "A clinician will confirm whether it’s appropriate for your condition."
    ),
    "sports_massage": (
        "Sports massage focuses on muscles and soft tissue to reduce tightness, improve movement, "
        "and support recovery. It can be helpful for general soreness, knots, or training-related pain. "
        "Your therapist can adapt pressure and focus areas depending on what you need."
    ),
    "dry_needling": (
        "Dry needling uses very fine needles to target tight muscle bands, sometimes called trigger points. "
        "It can help reduce muscle tension and pain and improve range of motion. "
        "You may feel a brief twitch response and mild soreness afterwards."
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

        return SERVICE_EXPLAIN_TEXT.get(
            topic or "",
            "Sure — which service would you like to know about? "
            "For example: shockwave therapy or sports massage."
        )

    # existing FAQ intents live here
    return "Sorry — I didn’t catch that."
