# app/media_streams/utterance_router.py
"""
Bounded utterance router for the AI receptionist.

Classifies mixed, repair, or context-rich caller utterances to guide
deterministic flow control.  The LLM is used ONLY for classification —
it never generates caller-facing responses or chooses flow states directly.

Integration contract
--------------------
*  analyze_utterance_for_flow_control() returns RouterResult | None.
*  On None (timeout, malformed output, low confidence, gate reject) the
   caller MUST fall back to its existing deterministic handling.
*  Deterministic code maps the returned action enum to all actual behaviour.
*  Bridge templates are the ONLY caller-facing strings this module produces.

Action vocabulary (strict, minimal)
------------------------------------
TREAT_AS_STATE_ANSWER               utterance cleanly answers the pending question
BRIDGE_THEN_ASK_PENDING             acknowledge context/reason then re-ask pending Q
ANSWER_SERVICE_FIT_THEN_ASK_PENDING service-fit question detected; answer briefly then ask pending
REPEAT_PENDING_QUESTION_CLEARLY     unclear but not repair; re-state pending Q
DO_NOT_ADVANCE_REPAIR               confusion / repair / meta-conversation; re-ask without advancing
ANSWER_FAQ_THEN_RETURN              FAQ detour within existing flow; return to pending Q after
INTENT_SWITCH_TO_BOOKING            caller clearly wants to book
INTENT_SWITCH_TO_FAQ                caller clearly wants information only
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Dict, Optional, TypedDict

logger = logging.getLogger(__name__)

# ── Action constants ───────────────────────────────────────────────────────────

TREAT_AS_STATE_ANSWER               = "TREAT_AS_STATE_ANSWER"
BRIDGE_THEN_ASK_PENDING             = "BRIDGE_THEN_ASK_PENDING"
ANSWER_SERVICE_FIT_THEN_ASK_PENDING = "ANSWER_SERVICE_FIT_THEN_ASK_PENDING"
REPEAT_PENDING_QUESTION_CLEARLY     = "REPEAT_PENDING_QUESTION_CLEARLY"
DO_NOT_ADVANCE_REPAIR               = "DO_NOT_ADVANCE_REPAIR"
ANSWER_FAQ_THEN_RETURN              = "ANSWER_FAQ_THEN_RETURN"
INTENT_SWITCH_TO_BOOKING            = "INTENT_SWITCH_TO_BOOKING"
INTENT_SWITCH_TO_FAQ                = "INTENT_SWITCH_TO_FAQ"

_VALID_ACTIONS = frozenset({
    TREAT_AS_STATE_ANSWER,
    BRIDGE_THEN_ASK_PENDING,
    ANSWER_SERVICE_FIT_THEN_ASK_PENDING,
    REPEAT_PENDING_QUESTION_CLEARLY,
    DO_NOT_ADVANCE_REPAIR,
    ANSWER_FAQ_THEN_RETURN,
    INTENT_SWITCH_TO_BOOKING,
    INTENT_SWITCH_TO_FAQ,
})

_VALID_INTENTS = frozenset({
    "booking", "faq", "service_fit", "repair", "answer", "unclear",
})


class RouterResult(TypedDict):
    action: str
    primary_intent: str
    secondary_intent: Optional[str]
    has_reason: bool
    has_service_fit_question: bool
    has_confusion_repair: bool
    has_real_answer_to_pending: bool
    reason_text: Optional[str]
    confidence: float
    source: str   # "llm" | "deterministic"


# ── Deterministic phrase sets ──────────────────────────────────────────────────

# Repair / confusion / meta-conversational phrases.
# Each entry is a lowercase substring — checked with `any(p in text_lower ...)`.
# Kept specific enough to avoid false-positives on valid answers.
REPAIR_PHRASES: tuple[str, ...] = (
    "what were you asking",
    "what did you mean",
    "what do you mean",
    "what are you asking",
    "what was that",
    "sorry what",
    "pardon",
    "come again",
    "could you repeat",
    "can you repeat",
    "say that again",
    "i'm not sure what you",
    "im not sure what you",
    "not sure what you mean",
    "not sure what you're asking",
    "not sure what you are asking",
    "that's not clear",
    "thats not clear",
    "that's unclear",
    "thats unclear",
    "that's kind of unclear",
    "that's a bit unclear",
    "that is unclear",
    "i'm confused",
    "im confused",
    "i don't understand",
    "i dont understand",
    "don't understand what you",
    "dont understand what you",
    "sorry, that's kind",
    "sorry thats kind",
    "not what i meant",
    "not what i was asking",
    "that's not what i meant",
    "thats not what i meant",
    "i was just asking",
    "i was just wondering",
    "i was only asking",
    "just asking if",
    "just asking whether",
    "i'm just asking",
    "im just asking",
    "only asking if",
    "only asking whether",
)
# Removed from above (too broad — fire on legitimate service-fit questions):
#   "i only wanted to know"  → "I only wanted to know what you could do" is service-fit
#   "i just wanted to know"  → "I just wanted to know what exactly could you do" is service-fit
#   "just wanted to ask"     → "I just wanted to ask what physio could do" is service-fit

# Service-fit / eligibility question phrases.
SERVICE_FIT_PHRASES: tuple[str, ...] = (
    "can you help",
    "could you help",
    "can help",
    "could help",
    "would you be able to help",
    "able to help",
    "able to assist",
    "would physio help",
    "would physiotherapy help",
    "can physio help",
    "can physiotherapy help",
    "does physio",
    "does physiotherapy",
    "is physio",
    "is physiotherapy",
    "would that help",
    "could that help",
    "is that something",
    "is this something",
    "would you treat",
    "do you treat",
    "do you deal with",
    "would you deal with",
    "is that the right place",
    "is this the right place",
    "right place for",
    "right clinic for",
    "help with my",
    "help with his",
    "help with her",
    "help with their",
    "help for my",
    "help for his",
    "help for her",
    "appropriate for",
    "suitable for",
    "the right thing",
    # "What could/would you do" family — caller asking about clinic capability/process
    "what could you do",
    "what exactly could",
    "what can the clinic",
    "what do you do for",
    "what would you do",
    "what would the clinic",
    "what does physio",
    "what does physiotherapy",
    "what would physio",
    "what would physiotherapy",
    # Third-person help phrases — "could you help him/her"
    "could you help him",
    "could you help her",
    "help him out",
    "help her out",
    # Treatment capability — "could you treat", "can you treat"
    "could you treat",
    "can you treat",
    "would you be able to treat",
    "could you help my",
    "can you help my",
    "could you see my",
    "can you see my",
)

# Injury / context / reason phrases.
CONTEXT_REASON_PHRASES: tuple[str, ...] = (
    "my son", "my daughter", "my wife", "my husband", "my partner",
    "my mother", "my father", "my mum", "my dad",
    "i have", "i've got", "i've had", "i've been having",
    "i'm suffering", "i'm having trouble", "i'm struggling",
    "been having", "been suffering", "been struggling",
    "hurt my", "hurt his", "hurt her",
    "injured my", "injured his", "injured her",
    "pain in", "pain with",
    "last week", "last month", "few weeks ago",
    "been going on",
)


# ── State sets ─────────────────────────────────────────────────────────────────

# States where pending-question protection is most critical.
_QUESTION_SENSITIVE_STATES = frozenset({
    "ASK_LOCATION",
    "NEW_OR_RETURNING",
    "COLLECT_REASON",
    "RETURNING_RECENCY",
    "COLLECT_NAME",
    "COLLECT_NAME_RETURNING",
    "FAQ_BOOKING_OFFER",
    "GENERAL_BOOKING_OFFER",
    "CONFIRM_PHONE",
    "CONFIRM_PHONE_RETURNING",
    "RETURNING_PLAN_CONFIRM_PHONE",
})

# States where the router is allowed to fire at all.
_ROUTER_ELIGIBLE_STATES = frozenset({
    "DETECT_INTENT",
    "ASK_LOCATION",
    "NEW_OR_RETURNING",
    "COLLECT_REASON",
    "FAQ_BOOKING_OFFER",
    "GENERAL_BOOKING_OFFER",
    "RETURNING_RECENCY",
})

# Minimum seconds between router LLM calls on the same session.
_ROUTER_COOLDOWN_SEC = 30.0


# ── Bridge templates ───────────────────────────────────────────────────────────
# The ONLY caller-facing strings produced by this module.
# Short, receptionist-appropriate, and never diagnostic.
# At DETECT_INTENT, templates are short acknowledgements; ask_current_question()
# speaks the actual next question immediately after.

BRIDGE_TEMPLATES: Dict[str, Dict[str, str]] = {
    ANSWER_SERVICE_FIT_THEN_ASK_PENDING: {
        "DETECT_INTENT": (
            "Yes, that's certainly something we can help with."
        ),
        "ASK_LOCATION": (
            "Yes, we can certainly help with that. "
            "Which clinic would be more convenient \u2014 our Alcester or Redditch practice?"
        ),
        "NEW_OR_RETURNING": (
            "Yes, we can certainly help with that. "
            "Have you been seen with us before, or would this be a new appointment?"
        ),
        "_default": (
            "Yes, that's certainly something we can help with. "
            "Let me take a few details."
        ),
    },
    BRIDGE_THEN_ASK_PENDING: {
        "DETECT_INTENT": (
            "I'm sorry to hear that."
        ),
        "ASK_LOCATION": (
            "I'm sorry to hear that \u2014 which clinic would you prefer, "
            "our Alcester or Redditch practice?"
        ),
        "NEW_OR_RETURNING": (
            "I'm sorry to hear that. "
            "Have you been seen with us before, or would this be a new appointment?"
        ),
        "_default": (
            "I'm sorry to hear that. Let me take a few details for you."
        ),
    },
    DO_NOT_ADVANCE_REPAIR: {
        "ASK_LOCATION": (
            "Sorry \u2014 which clinic would you prefer, "
            "our Alcester or Redditch practice?"
        ),
        "NEW_OR_RETURNING": (
            "Sorry \u2014 have you been seen with us before, "
            "or would this be a new appointment?"
        ),
        "COLLECT_REASON": (
            "Sorry \u2014 what's the main thing you'd like help with today?"
        ),
        "RETURNING_RECENCY": (
            "Sorry \u2014 could you remind me when you were last seen with us?"
        ),
        "FAQ_BOOKING_OFFER": (
            "Of course \u2014 was there something else you'd like to ask?"
        ),
        "GENERAL_BOOKING_OFFER": (
            "Of course \u2014 was there something else you'd like to ask?"
        ),
        "_default": (
            "Sorry, I didn't quite catch that \u2014 could you say that again?"
        ),
    },
    REPEAT_PENDING_QUESTION_CLEARLY: {
        "ASK_LOCATION": (
            "Just to confirm \u2014 which clinic would you prefer, "
            "our Alcester or Redditch practice?"
        ),
        "NEW_OR_RETURNING": (
            "Just to check \u2014 is this a new appointment, "
            "or have you been seen with us before?"
        ),
        "COLLECT_REASON": (
            "Could I ask what's brought you in today \u2014 "
            "what's the main issue you'd like help with?"
        ),
        "_default": (
            "Sorry \u2014 could you say that again please?"
        ),
    },
}


def get_bridge_text(action: str, state: str) -> str:
    """Return the deterministic bridge string for a given action + state."""
    templates = BRIDGE_TEMPLATES.get(action, {})
    return (
        templates.get(state)
        or templates.get("_default")
        or "Sorry, could you say that again?"
    )


# ── Deterministic signal helpers ───────────────────────────────────────────────

def is_repair_utterance(text: str) -> bool:
    """
    Purely deterministic — no LLM.
    Returns True when the utterance is repair, confusion, or meta-conversational.
    Safe to call at any point; never mutates state.
    """
    t = text.strip().lower()
    return any(p in t for p in REPAIR_PHRASES)


def has_service_fit_question(text: str) -> bool:
    """True when the utterance contains a service-fit / eligibility question."""
    t = text.strip().lower()
    return any(p in t for p in SERVICE_FIT_PHRASES)


def has_context_reason(text: str) -> bool:
    """True when the utterance mentions an injury, symptom, or medical context."""
    t = text.strip().lower()
    return any(p in t for p in CONTEXT_REASON_PHRASES)


# ── Child / minor reference detection ─────────────────────────────────────────

MINOR_PHRASES: tuple[str, ...] = (
    "my son", "my daughter", "my child", "my children",
    "my little boy", "my little girl",
    "my kid", "my kids",
    "my teenager", "my teen",
    "my baby", "my toddler",
    "little boy", "little girl",
    "young boy", "young girl", "young person",
    "son's", "daughter's",
    "for my son", "for my daughter", "for my child",
    "do you treat children", "do you see children",
    "can you treat children", "can you see children",
    "treat children", "treat kids", "see children",
    "paediatric", "pediatric",
    "child physio", "children physio", "kids physio",
    "he is ", "she is ",       # "he is 8 years old" / "she is 12"
    "how old", "years old",    # age-related follow-up
)


def has_minor_reference(text: str) -> bool:
    """True when the utterance references a child, minor, or paediatric patient."""
    t = text.strip().lower()
    return any(p in t for p in MINOR_PHRASES)


# ── Deterministic gate ─────────────────────────────────────────────────────────

_BOOKING_SIGS: tuple[str, ...] = (
    "book", "appointment", "schedule", "booked",
    "come in", "get an appointment", "make an appointment",
)


def _should_call_router(
    text: str,
    state: str,
    session: Dict[str, Any],
) -> tuple[bool, str]:
    """
    Deterministic gate. Returns (should_call: bool, reason: str).

    Only True when at least one enrichment condition holds and
    deterministic-only handling would be brittle.
    Never true for short utterances, digit-heavy strings, or ineligible states.
    """
    t = text.strip().lower()
    word_count = len(t.split())

    if word_count <= 2:
        return False, "too_short"

    if state not in _ROUTER_ELIGIBLE_STATES:
        return False, "state_not_eligible"

    # Cooldown: don't call the LLM again too soon on the same session.
    _last = session.get("_router_last_call_ts", 0.0)
    if (time.time() - _last) < _ROUTER_COOLDOWN_SEC:
        return False, "cooldown"

    # Digit-heavy text (phone numbers etc.) must not reach the router.
    if sum(1 for c in text if c.isdigit()) >= 4:
        return False, "digit_heavy"

    _has_booking   = any(s in t for s in _BOOKING_SIGS)
    _has_repair    = is_repair_utterance(text)
    _has_svc_fit   = has_service_fit_question(text)
    _has_reason    = has_context_reason(text)

    # Repair / confusion in a question-sensitive state.
    if _has_repair and state in _QUESTION_SENSITIVE_STATES:
        return True, "repair_in_question_state"

    # Multi-signal: booking + service-fit question or context reason.
    if _has_booking and (_has_svc_fit or _has_reason) and word_count >= 8:
        return True, "multi_signal_booking"

    # Rich first utterance at DETECT_INTENT with service-fit but no booking signal.
    if state == "DETECT_INTENT" and _has_svc_fit and word_count >= 8:
        return True, "detect_intent_service_fit"

    # Safety net: context-rich utterance at DETECT_INTENT with injury/reason context
    # but no booking signal — the caller may be asking a service-fit question using
    # phrasing not yet covered by SERVICE_FIT_PHRASES; route through LLM to check.
    if state == "DETECT_INTENT" and _has_reason and not _has_booking and word_count >= 12:
        return True, "detect_intent_context_no_booking"

    # Long utterance at question-sensitive state that doesn't obviously answer it.
    if state in _QUESTION_SENSITIVE_STATES and word_count >= 12 and not _has_booking:
        return True, "rich_non_answer_in_question_state"

    return False, "deterministic_sufficient"


# ── LLM system prompt ──────────────────────────────────────────────────────────

_ROUTER_SYSTEM_PROMPT = """\
You are a strict utterance classifier for a medical physiotherapy receptionist phone system.
Your ONLY job is to classify the shape and intent of a caller's utterance.
You MUST return valid JSON. You MUST NOT generate caller-facing responses.
You MUST NOT choose flow states. You MUST NOT make booking decisions.

