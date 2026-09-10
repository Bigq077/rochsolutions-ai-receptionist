# tests/regression/test_s13_payload_path_pins_the_requested_time.py
"""
S-13 - CAb8ac636017de7d35370fd7951c54d3cf, 10 Sep 2026 13:43, northgate,
build 9259595f50f5. Judge 1, outcome=abandoned.

    caller: "do you have anything around midday on tuesday"
    Susie : eight in the morning, twenty to ten, half past three
    caller: "what's the closest slot to midday do you have on tuesday then"
    Susie : half past ten, twenty past eleven, twenty to three

12:10 and 13:00 were bookable throughout - she had read them out sixteen
seconds earlier. He asked twice, explicitly, and hung up.

D8 exists to force a time the caller ASKED FOR back into a readout B-116 has
dropped, and it reads exactly one session key, `REQUESTED_TIMES_KEY`. That
key's three writers all live inside `check_availability`. A named-day
follow-up runs no tool - the live log says so in as many words, "no tool call
needed (D-B)" - so on that path nothing ever wrote the key from the caller's
own words and the pin was a no-op. D8's log line has never appeared in any
stored call.

TWO FAILURE MODES, and the fix has to close both:

  DEAD  - the last lookup named no time, the key is empty, the pin does
          nothing. This is the exhibit: the lookup was `date_hint="next week"`.
  STALE - the last lookup DID name a time, and the pin fires with a time from
          a question the caller has moved on from. The key's docstring
          ("written on EVERY lookup, empty included") defends against
          staleness between LOOKUPS and cannot defend against a turn that
          performs none.

The parser was never the problem: `requested_clock_times("around midday")`
returns 12:00 correctly. It was simply never called on this path.

Driven through `named_day_speech`, the real entry point, because a test that
writes the key itself is what
`test_the_named_day_producer_honours_a_requested_time` already does - and it
passed all week while the live path was dead.
"""
from __future__ import annotations

from app.tools.slot_followup import (
    REQUESTED_TIMES_KEY,
    named_day_speech,
    record_spoken_slots,
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


def _day(day_iso):
    return {
        "date": day_iso,
        "day_label": LABELS[day_iso],
        "slot_times": list(GRID),
        "slot_times_spoken": [f"spoken-{t}" for t in GRID],
        "slots": [
            {"start": f"{day_iso}T{t}:00+01:00", "end": f"{day_iso}T{t}:50+01:00"}
            for t in GRID
        ],
        "times_found_on_day": len(GRID),
        "times_not_shown": 0,
    }


def _at_step_five():
    """The session as it stood at turn 5 of the call.

    Steps 2, 3 and 4 have been spoken: the multi-day offer, then Monday, then
    Tuesday. Tuesday's 12:10 and 13:00 are therefore ALREADY HEARD, which is
    the point - B-116 correctly holds them out of the pool, and reaching past
    that is D8's entire job.
    """
    days = [_day(MON), _day(TUE), _day(WED)]
    session = {"available_days": days, "_slot_presentation_mode": "multi_day"}
    record_spoken_slots(session, [
        # step 2, the multi-day offer
        {"start": f"{MON}T08:00:00+01:00"}, {"start": f"{MON}T17:10:00+01:00"},
        {"start": f"{TUE}T08:50:00+01:00"}, {"start": f"{TUE}T16:20:00+01:00"},
        {"start": f"{WED}T09:40:00+01:00"}, {"start": f"{WED}T15:30:00+01:00"},
        # step 3, "tell me about Monday"
        {"start": f"{MON}T10:30:00+01:00"}, {"start": f"{MON}T11:20:00+01:00"},
        {"start": f"{MON}T14:40:00+01:00"},
        # step 4, "and what about Tuesday"
        {"start": f"{TUE}T12:10:00+01:00"}, {"start": f"{TUE}T13:00:00+01:00"},
        {"start": f"{TUE}T13:50:00+01:00"},
    ])
    return session


def _spoken_times(session):
    return [
        str(s.get("start"))[11:16]
        for s in (session.get("last_offered_slots") or [])
    ]


def test_the_exhibit_a_midday_request_on_a_named_day_is_answered_with_midday():
    """Step 5, word for word. 12:10 is 10 minutes from noon, inside
    NEAREST_TIME_TOLERANCE_MIN, and it is bookable."""
    session = _at_step_five()

    text = named_day_speech(session, "do you have anything around midday on tuesday")

    assert text, "the payload producer declined the exhibit's wording"
    spoken = _spoken_times(session)
    assert "12:10" in spoken, spoken


def test_the_second_ask_is_answered_too():
    """Step 5b. He rephrased rather than repeated, and got a third readout
    with nothing near noon in it."""
    session = _at_step_five()

    named_day_speech(session, "do you have anything around midday on tuesday")
    named_day_speech(
        session, "what's the closest slot to midday do you have on tuesday then"
    )

    assert "12:10" in _spoken_times(session), _spoken_times(session)


def test_the_pin_displaces_rather_than_filters():
    """B-116's pool had already removed 12:10 as heard. The pin must reach
    PAST that filter, not narrow to it - the readout still holds three times."""
    session = _at_step_five()

    named_day_speech(session, "do you have anything around midday on tuesday")

    spoken = _spoken_times(session)
    assert len(spoken) == 3, spoken


def test_the_key_is_written_on_every_payload_turn_empty_included():
    """The staleness half. A turn that names no time must CLEAR the key, or a
    time named three turns ago pins a slot in a readout that has nothing to do
    with it - the same defect the tool path's docstring guards against, one
    layer down."""
    session = _at_step_five()
    # 15:30 has ALREADY been heard on Wednesday, so B-116 holds it out of the
    # pool and only a live pin could put it back. That is what makes this
    # assertion discriminating: a time B-116 would pick anyway proves nothing.
    session[REQUESTED_TIMES_KEY] = ["15:30"]

    named_day_speech(session, "and what about wednesday")

    assert session.get(REQUESTED_TIMES_KEY) == [], session.get(REQUESTED_TIMES_KEY)
    assert "15:30" not in _spoken_times(session), _spoken_times(session)


def test_a_day_named_with_no_time_is_unchanged():
    """The no-op case, stated so a later reader can see the pin is additive:
    a plain named-day request still reads B-116's unheard pick."""
    session = _at_step_five()

    text = named_day_speech(session, "and what about wednesday")

    assert text
    spoken = _spoken_times(session)
    assert not {"09:40", "15:30"} & set(spoken), spoken
