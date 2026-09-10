# tests/regression/test_n4_a_time_asked_on_one_day_is_answered_on_that_day.py
"""
N4 - asking for a time on Monday was answered with Thursday.

CA91d1f12332f6230ed51ad1a427f91f5c, 11 Sep 2026 23:11, northgate, build
6e556ad0. The caller had just been read Monday's times:

    23:11:58  Susie : Monday 14th September - half past ten, twenty past
                      eleven, twenty to three. And I've a few others that day.
    23:12:14  caller: "as close as possible to 12 please"
    23:12:18  tool  : check_availability day_window=1 after_date=2026-09-14
                      date_hint="around 12 noon"
    23:12:18  check_availability BLOCKED - slots already retrieved this turn
    23:12:18  3 of 6 days already offered -- leading with the 3 the caller
              has not heard
    23:12:20  Susie : Number 1, THURSDAY 17th - ten past twelve ...

He hung up. 12:10 was bookable on Monday the whole time.

Three correct mechanisms composed into it -- N2's parse, the re-entrancy guard,
and B-137's "lead with the days they have not heard" -- and the day the request
named was dropped between the question and the presentation.

And a SECOND dropped input, found while anchoring this: D8's pin reads
`REQUESTED_TIMES_KEY`, which no refusal ever wrote. On that call the key held
the `[]` the named-day producer wrote for "how about monday", so the refusal's
readout could not have been answering "close to 12" at all.

Written against the traps of 9-11 Sep:

  * THE GUARD IS NOT WEAKENED. A second live lookup mid-turn is its own
    defect; every test here goes through the refusal, not around it.
  * A SESSION KEY IS ONLY AS LIVE AS ITS WRITERS (S-13). The key is asserted
    written on every refusal, empty included, so it cannot go stale.
  * DRIVE THE EXTRACTED HELPER THE GUARD CALLS, and pin by source that the
    guard calls it -- a helper whose call site is deleted still passes.
  * STAND-DOWNS ARE SILENT. Each deny-by-default branch has its own test,
    because a scoping that quietly never fires reads exactly like the old
    behaviour.
"""
from __future__ import annotations

import inspect
import logging

import pytest

import app.media_streams.llm_stream as ls
import app.tools.receptionist_tools as rt
from app.tools.slot_followup import (
    REQUESTED_TIMES_KEY,
    record_spoken_slots,
    try_unspoken_followup_speech,
)

MON, TUE, WED, THU, FRI, SAT = (
    "2026-09-14", "2026-09-15", "2026-09-16",
    "2026-09-17", "2026-09-18", "2026-09-19",
)
LABELS = {
    MON: "Monday 14th September", TUE: "Tuesday 15th September",
    WED: "Wednesday 16th September", THU: "Thursday 17th September",
    FRI: "Friday 18th September", SAT: "Saturday 19th September",
}
GRID = [
    "08:00", "08:50", "09:40", "10:30", "11:20", "12:10",
    "13:00", "13:50", "14:40", "15:30", "16:20", "17:10",
]
#: Thursday on the live call read "ten past twelve, or ten to seven": its grid
#: runs later. Kept different so a scoped readout cannot pass by coincidence.
THU_GRID = ["12:10", "13:00", "13:50", "14:40", "15:30", "16:20", "17:10",
            "18:00", "18:50"]

EXHIBIT_ARGS = {"day_window": 1, "after_date": MON, "date_hint": "around 12 noon"}
EXHIBIT_WORDS = "as close as possible to 12 please"

#: What the caller had heard by 23:12:14 -- the spread, then Monday's readout.
HEARD = {
    MON: ["08:00", "17:10", "10:30", "11:20", "14:40"],
    TUE: ["08:50", "16:20"],
    WED: ["09:40", "15:30"],
}


def _slots(day_iso, times):
    return [
        {"start": f"{day_iso}T{t}:00+01:00", "end": f"{day_iso}T{t}:50+01:00"}
        for t in times
    ]


