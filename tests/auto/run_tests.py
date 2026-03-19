from dotenv import load_dotenv
load_dotenv()

"""
run_tests.py — Single entry point for the Susie automated test suite.

Usage:
    # Run all scenarios
    python tests/auto/run_tests.py

    # Run a specific phase
    python tests/auto/run_tests.py --phase 2

    # Run a specific scenario
    python tests/auto/run_tests.py --scenario 2.1

    # Run Phase 8 (full end-to-end) only
    python tests/auto/run_tests.py --quick
"""

import asyncio
import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

# Allow running as a script from repo root: python tests/auto/run_tests.py
sys.path.insert(0, str(Path(__file__).parents[2]))

from tests.auto.config import (
    RESULTS_DIR,
    SUSIE_NUMBER,
    MIN_PASS_RATE,
    RENDER_SERVER_URL,
    TWILIO_ACCOUNT_SID,
    TWILIO_AUTH_TOKEN,
    TWILIO_TEST_NUMBER,
    ELEVENLABS_API_KEY,
)
from tests.auto.call_runner import CallRunner
from tests.auto.evaluator import Evaluator
from tests.auto.healer import Healer
from tests.auto.report import build_report
from tests.auto.server_manager import SharedServer
from tests.auto.scenarios.all_scenarios import SCENARIOS

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("run_tests")


async def _warmup_server() -> None:
    """
    Ping the Render server health endpoint to wake it from free-tier sleep.

    Render free-tier instances sleep after 15 minutes of inactivity.
    Without warmup every call gets 0 turns (30s Gather timeout fires before
    Susie can speak).  A successful /health response confirms the server is
    ready to handle WebSocket calls.
    """
    import httpx

    url = f"{RENDER_SERVER_URL}/health"
    print(f"\nWarming up server at {url} ...")
    for attempt in range(1, 4):
        try:
            async with httpx.AsyncClient(timeout=90) as client:
                resp = await client.get(url)
            logger.info("Server warmup attempt %d: HTTP %d", attempt, resp.status_code)
            if resp.status_code < 500:
                print(f"Server ready (HTTP {resp.status_code})")
                # Render free-tier: HTTP 200 on /health returns quickly but the
                # WebSocket call-handling pipeline (LLM, TTS, AssemblyAI) can
                # take 10-15 s more to fully initialise.  A short wait here
                # avoids 0-turn failures on the very first scenario.
                await asyncio.sleep(15)
                return
        except Exception as exc:
            logger.warning("Server warmup attempt %d failed: %r", attempt, exc)
            if attempt < 3:
                await asyncio.sleep(10)

    # Server didn't respond cleanly — continue anyway and let Twilio time out
    # naturally; better than aborting the whole run.
    logger.warning("Server warmup did not confirm ready — proceeding anyway")


async def run_single_call(
    scenario: dict,
    evaluator: Evaluator,
    shared_server: SharedServer,
) -> dict:
    """Run one Twilio call for a scenario and return the evaluated result."""
    runner = CallRunner(scenario, shared_server)
    result = await runner.run()
    evaluation = await evaluator.evaluate(result, scenario)
    result["evaluation"] = evaluation
    result["phase"] = scenario["phase"]
    return result


async def run_scenario_with_healing(
    scenario: dict,
    evaluator: Evaluator,
    healer: Healer,
    shared_server: SharedServer,
    max_attempts: int = 3,
) -> dict:
    """
    Run a scenario, print a per-test diagnosis, and retry on failure.
    Retries up to max_attempts times total.
    """
    print(f"\n{'=' * 50}")
    print(f"Running: {scenario['id']} — {scenario['name']}")
    print(f"{'=' * 50}")

    last_result: dict = {}

    for attempt in range(max_attempts):
        if attempt > 0:
            print(f"\n  ↺ Retry attempt {attempt}/{max_attempts - 1} for {scenario['id']}")
            await asyncio.sleep(5)

        try:
            result = await run_single_call(scenario, evaluator, shared_server)
        except Exception as exc:
            logger.error("Exception running %s: %r", scenario["id"], exc, exc_info=True)
            result = {
                "scenario_id":   scenario["id"],
                "scenario_name": scenario["name"],
                "phase":         scenario.get("phase", ""),
                "turns":         0,
                "end_reason":    "exception",
                "susie_said":    [],
                "test_said":     [],
                "duration_seconds": 0,
                "evaluation": {
                    "passed":      False,
                    "fail_reason": "exception",
                    "detail":      str(exc),
                    "checks":      {},
                    "transcript":  "",
                },
            }

        last_result = result

        # ── Per-test diagnosis (always printed) ───────────────────────────
        diagnosis = healer.build_diagnosis(result, scenario)
        print(diagnosis)

        passed = result.get("evaluation", {}).get("passed", False)

        # Save intermediate result
        result_path = (
            RESULTS_DIR
            / f"{scenario['id']}_{datetime.utcnow().strftime('%H%M%S')}.json"
        )
        result_path.write_text(
            json.dumps(result, indent=2, default=str), encoding="utf-8"
        )
        print(f"  Saved: {result_path}")

        if passed:
            break

        if attempt < max_attempts - 1:
            action = await healer.run_fixer_agent(diagnosis, result, scenario)
            if action == "skip":
                print(f"  → Healer: skipping remaining attempts")
                break
            # action == "retry": loop continues
        else:
            print(f"  → Max attempts reached for {scenario['id']}")

    return last_result


