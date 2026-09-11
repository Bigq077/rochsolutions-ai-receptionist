"""
Regression: the hour word inside "<minutes> past/to <hour>" is a component,
not a second time. "five past eight in the morning works" is 08:05, and only
08:05.

Found 2026-09-11 by the DT-21 scorer row on `uniform50`, the demo line's
diary. Week menu read (Monday, Tuesday, Wednesday -- eight in the morning
on each), then "tell me about Monday", then "five past eight in the morning
works". `_candidate_hhmm_from_text` emitted 08:00 for the "eight" -- it is
followed by "in the morning", a strong marker -- alongside the 08:05 that
`requested_clock_times` read. 08:05 sits on no day. Monday's, Tuesday's and
Wednesday's eight had been heard; Thursday's had not; and the whole-sweep
resolver answered:

    "The nearest I've got to five past eight in the morning is eight in the
     morning on Thursday 17th September. Shall I book that in for you?"

to a caller discussing Monday. N4's shape (88f801f5), through a component
read as a whole. The same read makes "ten to twelve works" carry 12:00 and
"half past three works" carry 15:00.

Sibling of B-114: the bare-hour pass is now run on the text with every
relative-time phrase removed, so the only reader of "five past eight" is the
parser that understands it.
"""
from __future__ import annotations

import pytest

from app.tools import slot_fact_guard as guard
from app.tools import slot_followup as sf
from app.tools.receptionist_tools import _spoken_slot_time
from app.tools.slot_offer import (
    apply_offer_to_session,
    build_slot_offer,
    offer_as_record,
)

MON, TUE, WED, THU = "2026-09-14", "2026-09-15", "2026-09-16", "2026-09-17"
GRID = ["08:00", "08:50", "09:40", "10:30", "11:20", "12:10", "13:00",
        "13:50", "14:40", "15:30", "16:20"]


def _day(date, label, times):
    return {
        "date": date,
        "day_label": label,
        "slot_times": list(times),
        "slot_times_spoken": [_spoken_slot_time(t) for t in times],
        "slots": [{"start": f"{date}T{t}:00+01:00"} for t in times],
    }


# ── the parser ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("said, not_wanted", [
    ("five past eight in the morning works", "08:00"),
    ("ten to twelve works", "12:00"),
    ("twenty past eleven works", "11:00"),
    ("half past three works", "15:00"),
    ("quarter to four works", "16:00"),
    ("at ten to twelve", "10:00"),          # "at ten" is not a time here either
    ("half three works", "15:00"),          # UK 15:30; 15:00 is simply wrong
])
def test_the_hour_component_is_not_emitted_as_a_time(said, not_wanted):
    assert not_wanted not in sf._candidate_hhmm_from_text(said)


@pytest.mark.parametrize("said, wanted", [
    ("half past three works", "15:30"),
    ("quarter to four works", "15:45"),
    ("eight in the morning works", "08:00"),
    ("at eight", "08:00"),
    ("does one work", "13:00"),
    ("not the one after, the one o'clock", "13:00"),
])
def test_a_whole_time_still_reads(said, wanted):
    assert wanted in sf._candidate_hhmm_from_text(said)


# ── the sweep ──────────────────────────────────────────────────────────────

def _week_then_monday():
    days = [_day(MON, "Monday 14th September", GRID),
            _day(TUE, "Tuesday 15th September", GRID),
            _day(WED, "Wednesday 16th September", GRID),
            _day(THU, "Thursday 17th September", GRID)]
    s = {"clinic_id": "northgate", "available_days": days}
    first = build_slot_offer(days[:3], pretrimmed=False)
    apply_offer_to_session(s, offer_as_record(first), first.chunks)
    s["_slot_presentation_mode"] = first.mode
    assert first.mode == "multi_day"
    monday = sf.try_unspoken_followup_speech(s, "tell me about monday")
    assert monday and "Monday" in monday
    return s


def test_five_past_eight_on_monday_is_not_answered_with_thursday():
    s = _week_then_monday()
    said = "five past eight in the morning works"
    guard.note_caller_speech(s, said)
    out = sf.try_unspoken_followup_speech(s, said) or ""
    assert "Thursday" not in out, out
    assert "nearest I've got" not in out, out
    # What IS said to 08:05 -- nobody's time, five minutes off Monday's
    # eight -- is DT-21's (test_dt21_*): a question naming Monday's eight.


def test_the_resolver_alone_finds_nothing_for_five_past_eight():
    s = _week_then_monday()
    remaining = sf.remaining_unspoken(s)
    assert sf.resolve_requested_time(
        "five past eight in the morning works", remaining, s["available_days"]
    ) is None
