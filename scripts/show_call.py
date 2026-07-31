#!/usr/bin/env python3
"""Print one call from obs — metadata, then the transcript.

WHY THIS EXISTS
---------------
detect_defects.py answers "which calls are broken". It does not show you the
call. Reading a transcript meant a throwaway script every time, and on
2026-07-30 that cost an exchange arguing about Render log access when the thing
actually wanted was already in obs.

USAGE
-----
    python scripts/show_call.py CAb81fe651        # SID or any unique prefix
    python scripts/show_call.py --last            # most recent call
    python scripts/show_call.py --last 5          # list the last 5, no transcript

Needs OBS_DATABASE_URL. Reads .env if the variable is not exported, so it works
both locally and in the Render shell.

WHAT IT CANNOT TELL YOU
-----------------------
obs stores what was SAID, not what the engine did. There is no tool-call trace,
so "did the model call check_availability" is not answerable here — that is a
Render log question. Guard counters that ARE exported appear under `guards`.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def _connect():
    from sqlalchemy import create_engine

    url = os.environ.get("OBS_DATABASE_URL")
    if not url:
        env = Path(__file__).resolve().parent.parent / ".env"
        if env.exists():
            for line in env.read_text(encoding="utf-8", errors="ignore").splitlines():
                if line.startswith("OBS_DATABASE_URL="):
                    url = line.split("=", 1)[1].strip()
    if not url:
        sys.exit("OBS_DATABASE_URL not set (and no .env alongside the repo root)")
    return create_engine(url, connect_args={"connect_timeout": 30})


# Shown above the transcript. Ordered so the question you usually came to ask —
# did this call end in a booking — is answered before you start reading.
_HEADLINE = (
    "call_sid", "start_utc", "duration_s", "booking_confirmed",
    "calendar_event_id", "acuity_booking_id", "success", "reason",
    # Recorded by the process, so it needs no interpretation. NULL on anything
    # before 2026-07-31 — for those, detect_defects' boundary list is the answer.
    "build_sha",
    "final_state", "turn_count", "clinic_id", "caller_number",
)


def _show(row) -> None:
    print("=" * 72)
    for k in _HEADLINE:
        if k in row:
            print(f"{k:20} {row[k]}")
    for k in ("collected", "screening", "raw"):
        v = row.get(k)
        if k == "raw" and isinstance(v, dict):
            v = v.get("guards")
            k = "guards"
        if v:
            print(f"{k:20} {v}")
    print("-" * 72)
    for t in (row.get("transcript") or []):
        who = t.get("role") or t.get("speaker") or "?"
        # An empty assistant turn is a real signal (dead air the caller heard),
        # so render it visibly rather than printing a blank line.
        print(f"[{who:9}] {t.get('text') or '<EMPTY TURN>'}")


def main() -> int:
    from sqlalchemy import text

    args = [a for a in sys.argv[1:] if a]
    if not args:
        sys.exit(__doc__)
    eng = _connect()

    with eng.connect() as c:
        if args[0] == "--last":
            n = int(args[1]) if len(args) > 1 else 1
            rows = list(c.execute(text(
                "SELECT * FROM calls ORDER BY start_utc DESC LIMIT :n"
            ), {"n": n}).mappings())
            if n > 1:
                for r in rows:
                    print(f"{r['start_utc']:%d %b %H:%M}Z  {r['call_sid']}  "
                          f"{r['duration_s']:>4}s  booked={r['booking_confirmed']}  "
                          f"{r['reason']}")
                return 0
        else:
            rows = list(c.execute(text(
                "SELECT * FROM calls WHERE call_sid LIKE :p ORDER BY start_utc"
            ), {"p": args[0] + "%"}).mappings())

    if not rows:
        print(f"no call matching {args[0]!r}", file=sys.stderr)
        return 1
    # A prefix matching several calls is shown in full rather than silently
    # picking one — an ambiguous SID that resolves to the wrong call is exactly
    # the kind of error this script exists to stop.
    for r in rows:
        _show(r)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
