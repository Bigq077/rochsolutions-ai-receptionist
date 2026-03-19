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
from tests.auto.report import build_report
from tests.auto.transcript import build_transcript
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


async def run_single_scenario(
    scenario: dict,
    evaluator: Evaluator,
) -> dict:
    print(f"\n{'=' * 50}")
    print(f"Running: {scenario['id']} — {scenario['name']}")
    print(f"{'=' * 50}")

    # Run the call
    runner = CallRunner(scenario)
    result = await runner.run()

    # Evaluate
    evaluation = await evaluator.evaluate(result, scenario)
    result["evaluation"] = evaluation
    result["phase"] = scenario["phase"]

    # Print immediate result
    status = "PASS" if evaluation["passed"] else "FAIL"
    print(f"\n{status}")
    if not evaluation["passed"]:
        print(f"REASON: {evaluation.get('fail_reason')}")
        print(f"DETAIL: {evaluation.get('detail')}")

    # Save result to file
    result_path = (
        RESULTS_DIR
        / f"{scenario['id']}_{datetime.utcnow().strftime('%H%M%S')}.json"
    )
    result_path.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    print(f"Saved: {result_path}")

    return result


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

    evaluator = Evaluator()
    all_results = []

    # Run scenarios sequentially — one call at a time
    for scenario in scenarios_to_run:
        try:
            result = await run_single_scenario(scenario, evaluator)
            all_results.append(result)
            # Small gap between calls
            await asyncio.sleep(3)
        except Exception as e:
            logger.error(f"ERROR in {scenario['id']}: {e}", exc_info=True)
            all_results.append(
                {
                    "scenario_id": scenario["id"],
                    "scenario_name": scenario["name"],
                    "phase": scenario["phase"],
                    "evaluation": {
                        "passed": False,
                        "fail_reason": "exception",
                        "detail": str(e),
                        "checks": {},
                    },
                }
            )

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
    results_path.write_text(json.dumps(all_results, indent=2, default=str), encoding="utf-8")
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
