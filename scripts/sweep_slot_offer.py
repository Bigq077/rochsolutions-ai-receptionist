"""Property sweep over the slot layer: generated diaries x generated callers.

Phase 1b of docs/plan/SLOT_PRESENTATION_CONVERGENCE.md.

WHY A GENERATOR AND NOT A REPLAY
--------------------------------
Phase 1's original design was a replay over the stored corpus, and four of its
five checks cannot run: obs stored transcripts and not availability payloads
until `calls.slot_offers` landed on 2026-09-03, so there is nothing historical
to feed the payload-taking predicates. That column is forward-only.

But `build_slot_offer`, `slot_accepted_by_caller`, `day_accepted_by_caller` and
`choose_presented_indices` are PURE. They do not need historical payloads, they
need representative ones -- and a clinic diary has finite structure. So this
generates the payload space instead of waiting for it, which is strictly
stronger than replay: it covers shapes the corpus never happened to contain.

Every defect the week to 2026-09-03 cost a phone call to find was of that kind:

  * "monday at 8 pm" resolving to 08:00 -- needs a day holding an 8 and a
    caller naming a meridiem. Two calls in the corpus had that shape, both
    after the defect shipped.
  * "monday at 10 in the morning" resolving to 08:00 -- needs a day whose only
    morning slot is not the one named.
  * a day offering BOTH 08:00 and 20:00, where the meridiem is the only thing
    separating two labels that fold to the same digit. **The corpus contains no
    such day at all**, and the first version of the fix was wrong on it.

The last one is the argument in one line: no amount of replay finds a defect in
a shape your clinics have never rostered.

WHAT IT CHECKS
--------------
Invariants, not expected strings. A sentence is allowed to change; these are
the things that must be true of every offer whatever the wording:

  1. RECORD vs SENTENCE   every slot in `slots` is named in the speech
  2. MAP vs RECORD        every keypad value names a slot in `slots`
  3. NO INVENTION         every slot offered exists in the payload
  4. MERIDIEM             an acceptance never resolves to a slot whose hour
                          contradicts a meridiem the caller stated
  5. UNSPOKEN             an acceptance never resolves to a slot never spoken
  6. REQUESTS             a request-shaped utterance never resolves as a pick
  7. MORE-TIMES           `more_times` is only claimed when the payload really
                          holds times that were not named

    python scripts/sweep_slot_offer.py            # full sweep, report
    python scripts/sweep_slot_offer.py --quick    # bounded, for CI

Exits non-zero on any violation.
"""
from __future__ import annotations

import argparse
import itertools
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.tools.slot_offer import build_slot_offer  # noqa: E402
from app.tools.slot_followup import (  # noqa: E402
    day_accepted_by_caller,
    record_spoken_slots,
    slot_accepted_by_caller,
)

# ── The diary space ─────────────────────────────────────────────────────────
# Real rosters, not random times. Each entry is one day's bookable times.
DAY_SHAPES = {
    "full_day":       ["08:00", "08:50", "09:40", "10:30", "11:20", "12:10",
                       "13:00", "13:50", "14:40", "15:30", "16:20", "17:10"],
    "morning_only":   ["08:00", "09:00", "10:00", "11:00"],
    "afternoon_only": ["13:00", "14:00", "15:00", "16:00"],
    # The shape the corpus has never contained, and the one that broke the
    # first version of the meridiem guard: two labels folding to the same digit.
    "am_and_pm_8":    ["08:00", "20:00"],
    "evening_rota":   ["17:00", "18:00", "19:00", "20:00"],
    "single_slot":    ["09:10"],
    "two_slots":      ["08:00", "17:10"],
    "lunchtime_gap":  ["09:00", "10:00", "15:00", "16:00"],
}

DATES = ["2026-09-07", "2026-09-08", "2026-09-09"]
LABELS = {
    "2026-09-07": "Monday 7th September",
    "2026-09-08": "Tuesday 8th September",
    "2026-09-09": "Wednesday 9th September",
}

_HOUR_WORDS = {
    0: "midnight", 1: "one", 2: "two", 3: "three", 4: "four", 5: "five",
    6: "six", 7: "seven", 8: "eight", 9: "nine", 10: "ten", 11: "eleven",
    12: "midday",
}
_MIN_WORDS = {
    0: "", 10: "ten past ", 15: "quarter past ", 20: "twenty past ",
    30: "half past ", 40: "twenty to ", 45: "quarter to ", 50: "ten to ",
}


def spoken_label(t: str) -> str:
    """The clinic's own spoken form for a 24h time. Mirrors the payload."""
    h, m = int(t[:2]), int(t[3:5])
    if m in (40, 45, 50):                      # "ten to nine"
        base = _HOUR_WORDS[(h + 1) % 12 or 12]
        band_h = h + 1
    else:
        base = _HOUR_WORDS[h % 12 or 12]
        band_h = h
    band = ("in the morning" if band_h < 12
            else "in the afternoon" if band_h < 17 else "in the evening")
    if base in ("midday", "midnight"):
        return base
    return f"{_MIN_WORDS.get(m, '')}{base} {band}"


def make_day(date: str, times, hidden: int = 0) -> dict:
    return {
        "date": date,
        "day_label": LABELS[date],
        "slot_times": list(times),
        "slot_times_spoken": [spoken_label(t) for t in times],
        "times_not_shown": hidden,
        "slots": [{"start": f"{date}T{t}:00+01:00", "end": ""} for t in times],
    }


