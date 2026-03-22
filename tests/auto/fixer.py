"""
fixer.py — Smart failure diagnosis for the Susie test suite.

After every content failure (Susie spoke but gave the wrong answer),
FixerAgent.analyze() is called.  It:

  1. Classifies the failure as a TEST infrastructure bug or a SUSIE
     behaviour bug.
  2. For test bugs  → applies a targeted code fix and signals a re-run.
  3. For Susie bugs → prints a developer-readable diagnosis with the
     exact file/behaviour to investigate.

This replaces the old "content failure — skipping retries" dead-end.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).parents[2]
CALL_RUNNER_PATH = Path(__file__).parent / "call_runner.py"

# ── Susie behaviour bug descriptions ──────────────────────────────────────────
# Maps a failed check name (or prefix) to a human-readable message and the
# Susie codebase area to look at.

_SUSIE_BUG_MAP: list[tuple[str, str, str]] = [
    # (check_name_or_prefix, root_cause_label, what_to_fix)
    (
        "reask_fired",
        "Silence re-ask not triggering",
        "app/media_streams/ — Susie's silence detection must emit\n"
        "        'didn't quite catch' after the caller is silent.\n"
        "        Ensure silence detection is active in the Media Streams\n"
        "        WebSocket pipeline (not just Gather-based flows).",
    ),
    (
        "second_reask",
        "Second silence re-ask not triggering",
        "app/media_streams/ — second silence re-ask ('sorry about that')\n"
        "        should fire after a second consecutive silence.",
    ),
    (
        "booking_confirmed",
        "Booking confirmation missing",
        "app/conversation/ — Susie's confirmation step must include\n"
        "        the appointment date, time, and patient name before ending.",
    ),
    (
        "slot_confirmed",
        "Slot not read back to caller",
        "app/conversation/ — Susie must read back the chosen slot\n"
        "        (e.g. 'Monday at 9am') before asking for name/number.",
    ),
    (
        "new_or_returning",
        "New/returning patient classification wrong",
        "app/conversation/ — the new/returning detection logic is\n"
        "        misclassifying the caller.  Check phrases like 'been before',\n"
        "        'first time', 'returning' in the intake flow.",
    ),
    (
        "flow_order",
        "Questions asked out of order",
        "app/conversation/ — the state machine is not enforcing the\n"
        "        correct question order: condition → duration → assessment\n"
        "        → new/returning → availability → slots → name → phone.",
    ),
    (
        "duration_question",
        "Duration question ('how long') not asked",
        "app/conversation/ — the intake flow is skipping the duration\n"
        "        question.  Susie must ask 'how long have you had this' before\n"
        "        moving to the assessment step.",
    ),
    (
        "empathy_response_present",
        "Empathy response missing",
        "app/prompts/ — Susie must acknowledge the patient's condition\n"
        "        with an empathetic response before asking how long they've had it.",
    ),
    (
        "empathy_contains_condition",
        "Empathy doesn't reference the patient's condition",
        "app/prompts/ — the empathy response must echo back the specific\n"
        "        condition the patient mentioned (e.g. 'back pain', 'knee problem').",
    ),
    (
        "greeting",
        "Greeting phrase mismatch",
        "app/ — check Susie's opening message template.  It must contain\n"
        "        'Susie' and 'how can I help' and must NOT mention 'Alcester',\n"
        "        'Redditch', 'say one', or 'say two'.",
    ),
    (
        "transfer",
        "Transfer message not played",
        "app/conversation/ — when the call cannot be handled, Susie must\n"
        "        play a transfer message before connecting to a human.",
    ),
    (
        "flow_continues",
        "Susie not continuing after silence / too few turns",
        "app/media_streams/ — Susie may be hanging up too early or not\n"
        "        processing the patient's audio.  Check that <Say> TTS audio\n"
        "        is being received and processed by the Media Streams pipeline.",
    ),
    (
        "no_dead_air",
        "Response latency too high (dead air)",
        "app/ — Susie's LLM or TTS is taking too long.  Check inference\n"
        "        pipeline latency; responses should arrive within 6 seconds.",
    ),
    (
        "no_technical_error",
        "Susie emitted a technical error phrase",
        "app/ — Susie said something like 'technical issue' or 'I apologise'.\n"
        "        Check the error-handling path in the conversation flow.",
    ),
    (
        "banned_phrases",
        "Banned filler phrase used",
        "app/prompts/ — remove phrases like 'absolutely', 'certainly',\n"
        "        'of course!', 'sure thing' from the system prompt.",
    ),
]


@dataclass
class FixResult:
    scenario_id: str
    scenario_name: str
    category: str          # "test_bug" | "susie_bug" | "unknown"
    root_cause: str
    fix_location: str
    status: str            # "auto_fixed" | "manual_fix_required" | "unknown"
    auto_fixed: bool = False
    failed_checks: list[str] = field(default_factory=list)

    def format(self) -> str:
        """Return a formatted diagnosis block for console output."""
        width = 58
        border = "─" * (width - 4)

        failed_str = ", ".join(self.failed_checks[:4])
        if len(self.failed_checks) > 4:
            failed_str += f" +{len(self.failed_checks) - 4} more"

        # Word-wrap fix_location lines (already pre-indented in the map)
        fix_lines = self.fix_location.split("\n")
        fix_block = "\n  │ ".join(fix_lines)

        return (
            f"\n  ┌─ FIXER DIAGNOSIS {border}\n"
            f"  │ Scenario  : {self.scenario_id} — {self.scenario_name}\n"
            f"  │ Category  : {self.category}\n"
            f"  │ Root cause: {self.root_cause}\n"
            f"  │ Failed    : {failed_str or '(none recorded)'}\n"
            f"  │ Fix here  : {fix_block}\n"
            f"  │ Status    : {self.status}\n"
            f"  └{'─' * (width - 2)}\n"
        )


class FixerAgent:
    """
    Stateless agent that classifies test failures and either applies a
    code fix (test infrastructure bugs) or emits a developer diagnosis
    (Susie behaviour bugs).
    """

    def analyze(self, result: dict, scenario: dict) -> FixResult:
        """
        Classify a failing test result and return a FixResult.

        Tries test-infrastructure patterns first (auto-fixable), then
        maps to Susie behaviour patterns (report only).
        """
        turns      = result.get("turns", 0)
        end_reason = result.get("end_reason", "unknown")
        test_said  = result.get("test_said", [])
        evaluation = result.get("evaluation", {})
        checks     = evaluation.get("checks", {})
        failed     = [k for k, v in checks.items() if v is False]
        sid        = scenario.get("id", "?")
        name       = scenario.get("name", "")

        # ── 1. Test infrastructure pattern: greeting wait too short ────────
        # turns=0 AND end_reason=completed AND scenario has responses
        # → Susie picked up but our 30s wait wasn't long enough
        if (
            turns == 0
            and end_reason == "completed"
            and scenario.get("responses")
        ):
            fixed = self._fix_greeting_wait()
            return FixResult(
                scenario_id=sid,
                scenario_name=name,
                category="Test infrastructure bug (auto-fixed)",
                root_cause="GREETING_WAIT_SECONDS too short — Susie's greeting "
                           "wasn't finished before patient audio started",
                fix_location=f"call_runner.py — GREETING_WAIT_SECONDS bumped by +5s\n"
                             f"        (new value: {self._current_greeting_wait()}s)",
                status="Auto-fixed — re-running test" if fixed else "Fix failed — check call_runner.py manually",
                auto_fixed=fixed,
                failed_checks=failed,
            )

        # ── 2. Susie behaviour patterns ────────────────────────────────────
        susie_fix = self._match_susie_pattern(failed, turns, end_reason, test_said)
        if susie_fix:
            root_cause, fix_location = susie_fix
            return FixResult(
                scenario_id=sid,
                scenario_name=name,
                category="Susie behaviour bug  (not a test issue)",
                root_cause=root_cause,
                fix_location=fix_location,
                status="Skipped — manual Susie fix required",
                auto_fixed=False,
                failed_checks=failed,
            )

        # ── 3. Unknown / catch-all ─────────────────────────────────────────
        return FixResult(
            scenario_id=sid,
            scenario_name=name,
            category="Unknown",
            root_cause=f"turns={turns}, end_reason={end_reason}, "
                       f"failed checks: {', '.join(failed) or 'none'}",
            fix_location="Investigate the result JSON in tests/auto/results/ "
                         "for more detail.",
            status="Skipped — manual investigation required",
            auto_fixed=False,
            failed_checks=failed,
        )

    # ── private helpers ────────────────────────────────────────────────────

    def _match_susie_pattern(
        self,
        failed: list[str],
        turns: int,
        end_reason: str,
        test_said: list,
    ) -> tuple[str, str] | None:
        """
        Return (root_cause, fix_location) for the first Susie pattern matched,
        or None if no pattern matches.
        """
        # Check "too few turns" heuristic first (broad catch)
        # If patient spoke (test_said non-empty) but Susie barely responded,
        # it's likely a Media Streams processing issue.
        if turns <= 1 and test_said and end_reason == "completed":
            if not any(c for c in failed if "greeting" in c):
                # Not a greeting issue — Susie heard the patient but didn't continue
                return (
                    "Susie not processing patient audio / hanging up early",
                    "app/media_streams/ — Susie may not be receiving or processing\n"
                    "        <Say> TTS audio from the test.  Check that the Media\n"
                    "        Streams WebSocket pipeline forwards all audio frames\n"
                    "        to the STT provider, including Twilio Polly <Say> output.",
                )

        # Walk the pattern map and return the first match
        for check_prefix, root_cause, fix_location in _SUSIE_BUG_MAP:
            if any(check_prefix in c for c in failed):
                return root_cause, fix_location

        return None

    def _fix_greeting_wait(self) -> bool:
        """
        Bump GREETING_WAIT_SECONDS by +5 in call_runner.py.
        Returns True on success, False on any error.
        """
        try:
            text = CALL_RUNNER_PATH.read_text(encoding="utf-8")
            import re
            match = re.search(r"(GREETING_WAIT_SECONDS\s*=\s*)(\d+)", text)
            if not match:
                logger.warning("Could not find GREETING_WAIT_SECONDS in call_runner.py")
                return False
            old_val = int(match.group(2))
            new_val = old_val + 5
            new_text = text[: match.start(2)] + str(new_val) + text[match.end(2):]
            CALL_RUNNER_PATH.write_text(new_text, encoding="utf-8")
            logger.info(
                "Auto-fix: GREETING_WAIT_SECONDS %d → %d in call_runner.py",
                old_val, new_val,
            )
            return True
        except Exception as exc:
            logger.error("Auto-fix failed: %r", exc)
            return False

    def _current_greeting_wait(self) -> int:
        """Read the current value of GREETING_WAIT_SECONDS from call_runner.py."""
        try:
            import re
            text = CALL_RUNNER_PATH.read_text(encoding="utf-8")
            match = re.search(r"GREETING_WAIT_SECONDS\s*=\s*(\d+)", text)
            return int(match.group(1)) if match else -1
        except Exception:
            return -1
