"""Does a multi-day readout say the same clock time on every day it names?

    python scripts/replay_multi_day_spread.py [--clinic northgate]

WHY THIS EXISTS
---------------
`replay_presented_times.py` calls `choose_presented_indices` one day at a time,
which is how the T1 fix was verified — and on 10 Sep 2026 that verification
passed while the live readout was still broken. CAfb09f66e read "eight in the
morning" for Monday, Tuesday AND Wednesday in a single sentence, then twice
more when the caller asked about two of those days by name. Judge 2, abandoned.

The defect was never in the per-day rule. It was in the LOOP around it:
`_cap_presented_slots` picks every day against the same spoken record, which is
empty on a first lookup, so each day independently chose position 0 — and on a
uniform grid position 0 is the same clock time on every day of the week.

A harness that calls the inner function cannot see that. This one drives
`_cap_presented_slots` itself, over the payloads the corpus actually returned.

WHAT IT REPORTS
---------------
Per stored multi-day lookup, the clock times the loop would speak on each day,
and whether any is repeated across them.

    repeats_a_clock_time    readouts naming one time on two or more days
    identical_openers       readouts where every day starts at the same time

Both should be 0 on a grid clinic. Neither is a hard gate on a clinic whose
diary genuinely offers one time a day — a two-slot Thursday rota cannot vary —
so the per-clinic split is printed rather than a single pass/fail.

No PII: a slot is a date and a time.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

from app.tools.receptionist_tools import _cap_presented_slots  # noqa: E402


def _as_list(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except Exception:
            return []
    return value if isinstance(value, list) else []


def _load(clinic=None):
    from sqlalchemy import create_engine, text as sql

    engine = create_engine(os.environ["OBS_DATABASE_URL"])
    with engine.connect() as conn:
        rows = conn.execute(sql(
            "select call_sid, clinic_id, slot_offers from calls "
            "where slot_offers is not null order by start_utc, call_sid"
        )).fetchall()
    return [r for r in rows if not clinic or (r[1] or "") == clinic]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--clinic")
    ap.add_argument("--show", action="store_true", help="print every repeat")
    args = ap.parse_args()

    stats = defaultdict(lambda: defaultdict(int))
    examples = []
    for sid, clinic, slot_offers in _load(args.clinic):
        for record in _as_list(slot_offers):
            if not isinstance(record, dict):
                continue
            payload = record.get("payload")
            if not isinstance(payload, list) or len(payload) < 2:
                continue          # a one-day payload cannot repeat across days
            # `record_offer` trims the payload to the fields the pure
            # functions read, and `slots` is not one of them -- but the
            # selection reads `start` off it. Rebuilt from the date and the
            # times, which is what built it in the first place.
            rebuilt = []
            for d in payload:
                if not isinstance(d, dict):
                    continue
                date = str(d.get("date") or "")[:10]
                times = d.get("slot_times")
                if not date or not isinstance(times, list) or not times:
                    continue
                probe = dict(d)
                probe["slots"] = [
                    {"start": f"{date}T{t}:00", "end": f"{date}T{t}:00"}
                    for t in times
                ]
                rebuilt.append(probe)
            if len(rebuilt) < 2:
                continue
            try:
                capped = _cap_presented_slots(
                    {"available_days": rebuilt}, {}, 3, "multi_day",
                )
            except Exception as exc:
                stats[clinic]["raised"] += 1
                if args.show:
                    print(f"  {sid[:14]} {type(exc).__name__}: {exc}")
                continue
            days = capped.get("presented_days") or []
            spoken = [list(d.get("slot_times") or []) for d in days]
            spoken = [times for times in spoken if times]
            if len(spoken) < 2:
                continue
            stats[clinic]["readouts"] += 1
            flat = [t for times in spoken for t in times]
            if len(flat) != len(set(flat)):
                stats[clinic]["repeats_a_clock_time"] += 1
                if len(examples) < 10:
                    examples.append((sid, clinic, spoken))
            if len({times[0] for times in spoken}) == 1:
                stats[clinic]["identical_openers"] += 1

    print(f"  {'clinic':<14}{'readouts':>10}{'repeats':>10}{'same opener':>13}{'raised':>8}")
    total = defaultdict(int)
    for clinic in sorted(stats, key=lambda c: -stats[c]["readouts"]):
        s = stats[clinic]
        for k, v in s.items():
            total[k] += v
        print(f"  {clinic:<14}{s['readouts']:>10}{s['repeats_a_clock_time']:>10}"
              f"{s['identical_openers']:>13}{s['raised']:>8}")
    print(f"  {'TOTAL':<14}{total['readouts']:>10}{total['repeats_a_clock_time']:>10}"
          f"{total['identical_openers']:>13}{total['raised']:>8}")

    if examples:
        print("\nreadouts naming one clock time on more than one day")
        for sid, clinic, spoken in examples:
            print(f"  {sid[:14]} {clinic}")
            for times in spoken:
                print(f"      {times}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