Return exactly this JSON schema (no markdown fences, raw JSON only):
{
  "action": "<one action from the allowed list>",
  "primary_intent": "<one of: booking, faq, service_fit, repair, answer, unclear>",
  "secondary_intent": "<same set or null>",
  "has_reason": <true|false>,
  "has_service_fit_question": <true|false>,
  "has_confusion_repair": <true|false>,
  "has_real_answer_to_pending": <true|false>,
  "reason_text": "<extracted injury or symptom phrase, or null>",
  "confidence": <float 0.0-1.0>
}

Allowed actions — apply in strict priority order:
1. TREAT_AS_STATE_ANSWER             utterance cleanly and directly answers the pending question
2. DO_NOT_ADVANCE_REPAIR             utterance is confusion, repair, or meta-conversation
3. ANSWER_SERVICE_FIT_THEN_ASK_PENDING  utterance contains a service-fit or eligibility question
4. BRIDGE_THEN_ASK_PENDING           utterance contains context/reason but no service-fit question
5. INTENT_SWITCH_TO_BOOKING          caller clearly wants to book with no other complicating signals
6. INTENT_SWITCH_TO_FAQ              caller clearly wants information only
7. ANSWER_FAQ_THEN_RETURN            FAQ detour within an existing flow; return to pending Q after
8. REPEAT_PENDING_QUESTION_CLEARLY   unclear utterance that is not repair; just re-ask pending Q

