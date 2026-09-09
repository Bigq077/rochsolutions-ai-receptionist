"""A caller who names a clock time must HEAR that time in the readout.

D8, `CA7d48a879ed6cb0554a3738dee8941380` (7 Sep 2026, theorem_v3), judge 3,
tag `loop`:

    caller : wednesday the 9th of september at 12 pm
    Susie  : Wednesday 9th September — Number 1, ten in the morning.
             Number 2, eleven in the morning. Number 3, three in the afternoon.
             And I've a few others that day.
    caller : no what else have you got that day
    Susie  : On Wednesday 9th September I also have — Number 1, midday.
    caller : number 1 midday

Midday was bookable the whole time. The band filter was never at fault —
`_has_explicit_clock` already skips the coarse morning/afternoon band precisely
so a named time is not filtered out. But surviving a filter is not being spoken:
`_choose_presented_times` then applies B-116, "times this caller has not heard,
chronologically", which has no notion of a time they asked for.

THE PARSER WAS THE RISK, NOT THE PIN. This repo's date handling has already
turned "September 19th" into 19 AUGUST through two independent day-first gates,
and the corpus is full of callers naming a date and a time in one breath. A bare
number-grab reads the date as the hour — which is B-126 one layer down, where
"9" matched inside "Wednesday the 9th of September" and a guard stood down.

`requested_clock_times` was therefore built against the corpus rather than
against imagination: 2,509 unique stored caller turns, of which 202 name a time.
It invents a time for none of the remaining 2,307. The cases below are real
utterances from that corpus unless marked otherwise.
"""
from __future__ import annotations

import pytest

from app.tools.receptionist_tools import _cap_presented_slots
from app.tools.slot_followup import (
    ACCEPTED_SLOT_KEY,
    REQUESTED_TIMES_KEY,
    choose_presented_indices,
    requested_clock_times,
)

# Wednesday 9th September on the real call: six bookable times, midday among
# them, three to be spoken.
TIMES = ["10:00", "11:00", "12:00", "13:00", "15:00", "17:10"]
SPOKEN = ["ten in the morning", "eleven in the morning", "midday",
          "one in the afternoon", "three in the afternoon",
          "ten past five in the evening"]


def _day(times=None, spoken=None, date="2026-09-09"):
    times = list(times or TIMES)
    spoken = list(spoken or SPOKEN)
    return {
        "date": date, "day_label": "Wednesday 9th September",
        "slot_times": times, "slot_times_spoken": spoken,
        "times_not_shown": 0,
        "slots": [{"start": "%sT%s:00+01:00" % (date, t), "end": ""}
                  for t in times],
    }


def _session(hint: str):
    return {REQUESTED_TIMES_KEY: requested_clock_times(hint)}


def _readout(hint: str, day=None):
    """The real chain: resolve the hint, then cap the day for speech."""
    out = _cap_presented_slots(
        {"available_days": [day or _day()], "success": True},
        session=_session(hint), mode="single_day",
    )
    return out["first_day"]["slot_times_spoken"]


# ── The live call ────────────────────────────────────────────────────────────

def test_the_named_time_is_spoken():
    assert "midday" in _readout("wednesday the 9th of september at 12 pm")


def test_without_the_named_time_it_is_still_dropped():
    """The control. B-116 is untouched — this is what every other caller gets,
    and it must not change."""
    assert "midday" not in _readout("")


def test_the_readout_does_not_grow():
    """The pin DISPLACES, never adds, so `limit` still means what
    `_cap_presented_slots` says it means and the keypad stays in step."""
    assert len(_readout("wednesday the 9th of september at 12 pm")) == 3
    assert len(_readout("")) == 3


def test_the_readout_stays_chronological():
    """The keypad map is built from this order — a caller pressing 2 means the
    second thing they heard."""
    spoken = _readout("wednesday the 9th of september at 12 pm")
    assert spoken == sorted(spoken, key=SPOKEN.index)


# ── The parser, on real corpus utterances ────────────────────────────────────

