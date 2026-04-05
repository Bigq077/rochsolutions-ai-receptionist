import sys
# Force UTF-8 output on Windows (avoids cp1252 UnicodeEncodeError for ✓ → ═ etc.)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

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
    USE_DIRECT_WS,
)
from tests.auto.call_runner import CallRunner
from tests.auto.evaluator import Evaluator
from tests.auto.fixer import FixerAgent
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


async def _check_network() -> bool:
    """Return True if basic DNS/HTTP is working."""
    import httpx
    try:
        async with httpx.AsyncClient(timeout=10) as http:
            await http.get("https://api.twilio.com")
        return True
    except Exception:
        return False


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


async def _preflight_check() -> bool:
    """
    Validate all external dependencies before making any real Twilio calls.

    Checks (in order):
      1. Twilio credentials — list 1 call to verify SID + token
      2. ElevenLabs API key — small TTS request (5 chars) to confirm key is valid
      3. Anthropic API key — small completion to confirm key is valid
      4. Render server — /health reachable
      5. ngrok — start SharedServer briefly to confirm tunnel works
      6. Scenario integrity — all 35 scenarios have required fields

    Returns True if all checks pass, False otherwise.
    Prints a clear PASS/FAIL summary for each check.
    """
    import httpx
    import time

    TICK = "[PASS]"
    CROSS = "[FAIL]"
    all_ok = True

    print("\n" + "=" * 60)
    print("PREFLIGHT CHECK — validating all dependencies")
    print("=" * 60)

    # ── 1. Twilio credentials ─────────────────────────────────────
    print("\n[1/6] Twilio credentials...")
    if USE_DIRECT_WS:
        print(f"  [SKIP] Twilio: not needed in direct WS mode (use --real-calls to test)")
    else:
        try:
            from twilio.rest import Client as TwilioClient
            client = TwilioClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
            # Listing calls is a lightweight read-only operation
            _ = client.calls.list(limit=1)
            print(f"  {TICK} Twilio: credentials valid (SID={TWILIO_ACCOUNT_SID[:8]}...)")
        except Exception as exc:
            print(f"  {CROSS} Twilio: {exc}")
            all_ok = False

    # ── 2. ElevenLabs API key ─────────────────────────────────────
    print("\n[2/6] ElevenLabs API key...")
    from tests.auto.config import ELEVENLABS_PATIENT_VOICE_ID
    try:
        async with httpx.AsyncClient(timeout=15) as http:
            resp = await http.post(
                f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_PATIENT_VOICE_ID}",
                headers={"xi-api-key": ELEVENLABS_API_KEY},
                json={
                    "text": "Hi.",
                    "model_id": "eleven_flash_v2_5",
                    "voice_settings": {"stability": 0.5, "similarity_boost": 0.8},
                },
            )
        if resp.status_code == 200:
            print(f"  {TICK} ElevenLabs: key valid, TTS working")
        elif resp.status_code == 401:
            print(f"  [WARN] ElevenLabs: 401 Unauthorized — API key is invalid or expired")
            print(f"         Key used: {ELEVENLABS_API_KEY[:12]}...")
            print(f"         Tests will still run using Twilio <Say> TTS fallback (lower audio quality).")
            # Not fatal — <Say> fallback handles this; do NOT set all_ok = False
        else:
            print(f"  [WARN] ElevenLabs: HTTP {resp.status_code} — {resp.text[:200]}")
    except Exception as exc:
        print(f"  [WARN] ElevenLabs: {exc}")

    # ── 3. Anthropic API key ──────────────────────────────────────
    print("\n[3/6] Anthropic API key (evaluator)...")
    from tests.auto.config import ANTHROPIC_API_KEY
    try:
        async with httpx.AsyncClient(timeout=20) as http:
            resp = await http.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": ANTHROPIC_API_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": "claude-haiku-4-5-20251001",
                    "max_tokens": 5,
                    "messages": [{"role": "user", "content": "ping"}],
                },
            )
        if resp.status_code in (200, 400):
            # 400 = malformed request but key was accepted — key is valid
            print(f"  {TICK} Anthropic: key valid (HTTP {resp.status_code})")
        elif resp.status_code == 401:
            print(f"  {CROSS} Anthropic: 401 Unauthorized — API key invalid")
            all_ok = False
        else:
            print(f"  [WARN] Anthropic: HTTP {resp.status_code}")
    except Exception as exc:
        print(f"  {CROSS} Anthropic: {exc}")
        all_ok = False

    # ── 4. Render server /health ──────────────────────────────────
    print(f"\n[4/6] Render server ({RENDER_SERVER_URL}/health)...")
    try:
        t0 = time.monotonic()
        async with httpx.AsyncClient(timeout=30) as http:
            resp = await http.get(f"{RENDER_SERVER_URL}/health")
        elapsed = time.monotonic() - t0
        if resp.status_code < 500:
            print(f"  {TICK} Render server: HTTP {resp.status_code} in {elapsed:.1f}s")
        else:
            print(f"  {CROSS} Render server: HTTP {resp.status_code} in {elapsed:.1f}s")
            all_ok = False
    except Exception as exc:
        print(f"  {CROSS} Render server: {exc}")
        all_ok = False

    # ── 5. ngrok tunnel ───────────────────────────────────────────
    print("\n[5/6] ngrok tunnel (SharedServer)...")
    if USE_DIRECT_WS:
        print(f"  [SKIP] ngrok: not needed in direct WS mode (use --real-calls to test)")
    else:
        try:
            server = SharedServer()
            await server.start()
            print(f"  {TICK} ngrok: tunnel alive at {server.webhook_url}")
            await server.stop()
        except Exception as exc:
            print(f"  {CROSS} ngrok: {exc}")
            all_ok = False

    # ── 6. Scenario integrity ─────────────────────────────────────
    print(f"\n[6/6] Scenario integrity ({len(SCENARIOS)} scenarios)...")
    scenario_errors = []
    for s in SCENARIOS:
        sid = s.get("id", "?")
        if not s.get("name"):
            scenario_errors.append(f"  {sid}: missing 'name'")
        if not s.get("phase"):
            scenario_errors.append(f"  {sid}: missing 'phase'")
        if "expected" not in s:
            scenario_errors.append(f"  {sid}: missing 'expected'")
    if scenario_errors:
        print(f"  {CROSS} Scenario errors:")
        for e in scenario_errors:
            print(e)
        all_ok = False
    else:
        print(f"  {TICK} All {len(SCENARIOS)} scenarios have required fields")

    # ── Summary ───────────────────────────────────────────────────
    print("\n" + "=" * 60)
    if all_ok:
        print("PREFLIGHT: ALL CHECKS PASSED — safe to run full test suite")
    else:
        print("PREFLIGHT: SOME CHECKS FAILED — fix the issues above before running")
    print("=" * 60 + "\n")

    return all_ok


