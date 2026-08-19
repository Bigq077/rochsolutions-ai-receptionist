#!/usr/bin/env python3
"""Print the latency baseline from the obs store instead of from a log export.

WHY THIS EXISTS
---------------
lat_parse.py reads `[LAT]` lines out of a Render log window. That was the only
way to get a latency figure, and it has a ceiling that no amount of care gets
past: the line takes roughly a dozen calls a day and log retention is measured in
hours, so an export can only ever contain the handful of calls inside the window.
Two sessions of exporting produced a largest sample of 29 turns across 3 calls —
one caller, one clinic, mostly reschedules. Enough to say llm_ttft dominates and
~86% of turns miss the 1.5 s bar. Not enough to size the work, and it could never
have become enough, because the window slides forward and drops what it passes.

Now that each call's turns are stored on its obs row, the sample accumulates
instead. This script reads them back and hands them to lat_parse.py, so the table
is produced by the same parser, with the same percentile method, as every earlier
baseline — the numbers are comparable to the locked one rather than a new dialect
of them.

USAGE
-----
    python scripts/lat_baseline.py                          # last 7 days
    python scripts/lat_baseline.py --since 2026-08-01
    python scripts/lat_baseline.py --clinic vital_edge
    python scripts/lat_baseline.py --lines > sample.log     # raw [LAT] lines

Needs OBS_DATABASE_URL (read from .env if unset), like the other obs scripts.

WHAT IT CANNOT SEE
------------------
Calls recorded before the `latency` column existed, and calls from any service
running with LATENCY_TIMING off — both are NULL by absence, not by measurement,
and are reported as skipped rather than counted as fast.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))  # scripts/ (detect_defects)
sys.path.insert(0, str(_ROOT))                            # repo root (lat_parse)

# The [LAT] field order, matching TurnTiming.as_record(). Order is cosmetic —
# lat_parse parses by key — but keeping it identical to the live log line means a
# line from here and a line from Render diff cleanly against each other.
_FIELDS = (
    "turn_seq", "path", "outcome", "ttfa_ms", "content_ttfa_ms", "ep_dispatch_ms",
    "llm_ttft_ms", "chunk_gate_ms", "tts_first_byte_ms", "audio_wire_ms",
    "flags", "model", "stt_model", "eot_confident", "capture_phase",
    "endpoint_wait_ms",
)


def _latency_of(row) -> dict | None:
    """The row's latency payload as a dict, whatever the driver handed back.

    Postgres JSON comes back as a dict; SQLite and some driver/pool combinations
    hand back the raw string. Both are normal, so both are accepted rather than
    letting one of them read as "this call has no latency".
    """
    value = row.get("latency") if hasattr(row, "get") else None
    if not value:
        return None
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return None
    return value if isinstance(value, dict) else None


def _lat_lines(rows) -> tuple[list[str], int, int]:
    """Render every stored turn as a [LAT] line. Returns (lines, calls, skipped)."""
    lines: list[str] = []
    calls = skipped = 0
    for row in rows:
        payload = _latency_of(row)
        turns = (payload or {}).get("turns") or []
        if not turns:
            skipped += 1
            continue
        calls += 1
        for turn in turns:
            kv = " ".join(f"{k}={turn.get(k, -1)}" for k in _FIELDS)
            lines.append(f"[LAT] {kv}\n")
    return lines, calls, skipped


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", help="ISO date/timestamp, UTC. Default: 7 days ago.")
    ap.add_argument("--clinic", help="restrict to one clinic_id")
    ap.add_argument("--lines", action="store_true",
                    help="emit raw [LAT] lines to stdout instead of the table")
    a = ap.parse_args()

    since = (
        datetime.fromisoformat(a.since).replace(tzinfo=timezone.utc)
        if a.since
        else datetime.now(timezone.utc) - timedelta(days=7)
    )

    from detect_defects import load_calls

    rows = load_calls(since)
    if a.clinic:
        rows = [r for r in rows if r.get("clinic_id") == a.clinic]

    lines, calls, skipped = _lat_lines(rows)

    if not lines:
        print(
            f"No stored latency in {len(rows)} call(s) since {since.date()}.\n"
            "Either the calls predate the `latency` column, or the service ran "
            "with LATENCY_TIMING off. Neither means the calls were fast.",
            file=sys.stderr,
        )
        return 1

    if a.lines:
        sys.stdout.writelines(lines)
        return 0

    print(
        f"{len(lines)} turns across {calls} calls since {since.date()}"
        f" ({skipped} call(s) had no stored latency)\n",
        file=sys.stderr,
    )

    # Hand the lines to lat_parse rather than re-implementing its table. Same
    # parser, same percentile method, so these numbers sit alongside the locked
    # baseline instead of being a second dialect of it.
    import lat_parse

    with tempfile.NamedTemporaryFile(
        "w", suffix=".lat", delete=False, encoding="utf-8"
    ) as fh:
        fh.writelines(lines)
        tmp = fh.name
    try:
        argv = sys.argv
        sys.argv = ["lat_parse", tmp]
        try:
            lat_parse.main()
        finally:
            sys.argv = argv
    finally:
        Path(tmp).unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
