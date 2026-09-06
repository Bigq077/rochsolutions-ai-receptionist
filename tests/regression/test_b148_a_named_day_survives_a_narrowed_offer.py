"""B-148 — "check for Tuesday" must still work after the offer has narrowed.

`CAdf1e02ca0ce4e83e9e02abf65bcacb02`, northgate, 2026-09-06 10:15:23, build
`cf592b11a2fa`. Susie had just narrowed to Monday. The caller said "um check for
tuesday please" and D-B did not fire at all:

    10:15:24  situational head (named_day): 'Let me have a look at Tuesday for you —'
    10:15:33  second filler phrase (genuine stall, 10.0s since dispatch)
    10:15:37  LAT turn_seq=12 llm_ttft_ms=12876 content_ttfa_ms=13700
              "from the data I already have for Tuesday the 8th — I've got ten
               past nine in the morning, or twenty past five in the evening"

Two of Tuesday's times, **thirteen seconds**, and the model's own internal
phrasing — "from the data I already have" — spoken to a caller. The head was
correct and there was nothing behind it: D-B's original defect, arriving again
because the producer could not resolve the day.

WHY. The resolution ladder was `day_named_by_caller` (needs the payload's FULL
label, so a bare "tuesday" is a partial and matches nothing) then
`_offered_day_by_weekday` (a bare weekday against `last_offered_slots`). The
second is right for a PICK — you can only choose from what was read out — and
wrong for a REQUEST. The turn before had narrowed the offer to Monday, so every
other day the clinic had became unreachable by name.

B-145 makes this commoner, not rarer: a day ACCEPTANCE now narrows too, so the
state that hides the rest of the week is reached by the most ordinary turn in
the call.

`_payload_day_by_weekday` resolves against the payload the caller's own lookup
returned. That is a closed candidate set, not the calendar, so it is not the
date parsing Tier 2 needs — and it declines on ambiguity, because a fortnight
holds two Tuesdays and weekday names repeat every seven days.
"""
from __future__ import annotations

import pytest

from app.tools.slot_followup import (
    _payload_day_by_weekday,
    day_refused_by_caller,
    named_day_speech,
    record_spoken_slots,
    try_unspoken_followup_speech,
)

TIMES = ["08:00", "08:50", "09:40", "10:30", "11:20", "12:10",
         "13:00", "13:50", "14:40", "15:30", "16:20", "17:10"]
SPOKEN = ["eight in the morning", "ten to nine in the morning",
          "twenty to ten in the morning", "half past ten in the morning",
          "twenty past eleven in the morning", "ten past twelve",
          "one in the afternoon", "ten to two in the afternoon",
          "twenty to three in the afternoon", "half past three in the afternoon",
          "twenty past four in the afternoon", "ten past five in the evening"]


def _day(date, label):
    return {
        "date": date, "day_label": label,
        "slot_times": list(TIMES), "slot_times_spoken": list(SPOKEN),
        "times_not_shown": 0,
        "slots": [{"start": f"{date}T{t}:00+01:00", "end": ""} for t in TIMES],
    }


WEEK = [
    _day("2026-09-07", "Monday 7th September"),
    _day("2026-09-08", "Tuesday 8th September"),
    _day("2026-09-09", "Wednesday 9th September"),
    _day("2026-09-10", "Thursday 10th September"),
    _day("2026-09-11", "Friday 11th September"),
    _day("2026-09-12", "Saturday 12th September"),
]
READ_OUT = WEEK[:3]


def _after_multi_day_readout(days=None):
    session = {
        "clinic_id": "northgate",
        "available_days": list(days if days is not None else WEEK),
        "_slot_presentation_mode": "multi_day",
        "last_offered_slots": [
            {"start": f"{d['date']}T08:00:00+01:00", "end": ""} for d in READ_OUT
        ],
        "slot_labels": [d["day_label"] for d in READ_OUT],
        "v3_dtmf_slot_map": {
            "1": "Monday 7th September",
            "2": "Tuesday 8th September",
            "3": "Wednesday 9th September",
        },
    }
    record_spoken_slots(session, [
        {"start": f"{d['date']}T{t}:00+01:00", "spoken": sp, "date": d["date"]}
        for d in READ_OUT
        for t, sp in zip(("08:00", "17:10"),
                         ("eight in the morning", "ten past five in the evening"))
    ])
    return session


