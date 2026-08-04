"""
app/obs/show.py
---------------
Retrieve and print a single stored call from the `calls` table (spec §5.1/§5.5).

    # List the most recent calls so you can find the one you want:
    python -m app.obs.show --recent 20 [--clinic vital_edge]

    # Print one call's metadata + full transcript by call_sid:
    python -m app.obs.show CA1511c6114506495f2705a9f691883685 [--redact]

Read-only over the store; a companion to app/obs/dashboard.py (which is aggregate
-only). Deliberately a CLI — it adds nothing to the live FastAPI app. Internal-only.

Transcripts are special-category health data (spec §7): pass --redact to strike
phone/email and any known caller name before printing (reuses app/obs/redact.py).
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from app import config
from app.obs import store


def _fmt(v: Any) -> str:
    return "—" if v in (None, "") else str(v)


def _known_names(call: Dict[str, Any]) -> List[str]:
    """Caller names we can safely strike in --redact mode (see redact.py scope)."""
    collected = call.get("collected") or {}
    names = [collected.get("name"), call.get("caller_name")]
    return [n for n in names if n]


def render_recent(calls: List[Dict[str, Any]]) -> str:
    if not calls:
        return "(no calls in range)"
    header = (
        f"{'start (UTC)':<20} {'call_sid':<36} {'from':<15} "
        f"{'outcome':<10} {'score':>5} {'turns':>5}  failure tags"
    )
    lines = [header, "-" * max(len(header), 80)]
    # Newest first — most recent call is usually the one you're looking for.
    for c in reversed(calls):
        start = (c.get("start_utc") or "")[:19].replace("T", " ")
        tags = ", ".join(c.get("failure_tags") or [])
        score = c.get("quality_score")
        lines.append(
            f"{start:<20} {_fmt(c.get('call_sid')):<36} "
            f"{_fmt(c.get('caller_number')):<15} "
            f"{_fmt(c.get('outcome') or c.get('reason')):<10} "
            f"{(str(score) if score is not None else '—'):>5} "
            f"{_fmt(c.get('turn_count')):>5}  {tags}"
        )
    return "\n".join(lines)


def render_call(call: Dict[str, Any], *, redact: bool) -> str:
    transcript = call.get("transcript") or []
    if redact:
        from app.obs.redact import redact_transcript
        transcript = redact_transcript(transcript, _known_names(call))

    meta_keys = [
        "call_sid", "clinic_id", "start_utc", "end_utc", "duration_s",
        "caller_number", "dialled_number", "reason", "outcome", "final_state",
        "booking_confirmed", "acuity_booking_id", "transfer_attempted",
        "graceful_exit", "turn_count", "total_retries", "tone",
        "quality_score", "intent_resolved", "action_needed", "failure_tags",
        "rubric_version", "judged_at",
    ]
    out = ["=" * 72, f"Call {call.get('call_sid')}", "=" * 72]
    for k in meta_keys:
        v = call.get(k)
        if isinstance(v, (list, dict)):
            v = json.dumps(v, ensure_ascii=False)
        out.append(f"  {k:<20} {_fmt(v)}")

    evidence = call.get("evidence")
    if evidence:
        out += ["", "Judge evidence:", f"  {evidence}"]

    out += ["", "-" * 72, "Transcript" + (" (redacted)" if redact else ""), "-" * 72]
    if not transcript:
        out.append("  (no transcript stored)")
    else:
        for turn in transcript:
            role = (turn.get("role") or "?").upper()
            text = turn.get("text") or ""
            out.append(f"  {role:<10} {text}")
    return "\n".join(out)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.obs.show")
    parser.add_argument("call_sid", nargs="?", default=None,
                        help="call_sid to print in full (omit with --recent to list)")
    parser.add_argument("--recent", type=int, metavar="N",
                        help="list the N most recent calls instead of printing one")
    parser.add_argument("--clinic", default=None, help="filter --recent to one clinic_id")
    parser.add_argument("--days", type=int, default=7,
                        help="--recent lookback window in days (default 7)")
    parser.add_argument("--redact", action="store_true",
                        help="strike phone/email/known names before printing (health data)")
    parser.add_argument("--json", action="store_true", help="emit raw JSON instead of text")
    args = parser.parse_args(argv)

    if not config.DATABASE_URL:
        print("ERROR: OBS_DATABASE_URL (or DATABASE_URL) not set — no store to read.",
              file=sys.stderr)
        return 2
    if not config.OBS_CAPTURE_ENABLED:
        print("NOTE: OBS_CAPTURE_ENABLED is not set — calls are only stored while it is on.",
              file=sys.stderr)

    if args.call_sid:
        call = store.get_call(args.call_sid)
        if call is None:
            print(f"No stored call {args.call_sid} "
                  f"(not captured, or in a different OBS_DATABASE_URL).", file=sys.stderr)
            return 1
        print(json.dumps(call, ensure_ascii=False, indent=2) if args.json
              else render_call(call, redact=args.redact))
        return 0

    # No call_sid → list recent calls so the user can find the one they want.
    n = args.recent or 20
    since = datetime.now(timezone.utc) - timedelta(days=args.days)
    calls = store.list_calls(since=since, clinic_id=args.clinic)[-n:]
    if args.json:
        print(json.dumps(calls, ensure_ascii=False, indent=2))
    else:
        print(f"Most recent {min(n, len(calls))} call(s), last {args.days} day(s)"
              + (f" (clinic={args.clinic})" if args.clinic else "") + ":\n")
        print(render_recent(calls))
        print("\nRun  python -m app.obs.show <call_sid>  to see a full transcript.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
