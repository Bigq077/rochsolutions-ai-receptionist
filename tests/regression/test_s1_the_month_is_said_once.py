"""
The month is said once, by the first day of a multi-day readout.

S-1(a), `docs/plan/SLOT_PRESENTATION_FINISH_2026-09-10.md`. A three-day readout
was measured at 17-19 s live (17.0 / 18.9 / 16.8 s in the 6 Sep register, 18.0 s
on CAb5b52d95 on 10 Sep) and callers barge in on nearly every turn. Reproduced
in synthesis at 18.59 s against the live chunk timings, of which the repeated
month is 12 characters -- 0.66 s.

TWO PROPERTIES, and the second is the one with teeth:

  * SPEECH is shortened. "Number 2, Tuesday the 15th -- ...".
  * The RECORD is not. `dtmf_map` keeps "Tuesday 15th September", because
    `day_selected_by_position` matches the map's value against
    `available_days[].day_label` by CONTAINMENT. Shortening the map would make
    "the second one" resolve to no day at all -- silently, by returning None,
    which is indistinguishable from a caller who named nothing.

And the guard: a readout that straddles a month end must keep the month, or
"Tuesday the 1st" said after "Monday 30th September" is heard as September.
Decided on the ISO date, never on the prose.

These are BEHAVIOURAL. Every one drives `build_slot_offer` itself -- the plan's
standing trap is that `assert "X" in source` proves nothing, and three changes
shipped completely inert on 9 Sep behind exactly that shape of check.
"""
from __future__ import annotations

from app.tools.slot_followup import day_selected_by_position
from app.tools.slot_offer import build_slot_offer


def _day(date, label, times, spoken):
    return {
        "date": date,
        "day_label": label,
        "slot_times": list(times),
        "slot_times_spoken": list(spoken),
        "slots": [{"start": "{}T{}:00+01:00".format(date, t), "end": ""} for t in times],
    }


SEPT = [
    _day("2026-09-14", "Monday 14th September", ["08:00", "15:30"],
         ["eight in the morning", "half past three in the afternoon"]),
    _day("2026-09-15", "Tuesday 15th September", ["08:50", "16:20"],
         ["ten to nine in the morning", "twenty past four in the afternoon"]),
    _day("2026-09-16", "Wednesday 16th September", ["09:00", "17:30"],
         ["nine in the morning", "half past five in the evening"]),
]

# 29 Sept, 30 Sept, 1 Oct -- the readout that must NOT be shortened.
ACROSS_MONTHS = [
    _day("2026-09-29", "Tuesday 29th September", ["08:00", "15:30"],
         ["eight in the morning", "half past three in the afternoon"]),
    _day("2026-09-30", "Wednesday 30th September", ["09:00", "16:20"],
         ["nine in the morning", "twenty past four in the afternoon"]),
    _day("2026-10-01", "Thursday 1st October", ["09:00", "17:30"],
         ["nine in the morning", "half past five in the evening"]),
]


def _offer(days):
    return build_slot_offer(days, more_times=False, more_days=False)


def test_the_first_day_still_names_its_month():
    text = _offer(SEPT).text
    assert "Monday 14th September" in text


def test_the_days_after_the_first_drop_the_month():
    text = _offer(SEPT).text
    assert "Tuesday the 15th" in text
    assert "Wednesday the 16th" in text
    # Said once, not three times.
    assert text.count("September") == 1


def test_a_readout_that_crosses_a_month_end_keeps_the_month():
    """"Tuesday the 1st" after "Monday 30th September" is heard as September."""
    text = _offer(ACROSS_MONTHS).text
    assert "Thursday 1st October" in text
    # The day that IS in the first day's month is still shortened, so this
    # test cannot pass merely by the feature being switched off.
    assert "Wednesday the 30th" in text


def test_the_keypad_map_keeps_the_full_label():
    """The record is not the speech. `day_selected_by_position` needs the month."""
    offer = _offer(SEPT)
    assert offer.dtmf_map == {
        "1": "Monday 14th September",
        "2": "Tuesday 15th September",
        "3": "Wednesday 16th September",
    }


def test_the_second_one_still_resolves_to_the_second_day():
    """End to end: the caller hears the short form and picks by position."""
    offer = _offer(SEPT)
    session = {"v3_dtmf_slot_map": offer.dtmf_map, "available_days": SEPT}
    assert day_selected_by_position(SEPT, session, "the second one please") == "2026-09-15"
    assert day_selected_by_position(SEPT, session, "the third one") == "2026-09-16"


def test_a_single_day_readout_is_untouched():
    """One day IS the first day, so it carries the month."""
    assert "Monday 14th September" in _offer([SEPT[0]]).text


def test_a_label_that_is_not_a_date_is_left_alone():
    """Deny by default on the shape -- no half-parsed prose reaches a caller."""
    days = [
        _day("2026-09-14", "that day", ["08:00"], ["eight in the morning"]),
        _day("2026-09-15", "tomorrow", ["09:00"], ["nine in the morning"]),
    ]
    text = _offer(days).text
    assert "tomorrow" in text
    assert "the tomorrow" not in text


def test_the_readout_got_shorter_and_named_the_same_slots():
    """The point of the change, and the thing it must not cost.

    Guards the reversion AND the failure mode that matters more: a shorter
    sentence that offers something different. The slots recorded are compared
    against the payload, not against a stored string.
    """
    offer = _offer(SEPT)
    assert len(offer.text) < 338            # the pre-change length, measured
    assert [s["spoken"] for s in offer.slots] == [
        "eight in the morning", "half past three in the afternoon",
        "ten to nine in the morning", "twenty past four in the afternoon",
        "nine in the morning", "half past five in the evening",
    ]
