"""
Regression: a caller who names a time gets ONE slot, on every path.

Spec rows DT-7 and DT-8; owner decision 12 Sep 2026 ("I prefer just giving
one slot rather than three general slots"); invariants 4, 10, 20.

Three shapes, from three calls:

1. THE HEARD TIME. Theorem CAf87ed571, 12 Sep 12:11. Monday had been read as
   nine, midday and four; the caller asked "anything around 12 on that day".
   12:00 was bookable, already spoken, and therefore invisible to a resolver
   that searched only UNSPOKEN times. The model answered -- correctly -- but
   nothing recorded its offer, and the keypad map still said {1: one,
   2: three} while Susie was offering twelve: a keypress would have booked
   one o'clock (inv. 10, the B-80 shape).

2. THE TOOL PATH. Demo CA778651b7, 11 Sep 08:44. "anything around 12"
   triggered a lookup; the executor built the ordinary three-slot readout
   and D8 pinned the asked time INTO it: "10:30, 11:20, 12:10", asked time
   third. The payload-answered producer for the same question speaks one
   slot. Same act, two answers (inv. 20).

3. THE DISTANCE. "around 12" against a day whose nearest is 12:50 fell
   outside the resolver's 20-minute tolerance and went to the model. The
   nearest bookable time, said honestly, is the answer whatever the distance
   -- for a round time, on a day the caller chose, ties declining (DT-9).
"""
from __future__ import annotations

import pytest

from app.media_streams.llm_stream import _named_time_offer
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
        "13:50", "14:40", "15:30", "16:20", "17:10"]


def _day(date, label, times):
    return {
        "date": date,
        "day_label": label,
        "slot_times": list(times),
        "slot_times_spoken": [_spoken_slot_time(t) for t in times],
        "slots": [{"start": f"{date}T{t}:00+01:00"} for t in times],
    }


def _session(days, readout_days=None):
    s = {"clinic_id": "northgate", "available_days": days}
    offer = build_slot_offer(readout_days or days[:3], pretrimmed=False)
    apply_offer_to_session(s, offer_as_record(offer), offer.chunks)
    s["_slot_presentation_mode"] = offer.mode
    return s


def _say(s, text):
    guard.note_caller_speech(s, text)
    return sf.try_unspoken_followup_speech(s, text)


# ── 1. the heard time (Theorem, 12:11) ─────────────────────────────────────

def _theorem():
    return [
        _day(MON, "Monday 14th September", ["09:00", "12:00", "13:00", "15:00", "16:00"]),
        _day(TUE, "Tuesday 15th September", ["11:00", "12:00", "16:00"]),
        _day(WED, "Wednesday 16th September", ["16:00"]),
    ]


def test_the_12_11_turn_is_answered_by_a_producer_with_the_heard_time():
    s = _session(_theorem())
    _say(s, "um could you tell me about monday please")      # 9, midday, 4
    _say(s, "yeah what else have you got on monday")         # 1, 3
    out = _say(s, "um so do you have anything around 12 on that day")
    assert out == ("Yes — midday on Monday 14th September is free. "
                   "Shall I book that in for you?")


def test_the_keypad_cannot_book_one_oclock_after_twelve_was_offered():
    """The defect's consequence: on the call, {1: one, 2: three} outlived the
    model's twelve. A producer narrows the offer and supersedes the map."""
    s = _session(_theorem())
    _say(s, "tell me about monday")
    _say(s, "what else have you got on monday")
    assert s["v3_dtmf_slot_map"] == {"1": "one in the afternoon", "2": "three in the afternoon"}
    _say(s, "anything around 12 on that day")
    assert [o["start"][:16] for o in s["last_offered_slots"]] == [f"{MON}T12:00"]
    assert s.get("v3_slot_map_superseded")


def test_the_one_slot_answer_is_repeatable_and_recorded_as_heard():
    s = _session(_theorem())
    _say(s, "tell me about monday")
    one = _say(s, "anything around 12 on that day")
    assert _say(s, "say that again") == one, "D-o: a one-slot answer is a readout too"
    assert f"{MON}T12:00:00" in s[sf._SPOKEN_KEY]


def test_heard_is_a_novelty_concern_and_relevance_wins():
    """Precedence level 3 over level 4: the time they asked for beats the
    rule that would withhold it for having been said."""
    days = _theorem()
    s = _session(days)
    _say(s, "tell me about monday")
    scoped = sf.bookable_on_current_day(s, "anything around 12")
    assert "12:00" in [x["time"] for x in scoped]
    unspoken = sf.remaining_unspoken_on_current_day(s, "anything around 12")
    assert "12:00" not in [x["time"] for x in unspoken]


# ── 2. the tool path (demo, 08:44) ─────────────────────────────────────────

def _week():
    return [_day(d, l, GRID) for d, l in (
        (MON, "Monday 14th September"), (TUE, "Tuesday 15th September"),
        (WED, "Wednesday 16th September"), (THU, "Thursday 17th September"))]


def _executor_result(days, presented):
    """The shape `check_availability` hands `_flush_slot_buf`: the full days,
    plus the trimmed days it chose to present."""
    return {"available_days": days, "presented_days": presented,
            "presentation_mode": "multi_day" if len(presented) > 1 else "single_day"}


