# tests/regression/test_n2_a_named_time_survives_the_words_around_it.py
"""
N2 - CA2ac47ad5889388b2974ccf19ee37ff5b, 10 Sep 2026 21:10, jv_v1 (Bolton),
build 2658f7272216. Judge 3, outcome=booked - and the caller had to ask for
midday THREE TIMES to be told it existed.

    caller: "um do you have around 12 on the tuesday"
    Susie : quarter past seven, eight in the evening        (correct: Tuesday
            holds only 19:15 and 20:00, nothing within tolerance of noon)
    caller: "um do you have around 12 am is that a slot you have"
    Susie : "the closest I've got to midday would be on a Saturday morning"
    caller: "yeah saturday as close as possible as 12 please"
    Susie : quarter past ten, eleven, quarter past one
    caller: "do you have 12 o'clock or is 11 and quarter past 1 the closest
             you have"
    Susie : "Yes - quarter to twelve in the morning on Saturday 19th September
             is free."

11:45 was bookable the whole time; it is what he eventually booked. So was
12:30. The Saturday readout offered him the three times FURTHEST from noon out
of the five it had.

D8 is wired correctly on this path - S-13 (`8ab39703`) put `user_text` into
both callers of `speak_one_day_from_payload`, and the log line confirms this
turn took it: "'Saturday 19th September' answered from the payload -- 3 of 5
bookable times spoken ... (D-B)". The pin had nothing to pin because the
PARSER returned nothing. Two holes, both in `requested_clock_times`:

  "as close as possible as 12"  -> []        the bare-hour arm requires a
                                             preposition from at|around|about|
                                             near|by|for, and "as" is not one
  "around 12 am"                -> ['00:00'] the meridiem arm reads it as
                                             MIDNIGHT and blanks the text, so
                                             the "around 12" arm never runs

Midnight is never bookable, so the second hole fails exactly as silently as the
first. Callers say "12 am" for midday constantly and this one said it on a
clinic line.

THE FIFTH SATURDAY TIME IS INFERRED, NOT READ. The log records "3 of 5" and the
three that were spoken. Of the candidate diaries tried, only
[10:15, 11:00, 11:45, 12:30, 13:15] makes the real `choose_presented_indices`
reproduce the live readout, so that is what is used here - stated plainly
because a reconstruction quietly presented as an observation is how a test
comes to assert the wrong thing.

Driven through `named_day_speech`, the real entry point, for the reason the
S-13 test gives: a test that writes the session key itself passes while the
live path is dead.
"""
from __future__ import annotations

from app.tools.slot_followup import (
    named_day_speech,
    record_spoken_slots,
    requested_clock_times,
)

MON, TUE, THU, SAT = "2026-09-14", "2026-09-15", "2026-09-17", "2026-09-19"

#: The lookup was date_hint="any", after_date=2026-09-14, day_window=7, so the
#: payload ran to the 20th. Only three days were PRESENTED; Saturday was in the
#: payload all along, which is why a named-day follow-up could answer it with
#: no tool call.
DIARY = {
    MON: (["18:45"], "Monday 14th September"),
    TUE: (["19:15", "20:00"], "Tuesday 15th September"),
    THU: (["19:30", "20:15"], "Thursday 17th September"),
    SAT: (["10:15", "11:00", "11:45", "12:30", "13:15"], "Saturday 19th September"),
}


def _day(day_iso):
    times, label = DIARY[day_iso]
    return {
        "date": day_iso,
        "day_label": label,
        "slot_times": list(times),
        "slot_times_spoken": [f"spoken-{t}" for t in times],
        "slots": [
            {"start": f"{day_iso}T{t}:00+01:00", "end": f"{day_iso}T{t}:45+01:00"}
            for t in times
        ],
        "times_found_on_day": len(times),
        "times_not_shown": 0,
    }


