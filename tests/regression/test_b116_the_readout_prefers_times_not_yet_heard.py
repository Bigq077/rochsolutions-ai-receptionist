# tests/regression/test_b116_the_readout_prefers_times_not_yet_heard.py
"""
B-116 - CA13b8dc5cb8d4e0474062f1dd43a6177a, 28 Aug 2026, theorem_v3, Alcester,
build 11eee35961cf (B-115 already live).

The caller wanted Tuesday mornings. Tuesday 8 September holds seven bookable
times; a morning band kept two, and both were read out as Number 1 and Number 2.
She then asked for more, and B-98 did its job:

  12:13:23  band 'morning tuesday 8 september 2026' is SPENT on ['2026-09-08']
            -- every slot it kept there has been spoken, so the day is opened
            slot_times 09:00 10:00 12:00 13:00 14:00 15:00 16:00

  12:13:24  "The available slots for Tuesday 8th September are -- Number 1,
             nine in the morning. Number 2, ten in the morning. Number 3,
             midday."

Two of the three were the two she had just heard. The retrieval was right and
the readout threw it away, because the cap took the chronologically first three
without asking what had been spoken. Judge score 2, outcome=abandoned.

THIRTY SECONDS LATER, the same call answered the same question correctly:

  12:13:40  "On Tuesday 8th September I ALSO have one in the afternoon, two in
             the afternoon, three in the afternoon, or four in the afternoon"

That is slot_followup's unspoken follow-up, which subtracts what was spoken.
So the system held a right answer and a wrong answer to one question, and which
one a caller got depended on which route their wording took. B-116 is not a
third rule: it points both presentation caps at the record slot_followup
already reads.

B-115 is a prerequisite. The cap runs AFTER session["available_days"] has been
overwritten with the opened day, so the record is validated against a payload
that GREW from 2 slots to 7. Before B-115 that growth dropped the record, the
spoken set read empty, and this preference would have been a no-op on the one
turn it exists for.
"""
from __future__ import annotations

import inspect

from app.tools.receptionist_tools import _cap_presented_slots
from app.tools.slot_followup import (
    choose_presented_indices,
    record_spoken_slots,
)

DAY = "2026-09-08"
ALL_SEVEN = ["09:00", "10:00", "12:00", "13:00", "14:00", "15:00", "16:00"]
HEARD = ["09:00", "10:00"]


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


def _session_after_the_band_was_spent():
    """The live sequence: banded pair heard, then B-98 opens the day."""
    session = {"available_days": [_day(HEARD)]}
    record_spoken_slots(session, _day(HEARD)["slots"])
    session["available_days"] = [_day(ALL_SEVEN)]     # B-98 opened it
    return session


# ---------------------------------------------------------------------------
# The defect
# ---------------------------------------------------------------------------
def test_the_readout_does_not_hand_back_the_times_just_heard():
    session = _session_after_the_band_was_spent()
    idx = choose_presented_indices(session, _day(ALL_SEVEN), 3)
    spoken = [ALL_SEVEN[i] for i in idx]
    assert spoken == ["12:00", "13:00", "14:00"], spoken
    assert "09:00" not in spoken and "10:00" not in spoken


def test_the_cap_used_by_the_other_executors_prefers_unheard_too():
    """VE and JV reach the readout through _cap_presented_slots, not the
    Acuity block, so the preference has to live in both or it is half a fix."""
    session = _session_after_the_band_was_spent()
    out = _cap_presented_slots({"available_days": [_day(ALL_SEVEN)]}, session)
    first = out["first_day"]
    assert first["slot_times"] == ["12:00", "13:00", "14:00"]
    assert first.get("more_times") is True


def test_the_acuity_readout_asks_which_three_not_just_how_many():
    """Theorem short-circuits to the Acuity executor, which has always had its
    OWN [:3]. Fixing only _cap_presented_slots would leave Mark on the bug."""
    src = inspect.getsource(
        __import__("app.tools.receptionist_tools", fromlist=["x"])
        ._check_availability_acuity
    )
    assert "_presented_indices(session, _fd, 3)" in src
    assert '(_fd.get("slot_times") or [])[:3]' not in src