Classification rules:
- has_confusion_repair=true for: "what were you asking", "sorry that's unclear", \
"I was just asking if you could help", "I don't understand", "what do you mean", \
"I was only asking whether", "pardon", "come again", "say that again"
- has_service_fit_question=true for: "can you help", "would physio help", \
"could help", "is this the right place", "do you treat", "able to help with", \
"would that be appropriate"
- has_reason=true when caller mentions injury, symptom, or medical condition; \
extract the phrase as reason_text (e.g. "ankle injury", "back pain", "knee problem")
- has_real_answer_to_pending=true ONLY when the utterance directly addresses \
the pending question (e.g. "Alcester" for a clinic question, "yes" for a yes/no question)
- Set confidence < 0.4 if genuinely unsure; the system will fall back to deterministic handling."""


# ── LLM client singleton ───────────────────────────────────────────────────────

_router_client = None


def _get_router_client():
    global _router_client
    if _router_client is None:
        from anthropic import AsyncAnthropic
        import httpx
        from app.media_streams.config import ANTHROPIC_API_KEY
        _router_client = AsyncAnthropic(
            api_key=ANTHROPIC_API_KEY,
            timeout=httpx.Timeout(5.0),
        )
    return _router_client


# ── Public entry point ─────────────────────────────────────────────────────────

async def analyze_utterance_for_flow_control(
    text: str,
    state: str,
    session: Dict[str, Any],
    pending_question: str = "",
    recent_context: str = "",
) -> Optional[RouterResult]:
    """
    Classify the utterance. Returns RouterResult or None.

    ALWAYS returns None on any error — callers MUST fall back to deterministic
    handling when None is returned. This function never raises.
    """
    _gate_ok, _gate_reason = _should_call_router(text, state, session)
    if not _gate_ok:
        logger.debug(
            "[utterance_router] gate=skip reason=%s state=%s text=%r",
            _gate_reason, state, text[:40],
        )
        return None

    # ── Deterministic fast-paths (no LLM needed) ───────────────────────────

    # Repair is always handled deterministically — phrase list is tight.
    if is_repair_utterance(text):
        logger.info(
            "[utterance_router] deterministic=repair state=%s text=%r",
            state, text[:40],
        )
        session["_router_last_call_ts"] = time.time()
        return RouterResult(
            action=DO_NOT_ADVANCE_REPAIR,
            primary_intent="repair",
            secondary_intent=None,
            has_reason=False,
            has_service_fit_question=False,
            has_confusion_repair=True,
            has_real_answer_to_pending=False,
            reason_text=None,
            confidence=0.95,
            source="deterministic",
        )

    _t_lower = text.strip().lower()
    _has_booking = any(s in _t_lower for s in _BOOKING_SIGS)

    # Service-fit at DETECT_INTENT without explicit booking signal — deterministic.
    if (
        state == "DETECT_INTENT"
        and has_service_fit_question(text)
        and not _has_booking
    ):
        _has_rsn = has_context_reason(text)
        logger.info(
            "[utterance_router] deterministic=service_fit_no_booking "
            "has_reason=%s text=%r",
            _has_rsn, text[:40],
        )
        session["_router_last_call_ts"] = time.time()
        return RouterResult(
            action=ANSWER_SERVICE_FIT_THEN_ASK_PENDING,
            primary_intent="service_fit",
            secondary_intent=None,
            has_reason=_has_rsn,
            has_service_fit_question=True,
            has_confusion_repair=False,
            has_real_answer_to_pending=False,
            reason_text=None,
            confidence=0.85,
            source="deterministic",
        )

    # ── LLM path (genuinely ambiguous mixed utterances) ────────────────────
    logger.info(
        "[utterance_router] llm_call: state=%s gate_reason=%s text=%r",
        state, _gate_reason, text[:60],
    )
    session["_router_last_call_ts"] = time.time()

    try:
        result = await asyncio.wait_for(
            _call_llm_classifier(text, state, pending_question, recent_context),
            timeout=4.0,
        )
    except asyncio.TimeoutError:
        logger.warning(
            "[utterance_router] llm_timeout state=%s — deterministic fallback", state
        )
        return None
    except Exception as _err:
        logger.warning(
            "[utterance_router] llm_error state=%s err=%r — deterministic fallback",
            state, _err,
        )
        return None

    if result is None:
        logger.info("[utterance_router] llm_result=None — deterministic fallback")
        return None

    logger.info(
        "[utterance_router] llm_result: action=%s intent=%s confidence=%.2f",
        result["action"], result["primary_intent"], result["confidence"],
    )
    return result


async def _call_llm_classifier(
    text: str,
    state: str,
    pending_question: str,
    recent_context: str,
) -> Optional[RouterResult]:
    """Internal: single non-streaming HAIKU call. Returns parsed RouterResult or None."""
    from app.media_streams.config import HAIKU

    client = _get_router_client()

    user_content = (
        f"State: {state}\n"
        f"Pending question: {pending_question or '(none)'}\n"
        f"Recent context: {recent_context or '(none)'}\n"
        f"Caller utterance: {text}"
    )

    response = await client.messages.create(
        model=HAIKU,
        max_tokens=256,
        system=_ROUTER_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_content}],
    )

    raw = response.content[0].text.strip()

    # Strip markdown fences if the model ignores the "raw JSON only" instruction.
    if "```" in raw:
        parts = raw.split("```")
        raw = parts[1] if len(parts) > 1 else raw
        if raw.startswith("json\n"):
            raw = raw[5:]
        elif raw.startswith("json"):
            raw = raw[4:]

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning(
            "[utterance_router] llm_json_parse_error raw=%r", raw[:120]
        )
        return None

    # Validate action — discard response if unknown.
    action = str(data.get("action", ""))
    if action not in _VALID_ACTIONS:
        logger.warning(
            "[utterance_router] llm_invalid_action %r — discarding", action
        )
        return None

    # Validate intents — coerce unknowns to "unclear".
    primary_intent = str(data.get("primary_intent", "unclear"))
    if primary_intent not in _VALID_INTENTS:
        primary_intent = "unclear"

    secondary_intent = data.get("secondary_intent")
    if secondary_intent and str(secondary_intent) not in _VALID_INTENTS:
        secondary_intent = None

    # Validate confidence — low confidence means fallback.
    try:
        confidence = float(data.get("confidence", 0.5))
        confidence = max(0.0, min(1.0, confidence))
    except (TypeError, ValueError):
        confidence = 0.5

    if confidence < 0.4:
        logger.info(
            "[utterance_router] llm_low_confidence=%.2f — deterministic fallback",
            confidence,
        )
        return None

    reason_text_raw = data.get("reason_text")
    reason_text = str(reason_text_raw) if reason_text_raw else None

    return RouterResult(
        action=action,
        primary_intent=primary_intent,
        secondary_intent=secondary_intent if secondary_intent else None,
        has_reason=bool(data.get("has_reason", False)),
        has_service_fit_question=bool(data.get("has_service_fit_question", False)),
        has_confusion_repair=bool(data.get("has_confusion_repair", False)),
        has_real_answer_to_pending=bool(data.get("has_real_answer_to_pending", False)),
        reason_text=reason_text,
        confidence=confidence,
        source="llm",
    )
