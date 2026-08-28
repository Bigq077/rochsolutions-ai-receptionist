"""
Regression: looking at Wednesday erased everything the caller heard about Friday.

B-101, CA315e501a893cd2a183a483a1f61c0c75 (27 Aug 2026, theorem_v3, Alcester).
Found while verifying B-98/B-99/B-100 — it is the reason those fixes needed two
round trips to reach one appointment.

    09:21:19  Friday 28 August, band "afternoons"  ->  ["14:00"]
    09:21:20  "Friday 28th August — the available time is two in the afternoon."
    ...       (a Wednesday lookup had run at 09:20:56, replacing available_days)
    09:21:32  Friday again -> ["14:00"], NO band-spent line
    09:21:42  caller: "um you don't have midday on that day by any chance"
    09:21:50  band ... is SPENT -> ["12:00","14:00"]   <- only after they asked

Friday 28 August holds midday AND two in the afternoon. B-98 opens such a day
once every in-band time has been SPOKEN — and it could not tell, because the
Wednesday lookup had wiped the record.

THE CAUSE. One fingerprint covered the WHOLE payload:

    fp = f"{len(flat)}|{flat[0]['start']}|{flat[-1]['start']}"
    if session.get(_SPOKEN_FP_KEY) != fp:
        session[_SPOKEN_KEY] = []          # every day, not just the changed one

so any lookup for any day dropped the record for every day. The record is a set
of ISO starts and an ISO start already names its own day, so the DATA was always
day-separable; only the guard was not.

THE FIX is granularity, not removal. The self-healing property that justified
the reset is kept exactly — a day whose slot set has really moved still drops
its record, because a stale one would hide times, which is the B-97 family —
but it is now decided per day, and a day the new payload does not mention keeps
what it knew.

FAILS CLOSED on the pre-B-101 single-string shape (a call in flight across the
deploy): nothing can be verified from it, so nothing is trusted.
"""
from __future__ import annotations

import inspect

from app.tools.slot_followup import (
    _SPOKEN_FP_KEY,
    _SPOKEN_KEY,
    record_spoken_slots,
    remaining_unspoken,
    spoken_starts_for_offer,
    unspoken_remain_on_day,
)

FRI, WED = "2026-08-28", "2026-09-02"


def _day(date: str, times: list[str], found: int | None = None) -> dict:
    found = len(times) if found is None else found
    return {
        "date": date,
        "day_label": date,
        "slot_times": times,
        "slot_times_spoken": times,
        "slots": [{"start": f"{date}T{t}:00+01:00", "end": ""} for t in times],
        "times_found_on_day": found,
        "times_not_shown": max(0, found - len(times)),
    }


def _iso(date: str, time: str) -> str:
    return f"{date}T{time}:00+01:00"


def _heard(session: dict, date: str, time: str) -> None:
    record_spoken_slots(session, [{"start": _iso(date, time)}])


# ---------------------------------------------------------------------------
# The live defect
# ---------------------------------------------------------------------------
def test_the_live_defect_a_detour_to_another_day_keeps_fridays_record():
    session: dict = {"available_days": [_day(FRI, ["14:00"], found=2)]}
    _heard(session, FRI, "14:00")

    # The caller asks about Wednesday. A whole new payload.
    session["available_days"] = [_day(WED, ["14:00"], found=2)]
    _heard(session, WED, "14:00")

    # ...and comes back to Friday.
    session["available_days"] = [_day(FRI, ["14:00"], found=2)]

    assert _iso(FRI, "14:00")[:19] in spoken_starts_for_offer(session), (
        "Friday's two o'clock was forgotten — B-98 cannot open the day and the "
        "caller has to name the hidden time themselves"
    )


def test_both_days_survive_in_the_record():
    session: dict = {"available_days": [_day(FRI, ["14:00"])]}
    _heard(session, FRI, "14:00")
    session["available_days"] = [_day(WED, ["14:00"])]
    _heard(session, WED, "14:00")

    assert sorted(session[_SPOKEN_KEY]) == [
        _iso(FRI, "14:00")[:19], _iso(WED, "14:00")[:19],
    ]


def test_a_day_the_payload_does_not_mention_is_left_alone():
    """The core of it: absence is not change."""
    session: dict = {"available_days": [_day(FRI, ["14:00"])]}
    _heard(session, FRI, "14:00")
    session["available_days"] = [_day(WED, ["10:00", "14:00"])]
    record_spoken_slots(session, [])

    assert _iso(FRI, "14:00")[:19] in session[_SPOKEN_KEY]


# ---------------------------------------------------------------------------
# The protection the reset existed for — kept, at day granularity
# ---------------------------------------------------------------------------
def test_a_day_whose_slots_really_moved_still_drops_its_record():
    """A stale record would hide times, which is the whole B-97 family. That
    must still self-heal — just for the day it applies to.

    B-115 changed the FIXTURE, not the subject. This used to grow Friday from
    [14:00] to [09:00, 14:00] and call that "moved" — but the caller's 14:00
    was still there, and the 09:00 beside it was unheard and still offered, so
    nothing was hidden and nothing needed dropping. That shape is what B-98
    does to a day every time it opens a spent band, and reading it as removal
    is the defect B-115 fixes. The 14:00 now actually GOES, which is the case
    this test was written for.
    """
    session: dict = {"available_days": [_day(FRI, ["14:00"])]}
    _heard(session, FRI, "14:00")

    session["available_days"] = [_day(FRI, ["09:00"])]   # the heard 14:00 is gone
    assert spoken_starts_for_offer(session) == set()
    record_spoken_slots(session, [])
    assert session[_SPOKEN_KEY] == []


