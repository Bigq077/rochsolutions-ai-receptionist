#!/usr/bin/env python3
"""
obs_source.py - read real call records from the observability store.

The suite writes `tests/auto/results/*.json`; production writes rows to the
`calls` table (app/obs/models.py). They share almost no vocabulary, so this
module translates obs rows into the same row shape collect_failures.py already
renders and clusters.

Reading needs only OBS_DATABASE_URL (or DATABASE_URL). It does NOT need
OBS_CAPTURE_ENABLED - that flag gates writes. You can triage calls already
captured without switching anything on.

Run from the repo root on a branch that has the full app/obs/ package
(origin/latency-eval has 22 modules; jv-v1-onboarding has 2 and cannot be used).
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any

# ---------------------------------------------------------------------------
# Severity - patient impact, per SKILL.md step 7. Lower = worse.
# This is the obs equivalent of FLOW_ORDER's rank: it decides what leads the
# report. It is NOT frequency; a 2-call severity-1 cluster outranks 40 rude
# phrasings, because one phantom booking is a patient who turns up to nothing.
# ---------------------------------------------------------------------------
SEVERITY: dict[str, int] = {
    # --- 1: the caller believes something that is not true --------------
    "PHANTOM_BOOKING":      1,   # derived; see derive_signals()
    "booking_error":        1,
    "hallucination":        1,
    "wrong_info":           1,
    # --- 2: clinical safety ----------------------------------------------
    "DORMANT_SCREENING":    2,   # derived; red-flag screen armed but never fired
    "missed_escalation":    2,
    "wrong_service_fit":    2,
    # --- 3: the caller gave up or went nowhere ---------------------------
    "dead_end":             3,
    "loop":                 3,
    "ABANDONED":            3,   # derived from outcome
    "caller_frustration":   3,
    # --- 4: handled, but not well ----------------------------------------
    "LOW_QUALITY":          4,   # derived from quality_score
    "NO_BOOKING":           4,   # derived from outcome
    "MISROUTED":            4,
}

DEFAULT_SEVERITY = 4
TERMINAL_TAGS = {"NO_BOOKING", "ABANDONED", "MISROUTED", "LOW_QUALITY"}


def severity(tag: str) -> int:
    return SEVERITY.get(tag, DEFAULT_SEVERITY)


# ---------------------------------------------------------------------------
# Derived signals - the value this module adds over reading the table raw
# ---------------------------------------------------------------------------

def derive_signals(row: dict[str, Any]) -> list[str]:
    """Signals the stored columns imply but never state outright."""
    out: list[str] = []

    # --- PHANTOM BOOKING -------------------------------------------------
    # The flow set booking_confirmed, but neither provider returned an id.
    # This is INCIDENT.md severity 1 exactly: "a booking the caller believes
    # exists but does not". It is mechanically detectable and nothing else in
    # the pipeline reports it, because each half looks fine on its own.
    if row.get("booking_confirmed") and not (
        row.get("acuity_booking_id") or row.get("calendar_event_id")
    ):
        out.append("PHANTOM_BOOKING")

    # --- DORMANT SCREENING ----------------------------------------------
    # screening.arm_paths maps {screen_id: "trigger"|"orphan"}. An "orphan"
    # with no "trigger" anywhere is a red-flag screen that armed and never
    # fired - the signature models.py says took a human reading a full log to
    # spot in the 2026-07-25 sweep.
    screening = row.get("screening") or {}
    arm_paths = (screening or {}).get("arm_paths") or {}
    if isinstance(arm_paths, dict) and arm_paths:
        vals = set(arm_paths.values())
        if "orphan" in vals and "trigger" not in vals:
            out.append("DORMANT_SCREENING")

    # --- outcome-derived --------------------------------------------------
    outcome = (row.get("outcome") or "").lower()
    if outcome == "abandoned":
        out.append("ABANDONED")
    elif outcome == "no_booking":
        out.append("NO_BOOKING")
    elif outcome == "misrouted":
        out.append("MISROUTED")

    # --- judge score ------------------------------------------------------
    score = row.get("quality_score")
    if isinstance(score, int) and score <= 2:
        out.append("LOW_QUALITY")

    return out


def is_failure(row: dict[str, Any], derived: list[str]) -> bool:
    """A call worth triaging.

    Deliberately wider than `success is False`: a call can report success and
    still be a severity-1 defect (PHANTOM_BOOKING is exactly that shape).
    """
    if row.get("failure_tags"):
        return True
    if derived:
        return True
    if row.get("success") is False:
        return True
    score = row.get("quality_score")
    return isinstance(score, int) and score <= 2


def last_assistant_turn(row: dict[str, Any]) -> str:
    turns = row.get("transcript") or []
    for t in reversed(turns):
        role = str((t or {}).get("role", "")).lower()
        if role in ("assistant", "susie", "bot", "ai"):
            return str(t.get("text") or "").strip().replace("\n", " ")[:160]
    return "(no assistant turn captured)"


# ---------------------------------------------------------------------------
# Load
# ---------------------------------------------------------------------------

def load_obs_rows(
    since: str | None = None,
    clinic: str | None = None,
    days: int | None = None,
) -> list[dict[str, Any]]:
    """Fetch call rows via app.obs.store.list_calls().

    Raises SystemExit with an actionable message rather than a traceback - this
    runs from a skill, and the failure modes are all configuration.
    """
    if not (os.getenv("OBS_DATABASE_URL") or os.getenv("DATABASE_URL")):
        raise SystemExit(
            "No obs store configured.\n"
            "  Set OBS_DATABASE_URL (preferred) or DATABASE_URL to the "
            "observability Postgres URL.\n"
            "  Reading does NOT require OBS_CAPTURE_ENABLED - that flag only "
            "gates writing new calls."
        )

    try:
        from app.obs import store  # noqa: PLC0415 - optional, branch-dependent
    except ImportError as exc:
        raise SystemExit(
            f"Could not import app.obs.store ({exc}).\n"
            "  Run from the repo root, on a branch carrying the full app/obs/ "
            "package.\n"
            "  origin/latency-eval has 22 modules; jv-v1-onboarding has 2 and "
            "cannot be used for triage."
        ) from exc

    start: datetime | None = None
    if since:
        start = datetime.fromisoformat(since).replace(tzinfo=timezone.utc)
    elif days:
        start = datetime.now(timezone.utc) - timedelta(days=days)

    rows = store.list_calls(since=start, clinic_id=clinic)
    if not rows:
        raise SystemExit(
            "The obs store returned no calls for that window.\n"
            "  Widen --since/--days, drop --clinic, and confirm "
            "OBS_DATABASE_URL points at the store the clinics actually write to."
        )
    return rows


def to_triage_rows(obs_rows: list[dict[str, Any]]) -> tuple[list[dict], int]:
    """Translate obs rows into collect_failures' row shape.

    Returns (failing rows, total calls seen).
    """
    out: list[dict] = []

    for r in obs_rows:
        derived = derive_signals(r)
        if not is_failure(r, derived):
            continue

        tags = list(r.get("failure_tags") or []) + derived
        # dedupe, keep worst first
        tags = sorted(set(tags), key=lambda t: (severity(t), t))
        worst = tags[0] if tags else "unclassified"

        out.append({
            # identity - call_sid replaces scenario_id; it is what `python -m
            # app.obs.show <sid>` and `app.obs.to_scenario <sid>` both take.
            "id": r.get("call_sid"),
            "label": r.get("call_sid"),
            "name": f"{r.get('clinic_id') or '?'} / {r.get('outcome') or 'unjudged'}",
            "phase": f"build {r.get('build_sha') or 'unknown'}",
            "source": "obs",
            # clustering keys
            "earliest_failing_check": worst,
            "cascade_only": worst in TERMINAL_TAGS,
            "failing_checks": tags,
            "severity": severity(worst),
            # stall point - final_state is the obs equivalent of a stuck state
            "stall_state": r.get("final_state"),
            "stall_turns": 0,
            "stall_handler": None,
            "trace_tail": [],
            "flow_step": r.get("final_state"),
            "selected_slot": (r.get("collected") or {}).get("slot"),
            "end_reason": r.get("reason"),
            "turns": r.get("turn_count"),
            "duration_seconds": r.get("duration_s"),
            "last_susie_turn": last_assistant_turn(r),
            # obs-only fields, surfaced by the renderer
            "quality_score": r.get("quality_score"),
            "build_sha": r.get("build_sha"),
            "clinic_id": r.get("clinic_id"),
            "evidence": (r.get("evidence") or "")[:200],
            "action_needed": r.get("action_needed"),
        })

    out.sort(key=lambda x: (x["severity"], str(x["earliest_failing_check"])))
    return out, len(obs_rows)

def fleet_summary(obs_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Headline numbers, computed by app/obs/reports.py - not re-derived here.

    Reusing the same function `python -m app.obs.weekly` calls means the triage
    report and the Monday ritual can never disagree about volume, booking rate
    or mean score.
    """
    try:
        from app.obs import reports  # noqa: PLC0415
    except ImportError:
        return {}
    return reports.summarise(obs_rows)


def missed_by_bottom_decile(
    obs_rows: list[dict[str, Any]], triage_rows: list[dict[str, Any]]
) -> list[str]:
    """Severity 1-2 calls that `weekly.py`'s bottom-decile list cannot surface.

    weekly.py ranks by quality_score. A PHANTOM_BOOKING scores *well* - the
    transcript reads as a clean, successful booking, because the failure is that
    the booking was never written, which the transcript cannot show. Those calls
    sit at the top of the score distribution and never enter the bottom decile.

    This is the gap the --obs mode exists to close, so name it explicitly.
    """
    try:
        from app.obs import reports  # noqa: PLC0415
        decile = {c.get("call_sid") for c in reports.bottom_decile(obs_rows)}
    except ImportError:
        return []
    return [
        r["id"] for r in triage_rows
        if r["severity"] <= 2 and r["id"] not in decile
    ]