async def main():
    parser = argparse.ArgumentParser(
        description="Susie automated test runner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--phase",
        help="Run specific phase e.g. '2'",
        default=None,
    )
    parser.add_argument(
        "--scenario",
        help="Run specific scenario e.g. '2.1'",
        default=None,
    )
    parser.add_argument(
        "--quick",
        help="Run Phase 8 only (full end-to-end)",
        action="store_true",
    )
    args = parser.parse_args()

    # Filter scenarios
    scenarios_to_run = SCENARIOS
    if args.scenario:
        scenarios_to_run = [
            s for s in SCENARIOS if s["id"] == args.scenario
        ]
    elif args.phase:
        scenarios_to_run = [
            s for s in SCENARIOS if s["id"].startswith(args.phase + ".")
        ]
    elif args.quick:
        scenarios_to_run = [
            s for s in SCENARIOS if s["id"].startswith("8.")
        ]

    if not scenarios_to_run:
        print("No matching scenarios found.")
        return

    # Validate required environment variables before making any calls
    missing = []
    if not TWILIO_ACCOUNT_SID:
        missing.append("TWILIO_ACCOUNT_SID")
    if not TWILIO_AUTH_TOKEN:
        missing.append("TWILIO_AUTH_TOKEN")
    if not TWILIO_TEST_NUMBER:
        missing.append("TWILIO_TEST_NUMBER")
    if not ELEVENLABS_API_KEY:
        missing.append("ELEVENLABS_API_KEY")
    if missing:
        print(f"ERROR — missing required environment variables: {', '.join(missing)}")
        sys.exit(1)

    print(f"\n{'=' * 60}")
    print(f"SUSIE AUTOMATED TEST SUITE")
    print(f"Running {len(scenarios_to_run)} scenario(s)")
    print(f"Target: {SUSIE_NUMBER}")
    print(f"{'=' * 60}")

    # Wake the Render server before making any calls — free-tier sleeps when idle
    await _warmup_server()

    # Start ONE shared ngrok + uvicorn server for the entire run.
    # This prevents the ngrok cascade failures that occurred when a new tunnel
    # was created per scenario (one failure → all subsequent fail).
    shared_server = SharedServer()
    print("\nStarting shared webhook server...")
    await shared_server.start()
    print(f"Webhook ready: {shared_server.webhook_url}")

    evaluator = Evaluator()
    healer    = Healer()
    all_results = []

    try:
        # Run scenarios sequentially
        for scenario in scenarios_to_run:
            result = await run_scenario_with_healing(
                scenario, evaluator, healer, shared_server
            )
            all_results.append(result)
            # Brief pause between scenarios to let Twilio finish recording callbacks
            await asyncio.sleep(3)

    finally:
        # Always shut down the shared server cleanly
        print("\nShutting down shared webhook server...")
        await shared_server.stop()

    # Build and print report
    report = build_report(all_results)
    print(f"\n\n{report}")

    # Save report
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    report_path = RESULTS_DIR / f"report_{ts}.txt"
    report_path.write_text(report, encoding="utf-8")
    print(f"\nReport saved: {report_path}")

    # Save all results as JSON
    results_path = RESULTS_DIR / f"results_{ts}.json"
    results_path.write_text(
        json.dumps(all_results, indent=2, default=str), encoding="utf-8"
    )
    print(f"Results saved: {results_path}")

    # Exit code based on pass rate
    total = len(all_results)
    passed = sum(
        1 for r in all_results if r.get("evaluation", {}).get("passed")
    )
    pass_rate = passed / total if total > 0 else 0

    if pass_rate >= MIN_PASS_RATE:
        print("\nCLINIC READY")
        sys.exit(0)
    else:
        print(f"\nNOT READY — {passed}/{total} passed")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
