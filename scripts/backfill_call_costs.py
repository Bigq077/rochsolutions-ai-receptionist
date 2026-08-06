#!/usr/bin/env python
"""
scripts/backfill_call_costs.py
------------------------------
Recompute per-call COGS over stored rows. Spec: docs/plan/COST_ROLLUP_SPEC.md §4.

Theorem and Vital Edge have real traffic. A month of real distribution beats any
estimate, and that is the entire point of the exercise.

Idempotent and re-runnable. Rows are recomputed when their cost_version differs
from the current RATE_TABLE_VERSION, so a rate change is a version bump plus a
re-run rather than a silently-mixed average.

    python scripts/backfill_call_costs.py --dry-run --since 2026-07-01
    python scripts/backfill_call_costs.py --since 2026-07-01
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timedelta, timezone
from typing import Optional

logging.basicConfig(level=logging.INFO, format="%(message)s")
_log = logging.getLogger("backfill_call_costs")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--since", help="ISO date, e.g. 2026-07-01")
    ap.add_argument("--until", help="ISO date (exclusive)")
    ap.add_argument("--clinic-id")
    ap.add_argument("--days", type=int, help="Shorthand for --since N days ago")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print the aggregate without writing anything")
    ap.add_argument("--force", action="store_true",
                    help="Recompute every row, not just those on a stale cost_version")
    args = ap.parse_args()

    from app.obs import cost as cost_mod
    from app.obs import store
    from app.obs.models import Call

    if not cost_mod.rates_configured():
        _log.error(
            "RATES in app/obs/cost.py are placeholders — nothing to backfill.\n"
            "Fill them from real vendor invoices (not list prices) and set\n"
            "RATE_TABLE_VERSION, then re-run. See COST_ROLLUP_SPEC.md §3.1."
        )
        return 2

    engine = store._get_engine()
    if engine is None or store._Session is None:
        _log.error("No OBS store configured — set OBS_DATABASE_URL.")
        return 2

    since: Optional[datetime] = datetime.fromisoformat(args.since) if args.since else None
    until: Optional[datetime] = datetime.fromisoformat(args.until) if args.until else None
    if args.days:
        since = datetime.now(timezone.utc) - timedelta(days=args.days)

    from sqlalchemy import select

    session = store._Session()
    target_version = cost_mod.RATE_TABLE_VERSION
    scanned = recomputed = skipped = failed = 0
    total_pence = 0

    try:
        stmt = select(Call)
        if since is not None:
            stmt = stmt.where(Call.start_utc >= since)
        if until is not None:
            stmt = stmt.where(Call.start_utc < until)
        if args.clinic_id:
            stmt = stmt.where(Call.clinic_id == args.clinic_id)

        for row in session.scalars(stmt):
            scanned += 1
            if not args.force and row.cost_version == target_version:
                skipped += 1
                if row.cost_pence is not None:
                    total_pence += row.cost_pence
                continue

            result = cost_mod.estimate_call_cost(
                duration_s=row.duration_s,
                transcript=row.transcript,
                llm_usage=(row.raw or {}).get("llm_usage"),
            )
            if result["total_pence"] is None:
                failed += 1
                _log.warning("  %s: not costed (%s)", row.call_sid, result["error"])
                continue

            total_pence += result["total_pence"]
            recomputed += 1
            if not args.dry_run:
                row.cost_pence = result["total_pence"]
                row.cost_breakdown = result["breakdown"]
                row.cost_version = result["version"]

        if args.dry_run:
            session.rollback()
            _log.info("DRY RUN — nothing written.")
        else:
            session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    _log.info(
        "scanned=%d recomputed=%d skipped(current)=%d failed=%d\n"
        "total COGS over scanned rows: £%.2f  (rate table %s)",
        scanned, recomputed, skipped, failed, total_pence / 100.0, target_version,
    )
    if failed:
        _log.warning("%d rows could not be costed — investigate before reconciling.", failed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
