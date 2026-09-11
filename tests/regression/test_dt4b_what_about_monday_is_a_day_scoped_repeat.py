"""
Regression: "what about Monday" after Monday has been read out is that
readout again, verbatim -- a REPEAT scoped to a day.

Spec rows DT-4 / DT-4b; owner decision D-q (2026-09-11); invariants 7, 10, 16.

Until D-q, N1's keep-rule (`_keep_times_heard_on_named_day`) declined once
three times had been heard on the day, and B-116 then withheld all three:
heard 08:00 / 08:50 / 09:40 -> re-read 10:30 / 11:20 / 16:20, zero overlap,
on both diary shapes that can reach it (scorer DT-4b, 11 Sep). N1's own
complaint, one step further along the same call: the caller cannot tell
whether the first three are gone.

D-q: "what about <day>" means "tell me about <day>" whatever the count. The
most recent thing Susie said about that day is said again, byte for byte;
novelty is reached by "what else on <day>", which writes a new entry. DT-4
(two heard from the week menu: keep them, fill to three) is unchanged --
nothing has been READ OUT for the day yet, so there is nothing to repeat.

Recorded per day by `apply_offer_to_session` (`LAST_READOUT_BY_DAY_KEY`),
keyed on the date the readout's slots share -- not `day_iso`, which is the
conversation's anchor and stays on Monday while "what else" reads Thursday.
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
LABEL = {MON: "Monday 14th September", TUE: "Tuesday 15th September",
         WED: "Wednesday 16th September", THU: "Thursday 17th September"}


def _day(date, times):
    return {
        "date": date,
        "day_label": LABEL[date],
        "slot_times": list(times),
        "slot_times_spoken": [_spoken_slot_time(t) for t in times],
        "slots": [{"start": f"{date}T{t}:00+01:00"} for t in times],
    }


def _week():
    days = [_day(d, GRID) for d in (MON, TUE, WED, THU)]
    s = {"clinic_id": "northgate", "available_days": days}
    first = build_slot_offer(days[:3], pretrimmed=False)
    assert first.mode == "multi_day"
    apply_offer_to_session(s, offer_as_record(first), first.chunks)
    s["_slot_presentation_mode"] = first.mode
    return s


def _say(s, text):
    guard.note_caller_speech(s, text)
    return sf.try_unspoken_followup_speech(s, text)


# ── the rule ───────────────────────────────────────────────────────────────

def test_what_about_monday_after_mondays_readout_is_that_readout_verbatim():
    s = _week()
    readout = _say(s, "could you tell me about monday please")
    assert readout and readout.startswith("Monday 14th September")
    keypad = dict(s["v3_dtmf_slot_map"])
    heard = list(s[sf._SPOKEN_KEY])
    again = _say(s, "um what about monday")
    assert again == readout
    assert dict(s["v3_dtmf_slot_map"]) == keypad           # inv. 10
    assert list(s[sf._SPOKEN_KEY]) == heard                # inv. 16
    assert guard.check_outgoing(s, again).clean


@pytest.mark.parametrize("said", [
    "what about monday",
    "and monday",
    "tell me about monday again",
    "what have you got on monday",
])
def test_every_wording_of_the_same_question_gets_the_same_answer(said):
    s = _week()
    readout = _say(s, "tell me about monday")
    assert _say(s, said) == readout, said


def test_the_most_recent_thing_said_about_the_day_is_what_repeats():
    """A second Monday readout (here forced through the producer with the
    first three already heard, the pre-D-q shape) replaces the record: 'what
    about Monday' now repeats THAT one, not the first."""
    s = _week()
    first = _say(s, "tell me about monday")
    # Wipe the record and re-read Monday cold, so the selector runs again and
    # produces a different three -- the "what else on Monday" outcome without
    # depending on N6's routing.
    s.pop(sf.LAST_READOUT_BY_DAY_KEY)
    second = sf.speak_one_day_from_payload(
        s, s["available_days"], MON, why="test", user_text="what else on monday")
    assert second and second != first
    assert _say(s, "what about monday") == second