# ---------------------------------------------------------------------------
# What must NOT change
# ---------------------------------------------------------------------------
def test_a_caller_who_has_heard_everything_is_not_starved():
    """The preference REORDERS; it must never withhold. When every time has
    been heard there is nothing unheard to lead with, and the readout falls
    back to the chronological three it always gave."""
    session = {"available_days": [_day(ALL_SEVEN)]}
    record_spoken_slots(session, _day(ALL_SEVEN)["slots"])
    idx = choose_presented_indices(session, _day(ALL_SEVEN), 3)
    assert [ALL_SEVEN[i] for i in idx] == ["09:00", "10:00", "12:00"]


def test_a_partly_heard_day_tops_up_in_chronological_order():
    """Two unheard on a three-slot readout: lead with them, then fill from the
    front. The result is still spoken soonest-first."""
    session = {"available_days": [_day(ALL_SEVEN)]}
    record_spoken_slots(session, _slots(["12:00", "13:00", "14:00", "15:00", "16:00"]))
    idx = choose_presented_indices(session, _day(ALL_SEVEN), 3)
    picked = [ALL_SEVEN[i] for i in idx]
    assert picked == ["09:00", "10:00", "12:00"]
    assert picked == sorted(picked)


def test_a_first_time_caller_is_unaffected():
    """No record yet -- byte-identical to the slice this replaced."""
    idx = choose_presented_indices({}, _day(ALL_SEVEN), 3)
    assert idx == [0, 1, 2]


def test_the_three_arrays_stay_aligned():
    """slot_times_spoken[i] must still describe slots[i]. Slicing them
    independently against a non-contiguous choice would speak one time and
    book another -- the failure this whole family exists to prevent."""
    session = _session_after_the_band_was_spent()
    out = _cap_presented_slots({"available_days": [_day(ALL_SEVEN)]}, session)
    first = out["first_day"]
    for t, label, slot in zip(
        first["slot_times"], first["slot_times_spoken"], first["slots"]
    ):
        assert label == f"spoken-{t}"
        assert slot["start"].startswith(f"{DAY}T{t}")


def test_a_desynchronised_day_falls_back_rather_than_guessing():
    session = _session_after_the_band_was_spent()
    broken = _day(ALL_SEVEN)
    broken["slot_times_spoken"] = broken["slot_times_spoken"][:4]
    assert choose_presented_indices(session, broken, 3) == [0, 1, 2]


def test_a_day_at_or_under_the_limit_is_untouched():
    session = _session_after_the_band_was_spent()
    assert choose_presented_indices(session, _day(HEARD), 3) == [0, 1]


def test_both_presentation_paths_use_the_one_predicate():
    """The point of B-116. If a third [:3] appears on a readout path, this
    fails rather than a caller hearing the same time twice."""
    import app.tools.receptionist_tools as rt

    cap = inspect.getsource(rt._cap_presented_slots)
    assert "_presented_indices(" in cap
    assert "value[:per_day]" not in cap


def test_an_exhausted_band_re_requested_offers_new_times_not_a_repeat():
    """OPEN DECISION, recorded here so it is a choice and not a side effect.

    The last turn of CA13b8dc5cb8 was "and what about tuesday morning again".
    Every morning slot had already been read out, so B-98 treats the band as
    spent and opens the day -- and this readout then leads with afternoons for
    a caller who just said "morning".

    That is deliberate. Reading back times she has already heard is the defect
    B-116 exists to remove, and afternoons are the only new true thing left to
    say about that day. The alternative -- repeating 09:00 and 10:00 because
    she named their band -- is what she abandoned the call over.

    What is NOT settled is the wording. "You have heard all the mornings; I do
    have afternoons" would carry the same times with the band acknowledged. If
    the owner wants that, it is a sentence change above this layer, not a
    different selection rule, and this test should keep passing through it.
    """
    session = _session_after_the_band_was_spent()
    out = _cap_presented_slots({"available_days": [_day(ALL_SEVEN)]}, session)
    spoken = out["first_day"]["slot_times"]
    assert all(t not in spoken for t in HEARD)
    assert out["first_day"].get("more_times") is True

