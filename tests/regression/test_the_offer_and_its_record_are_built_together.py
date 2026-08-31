"""
The slot sentence and the record of what it named come out of one function.

Step 1 of `docs/plan/DETERMINISTIC_SLOT_PRESENTATION.md`. `build_slot_offer` is
not wired into anything yet; these tests pin the contract before it is.

The two calls that motivated it, both theorem_v3, both 31 Aug 2026:

  CA44f1bdbe  the caller chose six in the evening, Acuity was written 18:00,
              and Gate 5 told them nine in the morning three times (B-126).
  CA7e3ccfd4  four different answers to "what is available on Wednesday",
              three of them opening "The available slots for Wednesday 9th
              September are", naming three disjoint sets, none of them the day.
              The caller abandoned.

Both trace to the same thing: the model writes the sentence, and
`resolve_spoken_options` cannot parse the shape the prompt asks it for, so the
record of what was offered is empty or projected. The test that matters here is
`test_the_record_holds_every_time_the_sentence_names` — it is the invariant the
whole plan rests on, and it is the one the current design cannot state.
"""
from __future__ import annotations

import pytest

from app.tools.slot_offer import build_slot_offer


def _day(date, label, times, spoken, hidden=0):
    return {
        "date": date,
        "day_label": label,
        "slot_times": list(times),
        "slot_times_spoken": list(spoken),
        "times_not_shown": hidden,
        "slots": [{"start": "{}T{}:00+01:00".format(date, t), "end": ""} for t in times],
    }


MON = _day(
    "2026-09-07", "Monday 7th September",
    ["10:00", "11:00", "13:00", "17:00"],
    ["ten in the morning", "eleven in the morning", "one in the afternoon",
     "five in the evening"],
)
TUE = _day(
    "2026-09-08", "Tuesday 8th September",
    ["09:00", "14:00"],
    ["nine in the morning", "two in the afternoon"],
)
# Wednesday's real Acuity data on CA7e3ccfd4: seven times.
WED = _day(
    "2026-09-09", "Wednesday 9th September",
    ["09:00", "10:00", "11:00", "12:00", "14:00", "15:00", "17:00"],
    ["nine in the morning", "ten in the morning", "eleven in the morning",
     "midday", "two in the afternoon", "three in the afternoon",
     "five in the evening"],
)


# ── the invariant ────────────────────────────────────────────────────────────

def test_the_record_holds_every_time_the_sentence_names():
    """The whole point. No reverse-parse, no projection, no disagreement."""
    offer = build_slot_offer([MON, TUE, WED])
    for slot in offer.slots:
        assert slot["spoken"] in offer.text, slot["spoken"]
    # And nothing is named that is not in the record.
    for day in (MON, TUE, WED):
        for label in day["slot_times_spoken"]:
            if label in offer.text:
                assert any(s["spoken"] == label for s in offer.slots), label


def test_the_keypad_map_matches_the_chunks_one_for_one():
    """B-80 and section 6 of _flush_slot_buf: a mismatch here used to be a
    warning nobody could act on. Now it cannot arise."""
    for days in ([MON, TUE, WED], [WED], [MON, TUE]):
        offer = build_slot_offer(days)
        assert len(offer.chunks) == len(offer.dtmf_map)
        for i, chunk in enumerate(offer.chunks, start=1):
            assert "Number {},".format(i) in chunk


# ── multi_day ────────────────────────────────────────────────────────────────

def test_a_multi_day_offer_names_two_times_per_day():
    offer = build_slot_offer([MON, TUE, WED])
    assert offer.mode == "multi_day"
    assert len(offer.slots) == 6
    assert offer.chunks[0].startswith("Here's what we've got coming up — Number 1,")
    assert offer.dtmf_map == {
        "1": "Monday 7th September",
        "2": "Tuesday 8th September",
        "3": "Wednesday 9th September",
    }