def _print_failure_summary(result: dict, scenario: dict) -> None:
    """Print a concise per-turn trace summary when a scenario fails."""
    traces = result.get("turn_traces", [])
    if not traces:
        return
    print(f"\n  [TURN TRACE SUMMARY — {scenario['id']} {scenario['name']}]")
    # First checkpoint failure
    for tr in traces:
        for err in tr.get("checkpoint_errors", []):
            print(f"    FIRST CHECKPOINT FAIL: turn {tr['turn_index']}: {err}")
            break
        else:
            continue
        break
    # First no-output turn
    for tr in traces:
        if tr.get("error_if_any") == "no_assistant_output_after_turn":
            print(
                f"    NO OUTPUT: turn {tr['turn_index']}"
                f" text={tr['user_text_raw'][:40]!r}"
                f" state_before={tr['state_before']}"
                f" handled_by={tr['handled_by']}"
            )
            break
    # Last turn summary
    last = traces[-1]
    print(
        f"    LAST TURN ({last['turn_index']}): "
        f"text={last['user_text_raw'][:40]!r}"
    )
    print(
        f"      state:    {last['state_before']} → {last['state_after']}"
    )
    print(f"      handled_by:        {last['handled_by']}")
    print(f"      asst_emitted:      {last['assistant_response_emitted']}")
    if last.get("assistant_response_text"):
        print(f"      asst_text:         {last['assistant_response_text'][:80]!r}")
    print(f"      booking_confirmed: {last['booking_confirmed_after']}")
    print(f"      error:             {last['error_if_any']}")


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
    fixer: FixerAgent,
    shared_server: SharedServer,
) -> dict:
    """
    Run a scenario and retry ONCE only for infrastructure failures.

    Content failures (Susie spoke but gave the wrong answer) are never
    retried — the same call will produce the same result and just burns
    Twilio credits.  Only infrastructure failures (ngrok dead, 0 turns,
    timeout, exception) get a single retry after a 30 s cool-down.
    """
    print(f"\n{'=' * 50}")
    print(f"Running: {scenario['id']} — {scenario['name']}")
    print(f"{'=' * 50}")

    def _make_exception_result(exc: Exception) -> dict:
        return {
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

    # ── First attempt ────────────────────────────────────────────────────
    try:
        result = await run_single_call(scenario, evaluator, shared_server)
    except Exception as exc:
        logger.error("Exception running %s: %r", scenario["id"], exc, exc_info=True)
        result = _make_exception_result(exc)

    diagnosis = healer.build_diagnosis(result, scenario)
    print(diagnosis)

    _save_result(result, scenario)

    if result.get("evaluation", {}).get("passed"):
        return result

    if not result.get("evaluation", {}).get("passed"):
        _print_failure_summary(result, scenario)

    # ── Decide whether to retry ──────────────────────────────────────────
    if not healer.should_retry(result):
        # Content failure — run fixer diagnosis then bail out
        fix_result = fixer.analyze(result, scenario)
        print(fix_result.format())
        return result

    # ── Single infrastructure retry ──────────────────────────────────────
    # Check network before retrying — if it's down, wait up to 3×60s
    for _net_attempt in range(3):
        if await _check_network():
            break
        logger.warning("Network appears down — waiting 60s before retry attempt %d/3", _net_attempt + 1)
        await asyncio.sleep(60)
    else:
        logger.error("Network still down after 3 checks — skipping retry for %s", scenario["id"])
        return result

    print(f"\n  ↺ Retrying {scenario['id']} after 30 s cool-down...")
    await asyncio.sleep(30)

    try:
        result = await run_single_call(scenario, evaluator, shared_server)
    except Exception as exc:
        logger.error("Exception on retry %s: %r", scenario["id"], exc, exc_info=True)
        result = _make_exception_result(exc)

    diagnosis = healer.build_diagnosis(result, scenario)
    print(diagnosis)
    _save_result(result, scenario)

    if not result.get("evaluation", {}).get("passed"):
        _print_failure_summary(result, scenario)

    return result


def _save_result(result: dict, scenario: dict) -> None:
    result_path = (
        RESULTS_DIR
        / f"{scenario['id']}_{datetime.utcnow().strftime('%H%M%S')}.json"
    )
    result_path.write_text(
        json.dumps(result, indent=2, default=str), encoding="utf-8"
    )
    print(f"  Saved: {result_path}")


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
        help="Run specific scenario(s) e.g. '2.1' or multiple: '2.1 3.1 4.2'",
        default=None,
        nargs="+",
    )
    parser.add_argument(
        "--from-phase",
        help="Run all scenarios from this phase number onwards e.g. '2'",
        default=None,
        dest="from_phase",
    )
    parser.add_argument(
        "--from-scenario",
        help="Run all scenarios from this scenario ID onwards e.g. '4.1' or '11.4'",
        default=None,
        dest="from_scenario",
    )
    parser.add_argument(
        "--quick",
        help="Run Phase 8 only (full end-to-end)",
        action="store_true",
    )
    parser.add_argument(
        "--preflight",
        help="Validate all API keys and infrastructure without making real calls",
        action="store_true",
    )
    parser.add_argument(
        "--real-calls",
        help="Use real Twilio calls instead of direct WebSocket (tests the full phone path). "
             "Run with --quick --real-calls before handing a number to a client.",
        action="store_true",
        dest="real_calls",
    )
    args = parser.parse_args()

    if args.real_calls:
        # Override the module-level flag so CallRunner uses the real Twilio flow
        import tests.auto.call_runner as _cr
        _cr.USE_DIRECT_WS = False

    if args.preflight:
        ok = await _preflight_check()
        sys.exit(0 if ok else 1)

    # Filter scenarios
    scenarios_to_run = SCENARIOS
    if args.scenario:
        ids = set(args.scenario)
        scenarios_to_run = [
            s for s in SCENARIOS if s["id"] in ids
        ]
    elif args.phase:
        scenarios_to_run = [
            s for s in SCENARIOS if s["id"].startswith(args.phase + ".")
        ]
    elif args.from_phase:
        try:
            from_num = int(args.from_phase)
        except ValueError:
            print(f"ERROR — --from-phase must be an integer, got '{args.from_phase}'")
            sys.exit(1)
        scenarios_to_run = [
            s for s in SCENARIOS
            if int(s["id"].split(".")[0]) >= from_num
        ]
    elif args.from_scenario:
        ids = [s["id"] for s in SCENARIOS]
        if args.from_scenario not in ids:
            print(f"ERROR — scenario '{args.from_scenario}' not found. Available: {', '.join(ids)}")
            sys.exit(1)
        start_idx = ids.index(args.from_scenario)
        scenarios_to_run = SCENARIOS[start_idx:]
    elif args.quick:
        scenarios_to_run = [
            s for s in SCENARIOS if s["id"].startswith("8.")
        ]

    if not scenarios_to_run:
        print("No matching scenarios found.")
        return

    # Validate required environment variables before making any calls
    # Twilio credentials are only needed for real-call mode
    if args.real_calls:
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
    # Not needed in direct WS mode — skipped to avoid ngrok cost/complexity.
    if args.real_calls:
        shared_server = SharedServer()
        print("\nStarting shared webhook server...")
        await shared_server.start()
        print(f"Webhook ready: {shared_server.webhook_url}")
    else:
        shared_server = None
        print("\nDirect WS mode — no Twilio calls, no ngrok needed")

    evaluator = Evaluator()
    healer    = Healer()
    fixer     = FixerAgent()
    all_results = []

    try:
        # Run scenarios sequentially
        for scenario in scenarios_to_run:
            try:
                result = await run_scenario_with_healing(
                    scenario, evaluator, healer, fixer, shared_server
                )
            except asyncio.CancelledError as exc:
                _end_reason = "direct_ws_interrupted" if USE_DIRECT_WS else "ngrok_crash"
                logger.error(
                    "CancelledError running scenario %s — %s: %r",
                    scenario["id"],
                    "direct WS session interrupted" if USE_DIRECT_WS else "likely ngrok crash",
                    exc,
                )
                result = {
                    "scenario_id":   scenario["id"],
                    "scenario_name": scenario["name"],
                    "phase":         scenario.get("phase", ""),
                    "turns":         0,
                    "end_reason":    _end_reason,
                    "susie_said":    [],
                    "test_said":     [],
                    "duration_seconds": 0,
                    "timestamp":     datetime.utcnow().isoformat(),
                    "call_sid":      None,
                    "evaluation": {
                        "passed":      False,
                        "fail_reason": _end_reason,
                        "detail":      str(exc),
                        "checks":      {},
                        "transcript":  "",
                    },
                }
                _save_result(result, scenario)
                # Only restart the shared (ngrok) server when not in direct WS mode.
                # In direct WS mode shared_server is None — nothing to restart.
                if shared_server is not None and not USE_DIRECT_WS:
                    logger.info("Restarting shared server after ngrok crash...")
                    try:
                        await shared_server.stop()
                    except Exception:
                        pass
                    try:
                        await shared_server.start()
                        logger.info("Shared server restarted — new webhook: %s", shared_server.webhook_url)
                    except Exception as restart_exc:
                        logger.error("Failed to restart shared server: %r — remaining tests may fail", restart_exc)
            except Exception as exc:
                logger.error(
                    "Exception running scenario %s: %r",
                    scenario["id"], exc, exc_info=True,
                )
                result = {
                    "scenario_id":   scenario["id"],
                    "scenario_name": scenario["name"],
                    "phase":         scenario.get("phase", ""),
                    "turns":         0,
                    "end_reason":    "infrastructure_failure",
                    "susie_said":    [],
                    "test_said":     [],
                    "duration_seconds": 0,
                    "timestamp":     datetime.utcnow().isoformat(),
                    "call_sid":      None,
                    "evaluation": {
                        "passed":      False,
                        "fail_reason": "infrastructure_failure",
                        "detail":      str(exc),
                        "checks":      {},
                        "transcript":  "",
                    },
                }
                _save_result(result, scenario)
            all_results.append(result)
            # Brief pause between scenarios to let Twilio finish recording callbacks
            await asyncio.sleep(3)

    finally:
        if shared_server is not None:
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
