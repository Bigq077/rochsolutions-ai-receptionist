"""Replay the two live Theorem calls of 31 Aug 2026 through build_slot_offer.

Step 2 of docs/plan/DETERMINISTIC_SLOT_PRESENTATION.md, in its cheapest form:
the payloads are transcribed from the Render logs rather than pulled from obs,
so this runs with no database. It answers the only question that gates wiring —
does the deterministic formatter say what the model said, and is its record
right where the model's was not?

    python scripts/compare_slot_offer_to_live_calls.py
"""
from __future__ import annotations

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.tools.slot_offer import build_slot_offer


def day(date, label, times, spoken, hidden=0):
    return {
        "date": date, "day_label": label,
        "slot_times": times, "slot_times_spoken": spoken,
        "times_not_shown": hidden,
        "slots": [{"start": "{}T{}:00+01:00".format(date, t), "end": ""} for t in times],
    }


M = ["ten in the morning", "eleven in the morning", "one in the afternoon",
     "two in the afternoon", "three in the afternoon", "five in the evening"]
CASES = [
    (
        "CA44f1bdbe 20:39:03  multi_day, week of 7 Sept",
        [day("2026-09-07", "Monday 7th September",
             ["10:00", "11:00", "13:00", "14:00", "15:00", "17:00"], M),
         day("2026-09-08", "Tuesday 8th September",
             ["09:00", "10:00", "11:00", "14:00", "15:00", "16:00", "17:00"],
             ["nine in the morning", "ten in the morning", "eleven in the morning",
              "two in the afternoon", "three in the afternoon",
              "four in the afternoon", "five in the evening"]),
         day("2026-09-09", "Wednesday 9th September",
             ["09:00", "10:00", "11:00", "12:00", "14:00", "15:00", "17:00", "18:00"],
             ["nine in the morning", "ten in the morning", "eleven in the morning",
              "midday", "two in the afternoon", "three in the afternoon",
              "five in the evening", "six in the evening"])],
        {},
        "Number 3, Wednesday 9th September - nine in the morning or six in the evening",
    ),
    (
        "CA7e3ccfd4 21:31:39  single_day, afternoon band on Wed 9th",
        [day("2026-09-09", "Wednesday 9th September",
             ["14:00", "15:00"], ["two in the afternoon", "three in the afternoon"],
             hidden=5)],
        {},
        "The available slots for Wednesday 9th September are - Number 1, two in "
        "the afternoon. Number 2, three in the afternoon. And I've a few others "
        "that day if none of those work.",
    ),
    (
        "CA7e3ccfd4 21:32:00  single_day, whole of Wed 9th",
        [day("2026-09-09", "Wednesday 9th September",
             ["09:00", "10:00", "11:00", "12:00", "14:00", "15:00", "17:00"],
             ["nine in the morning", "ten in the morning", "eleven in the morning",
              "midday", "two in the afternoon", "three in the afternoon",
              "five in the evening"])],
        {},
        "The available slots for Wednesday 9th September are - Number 1, ten in "
        "the morning. Number 2, eleven in the morning. Number 3, midday. And "
        "I've a few others that day...",
    ),
]

for name, days, kwargs, live in CASES:
    offer = build_slot_offer(days, **kwargs)
    print("=" * 78)
    print(name)
    print("-- live (model) --")
    print("   " + live)
    print("-- deterministic --")
    for c in offer.chunks:
        print("   | " + c)
    print("   record : {}".format([s["start"][11:16] for s in offer.slots]))
    print("   map    : {}".format(offer.dtmf_map))
    print("   more   : {}".format(offer.more_times))
    missing = [s["spoken"] for s in offer.slots if s["spoken"] not in offer.text]
    print("   RECORD vs SENTENCE: {}".format(
        "agree" if not missing else "DISAGREE {}".format(missing)))
