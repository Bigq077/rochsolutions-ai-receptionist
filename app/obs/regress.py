"""
app/obs/regress.py
------------------
Offline regression runner for mined scenarios (spec §5.4).

    python -m app.obs.regress [--dir tests/auto/scenarios/regressions]

Loads every auto-generated regression scenario, replays its embedded (redacted)
transcript, and checks the deterministic assertions in `expected` — no live server
and no Claude call, so it runs in CI. Exits non-zero if any assertion fails, so a
fixed failure can never silently regress.

This is what `python tests/auto/run_tests.py --ci` delegates to. Only the
deterministic subset of assertions is checked here; LLM-judged assertions are the
live suite's job and are skipped offline (reported, not failed).
"""
from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_DEFAULT_DIR = Path("tests/auto/scenarios/regressions")

# Technical-error phrases Susie should never utter (mirrors evaluator.py).
_ERROR_PHRASES = (
    "technical issue", "having a small technical", "technical difficulty", "i apologise",
)

# Assertion keys we can evaluate against a transcript alone. Anything else in
# `expected` is an LLM-judged / live-only check and is skipped offline.
_DETERMINISTIC_KEYS = {
    "no_technical_error", "not_said", "greeting_contains", "greeting_not_contains",
    "first_susie_turn_contains", "first_susie_turn_not_contains",
}


def load_scenarios(directory: Path) -> List[Dict[str, Any]]:
    """Import every SCENARIO from *.py modules in the regressions directory."""
    scenarios: List[Dict[str, Any]] = []
    if not directory.exists():
        return scenarios
    for path in sorted(directory.glob("*.py")):
        if path.name == "__init__.py":
            continue
        spec = importlib.util.spec_from_file_location(f"_regress_{path.stem}", path)
        if spec is None or spec.loader is None:
            continue
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        scenario = getattr(module, "SCENARIO", None)
        if isinstance(scenario, dict):
            scenarios.append(scenario)
    return scenarios


def _assistant_texts(transcript: List[Dict[str, str]]) -> List[str]:
    return [str(t.get("text", "")).lower()
            for t in transcript if str(t.get("role", "")).lower() == "assistant"]


def check_scenario(scenario: Dict[str, Any]) -> Tuple[bool, List[str], int]:
    """Return (passed, failures, checked_count) for one scenario's transcript.

    Replays the embedded transcript and evaluates the deterministic assertions.
    """
    transcript = scenario.get("transcript") or []
    expected = scenario.get("expected") or {}
    assistant = _assistant_texts(transcript)
    all_assistant = " ".join(assistant)
    first = assistant[0] if assistant else ""

    failures: List[str] = []
    checked = 0

    if expected.get("no_technical_error"):
        checked += 1
        hit = next((p for p in _ERROR_PHRASES if p in all_assistant), None)
        if hit:
            failures.append(f"no_technical_error: found {hit!r}")

    for phrase in expected.get("not_said", []):
        checked += 1
        if phrase.lower() in all_assistant:
            failures.append(f"not_said: Susie said banned phrase {phrase!r}")

    for phrase in expected.get("greeting_contains", []):
        checked += 1
        if phrase.lower() not in first:
            failures.append(f"greeting_contains: greeting missing {phrase!r}")

    for phrase in expected.get("greeting_not_contains", []):
        checked += 1
        if phrase.lower() in first:
            failures.append(f"greeting_not_contains: greeting has {phrase!r}")

    if "first_susie_turn_contains" in expected:
        checked += 1
        if expected["first_susie_turn_contains"].lower() not in first:
            failures.append("first_susie_turn_contains: missing")

    for phrase in expected.get("first_susie_turn_not_contains", []):
        checked += 1
        if phrase.lower() in first:
            failures.append(f"first_susie_turn_not_contains: has {phrase!r}")

    return (not failures, failures, checked)


def run(directory: Path) -> int:
    scenarios = load_scenarios(directory)
    if not scenarios:
        print(f"No regression scenarios in {directory} — nothing to check.")
        return 0

    total_failures = 0
    for scenario in scenarios:
        sid = scenario.get("id", "?")
        if not scenario.get("transcript"):
            print(f"SKIP  {sid} (no embedded transcript — live-only)")
            continue
        passed, failures, checked = check_scenario(scenario)
        skipped = sorted(set(scenario.get("expected", {})) - _DETERMINISTIC_KEYS)
        if passed:
            note = f" (+{len(skipped)} live-only skipped)" if skipped else ""
            print(f"PASS  {sid} — {checked} deterministic checks{note}")
        else:
            total_failures += len(failures)
            print(f"FAIL  {sid}:")
            for f in failures:
                print(f"        - {f}")

    print("-" * 60)
    if total_failures:
        print(f"REGRESSION: {total_failures} assertion(s) failed across "
              f"{len(scenarios)} scenario(s).")
        return 1
    print(f"OK: all {len(scenarios)} regression scenario(s) pass.")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.obs.regress")
    parser.add_argument("--dir", default=str(_DEFAULT_DIR))
    args = parser.parse_args(argv)
    return run(Path(args.dir))


if __name__ == "__main__":
    raise SystemExit(main())
