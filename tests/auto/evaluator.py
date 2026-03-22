"""
evaluator.py — Pass/fail evaluation for a single Susie test call.

Two-stage evaluation:
  1. Rule-based checks  — fast, deterministic, no API call needed
  2. Claude evaluation  — semantic checks that need language understanding

Results are merged into a single checks dict. A scenario passes only if
every check is True.
"""

import asyncio
import json
import logging

import anthropic

from .config import ANTHROPIC_API_KEY
from .transcript import build_transcript

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

# Mapping: Claude check name → expected dict key that must be present for it to gate.
# If the scenario's expected dict doesn't have the corresponding key, the Claude
# check is recorded as informational only and never causes a FAIL.
_CLAUDE_GATE_MAP: dict[str, str] = {
    "flow_order_correct":       "correct_order",
    "no_question_asked_twice":  "no_question_asked_twice",
    "new_or_returning_correct": "new_or_returning_correct",
    "empathy_response_present": "empathy_response_present",
    "empathy_contains_condition": "empathy_contains_condition",
    "duration_question_asked":  "duration_question_asked",
    "slot_confirmed":           "slot_confirmed",
    "booking_confirmed":        "booking_confirmed",
    "no_state_corruption":      "no_state_corruption",
    "graceful_end":             "graceful_end",
    # banned_phrases_absent is handled by the rule-based "not_said" check in 7.4;
    # never auto-gate here (Claude may hallucinate false negatives on empty transcripts).
}

_CLAUDE_PROMPT = """\
You are evaluating a test call with Susie, an AI receptionist for a physiotherapy clinic.

SCENARIO: {scenario_name}

TRANSCRIPT:
{transcript}

Evaluate the following and respond with ONLY a valid JSON object — no preamble, \
no markdown fences, no explanation outside the JSON.

{{
  "flow_order_correct": true,
  "no_question_asked_twice": true,
  "new_or_returning_correct": true,
  "empathy_response_present": true,
  "empathy_contains_condition": true,
  "duration_question_asked": true,
  "slot_confirmed": true,
  "booking_confirmed": true,
  "no_state_corruption": true,
  "graceful_end": null,
  "banned_phrases_absent": true,
  "overall_quality": "excellent",
  "fail_details": null
}}

Field definitions:

flow_order_correct (bool)
  Did Susie follow this exact order when all steps were reached?
  1. What brings you in
  2. How long have you had that
  3. Physiotherapy assessment recommendation
  4. Have you been with us before (new vs returning)
  5. What days/times work best
  6. Present available slots
  7. Full name
  8. Phone number confirmation
  9. Booking confirmation

no_question_asked_twice (bool)
  Was any question asked more than once (excluding silence re-asks)?

new_or_returning_correct (bool | null)
  null if the scenario never reached that point.
  true  if Susie correctly identified the caller as new or returning.
  false if she said "welcome back" to a new patient, or treated a returning
        patient as new.

empathy_response_present (bool | null)
  null if not applicable.
  true if Susie responded with genuine empathy to the caller's condition
  before asking the duration question.

empathy_contains_condition (bool | null)
  null if not applicable.
  true if the empathy response specifically referenced the condition the
  caller described (e.g. "back pain", "headaches"), not just generic sympathy.

duration_question_asked (bool)
  Did Susie ask how long the caller has had their condition?

slot_confirmed (bool | null)
  null if slots were never presented.
  true if Susie read the selected slot back to the caller before moving on.

booking_confirmed (bool)
  Did Susie confirm the completed booking with the patient's name and
  appointment details before ending the call?

no_state_corruption (bool)
  Did the flow continue correctly and in the right order after any silence,
  interruption, or mid-flow question from the caller?

graceful_end (bool | null)
  null if the caller did not abandon mid-flow.
  true if Susie handled an early hang-up or "never mind" gracefully without
  errors or awkward repetition.

banned_phrases_absent (bool)
  true if NONE of these appear anywhere in Susie's turns:
  "absolutely", "certainly", "of course!", "sure thing",
  "bear with me", "i am waiting", "are you still there",
  "welcome back" (when speaking to a new patient).

overall_quality ("excellent" | "good" | "poor")
  Your holistic rating of the interaction.

fail_details (string | null)
  Concise description of any failures. null if everything passed.\
"""


