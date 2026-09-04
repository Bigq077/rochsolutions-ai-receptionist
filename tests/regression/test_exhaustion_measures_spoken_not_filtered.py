r"""B-141: "no further times that day" said with ten of twelve never spoken.

`exhaustion_claim_is_supported` gates a completeness claim about a DAY. Its own
comment calls itself "POSITIVE proof of completeness". It was not — it proved
only that no time-of-day BAND had filtered the day, which is a different
question, and on a multi-day offer the two come apart every single time.

    times_not_shown  =  how many times a preference FILTER removed
    the claim needs   =  how many bookable times the caller never HEARD

The readout trims to two times per day on a multi-day offer, and
`session["available_days"]` deliberately keeps the FULL day so the follow-up
paths can still see every real time (`_trim_presented_days` sets
`out["available_days"] = days`, untrimmed). So `times_not_shown` stays 0 while
ten of twelve times sit unspoken.

MEASURED 4 Sep 2026 against every `slot_offers` row in the obs store — 102 of
102 day-entries held bookable times the caller never heard, and
`times_not_shown` read 0 on all 102. Not an edge case: the universal case for
a multi-day offer.

The payload below is verbatim from `CA9c39d09fe12bfc1e`, the offer stored on
4 September. Monday 7th is twelve bookable; the caller heard two.

This is the presented-vs-bookable split already recorded from B-95, reaching
the one predicate whose entire job is to be positive proof.

NOTE ON HARNESSES. `replay_day_picks`, `replay_slot_resolutions` and
`replay_slot_readouts` are all byte-identical with this fix on and off — none
of them calls this predicate. A green harness that cannot see the change is
not evidence, so this file is the proof: every assertion here flips when the
fix is disabled.
"""
from __future__ import annotations

import pytest

from app.tools.slot_followup import (
    _SPOKEN_FP_KEY,
    exhaustion_claim_is_supported,
    offer_day_hides_times,
    record_spoken_slots,
    spoken_starts_for_offer,
)

# Verbatim from the stored offer. Monday holds twelve bookable times and
# reports that nothing was hidden.
MONDAY_TIMES = [
    "08:00", "08:50", "09:40", "10:30", "11:20", "12:10",
    "13:00", "13:50", "14:40", "15:30", "16:20", "17:10",
]
SPOKEN = [
    "eight in the morning", "ten to nine in the morning",
    "twenty to ten in the morning", "half past ten in the morning",
    "twenty past eleven in the morning", "ten past twelve in the afternoon",
    "one in the afternoon", "ten to two in the afternoon",
    "twenty to three in the afternoon", "half past three in the afternoon",
    "twenty past four in the afternoon", "ten past five in the evening",
]


def _monday(times=None):
    times = MONDAY_TIMES if times is None else times
    return {
        "date": "2026-09-07",
        "day_label": "Monday 7th September",
        "slot_times": list(times),
        "slot_times_spoken": SPOKEN[: len(times)],
        "slots": [
            {"start": f"2026-09-07T{t}:00+01:00", "end": f"2026-09-07T{t}:00+01:00"}
            for t in times
        ],
        "times_found_on_day": len(times),
        "times_not_shown": 0,
    }


def _session(heard_times, day=None):
    """The session as the engine leaves it: the FULL day in available_days,
    and only what was read out in last_offered_slots."""
    day = day or _monday()
    offered = [{"start": f"2026-09-07T{t}:00+01:00"} for t in heard_times]
    session = {
        "clinic_id": "northgate",
        "available_days": [day],
        "_slot_presentation_mode": "multi_day",
        "last_offered_slots": offered,
    }
    record_spoken_slots(session, offered)
    return session


# -- The defect -------------------------------------------------------------

def test_a_day_with_ten_unspoken_times_is_not_exhausted():
    """The live shape: twelve bookable, two read out, nothing band-filtered."""
    session = _session(["08:00", "17:10"])
    assert len(spoken_starts_for_offer(session)) == 2
    assert offer_day_hides_times(session) is False, (
        "no band filtered this day — which is exactly why times_not_shown "
        "cannot answer the question being asked"
    )
    assert exhaustion_claim_is_supported(session) is False, (
        "ten of twelve times were never spoken and the day was called finished"
    )


def test_the_filter_count_still_says_nothing_was_hidden():
    """Pins WHY the old test passed. If this ever starts reporting a positive
    count, the trim moved onto session["available_days"] and several other
    readers change meaning with it — see the handover's warning against
    recomputing it at the trim."""
    session = _session(["08:00", "17:10"])
    assert session["available_days"][0]["times_not_shown"] == 0


@pytest.mark.parametrize("heard", [
    [],                                  # nothing read out at all
    ["08:00"],                           # one of twelve
    ["08:00", "17:10"],                  # the live two
    MONDAY_TIMES[:-1],                   # eleven of twelve
])
def test_any_unspoken_time_defeats_the_claim(heard):
    """One unheard bookable time is enough. The sentence is absolute, so the
    evidence for it has to be."""
    assert exhaustion_claim_is_supported(_session(heard)) is False


# -- The honest case must survive -------------------------------------------

def test_a_day_heard_in_full_still_says_so():
    """Declining a false claim must not cost the true one. Losing this is how
    "anything else that day?" goes back to the model, which is the producer
    that said "those are the two available slots" with three unoffered."""
    assert exhaustion_claim_is_supported(_session(MONDAY_TIMES)) is True


def test_a_short_day_heard_in_full_still_says_so():
    """The ordinary single-day case, unchanged."""
    day = _monday(["12:00", "14:00"])
    assert exhaustion_claim_is_supported(
        _session(["12:00", "14:00"], day=day)
    ) is True


# -- Fails closed, like the rest of the module ------------------------------

def test_an_unreadable_spoken_record_declines_rather_than_claims():
    """Silence about completeness costs the caller one repetition. A wrong
    completeness claim sends them away from a free appointment.

    The fingerprint map is what `_spoken_starts_for_current_offer` refuses to
    read past — a pre-B-101 shape verifies nothing, so nothing is vouched for
    and every bookable time counts as unheard.
    """
    session = _session(MONDAY_TIMES)
    session[_SPOKEN_FP_KEY] = "nonsense"
    assert spoken_starts_for_offer(session) == set()
    assert exhaustion_claim_is_supported(session) is False


def test_a_band_filtered_day_is_still_refused_first():
    """The original B-99 arm must keep working — it is checked before the new
    one and answers a different question."""
    day = _monday(["14:00"])
    day["times_not_shown"] = 1
    day["times_found_on_day"] = 2
    session = _session(["14:00"], day=day)
    assert offer_day_hides_times(session) is True
    assert exhaustion_claim_is_supported(session) is False
