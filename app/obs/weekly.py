"""
app/obs/weekly.py
-----------------
The Monday-ritual command (spec §5.5 + "the weekly ritual").

    python -m app.obs.weekly [--days 7] [--clinic theorem]

Prints the week's summary plus the bottom-decile calls by quality_score — the
call_sids to replay/review and turn into regression scenarios. This is the input
to the 30-minute weekly improvement loop; run it, review the listed calls, and
convert new failure modes with `python -m app.obs.to_scenario <call_sid>`.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from app import config
from app.obs import reports, store


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.obs.weekly")
    parser.add_argument("--days", type=int, default=7, help="lookback window in days")
    parser.add_argument("--clinic", default=None, help="filter to one clinic_id")
    args = parser.parse_args(argv)

    if not config.DATABASE_URL:
        print("ERROR: OBS_DATABASE_URL (or DATABASE_URL) not set — no store to report on.", file=sys.stderr)
        return 2

    since = datetime.now(timezone.utc) - timedelta(days=args.days)
    calls = store.list_calls(since=since, clinic_id=args.clinic)
    s = reports.summarise(calls)

    scope = f" (clinic={args.clinic})" if args.clinic else ""
    mean = f"{s['mean_quality_score']:.2f}" if s["mean_quality_score"] is not None else "—"
    print(f"Susie weekly review — last {args.days} days{scope}")
    print(f"  volume={s['volume']}  booking_rate={s['booking_rate'] * 100:.0f}%  "
          f"mean_score={mean} (scored {s['scored']})")
    if s["failure_tags"]:
        print("  failure tags: " + ", ".join(f"{k}×{v}" for k, v in s["failure_tags"].items()))
    print()

    worst = reports.bottom_decile(calls)
    if not worst:
        print("No judged calls yet — enable the judge (OBS_JUDGE_ENABLED) to populate scores.")
        return 0

    print(f"Bottom-decile calls to review ({len(worst)}):")
    print(f"  {'score':>5}  {'clinic':<12} {'call_sid':<20} failure_tags")
    for c in worst:
        tags = ", ".join(c.get("failure_tags") or [])
        print(f"  {c['quality_score']:>5}  {(c.get('clinic_id') or '')[:12]:<12} "
              f"{(c.get('call_sid') or '')[:20]:<20} {tags}")
    print("\nReview each, then: python -m app.obs.to_scenario <call_sid>")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
