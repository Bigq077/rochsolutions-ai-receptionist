"""Phase 1a: run the day-pick discrimination over every stored caller turn.

    python scripts/replay_day_picks.py                 # whole corpus
    python scripts/replay_day_picks.py --clinic northgate
    python scripts/replay_day_picks.py --show-accepts   # every ACCEPT, for eyeballing

WHAT THIS MEASURES, AND WHY IT IS THE RIGHT THING TO MEASURE
------------------------------------------------------------
`day_accepted_by_caller` (3b, 2026-09-03) decides whether a caller who names a
day has ACCEPTED it or ASKED ABOUT it. Both readings are common English and the
two outcomes are opposites:

    accepted -> "Monday it is —"                  (no lookup is coming)
    asked    -> "Let me see what Monday looks like —"  (a lookup IS coming)

Getting it backwards in the ASKED direction is the promised-work defect, which
this family has produced three times. So the harmful error is a REQUEST scored
as an acceptance, and this script's whole job is to surface every one of those
across real caller language rather than the handful of phrasings a test author
happens to think of.

WHAT IT CANNOT DO, STATED SO THE OUTPUT IS NOT OVER-READ
-------------------------------------------------------
obs stores SPEECH. `day_accepted_by_caller` also needs `last_offered_slots` and
`available_days`, and neither was stored before `calls.slot_offers` landed on
2026-09-03. So this cannot run the whole predicate over history -- it runs the
two REGEXES that carry the discrimination (`_DAY_ACCEPT_RE`, `_DAY_REQUEST_RE`)
plus the clock-time test, which is the half that decides accept-vs-request. The
day-resolution half needs the offer and is covered by the generated sweep
instead (`scripts/sweep_slot_offer.py`).

That split is the honest one: this answers "does the language discrimination
hold up against real callers", the sweep answers "does the resolution hold up
against real diaries", and neither pretends to be the other.

A turn counts only when the PREVIOUS assistant turn read out numbered options,
because that is the only context in which the predicate is consulted.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.tools.slot_followup import (  # noqa: E402
    _DAY_ACCEPT_RE,
    _DAY_REQUEST_RE,
    _clock_time_named,
    utterance_requests_different_day,
    utterance_requests_more_slots,
)

_DAY = re.compile(r"\b(?:mon|tues|wednes|thurs|fri|satur|sun)day\b", re.I)
#: An assistant turn that read out numbered options. The only context in which
#: the predicate is ever asked.
_READOUT = re.compile(r"\bnumber\s+\d\b|\bany of those\b|\bwhich suits\b", re.I)


def verdict(text: str) -> str:
    """The same ladder `day_accepted_by_caller` walks, minus day resolution."""
    if utterance_requests_more_slots(text) or utterance_requests_different_day(text):
        return "other-path"
    if "?" in text or _DAY_REQUEST_RE.search(text):
        return "request"
    if not _DAY_ACCEPT_RE.search(text):
        return "no-accept-word"
    if _clock_time_named(text):
        return "has-a-time"     # slot_accepted_by_caller owns these
    return "ACCEPT"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--clinic")
    ap.add_argument("--show-accepts", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    try:
        from dotenv import load_dotenv
        load_dotenv()
    except Exception:
        pass
    url = os.getenv("OBS_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not url:
        print("OBS_DATABASE_URL is not set", file=sys.stderr)
        return 2

    from sqlalchemy import create_engine, text as _sql

    engine = create_engine(url)
    sql = "select call_sid, clinic_id, transcript from calls where transcript is not null"
    params = {}
    if args.clinic:
        sql += " and clinic_id = :c"
        params["c"] = args.clinic
    sql += " order by created_at desc"
    if args.limit:
        sql += f" limit {int(args.limit)}"

    counts, accepts, calls = {}, [], 0
    with engine.connect() as conn:
        for sid, clinic, tr in conn.execute(_sql(sql), params):
            if not tr:
                continue
            turns = tr if isinstance(tr, list) else json.loads(tr)
            calls += 1
            after_readout = False
            for turn in turns:
                if not isinstance(turn, dict):
                    continue
                role = (turn.get("role") or "").lower()
                body = turn.get("text") or ""
                if role != "user":
                    if _READOUT.search(body):
                        after_readout = True
                    continue
                if not after_readout or not _DAY.search(body):
                    continue
                v = verdict(body)
                counts[v] = counts.get(v, 0) + 1
                if v == "ACCEPT":
                    accepts.append((sid, clinic, body.strip()))

    print(f"{calls} calls, day-naming caller turns AFTER a numbered readout\n")
    total = sum(counts.values()) or 1
    for k in sorted(counts, key=lambda x: -counts[x]):
        print(f"  {k:16s} {counts[k]:5d}  {100*counts[k]/total:5.1f}%")
    print(f"  {'TOTAL':16s} {total:5d}")

    print("\nEvery turn scored ACCEPT is listed below. A REQUEST appearing here")
    print("is the harmful error -- it would put \"Monday it is —\" in front of a")
    print("lookup that really is happening.\n")
    if args.show_accepts or len(accepts) <= 40:
        for sid, clinic, body in accepts:
            print(f"  [{clinic}] {sid[:12]}  {body[:96]!r}")
    else:
        for sid, clinic, body in accepts[:40]:
            print(f"  [{clinic}] {sid[:12]}  {body[:96]!r}")
        print(f"  ... {len(accepts) - 40} more (--show-accepts for all)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