def session_for(days, offer) -> dict:
    """The session as the ENGINE leaves it after reading `offer` out.

    `record_spoken_slots` takes what was SPOKEN, not what the diary holds. The
    first version of this harness recorded the whole payload and reported 268
    violations of the resolver's "only among times the caller was actually
    READ" rule -- every one of them the harness's own fault. A generator that
    builds an unreachable session measures nothing, which is the same trap the
    hand-built test fixtures fell into on 2026-09-03.
    """
    session = {
        "available_days": days,
        "last_offered_slots": [
            {"start": f"{d['date']}T{d['slot_times'][0]}:00+01:00", "end": ""}
            for d in days
        ],
        "slot_labels": [d["day_label"] for d in days],
    }
    record_spoken_slots(session, [
        {"start": s.get("start"), "spoken": s.get("spoken"), "date": s.get("date")}
        for s in (offer.slots if offer else [])
    ])
    return session


# ── The caller space ────────────────────────────────────────────────────────
ACCEPT_TEMPLATES = [
    "yeah {} works", "{} works", "{} please", "let's do {}", "yeah {} is fine",
]
REQUEST_TEMPLATES = [
    "what about {}", "how about {}", "do you have {}", "can you do {}",
    "is {} free", "anything on {}", "have you got {}",
]
MERIDIEM_TEMPLATES = ["yeah {day} at {n} {mer} works", "{day} at {n}{mer}"]


def violations_for(days, quick: bool):
    """Yield (rule, detail) for one generated diary."""
    offer = build_slot_offer(days)
    if offer is None:
        return
    session = session_for(days, offer)
    session["_slot_offer_mode"] = offer.mode
    text = offer.text.lower()
    payload_starts = {
        f"{d['date']}T{t}:00+01:00" for d in days for t in d["slot_times"]
    }

    # 1. every slot in the record is named in the sentence
    for s in offer.slots:
        if (s.get("spoken") or "").lower() not in text:
            yield ("RECORD vs SENTENCE",
                   f"{s.get('spoken')!r} is in the record and not in the speech")

    # 2. every keypad value names a slot in the record
    spoken_set = {(s.get("spoken") or "").lower() for s in offer.slots}
    label_set = {str(d["day_label"]).lower() for d in days}
    for key, val in (offer.dtmf_map or {}).items():
        v = str(val).lower()
        if v not in spoken_set and v not in label_set:
            yield ("MAP vs RECORD", f"key {key} -> {val!r} names no offered slot")

    # 3. no invention
    for s in offer.slots:
        if s.get("start") not in payload_starts:
            yield ("NO INVENTION", f"{s.get('start')} is not in the payload")

    # 7. more_times only when the payload really holds unnamed times
    named = len(offer.slots)
    total = sum(len(d["slot_times"]) + int(d.get("times_not_shown") or 0)
                for d in days)
    if offer.more_times and total <= named:
        yield ("MORE-TIMES",
               f"claimed more times with {named} named of {total} held")

    # 4/5. acceptances
    for d in days:
        day_word = d["day_label"].split()[0].lower()
        for t in d["slot_times"]:
            hour = int(t[:2])
            mer = "am" if hour < 12 else "pm"
            wrong = "pm" if mer == "am" else "am"
            n = hour % 12 or 12
            for tpl in (MERIDIEM_TEMPLATES if quick else MERIDIEM_TEMPLATES):
                # a meridiem that CONTRADICTS every slot on the day
                utt = tpl.format(day=day_word, n=n, mer=wrong)
                got = slot_accepted_by_caller(session_for(days, offer), utt)
                if got:
                    gh = int(str(got)[11:13])
                    said = (n % 12) + (12 if wrong == "pm" else 0)
                    if gh != said:
                        yield ("MERIDIEM",
                               f"{utt!r} -> {got} (hour {gh}, caller said {said})")

    # 5. an acceptance never resolves to a slot that was never spoken
    spoken_starts = {s.get("start") for s in offer.slots}
    for d in days:
        day_word = d["day_label"].split()[0].lower()
        for tpl in ACCEPT_TEMPLATES[:2 if quick else None]:
            for t in d["slot_times"]:
                utt = tpl.format(f"{day_word} at {spoken_label(t)}")
                got = slot_accepted_by_caller(session_for(days, offer), utt)
                if got and got not in spoken_starts:
                    yield ("UNSPOKEN", f"{utt!r} -> {got}, which was never read out")

    # 6. requests are never picks
    for d in days:
        day_word = d["day_label"].split()[0].lower()
        for tpl in REQUEST_TEMPLATES[:3 if quick else None]:
            utt = tpl.format(day_word)
            if day_accepted_by_caller(session_for(days, offer), utt):
                yield ("REQUESTS", f"{utt!r} resolved as an acceptance")


def diaries(quick: bool):
    shapes = list(DAY_SHAPES.items())
    if quick:
        shapes = shapes[:4]
    # single-day offers
    for name, times in shapes:
        yield f"single/{name}", [make_day(DATES[0], times)]
        yield f"single/{name}+hidden", [make_day(DATES[0], times, hidden=6)]
    # multi-day offers, every ordered combination of shapes
    combos = itertools.product(shapes, repeat=2 if quick else 3)
    for combo in combos:
        days = [make_day(DATES[i], t) for i, (_, t) in enumerate(combo)]
        yield "multi/" + "+".join(n for n, _ in combo), days


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true",
                    help="bounded sweep, for CI")
    args = ap.parse_args()

    total = 0
    found = {}
    for name, days in diaries(args.quick):
        total += 1
        for rule, detail in violations_for(days, args.quick):
            found.setdefault(rule, []).append(f"{name}: {detail}")

    print(f"swept {total} generated diaries")
    if not found:
        print("no invariant violations")
        return 0
    for rule, items in sorted(found.items()):
        print(f"\n{rule}  ({len(items)})")
        for line in items[:12]:
            print("   " + line)
        if len(items) > 12:
            print(f"   ... and {len(items) - 12} more")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