def _day(day_iso, times=None):
    times = list((THU_GRID if day_iso == THU else GRID) if times is None else times)
    return {
        "date": day_iso,
        "day_label": LABELS[day_iso],
        "slot_times": times,
        "slot_times_spoken": [f"spoken-{day_iso}-{t}" for t in times],
        "slots": _slots(day_iso, times),
        "times_found_on_day": len(times),
        "times_not_shown": 0,
    }


def _payload():
    return [_day(d) for d in (MON, TUE, WED, THU, FRI, SAT)]


def _at_23_12_14(**extra):
    """The session when the caller asked for midday: six days fetched, the
    spread and then Monday's single-day readout heard."""
    session = {"available_days": _payload(), **extra}
    for date, times in HEARD.items():
        record_spoken_slots(session, _slots(date, times))
    session["last_offered_slots"] = _slots(MON, ["10:30", "11:20", "14:40"])
    session["_slot_presentation_mode"] = "single_day"
    return session


def _presented(out):
    """(date, clock) the refusal hands the presenter, whichever mode it chose."""
    days = [out["first_day"]] if out.get("first_day") else (out.get("presented_days") or [])
    return [(d["date"], t) for d in days for t in (d.get("slot_times") or [])]


# ---------------------------------------------------------------------------
# _day_named_by_lookup -- pure, deny by default
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("args,expected,why", [
    (EXHIBIT_ARGS, MON, "the live call"),
    ({"day_window": "1", "after_date": MON}, MON, "a string window is still one day"),
    ({"day_window": 1.0, "after_date": MON}, MON, "a float window is still one day"),
    ({"day_window": 1, "after_date": f"{MON}T00:00:00"}, MON, "a timestamped date"),
    ({"day_window": 1, "after_date": MON, "date_hint": "monday around 12"}, MON,
     "a weekday that agrees"),
    ({"after_date": MON}, None, "after_date alone is 'not before' -- next week"),
    ({"day_window": 7, "after_date": MON}, None, "a week is not a named day"),
    ({"day_window": 2, "after_date": MON}, None, "two days is not one"),
    ({"day_window": True, "after_date": MON}, None, "a bool is not a window"),
    ({"day_window": 1}, None, "no date"),
    ({"day_window": 1, "after_date": "monday"}, None, "not an ISO date"),
    ({"day_window": 1, "after_date": "2026-09-31"}, None, "not a real date"),
    ({"day_window": 1, "after_date": "2026-09-21"}, None, "a day the payload does not hold"),
    ({"day_window": 1, "after_date": MON, "date_hint": "thursday around 12"}, None,
     "B-86: the weekday and the date disagree"),
    ({"day_window": "one", "after_date": MON}, None, "unparseable window"),
    (None, None, "no args"),
    ("nonsense", None, "junk args"),
])
def test_which_requests_name_one_day(args, expected, why):
    assert ls._day_named_by_lookup(args, _payload()) == expected, why


def test_a_named_day_with_no_bookable_times_names_nothing():
    """A day in the payload with nothing to book cannot be read out, and a
    refusal cannot claim it is full -- the real lookup owns that."""
    days = _payload()
    days[0] = dict(days[0], slot_times=[], slot_times_spoken=[], slots=[])
    assert ls._day_named_by_lookup(EXHIBIT_ARGS, days) is None


@pytest.mark.parametrize("days", [None, "nonsense", [], [None], [{"no": "date"}]])
def test_it_never_raises_on_a_broken_payload(days):
    assert ls._day_named_by_lookup(EXHIBIT_ARGS, days) is None


# ---------------------------------------------------------------------------
# The defect, through the helper the guard calls
# ---------------------------------------------------------------------------
def test_the_exhibit_is_answered_on_monday():
    session = _at_23_12_14()

    out = ls._already_retrieved_result(session, EXHIBIT_ARGS, EXHIBIT_WORDS)

    assert out["status"] == "already_retrieved", out
    assert {d for d, _ in _presented(out)} == {MON}, _presented(out)
    assert out.get("presentation_mode") == "single_day", out
    assert "presented_days" not in out, out