@pytest.mark.parametrize("utterance,expected", [
    # A date and a time in one breath — the B-126 shape.
    ("wednesday the 9th of september at 12 pm", ["12:00"]),
    ("monday the 7th at 10 in the morning", ["10:00"]),
    ("the 10th of august at 5 in the evening", ["17:00"]),
    ("tuesday the 11th at 6 10 in the evening", ["18:10"]),
    ("half past 4 on the 24th", ["04:30", "16:30"]),
    # Meridiem settles the twin outright.
    ("yeah monday at 8 am works", ["08:00"]),
    ("yeah monday at 8 pm works", ["20:00"]),
    ("can i have 3 pm please", ["15:00"]),
    # A band word the caller actually said settles it too.
    ("um yeah saturday at 9 in the morning suits me", ["09:00"]),
    ("yeah 3 o clock in the afternoon works", ["15:00"]),
    ("half past six in the evening works for me", ["18:30"]),
    # No meridiem, no band — both readings survive for the diary to settle.
    ("um yeah quarter to 12 works for me", ["11:45", "23:45"]),
    ("yeah 10 to 9 works", ["08:50", "20:50"]),
    ("20 past 4 suits", ["04:20", "16:20"]),
    ("half past 4 please", ["04:30", "16:30"]),
    ("midday", ["12:00"]),
])
def test_the_resolver_reads_real_utterances(utterance, expected):
    assert requested_clock_times(utterance) == expected, utterance


@pytest.mark.parametrize("utterance", [
    # A DATE is not a time. Every one of these must resolve to nothing.
    "do you have anything on the 19th of september",
    "can i book for the 24th",
    "the 31st please",
    "september 19th",
    # A DURATION is not a time — this is the opening reason on a booking call,
    # and the loose "about N" arm read it as three o'clock.
    "i've had knee pain for about 3 weeks",
    "um yeah hi there i've had knee pain for about 3 weeks um it's worse going "
    "downstairs can i get booked in please",
    "it's been sore for about 2 days",
    # An AGE is not a time, on the one clinic with an under-age gate.
    "um he's 18 17 i mean he's turning 18 in a couple months",
    # No time at all.
    "i would like to book an appointment",
    "yeah that works",
    "",
])
def test_the_resolver_invents_nothing(utterance):
    assert requested_clock_times(utterance) == [], utterance


def test_a_date_alone_never_pins():
    """The whole point of the date mask: a caller naming only a date gets the
    ordinary readout, not a slot chosen by their date's digits."""
    assert _readout("do you have anything on the 19th of september") == _readout("")


def test_the_ninth_does_not_pin_nine_oclock():
    """B-126, one layer down: "9" inside "the 9th of September" is a DATE."""
    day = _day(times=["09:00", "10:00", "11:00", "15:00", "17:10"],
               spoken=["nine in the morning", "ten in the morning",
                       "eleven in the morning", "three in the afternoon",
                       "ten past five in the evening"])
    hinted = _readout("wednesday the 9th of september", day=day)
    plain = _readout("", day=day)
    assert hinted == plain


# ── The guards on the pin itself ─────────────────────────────────────────────

def test_two_readings_decline():
    """"at 5" is 05:00 or 17:00. When the day holds BOTH, which was meant is
    unknowable here, so neither is pinned."""
    day = _day(times=["05:00", "09:00", "12:00", "15:00", "17:00"],
               spoken=["five in the morning", "nine in the morning", "midday",
                       "three in the afternoon", "five in the evening"])
    assert requested_clock_times("tuesday at 5") == ["05:00", "17:00"]
    assert _readout("tuesday at 5", day=day) == _readout("", day=day)


def test_a_band_word_resolves_the_twin_and_pins():
    """The same day, the same hour — but the caller said which end they meant."""
    day = _day(times=["05:00", "09:00", "12:00", "15:00", "17:00"],
               spoken=["five in the morning", "nine in the morning", "midday",
                       "three in the afternoon", "five in the evening"])
    assert requested_clock_times("tuesday at 5 in the evening") == ["17:00"]
    assert "five in the evening" in _readout("tuesday at 5 in the evening", day=day)


def test_a_time_this_day_does_not_hold_is_a_no_op():
    assert _readout("at 4 in the morning") == _readout("")