# ---------------------------------------------------------------------------
# Evaluator
# ---------------------------------------------------------------------------

class Evaluator:
    def __init__(self):
        # Synchronous client — calls wrapped with asyncio.to_thread
        # so the event loop is never blocked.
        self.client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def evaluate(self, result: dict, scenario: dict) -> dict:
        """
        Evaluate a call result against the scenario's expected criteria.

        Returns:
            {
                "passed":      bool,
                "checks":      {check_name: bool, ...},
                "fail_reason": str | None,
                "detail":      str,
                "transcript":  str,
            }
        """
        transcript = build_transcript(result)
        expected = scenario.get("expected", {})

        # Stage 1 — fast rule-based checks
        rule_results = self._rule_checks(result, expected)

        # Stage 2 — Claude semantic checks (only if API key is set)
        if ANTHROPIC_API_KEY:
            try:
                claude_results = await self._claude_evaluate(
                    transcript, expected, scenario
                )
            except Exception as e:
                logger.warning(f"Claude evaluation failed: {e}")
                # Empty dict — don't gate anything on a failed Claude call
                claude_results = {}
        else:
            logger.warning("ANTHROPIC_API_KEY not set — skipping Claude evaluation")
            claude_results = {}

        # Merge; rule checks take precedence on name collision
        all_checks: dict = {**claude_results, **rule_results}

        # Only include bool values that are real gate checks.
        # Keys prefixed with "info_" are informational only (never cause FAIL).
        # Null / None values are also informational.
        gating = {
            k: v for k, v in all_checks.items()
            if isinstance(v, bool) and not k.startswith("info_")
        }

        # Guard: a scenario that defines patient responses but only produced 1
        # Susie turn (the greeting) cannot pass — Susie never processed any
        # patient audio.  This prevents false positives on phase 3 scenarios
        # (and any others) that lack flow_completed in their expected dict.
        if scenario.get("responses") and result.get("turns", 0) <= 1:
            all_checks["susie_responded_to_patient"] = False
            gating["susie_responded_to_patient"] = False

        if not gating:
            # No checks = evaluation completely failed (network down, Claude unreachable, etc.)
            passed = False
            logger.warning("Evaluation produced zero checks — marking as infrastructure failure")
        else:
            passed = all(gating.values())
        fail_reasons = [k for k, v in gating.items() if not v]

        return {
            "passed": passed,
            "checks": all_checks,
            "fail_reason": fail_reasons[0] if fail_reasons else None,
            "detail": (
                self._get_detail(fail_reasons, result)
                if fail_reasons
                else "All checks passed"
            ),
            "transcript": transcript,
        }

    # ------------------------------------------------------------------
    # Stage 1 — Rule-based checks
    # ------------------------------------------------------------------

    def _rule_checks(self, result: dict, expected: dict) -> dict:
        checks: dict = {}

        susie_turns = result.get("susie_said", [])
        susie_texts = [t["text"].lower() for t in susie_turns]
        all_susie = " ".join(susie_texts)
        first_turn = susie_texts[0] if susie_texts else ""

        # ── answered within time ──────────────────────────────────────
        if "answered_within_seconds" in expected:
            checks["answered_in_time"] = (
                result.get("duration_seconds", 999) > 0
                and len(susie_turns) > 0
            )

        # ── flow completed ────────────────────────────────────────────
        # In the TwiML-based architecture the runner never sets end_reason="complete"
        # — calls always end with "completed" (Twilio status callback after hangup).
        # We accept both values and require Susie to have had at least as many turns
        # as there are scripted patient responses, guaranteeing she responded to each.
        if expected.get("flow_completed"):
            scenario_responses = result.get("scenario", {}).get("responses", [])
            min_turns_needed = max(2, len(scenario_responses))
            checks["flow_completed"] = (
                result.get("end_reason") in ("complete", "completed")
                and result.get("turns", 0) >= min_turns_needed
            )

        # ── no technical error phrases ────────────────────────────────
        if expected.get("no_technical_error"):
            error_phrases = [
                "technical issue",
                "having a small technical",
                "technical difficulty",
                "i apologise",
            ]
            checks["no_technical_error"] = not any(
                p in all_susie for p in error_phrases
            )

        # ── not_said phrases ──────────────────────────────────────────
        for phrase in expected.get("not_said", []):
            key = f"not_said_{phrase[:20]}"
            checks[key] = phrase.lower() not in all_susie

        # ── greeting_contains ─────────────────────────────────────────
        for phrase in expected.get("greeting_contains", []):
            checks[f"greeting_has_{phrase[:15]}"] = phrase.lower() in first_turn

        # ── greeting_not_contains ─────────────────────────────────────
        for phrase in expected.get("greeting_not_contains", []):
            checks[f"greeting_no_{phrase[:15]}"] = phrase.lower() not in first_turn

        # ── first_susie_turn_contains ─────────────────────────────────
        if "first_susie_turn_contains" in expected:
            phrase = expected["first_susie_turn_contains"].lower()
            checks["first_turn_contains"] = phrase in first_turn

        # ── first_susie_turn_not_contains ─────────────────────────────
        for phrase in expected.get("first_susie_turn_not_contains", []):
            checks[f"first_turn_no_{phrase[:15]}"] = phrase.lower() not in first_turn

        # ── reask_fires ───────────────────────────────────────────────
        if expected.get("reask_fires") or expected.get("susie_reasks"):
            checks["reask_fired"] = any(
                "didn't quite catch" in t or "catch that" in t
                for t in susie_texts
            )

        # ── reask_contains ────────────────────────────────────────────
        if "reask_contains" in expected:
            phrase = expected["reask_contains"].lower()
            checks["reask_phrase_correct"] = any(phrase in t for t in susie_texts)

        # ── second_reask_fires ────────────────────────────────────────
        if expected.get("second_reask_fires"):
            checks["second_reask_fired"] = any(
                "sorry about that" in t for t in susie_texts
            )

        # ── second_reask_contains ─────────────────────────────────────
        if "second_reask_contains" in expected:
            phrase = expected["second_reask_contains"].lower()
            checks["second_reask_phrase_correct"] = any(
                phrase in t for t in susie_texts
            )

        # ── transfer_message_played ───────────────────────────────────
        if expected.get("transfer_message_played"):
            checks["transfer_played"] = any(
                "transfer" in t or "trouble hearing" in t for t in susie_texts
            )

        # ── transfer_message_contains ─────────────────────────────────
        for phrase in expected.get("transfer_message_contains", []):
            checks[f"transfer_has_{phrase[:15]}"] = any(
                phrase.lower() in t for t in susie_texts
            )

        # ── no_dead_air ───────────────────────────────────────────────
        if "no_dead_air_over_seconds" in expected:
            limit = expected["no_dead_air_over_seconds"]
            checks["no_dead_air"] = result.get("max_gap_seconds", 0) <= limit

        # ── no_crash / graceful_end ───────────────────────────────────
        if expected.get("no_crash"):
            checks["no_crash"] = result.get("end_reason") != "error"

        # ── flow_continues / flow_continues_after_silence ─────────────
        # True if at least 2 turns happened and the call didn't die on silence.
        if expected.get("flow_continues") or expected.get("flow_continues_after_silence"):
            checks["flow_continues"] = (
                result.get("turns", 0) > 1
                and result.get("end_reason") not in (
                    "timeout_no_speech", "timeout", "ngrok_died", "exception"
                )
            )

        # ── confirmation_contains ─────────────────────────────────────
        # Checks that the slot Susie read back contains the expected word,
        # e.g. "first" for scenario 4.1 where the caller chose slot 1.
        if "confirmation_contains" in expected:
            phrase = expected["confirmation_contains"].lower()
            checks["confirmation_contains"] = phrase in all_susie

        # ── number_confirmed_verbally ─────────────────────────────────
        # Susie should read the phone number back to the caller before
        # confirming the booking.  We accept either a digit string (e.g.
        # "07700900123") or five or more spoken digit words in a row.
        if expected.get("number_confirmed_verbally"):
            import re
            _DIGIT_WORD = (
                r"(?:zero|one|two|three|four|five|six|seven|eight|nine|oh)"
            )
            checks["number_confirmed_verbally"] = bool(
                # e.g. "07700 900 123" — 5+ consecutive digit characters
                re.search(r"\d{5,}", all_susie)
                # e.g. "zero seven seven zero zero nine" — 5+ digit words in a row
                or re.search(
                    rf"{_DIGIT_WORD}(?:\s+{_DIGIT_WORD}){{4,}}", all_susie
                )
            )

        return checks

    # ------------------------------------------------------------------
    # Stage 2 — Claude semantic evaluation
    # ------------------------------------------------------------------

    async def _claude_evaluate(
        self, transcript: str, expected: dict, scenario: dict
    ) -> dict:
        prompt = _CLAUDE_PROMPT.format(
            scenario_name=scenario.get("name", "Unknown"),
            transcript=transcript,
        )

        # Run sync Anthropic client off the event loop thread
        response = await asyncio.to_thread(
            self.client.messages.create,
            model="claude-sonnet-4-6",
            max_tokens=1000,
            messages=[{"role": "user", "content": prompt}],
        )

        text = response.content[0].text.strip()

        # Strip accidental markdown fences
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()

        try:
            raw: dict = json.loads(text)
        except json.JSONDecodeError as e:
            logger.warning(f"Claude returned invalid JSON on first attempt: {e}\nRaw: {text[:200]}")
            # Retry with a stricter prompt
            retry_prompt = (
                "You MUST respond with ONLY a raw JSON object. "
                "No prose, no explanation, no markdown. Just the JSON.\n\n"
                + prompt
            )
            retry_response = await asyncio.to_thread(
                self.client.messages.create,
                model="claude-sonnet-4-6",
                max_tokens=1000,
                messages=[{"role": "user", "content": retry_prompt}],
            )
            retry_text = retry_response.content[0].text.strip()
            if retry_text.startswith("```"):
                retry_text = retry_text.split("```")[1]
                if retry_text.startswith("json"):
                    retry_text = retry_text[4:]
                retry_text = retry_text.strip()
            try:
                raw: dict = json.loads(retry_text)
            except json.JSONDecodeError as e2:
                logger.error(f"Claude returned invalid JSON after retry: {e2}\nRaw: {retry_text[:200]}")
                return {}  # skip Claude gating; don't add a bool that fails evaluation

        # Filter Claude results: only gate on checks relevant to this scenario.
        # Null values are informational, non-bool values are metadata.
        result: dict = {}
        for key, value in raw.items():
            if key in ("overall_quality", "fail_details"):
                # Always keep as metadata — never a boolean gate
                result[key] = value
            elif value is None:
                # Null = not applicable — skip from gating
                pass
            elif isinstance(value, bool):
                # Only include as a gate if the scenario explicitly requires this check.
                # Checks not in _CLAUDE_GATE_MAP are always informational.
                expected_key = _CLAUDE_GATE_MAP.get(key)
                if expected_key is not None and expected_key in expected:
                    result[key] = value
                else:
                    # Store with "info_" prefix so it appears in the report but doesn't gate
                    result[f"info_{key}"] = value
            # Ignore any other types Claude may hallucinate

        return result

    # ------------------------------------------------------------------
    # Detail builder
    # ------------------------------------------------------------------

    def _get_detail(self, fail_reasons: list, result: dict) -> str:
        parts = [f"{r} failed" for r in fail_reasons[:3]]
        if len(fail_reasons) > 3:
            parts.append(f"… and {len(fail_reasons) - 3} more")
        return " | ".join(parts)