def test_the_exhibit_offers_midday():
    """What he asked for. 12:10 is ten minutes from noon and bookable."""
    out = ls._already_retrieved_result(_at_23_12_14(), EXHIBIT_ARGS, EXHIBIT_WORDS)

    assert (MON, "12:10") in _presented(out), _presented(out)


def test_before_n4_the_same_refusal_led_with_thursday():
    """The exhibit, reproduced: unscoped, the presenter leads with the days
    not heard. Pinned so the scoped result above is known to be the change and
    not a coincidence of the fixture."""
    session = _at_23_12_14()

    out = ls._presentation_for_refusal(session, session["available_days"])

    dates = [d for d, _ in _presented(out)]
    assert MON not in dates, dates
    assert dates[0] == THU, dates


def test_the_scoped_readout_repeats_nothing_heard_on_monday():
    """He asked for a time near noon, not to be re-read Monday -- this is a
    'what else'-shaped refusal and B-116's rule still holds inside it."""
    out = ls._already_retrieved_result(_at_23_12_14(), EXHIBIT_ARGS, EXHIBIT_WORDS)

    assert not {c for _, c in _presented(out)} & set(HEARD[MON]), _presented(out)


def test_every_time_is_a_real_monday_slot():
    out = ls._already_retrieved_result(_at_23_12_14(), EXHIBIT_ARGS, EXHIBIT_WORDS)

    assert _presented(out), out
    for date, clock in _presented(out):
        assert date == MON and clock in GRID, (date, clock)


def test_the_scoped_readout_is_capped():
    """B-118: a refusal is capped, scoped or not."""
    out = ls._already_retrieved_result(_at_23_12_14(), EXHIBIT_ARGS, EXHIBIT_WORDS)

    assert 0 < len(_presented(out)) <= 3, _presented(out)
    assert out["first_day"].get("more_times") is True, out["first_day"]


def test_the_mechanism_fired(caplog):
    with caplog.at_level(logging.INFO, logger=ls.logger.name):
        ls._already_retrieved_result(_at_23_12_14(), EXHIBIT_ARGS, EXHIBIT_WORDS)

    assert any("(N4)" in r.getMessage() for r in caplog.records), [
        r.getMessage() for r in caplog.records
    ]


def test_the_model_message_is_unchanged():
    """test_b118 scans the strings the model is shown; the refusal's wording
    moved into a helper and must not have changed on the way."""
    out = ls._already_retrieved_result(_at_23_12_14(), EXHIBIT_ARGS, EXHIBIT_WORDS)

    assert out["message"].startswith(
        "check_availability has already returned slot data. Do NOT call it again."
    ), out["message"]
    assert "first_day.slot_times_spoken" in out["message"]


# ---------------------------------------------------------------------------
# The requested time -- the second dropped input
# ---------------------------------------------------------------------------
def test_the_refusal_writes_the_callers_time():
    session = _at_23_12_14(**{REQUESTED_TIMES_KEY: []})    # the producer's stale []

    ls._already_retrieved_result(session, EXHIBIT_ARGS, EXHIBIT_WORDS)

    assert session[REQUESTED_TIMES_KEY] == ["12:00"], session[REQUESTED_TIMES_KEY]


def test_the_refusal_clears_a_stale_time():
    """Written EMPTY INCLUDED (S-13): a time named turns ago must not pin a slot
    into a readout that is not about it."""
    session = _at_23_12_14(**{REQUESTED_TIMES_KEY: ["13:00"]})

    ls._already_retrieved_result(session, {}, "what else have you got")

    assert session[REQUESTED_TIMES_KEY] == [], session[REQUESTED_TIMES_KEY]


