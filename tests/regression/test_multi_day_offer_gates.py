"""Two gates step 4 has to pass before multi_day is wired.

Step 4 of `docs/plan/DETERMINISTIC_SLOT_PRESENTATION.md` speaks a multi_day
offer from the payload and returns from `_flush_slot_buf` section 1b, exactly as
step 3 did for single_day. That return skips sections 3a-6. For single_day that
was safe: 3b and 3b-ii are already gated to `single_day` by `_allow_append`, and
3a, 4, 5 and 6 are all replaced by construction.

multi_day is NOT the same, and these are the two places it differs. Both are
written to FAIL against `build_slot_offer` as it stands, deliberately: they are
the gate, not a description of today.

WHY THESE TWO AND NOT OTHERS. `scripts/slot_readout_census.py` measured the
corpus on 1 Sept 2026 and found 51 of 52 multi_day readouts (98%) hand the
positional resolver a DAY-only label, so section 3a is worth replacing on its
own. It could NOT size these two:

  * The section 3c sentence shows 0 occurrences in 781 calls -- but the corpus
    holds ZERO readouts of any kind after 28 Aug, the day
    `append_other_dates_offer` landed. The zero measures missing data, not a
    missing defect, so 3c is pinned here by contract rather than by frequency.
  * The `v3_last_offered_day_iso` divergence needs the availability payload,
    and no payload is stored anywhere in obs.
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
        "slots": [
            {"start": "{}T{}:00+01:00".format(date, t), "end": ""} for t in times
        ],
    }


THREE_DAYS = [
    _day("2026-09-07", "Monday 7th September", ["10:00", "17:00"],
         ["ten in the morning", "five in the evening"]),
    _day("2026-09-08", "Tuesday 8th September", ["09:00", "14:00"],
         ["nine in the morning", "two in the afternoon"]),
    _day("2026-09-09", "Wednesday 9th September", ["11:00", "18:00"],
         ["eleven in the morning", "six in the evening"]),
]

# The shape `other_dates_for_requested_day` arrives in, from the B-111 payload.
# No times: naming a time for a date nobody heard is the B-108b defect, and the
# payload deliberately carries none.
OTHER_TUESDAYS = [
    {"date": "2026-09-15", "spoken": "Tuesday 15th September"},
    {"date": "2026-09-22", "spoken": "Tuesday 22nd September"},
]


class TestSectionThreeCSurvivesTheHandover:
    """Section 3c has no mode gate, so multi_day loses it on the early return.

    `append_other_dates_offer` (B-111) is appended at llm_stream.py section 3c
    with no `_allow_append` check, and `_slot_other_dates` is set from
    `result["other_dates_for_requested_day"]` with no mode check either. So it
    can fire on multi_day today, and wiring multi_day through section 1b would
    silently stop it.

    Re-teaching the formatter is NOT the fix, and the repo has already paid for
    that lesson twice: B-109/B-110 wrote guidance asking the model to name these
    dates and, in `append_other_dates_offer`'s own words, "it never did"; and
    8de7e7d0 had to REMOVE the more-times example from that prompt because the
    model copied it onto a day with no further times and invented availability.
    The prompt now forbids mentioning further availability at all. So the
    sentence is code's to emit -- which means `build_slot_offer` has to emit it.
    """

    def test_the_offer_names_the_further_dates_the_payload_held_back(self):
        offer = build_slot_offer(THREE_DAYS, other_dates=OTHER_TUESDAYS)
        assert offer is not None
        assert "Tuesday 15th" in offer.text or "the 15th" in offer.text
        assert "Tuesday 22nd" in offer.text or "the 22nd" in offer.text

    def test_it_says_nothing_when_the_payload_held_nothing_back(self):
        """The claim is about the clinic's diary, so silence is the default."""
        offer = build_slot_offer(THREE_DAYS, other_dates=[])
        assert offer is not None
        assert "also got" not in offer.text.lower()

    def test_a_date_already_named_in_an_earlier_chunk_is_not_said_twice(self):
        """The dedupe is against the WHOLE offer, not the chunk appended to.

        `append_other_dates_offer` suppresses a date the reply already names.
        Here Tuesday 8th is named in chunk 2 and the sentence is appended to
        chunk 3, so checking only the chunk being appended to would say it
        twice — and multi_day is the only place that can happen.
        """
        offer = build_slot_offer(
            THREE_DAYS,
            other_dates=[{"date": "2026-09-08", "spoken": "Tuesday 8th September"}],
        )
        assert offer is not None
        assert "also got" not in offer.text.lower()

    def test_a_date_with_no_spoken_form_is_ignored(self):
        offer = build_slot_offer(
            THREE_DAYS, other_dates=[{"date": "2026-09-15", "spoken": ""}],
        )
        assert offer is not None
        assert "also got" not in offer.text.lower()

    def test_the_further_dates_are_not_added_to_the_record_or_the_keypad(self):
        """Naming a date is not offering a slot on it.

        The payload carries no times for these dates on purpose. A date that
        reaches `slots` would become bookable through a record the caller never
        heard a time for, and a date in `dtmf_map` would make it pressable.
        """
        offer = build_slot_offer(THREE_DAYS, other_dates=OTHER_TUESDAYS)
        assert offer is not None
        assert all(s["date"] != "2026-09-15" for s in offer.slots)
        assert all(s["date"] != "2026-09-22" for s in offer.slots)
        assert all("15th" not in v and "22nd" not in v
                   for v in offer.dtmf_map.values())


class TestTheRecoveryAnchorNamesADayTheCallerHeard:
    """`v3_last_offered_day_iso` has two sources that disagree on multi_day.

    Section 4 sets it from `session["available_days"][0]["date"]` -- the
    PAYLOAD's first day. Section 1b sets it from `_det_slots[0]["date"]` -- the
    first day actually SPOKEN. On single_day those coincide, which is why step 3
    could take either. On multi_day they can diverge, because `build_slot_offer`
    sorts the days and drops any with no bookable slot.

    It is the FAQ-detour recovery anchor: if the caller asks a question
    mid-selection the slot map is cleared, and CALL STATE uses this to send
    `check_availability` back to the right day. Pointed at a day the caller was
    never offered, recovery lands somewhere they never heard of.

    `slots[0]["date"]` is already the right value. The gate is that step 4 must
    not reach for it by convention -- the wiring needs a named, tested source,
    so that a later change to the ordering of `slots` cannot silently move the
    anchor.
    """

    def test_the_payloads_first_day_is_not_the_first_day_spoken(self):
        """The divergence is real, so the wiring cannot use available_days[0]."""
        days = [
            _day("2026-09-06", "Sunday 6th September", [], []),  # closed
            *THREE_DAYS,
        ]
        offer = build_slot_offer(days)
        assert offer is not None
        assert days[0]["date"] == "2026-09-06"
        assert offer.slots[0]["date"] == "2026-09-07"

    def test_the_offer_names_its_own_first_spoken_date(self):
        offer = build_slot_offer(THREE_DAYS)
        assert offer is not None
        assert offer.first_spoken_date == "2026-09-07"

    def test_the_first_spoken_date_skips_a_day_with_no_bookable_slot(self):
        days = [_day("2026-09-06", "Sunday 6th September", [], []), *THREE_DAYS]
        offer = build_slot_offer(days)
        assert offer is not None
        assert offer.first_spoken_date == "2026-09-07"

    def test_the_first_spoken_date_is_always_a_day_the_offer_named(self):
        """The property the anchor actually needs, stated once."""
        offer = build_slot_offer(THREE_DAYS)
        assert offer is not None
        assert offer.first_spoken_date in {s["date"] for s in offer.slots}
