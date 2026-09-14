#!/usr/bin/env python3
"""
collect_failures.py - gather Susie call failures into one compact record set.

This is the *collection* step of susie-triage. It does not cluster and it does
not diagnose; it produces the evidence an agent clusters by hand.

Usage:
    python collect_failures.py tests/auto/results/results_20260407_110303.json
    python collect_failures.py tests/auto/results --since 2026-04-01
    python collect_failures.py <path> --json      # machine-readable output

Everything it prints is derived from the result records, never inferred.

FLOW_ORDER below is the canonical position of each gating check in a call.
It is documented in prose in ../references/symptom-map.md - keep them in sync.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

# ---------------------------------------------------------------------------
# Flow order - earliest position in the call at which a check can fail.
# Lower rank = earlier. Prefixes are matched with startswith().
# Rank 90 = cross-cutting: these can fail anywhere, so they are never taken as
# the earliest failing check when a positional check also failed.
# ---------------------------------------------------------------------------
FLOW_ORDER: list[tuple[str, int]] = [
    # --- connection / greeting -------------------------------------------
    ("answered_in_time",              10),
    ("greeting_has_",                 11),
    ("greeting_no_",                  11),
    ("first_turn_contains",           12),
    ("first_turn_no_",                12),
    # --- caller audio reaching Susie at all -------------------------------
    ("susie_responded_to_patient",    20),
    ("flow_continues",                21),
    # --- silence / re-ask ladder ------------------------------------------
    ("reask_fired",                   30),
    ("reask_phrase_correct",          31),
    ("second_reask_fired",            32),
    ("second_reask_phrase_correct",   33),
    ("transfer_played",               34),
    ("transfer_has_",                 35),
    # --- clinic disambiguation (two-clinic tenants) -----------------------
    ("asked_which_clinic",            40),
    ("location_not_asked",            41),
    # --- intake -----------------------------------------------------------
    ("new_or_returning_correct",      50),
    ("empathy_response_present",      51),
    ("empathy_contains_condition",    52),
    ("duration_question_asked",       53),
    ("offered_booking",               54),
    # --- identity collection ----------------------------------------------
    ("asked_for_name",                60),
    ("number_confirmed_verbally",     61),
    # --- availability / slot selection ------------------------------------
    ("asked_for_availability",        70),
    ("slot_confirmed",                71),
    ("confirmation_contains",         72),
    # --- terminal ---------------------------------------------------------
    ("booking_confirmed",             80),
    ("reschedule_confirmed",          80),
    ("cancel_confirmed",              80),
    ("flow_completed",                81),
    ("graceful_end",                  82),
    # --- cross-cutting: position-free, never the earliest on their own ----
    ("flow_order_correct",            90),
    ("no_question_asked_twice",       90),
    ("no_state_corruption",           90),
    ("not_said_",                     90),
    ("banned_phrases_absent",         90),
    ("no_technical_error",            90),
    ("no_dead_air",                   90),
    ("no_crash",                      90),
]

TERMINAL_RANK = 80       # booking/reschedule/cancel/flow_completed
CROSS_CUTTING_RANK = 90  # can fail anywhere in the call
UNKNOWN_RANK = 95


def rank(check: str) -> int:
    """Flow-order rank of a check name. Longest matching prefix wins."""
    best, best_len = UNKNOWN_RANK, -1
    for prefix, r in FLOW_ORDER:
        if check.startswith(prefix) and len(prefix) > best_len:
            best, best_len = r, len(prefix)
    return best


def failing_checks(record: dict) -> list[str]:
    checks = (record.get("evaluation") or {}).get("checks", {}) or {}
    return [
        k for k, v in checks.items()
        if v is False and not k.startswith("info_")
    ]


def earliest(checks: list[str]) -> tuple[str | None, bool]:
    """Return (earliest failing check, cascade_only).

    cascade_only is True when every failing check is terminal or cross-cutting:
    nothing upstream failed, so the check data alone cannot localise this call.
    Use the stall point (flow_step + last Susie turn) instead.
    """
    if not checks:
        return None, False
    ordered = sorted(checks, key=lambda c: (rank(c), c))
    first = ordered[0]
    return first, rank(first) >= TERMINAL_RANK


def last_susie_turn(record: dict) -> str:
    turns = record.get("susie_said") or []
    if not turns:
        return "(no speech captured)"
    text = (turns[-1].get("text") or "").strip().replace("\n", " ")
    return text[:160]


def stall(record: dict) -> dict:
    """Find the trailing run of turns where the flow state never advanced.

    turn_traces record state_before -> state_after for every caller utterance.
    A call that ends with several turns in the same state was *heard* and not
    *understood* - a very different bug from one where nothing was heard at all.
    """
    traces = record.get("turn_traces") or []
    if not traces:
        return {"state": None, "turns": 0, "handler": None, "tail": []}

    last_state = traces[-1].get("state_after")
    run = 0
    handlers: list[str] = []
    for tr in reversed(traces):
        if tr.get("state_before") == last_state and tr.get("state_after") == last_state:
            run += 1
            h = tr.get("handled_by")
            if h and h not in handlers:
                handlers.append(h)
        else:
            break

    tail = [
        f"{tr.get('state_before')}->{tr.get('state_after')}"
        f"  by={tr.get('handled_by')}"
        f"  said={(tr.get('user_text_normalized') or '')[:48]!r}"
        for tr in traces[-3:]
    ]
    return {
        "state": last_state if run else None,
        "turns": run,
        "handler": handlers[0] if handlers else None,
        "tail": tail,
    }


def load_records(target: Path, since: str | None) -> list[dict]:
    if target.is_dir():
        files = sorted(target.glob("results_*.json"))
    else:
        files = [target]

    if since:
        stamp = since.replace("-", "")
        files = [
            f for f in files
            if len(f.stem.split("_")) > 1 and f.stem.split("_")[1] >= stamp
        ]

    if not files:
        sys.exit(
            f"No results_*.json found under {target}"
            + (f" since {since}" if since else "")
        )

    records: list[dict] = []
    for f in files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"WARN: skipping {f.name}: {exc}", file=sys.stderr)
            continue
        if not isinstance(data, list):
            print(f"WARN: skipping {f.name}: not a results list", file=sys.stderr)
            continue
        for r in data:
            if isinstance(r, dict):
                r["_source"] = f.name
                records.append(r)
    return records


def summarise(records: list[dict]) -> dict:
    passed = [r for r in records if (r.get("evaluation") or {}).get("passed")]
    failed = [r for r in records if not (r.get("evaluation") or {}).get("passed")]

    rows = []
    for r in failed:
        checks = failing_checks(r)
        first, cascade_only = earliest(checks)
        st = stall(r)
        rows.append({
            "stall_state": st["state"],
            "stall_turns": st["turns"],
            "stall_handler": st["handler"],
            "trace_tail": st["tail"],
            "id": r.get("scenario_id"),
            "name": r.get("scenario_name"),
            "phase": r.get("phase"),
            "source": r.get("_source"),
            "earliest_failing_check": first,
            "cascade_only": cascade_only,
            "failing_checks": checks,
            "flow_step": r.get("flow_step"),
            "selected_slot": r.get("selected_slot"),
            "end_reason": r.get("end_reason"),
            "turns": r.get("turns"),
            "duration_seconds": r.get("duration_seconds"),
            "last_susie_turn": last_susie_turn(r),
        })
    rows.sort(key=lambda x: (rank(x["earliest_failing_check"] or ""), str(x["id"])))

    # Scenario ids repeat across runs - qualify them when the window spans more
    # than one results file, so a cluster of four is not one id listed four times.
    multi_run = len({r.get("_source") for r in records}) > 1
    for row in rows:
        if multi_run:
            stamp = (row["source"] or "").replace("results_", "").replace(".json", "")
            row["label"] = f"{row['id']}@{stamp}"
        else:
            row["label"] = str(row["id"])

    by_earliest: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        by_earliest[row["earliest_failing_check"] or "(none)"].append(row["label"])

    return {
        "total": len(records),
        "passed": len(passed),
        "failed": len(failed),
        "stall_states": Counter(
            row["stall_state"] for row in rows if row["stall_turns"] >= 2
        ),
        "pass_flow_steps": Counter(r.get("flow_step") for r in passed),
        "fail_flow_steps": Counter(r.get("flow_step") for r in failed),
        "by_earliest": dict(by_earliest),
        "rows": rows,
    }


def summarise_obs(rows: list[dict], total: int) -> dict:
    """Build the same summary shape from obs rows (see obs_source.py)."""
    by_earliest: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        by_earliest[row["earliest_failing_check"] or "(none)"].append(row["label"])

    return {
        "source": "obs",
        "total": total,
        "passed": total - len(rows),
        "failed": len(rows),
        "stall_states": Counter(
            r["stall_state"] for r in rows if r.get("stall_state")
        ),
        "builds": Counter(r.get("build_sha") or "unknown" for r in rows),
        "clinics": Counter(r.get("clinic_id") or "unknown" for r in rows),
        "pass_flow_steps": Counter(),
        "fail_flow_steps": Counter(),
        "by_earliest": dict(by_earliest),
        "rows": rows,
    }


def render_obs(s: dict) -> str:
    """Renderer for obs rows - different columns, same clustering discipline."""
    from obs_source import severity  # local import; obs mode only

    out: list[str] = []
    out.append(
        f"CALLS: {s['total']} | CLEAN: {s['passed']} | WITH FINDINGS: {s['failed']}"
    )
    f = s.get("fleet") or {}
    if f:
        mean = f.get("mean_quality_score")
        out.append(
            f"FLEET (app/obs/reports.summarise - same figures as "
            f"`python -m app.obs.weekly`):"
        )
        out.append(
            f"  volume={f.get('volume')}  "
            f"booking_rate={(f.get('booking_rate') or 0) * 100:.0f}%  "
            f"mean_score={mean:.2f}" if mean is not None else
            f"  volume={f.get('volume')}  "
            f"booking_rate={(f.get('booking_rate') or 0) * 100:.0f}%  mean_score=-"
        )
        if f.get("failure_tags"):
            out.append("  judge tags: " + ", ".join(
                f"{k}x{v}" for k, v in f["failure_tags"].items()
            ))
    out.append("")
    out.append("CANDIDATE CLUSTERS (grouped by worst signal, ranked by patient impact):")
    for tag, ids in sorted(
        s["by_earliest"].items(), key=lambda kv: (severity(kv[0]), kv[0])
    ):
        sev = severity(tag)
        note = {
            1: "  [SEV 1 - caller believes something untrue]",
            2: "  [SEV 2 - clinical safety]",
            3: "  [SEV 3 - caller gave up]",
        }.get(sev, "")
        out.append(f"  {len(ids):3d}  sev{sev}  {tag}{note}")
        out.append(f"       {', '.join(ids[:12])}"
                   + (f"  (+{len(ids) - 12} more)" if len(ids) > 12 else ""))
    out.append("")
    out.append("FINAL STATE (where these calls ended):")
    for st, n in s["stall_states"].most_common(12):
        out.append(f"  {n:3d}  {st}")
    out.append("")
    out.append("BY BUILD (grouping defects by build is the main question "
               "this table answers):")
    for b, n in s["builds"].most_common(10):
        out.append(f"  {n:3d}  {b}")
    if len(s["clinics"]) > 1:
        out.append("")
        out.append("BY CLINIC:")
        for c, n in s["clinics"].most_common():
            out.append(f"  {n:3d}  {c}")
    missed = s.get("missed_by_decile") or []
    if missed:
        out.append("")
        out.append(
            f"INVISIBLE TO `app/obs/weekly.py` ({len(missed)} sev1-2 calls):"
        )
        out.append(
            "  weekly.py lists the bottom decile BY SCORE. These score too "
            "well to appear there - a phantom booking reads as a clean "
            "successful call, because what failed is the write, which the "
            "transcript cannot show."
        )
        out.append("  " + ", ".join(missed[:10])
                   + (f"  (+{len(missed) - 10} more)" if len(missed) > 10 else ""))
    out.append("")
    out.append("CALLS WITH FINDINGS:")
    for r in s["rows"]:
        out.append(
            f"  [{r['label']}] {r['name']}  sev{r['severity']}\n"
            f"      signals={','.join(r['failing_checks'])}"
            f"{' (terminal - look upstream)' if r['cascade_only'] else ''}\n"
            f"      final_state={r['stall_state']} quality={r.get('quality_score')} "
            f"build={r.get('build_sha')} turns={r['turns']} "
            f"action={r.get('action_needed')}\n"
            f"      last_susie=\"{r['last_susie_turn']}\""
        )
        if r.get("evidence"):
            out.append(f"      judge: {r['evidence']}")
        out.append(f"      replay: python -m app.obs.show {r['id']}")
    return "\n".join(out)


def render(s: dict) -> str:
    out: list[str] = []
    rate = (s["passed"] / s["total"] * 100) if s["total"] else 0.0
    out.append(
        f"CALLS: {s['total']} | PASS: {s['passed']} | FAIL: {s['failed']} "
        f"| RATE: {rate:.1f}%"
    )
    out.append("")
    out.append("CANDIDATE CLUSTERS (grouped by earliest failing check, flow order):")
    for check, ids in sorted(s["by_earliest"].items(), key=lambda kv: rank(kv[0])):
        r = rank(check)
        if r >= CROSS_CUTTING_RANK:
            tag = "  [cross-cutting - can fail anywhere; not a location]"
        elif r >= TERMINAL_RANK:
            tag = "  [terminal - fails whenever anything upstream failed; split by stall point]"
        else:
            tag = ""
        out.append(f"  {len(ids):3d}  {check}{tag}")
        out.append(f"       {', '.join(ids)}")
    out.append("")
    if s["stall_states"]:
        out.append("STALL STATES (failing calls stuck 2+ turns in one state):")
        for st, n in s["stall_states"].most_common():
            out.append(f"  {n:3d}  {st}")
        out.append("")
    out.append("FLOW STEP AT END OF CALL (pass vs fail - a stall shows up here):")
    steps = sorted(
        set(s["pass_flow_steps"]) | set(s["fail_flow_steps"]),
        key=lambda x: (x is None, x),
    )
    out.append("  step   pass   fail")
    for st in steps:
        out.append(
            f"  {str(st):>4}   {s['pass_flow_steps'][st]:4d}   "
            f"{s['fail_flow_steps'][st]:4d}"
        )
    out.append("")
    out.append("FAILING CALLS:")
    for r in s["rows"]:
        out.append(
            f"  [{r['label']}] {r['name']}\n"
            f"      phase={r['phase']}\n"
            f"      earliest={r['earliest_failing_check']}"
            f"{' (cascade-only)' if r['cascade_only'] else ''}"
            f"  all={','.join(r['failing_checks'])}\n"
            f"      flow_step={r['flow_step']} selected_slot={r['selected_slot']} "
            f"end={r['end_reason']} turns={r['turns']}\n"
            f"      last_susie=\"{r['last_susie_turn']}\""
        )
        if r["stall_turns"] >= 2:
            out.append(
                f"      STALLED {r['stall_turns']} turns in {r['stall_state']} "
                f"(handled_by={r['stall_handler']})"
            )
        for line in r["trace_tail"]:
            out.append(f"        {line}")
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser(description="Collect Susie call failures.")
    ap.add_argument("target", nargs="?",
                    help="a results_*.json file or a directory of them "
                         "(omit when using --obs)")
    ap.add_argument("--obs", action="store_true",
                    help="read REAL calls from the observability store instead "
                         "of suite results. Needs OBS_DATABASE_URL; does not "
                         "need OBS_CAPTURE_ENABLED.")
    ap.add_argument("--clinic", help="obs only: filter to one clinic_id")
    ap.add_argument("--days", type=int,
                    help="obs only: look back this many days")
    ap.add_argument("--since", help="YYYY-MM-DD - only runs/calls on or after")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of text")
    args = ap.parse_args()

    if args.obs:
        sys.path.insert(0, str(Path(__file__).parent))
        from obs_source import load_obs_rows, to_triage_rows

        obs_rows = load_obs_rows(
            since=args.since, clinic=args.clinic, days=args.days
        )
        rows, total = to_triage_rows(obs_rows)
        if not rows:
            print(f"CALLS: {total} | no findings in this window.")
            return
        from obs_source import fleet_summary, missed_by_bottom_decile
        s = summarise_obs(rows, total)
        s["fleet"] = fleet_summary(obs_rows)
        s["missed_by_decile"] = missed_by_bottom_decile(obs_rows, rows)
        if args.json:
            for k in ("stall_states", "builds", "clinics",
                      "pass_flow_steps", "fail_flow_steps"):
                s[k] = dict(s[k])
            print(json.dumps(s, indent=2, default=str))
        else:
            print(render_obs(s))
        return

    if not args.target:
        ap.error("give a results path, or pass --obs to read the live store")

    records = load_records(Path(args.target), args.since)
    if not records:
        sys.exit(f"No usable call records read from {args.target}")
    s = summarise(records)
    if args.json:
        s["pass_flow_steps"] = dict(s["pass_flow_steps"])
        s["fail_flow_steps"] = dict(s["fail_flow_steps"])
        print(json.dumps(s, indent=2, default=str))
    else:
        print(render(s))


if __name__ == "__main__":
    main()
