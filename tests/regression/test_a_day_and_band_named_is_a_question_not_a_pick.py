"""
Regression: "what about Monday morning" is a REQUEST for Monday's mornings,
answered with Monday's mornings -- not an acceptance of the one morning
time on the offer.

Spec rows DT-10 (band has times: those only), DT-11 (band empty: say so,
then open the day); B-147's sibling.

CAf80eb02d, northgate demo line, build 83f47aad, 11 Sep 2026 10:48. Monday
had been read at eight, twenty to three and ten past five:

    caller: "what about monday morning"
    log   : [ms_conn v3] caller ACCEPTED 2026-09-14T08:00:00+01:00
            ('what about monday morning') -- pinned into any readout (P6b)
    Susie : "Monday morning it is -- on Monday the 14th I've got eight in
             the morning -- does that work?"                  (model, 3.4 s)

Monday held five mornings. `slot_accepted_by_caller`'s band fallback picks
FOR the caller when exactly one offered time sits in the band they named,
and never asked whether they were choosing or asking. `_DAY_REQUEST_RE`
existed one function down to keep exactly this shape out of
`day_accepted_by_caller`; the band fallback now consults it. Explicit label
picks ("what about monday at eight") are untouched.

The question then reaches the named-day producer, which had no band
handling at all -- a band only ever reached the diary through the model's
tool call. It now narrows to the band (DT-10); with the band empty or fully
heard it says so and opens the rest of the day (DT-11). Both also reach the
producer on a single-day offer and on a day whose every time has been heard,
neither of which previously did.
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
        "13:50", "14:40", "15:30", "16:20", "17:10"]
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


def _offer(s, days_shown, mode=None):
    off = build_slot_offer(days_shown, pretrimmed=False)
    apply_offer_to_session(s, offer_as_record(off), off.chunks)
    s["_slot_presentation_mode"] = mode or off.mode
    return off


def _the_10_48_table():
    """Monday's full grid; 08:00 / 14:40 / 17:10 on the table, as at 10:48."""
    days = [_day(d, GRID) for d in (MON, TUE, WED, THU)]
    s = {"clinic_id": "northgate", "available_days": days}
    _offer(s, [_day(MON, ["08:00", "14:40", "17:10"])], mode="multi_day")
    return s


def _say(s, text):
    guard.note_caller_speech(s, text)
    return sf.try_unspoken_followup_speech(s, text)


def _times_in(text, date=MON):
    return [t for t in GRID if _spoken_slot_time(t) in (text or "")]


def _band(t):
    return sf.part_of_day(f"{MON}T{t}:00")


# ── the call ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("said", [
    "what about monday morning",                 # 10:48, verbatim
    "have you got anything monday morning",
    "is there anything on monday in the morning",
    "what's free monday morning",
])
def test_a_day_and_band_in_a_question_is_not_a_pick(said):
    s = _the_10_48_table()
    assert sf.slot_accepted_by_caller(s, said) is None, said
    assert sf.day_accepted_by_caller(s, said) is None, said


@pytest.mark.parametrize("said", [
    "monday morning works",
    "the morning one please",
    "yeah monday morning",
])
def test_a_band_that_chooses_still_picks_the_one_offered_time(said):
    s = _the_10_48_table()
    assert sf.slot_accepted_by_caller(s, said) == f"{MON}T08:00:00+01:00", said


def test_a_day_and_an_explicit_time_in_a_question_still_names_that_slot():
    s = _the_10_48_table()
    assert sf.slot_accepted_by_caller(s, "what about monday at eight") == f"{MON}T08:00:00+01:00"


# ── DT-10: the band, from the producer ─────────────────────────────────────

def test_what_about_monday_morning_reads_mondays_mornings():
    s = _the_10_48_table()
    out = _say(s, "what about monday morning")
    assert out and out.startswith("Monday 14th September"), out
    times = _times_in(out)
    assert len(times) == 3 and all(_band(t) == "morning" for t in times), times
    assert "08:00" in times                       # N1: the heard morning kept
    assert guard.check_outgoing(s, out).clean


def test_the_afternoon_likewise():
    s = _the_10_48_table()
    out = _say(s, "have you got anything monday afternoon")
    times = _times_in(out)
    assert len(times) == 3 and all(_band(t) == "afternoon" for t in times), out


