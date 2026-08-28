# tests/regression/test_b115_a_day_that_grew_is_not_a_day_that_changed.py
"""
B-115 - CA0f8ffe7b5f0fbb576fdc956df31c545b, 28 Aug 2026, theorem_v3, Alcester.

Tuesday 8 September holds seven times: 09:00 10:00 12:00 13:00 14:00 15:00 16:00.

  10:40:37  a band-filtered payload shows 2 of them; the caller hears both.
            day fingerprint: 2|...T09:00|...T10:00
  10:40:56  band ... is SPENT on ['2026-09-08'] -- opened to its hidden times
            day fingerprint: 7|...T09:00|...T16:00
  10:40:57  spoken record dropped for ['2026-09-08']

B-98 opens a day precisely BECAUSE its in-band times have been spoken, so the
act of opening it destroyed the record that justified opening it. The two slots
were still there; five more had appeared beside them. An equality test on
`count|first|last` cannot tell a day that GREW from a day that CHANGED.

SCOPE, stated because it is easy to get wrong later: nothing the caller heard
was lost on that call. The availability presentation is spoken-blind - it caps
to the chronologically first three whatever the record says - so 09:00 and
10:00 were re-read regardless of this drop. This is a LATENT fault and a
prerequisite, not the cause of that re-offer. Any presentation that later
filters by "already heard" reads this record and would find it empty at exactly
the moment it matters.

TWO functions ask "is this record still trustworthy", and they disagreed once
before (B-102), which let the B-101 shape survive its own fix. The rule now
lives in _day_record_survives and both call it.
"""
from __future__ import annotations

import inspect

from app.tools.slot_followup import (
    _day_record_survives,
    _spoken_key_set,
    _spoken_starts_for_current_offer,
    record_spoken_slots,
    unspoken_remain_on_day,
)

DAY = "2026-09-08"
ALL_SEVEN = ["09:00", "10:00", "12:00", "13:00", "14:00", "15:00", "16:00"]


def _day(times):
    return {
        "date": DAY,
        "day_label": "Tuesday 8th September",
        "slots": [{"start": f"{DAY}T{t}:00+01:00"} for t in times],
    }


def _heard_the_band_then_opened_the_day():
    """The live sequence: banded payload, both spoken, then B-98 opens it."""
    session = {"available_days": [_day(["09:00", "10:00"])]}
    record_spoken_slots(session, session["available_days"][0]["slots"])
    session["available_days"] = [_day(ALL_SEVEN)]      # B-98 opens the day
    return session


# ---------------------------------------------------------------------------
# The live defect
# ---------------------------------------------------------------------------

def test_opening_a_spent_band_keeps_what_the_caller_heard():
    session = _heard_the_band_then_opened_the_day()
    assert _spoken_key_set(session) == {
        f"{DAY}T09:00:00", f"{DAY}T10:00:00",
    }


def test_the_read_only_sibling_agrees():
    """B-102: the reader used to trust the other way round from the writer."""
    session = _heard_the_band_then_opened_the_day()
    assert _spoken_starts_for_current_offer(session) == {
        f"{DAY}T09:00:00", f"{DAY}T10:00:00",
    }


def test_the_day_is_not_reported_finished_after_it_opens():
    session = _heard_the_band_then_opened_the_day()
    assert unspoken_remain_on_day(session, DAY) is True


# ---------------------------------------------------------------------------
# A day that really moved still drops - the protection B-101 existed for
# ---------------------------------------------------------------------------

def test_a_slot_booked_away_by_someone_else_drops_the_record():
    session = {"available_days": [_day(["09:00", "10:00", "12:00"])]}
    record_spoken_slots(session, session["available_days"][0]["slots"][:2])
    # 09:00 goes - taken by another caller between lookups.
    session["available_days"] = [_day(["10:00", "12:00"])]
    assert _spoken_key_set(session) == set()


def test_a_day_shrinking_to_slots_the_caller_never_heard_drops_the_record():
    session = {"available_days": [_day(["09:00", "10:00"])]}
    record_spoken_slots(session, session["available_days"][0]["slots"])
    session["available_days"] = [_day(["15:00", "16:00"])]
    assert _spoken_key_set(session) == set()


def test_changing_location_clears_the_record():
    """A 9am at Redditch is not the 9am the caller heard at Alcester, and the
    old equality test only caught that when the slot counts happened to
    differ."""
    session = {
        "selected_location": "alcester",
        "available_days": [_day(["09:00", "10:00"])],
    }
    record_spoken_slots(session, session["available_days"][0]["slots"])
    assert _spoken_key_set(session)                       # recorded under alcester
    session["selected_location"] = "redditch"
    session["available_days"] = [_day(["09:00", "10:00", "11:00"])]
    assert _spoken_key_set(session) == set()


# ---------------------------------------------------------------------------
# B-101's own guarantee must survive
# ---------------------------------------------------------------------------

def test_a_lookup_for_another_day_leaves_this_one_alone():
    other = "2026-09-02"
    session = {"available_days": [_day(["09:00", "10:00"])]}
    record_spoken_slots(session, session["available_days"][0]["slots"])
    session["available_days"] = [{
        "date": other, "day_label": "Wednesday 2nd September",
        "slots": [{"start": f"{other}T14:00:00+01:00"}],
    }]
    assert _spoken_key_set(session) == {
        f"{DAY}T09:00:00", f"{DAY}T10:00:00",
    }


# ---------------------------------------------------------------------------
# The predicate itself
# ---------------------------------------------------------------------------

def test_a_day_absent_from_the_payload_keeps_its_record():
    assert _day_record_survives(None, [f"{DAY}T09:00:00"]) is True


def test_growth_survives_and_loss_does_not():
    heard = [f"{DAY}T09:00:00", f"{DAY}T10:00:00"]
    grew = {f"{DAY}T{t}:00" for t in ALL_SEVEN}
    assert _day_record_survives(grew, heard) is True
    assert _day_record_survives({f"{DAY}T10:00:00"}, heard) is False
    assert _day_record_survives(set(), heard) is False
    assert _day_record_survives(set(), []) is True        # nothing to lose


# ---------------------------------------------------------------------------
# The two consumers must not drift apart again
# ---------------------------------------------------------------------------

def test_both_consumers_use_the_shared_predicate():
    """B-102 was the writer and the reader deciding trust differently. Neither
    may carry its own copy of the rule."""
    for fn in (_spoken_key_set, _spoken_starts_for_current_offer):
        src = inspect.getsource(fn)
        assert "_day_record_survives(" in src, f"{fn.__name__} has its own rule"
        assert "] == old[" not in src, f"{fn.__name__} still compares fingerprints"