def test_a_time_already_being_spoken_is_a_no_op():
    """10:00 is index 0 and B-116 already keeps it — the pin must not reshuffle
    a readout that was already right."""
    assert _readout("at 10 in the morning") == _readout("")


def test_an_empty_request_leaves_the_rule_untouched():
    """The inertness guarantee: with nothing requested, every readout on every
    clinic is byte-identical to B-116's own answer."""
    day = _day()
    for limit in (1, 2, 3, 4):
        assert (choose_presented_indices({}, day, limit)
                == choose_presented_indices(
                    {REQUESTED_TIMES_KEY: []}, day, limit))


# ── Precedence against the accepted-slot pin ─────────────────────────────────

def test_an_accepted_slot_outranks_a_requested_time():
    """`_pin_accepted_index` runs OUTSIDE this one on purpose: a slot the
    caller has agreed to must survive a readout, and a time they merely asked
    about must not displace it."""
    day = _day()
    session = {
        REQUESTED_TIMES_KEY: requested_clock_times("at 11 in the morning"),
        ACCEPTED_SLOT_KEY: "2026-09-09T17:10:00",
    }
    idx = choose_presented_indices(session, day, 3)
    assert TIMES.index("17:10") in idx, "the accepted slot was displaced"
    assert len(idx) == 3


def test_both_pins_fit_when_there_is_room():
    day = _day()
    session = {
        REQUESTED_TIMES_KEY: requested_clock_times("at 11 in the morning"),
        ACCEPTED_SLOT_KEY: "2026-09-09T17:10:00",
    }
    idx = choose_presented_indices(session, day, 3)
    assert TIMES.index("11:00") in idx
    assert TIMES.index("17:10") in idx


# ── It must never raise on a live booking turn ───────────────────────────────

@pytest.mark.parametrize("day", [
    {}, {"slot_times": None}, {"slot_times": []},
    {"slot_times": ["nonsense"]}, {"slot_times": [None]},
])
def test_a_malformed_day_falls_through_quietly(day):
    session = {REQUESTED_TIMES_KEY: ["12:00"]}
    assert choose_presented_indices(session, day, 3) == \
        choose_presented_indices({}, day, 3)


@pytest.mark.parametrize("value", [None, "not a list", 12, {"a": 1}])
def test_a_malformed_session_value_falls_through_quietly(value):
    day = _day()
    assert choose_presented_indices({REQUESTED_TIMES_KEY: value}, day, 3) == \
        choose_presented_indices({}, day, 3)


# ── Every reader must publish, or the fix is silently half-live ──────────────
# The lesson this repo keeps paying for, most recently on D10: an honesty field
# written by ONE availability path covers Theorem and misses the three shared
# readers. `_requested_clock_times` is written at the three `check_availability`
# entry points, which between them cover all four clinics. A fourth reader added
# without the write would look fine in every test above, because every test
# above supplies the key by hand.

def test_every_availability_entry_point_publishes_the_requested_times():
    import inspect
    from app.tools import receptionist_tools as rt

    entry_points = (
        rt._exec_check_availability,      # Acuity + the google_calendar fall-through
        rt._check_availability_diary,     # Vital Edge
        rt._check_availability_published,
    )
    for fn in entry_points:
        assert "session" in str(inspect.signature(fn)), fn.__name__
        src = inspect.getsource(fn)
        assert "requested_clock_times" in src, (
            "%s does not resolve the caller's named time, so D8 is live on "
            "whichever clinics it serves" % fn.__name__
        )


def test_the_key_is_rewritten_on_every_lookup_not_only_when_a_time_is_named():
    """It is session state, so a stale value would pin a slot in a readout it
    has nothing to do with. The write is unconditional — `requested_clock_times`
    returns [] for a hint that names no time, and [] is what gets stored."""
    import inspect
    from app.tools import receptionist_tools as rt
    for fn in (rt._exec_check_availability, rt._check_availability_diary,
               rt._check_availability_published):
        src = inspect.getsource(fn)
        assert "session[_RTK] = _rct(_pref)" in src, fn.__name__
        # not guarded by "if the hint has a time" — that is the whole point
        assert "if _rct(_pref)" not in src, fn.__name__