def _trim(day, times):
    x = dict(day)
    x["slot_times"] = list(times)
    x["slot_times_spoken"] = [_spoken_slot_time(t) for t in times]
    x["slots"] = [s for s in day["slots"] if s["start"][11:16] in times]
    return x


def test_a_lookup_for_a_named_time_returns_one_slot_not_a_pinned_readout():
    days = _week()
    presented = [_trim(days[0], ["08:00", "17:10"]), _trim(days[1], ["08:50", "16:20"]),
                 _trim(days[2], ["09:40", "15:30"])]
    s = {"clinic_id": "northgate", "available_days": days}
    s[sf.REQUESTED_TIMES_KEY] = sf.requested_clock_times("around 12")    # what D8 writes
    offer = _named_time_offer(s, _executor_result(days, presented), presented)
    assert offer is not None and offer.mode == "one_slot"
    assert offer.text == ("The nearest I've got to midday is ten past twelve in the "
                          "afternoon on Monday 14th September. Shall I book that in for you?")
    assert offer.dtmf_map == {} and len(offer.slots) == 1


def test_the_full_day_is_searched_not_the_trimmed_presentation():
    """12:10 is not among the two times the executor chose to present for
    Monday; it is on Monday's diary. The named time is found there."""
    days = _week()
    presented = [_trim(days[0], ["08:00", "17:10"])]
    s = {"clinic_id": "northgate", "available_days": days}
    s[sf.REQUESTED_TIMES_KEY] = ["12:00"]
    offer = _named_time_offer(s, _executor_result(days, presented), presented)
    assert offer is not None and offer.slots[0]["start"].startswith(f"{MON}T12:10")


def test_with_no_time_named_the_readout_is_untouched():
    days = _week()
    presented = [_trim(days[0], ["08:00", "17:10"])]
    s = {"clinic_id": "northgate", "available_days": days}
    s[sf.REQUESTED_TIMES_KEY] = []
    assert _named_time_offer(s, _executor_result(days, presented), presented) is None


def test_the_one_slot_offer_records_like_any_other_offer():
    """Through the one writer: the offer on the table is that slot, no
    keypad map is armed, and 'say that again' repeats the sentence."""
    days = _week()
    presented = [_trim(days[0], ["08:00", "17:10"])]
    s = {"clinic_id": "northgate", "available_days": days}
    s[sf.REQUESTED_TIMES_KEY] = ["12:00"]
    offer = _named_time_offer(s, _executor_result(days, presented), presented)
    apply_offer_to_session(s, offer_as_record(offer), offer.chunks)
    assert [o["start"][:16] for o in s["last_offered_slots"]] == [f"{MON}T12:10"]
    assert "v3_dtmf_slot_map" not in s
    assert sf.try_unspoken_followup_speech(s, "say that again") == offer.text


# ── 3. the distance (DT-8, far) ────────────────────────────────────────────

def test_a_round_time_far_from_everything_gets_the_nearest_said_honestly():
    """Monday holds 08:00, 10:30, 17:10; 'around 12' is 90 minutes from the
    nearest. One slot, named as the nearest -- not the model."""
    days = [_day(MON, "Monday 14th September", ["08:00", "10:30", "17:10"])]
    s = _session(days)
    out = _say(s, "anything around 12")
    assert out == ("The nearest I've got to midday is half past ten in the morning "
                   "on Monday 14th September. Shall I book that in for you?")


def test_far_applies_only_to_a_day_the_caller_chose():
    """After a Mon/Tue/Wed menu with 12:10 on THURSDAY only, 'around 12' must
    not be answered with Monday's nearest -- Monday leads the menu by
    construction, not by choice. The whole-sweep pass finds Thursday."""
    days = [_day(MON, "Monday 14th September", ["08:00", "10:30", "17:10"]),
            _day(THU, "Thursday 17th September", ["09:00", "12:10", "16:20"])]
    s = _session(days)
    out = _say(s, "anything around 12")
    assert "Thursday 17th September" in out and "ten past twelve" in out


def test_a_tie_still_declines():
    """DT-9: 14:00 sits exactly between 13:00 and 15:00 on Theorem's Monday."""
    s = _session(_theorem())
    _say(s, "tell me about monday")
    assert _say(s, "anything around 2") is None


def test_a_twin_cannot_reach_across_the_day():
    """'at 2' is 02:00 and 14:00. Far must not let 02:00 find 08:00."""
    days = [_day(MON, "Monday 14th September", ["08:00", "13:50", "17:10"])]
    s = _session(days)
    out = _say(s, "what about 2 o'clock")
    assert "ten to two" in out and "eight in the morning" not in out


def test_a_non_round_time_does_not_drift_far():
    """Invariant 4: 'nine forty-one' is a time read off a diary. It matches
    exactly or not at all."""
    days = [_day(MON, "Monday 14th September", ["08:00", "10:30", "17:10"])]
    s = _session(days)
    assert _say(s, "anything at 9:41") is None


def test_the_guard_passes_every_one_slot_sentence():
    for said, days in (
        ("anything around 12", [_day(MON, "Monday 14th September", ["08:00", "10:30", "17:10"])]),
        ("anything around 12 on that day", _theorem()),
    ):
        s = _session(days)
        _say(s, "tell me about monday")
        out = _say(s, said)
        assert out, said
        assert guard.check_outgoing(s, out).clean, out
