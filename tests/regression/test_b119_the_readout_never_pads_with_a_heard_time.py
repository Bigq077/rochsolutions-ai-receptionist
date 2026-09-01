# tests/regression/test_b119_the_readout_never_pads_with_a_heard_time.py
"""
B-119 - CA9bafe3615359d22f513f77fa89d4f667, 28 Aug 2026, theorem_v3, Alcester,
build e430d7ec (B-116, B-117 and B-118 ALL already live).

The whole slot family fired correctly on this call and the caller still heard a
time twice.

  13:57:06  "Number 1, nine in the morning. Number 2, ten in the morning."
  13:57:23  band SPENT -> B-98 opens the day to all seven
            B-117: "I've given you all the mornings I have that day, I'm afraid."
            B-116: "Number 1, midday. Number 2, one. Number 3, two."
  13:57:37  caller: "what have you got on tuesday the 8th"
  13:57:45  check_availability BLOCKED - already_retrieved
            B-118 hands back first_day built by _cap_presented_slots
  13:57:46  "Number 1, NINE IN THE MORNING. Number 2, three in the afternoon.
             Number 3, four in the afternoon."

Twenty-three seconds after saying she had given out all the mornings, she led
with a morning. The caller said "i said 4 in the afternoon works", then hung up
without booking. Judge score 1.

Nothing in B-116/117/118 was wrong. The defect is one branch they all share:
five of the seven times had been heard, so `unheard` held two against a limit of
three, and the selector padded the list back up to three from the front. Index 0
is 09:00. `sorted()` then put it first.

The rule B-119 sets: a short unheard list is the answer. Pad ONLY when nothing
is unheard, which is a genuine repeat request and must still be served.
"""
from __future__ import annotations

from app.tools.receptionist_tools import _cap_presented_slots
from app.tools.slot_followup import choose_presented_indices, record_spoken_slots

DAY = "2026-09-08"
ALL_SEVEN = ["09:00", "10:00", "12:00", "13:00", "14:00", "15:00", "16:00"]
# What the caller had heard by 13:57:45: the banded pair, then B-116's three.
HEARD_BY_THE_REFUSAL = ["09:00", "10:00", "12:00", "13:00", "14:00"]


def _slots(times):
    return [{"start": f"{DAY}T{t}:00+01:00", "end": f"{DAY}T{t}:59+01:00"} for t in times]


def _day(times):
    return {
        "date": DAY,
        "day_label": "Tuesday 8th September",
        "slot_times": list(times),
        "slot_times_spoken": [f"spoken-{t}" for t in times],
        "slots": _slots(times),
        "times_found_on_day": len(ALL_SEVEN),
        "times_not_shown": len(ALL_SEVEN) - len(times),
    }


def _session_at_the_refusal():
    session = {"available_days": [_day(ALL_SEVEN)]}
    record_spoken_slots(session, _slots(HEARD_BY_THE_REFUSAL))
    return session


# ---------------------------------------------------------------------------
# The defect
# ---------------------------------------------------------------------------
def test_the_readout_never_pads_with_a_heard_time():
    """Two unheard against a limit of three: speak two."""
    session = _session_at_the_refusal()
    idx = choose_presented_indices(session, _day(ALL_SEVEN), 3)
    spoken = [ALL_SEVEN[i] for i in idx]
    assert spoken == ["15:00", "16:00"], spoken
    for heard in HEARD_BY_THE_REFUSAL:
        assert heard not in spoken


def test_the_morning_is_not_read_back_after_the_band_was_declared_spent():
    """The live sentence pair, asserted as one fact: nothing B-117 called spent
    may appear in the readout that follows it."""
    session = _session_at_the_refusal()
    idx = choose_presented_indices(session, _day(ALL_SEVEN), 3)
    spoken = [ALL_SEVEN[i] for i in idx]
    mornings = [t for t in spoken if int(t[:2]) < 12]
    assert mornings == [], mornings


