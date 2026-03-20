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
    Ping the Render server until the full LLM/TTS pipeline is hot.

    Strategy:
      1. Keep pinging /health until we get HTTP 200 (server awake).
      2. Keep pinging every 10 s, measuring response time.
         Render's HTTP server responds immediately after wake-up, but the
         LLM connection pool and TTS worker take 2-4 minutes more.  Once
         the server is truly hot, /health responds in < 1 s.  We wait for
         3 consecutive sub-second pings (or 3 minutes total) before
         declaring the pipeline ready.
      3. Always wait at least 60 s so the first Twilio call has headroom.
    """
    import time
    import httpx

    _MAX_WARMUP_S   = 180   # hard ceiling: 3 minutes
    _MIN_WARMUP_S   = 60    # always wait at least 60 s
    _FAST_THRESHOLD = 1.0   # response time below this ⇒ server is hot
    _FAST_NEEDED    = 3     # consecutive fast pings required
    _POLL_INTERVAL  = 10    # seconds between polls

    url = f"{RENDER_SERVER_URL}/health"
    print(f"\nWarming up Render server at {url} ...")

    # ── Step 1: block until server answers ───────────────────────────────
    woke_at = None
    for attempt in range(1, 7):
        try:
            t0 = time.monotonic()
            async with httpx.AsyncClient(timeout=90) as client:
                resp = await client.get(url)
            elapsed = time.monotonic() - t0
            logger.info("Warmup ping %d: HTTP %d in %.1fs", attempt, resp.status_code, elapsed)
            if resp.status_code < 500:
                print(f"  Server awake (HTTP {resp.status_code}, {elapsed:.1f}s)")
                woke_at = time.monotonic()
                break
        except Exception as exc:
            logger.warning("Warmup ping %d failed: %r", attempt, exc)
            await asyncio.sleep(10)

    if woke_at is None:
        logger.warning("Server never responded — proceeding anyway")
        return

    # ── Step 2: poll until response time is consistently fast ────────────
    print(f"  Polling until pipeline hot (max {_MAX_WARMUP_S}s, min {_MIN_WARMUP_S}s)...")
    fast_streak = 0

    while True:
        await asyncio.sleep(_POLL_INTERVAL)
        total_elapsed = time.monotonic() - woke_at

        if total_elapsed >= _MAX_WARMUP_S:
            print(f"  Warmup ceiling reached ({_MAX_WARMUP_S}s) — proceeding")
            break

        try:
            t0 = time.monotonic()
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(url)
            ping_s = time.monotonic() - t0
            total_elapsed = time.monotonic() - woke_at
            print(
                f"  [{total_elapsed:>5.0f}s] /health → {resp.status_code}"
                f" in {ping_s:.2f}s"
            )
            logger.info(
                "Warmup poll: HTTP %d in %.2fs (total %.0fs)",
                resp.status_code, ping_s, total_elapsed,
            )
            if ping_s < _FAST_THRESHOLD and total_elapsed >= _MIN_WARMUP_S:
                fast_streak += 1
                if fast_streak >= _FAST_NEEDED:
                    print(
                        f"  Pipeline hot ({fast_streak} consecutive pings"
                        f" < {_FAST_THRESHOLD}s) — ready!"
                    )
                    break
            else:
                fast_streak = 0
        except Exception as exc:
            logger.warning("Warmup poll failed: %r", exc)
            fast_streak = 0

    print("  Server ready.")


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
            # Infrastructure failures need longer wait — Render cold-start takes 30-45s.
            # Content failures (speech happened but wrong) only need a short pause.
            prev_turns      = last_result.get("turns", 0)
            prev_end_reason = last_result.get("end_reason", "unknown")
            is_infra = (
                prev_turns == 0
                or prev_end_reason in ("timeout", "timeout_no_speech", "ngrok_died", "exception")
            )
            retry_wait = 30 if is_infra else 5
            print(f"  Waiting {retry_wait}s before retry ({'infra' if is_infra else 'content'} failure)...")
            await asyncio.sleep(retry_wait)

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
                "timestamp":     datetime.utcnow().isoformat(),
                "call_sid":      None,
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
