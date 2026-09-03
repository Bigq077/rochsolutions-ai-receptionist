"""When one day is on offer, a caller naming only a time has picked a slot.

Live 2026-09-03 13:07:24 on the demo line, build ae97af1e:

    13:07:12  'uh yeah monday works'
    13:07:12  situational head (slot_picked): 'Monday it is —'
    13:07:14  "Monday the 7th of September — I've got eight in the morning or
               ten past five in the evening. Which suits?"
    13:07:24  'um 10 past 5 in the evening works'
    13:07:25  situational head (time_band): "Let me see what I've got in the evening —"
    13:07:27  "So that's Monday the 7th of September at ten past five in the evening"

She promised a lookup and then did not do one. She confirmed -- the times were
already on the table. That is the promised-work defect, and it is reached by
the most ordinary answer a caller can give: Susie narrows to a day, re-reads
that day's times, and the caller answers with a TIME because the day is settled
and she just said it back to them.

WHY IT RESOLVED TO NOTHING. `slot_accepted_by_caller` step 2 requires a DAY,
by position, by full label, or by bare weekday. "10 past 5 in the evening"
names none, so the resolver declined, `_hs_picking` stayed false, and the
TIME_BAND diary head fired.

Declining was right in general and wrong here: with ONE day on the table there
is nothing for the day to be. So the ladder gets a fourth and last step --
the offer's sole date -- and it fires only when the offer holds exactly one.

Deny-by-default is untouched everywhere it matters. A multi_day offer still
needs the day named; guessing there would pin a slot on the wrong DAY, which
is the failure the whole ladder exists to prevent, and it has its own test
below and in test_a_numeral_pick_resolves_a_spelled_label.
"""
from __future__ import annotations

import pytest

from app.tools.slot_followup import record_spoken_slots, slot_accepted_by_caller


def _day(date, label, times, spoken):
    return {
        "date": date, "day_label": label,
        "slot_times": list(times), "slot_times_spoken": list(spoken),
        "times_not_shown": 0,
        "slots": [{"start": f"{date}T{t}:00+01:00", "end": ""} for t in times],
    }


# Monday as Susie re-read it at 13:07:14, after the caller picked the day.
NARROWED = [
    _day("2026-09-07", "Monday 7th September", ["08:00", "17:10"],
         ["eight in the morning", "ten past five in the evening"]),
]

# The offer before any narrowing: three days, so the day is still open.
MULTI = NARROWED + [
    _day("2026-09-08", "Tuesday 8th September", ["08:50", "17:10"],
         ["ten to nine in the morning", "ten past five in the evening"]),
    _day("2026-09-09", "Wednesday 9th September", ["08:00", "16:20"],
         ["eight in the morning", "twenty past four in the afternoon"]),
]


def _session(days):
    session = {
        "available_days": days,
        "last_offered_slots": [
            {"start": f"{d['date']}T{t}:00+01:00", "end": ""}
            for d in days for t in d["slot_times"]
        ],
        "slot_labels": [d["day_label"] for d in days],
    }
    record_spoken_slots(session, [
        {"start": f"{d['date']}T{t}:00+01:00", "spoken": sp, "date": d["date"]}
        for d in days
        for t, sp in zip(d["slot_times"], d["slot_times_spoken"])
    ])
    return session


@pytest.mark.parametrize("utterance,expected", [
    # The live utterance, verbatim.
    ("um 10 past 5 in the evening works", "2026-09-07T17:10:00+01:00"),
    ("ten past five in the evening works", "2026-09-07T17:10:00+01:00"),
    ("8 in the morning works",             "2026-09-07T08:00:00+01:00"),
    ("eight in the morning please",        "2026-09-07T08:00:00+01:00"),
    # A band, when the day holds exactly one slot in it.
    ("the evening one works",              "2026-09-07T17:10:00+01:00"),
    ("the morning one",                    "2026-09-07T08:00:00+01:00"),
])
def test_a_time_only_pick_resolves_once_the_offer_holds_one_day(utterance, expected):
    assert slot_accepted_by_caller(_session(NARROWED), utterance) == expected


@pytest.mark.parametrize("utterance", [
    "um 10 past 5 in the evening works",
    "the evening one works",
    "8 in the morning works",
])
def test_the_same_words_decline_while_three_days_are_on_offer(utterance):
    """THE invariant. Two of those three days hold a ten-past-five, so a caller
    who names only the time has genuinely not said which day. Guessing would
    pin the wrong DAY -- a whole class worse than declining, and the reason
    every step of this ladder denies by default."""
    assert slot_accepted_by_caller(_session(MULTI), utterance) is None


def test_a_named_day_still_wins_on_a_multi_day_offer():
    """The steps above the new one are unchanged."""
    assert slot_accepted_by_caller(
        _session(MULTI), "monday at ten past five works"
    ) == "2026-09-07T17:10:00+01:00"


def test_the_meridiem_guard_still_holds_on_a_single_day_offer():
    """The new step must not become a way round the 8pm fix. Monday holds no
    evening eight, so this still declines even though the day is unambiguous."""
    assert slot_accepted_by_caller(_session(NARROWED), "yeah 8 pm works") is None


def test_an_unoffered_time_still_declines():
    """The day being settled does not make every time available."""
    assert slot_accepted_by_caller(_session(NARROWED), "half past two works") is None