def test_each_day_keeps_its_own_record():
    s = _week()
    mon = _say(s, "tell me about monday")
    tue = _say(s, "what about tuesday")
    assert tue.startswith("Tuesday 15th September") and tue != mon
    assert _say(s, "and what about monday") == mon
    assert _say(s, "what about tuesday") == tue


def test_on_a_single_day_offer_the_same_question_repeats_the_offer():
    """Before D-q the named-day producer declined on single_day and the turn
    went to the model."""
    days = [_day(MON, GRID)]
    s = {"clinic_id": "northgate", "available_days": days}
    first = build_slot_offer(days, pretrimmed=False)
    assert first.mode == "single_day"
    apply_offer_to_session(s, offer_as_record(first), first.chunks)
    s["_slot_presentation_mode"] = first.mode
    assert _say(s, "what about monday") == first.text


# ── what it must not touch ─────────────────────────────────────────────────

def test_dt4_two_heard_from_the_week_menu_still_keeps_them_and_fills():
    """Nothing has been READ OUT for Monday yet: DT-4 as it stands."""
    s = _week()
    week_mon = [o["start"][11:16] for o in s["last_offered_slots"] if o["start"].startswith(MON)]
    assert len(week_mon) == 1          # positional: one per day on multi_day
    heard_mon = sorted(t[11:16] for t in s[sf._SPOKEN_KEY] if t.startswith(MON))
    assert len(heard_mon) == 2
    out = _say(s, "tell me about monday")
    for t in heard_mon:
        assert _spoken_slot_time(t) in out, (t, out)


def test_a_narrowed_ask_is_a_new_selection_not_a_repeat():
    """A band word narrows the ask; the repeat declines and the selection
    rules answer. ("monday afternoon" here would be read by the accept reader
    as a pick of the one afternoon time on the offer -- a different, earlier
    door -- so the probe names the band with two offered times in it.)"""
    s = _week()
    readout = _say(s, "tell me about monday")
    assert sf._day_readout_to_repeat(
        s, s["available_days"][0], "what about monday morning") is None
    band = _say(s, "what about monday morning")
    assert band and band != readout and band.startswith("Monday 14th September")


def test_a_time_named_with_the_day_goes_to_the_resolver_not_the_repeat():
    s = _week()
    _say(s, "tell me about monday")
    out = _say(s, "what about monday around twelve")
    assert "ten past twelve" in out and "Number 1" not in out


def test_a_readout_whose_time_has_left_the_diary_is_not_repeated():
    s = _week()
    readout = _say(s, "tell me about monday")
    first_start = s["last_offered_slots"][0]["start"]
    gone = first_start[11:16]
    # The diary moves: that slot is booked by someone else.
    mon = s["available_days"][0]
    idx = mon["slot_times"].index(gone)
    for k in ("slot_times", "slot_times_spoken", "slots"):
        mon[k] = [v for i, v in enumerate(mon[k]) if i != idx]
    out = _say(s, "what about monday")
    assert out and out != readout
    assert _spoken_slot_time(gone) not in out
    assert guard.check_outgoing(s, out).clean


@pytest.mark.parametrize("said", [
    "what else have you got",
    "anything on a different day",
    "monday doesn't work",
    "monday at ten past five please",
])
def test_these_are_not_a_day_scoped_repeat(said):
    s = _week()
    readout = _say(s, "tell me about monday")
    assert _say(s, said) != readout, said


def test_the_record_is_keyed_on_the_day_the_readout_is_of():
    """`day_iso` is the anchor; a Thursday readout under a Monday anchor is
    filed under Thursday."""
    s = _week()
    days = s["available_days"]
    thu = build_slot_offer([days[3]], pretrimmed=False)
    apply_offer_to_session(s, offer_as_record(thu, day_iso=MON), thu.chunks)
    assert set(s[sf.LAST_READOUT_BY_DAY_KEY]) == {THU}