def test_the_pin_now_reaches_a_time_the_selection_would_have_dropped(caplog):
    """D8 fires on a refusal. 08:50 was heard on TUESDAY, so S-2 would not
    choose it for Monday; the caller asking for nine is what puts it back."""
    session = _at_23_12_14()
    with caplog.at_level(logging.INFO):
        out = ls._already_retrieved_result(
            session, EXHIBIT_ARGS | {"date_hint": "around 9"},
            "as close as possible to 9 please",
        )

    assert (MON, "08:50") in _presented(out), _presented(out)
    assert any("(D8)" in r.getMessage() for r in caplog.records), [
        r.getMessage() for r in caplog.records
    ]


def test_no_words_writes_an_empty_time_and_still_refuses():
    session = _at_23_12_14(**{REQUESTED_TIMES_KEY: ["13:00"]})

    out = ls._already_retrieved_result(session, EXHIBIT_ARGS, None)

    assert out["status"] == "already_retrieved"
    assert session[REQUESTED_TIMES_KEY] == []


# ---------------------------------------------------------------------------
# What must NOT change
# ---------------------------------------------------------------------------
def test_an_unscoped_refusal_is_the_old_presentation():
    """No day_window: exactly what `_presentation_for_refusal` gave before."""
    session = _at_23_12_14()
    before = ls._presentation_for_refusal(session, session["available_days"])

    out = ls._already_retrieved_result(session, {"date_hint": "anything later"}, "")

    assert _presented(out) == _presented(before)
    assert out.get("presentation_mode") == before.get("presentation_mode")


@pytest.mark.parametrize("args", [
    {"day_window": 1, "after_date": "2026-09-21"},
    {"day_window": 7, "after_date": MON},
    {"day_window": 1, "after_date": MON, "date_hint": "thursday"},
])
def test_a_request_that_names_no_payload_day_is_unchanged(args):
    session = _at_23_12_14()
    before = ls._presentation_for_refusal(session, session["available_days"])

    out = ls._already_retrieved_result(session, args, "")

    assert _presented(out) == _presented(before), args


def test_available_days_stays_whole_when_scoped():
    """B-118/B-97: _resolve_slot_iso, DTMF and the unspoken follow-up read
    every bookable time. Scoping the READOUT must not trim the PAYLOAD."""
    session = _at_23_12_14()
    days = session["available_days"]

    out = ls._already_retrieved_result(session, EXHIBIT_ARGS, EXHIBIT_WORDS)

    assert out["available_days"] is days
    assert out["total_days"] == len(days) == 6
    assert [len(d["slot_times"]) for d in out["available_days"]] == \
        [len(d["slot_times"]) for d in _payload()]


def test_the_availability_arming_check_still_sees_a_payload():
    out = ls._already_retrieved_result(_at_23_12_14(), EXHIBIT_ARGS, EXHIBIT_WORDS)
    probe = {}
    assert ls._note_availability_seen(probe, out) is True


def test_the_session_payload_is_not_mutated():
    session = _at_23_12_14()
    ls._already_retrieved_result(session, EXHIBIT_ARGS, EXHIBIT_WORDS)

    assert [d["date"] for d in session["available_days"]] == [MON, TUE, WED, THU, FRI, SAT]
    assert session["available_days"][0]["slot_times"] == GRID


def test_a_later_named_day_is_never_called_the_earliest():
    """Scoped to one day, ANY day is the earliest of the list. A soonest caller
    who named Wednesday must not be told Wednesday is 'the earliest I have'."""
    session = _at_23_12_14(day_preference="as soon as possible")

    out = ls._presentation_for_refusal(
        session, session["available_days"], only_date=WED,
    )

    assert {d for d, _ in _presented(out)} == {WED}
    assert not out.get("lead_in"), out.get("lead_in")


