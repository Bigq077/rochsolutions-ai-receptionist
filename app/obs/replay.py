"""
app/obs/replay.py
-----------------
Offline replay harness (spec §5.1) — the tool every later phase builds on.

    python -m app.obs.replay <call_sid>
    python -m app.obs.replay <call_sid> --json   # machine-readable dump

Loads a stored call from the durable store and reconstructs its turn-by-turn
trace offline and deterministically, printing the metadata header followed by
the ordered transcript. Phases 3 (LLM-as-judge) and 4 (failure→regression)
import `load_call()` / `format_trace()` to consume the exact same reconstruction.

Scope note: "replay" here means faithful reconstruction of the recorded
conversation and outcome from the durable transcript — the artefact the judge
and regression pipeline actually operate on. It intentionally does NOT re-drive
the live audio/LLM pipeline (that is neither deterministic nor safe to run
offline, and no downstream phase needs it).
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict, List, Optional

from app import config
from app.obs import store


def load_call(call_sid: str) -> Optional[Dict[str, Any]]:
    """Return the stored call as a plain dict, or None if not found."""
    return store.get_call(call_sid)


def format_trace(call: Dict[str, Any]) -> str:
    """Render a human-readable header + turn-by-turn trace for a stored call."""
    lines: List[str] = []
    sep = "=" * 68
    lines.append(sep)
    lines.append(f"call_sid    : {call.get('call_sid')}")
    lines.append(f"clinic_id   : {call.get('clinic_id')}")
    lines.append(f"outcome     : reason={call.get('reason')} "
                 f"success={call.get('success')} final_state={call.get('final_state')}")
    lines.append(f"booking     : confirmed={call.get('booking_confirmed')} "
                 f"acuity_id={call.get('acuity_booking_id')} "
                 f"transfer_attempted={call.get('transfer_attempted')}")
    lines.append(f"timing      : start={call.get('start_utc')} "
                 f"end={call.get('end_utc')} duration_s={call.get('duration_s')}")
    lines.append(f"turns       : {call.get('turn_count')} "
                 f"(retries={call.get('total_retries')}, tone={call.get('tone')})")
    lines.append(sep)

    transcript = call.get("transcript") or []
    if not transcript:
        lines.append("(no transcript turns stored)")
    else:
        for i, turn in enumerate(transcript, 1):
            role = str(turn.get("role", "?")).upper()
            text = turn.get("text", "")
            lines.append(f"[{i:>3}] {role:<9} | {text}")
    lines.append(sep)
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.obs.replay",
        description="Replay a stored call's transcript offline.",
    )
    parser.add_argument("call_sid", help="Twilio call SID of a stored call")
    parser.add_argument("--json", action="store_true",
                        help="Print the raw stored call as JSON instead of a trace")
    args = parser.parse_args(argv)

    if not config.DATABASE_URL:
        print("ERROR: DATABASE_URL is not set — no store to replay from.", file=sys.stderr)
        return 2

    call = load_call(args.call_sid)
    if call is None:
        print(f"ERROR: no stored call with call_sid={args.call_sid}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(call, indent=2, ensure_ascii=False))
    else:
        print(format_trace(call))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