def test_the_refusal_payload_carries_the_short_list_too():
    """B-118 routes the refusal through _cap_presented_slots, so the branch has
    to hold there as well - that is the exact door this call came through."""
    session = _session_at_the_refusal()
    out = _cap_presented_slots({"available_days": [_day(ALL_SEVEN)]}, session)
    first = out["first_day"]
    assert first["slot_times"] == ["15:00", "16:00"], first["slot_times"]
    assert len(first["slots"]) == 2
    assert len(first["slot_times_spoken"]) == 2


def test_one_unheard_time_is_offered_alone():
    """A list of one is still the right answer. Rounding it up to three would
    mean two of the three were repeats."""
    session = {"available_days": [_day(ALL_SEVEN)]}
    record_spoken_slots(session, _slots(ALL_SEVEN[:-1]))
    idx = choose_presented_indices(session, _day(ALL_SEVEN), 3)
    assert [ALL_SEVEN[i] for i in idx] == ["16:00"]


# ---------------------------------------------------------------------------
# What must NOT change
# ---------------------------------------------------------------------------
def test_a_genuine_repeat_is_still_served_in_full():
    """"Could you repeat those?" after hearing everything is not this defect.
    Nothing is unheard, so the readout is served IN FULL rather than shortened
    -- that is the reason the pad exists at all.

    Since 1 Sept the three are spread across the day instead of taken
    chronologically. B-119 is about LENGTH, not order: the property is that a
    genuine repeat still gets three, so the count is asserted first.
    """
    session = {"available_days": [_day(ALL_SEVEN)]}
    record_spoken_slots(session, _day(ALL_SEVEN)["slots"])
    idx = choose_presented_indices(session, _day(ALL_SEVEN), 3)
    assert len(idx) == 3, "a genuine repeat is served in full"
    assert [ALL_SEVEN[i] for i in idx] == ["09:00", "10:00", "16:00"]


def test_a_first_time_caller_hears_times_spread_across_the_day():
    """DELIBERATE REVERSAL -- owner decision, 1 Sept 2026. Was
    `test_a_first_time_caller_is_unaffected`, pinning "byte-identical to the
    plain chronological slice".

    See the twin of this test in test_b116_* for the reasoning. B-119 is
    unaffected either way: with no spoken record there is nothing to pad WITH.
    """
    idx = choose_presented_indices({}, _day(ALL_SEVEN), 3)
    assert len(idx) == 3
    assert idx == [0, 1, 6]


def test_more_unheard_than_the_limit_still_caps_at_three():
    """The cap is the cap. B-119 only removes the top-up, never the ceiling.

    Six times are unheard against a limit of three, so the ceiling is what is
    under test here. The 1 Sept spread rule decides WHICH three, and 09:00 --
    the one time already heard -- stays out of them either way.
    """
    session = {"available_days": [_day(ALL_SEVEN)]}
    record_spoken_slots(session, _slots(["09:00"]))
    idx = choose_presented_indices(session, _day(ALL_SEVEN), 3)
    picked = [ALL_SEVEN[i] for i in idx]
    assert len(picked) == 3, "the ceiling holds"
    assert "09:00" not in picked
    assert picked == ["10:00", "12:00", "16:00"]


def test_available_days_still_carries_every_bookable_time():
    """DTMF, _resolve_slot_iso and the unspoken follow-up read available_days.
    Speaking two must never shrink what is bookable."""
    session = _session_at_the_refusal()
    out = _cap_presented_slots({"available_days": [_day(ALL_SEVEN)]}, session)
    assert out["available_days"][0]["slot_times"] == ALL_SEVEN
    assert len(out["available_days"][0]["slots"]) == 7


def test_the_arrays_stay_aligned_when_the_list_is_short():
    """A label from one slot against a start from another books the wrong time.
    Two entries must mean the same two everywhere."""
    session = _session_at_the_refusal()
    out = _cap_presented_slots({"available_days": [_day(ALL_SEVEN)]}, session)
    first = out["first_day"]
    for label, start in zip(first["slot_times_spoken"], first["slots"]):
        assert label == f"spoken-{start['start'][11:16]}"
