"""Replay `choose_presented_indices` over the stored offer corpus.

    python scripts/replay_presented_times.py --out BASE.json     # on the base tree
    python scripts/replay_presented_times.py --out CAND.json     # on the candidate
    python scripts/replay_presented_times.py --diff BASE.json CAND.json

WHY THIS EXISTS
---------------
`replay_slot_decisions.py` is the harness for slot decisions, and it says so
itself: obs stores SPEECH, not payloads, so `remaining_unspoken_on_current_day`,
`choose_presented_indices` and `all_remaining_on_next_day` are NOT covered by
it. A change to the readout selection can therefore pass that replay with
`CHANGED: 0` while altering every readout on every clinic -- which is exactly
what a T1-shaped change does.

`calls.slot_offers` (forward-only from 3 Sep, 120 calls) stores the missing
half: the availability payload AND the offer built from it, per lookup, in
sequence. That is enough to re-run the selection itself.

WHAT IT REPLAYS
---------------
For each stored lookup, in `seq` order within a call:

  * the session is rebuilt from what the caller has ACTUALLY BEEN READ so far
    -- `record_spoken_slots` over the earlier offers' own slots, the real
    writer, not a reimplementation of it;
  * `available_days` is set to the PREVIOUS lookup's payload, because that is
    the state the builders call this in (see `spoken_starts_for_offer`);
  * `choose_presented_indices` is then run against each day of this lookup's
    payload, at the limit the live cap actually used on that day.

BUILT IS NOT SPOKEN
-------------------
`record_offer` is called where the deterministic offer is BUILT
(`llm_stream.py:7353`), not where it is spoken -- and two stand-down branches
below it, P6 and P6b, discard that offer and speak the model instead. Measured
over this corpus: **10 of 77 recorded offers (13%) were never said out loud.**

Feeding those to `record_spoken_slots` would tell the replay the caller had
heard times they never did, which is the one thing that would make a
"repeated clock times" measurement lie. So each offer's first chunk is checked
against the call's assistant transcript, and an offer that does not appear
there contributes nothing to the heard set. `skipped_unspoken` reports how many.

WHAT IT CANNOT SEE, STATED SO THE OUTPUT IS NOT OVER-READ
---------------------------------------------------------
`_requested_clock_times` is not stored, so D8's pin cannot fire in replay. On a
turn where it fired live, the replayed base selection will not match the stored
one -- `matched_stored` counts that and it is a health metric, never a gate.
The band filters, `day_preference` and the accepted-slot pin are likewise not
reconstructable from this column; days they touched are reported, not asserted
on.

THE ASSERTIONS THAT ARE GATES
-----------------------------
  lost_a_slot        a day offering FEWER times after the change      MUST be 0
  invented_a_slot    a time not on that day's payload                 MUST be 0
  re_offered_a_heard_time  a time already read out ON THAT DAY        MUST be 0

`changed_a_heard_day` WAS the third gate, and it was retired deliberately on
10 Sep 2026 rather than found stale. It asserted that a day the caller had
already been read must never change, which was T1's stand-down restated as an
invariant. S-2 superseded that decision: a multi-day readout makes every day it
named a heard day, so the stand-down silenced the cross-day preference for the
rest of the call, and "twenty to ten" was offered for Monday and again for
Tuesday seventeen seconds apart (CA8214b75c).

What that gate was really protecting is B-116's pool, and THAT is what the
replacement asserts directly: whatever the selection does on a heard day, it
may never hand back a time the caller was already read on that day. The old
count is still printed, because a large swing in it is worth seeing -- it is
just no longer a failure.

No PII: a slot is a date and a time, and nothing else is read.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

from app.tools.slot_followup import (  # noqa: E402
    choose_presented_indices,
    record_spoken_slots,
)


def _as_list(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except Exception:
            return []
    return value if isinstance(value, list) else []


def _load(clinic=None, limit=None):
    from sqlalchemy import create_engine, text as sql

    engine = create_engine(os.environ["OBS_DATABASE_URL"])
    query = (
        "select call_sid, clinic_id, slot_offers, transcript from calls "
        "where slot_offers is not null order by start_utc, call_sid"
    )
    with engine.connect() as conn:
        rows = conn.execute(sql(query)).fetchall()
    if clinic:
        rows = [r for r in rows if (r[1] or "") == clinic]
    return rows[: int(limit)] if limit else rows


def _norm(text):
    return re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).strip()


def _assistant_speech(transcript):
    """Everything Susie actually said on this call, normalised, as one string."""
    return " || ".join(
        _norm(t.get("text")) for t in _as_list(transcript)
        if isinstance(t, dict) and (t.get("role") or "") == "assistant"
    )


def _was_spoken(offer_record, speech):
    """Did this BUILT offer actually reach the caller?

    `record_offer` fires where the offer is built (llm_stream.py:7353); P6 and
    P6b below it can still stand it down and speak the model instead. 10 of 77
    recorded offers in this corpus were never said. An offer whose opening
    chunk is nowhere in the transcript did not happen, and must not be counted
    as heard.
    """
    chunks = ((offer_record or {}).get("offer") or {}).get("chunks") or []
    if not chunks:
        return False
    first = _norm(chunks[0])
    return bool(first) and first in speech


def _spoken_slots_of(offer_record):
    """The slots this lookup's offer actually READ OUT, as the writer wants them."""
    offer = (offer_record or {}).get("offer") or {}
    slots = offer.get("slots")
    if not isinstance(slots, list):
        return []
    return [{"start": s.get("start")} for s in slots if isinstance(s, dict) and s.get("start")]


