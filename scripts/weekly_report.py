#!/usr/bin/env python3
"""
scripts/weekly_report.py
------------------------
Aggregates the last 7 days of per-call JSONL logs from the logs/ directory
and prints a human-readable weekly summary to stdout.

Usage:
    python scripts/weekly_report.py [--days N] [--log-dir PATH]

Stdlib only — no third-party dependencies.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List


def load_records(log_dir: Path, days: int) -> List[Dict[str, Any]]:
    """Read JSONL records from the last `days` calendar days."""
    records: List[Dict[str, Any]] = []
    now_utc = datetime.now(timezone.utc)
    cutoff = now_utc - timedelta(days=days)

    for day_offset in range(days + 1):
        date = (now_utc - timedelta(days=day_offset)).strftime("%Y-%m-%d")
        path = log_dir / f"calls_{date}.jsonl"
        if not path.exists():
            continue
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                # Filter to calls that started within the window
                start_str = rec.get("start_utc", "")
                if start_str:
                    try:
                        start = datetime.fromisoformat(start_str)
                        if start < cutoff:
                            continue
                    except ValueError:
                        pass
                records.append(rec)

    return records


def fmt_duration(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    return f"{m}m {s:02d}s"


def build_report(records: List[Dict[str, Any]], days: int) -> str:
    if not records:
        return f"No call records found in the last {days} days.\n"

    total = len(records)
    successful = sum(1 for r in records if r.get("success"))
    booked = sum(1 for r in records if r.get("reason") == "booked")
    transferred = sum(1 for r in records if r.get("reason") == "transferred")
    graceful_exits = sum(1 for r in records if r.get("graceful_exit"))
    booking_confirmed = sum(1 for r in records if r.get("booking_confirmed"))

    durations = [r["duration_s"] for r in records if isinstance(r.get("duration_s"), (int, float))]
    avg_duration = sum(durations) / len(durations) if durations else 0
    max_duration = max(durations) if durations else 0

    total_retries = sum(r.get("total_retries", 0) for r in records)
    avg_retries = total_retries / total if total else 0

    # Per-clinic breakdown
    by_clinic: Dict[str, List[Dict]] = defaultdict(list)
    for r in records:
        clinic = r.get("clinic_id") or "unknown"
        by_clinic[clinic].append(r)

    # Reason distribution
    reason_counts: Counter = Counter(r.get("reason", "unknown") for r in records)

    # Tone distribution
    tone_counts: Counter = Counter(r.get("tone", "unknown") for r in records)

    lines: List[str] = []
    lines.append("=" * 60)
    lines.append(f"  SUSIE WEEKLY CALL REPORT — last {days} days")
    lines.append("=" * 60)
    lines.append(f"  Total calls           : {total}")
    lines.append(f"  Bookings confirmed    : {booking_confirmed}  ({100 * booking_confirmed // total if total else 0}%)")
    lines.append(f"  Successful calls      : {successful}")
    lines.append(f"  Booked (reason)       : {booked}")
    lines.append(f"  Transferred           : {transferred}")
    lines.append(f"  Graceful exits        : {graceful_exits}")
    lines.append("")
    lines.append(f"  Avg call duration     : {fmt_duration(avg_duration)}")
    lines.append(f"  Longest call          : {fmt_duration(max_duration)}")
    lines.append(f"  Total retries         : {total_retries}")
    lines.append(f"  Avg retries/call      : {avg_retries:.1f}")
    lines.append("")
    lines.append("  Call outcome breakdown:")
    for reason, count in reason_counts.most_common():
        lines.append(f"    {reason:<22}: {count}")
    lines.append("")
    lines.append("  Caller tone breakdown:")
    for tone, count in tone_counts.most_common():
        lines.append(f"    {tone:<22}: {count}")
    lines.append("")

    if len(by_clinic) > 1:
        lines.append("  Per-clinic summary:")
        for clinic_id, recs in sorted(by_clinic.items()):
            booked_c = sum(1 for r in recs if r.get("booking_confirmed"))
            lines.append(f"    {clinic_id:<20}: {len(recs)} calls, {booked_c} booked")
        lines.append("")

    lines.append("=" * 60)
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Susie weekly call report")
    parser.add_argument("--days", type=int, default=7, help="Number of days to look back (default: 7)")
    parser.add_argument("--log-dir", type=str, default="logs", help="Path to logs directory (default: logs/)")
    args = parser.parse_args()

    log_dir = Path(args.log_dir)
    if not log_dir.exists():
        print(f"Log directory not found: {log_dir}", file=sys.stderr)
        sys.exit(1)

    records = load_records(log_dir, args.days)
    print(build_report(records, args.days))


if __name__ == "__main__":
    main()