# ── The live sequence ───────────────────────────────────────────────────────

def test_a_named_day_still_resolves_after_the_offer_narrows():
    """The defect, in the two turns that produced it."""
    session = _after_multi_day_readout()

    first = try_unspoken_followup_speech(session, "uh check for monday please")
    assert first and "Monday" in first
    dates = {str((o or {}).get("start") or "")[:10]
             for o in session.get("last_offered_slots") or []}
    assert dates == {"2026-09-07"}, "the offer did not narrow — fixture drift"

    second = try_unspoken_followup_speech(session, "um check for tuesday please")
    assert second, "still falls through to the model"
    assert "Tuesday" in second, second


def test_a_named_day_resolves_after_a_refusal_narrows_the_offer():
    """B-147 moves the offer onto the unheard days; the same gap follows it."""
    session = _after_multi_day_readout()
    assert try_unspoken_followup_speech(session, "monday doesn't work")

    spoken = try_unspoken_followup_speech(session, "um check for tuesday please")
    assert spoken and "Tuesday" in spoken, spoken


@pytest.mark.parametrize("utterance,label", [
    ("um check for tuesday please", "Tuesday"),
    ("what about friday", "Friday"),
    ("can you do saturday", "Saturday"),
    ("is thursday free", "Thursday"),
])
def test_any_payload_day_is_reachable_by_name(utterance, label):
    """A day the clinic has, that was never read out, is still askable."""
    session = _after_multi_day_readout()
    try_unspoken_followup_speech(session, "uh check for monday please")

    spoken = try_unspoken_followup_speech(session, utterance)
    assert spoken and label in spoken, f"{utterance!r} -> {spoken!r}"


# ── Deny by default ─────────────────────────────────────────────────────────

def test_a_repeated_weekday_in_the_payload_declines():
    """A fortnight holds two Tuesdays. Guessing the nearer is exactly the
    mistake `weekday names repeat every seven days` records."""
    fortnight = WEEK + [_day("2026-09-15", "Tuesday 15th September")]
    assert _payload_day_by_weekday(fortnight, "check for tuesday please") is None


def test_two_weekdays_named_is_a_comparison_not_a_request():
    assert _payload_day_by_weekday(WEEK, "is it tuesday or wednesday") is None


@pytest.mark.parametrize("bad", [None, [], [{}], "not a list"])
def test_a_missing_or_malformed_payload_declines(bad):
    assert _payload_day_by_weekday(bad, "check for tuesday") is None


def test_a_day_with_no_bookable_times_is_not_offered():
    empty = [dict(d, slot_times=[]) if d["date"] == "2026-09-11" else d
             for d in WEEK]
    assert _payload_day_by_weekday(empty, "what about friday") is None


def test_a_refused_day_is_still_never_read_out_through_this_step():
    """B-147's guard sits ABOVE the ladder, so widening the ladder must not
    reopen it — the refusal declines before any day is resolved."""
    session = _after_multi_day_readout()
    try_unspoken_followup_speech(session, "uh check for monday please")

    assert named_day_speech(session, "tuesday doesn't work") is None
    spoken = try_unspoken_followup_speech(session, "tuesday doesn't work") or ""
    assert "Tuesday" not in spoken, spoken


def test_the_offer_still_wins_over_the_payload():
    """The new step is LAST. While a day is on the table, the offer resolves it
    — a pick must keep being answered from what was actually read out."""
    session = _after_multi_day_readout()
    spoken = try_unspoken_followup_speech(session, "what about wednesday")
    assert spoken and "Wednesday" in spoken, spoken
