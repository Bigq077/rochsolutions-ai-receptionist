# tests/regression/test_t1b_named_day_and_multi_day_readouts_vary.py
"""
T1b - CAfb09f66e332f988c312553d68ec63f18, 10 Sep 2026, northgate,
build 8ed9e1954205. Judge 2, outcome=abandoned.

    Susie : I've got a few days - Number 1, Monday 14th September - eight in
            the morning, or ten past five in the evening. Number 2, Tuesday
            15th - eight in the morning, or twenty past four. Number 3,
            Wednesday 16th - eight in the morning, or ...
    caller: "um yeah can you tell me about monday"
    Susie : Monday 14th - eight in the morning, one in the afternoon, ten past
            five in the evening
    caller: "and what about tuesday"
    Susie : Tuesday 15th - eight in the morning, ten past twelve, twenty past
            four in the afternoon

"Eight in the morning" four times in one call. Both Monday times and both
Tuesday times he was re-read had been spoken to him thirty seconds earlier.

TWO DISTINCT SITES, and the T1 fix (748677ee) reached NEITHER. That fix was
verified against `choose_presented_indices` and was correct there; this call
never went through it.

  SITE 1 - the multi-day readout. `_cap_presented_slots` picks each day in a
  loop against the SAME spoken record, which is empty on a first lookup. So
  every day independently chose position 0, and on northgate's uniform
  50-minute grid position 0 is 08:00 on every day of the week.

  SITE 2 - `speak_one_day_from_payload`, the named-day producer behind both
  D-B and B-145. It handed `build_slot_offer` the WHOLE day, and that
  function's own docstring states the contract: "PASS `more_times` when the
  days handed in have ALREADY been trimmed ... `_cap_presented_slots` selects
  those positions through `choose_presented_indices`, which prefers times this
  caller has not heard (B-116) - knowledge this function does not have and
  must not overrule." It never got that trim, so it read the chronological
  head of the day - the exact slice B-116 exists to replace. D8's
  requested-time pin lives inside the same call and was lost with it.

Both sites are driven here through the REAL entry points, never a
reimplementation of the loop: a mock of the loop is what let the first version
of this measurement pass while the live path stayed broken.
"""
from __future__ import annotations

from app.tools.receptionist_tools import _cap_presented_slots
from app.tools.slot_followup import (
    REQUESTED_TIMES_KEY,
    record_spoken_slots,
    speak_one_day_from_payload,
)

MON, TUE, WED = "2026-09-14", "2026-09-15", "2026-09-16"

#: northgate's real grid: 50-minute steps from 08:00.
GRID = [
    "08:00", "08:50", "09:40", "10:30", "11:20", "12:10",
    "13:00", "13:50", "14:40", "15:30", "16:20", "17:10",
]
LABELS = {
    MON: "Monday 14th September",
    TUE: "Tuesday 15th September",
    WED: "Wednesday 16th September",
}


def _slots(day_iso, times):
    return [
        {"start": f"{day_iso}T{t}:00+01:00", "end": f"{day_iso}T{t}:50+01:00"}
        for t in times
    ]


def _day(day_iso, times=None):
    times = list(GRID if times is None else times)
    return {
        "date": day_iso,
        "day_label": LABELS[day_iso],
        "slot_times": times,
        "slot_times_spoken": [f"spoken-{t}" for t in times],
        "slots": _slots(day_iso, times),
        "times_found_on_day": len(times),
        "times_not_shown": 0,
    }


def _days():
    return [_day(MON), _day(TUE), _day(WED)]


def _multi_day_readout(session=None, days=None):
    """The live loop, not a mock of it."""
    capped = _cap_presented_slots(
        {"available_days": days or _days()}, session if session is not None else {},
        3, "multi_day",
    )
    return {d["date"]: list(d["slot_times"]) for d in capped["presented_days"]}


def _after_the_multi_day_offer():
    """The session as it stood at turn 3 of the call: the six times in the
    multi-day offer have been spoken, and `available_days` holds the sweep."""
    days = _days()
    session = {"available_days": days}
    record_spoken_slots(session, [
        {"start": f"{MON}T08:00:00+01:00"}, {"start": f"{MON}T17:10:00+01:00"},
        {"start": f"{TUE}T08:00:00+01:00"}, {"start": f"{TUE}T16:20:00+01:00"},
        {"start": f"{WED}T08:00:00+01:00"}, {"start": f"{WED}T17:10:00+01:00"},
    ])
    return session, days


def _spoken_times(session):
    return [
        str(s.get("start"))[11:16]
        for s in (session.get("last_offered_slots") or [])
    ]


