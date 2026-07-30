# tests/regression/test_followup_batch_is_single_day.py
"""
C1 — an unspoken-slot follow-up batch must never straddle two days.

CA5c4fb14f (30 Jul 2026) told a caller "Tuesday the 4th of August at seven in
the evening", he said yes, she said "All booked", and the calendar event was
created for 2026-08-05T19:00 — a Wednesday. He would have arrived to nothing.

MECHANISM
---------
`remaining_slots_after_offer` flattens across EVERY day in available_days, so
`remaining[:n]` could take one slot from Tuesday and the next from Wednesday.
Both consumers of that batch present it as a single day, labelled from batch[0]:

    format_next_batch_speech   -> "On {batch[0].day_label} I also have A, or B"
    build_followup_tool_result -> first_day.date/day_label from batch[0],
                                  first_day.slots from the WHOLE batch

Every slot keeps its own true `start`, so picking the second option books the day
it really belongs to while the caller was told the first slot's day. Nothing
downstream is wrong — the booking matches the slot — which is why no existing
detector or guard caught it, and why it is the only defect in the register the
caller cannot notice.

The fixture below is the real V2 shape: Tuesday had 17:45 and 18:30 already
spoken plus 17:30 unspoken, and Wednesday had 19:00.
"""
from __future__ import annotations

import pytest

from app.tools.slot_followup import (
    build_followup_tool_result,
    format_next_batch_speech,
    next_slot_batch,
    remaining_slots_after_offer,
)

TUE = {
    "date": "2026-08-04",
    "day_label": "Tuesday the 4th of August",
    "slot_times": ["17:45", "18:30", "17:30"],
    "slot_times_spoken": ["quarter to six in the evening",
                          "half past six in the evening",
                          "half past five in the evening"],
    "slots": [{"start": "2026-08-04T17:45:00"},
              {"start": "2026-08-04T18:30:00"},
              {"start": "2026-08-04T17:30:00"}],
}
WED = {
    "date": "2026-08-05",
    "day_label": "Wednesday the 5th of August",
    "slot_times": ["19:00"],
    "slot_times_spoken": ["seven in the evening"],
    "slots": [{"start": "2026-08-05T19:00:00"}],
}
ALREADY_SPOKEN = [{"start": "2026-08-04T17:45:00"}, {"start": "2026-08-04T18:30:00"}]


def _v2_remaining():
    return remaining_slots_after_offer([TUE, WED], ALREADY_SPOKEN)


def test_remaining_does_span_days_that_is_the_input_not_the_bug():
    """Guards the premise: if this stops spanning days the test proves nothing."""
    assert {s["date"] for s in _v2_remaining()} == {"2026-08-04", "2026-08-05"}


def test_batch_is_confined_to_one_day():
    batch, _ = next_slot_batch(_v2_remaining(), n=2)
    assert batch, "a batch was available; it must not be empty"
    assert len({s["date"] for s in batch}) == 1, (
        f"batch straddles days: {[(s['date'], s['spoken']) for s in batch]} — "
        "the second slot would be announced under the first slot's day name"
    )


def test_spoken_offer_only_names_slots_from_the_day_it_announces():
    """The V2 sentence, regenerated. It must not offer the Wednesday time."""
    batch, more = next_slot_batch(_v2_remaining(), n=2)
    speech = format_next_batch_speech(batch, more)
    assert "Tuesday the 4th of August" in speech
    assert "seven in the evening" not in speech, (
        f"a Wednesday slot is being offered as Tuesday: {speech!r}"
    )


def test_tool_result_slots_all_match_its_declared_date():
    """first_day declares one date; every slot inside it must be on that date."""
    batch, more = next_slot_batch(_v2_remaining(), n=2)
    first_day = build_followup_tool_result([TUE, WED], batch, more)["first_day"]
    assert first_day["date"] == "2026-08-04"
    for slot in first_day["slots"]:
        assert slot["start"].startswith(first_day["date"]), (
            f"slot {slot['start']} is inside first_day dated "
            f"{first_day['date']} labelled {first_day['day_label']!r}"
        )


def test_more_times_means_more_on_that_day():
    """`more` feeds "I've a few others THAT DAY" — so it must mean that day.

    Tuesday has exactly one unspoken slot left. Counting the Wednesday slot as
    "more" would have her promise further Tuesday times that do not exist.
    """
    batch, more = next_slot_batch(_v2_remaining(), n=2)
    assert len(batch) == 1
    assert more is False


def test_fail_safe_drops_off_day_slots_reaching_the_tool_result():
    """Backstop for any future caller that bypasses next_slot_batch.

    Offering fewer times costs a follow-up question. Announcing a slot under the
    wrong day's name sends a patient to the clinic on the wrong day.
    """
    multi_day = _v2_remaining()          # deliberately spans Tue + Wed
    result = build_followup_tool_result([TUE, WED], multi_day, False)
    first_day = result["first_day"]
    assert [s["start"] for s in first_day["slots"]] == ["2026-08-04T17:30:00"]
    assert first_day["more_times"] is True, (
        "dropped slots still exist on other days — the caller must not be told "
        "these are the only times"
    )


def test_single_day_availability_is_unaffected():
    """The common case must not change: one day in, both its slots offered."""
    remaining = remaining_slots_after_offer([TUE], ALREADY_SPOKEN[:1])
    batch, more = next_slot_batch(remaining, n=2)
    assert [s["spoken"] for s in batch] == ["half past six in the evening",
                                            "half past five in the evening"]
    assert more is False


@pytest.mark.parametrize("remaining", [[], None])
def test_empty_remaining_is_safe(remaining):
    assert next_slot_batch(remaining or [], n=2) == ([], False)


def test_slots_without_a_date_field_group_together():
    """A missing `date` must not make every slot look like its own day.

    Falls back to the ISO start. If this regressed to `None` keys, a batch of
    dateless slots would be treated as multi-day and silently truncated to one.
    """
    day = {
        "day_label": "Tuesday the 4th of August",
        "slot_times": ["17:30", "18:00"],
        "slot_times_spoken": ["half past five", "six"],
        "slots": [{"start": "2026-08-04T17:30:00"}, {"start": "2026-08-04T18:00:00"}],
    }
    batch, more = next_slot_batch(remaining_slots_after_offer([day], []), n=2)
    assert len(batch) == 2
    assert more is False