def _presented_count_per_date(offer_record):
    """How many times the live cap spoke, per date. The limit to replay at."""
    counts = {}
    for slot in _spoken_slots_of(offer_record):
        counts[str(slot["start"])[:10]] = counts.get(str(slot["start"])[:10], 0) + 1
    return counts


def _stored_clocks_per_date(offer_record):
    out = {}
    for slot in _spoken_slots_of(offer_record):
        out.setdefault(str(slot["start"])[:10], []).append(str(slot["start"])[11:16])
    return out


def collect(rows):
    """One record per (call, lookup, day). Pure replay -- no engine, no network."""
    out = []
    skipped_unspoken = 0
    for sid, clinic, slot_offers, transcript in rows:
        speech = _assistant_speech(transcript)
        offers = _as_list(slot_offers)
        if not offers:
            continue
        offers = sorted(
            [o for o in offers if isinstance(o, dict)],
            key=lambda o: int(o.get("seq") or 0),
        )
        for k, record in enumerate(offers):
            payload = record.get("payload")
            if not isinstance(payload, list) or not payload:
                continue
            # The session as it stood when this lookup ran.
            session = {}
            for earlier in offers[:k]:
                if not _was_spoken(earlier, speech):
                    continue
                spoken = _spoken_slots_of(earlier)
                if spoken:
                    session["available_days"] = earlier.get("payload") or []
                    record_spoken_slots(session, spoken)
            session["available_days"] = (
                offers[k - 1].get("payload") or [] if k else []
            )
            heard_dates = {
                str(s.get("start"))[:10]
                for earlier in offers[:k] if _was_spoken(earlier, speech)
                for s in _spoken_slots_of(earlier)
            }
            limits = _presented_count_per_date(record)
            stored = _stored_clocks_per_date(record)
            for day in payload:
                if not isinstance(day, dict):
                    continue
                date = str(day.get("date") or "")[:10]
                times = day.get("slot_times")
                if not date or not isinstance(times, list) or not times:
                    continue
                limit = limits.get(date) or min(3, len(times))
                # The stored payload carries no `slots` array; the pure
                # functions read `start`, so it is reconstructed from the
                # date and the times, which is what built it.
                probe = dict(day)
                probe["slots"] = [
                    {"start": f"{date}T{t}:00", "end": f"{date}T{t}:00"} for t in times
                ]
                try:
                    idx = choose_presented_indices(session, probe, limit)
                except Exception as exc:      # a readout preference must not raise
                    out.append({
                        "call": sid, "clinic": clinic, "seq": k, "date": date,
                        "error": f"{type(exc).__name__}: {exc}",
                    })
                    continue
                out.append({
                    "call": sid,
                    "clinic": clinic,
                    "seq": k,
                    "date": date,
                    "limit": limit,
                    "day_times": list(times),
                    "chosen": [times[i] for i in idx if 0 <= i < len(times)],
                    "heard_day": date in heard_dates,
                    "any_heard": bool(heard_dates),
                    "heard_clocks": sorted({
                        str(s.get("start"))[11:16]
                        for earlier in offers[:k] if _was_spoken(earlier, speech)
                        for s in _spoken_slots_of(earlier)
                    }),
                    # B-116's own subtraction, per day. The gate below is the
                    # only thing standing between a heard-day selection and
                    # re-offering a time the caller has already turned down.
                    "heard_clocks_this_day": sorted({
                        str(s.get("start"))[11:16]
                        for earlier in offers[:k] if _was_spoken(earlier, speech)
                        for s in _spoken_slots_of(earlier)
                        if str(s.get("start"))[:10] == date
                    }),
                    "stored": stored.get(date, []),
                })
        skipped_unspoken += sum(
            1 for o in offers if not _was_spoken(o, speech)
        )
    if skipped_unspoken:
        print(f"skipped_unspoken        {skipped_unspoken}   "
              f"offers built but never said (P6/P6b stood them down)")
    return out


def _key(rec):
    return (rec.get("call"), rec.get("seq"), rec.get("date"))


