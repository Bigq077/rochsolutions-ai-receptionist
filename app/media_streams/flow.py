# app/media_streams/flow.py
"""
Multi-intent conversation flow for the Susie AI receptionist.

All conversation decisions live here.  Nothing else in the pipeline
decides what Susie says next.

Each intent maps to a dedicated flow array (a list of step dicts).
The FlowEngine starts in DETECT_INTENT_FLOW.  The first caller utterance
classifies the intent and switches to the correct flow.  From that point
the engine works identically to the original single-flow design:

    Susie asks a question.  Caller answers.  Step advances.  Repeat.

Flows:
    DETECT_INTENT_FLOW  — entry point (1 step, no spoken question)
    BOOKING_FLOW        — new appointment booking (10 steps)
    RESCHEDULE_FLOW     — reschedule existing appointment (7 steps)
    CANCEL_FLOW         — cancel existing appointment (5 steps)
    FAQ_FLOW            — price / insurance / hours / services questions (2 steps)

Usage from connection.py (unchanged):
    flow = FlowEngine(session, tts_text_queue, llm_fn)
    # First caller utterance starts the flow:
    await flow.ask_current_question()
    # Every subsequent utterance goes through:
    await flow.handle_transcript(transcript)
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Coroutine, Dict, List, Optional

try:
    from rapidfuzz import fuzz as _fuzz
    _RAPIDFUZZ_AVAILABLE = True
except ImportError:
    _fuzz = None  # type: ignore[assignment]
    _RAPIDFUZZ_AVAILABLE = False

logger = logging.getLogger(__name__)
if not _RAPIDFUZZ_AVAILABLE:
    logger.warning(
        "[ms_flow] rapidfuzz not installed — fuzzy matching disabled. "
        "Install with: pip install rapidfuzz>=3.0.0"
    )


def _fuzzy_match(text: str, patterns: list, threshold: int = 80) -> bool:
    """
    Return True if any pattern fuzzy-matches the text at or above threshold.

    Uses rapidfuzz.fuzz.partial_ratio for substring-aware matching.
    Falls back gracefully (returns False) if rapidfuzz is not installed.
    """
    if not _RAPIDFUZZ_AVAILABLE or _fuzz is None:
        return False
    text_clean = text.strip().lower()
    for pattern in patterns:
        score = _fuzz.partial_ratio(text_clean, pattern)
        if score >= threshold:
            return True
    return False


# ---------------------------------------------------------------------------
# Question-worth-storing guard
# ---------------------------------------------------------------------------

_NEVER_STORE_AS_QUESTION = [
    "hi there",
    "hello",
    "this is susie",
    "roch solutions",
    "theorem health",
    "of course you can book",
]
_KNOWN_QUESTION_PHRASES = [
    "what brings you in",
    "how long have you had",
    "does that sound ok",
    "been with us before",
    "work best for you",
    "full name please",
    "reach you on",
    "which would you prefer",
    "that right",
    "sound ok",
    "catch that",
    "about that",
    "would you like",
    "no problem — which",
]


def _is_question_worth_storing(text: str) -> bool:
    """Return True only if text is a real question Susie asked — not the greeting."""
    t = text.strip().lower()
    for phrase in _NEVER_STORE_AS_QUESTION:
        if t.startswith(phrase):
            return False
    for q in _KNOWN_QUESTION_PHRASES:
        if q in t:
            return True
    if t.endswith("?"):
        return True
    return False


# ---------------------------------------------------------------------------
# Flow definitions
# ---------------------------------------------------------------------------

# ---------- Entry-point flow (intent detection) ---------------------------

DETECT_INTENT_FLOW: List[Dict[str, Any]] = [
    {
        "step": 0,
        "state": "DETECT_INTENT",
        "question": None,       # greeting already played by connection.py
        "answer_field": "intent",
        "use_llm": False,
        "extract": "intent",
        "llm_instruction": None,
    },
]

# ---------- Booking flow (new appointment) --------------------------------

BOOKING_FLOW: List[Dict[str, Any]] = [
    {
        "step": 0,
        "state": "COLLECT_REASON",
        "question": (
            "Of course you can book an appointment — "
            "what brings you in today?"
        ),
        "answer_field": "reason",
        "use_llm": False,
        "extract": "any",
        "llm_instruction": None,
    },
    {
        "step": 1,
        "state": "COLLECT_DURATION",
        "question": None,   # LLM generates this
        "answer_field": "duration",
        "use_llm": True,
        "llm_instruction": (
            "The caller said: '{reason}'. "
            "Respond with ONE sentence of genuine empathy "
            "about their specific condition. "
            "End with exactly: '— how long have you had that?' "
            "Say nothing else. No other questions."
        ),
        "extract": "duration",
    },
    {
        "step": 2,
        "state": "CONFIRM_ASSESSMENT",
        "question": (
            "OK, that's noted. To get the best possible "
            "diagnosis initially I would recommend a "
            "physiotherapy assessment — does that sound OK?"
        ),
        "answer_field": "assessment_confirmed",
        "use_llm": False,
        "extract": "yes_no",
        "llm_instruction": None,
    },
    {
        "step": 3,
        "state": "NEW_OR_RETURNING",
        "question": "Have you been with us before?",
        "answer_field": "new_or_returning",
        "use_llm": False,
        "extract": "new_or_returning",
        "llm_instruction": None,
    },
    {
        "step": 4,
        "state": "COLLECT_AVAILABILITY",
        "question": "What days or times work best for you?",
        "answer_field": "availability",
        "use_llm": False,
        "extract": "availability",
        "llm_instruction": None,
    },
    {
        "step": 5,
        "state": "PRESENT_SLOTS",
        "question": "Let me check what we have available for you.",
        "answer_field": "selected_slot",
        "use_llm": True,
        "llm_instruction": (
            "Call check_availability with location='alcester', "
            "duration_minutes=50, preference='{availability}'. "
            "Present up to 3 slots in this exact format: "
            "'I have found [N] available slots during that time frame. "
            "The first being [DAY] the [DDth] of [MONTH] at [H:MMam/pm], "
            "the second being [DAY] the [DDth] of [MONTH] at [H:MMam/pm], "
            "the third being [DAY] the [DDth] of [MONTH] at [H:MMam/pm]. "
            "Which would you prefer?' "
            "Use ordinal dates like 'Monday the 23rd of March at 9am'. "
            "Never deviate from this format."
        ),
        "extract": "slot_selection",
    },
    {
        "step": 6,
        "state": "COLLECT_NAME",
        "question": "Could I take your full name please?",
        "answer_field": "full_name",
        "use_llm": False,
        "extract": "name",
        "llm_instruction": None,
    },
    {
        "step": 7,
        "state": "CONFIRM_PHONE",
        "question": (
            "Just to confirm — shall I use the number "
            "you're calling from for the booking?"
        ),
        "answer_field": "phone_confirmed",
        "use_llm": False,
        "extract": "phone_confirm",
        "llm_instruction": None,
    },
    {
        "step": 8,
        "state": "COLLECT_PHONE",
        "question": "And the best number to reach you on?",
        "answer_field": "phone_number",
        "use_llm": False,
        "extract": "phone",
        "llm_instruction": None,
    },
    {
        "step": 9,
        "state": "CONFIRM_BOOKING",
        "question": None,   # LLM generates this
        "answer_field": "booking_confirmed",
        "use_llm": True,
        "llm_instruction": (
            "Confirm the booking with a warm summary. "
            "Include: patient name '{full_name}', "
            "appointment type 'physiotherapy assessment', "
            "date and time '{selected_slot}'. "
            "Tell them a confirmation will follow. "
            "Keep it brief and warm."
        ),
        "extract": "none",
    },
]

# Backward-compat alias
FLOW = BOOKING_FLOW

# ---------- Reschedule flow -----------------------------------------------

RESCHEDULE_FLOW: List[Dict[str, Any]] = [
    {
        "step": 0,
        "state": "COLLECT_NAME_RESCHEDULE",
        "question": "Of course — could I take your full name please?",
        "answer_field": "full_name",
        "use_llm": False,
        "extract": "name",
        "llm_instruction": None,
    },
    {
        "step": 1,
        "state": "CONFIRM_PHONE",
        "question": (
            "Just to confirm — shall I use the number "
            "you're calling from for the reschedule?"
        ),
        "answer_field": "phone_confirmed",
        "use_llm": False,
        "extract": "phone_confirm",
        "llm_instruction": None,
    },
    {
        "step": 2,
        "state": "COLLECT_PHONE",
        "question": "And the best number to reach you on?",
        "answer_field": "phone_number",
        "use_llm": False,
        "extract": "phone",
        "llm_instruction": None,
    },
    {
        "step": 3,
        "state": "COLLECT_AVAILABILITY_RESCHEDULE",
        "question": "What days or times work best for the new appointment?",
        "answer_field": "availability",
        "use_llm": False,
        "extract": "availability",
        "llm_instruction": None,
    },
    {
        "step": 4,
        "state": "PRESENT_NEW_SLOTS",
        "question": "Let me check what we have available.",
        "answer_field": "selected_slot",
        "use_llm": True,
        "llm_instruction": (
            "Call check_availability with location='alcester', "
            "duration_minutes=50, preference='{availability}'. "
            "Present up to 3 slots in this exact format: "
            "'I have found [N] available slots during that time frame. "
            "The first being [DAY] the [DDth] of [MONTH] at [H:MMam/pm], "
            "the second being [DAY] the [DDth] of [MONTH] at [H:MMam/pm], "
            "the third being [DAY] the [DDth] of [MONTH] at [H:MMam/pm]. "
            "Which would you prefer?' "
            "Use ordinal dates like 'Monday the 23rd of March at 9am'. "
            "Never deviate from this format."
        ),
        "extract": "slot_selection",
    },
    {
        "step": 5,
        "state": "CONFIRM_RESCHEDULE",
        "question": None,
        "answer_field": "reschedule_confirmed",
        "use_llm": True,
        "llm_instruction": (
            "Call reschedule_appointment with patient_name='{full_name}', "
            "phone='{phone_number}', location='alcester', "
            "new_slot_iso='{selected_slot}', duration_minutes=50. "
            "After rescheduling confirm warmly: "
            "'I've rescheduled your appointment to [new date/time]. "
            "You'll receive a confirmation text shortly. "
            "Is there anything else I can help you with?' "
            "Use ordinal dates like 'Monday the 23rd of March at 9am'."
        ),
        "extract": "none",
    },
]

# ---------- Cancel flow ---------------------------------------------------

CANCEL_FLOW: List[Dict[str, Any]] = [
    {
        "step": 0,
        "state": "COLLECT_NAME_CANCEL",
        "question": "Of course — could I take your full name please?",
        "answer_field": "full_name",
        "use_llm": False,
        "extract": "name",
        "llm_instruction": None,
    },
    {
        "step": 1,
        "state": "CONFIRM_PHONE",
        "question": (
            "Just to confirm — shall I use the number "
            "you're calling from for the cancellation?"
        ),
        "answer_field": "phone_confirmed",
        "use_llm": False,
        "extract": "phone_confirm",
        "llm_instruction": None,
    },
    {
        "step": 2,
        "state": "COLLECT_PHONE",
        "question": "And the best number we have on file for you?",
        "answer_field": "phone_number",
        "use_llm": False,
        "extract": "phone",
        "llm_instruction": None,
    },
    {
        "step": 3,
        "state": "CONFIRM_CANCEL",
        "question": None,
        "answer_field": "cancel_confirmed",
        "use_llm": True,
        "llm_instruction": (
            "Call cancel_appointment with patient_name='{full_name}', "
            "phone='{phone_number}', location='alcester'. "
            "After cancelling confirm briefly: "
            "'I've cancelled your appointment. "
            "You'll receive a confirmation text shortly. "
            "Is there anything else I can help you with?'"
        ),
        "extract": "none",
    },
]

# ---------- FAQ flow (price / insurance / hours / services) ---------------

FAQ_FLOW: List[Dict[str, Any]] = [
    {
        "step": 0,
        "state": "ANSWER_FAQ",
        "question": None,   # LLM generates the full answer
        "answer_field": "faq_answered",
        "use_llm": True,
        "llm_instruction": (
            "Call get_clinic_info with topic='{faq_topic}'. "
            "Answer the caller's question naturally and concisely — "
            "one or two sentences. "
            "After answering ask: 'Is there anything else I can help "
            "you with, or would you like to book an appointment?'"
        ),
        "extract": "none",
    },
    {
        "step": 1,
        "state": "FAQ_BOOKING_OFFER",
        "question": None,   # LLM already asked — wait for caller reply
        "answer_field": "faq_booking_response",
        "use_llm": False,
        "extract": "faq_booking",
        "llm_instruction": None,
    },
]

# Intent → FAQ topic mapping
_INTENT_TO_FAQ_TOPIC = {
    "faq_prices":    "prices",
    "faq_insurance": "insurance",
    "faq_hours":     "hours",
    "faq_location":  "address",
    "faq_services":  "services",
}


# ---------------------------------------------------------------------------
# Flow engine
# ---------------------------------------------------------------------------

class FlowEngine:
    """
    Drives the Susie booking conversation one step at a time.

    The engine owns ALL conversation decisions.  connection.py just feeds it
    transcripts; it plays the right phrase and advances the step.

    Constructor args:
        session    — the call's live session dict (mutated in place)
        tts_queue  — asyncio.Queue; put text here to synthesise via TTS
        llm_fn     — async callable (instruction: str) -> str
                     calls the LLM and streams output to tts_queue internally;
                     returns the full response text
    """

    def __init__(
        self,
        session: Dict[str, Any],
        tts_queue: Any,             # asyncio.Queue
        llm_fn: Callable,           # async (instruction: str) -> str
    ) -> None:
        self.session          = session
        self._tts             = tts_queue
        self._llm             = llm_fn
        self._active_flow: List[Dict[str, Any]] = DETECT_INTENT_FLOW
        self._intent_detected: bool = False

    # ── public API ────────────────────────────────────────────────────────

    def current_step(self) -> Optional[Dict[str, Any]]:
        """Return the current active-flow step dict, or None if flow is complete."""
        idx = self.session.get("flow_step", 0)
        if idx >= len(self._active_flow):
            return None
        return self._active_flow[idx]

    def is_complete(self) -> bool:
        """True when all steps in the active flow have been completed."""
        return self.session.get("flow_step", 0) >= len(self._active_flow)

    async def ask_current_question(self) -> None:
        """
        Play the current step's question (or call LLM to generate it).

        Called ONCE when the first caller utterance arrives — this starts
        the flow by playing step 0's booking-open question.
        """
        step = self.current_step()
        if step is None:
            logger.info("[ms_flow] ask_current_question: flow already complete")
            return

        # DETECT_INTENT step has no question — wait silently for caller to speak
        if not step["use_llm"] and step["question"] is None:
            logger.info("[ms_flow] step %d (%s): no question to play — waiting for transcript",
                        step["step"], step["state"])
            return

        # For PRESENT_SLOTS / PRESENT_NEW_SLOTS: ensure location is set so
        # check_availability never asks the caller mid-booking.
        if step["state"] in ("PRESENT_SLOTS", "PRESENT_NEW_SLOTS"):
            self.session.setdefault("selected_location", "alcester")
            logger.info(
                "[ms_flow] %s: selected_location=%r",
                step["state"], self.session["selected_location"],
            )

        # CONFIRM_PHONE: skip if no Twilio number — go straight to COLLECT_PHONE
        if step["state"] == "CONFIRM_PHONE" and not self.session.get("phone_from_twilio"):
            self.session["flow_step"] = step["step"] + 1
            logger.info("[ms_flow] no Twilio number — skipping CONFIRM_PHONE")
            await self.ask_current_question()
            return

        # COLLECT_PHONE: skip if Twilio number was confirmed in CONFIRM_PHONE
        if step["state"] == "COLLECT_PHONE" and self.session.get("phone_confirmed"):
            phone = (
                self.session.get("phone_number")
                or self.session.get("collected", {}).get("phone")
                or self.session.get("twilio_from", "")
            )
            self.session[step["answer_field"]] = phone
            self.session["flow_step"] = step["step"] + 1
            logger.info("[ms_flow] phone confirmed from Twilio — skipping COLLECT_PHONE")
            await self.ask_current_question()
            return

        if step["use_llm"]:
            # If the step has an immediate phrase (e.g. "Let me check…"), say it first
            if step["question"]:
                await self._tts.put(step["question"])
            # Build instruction, filling in session fields
            try:
                instruction = step["llm_instruction"].format(**self.session)
            except (KeyError, AttributeError) as exc:
                logger.warning(
                    "[ms_flow] instruction format failed step=%d: %r — using raw template",
                    step["step"], exc,
                )
                instruction = step["llm_instruction"] or ""
            response = await self._llm(instruction)
            # Store the LLM-generated question so SilenceHandler can re-ask it
            _q = response or (step["question"] or "")
            if _is_question_worth_storing(_q):
                self.session["last_question"] = _q
            # After check_availability runs (inside _llm), save slots_offered so
            # the slot confirmation phrase can reference the full slot text strings.
            if step["state"] in ("PRESENT_SLOTS", "PRESENT_NEW_SLOTS"):
                offered = self.session.get("last_offered_slots") or []
                if offered:
                    self.session["slots_offered"] = list(offered)
                    self.session["slots_count"]   = len(offered)
                    logger.info(
                        "[ms_flow] slots_offered saved: %d slots",
                        len(offered),
                    )
        else:
            await self._tts.put(step["question"])
            if _is_question_worth_storing(step["question"]):
                self.session["last_question"] = step["question"]

        logger.info(
            "[ms_flow] asked step %d (%s) last_question=%r",
            step["step"], step["state"],
            str(self.session.get("last_question", ""))[:80],
        )

    async def handle_transcript(self, transcript: str) -> None:
        """
        Extract an answer from the caller's utterance, advance the step,
        and ask the next question.

        This is the ONLY function called on incoming transcripts.
        If no answer is extracted, re-ask the current question.
        """
        step = self.current_step()
        if step is None:
            logger.info("[ms_flow] flow complete — ignoring transcript: %r", transcript[:60])
            return

        text = transcript.strip().lower()

        # ── SLOT CONFIRMATION: waiting for yes/no after slot selection ────────
        if self.session.get("slot_pending_confirmation"):
            await self._handle_slot_confirmation(text, transcript)
            return

        # ── DETECT_INTENT: route to correct flow on first utterance ───────────
        if step["state"] == "DETECT_INTENT":
            intent = self._detect_intent(text)
            self.session["intent"] = intent
            self._switch_flow(intent)
            await self.ask_current_question()
            return

        # ── FAQ_BOOKING_OFFER: yes → switch to booking, no → goodbye ─────────
        if step["state"] == "FAQ_BOOKING_OFFER":
            answer = self._extract("faq_booking", text, transcript)
            if answer == "book":
                self._switch_flow("booking")
                await self.ask_current_question()
                return
            elif answer == "done":
                await self._tts.put(
                    "Great — thanks for calling Theorem Health. Have a lovely day!"
                )
                self.session["flow_step"] = len(self._active_flow)
                return
            else:
                await self._tts.put(
                    "Sorry, I didn't quite catch that — "
                    "would you like to book an appointment?"
                )
                return

        answer = self._extract(step["extract"], text, transcript)

        if answer is None:
            # No valid answer extracted — gentle re-ask
            logger.info(
                "[ms_flow] no answer for step %d (%s) from %r — re-asking",
                step["step"], step["answer_field"], transcript[:60],
            )
            last_q = self.session.get("last_question", "")
            phrase = (
                f"Sorry, I didn't quite catch that — {last_q}"
                if last_q
                else "Sorry, I didn't quite catch that."
            )
            await self._tts.put(phrase)
            # Keep last_question unchanged so SilenceHandler can re-ask again
            return

        # CONFIRM_PHONE: declined path — clear Twilio number, collect manually
        if step["state"] == "CONFIRM_PHONE" and answer is False:
            self.session["phone_confirmed"]  = False
            self.session["phone_from_twilio"] = False
            self.session["phone_number"]     = None
            collected = self.session.setdefault("collected", {})
            collected.pop("phone", None)
            self.session["flow_step"] = step["step"] + 1  # → COLLECT_PHONE
            phrase = (
                "No problem — what number would you like to use for the booking?"
            )
            await self._tts.put(phrase)
            if _is_question_worth_storing(phrase):
                self.session["last_question"] = phrase
            logger.info("[ms_flow] CONFIRM_PHONE declined — will collect manually")
            return

        # Store the answer
        self.session[step["answer_field"]] = answer
        # Mirror into collected{} for LLM context
        if step["answer_field"] in ("full_name", "phone_number", "new_or_returning"):
            col = self.session.setdefault("collected", {})
            if step["answer_field"] == "full_name":
                col["full_name"] = answer
                col["name"]      = answer
            elif step["answer_field"] == "phone_number":
                col["phone"] = answer
            elif step["answer_field"] == "new_or_returning":
                col["patient_type"] = answer

        logger.info(
            "[ms_flow] step %d %s=%r",
            step["step"], step["answer_field"], str(answer)[:60],
        )

        # ── SLOT CONFIRMATION: intercept before advancing ──────────────────
        # After slot selection, confirm with the caller before moving to name
        # collection.  flow_step is NOT advanced here — it advances in
        # _handle_slot_confirmation when the caller says yes.
        if step["state"] in ("PRESENT_SLOTS", "PRESENT_NEW_SLOTS"):
            self.session["slot_pending_confirmation"] = True
            slot_text = str(answer)
            phrase = (
                f"Just to confirm — you'd like the {slot_text} appointment. "
                f"Is that right?"
            )
            await self._tts.put(phrase)
            if _is_question_worth_storing(phrase):
                self.session["last_question"] = phrase
            logger.info("[ms_flow] slot confirmation requested: %r", phrase[:80])
            return

        # Advance to next step
        self.session["flow_step"] = step["step"] + 1
        logger.info("[ms_flow] → step %d", step["step"] + 1)

        # Ask the next question
        await self.ask_current_question()

    # ── intent routing ────────────────────────────────────────────────────

    def _detect_intent(self, text: str) -> str:
        """
        Classify the caller's first utterance into one of seven intent strings.
        Returns "booking" as the default fallback.
        """
        reschedule_p = (
            "reschedule", "change my appointment", "move my appointment",
            "change the time", "different time", "different day",
            "rebook", "move it",
        )
        cancel_p = (
            "cancel", "cancellation", "don't want", "dont want", "not coming",
            "won't be able", "wont be able", "need to cancel", "want to cancel",
        )
        insurance_p = (
            "insurance", "bupa", "axa", "aviva", "vitality",
            "covered", "cover", "claim", "health insurance",
        )
        price_p = (
            "price", "cost", "how much", "charge", "fee", "rates", "pricing",
        )
        hours_p = (
            "hours", "opening hours", "open", "close",
            "when are you", "what time are you",
        )
        location_p = (
            "where are you", "address", "parking", "directions", "how do i get",
        )
        services_p = (
            "services", "treatments", "what do you offer", "what do you do",
            "what can you help", "what conditions",
        )
        if any(p in text for p in reschedule_p): return "reschedule"
        if any(p in text for p in cancel_p):     return "cancel"
        if any(p in text for p in insurance_p):  return "faq_insurance"
        if any(p in text for p in price_p):      return "faq_prices"
        if any(p in text for p in hours_p):      return "faq_hours"
        if any(p in text for p in location_p):   return "faq_location"
        if any(p in text for p in services_p):   return "faq_services"
        return "booking"

    def _switch_flow(self, intent: str) -> None:
        """
        Switch _active_flow to the flow matching the given intent and
        reset flow_step to 0.
        """
        _faq_intents = {
            "faq_prices", "faq_insurance", "faq_hours",
            "faq_location", "faq_services",
        }
        if intent == "reschedule":
            self._active_flow = RESCHEDULE_FLOW
        elif intent == "cancel":
            self._active_flow = CANCEL_FLOW
        elif intent in _faq_intents:
            self._active_flow = FAQ_FLOW
            self.session["faq_topic"] = _INTENT_TO_FAQ_TOPIC.get(intent, "services")
        else:
            self._active_flow = BOOKING_FLOW
        self.session["flow_step"] = 0
        self.session["selected_location"] = "alcester"   # always alcester — no question asked
        self._intent_detected = True
        logger.info(
            "[ms_flow] intent=%s → flow[0]=%s",
            intent, self._active_flow[0]["state"],
        )

    # ── slot confirmation ─────────────────────────────────────────────────

    async def _handle_slot_confirmation(self, text: str, transcript: str) -> None:
        """
        Handle the yes/no response after Susie has confirmed a slot selection.

        yes → clear flag, advance flow_step past PRESENT_SLOTS, ask next question
        no  → clear flag, clear selected_slot, re-ask which slot they prefer
              (does NOT re-run LLM/check_availability — slots are still offered)
        no match → re-ask the confirmation phrase
        """
        yes_patterns = [
            "yes", "yeah", "ya", "yep", "yup", "correct",
            "that's right", "thats right", "perfect",
            "sounds good", "that works", "go ahead",
            "please", "ok", "okay", "sure", "fine",
            "that one", "confirmed",
        ]
        no_patterns = [
            "no", "nope", "wrong", "different",
            "not that", "actually no", "change",
            "other one", "different one",
        ]

        for p in yes_patterns:
            if p in text:
                logger.info("[ms_flow] slot confirmation: YES matched=%r", p)
                self.session["slot_pending_confirmation"] = False
                step = self.current_step()
                if step:
                    self.session["flow_step"] = step["step"] + 1
                await self.ask_current_question()
                return

        for p in no_patterns:
            if p in text:
                logger.info("[ms_flow] slot confirmation: NO matched=%r", p)
                self.session["slot_pending_confirmation"] = False
                self.session["selected_slot"] = None
                # Stay at PRESENT_SLOTS — caller picks again from already-offered slots.
                # Do NOT re-run ask_current_question (that would re-call the LLM).
                phrase = "No problem — which slot would you prefer?"
                await self._tts.put(phrase)
                if _is_question_worth_storing(phrase):
                    self.session["last_question"] = phrase
                return

        # No match — re-ask the confirmation phrase
        last_q = self.session.get("last_question", "")
        phrase = (
            f"Sorry, I didn't quite catch that — {last_q}"
            if last_q
            else "Sorry, I didn't quite catch that."
        )
        await self._tts.put(phrase)

    # ── extraction ────────────────────────────────────────────────────────

    def _extract(self, method: str, text: str, raw: str) -> Optional[Any]:
        """
        Extract a typed answer from the caller's normalised text.

        Returns the extracted value, or None if no valid answer was found.
        """

        # ----- any: any non-empty response is valid ----------------------
        if method == "any":
            return raw.strip() if text.strip() else None

        # ----- duration: time-period / quantity signals ------------------
        if method == "duration":
            signals = (
                "day", "days", "week", "weeks", "month", "months",
                "year", "years", "hour", "hours", "while", "ago",
                "since", "recently", "just", "about", "couple", "few",
                "one", "two", "three", "four", "five", "six", "seven",
                "eight", "nine", "ten", "always", "long", "time",
                "yesterday", "today", "morning",
                # Northern English / informal duration expressions
                "a fair while", "good while", "donkey's years",
                "ages like", "not long like", "recently like",
                "not long ago", "a good while",
                "on and off like", "comes and goes",
                "been plaguing me", "been at me",
                "right long time", "forever like",
                "ages", "forever", "plaguing",
            )
            return raw.strip() if any(s in text for s in signals) else None

        # ----- yes_no: affirmative confirmation --------------------------
        if method == "yes_no":
            yes = (
                "yes", "yeah", "ya", "yep", "yup", "ok", "okay",
                "sure", "fine", "alright", "sounds good", "go ahead",
                "please", "that works", "correct", "definitely",
                "of course", "absolutely",
                # Northern English / informal affirmatives
                "aye", "aye go on", "go on then",
                "right then", "fair enough",
                "sound", "sorted", "champion",
                "mint that", "yeah go on",
                "right okay", "alright then",
                "that'll do", "that sounds right",
                "reight", "reight then",
                "no bother", "yeah that's fine",
                "that's sound", "perfect that",
            )
            if any(p in text for p in yes):
                return True
            # Fuzzy fallback for yes_no
            yes_fuzzy = [
                "yes", "yeah", "that's fine", "sounds good",
                "go ahead", "that works",
            ]
            if _fuzzy_match(text, yes_fuzzy, threshold=75):
                logger.info("[ms_extract] fuzzy yes: '%s'", text)
                return True
            return None

        # ----- new_or_returning ------------------------------------------
        if method == "new_or_returning":
            # CRITICAL: new_patterns checked FIRST.
            # "i have not" contains "i have" — if returning were checked first
            # it would incorrectly match as returning.  Order must never change.
            new_patterns = [
                "i have not", "i haven't", "i havent",
                "have not been", "haven't been", "havent been",
                "not been", "never been", "never visited",
                "never", "first time", "first visit",
                "new patient", "i'm new", "im new",
                "no i", "nah", "nope",
                "no never", "not visited", "don't think",
                "dont think", "no not", "not really",
                # Northern English / informal variants
                "never 'ad", "not 'ad", "me first",
                "first time like", "never like",
                "nope never", "nah never",
                "haven't no", "not as such",
                "don't think so", "not that i know",
                "new to you", "new here",
                "no i 'ave not", "no i havent",
                "no", "never",
            ]
            returning_patterns = [
                "i have been", "i've been", "ive been",
                "yeah i have", "yes i have", "yep i have",
                "been before", "been there", "been with you",
                "been a patient", "been here", "come before",
                "visited before", "existing", "returning",
                "yeah", "yes", "yep", "yup", "ya",
                "i have", "have been",
                # Northern English / informal variants
                "aye", "aye i have", "aye been",
                "yeah been", "yep been",
                "been a few times", "few times",
                "come before like", "been like",
                "i 'ave", "i ave been",
                "visited", "existing patient", "registered",
            ]
            matched = False
            for p in new_patterns:
                if p in text:
                    logger.info(
                        "[ms_extract] new_or_returning=new matched=%r transcript=%r",
                        p, raw[:60],
                    )
                    matched = True
                    return "new"
            if not matched:
                for p in returning_patterns:
                    if p in text:
                        logger.info(
                            "[ms_extract] new_or_returning=returning matched=%r transcript=%r",
                            p, raw[:60],
                        )
                        return "returning"
            # Fuzzy fallback for new_or_returning
            new_fuzzy = [
                "not been", "never been", "first time",
                "have not", "haven't been", "new patient",
            ]
            returning_fuzzy = [
                "been before", "been there", "have been",
                "visited before", "existing patient",
            ]
            if _fuzzy_match(text, new_fuzzy, threshold=75):
                logger.info("[ms_extract] fuzzy new: '%s'", text)
                return "new"
            if _fuzzy_match(text, returning_fuzzy, threshold=75):
                logger.info("[ms_extract] fuzzy returning: '%s'", text)
                return "returning"
            return None

        # ----- availability: day / time references ----------------------
        if method == "availability":
            signals = (
                "monday", "tuesday", "wednesday", "thursday", "friday",
                "saturday", "sunday", "morning", "afternoon", "evening",
                "next week", "this week", "after", "before", "anytime",
                "any day", "flexible", "weekday", "weekend", "today",
                "tomorrow", "week", "from", "starting", "available", "free",
                # Northern English / informal availability expressions
                "any road", "whenever suits",
                "whenever really", "flexible like",
                "don't mind like", "not bothered",
                "owt really", "any time like",
                "whenever tha can", "whenever you can",
                "as soon as", "sharpish",
                "fairly soon", "sooner the better",
                "whenever", "anytime",
            )
            return raw.strip() if any(s in text for s in signals) else None

        # ----- slot_selection: which appointment slot the caller chose ---
        if method == "slot_selection":
            offered     = self.session.get("last_offered_slots") or []
            labels      = self.session.get("slot_labels") or []
            slots_count = self.session.get("slots_count", len(offered) or 3)

            def _pick(idx: int) -> Optional[Any]:
                """Return the human-readable slot label at 0-based index.
                slot_labels contains strings like 'Mon 23 Mar at 09:00'
                which book_appointment can resolve AND the confirmation phrase
                can repeat verbatim.  Falls back to raw slot dict (book_appointment
                handles that too), then to a plain number."""
                if labels and idx < len(labels):
                    return labels[idx]
                if offered and idx < len(offered):
                    return offered[idx]
                return str(idx + 1)

            # "last / final" catch-all → highest slot
            last_p = (
                "last one", "the last one", "final one", "the final one",
                "the last", "last option", "last slot", "final slot",
                "final option", "that last one", "the final",
                # Northern English / informal
                "last un", "t'last", "final un",
            )
            if any(p in text for p in last_p):
                available = len(labels) or len(offered) or slots_count
                idx = min(slots_count, available) - 1
                logger.info("[ms_flow] slot_selection last/final → idx=%d", idx)
                return _pick(idx)

            # Generic "that one" patterns — treated as slot 1 (index 0) since
            # the caller will be asked to confirm and can correct if needed
            generic_p = (
                "that un", "that one there",
                "that'll do", "that suits", "that works for me like",
            )
            if any(p in text for p in generic_p):
                logger.info("[ms_flow] slot_selection generic → idx=0")
                return _pick(0)

            # Numbered patterns
            slot_map = {
                0: ("first", "one", "1", "option one", "number one",
                    "first one", "the first", "first slot", "option 1",
                    # Northern English / informal
                    "first un", "t'first"),
                1: ("second", "two", "2", "option two", "number two",
                    "second one", "the second", "second slot", "option 2",
                    "middle",
                    # Northern English / informal
                    "middle un", "t'second"),
                2: ("third", "three", "3", "option three", "number three",
                    "third one", "the third", "third slot", "option 3"),
            }
            for idx, patterns in slot_map.items():
                if idx < slots_count:
                    if any(p in text for p in patterns):
                        logger.info("[ms_flow] slot_selection idx=%d", idx)
                        return _pick(idx)

            # Fuzzy fallback for slot_selection
            slot_fuzzy = {
                "1": ["first one", "first slot", "first option"],
                "2": ["second one", "second slot", "middle one"],
                "3": ["third one", "last one", "final one"],
            }
            for slot, fuzzy_patterns in slot_fuzzy.items():
                if int(slot) <= slots_count:
                    if _fuzzy_match(text, fuzzy_patterns, threshold=70):
                        logger.info(
                            "[ms_extract] fuzzy slot=%s: '%s'", slot, text
                        )
                        return _pick(int(slot) - 1)
            return None

        # ----- phone_confirm: yes/no to using the Twilio caller-ID number --
        if method == "phone_confirm":
            yes_p = (
                "yes", "yeah", "yep", "yup", "sure", "that's fine",
                "thats fine", "correct", "that one", "use that",
                "yes please", "that's the one", "go ahead", "ok",
                "okay", "fine", "sounds good", "that works",
            )
            no_p = (
                "no", "nope", "different", "another", "use another",
                "different number", "no different", "actually no",
                "not that one", "different one",
            )
            for p in yes_p:
                if p in text:
                    return True
            for p in no_p:
                if p in text:
                    return False
            return None

        # ----- name: 1-5 word name ---------------------------------------
        if method == "name":
            words = raw.strip().split()
            return raw.strip() if 1 <= len(words) <= 5 else None

        # ----- phone: 10+ digit number ----------------------------------
        if method == "phone":
            digits = "".join(c for c in raw if c.isdigit())
            return digits if len(digits) >= 10 else None

        # ----- none: no extraction needed (LLM confirmation steps) ------
        if method == "none":
            return True

        # ----- location_selection: Alcester or Redditch ------------------
        if method == "location_selection":
            if any(p in text for p in ("alcester", "alchester", "alster", "first", "one", "1")):
                return "alcester"
            if any(p in text for p in ("redditch", "reditch", "second", "two", "2")):
                return "redditch"
            return None

        # ----- faq_booking: wants to book after FAQ answer ---------------
        if method == "faq_booking":
            yes_p = (
                "yes", "yeah", "book", "booking", "appointment",
                "please", "sure", "i would", "i'd like",
            )
            no_p = (
                "no", "nope", "that's all", "thats all", "nothing else",
                "thanks", "thank you", "bye", "goodbye", "no thank",
            )
            for p in yes_p:
                if p in text: return "book"
            for p in no_p:
                if p in text: return "done"
            return None

        # ----- intent: classify first caller utterance -------------------
        if method == "intent":
            # Handled as a special case in handle_transcript(); this path
            # is a safety fallback so _extract() never returns None for it.
            return self._detect_intent(text)

        logger.warning("[ms_flow] unknown extract method: %r", method)
        return None
