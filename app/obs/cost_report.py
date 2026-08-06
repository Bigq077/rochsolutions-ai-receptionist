"""
app/obs/cost_report.py
----------------------
Per-clinic COGS and margin. The deliverable of COST_ROLLUP_SPEC.md.

The question this answers: at a flat £199/mo, at what call volume does a clinic
stop being profitable? The distribution matters more than the mean — a clinic
whose p90 call costs 3x its median is a different pricing problem from one with
a tight spread.

Reads through store.list_calls(); adds no query layer of its own.

    python -m app.obs.cost_report --since 2026-07-01
"""
from __future__ import annotations

import argparse
import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

_log = logging.getLogger(__name__)

# Cohort 1 price points, in pence. See docs/plan/COHORT_1_PLAN.md §2.3.
FOUNDING_PRICE_PENCE = 10_900   # £109 — Theorem, Vital Edge, Joint Venture
COHORT_PRICE_PENCE = 19_900     # £199 — all Hands On Money clinics


def _percentile(sorted_vals: List[int], pct: float) -> Optional[int]:
    """Nearest-rank percentile. Small n here; no need for interpolation."""
    if not sorted_vals:
        return None
    idx = max(0, min(len(sorted_vals) - 1, int(round(pct / 100.0 * len(sorted_vals) + 0.5)) - 1))
    return sorted_vals[idx]


def summarise(calls: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate one clinic-month's rows into the numbers that decide pricing."""
    costed = [c for c in calls if c.get("cost_pence") is not None]
    uncosted = len(calls) - len(costed)

    # Refuse to average across rate-table versions — that silently mixes rate
    # bases and produces a number nobody can reconcile against an invoice.
    versions = {c.get("cost_version") for c in costed if c.get("cost_version")}

    estimated_llm = sum(
        1 for c in costed
        if (c.get("cost_breakdown") or {}).get("llm_estimated") is True
    )

    vals = sorted(int(c["cost_pence"]) for c in costed)
    total = sum(vals)
    n = len(vals)

    mean = round(total / n) if n else None
    p50 = _percentile(vals, 50)
    p90 = _percentile(vals, 90)

    # Break-even: how many calls at the p90 cost before COGS eats the fee. p90
    # rather than mean, because the question is whether a BUSY clinic is
    # profitable, and a busy clinic's calls skew long.
    def _breakeven(price_pence: int) -> Optional[int]:
        if not p90 or p90 <= 0:
            return None
        return price_pence // p90

    return {
        "calls": len(calls),
        "costed": n,
        "uncosted": uncosted,
        "llm_estimated": estimated_llm,
        "versions": sorted(v for v in versions if v),
        "mixed_versions": len(versions) > 1,
        "total_pence": total,
        "mean_pence": mean,
        "p50_pence": p50,
        "p90_pence": p90,
        "max_pence": vals[-1] if vals else None,
        "margin_199_pence": COHORT_PRICE_PENCE - total if n else None,
        "margin_109_pence": FOUNDING_PRICE_PENCE - total if n else None,
        "breakeven_calls_199": _breakeven(COHORT_PRICE_PENCE),
        "breakeven_calls_109": _breakeven(FOUNDING_PRICE_PENCE),
    }


def _month_key(row: Dict[str, Any]) -> str:
    raw = row.get("start_utc")
    if not raw:
        return "unknown"
    try:
        return str(raw)[:7]  # YYYY-MM
    except Exception:
        return "unknown"


def build(since: Optional[datetime] = None, until: Optional[datetime] = None,
          clinic_id: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
    """{clinic_id: {month: summary}} for the requested window."""
    from app.obs import store

    rows = store.list_calls(since=since, until=until, clinic_id=clinic_id)
    buckets: Dict[str, Dict[str, List[Dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    for r in rows:
        buckets[r.get("clinic_id") or "unknown"][_month_key(r)].append(r)

    return {
        clinic: {month: summarise(rs) for month, rs in sorted(months.items())}
        for clinic, months in sorted(buckets.items())
    }


def _gbp(pence: Optional[int]) -> str:
    return "—" if pence is None else f"£{pence / 100:,.2f}"


def render(report: Dict[str, Dict[str, Any]]) -> str:
    if not report:
        return "No calls in window (or no OBS store configured).\n"

    out: List[str] = []
    for clinic, months in report.items():
        out.append(f"\n=== {clinic} ===")
        for month, s in months.items():
            out.append(f"\n  {month}   {s['calls']} calls ({s['costed']} costed, {s['uncosted']} not)")
            if not s["costed"]:
                out.append("    no costed rows — are RATES filled in app/obs/cost.py?")
                continue
            out.append(f"    total COGS      {_gbp(s['total_pence'])}")
            out.append(f"    per call        mean {_gbp(s['mean_pence'])}  "
                       f"p50 {_gbp(s['p50_pence'])}  p90 {_gbp(s['p90_pence'])}  "
                       f"max {_gbp(s['max_pence'])}")
            out.append(f"    margin @ £199   {_gbp(s['margin_199_pence'])}")
            out.append(f"    margin @ £109   {_gbp(s['margin_109_pence'])}")
            out.append(f"    break-even      {s['breakeven_calls_199']} calls @ £199  "
                       f"/ {s['breakeven_calls_109']} @ £109   (at p90 cost)")
            if s["llm_estimated"]:
                out.append(f"    ⚠ {s['llm_estimated']}/{s['costed']} rows use ESTIMATED LLM cost "
                           f"— exclude from the margin decision")
            if s["mixed_versions"]:
                out.append(f"    ⚠ mixed rate-table versions {s['versions']} — "
                           f"re-run scripts/backfill_call_costs.py before trusting this")
        out.append("")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description="Per-clinic COGS and margin report.")
    ap.add_argument("--since", help="ISO date, e.g. 2026-07-01")
    ap.add_argument("--until", help="ISO date (exclusive)")
    ap.add_argument("--clinic-id")
    ap.add_argument("--days", type=int, help="Shorthand for --since N days ago")
    args = ap.parse_args()

    since = datetime.fromisoformat(args.since) if args.since else None
    until = datetime.fromisoformat(args.until) if args.until else None
    if args.days:
        since = datetime.now(timezone.utc) - timedelta(days=args.days)

    print(render(build(since=since, until=until, clinic_id=args.clinic_id)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
