"""
app/obs/to_scenario.py
----------------------
Turn a judged-bad real call into a committed, PII-free regression scenario (§5.4).

    python -m app.obs.to_scenario <call_sid> [--out tests/auto/scenarios/regressions] [--force]

Loads a stored, judged call (Phase 1 + 3), redacts all PII from its transcript
(app/obs/redact.py — with a HARD assertion that none survives), and writes a
scenario module in the existing tests/auto format. The emitted module carries:
- responses:  the caller's turns (redacted) — the inputs to re-drive the fix,
- transcript: the redacted transcript — replayed offline by app/obs/regress.py,
- expected:   a conservative deterministic baseline (no_technical_error),
- source:     {call_sid, quality_score, failure_tags, rubric_version} for context.

The human refines `expected` when they write the fix — this tool captures the
input + the recorded failure PII-free; it does not invent the correct behaviour.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from app import config
from app.obs import redact, store

_DEFAULT_OUT = Path("tests/auto/scenarios/regressions")


def _names_from_call(call: Dict[str, Any]) -> List[str]:
    names: List[str] = []
    collected = call.get("collected") or {}
    for key in ("name", "full_name", "first_name", "last_name"):
        v = collected.get(key)
        if v:
            names.append(str(v))
    raw = call.get("raw") or {}
    rc = raw.get("collected") or {}
    for key in ("name", "full_name"):
        if rc.get(key):
            names.append(str(rc[key]))
    return names


def _caller_responses(transcript: List[Dict[str, str]]) -> List[str]:
    """Redacted caller turns (role user/caller), in order — the scenario inputs."""
    return [t["text"] for t in transcript
            if str(t.get("role", "")).lower() in ("user", "caller")]


def _slug(call_sid: str) -> str:
    tail = re.sub(r"[^A-Za-z0-9]", "", call_sid or "")[-8:] or "unknown"
    return f"regression_{tail}"


def build_scenario(call: Dict[str, Any]) -> Dict[str, Any]:
    """Build a redacted, PII-free scenario dict from a stored call. Raises on leak."""
    names = _names_from_call(call)
    transcript = redact.redact_transcript(call.get("transcript") or [], names)
    redact.assert_transcript_clean(transcript)  # HARD: no phone/email survives

    responses = _caller_responses(transcript)
    for r in responses:
        redact.assert_no_pii(r, where="response")

    scenario = {
        "id": _slug(call.get("call_sid")),
        "phase": "Regression — mined from real calls",
        "name": f"Regression from {_slug(call.get('call_sid'))} "
                f"(score {call.get('quality_score')}, tags {call.get('failure_tags') or []})",
        "responses": responses,
        "expected": {
            # Conservative deterministic baseline. Extend by hand when you fix the
            # underlying failure (e.g. add `not_said` / `*_contains` assertions).
            "no_technical_error": True,
        },
        "transcript": transcript,   # embedded — replayed offline by app/obs/regress.py
        "source": {
            "call_sid_slug": _slug(call.get("call_sid")),  # slug only, not the raw SID
            "quality_score": call.get("quality_score"),
            "failure_tags": call.get("failure_tags") or [],
            "rubric_version": call.get("rubric_version"),
        },
    }
    return scenario


def _assert_scenario_clean(scenario: Dict[str, Any]) -> None:
    """Hard-assert the caller-derived fields carry no PII.

    Only transcript + responses are scanned — those hold real caller speech, which
    is where PII originates. The id / source / name fields are synthesised from a
    Twilio call-SID slug and the judge's tags (not PII, and SID digits would false-
    positive against the phone pattern), so they are intentionally not scanned.
    """
    redact.assert_transcript_clean(scenario.get("transcript") or [])
    for r in scenario.get("responses") or []:
        redact.assert_no_pii(r, where="response")


def render_module(scenario: Dict[str, Any]) -> str:
    """Serialise a scenario dict to a Python module source string (PII-checked)."""
    import pprint
    _assert_scenario_clean(scenario)  # belt-and-braces before we ever write it out
    body = pprint.pformat(scenario, width=100, sort_dicts=False)
    return (
        '"""Auto-generated regression scenario (app/obs/to_scenario.py).\n'
        "PII-redacted from a real judged-bad call. Do not add real names/numbers.\n"
        "Refine `expected` when you fix the underlying failure.\n"
        '"""\n\n'
        f"SCENARIO = {body}\n"
    )


def write_scenario(scenario: Dict[str, Any], out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "__init__.py").touch(exist_ok=True)
    path = out_dir / f"{scenario['id']}.py"
    path.write_text(render_module(scenario), encoding="utf-8")
    return path


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.obs.to_scenario")
    parser.add_argument("call_sid")
    parser.add_argument("--out", default=str(_DEFAULT_OUT))
    parser.add_argument("--force", action="store_true",
                        help="emit even if the call was not judged bad")
    args = parser.parse_args(argv)

    if not config.DATABASE_URL:
        print("ERROR: DATABASE_URL not set — no store to read from.", file=sys.stderr)
        return 2

    call = store.get_call(args.call_sid)
    if call is None:
        print(f"ERROR: no stored call {args.call_sid}", file=sys.stderr)
        return 1

    score = call.get("quality_score")
    tags = call.get("failure_tags") or []
    is_bad = (isinstance(score, int) and score <= 2) or bool(tags)
    if not is_bad and not args.force:
        print(f"Call {args.call_sid} is not judged bad (score={score}, tags={tags}). "
              f"Use --force to make a scenario anyway.", file=sys.stderr)
        return 1

    try:
        scenario = build_scenario(call)
        path = write_scenario(scenario, Path(args.out))
    except redact.PIILeakError as exc:
        print(f"ABORTED: {exc}", file=sys.stderr)
        return 3

    print(f"OK: wrote {path} ({len(scenario['responses'])} caller turns, PII-free).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
