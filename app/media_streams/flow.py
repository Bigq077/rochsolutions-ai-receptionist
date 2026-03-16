# app/media_streams/flow.py
"""
Linear booking flow for the Susie AI receptionist.

All conversation decisions live here.  Nothing else in the pipeline
decides what Susie says next.

The entire booking conversation is one linear array of steps.
Susie asks a question.  Caller answers.  Step advances.  Repeat.
That is the entire logic.

Usage from connection.py:
    flow = FlowEngine(session, tts_text_queue, llm_fn)
    # First caller utterance starts the flow:
    await flow.ask_current_question()
    # Every subsequent utterance goes through:
    await flow.handle_transcript(transcript)
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Coroutine, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Flow definition
# ---------------------------------------------------------------------------

FLOW: List[Dict[str, Any]] = [
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
            "Call check_availability with location='{selected_location}', "
            "duration_minutes=50, preference='{availability}'. "
            "Do NOT ask the caller about location — use {selected_location}. "
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
        self.session   = session
        self._tts      = tts_queue
        self._llm      = llm_fn

    # ── public API ────────────────────────────────────────────────────────

    def current_step(self) -> Optional[Dict[str, Any]]:
        """Return the current FLOW step dict, or None if flow is complete."""
        idx = self.session.get("flow_step", 0)
        if idx >= len(FLOW):
            return None
        return FLOW[idx]

    def is_complete(self) -> bool:
        """True when all steps have been completed."""
        return self.session.get("flow_step", 0) >= len(FLOW)

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

        # For PRESENT_SLOTS: ensure a location is set so check_availability doesn't
        # ask the caller about Alcester vs Redditch mid-booking.
        if step["state"] == "PRESENT_SLOTS":
            self.session.setdefault("selected_location", "alcester")
            logger.info(
                "[ms_flow] PRESENT_SLOTS: selected_location=%r",
                self.session["selected_location"],
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
            self.session["last_question"] = response or (step["question"] or "")
        else:
            await self._tts.put(step["question"])
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

        text   = transcript.strip().lower()
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

        # Advance to next step
        self.session["flow_step"] = step["step"] + 1
        logger.info("[ms_flow] → step %d", step["step"] + 1)

        # Ask the next question
        await self.ask_current_question()

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
            )
            return raw.strip() if any(s in text for s in signals) else None

        # ----- yes_no: affirmative confirmation --------------------------
        if method == "yes_no":
            yes = (
                "yes", "yeah", "ya", "yep", "yup", "ok", "okay",
                "sure", "fine", "alright", "sounds good", "go ahead",
                "please", "that works", "correct", "definitely",
                "of course", "absolutely",
            )
            return True if any(p in text for p in yes) else None

        # ----- new_or_returning ------------------------------------------
        if method == "new_or_returning":
            new_p = (
                "not been", "never been", "i have not", "i haven't",
                "i havent", "havent been", "haven t been",
                "first time", "new patient", "i'm new", "im new",
                "haven't been", "have not been", "never", "first visit",
                "never visited", "not visited", "no i", "no,", "nope",
                "nah", "not really",
            )
            ret_p = (
                "yes", "yeah", "ya", "yep", "yup", "been before",
                "i have been", "existing", "returning", "been there",
                "come before", "visited before", "been with you",
                "been a patient", "been here", "i've been", "i ve been",
                "have been",
            )
            # Returning checked FIRST — strongest signal wins
            for p in ret_p:
                if p in text:
                    return "returning"
            # Explicit new-patient phrases
            if text.strip() in ("no", "nope", "nah", "never"):
                return "new"
            for p in new_p:
                if p in text:
                    return "new"
            # Word-level negative fallback: catches "I have not", "No I haven't", etc.
            _neg_words = {"no", "not", "never", "nope", "nah", "havent", "haven't"}
            if any(w in _neg_words for w in text.split()):
                return "new"
            return None

        # ----- availability: day / time references ----------------------
        if method == "availability":
            signals = (
                "monday", "tuesday", "wednesday", "thursday", "friday",
                "saturday", "sunday", "morning", "afternoon", "evening",
                "next week", "this week", "after", "before", "anytime",
                "any day", "flexible", "weekday", "weekend", "today",
                "tomorrow", "week", "from", "starting", "available", "free",
            )
            return raw.strip() if any(s in text for s in signals) else None

        # ----- slot_selection: which appointment slot the caller chose ---
        if method == "slot_selection":
            offered     = self.session.get("last_offered_slots") or []
            slots_count = self.session.get("slots_count", len(offered) or 3)

            def _pick(idx: int) -> Optional[Any]:
                """Return the slot at 0-based index, or the index string as fallback."""
                if offered and idx < len(offered):
                    return offered[idx]
                return str(idx + 1)

            # "last / final" catch-all → highest slot
            last_p = (
                "last one", "the last one", "final one", "the final one",
                "the last", "last option", "last slot", "final slot",
                "final option", "that last one", "the final",
            )
            if any(p in text for p in last_p):
                idx = min(slots_count, len(offered) if offered else slots_count) - 1
                logger.info("[ms_flow] slot_selection last/final → idx=%d", idx)
                return _pick(idx)

            # Numbered patterns
            slot_map = {
                0: ("first", "one", "1", "option one", "number one",
                    "first one", "the first", "first slot", "option 1"),
                1: ("second", "two", "2", "option two", "number two",
                    "second one", "the second", "second slot", "option 2",
                    "middle"),
                2: ("third", "three", "3", "option three", "number three",
                    "third one", "the third", "third slot", "option 3"),
            }
            for idx, patterns in slot_map.items():
                if idx < slots_count:
                    if any(p in text for p in patterns):
                        logger.info("[ms_flow] slot_selection idx=%d", idx)
                        return _pick(idx)
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

        logger.warning("[ms_flow] unknown extract method: %r", method)
        return None