def _at_the_saturday_ask():
    """The session as it stood when he asked for Saturday near noon.

    Heard by then: the three-day offer (18:45, 19:15, 19:30) and Tuesday read
    twice (19:15, 20:00). All evenings, so none of them collides with a
    Saturday morning - B-116's pool for Saturday is untouched, and the readout
    it produces is the one the caller actually got.
    """
    days = [_day(MON), _day(TUE), _day(THU), _day(SAT)]
    session = {"available_days": days, "_slot_presentation_mode": "multi_day"}
    record_spoken_slots(session, [
        {"start": f"{MON}T18:45:00+01:00"},
        {"start": f"{TUE}T19:15:00+01:00"},
        {"start": f"{THU}T19:30:00+01:00"},
        {"start": f"{TUE}T20:00:00+01:00"},
    ])
    return session


def _spoken_times(session):
    return [
        str(s.get("start"))[11:16]
        for s in (session.get("last_offered_slots") or [])
    ]


# --------------------------------------------------------------------------
# The parser, which is where both holes are.
# --------------------------------------------------------------------------

def test_a_bare_hour_after_as_is_a_time():
    """"as close as possible as 12". The arm already accepts at, around, about,
    near, by and for; "as" reached it and was not one of them."""
    assert "12:00" in requested_clock_times(
        "yeah saturday as close as possible as 12 please"
    )


def test_twelve_am_offers_midday_as_a_candidate():
    """A caller saying "12 am" on a clinic line means noon.

    Not a claim that the literal reading is wrong - 00:00 stays. This module's
    contract is CANDIDATES, matched against the day's real bookable times, so
    an impossible reading dies on its own. Emitting only midnight is what makes
    the pin fail silently: no clinic opens at midnight, so there is nothing for
    it to fail against.
    """
    out = requested_clock_times("um do you have around 12 am is that a slot you have")
    assert "12:00" in out, out


def test_the_other_two_askings_still_parse():
    """Neither of these was broken, and they are asserted so a fix to the two
    above cannot quietly cost them."""
    assert "12:00" in requested_clock_times("um do you have around 12 on the tuesday")
    assert "12:00" in requested_clock_times(
        "do you have 12 o'clock or is 11 and quarter past 1 the closest you have"
    )


def test_as_does_not_turn_an_age_into_a_time():
    """The boundary the preposition list is defending.

    The corpus already caught this arm reading "um he's 18 17 i mean he's
    turning 18 in a couple months" as 18:17 on the one clinic with an under-age
    gate. 18:00 and 20:00 are real bookable times on THIS clinic, so a
    candidate invented here would pin a real slot rather than dying quietly.
    """
    assert requested_clock_times("he's as old as 18") == []
    assert requested_clock_times("as soon as possible") == []


# --------------------------------------------------------------------------
# The caller-visible property.
# --------------------------------------------------------------------------

def test_the_exhibit_saturday_near_noon_is_answered_with_a_time_near_noon():
    """His third asking, word for word. 11:45 is 15 minutes from noon - inside
    NEAREST_TIME_TOLERANCE_MIN - and bookable; he booked it four turns later."""
    session = _at_the_saturday_ask()

    text = named_day_speech(session, "yeah saturday as close as possible as 12 please")

    assert text, "the payload producer declined the exhibit's wording"
    spoken = _spoken_times(session)
    assert "11:45" in spoken, spoken


def test_the_pin_displaces_rather_than_filters():
    """Three times were spoken live and three must still be spoken. The pin
    reaches past B-116's pick; it does not narrow the readout to one."""
    session = _at_the_saturday_ask()

    named_day_speech(session, "yeah saturday as close as possible as 12 please")

    assert len(_spoken_times(session)) == 3, _spoken_times(session)


def test_without_the_pin_b116_drops_both_midday_times():
    """The condition the pin exists to reach past, asserted so a later reader
    can see this test is discriminating.

    B-116 picks unheard times spread across the day, which on this diary is
    10:15, 11:00, 13:15 - exactly what the caller heard. BOTH times nearest
    noon are dropped. If this ever stops being true the exhibit above stops
    testing D8 and starts testing B-116's spread.
    """
    session = _at_the_saturday_ask()

    named_day_speech(session, "and what about saturday")

    spoken = _spoken_times(session)
    assert "11:45" not in spoken and "12:30" not in spoken, spoken