def test_the_second_time_comes_from_a_different_part_of_the_day():
    """"ten in the morning or eleven in the morning" is not two options.

    The LATEST slot in another part of the day, so Wednesday pairs its nine in
    the morning with five in the evening rather than with three in the
    afternoon. That is what the model itself produced on CA44f1bdbe, where the
    evening slot was the one the caller wanted — so this reproduces today's
    speech rather than quietly narrowing it.
    """
    offer = build_slot_offer([MON, TUE, WED])
    assert "ten in the morning, or five in the evening" in offer.text
    assert "nine in the morning, or two in the afternoon" in offer.text   # Tue
    assert "nine in the morning, or five in the evening" in offer.text    # Wed


def test_a_day_whose_slots_share_one_part_still_offers_a_later_one():
    """The prompt's fallback: "if every slot that day falls in the same part of
    the day ... still give a second, later time"."""
    morning_only = _day(
        "2026-09-10", "Thursday 10th September",
        ["09:00", "10:00", "11:00"],
        ["nine in the morning", "ten in the morning", "eleven in the morning"],
    )
    offer = build_slot_offer([morning_only, TUE])
    assert "nine in the morning, or eleven in the morning" in offer.text


def test_a_multi_day_offer_never_claims_a_few_others_that_day():
    """B-99: after three days, "that day" names nothing. Enforced structurally."""
    offer = build_slot_offer([MON, TUE, WED])
    assert offer.more_times is True          # true of the data
    assert "that day" not in offer.text      # and still not said


# ── single_day ───────────────────────────────────────────────────────────────

def test_a_single_day_offer_caps_at_three_and_says_so():
    offer = build_slot_offer([WED])
    assert offer.mode == "single_day"
    assert len(offer.slots) == 3
    assert offer.chunks[0].startswith(
        "The available slots for Wednesday 9th September are — Number 1,"
    )
    assert offer.more_times is True
    assert "others that day" in offer.text


def test_three_times_on_one_day_are_spread_not_clustered():
    """A morning, a middle and an evening — what a receptionist offers.

    On the CA7e3ccfd4 payload the model read out ten, eleven, midday: three
    times inside two hours, on a day running nine to five. Filling forwards from
    the earliest was no better (nine, ten, five in the evening — two adjacent
    then a jump), so the pick is evenly spaced across the day instead.
    """
    offer = build_slot_offer([WED])
    assert [s["start"][11:16] for s in offer.slots] == ["09:00", "12:00", "17:00"]


def test_a_day_read_out_in_full_makes_no_further_claim():
    offer = build_slot_offer([TUE])
    assert offer.more_times is False
    assert "others" not in offer.text
    assert offer.text.endswith("Either of those work?")


def test_a_banded_day_counts_the_times_the_band_hid():
    """B-97: a slot the filter removed is not in `slots` and no walk can see it."""
    banded = _day(
        "2026-09-09", "Wednesday 9th September",
        ["14:00", "15:00"], ["two in the afternoon", "three in the afternoon"],
        hidden=5,
    )
    offer = build_slot_offer([banded])
    assert offer.more_times is True
    assert "others that day" in offer.text


def test_a_lone_slot_is_not_read_out_as_a_numbered_list():
    one = _day("2026-09-04", "Friday 4th September", ["13:00"],
               ["one in the afternoon"])
    offer = build_slot_offer([one])
    assert offer.text.startswith("The slot I have on Friday 4th September is")
    assert "Number" not in offer.text
    assert offer.dtmf_map == {"1": "one in the afternoon"}


def test_the_earliest_lead_in_is_a_parameter_not_a_model_choice():
    """B-125 was a ranking CLAIM the model made. Here it is the caller's ask."""
    plain = build_slot_offer([WED])
    earliest = build_slot_offer([WED], lead_in="earliest")
    assert "The earliest I have is" not in plain.text
    assert earliest.text.startswith("The earliest I have is Wednesday 9th September —")
    assert [s["start"] for s in plain.slots] == [s["start"] for s in earliest.slots]


# ── refusals ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("payload", [None, [], [{}], [{"date": "2026-09-09"}]])
def test_nothing_to_offer_returns_none_rather_than_a_sentence(payload):
    """The caller keeps its own empty-day handling; it does not inherit one."""
    assert build_slot_offer(payload) is None
