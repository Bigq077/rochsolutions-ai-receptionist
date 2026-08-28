# tests/regression/test_b112_band_hidden_times_are_still_more_times.py
"""
B-112 — CAf5c4febac49f0a47692a6a61d6f0af0d, 28 Aug 2026, theorem_v3, Alcester,
build cefd70f82f04.

  10:00:29  caller: "can you show me the dates on the 8th then please"
            -> the model sent date_hint "Tuesday 8 September 2026 morning".
               Nobody had said morning at any point in the call.
  10:00:33  Acuity returned SEVEN slots for 2026-09-08. The morning band kept
            two. The payload was honest about it: times_found_on_day 7,
            times_not_shown 5, and the tool set more_times=True.
  10:00:35  "The available slots for Tuesday 8th September are — Number 1,
            nine in the morning. Number 2, ten in the morning.
            Any of those work?"

            No "and I've a few others that day". The Gate 5 log shows neither
            a `stripped` nor an `appended` action.
  10:00:46  caller: "no none of those work"
  10:00:53  "No problem — would the week of the 14th of September suit you?"

Five bookable slots on the 8th, and the caller was moved to another week.

Root cause. llm_stream reads the tool's more_times, and then, when the readout
carried "Number N" anchors, REPLACES it with unspoken_remain_on_day() -- on the
documented grounds that the cumulative record subsumes every producer of the
flag. It did not. That function walked session["available_days"], which holds
the SURVIVORS of the band; both survivors had just been spoken, so it returned
False and the true flag was discarded.

A band-hidden slot is never in available_days at all, so no walk over that list
can ever see it, and the caller cannot possibly have heard it.

This is the third door onto B-97's false completeness:

  B-97   the model said "that's the only one we have that day"
  B-100  the same claim in the un-numbered readout's opener
  B-112  the claim made by SILENCE -- the tail that says otherwise, missing

The promise stays keepable: B-98 opens a band-spent day on the next lookup.
"""
from __future__ import annotations

from app.tools.slot_followup import (
    record_spoken_slots,
    reconcile_extra_slots_claim,
    unspoken_remain_on_day,
)

DAY = "2026-09-08"

# Tuesday 8 September as the payload really described it: seven times found,
# two surviving the band the model invented.
BANDED_DAY = {
    "date": DAY,
    "day_label": "Tuesday 8th September",
    "slot_times": ["09:00", "10:00"],
    "slot_times_spoken": ["nine in the morning", "ten in the morning"],
    "slots": [
        {"start": f"{DAY}T09:00:00+01:00", "end": f"{DAY}T09:30:00+01:00"},
        {"start": f"{DAY}T10:00:00+01:00", "end": f"{DAY}T10:30:00+01:00"},
    ],
    "times_found_on_day": 7,
    "times_not_shown": 5,
}

# Verbatim, from the 10:00:35 TTS chunks.
LIVE_READOUT = (
    "The available slots for Tuesday 8th September are — "
    "Number 1, nine in the morning. Number 2, ten in the morning. "
    "Any of those work?"
)


def _session_after_the_readout() -> dict:
    session = {"available_days": [BANDED_DAY]}
    record_spoken_slots(session, BANDED_DAY["slots"])
    return session


def test_a_day_whose_band_hides_times_still_has_more_after_both_are_spoken():
    """The defect, at the seam that produced it."""
    session = _session_after_the_readout()
    assert unspoken_remain_on_day(session, DAY) is True, (
        "both survivors were spoken, but five bookable times on the 8th were "
        "never in available_days to be walked"
    )


def test_the_tail_reaches_the_caller():
    """End to end through the reconciler llm_stream actually calls."""
    session = _session_after_the_readout()
    text, action = reconcile_extra_slots_claim(
        LIVE_READOUT,
        unspoken_remain_on_day(session, DAY),
        2,
        allow_append=True,
    )
    assert action == "appended", "the caller heard nothing about the other five"
    assert "few others that day" in text
    # Before the closing question, never after it — the caller's "yes" has to
    # stay unambiguous.
    assert text.rstrip().endswith("?")
    assert text.index("few others that day") < text.index("Any of those work?")


def test_an_unbanded_day_the_caller_has_heard_in_full_is_still_finished():
    """The other direction. Over-promising is the harm this must not add."""
    whole_day = {
        **BANDED_DAY,
        "times_found_on_day": 2,
        "times_not_shown": 0,
    }
    session = {"available_days": [whole_day]}
    record_spoken_slots(session, whole_day["slots"])
    assert unspoken_remain_on_day(session, DAY) is False

    text, action = reconcile_extra_slots_claim(
        LIVE_READOUT, False, 2, allow_append=True,
    )
    assert action in ("unchanged", "stripped")
    assert "few others that day" not in text


def test_a_day_heard_in_full_before_a_band_shrank_it_is_not_re_promised():
    """Counting, not `times_not_shown > 0`.

    The caller heard all seven UNBANDED on an earlier turn; a later banded
    fetch shows two and reports five hidden. Nothing is left to offer, and a
    bare hidden-count test would have claimed otherwise.
    """
    session = {"available_days": [BANDED_DAY]}
    record_spoken_slots(session, [
        {"start": f"{DAY}T{h:02d}:00:00+01:00"} for h in (9, 10, 11, 13, 14, 15, 16)
    ])
    assert unspoken_remain_on_day(session, DAY) is False


def test_a_reader_whose_payload_omits_the_count_behaves_as_before():
    """VE's diary and the generic Google path must not change shape."""
    bare = {k: v for k, v in BANDED_DAY.items()
            if k not in ("times_found_on_day", "times_not_shown")}
    session = {"available_days": [bare]}
    assert unspoken_remain_on_day(session, DAY) is True   # nothing spoken yet
    record_spoken_slots(session, bare["slots"])
    assert unspoken_remain_on_day(session, DAY) is False
