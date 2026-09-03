"""A caller who names a clock time must never be pinned to a different one.

Two live calls on the demo line, both on build `699dfc9f`, both abandoned:

  * 2026-09-03 00:46:26 — "uh yeah monday at 8 pm works" pinned **08:00**.
    `_strip_part_of_day` reduces "eight in the morning" to "eight", which
    folds to the digit 8, and "8 pm" contains an 8. The meridiem the caller
    said was discarded. Survived only because the model happened to notice
    Monday has no evening eight and re-asked.

  * 2026-09-03 01:29:14 — "yeah monday the 7th at 10 in the morning" pinned
    **08:00**. No label matched, so the band fallback at the end of the
    resolver ran, found 08:00 was the only MORNING slot offered on Monday, and
    returned it. Judge score 2; the caller answered a question they had
    already answered and hung up.

WHY THIS IS THE WORST CLASS OF DEFECT IN THIS SYSTEM. `ACCEPTED_SLOT_KEY` is
pinned so the accepted slot survives into the readout (P6b) and the read-back
is generated FROM the pin. So a wrong pin sounds correct every time it is read
aloud — the caller hears "Monday at eight in the morning", which is exactly
what the diary would get. This is the same family as the 90-minute booking
written as 60: a wrong value no verbal confirmation can catch.

The resolver's own docstring already forbids it:

    Returning the WRONG slot would pin it into the next readout and read it
    back as an appointment, so ambiguity always declines.

Both exits now ask `_time_contradicts`, and the band fallback declines when the
caller named a clock time of their own — a band may choose FOR a caller only
when the caller left the choice open.
"""
from __future__ import annotations

import pytest

from app.tools.slot_followup import (
    record_spoken_slots,
    slot_accepted_by_caller,
)


def _day(date, label, times, spoken):
    return {
        "date": date,
        "day_label": label,
        "slot_times": list(times),
        "slot_times_spoken": list(spoken),
        "times_not_shown": 0,
        "slots": [
            {"start": "{}T{}:00+01:00".format(date, t), "end": ""} for t in times
        ],
    }


# The northgate offer, exactly as both calls heard it.
OFFER = [
    _day("2026-09-07", "Monday 7th September", ["08:00", "17:10"],
         ["eight in the morning", "ten past five in the evening"]),
    _day("2026-09-08", "Tuesday 8th September", ["08:50", "17:10"],
         ["ten to nine in the morning", "ten past five in the evening"]),
    _day("2026-09-09", "Wednesday 9th September", ["08:00", "16:20"],
         ["eight in the morning", "twenty past four in the afternoon"]),
]


def _mid_offer(days):
    """A caller who has just been read `days` as a multi_day offer."""
    session = {
        "available_days": days,
        "last_offered_slots": [
            {"start": "{}T{}:00+01:00".format(d["date"], d["slot_times"][0]),
             "end": ""}
            for d in days
        ],
        "slot_labels": [d["day_label"] for d in days],
    }
    record_spoken_slots(session, [
        {"start": "{}T{}:00+01:00".format(d["date"], t),
         "spoken": spoken, "date": d["date"]}
        for d in days
        for t, spoken in zip(d["slot_times"], d["slot_times_spoken"])
    ])
    return session


# ── The two live utterances must resolve to nothing ─────────────────────────

@pytest.mark.parametrize("utterance", [
    "uh yeah monday at 8 pm works",          # 00:46:26 — meridiem discarded
    "yeah monday the 7th at 10 in the morning",  # 01:29:14 — band substituted
])
def test_a_time_the_offer_does_not_hold_is_declined(utterance):
    """Declining is cheap: the turn re-asks and the caller is no worse off.

    Pinning is not: it reaches the calendar, and every read-back on the way
    there is generated from the pin and therefore agrees with it.
    """
    got = slot_accepted_by_caller(_mid_offer(OFFER), utterance)
    assert got is None, (
        f"{utterance!r} resolved to {got!r} — the caller named a time this "
        f"offer does not hold, and a resolver that guesses writes the wrong "
        f"appointment"
    )


