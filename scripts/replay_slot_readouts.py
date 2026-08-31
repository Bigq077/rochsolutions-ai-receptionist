"""Corpus study: how often does the system fail to know what it just offered?

Step 2 of docs/plan/DETERMINISTIC_SLOT_PRESENTATION.md.

The obs store keeps transcripts, not availability payloads, so a payload-in /
sentence-out replay of `build_slot_offer` is not possible from it. What IS
possible is the measurement that actually decides the plan: run the REAL spoken
readouts through the reverse-parsing layer and count how often it cannot tell
what was said.

Four things are counted, all from bot speech only:

  1. READOUTS          — assistant turns that present numbered slot options.
  2. UNRESOLVABLE      — readouts whose options carry MORE THAN ONE time each
                         ("Monday 7th — ten in the morning or five in the
                         evening"). resolve_spoken_options is all-or-nothing per
                         option, so these record NOTHING. Measured directly on
                         31 Aug: both Theorem calls logged "could not resolve
                         spoken option(s)".
  3. CONTRADICTIONS    — one call making two or more completeness claims about
                         the SAME day with DIFFERENT time sets. This is the
                         CA7e3ccfd4 defect: "The available slots for Wednesday
                         9th September are" said three times, naming three
                         disjoint sets.
  4. ORPHAN TAILS      — "a few others THAT DAY" after a multi-day readout,
                         where "that day" names nothing (B-99).

No PII is printed: only bot sentences, and only the day/time fragments matched.

    python scripts/replay_slot_readouts.py [--clinic theorem_v3] [--limit N]
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

from sqlalchemy import create_engine, text  # noqa: E402

from app.tools.slot_followup import option_label_candidates  # noqa: E402

# "nine in the morning", "half past four", "midday", "quarter to five"
TIME_RE = re.compile(
    r"\b(?:midday|noon|midnight|"
    r"(?:half past|quarter past|quarter to)\s+\w+|"
    r"\w+(?:\s+\w+)?\s+in the (?:morning|afternoon|evening))\b",
    re.I,
)
DAY_RE = re.compile(
    r"\b(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\b"
    r"[^,.—-]{0,24}",
    re.I,
)
NUMBERED_RE = re.compile(r"Number\s+\d", re.I)
COMPLETENESS_RE = re.compile(
    r"(?:the available slots for|the slots? (?:I have|we have) (?:on|for)|"
    r"the earliest I have is)\s+([^—,.-]{3,40})",
    re.I,
)
THAT_DAY_TAIL_RE = re.compile(
    r"(?:a few|a couple|some|several|others?)[^.]{0,40}\bthat day\b", re.I
)


def bot_turns(transcript):
    for turn in transcript or []:
        if not isinstance(turn, dict):
            continue
        role = str(turn.get("role") or "").lower()
        if role in ("assistant", "bot", "susie", "agent"):
            yield str(turn.get("text") or "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--clinic")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    engine = create_engine(os.environ["OBS_DATABASE_URL"])
    q = "select call_sid, clinic_id, transcript from calls where transcript is not null"
    if args.clinic:
        q += " and clinic_id = :c"
    q += " order by start_utc"
    if args.limit:
        q += " limit {}".format(int(args.limit))

    stats = defaultdict(int)
    per_clinic = defaultdict(lambda: defaultdict(int))
    contradiction_examples = []
    unresolvable_examples = []

    with engine.connect() as conn:
        rows = conn.execute(
            text(q), {"c": args.clinic} if args.clinic else {}
        ).fetchall()

    for call_sid, clinic_id, transcript in rows:
        stats["calls"] += 1
        per_clinic[clinic_id]["calls"] += 1
        claims = defaultdict(list)          # day -> [frozenset(times)]
        call_had_readout = False

        for said in bot_turns(transcript):
            if not NUMBERED_RE.search(said):
                # Still track completeness claims made without numbering.
                pass
            else:
                call_had_readout = True
                stats["readouts"] += 1
                per_clinic[clinic_id]["readouts"] += 1
                options = option_label_candidates(said)
                multi = 0
                for label in options.values():
                    head = label[0] if isinstance(label, (list, tuple)) else label
                    if len(TIME_RE.findall(str(head))) > 1:
                        multi += 1
                if multi:
                    stats["unresolvable_readouts"] += 1
                    per_clinic[clinic_id]["unresolvable_readouts"] += 1
                    if len(unresolvable_examples) < 5:
                        unresolvable_examples.append(said[:150])
                # B-99: a "that day" tail after a readout naming 2+ days
                days_named = {d.split()[0].lower() for d in DAY_RE.findall(said)}
                if len(days_named) > 1 and THAT_DAY_TAIL_RE.search(said):
                    stats["orphan_that_day_tails"] += 1
                    per_clinic[clinic_id]["orphan_that_day_tails"] += 1

            for m in COMPLETENESS_RE.finditer(said):
                day = m.group(1).strip().lower()
                times = frozenset(t.lower() for t in TIME_RE.findall(said))
                if times:
                    claims[day].append(times)

        for day, sets in claims.items():
            distinct = {s for s in sets}
            if len(distinct) > 1:
                stats["calls_contradicting_themselves"] += 1
                per_clinic[clinic_id]["calls_contradicting_themselves"] += 1
                if len(contradiction_examples) < 6:
                    contradiction_examples.append(
                        (call_sid[:12], clinic_id, day,
                         [sorted(s) for s in sets])
                    )
                break
        if call_had_readout:
            stats["calls_with_a_readout"] += 1
            per_clinic[clinic_id]["calls_with_a_readout"] += 1

    def pct(a, b):
        return "{:5.1f}%".format(100.0 * a / b) if b else "    - "

    print("=" * 74)
    print("CORPUS: {} calls".format(stats["calls"]))
    print("=" * 74)
    print("calls containing a numbered slot readout : {:5d}  {}".format(
        stats["calls_with_a_readout"], pct(stats["calls_with_a_readout"], stats["calls"])))
    print("numbered readouts total                  : {:5d}".format(stats["readouts"]))
    print()
    print("readouts the reverse-parse CANNOT resolve : {:5d}  {}".format(
        stats["unresolvable_readouts"], pct(stats["unresolvable_readouts"], stats["readouts"])))
    print("  (option carries 2+ times, so resolve_spoken_options records nothing)")
    print()
    print("calls that contradict themselves on a day : {:5d}  {}".format(
        stats["calls_contradicting_themselves"],
        pct(stats["calls_contradicting_themselves"], stats["calls_with_a_readout"])))
    print("  (2+ completeness claims, same day, different time sets)")
    print()
    print("orphan 'that day' tails after a multi-day : {:5d}".format(
        stats["orphan_that_day_tails"]))
    print()
    print("-" * 74)
    print("{:<14}{:>7}{:>10}{:>14}{:>14}".format(
        "clinic", "calls", "readouts", "unresolvable", "contradicts"))
    for clinic, s in sorted(per_clinic.items(), key=lambda x: -x[1]["calls"]):
        print("{:<14}{:>7}{:>10}{:>14}{:>14}".format(
            str(clinic), s["calls"], s["readouts"],
            s["unresolvable_readouts"], s["calls_contradicting_themselves"]))

    if unresolvable_examples:
        print()
        print("-- unresolvable readouts (bot speech) " + "-" * 35)
        for ex in unresolvable_examples:
            print("   " + ex)
    if contradiction_examples:
        print()
        print("-- self-contradicting calls " + "-" * 45)
        for sid, clinic, day, sets in contradiction_examples:
            print("   {} {}  day={!r}".format(sid, clinic, day))
            for s in sets:
                print("        {}".format(s))


if __name__ == "__main__":
    main()
