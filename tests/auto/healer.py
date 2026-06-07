"""
healer.py — Per-test diagnosis and retry logic.

After every test:
  1. build_diagnosis()  → prints a structured summary of what happened
  2. run_fixer_agent()  → decides whether to retry or skip

Healing strategy (simplified and reliable):
  • Infrastructure failure (0 turns, ngrok dead, timeout) → retry immediately
  • Any other failure → retry once more (gives the server a chance to warm up)
  • After max_attempts reached → log and continue to next scenario

NOTE: Code-editing and Render-redeploy were removed from the healer because
they caused Render to restart mid-run, which made subsequent tests fail with
0 turns.  Real Susie server bugs are fixed directly in the codebase before
running the test suite.
"""

from __future__ import annotations

import logging
from pathlib import Path

from .transcript import build_transcript

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).parents[2]


class Healer:
    """
    Stateful per-run healer.
    Tracks consecutive infrastructure failures so we can report clearly.
    """

    def __init__(self):
        self._infra_fail_streak = 0

    # ── public API ────────────────────────────────────────────────────────────

    def build_diagnosis(self, result: dict, scenario: dict) -> str:
        """Return a formatted per-test diagnosis block (always printed)."""
        evaluation = result.get("evaluation", {})
        passed     = evaluation.get("passed", False)
        status     = "✓ PASS" if passed else "✗ FAIL"
        turns      = result.get("turns", 0)
        end_reason = result.get("end_reason", "unknown")
        duration   = result.get("duration_seconds", 0.0)

        lines = [
            "═" * 58,
            f"[{scenario['id']}]  {scenario['name']}  →  {status}",
            f"  turns={turns}  end_reason={end_reason}  duration={duration:.1f}s",
        ]

        # Transcript (capped at 25 lines)
        transcript = evaluation.get("transcript") or build_transcript(result)
        if transcript:
            lines.append("  Transcript:")
            for tline in transcript.strip().split("\n")[:25]:
                lines.append(f"    {tline}")
        else:
            lines.append("  Transcript: (empty — no turns recorded)")

        if not passed:
            checks      = evaluation.get("checks", {})
            fail_checks = [k for k, v in checks.items() if v is False]
            if fail_checks:
                lines.append("  Failed checks:")
                for c in fail_checks:
                    lines.append(f"    • {c}")

            detail = evaluation.get("detail", "")
            if detail:
                lines.append(f"  Evaluator detail: {detail}")

            # Root cause classification
            cause = self._classify(turns, end_reason, fail_checks)
            lines.append(f"  Root cause: {cause}")

        lines.append("═" * 58)
        return "\n".join(lines)

    def should_retry(self, result: dict) -> bool:
        """
        Return True only for transient infrastructure failures worth retrying.

        Content failures (Susie gave the wrong answer) are NEVER retried —
        the same call will produce the same wrong answer and just wastes
        Twilio credits.  Only retry when the conversation never happened at
        all (ngrok died, Twilio timeout, server cold-start, exception).
        """
        turns      = result.get("turns", 0)
        end_reason = result.get("end_reason", "unknown")

        if self._is_infrastructure_failure(result):
            self._infra_fail_streak += 1
            print(
                f"  Healer: Infrastructure failure "
                f"(streak={self._infra_fail_streak}) — will retry once"
            )
            return True

        # Susie spoke but gave the wrong answer — retrying won't fix it
        self._infra_fail_streak = 0
        checks      = result.get("evaluation", {}).get("checks", {})
        fail_checks = [k for k, v in checks.items() if v is False]
        print(
            f"  Healer: Content failure ({', '.join(fail_checks[:3])}) "
            f"— Susie behaviour issue, skipping retries"
        )
        return False

    # ── helpers ──────────────────────────────────────────────────────────────

    @staticmethod
    def _is_infrastructure_failure(result: dict) -> bool:
        turns      = result.get("turns", 0)
        end_reason = result.get("end_reason", "unknown")

        # Classic infrastructure failures — no conversation at all
        if turns == 0:
            return True
        if end_reason in (
            "timeout", "timeout_no_speech", "ngrok_died", "exception",
            "direct_ws_session_lost", "direct_ws_interrupted",
            "ws_keepalive_timeout", "handler_not_ready",
        ):
            return True

        # Early call drop: Twilio reported "completed" but the flow barely started.
        # This happens when the Render WebSocket drops mid-call or Twilio routes
        # the call away before all patient audio is injected.
        # Signature: scenario expects flow_completed, but booking never confirmed
        # and flow_step is still in the first 2 steps (greeting + first question).
        scenario          = result.get("scenario", {})
        expected          = scenario.get("expected", {})
        n_responses       = len(scenario.get("responses", []))
        flow_step         = result.get("flow_step")
        booking_confirmed = result.get("booking_confirmed")

        if (
            expected.get("flow_completed")           # scenario expects a full run
            and not booking_confirmed                 # booking never completed
            and flow_step is not None
            and flow_step <= 2                        # flow barely started
            and n_responses >= 5                      # scenario has many turns (not a short edge case)
            and end_reason == "completed"             # Twilio said "completed" (not a known infra error)
        ):
            return True

        return False

    @staticmethod
    def _classify(turns: int, end_reason: str, fail_checks: list[str]) -> str:
        if turns == 0 or end_reason in ("timeout", "timeout_no_speech", "ngrok_died"):
            return "Infrastructure failure — no conversation happened (server cold or ngrok dead)"
        if "flow_completed" in fail_checks and end_reason == "completed" and turns <= 6:
            return "Early call drop — Twilio/Render dropped the call before flow finished (will retry)"
        if "flow_completed" in fail_checks and end_reason == "completed":
            return "Call ended prematurely — Susie hung up before scenario finished"
        if any("greeting" in c for c in fail_checks):
            return "Greeting phrase mismatch"
        if "reask_fired" in fail_checks:
            return "Silence re-ask did not fire when expected"
        if "booking_confirmed" in fail_checks:
            return "Booking confirmation phrase missing or not recognised"
        if "slot_confirmed" in fail_checks:
            return "Slot not read back to caller before moving on"
        if "new_or_returning_correct" in fail_checks:
            return "New/returning patient classification wrong"
        if "flow_order_correct" in fail_checks:
            return "Susie asked questions out of order"
        if fail_checks:
            return f"Check failed: {fail_checks[0]}"
        return "Unknown"