def test_a_day_that_merely_grew_keeps_its_record():
    """The other side of the same line (B-115)."""
    session: dict = {"available_days": [_day(FRI, ["14:00"])]}
    _heard(session, FRI, "14:00")

    session["available_days"] = [_day(FRI, ["09:00", "14:00"])]
    assert spoken_starts_for_offer(session) == {_iso(FRI, "14:00")[:19]}


def test_only_the_day_that_moved_is_dropped():
    session: dict = {"available_days": [_day(FRI, ["14:00"])]}
    _heard(session, FRI, "14:00")
    session["available_days"] = [_day(WED, ["14:00"])]
    _heard(session, WED, "14:00")

    session["available_days"] = [_day(FRI, ["09:00"])]   # Friday only, 14:00 gone
    record_spoken_slots(session, [])

    assert session[_SPOKEN_KEY] == [_iso(WED, "14:00")[:19]], (
        "Wednesday was collateral damage — the whole point of per-day scoping"
    )


# ---------------------------------------------------------------------------
# What the record feeds
# ---------------------------------------------------------------------------
def test_the_more_times_tail_is_accurate_across_a_day_change():
    """unspoken_remain_on_day decides the "a few others that day" claim."""
    session: dict = {"available_days": [_day(FRI, ["12:00", "14:00"])]}
    _heard(session, FRI, "12:00")
    _heard(session, FRI, "14:00")

    session["available_days"] = [_day(WED, ["14:00"])]          # a detour
    _heard(session, WED, "14:00")
    session["available_days"] = [_day(FRI, ["12:00", "14:00"])]  # and back

    assert unspoken_remain_on_day(session, FRI) is False, (
        "she would promise times the caller has already heard"
    )


def test_the_follow_up_does_not_re_offer_a_heard_slot_after_a_detour():
    session: dict = {"available_days": [_day(FRI, ["12:00", "14:00"])]}
    _heard(session, FRI, "12:00")
    session["available_days"] = [_day(WED, ["14:00"])]
    _heard(session, WED, "14:00")
    session["available_days"] = [_day(FRI, ["12:00", "14:00"])]

    starts = {s["start"][:19] for s in remaining_unspoken(session)}
    assert _iso(FRI, "12:00")[:19] not in starts
    assert _iso(FRI, "14:00")[:19] in starts


# ---------------------------------------------------------------------------
# Fails closed
# ---------------------------------------------------------------------------
def test_the_pre_b101_string_shape_verifies_nothing():
    """A call in flight across the deploy carries the old single-string
    fingerprint. It cannot vouch for any day, so nothing is trusted — the same
    direction the old whole-payload mismatch took."""
    session: dict = {
        "available_days": [_day(FRI, ["14:00"])],
        _SPOKEN_KEY: [_iso(FRI, "14:00")[:19]],
        _SPOKEN_FP_KEY: "1|2026-08-28T14:00:00+01:00|2026-08-28T14:00:00+01:00",
    }
    assert spoken_starts_for_offer(session) == set()


def test_the_read_only_accessor_never_mutates():
    """It feeds a Gate 5 text guard, which must never be the thing that clears
    a booking record on its way past."""
    session: dict = {
        "available_days": [_day(FRI, ["14:00"])],
        _SPOKEN_KEY: [_iso(FRI, "14:00")[:19]],
        _SPOKEN_FP_KEY: "stale-string",
    }
    before = dict(session)
    spoken_starts_for_offer(session)
    assert session[_SPOKEN_KEY] == before[_SPOKEN_KEY]
    assert session[_SPOKEN_FP_KEY] == before[_SPOKEN_FP_KEY]


def test_it_never_raises_on_junk():
    for junk in ({}, {"available_days": None}, {"available_days": "nonsense"},
                 {"available_days": [None]}, {"available_days": [{}]},
                 {"available_days": [_day(FRI, ["14:00"])], _SPOKEN_FP_KEY: 7},
                 {"available_days": [_day(FRI, ["14:00"])], _SPOKEN_KEY: [None]}):
        assert isinstance(spoken_starts_for_offer(junk), set), junk


def test_recording_adds_only_what_it_was_given():
    """Persistence makes any OVER-record permanent rather than transient, so
    the writer must stay strictly literal: it records the starts handed to it
    and invents nothing."""
    session: dict = {"available_days": [_day(FRI, ["12:00", "14:00"])]}
    _heard(session, FRI, "12:00")
    assert session[_SPOKEN_KEY] == [_iso(FRI, "12:00")[:19]]


# ---------------------------------------------------------------------------
# Structural
# ---------------------------------------------------------------------------
def test_no_whole_payload_fingerprint_survives():
    """One global fingerprint over every day IS the defect. If it comes back,
    so does the two-round-trip call."""
    import app.tools.slot_followup as sf

    src = inspect.getsource(sf).replace("\r\n", "\n")
    assert "_day_fingerprints" in src
    assert "def _availability_fingerprint" not in src, (
        "the whole-payload fingerprint is back"
    )