# ---------------------------------------------------------------------------
# Site 1 - the multi-day readout
# ---------------------------------------------------------------------------
def test_a_multi_day_readout_does_not_open_every_day_at_the_same_time():
    """The defect, at the top of the call. Three days, one clock time."""
    readout = _multi_day_readout()
    openers = [times[0] for times in readout.values()]

    assert len(readout) == 3, readout
    assert len(set(openers)) == len(openers), readout


def test_a_multi_day_readout_repeats_no_clock_time_across_its_days():
    """Stronger than the openers: nothing said twice anywhere in the answer."""
    readout = _multi_day_readout()
    spoken = [t for times in readout.values() for t in times]

    assert len(spoken) == len(set(spoken)), readout


def test_the_first_day_still_leads_with_the_earliest():
    """Nothing has been committed when the first day is picked, so it is
    unaffected - and the day's real earliest is what most callers want."""
    assert _multi_day_readout()[MON][0] == "08:00"


def test_every_time_offered_is_a_real_slot_on_that_day():
    """The one thing that must never break: a spoken time the caller cannot
    book. Positions are chosen across three parallel arrays."""
    for date, times in _multi_day_readout().items():
        assert set(times) <= set(GRID), (date, times)


def test_soonest_still_leads_every_day_with_its_earliest():
    """B-137/B-142. "Sooner" and "what else" are opposite questions: a caller
    who asked for the earliest wants each day's earliest, repeats and all."""
    readout = _multi_day_readout({"day_preference": "as soon as possible"})

    assert [times[0] for times in readout.values()] == ["08:00"] * 3, readout


# ---------------------------------------------------------------------------
# Site 2 - the named-day producer
# ---------------------------------------------------------------------------
def test_a_named_day_is_not_re_read_at_the_times_already_heard():
    """"Can you tell me about monday", after Monday's 08:00 and 17:10 were
    read out in the multi-day offer."""
    session, days = _after_the_multi_day_offer()

    text = speak_one_day_from_payload(session, days, MON, why="D-B")

    assert text
    spoken = _spoken_times(session)
    assert len(spoken) == 3, spoken
    assert not {"08:00", "17:10"} & set(spoken), spoken


def test_a_second_named_day_is_not_re_read_either():
    """"And what about tuesday" - the same defect one turn later."""
    session, days = _after_the_multi_day_offer()
    speak_one_day_from_payload(session, days, MON, why="D-B")

    speak_one_day_from_payload(session, days, TUE, why="D-B")

    spoken = _spoken_times(session)
    assert not {"08:00", "16:20"} & set(spoken), spoken


def test_the_named_day_producer_honours_a_requested_time():
    """D8's pin lives inside `choose_presented_indices`, so bypassing that
    call dropped it too: a caller who named a day AND a time got neither.

    RE-AIMED for S-13, not weakened. This test used to write
    `REQUESTED_TIMES_KEY` itself, and it passed all week while the live path
    was dead - nothing on this path ever wrote that key, because its only
    three writers are inside `check_availability` and a named-day follow-up
    runs no tool. The producer now writes it from the caller's own words on
    every payload turn, so the caller's words are what this hands in.
    """
    session, days = _after_the_multi_day_offer()

    speak_one_day_from_payload(
        session, days, MON, why="D-B",
        user_text="can you tell me about monday at one in the afternoon",
    )

    assert session[REQUESTED_TIMES_KEY] == ["13:00"]
    assert "13:00" in _spoken_times(session), _spoken_times(session)


def test_the_named_day_producer_still_says_there_are_more():
    """B-97. A pre-trimmed day looks complete to the formatter, so `more_times`
    has to be passed explicitly now - or she claims a 12-slot day holds 3."""
    session, days = _after_the_multi_day_offer()

    text = speak_one_day_from_payload(session, days, MON, why="D-B")

    assert "a few others" in (text or "").lower(), text


def test_a_short_day_is_spoken_whole_and_claims_nothing_more():
    """The other side of it: when the day really does hold three or fewer,
    nothing is held back and the completeness claim is true."""
    days = [_day(MON, ["09:40", "14:40"])]
    session = {"available_days": days}

    text = speak_one_day_from_payload(session, days, MON, why="D-B")

    assert sorted(_spoken_times(session)) == ["09:40", "14:40"]
    assert "a few others" not in (text or "").lower(), text


def test_the_named_day_producer_never_offers_a_time_off_another_day():
    """It narrows to ONE day. Positions come from three parallel arrays, and a
    mis-indexed pick here would name a slot on the wrong date."""
    session, days = _after_the_multi_day_offer()

    speak_one_day_from_payload(session, days, TUE, why="D-B")

    assert all(
        str(s.get("start"))[:10] == TUE
        for s in session.get("last_offered_slots") or []
    ), session.get("last_offered_slots")