@pytest.mark.parametrize("said", [
    "what about monday at one in the afternoon",
    "what about monday at 2pm in the afternoon",
])
def test_a_band_that_qualifies_a_clock_time_is_not_a_band_request(said):
    """Found by N1's D8 test in the full suite: narrowing to 'afternoon'
    evicted the heard morning N1 keeps. A clock time is D8's, not DT-10's."""
    assert sf._band_requested(said) is None, said
    assert sf._band_requested("what about monday afternoon") == "afternoon"


def test_a_band_answer_still_admits_the_rest_of_the_day():
    s = _the_10_48_table()
    out = _say(s, "what about monday morning")
    assert "a few others that day" in out


def test_a_band_answer_is_not_a_day_scoped_repeat():
    """D-q: a band word narrows the ask, so the verbatim repeat declines."""
    s = _the_10_48_table()
    first = _say(s, "tell me about monday")
    out = _say(s, "what about monday morning")
    assert out != first and all(_band(t) == "morning" for t in _times_in(out))


# ── DT-11: the band is empty, or all heard ─────────────────────────────────

def test_a_band_the_day_lacks_is_said_so_and_the_day_opened():
    """A day with no evening at all (17:10 IS an evening to `part_of_day`,
    so the grid is cut at 16:20 here)."""
    day_times = [t for t in GRID if _band(t) != "evening"]
    days = [_day(MON, day_times), _day(TUE, day_times)]
    s = {"clinic_id": "northgate", "available_days": days}
    _offer(s, days)
    out = _say(s, "what about monday evening")
    assert out.startswith("I've nothing in the evening on Monday 14th September, I'm afraid. "), out
    times = _times_in(out)
    assert times and all(_band(t) != "evening" for t in times)


def test_the_one_evening_already_heard_is_said_so_and_the_rest_opened():
    """On the 10:48 table 17:10 -- the day's only evening -- was on the
    offer, so 'Monday evening' is the spent case, not the empty one."""
    s = _the_10_48_table()
    out = _say(s, "what about monday evening")
    assert out.startswith("I've given you all the evenings I have that day, I'm afraid. "), out
    assert all(_band(t) != "evening" for t in _times_in(out))


def test_every_morning_heard_is_said_so_and_the_rest_of_the_day_opened():
    s = _the_10_48_table()
    for _ in range(3):
        _say(s, "what else on monday morning")
    heard = {str(x)[11:16] for x in s[sf._SPOKEN_KEY] if str(x).startswith(MON)}
    assert all(t in heard for t in GRID if _band(t) == "morning")
    out = _say(s, "what about monday morning")
    assert out.startswith("I've given you all the mornings I have that day, I'm afraid. "), out
    times = _times_in(out)
    assert times and all(_band(t) != "morning" for t in times), times


def test_the_preface_is_spoken_not_recorded():
    """A later 'what about Monday' repeats the times, not the apology."""
    s = _the_10_48_table()
    out = _say(s, "what about monday evening")
    again = _say(s, "what about monday")
    assert again == out.split("I'm afraid. ", 1)[1]


# ── reach ──────────────────────────────────────────────────────────────────

def test_on_a_single_day_offer_the_band_still_reaches_the_producer():
    days = [_day(MON, GRID)]
    s = {"clinic_id": "northgate", "available_days": days}
    _offer(s, days)
    assert s["_slot_presentation_mode"] == "single_day"
    out = _say(s, "what about monday morning")
    assert out and all(_band(t) == "morning" for t in _times_in(out)), out


def test_on_a_rota_where_everything_was_heard_the_band_still_reaches_the_producer():
    """sparse: two times a day, both on the week menu, `remaining` empty."""
    days = [_day(MON, ["09:00", "17:00"]), _day(THU, ["09:00", "17:00"])]
    s = {"clinic_id": "northgate", "available_days": days}
    _offer(s, days)
    assert not sf.remaining_unspoken(s)
    out = _say(s, "what about monday afternoon")
    assert out and out.startswith("I've nothing in the afternoon on Monday"), out


def test_what_else_on_monday_morning_walks_the_mornings():
    """The 'more' path honours the band too (DT-10), and says why when the
    mornings run out (DT-11) rather than silently reading afternoons."""
    s = _the_10_48_table()
    a = _say(s, "what about monday morning")
    b = _say(s, "what else on monday morning")
    assert b and all(_band(t) == "morning" for t in _times_in(b)), b
    assert not set(_times_in(b)) & set(_times_in(a))
    c = _say(s, "what else on monday morning")             # mornings now spent
    assert c.startswith("I've given you all the mornings I have that day, I'm afraid. "), c
    assert all(_band(t) != "morning" for t in _times_in(c))
