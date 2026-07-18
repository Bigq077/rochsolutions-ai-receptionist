"""
app/obs/reports.py
------------------
Read-only aggregation over stored calls (spec §5.5).

Pure functions over lists of call dicts (as store.list_calls / get_call return),
so they are trivially testable without a database. The dashboard and weekly CLIs
(app/obs/dashboard.py, app/obs/weekly.py) pull rows from the store and render
these summaries. Internal-only — no client-facing view.
"""
from __future__ import annotations

import math
from collections import Counter, OrderedDict
from datetime import datetime
from typing import Any, Dict, List, Optional


def iso_week(start_utc: Optional[str]) -> str:
    """ISO year-week label (e.g. '2026-W28') from an ISO timestamp, or 'unknown'."""
    if not start_utc:
        return "unknown"
    try:
        dt = datetime.fromisoformat(start_utc)
    except (ValueError, TypeError):
        return "unknown"
    y, w, _ = dt.isocalendar()
    return f"{y}-W{w:02d}"


def summarise(calls: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Volume, booking rate, mean quality score, and failure-tag frequency."""
    volume = len(calls)
    booked = sum(1 for c in calls if c.get("booking_confirmed"))
    scores = [c["quality_score"] for c in calls if isinstance(c.get("quality_score"), int)]
    tags: Counter = Counter()
    for c in calls:
        for t in c.get("failure_tags") or []:
            tags[t] += 1
    return {
        "volume": volume,
        "booked": booked,
        "booking_rate": (booked / volume) if volume else 0.0,
        "scored": len(scores),
        "mean_quality_score": (sum(scores) / len(scores)) if scores else None,
        "failure_tags": dict(tags.most_common()),
    }


def by_clinic_week(calls: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Per-(clinic, ISO week) summary rows, ordered by clinic then week."""
    groups: "OrderedDict[tuple, List[Dict[str, Any]]]" = OrderedDict()
    for c in calls:
        key = (c.get("clinic_id") or "unknown", iso_week(c.get("start_utc")))
        groups.setdefault(key, []).append(c)

    rows: List[Dict[str, Any]] = []
    for (clinic, week) in sorted(groups.keys()):
        s = summarise(groups[(clinic, week)])
        rows.append({"clinic_id": clinic, "week": week, **s})
    return rows


def bottom_decile(calls: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The worst ~10% of *judged* calls by quality_score (lowest first).

    Ties at the cutoff score are all included so the boundary isn't arbitrary.
    Returns [] if no calls have been judged.
    """
    judged = [c for c in calls if isinstance(c.get("quality_score"), int)]
    if not judged:
        return []
    judged.sort(key=lambda c: c["quality_score"])
    k = max(1, math.ceil(len(judged) * 0.10))
    cutoff = judged[k - 1]["quality_score"]
    # Include everyone at or below the cutoff score (ties beyond k).
    return [c for c in judged if c["quality_score"] <= cutoff]
