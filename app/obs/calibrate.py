"""
app/obs/calibrate.py
--------------------
Judge calibration harness (spec §5.3).

    python -m app.obs.calibrate [calibration/labels.csv]

Runs the judge over a set of hand-labelled real calls and reports agreement
between the judge's quality_score and the human score:
  - exact agreement  (judge == human)
  - within-1 agreement (|judge - human| <= 1)

Do NOT trust the judge's scores until this agreement is measured and acceptable.
Re-run whenever judge.RUBRIC_VERSION changes.

The labels file references call_sids only — never commit real names, numbers, or
clinical detail (spec §7). Each labelled call is loaded from the durable store
(Phase 1), so this requires DATABASE_URL + OBS_JUDGE_ENABLED + ANTHROPIC_API_KEY.
"""
from __future__ import annotations

import asyncio
import csv
import sys
from pathlib import Path
from typing import List, Optional, Tuple

from app import config
from app.obs import judge, store

_DEFAULT_LABELS = Path("calibration/labels.csv")


def _read_labels(path: Path) -> List[Tuple[str, int, str]]:
    rows: List[Tuple[str, int, str]] = []
    with path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            sid = (row.get("call_sid") or "").strip()
            raw = (row.get("human_score") or "").strip()
            if not sid or sid.startswith("#") or not raw:
                continue
            try:
                rows.append((sid, int(raw), (row.get("notes") or "").strip()))
            except ValueError:
                continue
    return rows


async def _judge_score(call_sid: str) -> Optional[int]:
    call = store.get_call(call_sid)
    if call is None:
        return None
    # Reuse a stored judgement if present, else judge now.
    if call.get("quality_score") is not None and call.get("rubric_version") == judge.RUBRIC_VERSION:
        return call["quality_score"]
    judgement = await judge.judge_call(call)
    return judgement.get("quality_score") if judgement else None


async def run(path: Path) -> int:
    labels = _read_labels(path)
    if not labels:
        print(f"No usable labels in {path} (need call_sid,human_score rows).",
              file=sys.stderr)
        return 1

    exact = within1 = scored = 0
    print(f"{'call_sid':<24} {'human':>5} {'judge':>5} {'Δ':>3}  notes")
    print("-" * 72)
    for sid, human, notes in labels:
        j = await _judge_score(sid)
        if j is None:
            print(f"{sid:<24} {human:>5} {'—':>5} {'?':>3}  (not scored: missing call/judge)")
            continue
        scored += 1
        delta = abs(j - human)
        if delta == 0:
            exact += 1
        if delta <= 1:
            within1 += 1
        print(f"{sid:<24} {human:>5} {j:>5} {delta:>3}  {notes}")

    print("-" * 72)
    if scored:
        print(f"Scored {scored}/{len(labels)} labelled calls "
              f"(rubric {judge.RUBRIC_VERSION})")
        print(f"Exact agreement : {exact}/{scored} = {exact / scored:.0%}")
        print(f"Within-1        : {within1}/{scored} = {within1 / scored:.0%}")
    else:
        print("No calls could be scored — check DATABASE_URL and that the "
              "labelled call_sids exist in the store.")
        return 1
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    path = Path(argv[0]) if argv else _DEFAULT_LABELS

    if not config.DATABASE_URL:
        print("ERROR: DATABASE_URL not set — no store to calibrate against.", file=sys.stderr)
        return 2
    if not judge.is_enabled():
        print("ERROR: judge disabled — set OBS_JUDGE_ENABLED=true and ANTHROPIC_API_KEY.",
              file=sys.stderr)
        return 2
    if not path.exists():
        print(f"ERROR: labels file not found: {path}", file=sys.stderr)
        return 2

    return asyncio.run(run(path))


if __name__ == "__main__":
    raise SystemExit(main())