def diff(base_path, cand_path):
    base = {_key(r): r for r in json.load(open(base_path))}
    cand = {_key(r): r for r in json.load(open(cand_path))}
    shared = sorted(set(base) & set(cand))
    print(f"baseline days: {len(base)}   candidate days: {len(cand)}   shared: {len(shared)}")
    if set(base) ^ set(cand):
        print(f"!! {len(set(base) ^ set(cand))} day(s) present on only one side")

    changed = lost = invented = heard_day_changed = re_offered = 0
    repeats_before = repeats_after = cross_day_days = 0
    heard_days = 0
    heard_repeats_before = heard_repeats_after = 0
    examples = []
    for key in shared:
        b, c = base[key], cand[key]
        if b.get("error") or c.get("error"):
            continue
        day_times = set(b.get("day_times") or [])
        bc, cc = b.get("chosen") or [], c.get("chosen") or []
        heard = set(b.get("heard_clocks") or [])
        # The improvement measure, over the days the change can reach: a fresh
        # day, on a call where something has already been read out.
        if b.get("any_heard") and not b.get("heard_day"):
            cross_day_days += 1
            repeats_before += len(set(bc) & heard)
            repeats_after += len(set(cc) & heard)
        # S-2's own population: a day the caller HAS heard, on a call where
        # something was read out on another day too. This is the half the old
        # gate made unmeasurable by forbidding it.
        if b.get("heard_day"):
            heard_days += 1
            heard_repeats_before += len(set(bc) & heard)
            heard_repeats_after += len(set(cc) & heard)
        # The gate. Independent of whether the day changed: a selection that
        # re-offers a time already read out on that day is wrong either way.
        #
        # ...UNLESS the day cannot fill the readout without it. B-116 returns
        # every slot on a day holding `limit` or fewer, and B-119 settled that
        # withholding is right only while something unheard remains. On
        # CAffe1e08713631e5178c6fe73f44e4037/2026-09-09 the day held exactly
        # two times and one had been heard, so both base and candidate offered
        # it -- correctly. Gating on the un-narrowed set would have failed a
        # rule this file is supposed to be defending.
        own = set(c.get("heard_clocks_this_day") or [])
        spare = set(c.get("day_times") or []) - own
        if own & set(cc) and len(spare) >= (c.get("limit") or 0):
            re_offered += 1
        if bc == cc:
            continue
        changed += 1
        if len(cc) < len(bc):
            lost += 1
        if not set(cc) <= day_times:
            invented += 1
        if b.get("heard_day"):
            heard_day_changed += 1
        if len(examples) < 12:
            examples.append((key, b.get("clinic"), bc, cc, sorted(heard)))

    print()
    print(f"{'days changed':<26}{changed}")
    print(f"{'  lost a slot':<26}{lost}          MUST be 0")
    print(f"{'  invented a slot':<26}{invented}          MUST be 0")
    print(f"{'re-offered a heard time':<26}{re_offered}          MUST be 0")
    print(f"{'  on a day already heard':<26}{heard_day_changed}          (reported, not a gate -- see the header)")
    print()
    print(f"{'cross-day readouts':<26}{cross_day_days}")
    print(f"{'  repeated clock times':<26}{repeats_before} -> {repeats_after}")
    print(f"{'heard-day readouts':<26}{heard_days}")
    print(f"{'  repeated clock times':<26}{heard_repeats_before} -> {heard_repeats_after}")
    print()
    for (sid, seq, date), clinic, bc, cc, heard in examples:
        print(f"  {sid[:14]} seq={seq} {date} {clinic}")
        print(f"      heard {heard}")
        print(f"      base  {bc}")
        print(f"      cand  {cc}")
    return 0 if (lost == 0 and invented == 0 and re_offered == 0) else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out")
    ap.add_argument("--clinic")
    ap.add_argument("--limit")
    ap.add_argument("--diff", nargs=2, metavar=("BASE", "CAND"))
    args = ap.parse_args()

    if args.diff:
        return diff(*args.diff)

    rows = _load(args.clinic, args.limit)
    records = collect(rows)
    errors = [r for r in records if r.get("error")]
    fresh = [r for r in records if r.get("any_heard") and not r.get("heard_day")]
    matched = [
        r for r in records
        if r.get("stored") and r.get("chosen") == r.get("stored")
    ]
    print(f"{'calls':<24}{len(rows)}")
    print(f"{'days replayed':<24}{len(records)}")
    print(f"{'  on a fresh day':<24}{len(fresh)}")
    print(f"{'  matched the stored offer':<24}{len(matched)}   (health, not a gate)")
    print(f"{'  raised':<24}{len(errors)}")
    for e in errors[:5]:
        print("   ", e["call"][:14], e["date"], e["error"])
    if args.out:
        with open(args.out, "w") as handle:
            json.dump(records, handle)
        print(f"\nwrote {args.out} ({len(records)} days)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