def test_the_earliest_day_may_still_carry_its_true_lead_in():
    """The other side: when the named day IS the payload's earliest, the claim
    is true and is not taken away."""
    session = {"available_days": _payload(), "day_preference": "as soon as possible"}

    out = ls._presentation_for_refusal(
        session, session["available_days"], only_date=MON,
    )

    assert out.get("lead_in") == "earliest", out.get("lead_in")


def test_the_sparse_rota_note_is_decided_on_the_whole_payload(monkeypatch):
    """B-137's note is a claim about the DIARY; one day would look sparse."""
    seen = []

    def _spy(result, session, days):
        seen.append(len(days))
        return None

    monkeypatch.setattr(rt, "_sparse_rota_note", _spy)
    session = _at_23_12_14()

    out = ls._presentation_for_refusal(session, session["available_days"], only_date=MON)

    assert seen and seen[-1] == 6, seen
    assert "sparse_rota_note" not in out


def test_a_sparse_rota_note_true_of_the_whole_payload_survives(monkeypatch):
    note = {"kind": "sparse_rota", "days": ["Thursday"]}
    monkeypatch.setattr(rt, "_sparse_rota_note", lambda r, s, d: note if len(d) == 6 else None)
    session = _at_23_12_14()

    out = ls._presentation_for_refusal(session, session["available_days"], only_date=MON)

    assert out.get("sparse_rota_note") == note


def test_presentation_for_refusal_never_raises():
    for days, only in ((None, MON), ("nonsense", MON), ([], MON), ([None], MON),
                       ([{"date": MON}], MON), (_payload(), "junk"), (_payload(), 12)):
        ls._presentation_for_refusal({}, days, only_date=only)


def test_already_retrieved_result_never_raises():
    for session, args, words in (({}, None, None), ({"available_days": "x"}, "x", 12),
                                 ({"available_days": [None]}, EXHIBIT_ARGS, "")):
        out = ls._already_retrieved_result(session, args, words)
        assert out["status"] == "already_retrieved"


# ---------------------------------------------------------------------------
# N1 then N4 -- the two fixes on one call
# ---------------------------------------------------------------------------
def test_the_whole_call_after_both_fixes():
    """The 11 Sep call replayed end to end against both fixes: "how about
    monday" keeps Monday's offered times (N1); "as close as possible to 12"
    is then answered ON MONDAY, near noon, with nothing he has just been read
    (N4)."""
    days = _payload()
    spread = {MON: ["08:00", "17:10"], TUE: ["08:50", "16:20"], WED: ["09:40", "15:30"]}
    offered = [{"start": f"{d}T{t}:00+01:00", "end": "", "date": d}
               for d, ts in spread.items() for t in ts]
    session = {
        "available_days": days, "_slot_presentation_mode": "multi_day",
        "last_offered_slots": offered,
        "slot_labels": [LABELS[d] for d in spread],
        "v3_dtmf_slot_map": {"1": LABELS[MON], "2": LABELS[TUE], "3": LABELS[WED]},
    }
    record_spoken_slots(session, offered)

    assert try_unspoken_followup_speech(session, "how about monday")
    monday_readout = {str(s["start"])[11:16] for s in session["last_offered_slots"]}
    assert {"08:00", "17:10"} <= monday_readout, monday_readout

    out = ls._already_retrieved_result(session, EXHIBIT_ARGS, EXHIBIT_WORDS)

    presented = _presented(out)
    assert {d for d, _ in presented} == {MON}, presented
    assert (MON, "12:10") in presented, presented
    assert not {c for _, c in presented} & monday_readout, (monday_readout, presented)


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------
def test_the_guard_hands_the_helper_the_request_and_the_words():
    """Pinned by source: a helper whose call site is deleted still passes every
    behavioural test above."""
    src = inspect.getsource(ls)
    assert "result = _already_retrieved_result(session, args, _user)" in src


def test_every_refusal_that_carries_diary_data_still_uses_the_helper():
    """B-118's count, restated here because this commit moved one of them."""
    src = inspect.getsource(ls)
    assert src.count("_presentation_for_refusal(") >= 4