def test_the_8pm_case_is_not_merely_declined_by_accident():
    """Pin the mechanism, not just the outcome.

    If a later edit makes "eight" stop matching "8 pm" for some unrelated
    reason, the test above would still pass while the guard rotted. This
    asserts the meridiem itself is read.
    """
    from app.tools.slot_followup import _meridiem_hour_named, _time_contradicts

    assert _meridiem_hour_named("monday at 8 pm") == 20
    assert _meridiem_hour_named("monday at 8 am") == 8
    assert _meridiem_hour_named("monday morning") is None
    # Two readings decline, the standing rule for two of anything here.
    assert _meridiem_hour_named("8am or 8pm, either") is None
    assert _time_contradicts("monday at 8 pm", "2026-09-07T08:00:00+01:00")
    assert not _time_contradicts("monday at 8 am", "2026-09-07T08:00:00+01:00")


# ── And the guard must not eat the picks that were always correct ───────────

@pytest.mark.parametrize("utterance,expected", [
    # Named outright, and the offer holds it.
    ("yeah monday the 7th at eight in the morning", "2026-09-07T08:00:00+01:00"),
    ("monday at ten past five works",               "2026-09-07T17:10:00+01:00"),
    ("wednesday at twenty past four",               "2026-09-09T16:20:00+01:00"),
    # The meridiem AGREES — it must not decline on its mere presence.
    ("monday at 8 am please",                       "2026-09-07T08:00:00+01:00"),
    # A band with no time of the caller's own: the fallback may still choose,
    # because the caller left the choice open. "one" here is a pronoun and the
    # clock fold must not read it as one o'clock.
    ("the morning one on monday",                   "2026-09-07T08:00:00+01:00"),
    ("wednesday afternoon works",                   "2026-09-09T16:20:00+01:00"),
])
def test_a_pick_the_offer_does_hold_still_resolves(utterance, expected):
    got = slot_accepted_by_caller(_mid_offer(OFFER), utterance)
    assert got == expected, f"{utterance!r} resolved to {got!r}, wanted {expected!r}"


# ── The meridiem must FILTER, not merely veto ───────────────────────────────

# A day holding both 08:00 and 20:00. Both labels strip to the bare digit "8",
# so both match on the digit alone and the meridiem is the ONLY thing that
# separates them.
EVENING_OFFER = [
    _day("2026-09-07", "Monday 7th September", ["08:00", "20:00"],
         ["eight in the morning", "eight in the evening"]),
]


@pytest.mark.parametrize("utterance,expected", [
    ("yeah monday at 8 pm works",      "2026-09-07T20:00:00+01:00"),
    ("yeah monday at 8 am works",      "2026-09-07T08:00:00+01:00"),
    ("monday at eight in the evening", "2026-09-07T20:00:00+01:00"),
    ("monday at eight in the morning", "2026-09-07T08:00:00+01:00"),
])
def test_the_meridiem_selects_between_two_slots_an_hour_apart_in_name(
    utterance, expected
):
    """Owner's rule, 2026-09-03: a caller who names a slot from the readout
    gets THAT slot. Declining is only for a time the offer does not hold.

    The first version of this guard vetoed instead of filtering, so on this
    offer BOTH labels matched the digit, `len(hits) == 1` failed, and a caller
    who asked for a slot they had just been read got None. Caught by the owner
    asking the obvious question -- "if somebody asks for a specific slot from
    the readout she should offer that slot" -- which is the right question and
    the reason this test exists.
    """
    got = slot_accepted_by_caller(_mid_offer(EVENING_OFFER), utterance)
    assert got == expected, f"{utterance!r} resolved to {got!r}, wanted {expected!r}"


def test_a_bare_hour_with_two_readings_on_offer_still_declines():
    """"monday at 8" when the day holds BOTH 08:00 and 20:00 names neither.

    This is the standing rule -- ambiguity declines -- and it is the one case
    where declining is right even though the caller named a real offered time.
    """
    assert slot_accepted_by_caller(_mid_offer(EVENING_OFFER), "monday at 8 works") is None
