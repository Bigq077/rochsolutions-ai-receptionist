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

    async def run_fixer_agent(
        self,
        diagnosis: str,
        result: dict,
        scenario: dict,
    ) -> str:
        """
        Decide whether to retry or skip this scenario.

        Returns:
            "retry" — run the scenario again
            "skip"  — give up on this scenario
        """
        turns      = result.get("turns", 0)
        end_reason = result.get("end_reason", "unknown")

        # Infrastructure failure: server cold, ngrok dead, or no speech
        if self._is_infrastructure_failure(turns, end_reason):
            self._infra_fail_streak += 1
            print(
                f"  Healer: Infrastructure failure "
                f"(streak={self._infra_fail_streak}) — retrying"
            )
            return "retry"

        # Real conversation happened but something went wrong with Susie's responses
        self._infra_fail_streak = 0
        checks      = result.get("evaluation", {}).get("checks", {})
        fail_checks = [k for k, v in checks.items() if v is False]
        print(
            f"  Healer: Conversation failure ({', '.join(fail_checks[:3])}) — retrying"
        )
        return "retry"

    # ── helpers ──────────────────────────────────────────────────────────────

    @staticmethod
    def _is_infrastructure_failure(turns: int, end_reason: str) -> bool:
        return (
            turns == 0
            or end_reason in ("timeout", "timeout_no_speech", "ngrok_died", "exception")
        )

    @staticmethod
    def _classify(turns: int, end_reason: str, fail_checks: list[str]) -> str:
        if turns == 0 or end_reason in ("timeout", "timeout_no_speech", "ngrok_died"):
            return "Infrastructure failure — no conversation happened (server cold or ngrok dead)"
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
