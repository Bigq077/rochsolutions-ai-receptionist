"""
Regression: the day you come back to is judged against the day you detoured to.

B-102, CA102f053758f4720339a5278a98fc8b9f (27 Aug 2026, theorem_v3, Alcester).
Found verifying B-101 on a live call. B-101 WORKED -- and the two-round-trip
shape it was aimed at survived anyway, one gate further down.

    10:37:23  three days offered, Friday 28 Aug -> ["14:00"] (band "afternoons")
    10:37:44  detour to Wednesday 2 Sep
              "spoken record dropped for ['2026-09-02'] ... Every other day is
              kept (B-101)"        <- Friday's 14:00 SURVIVED, as designed
    10:38:03  "the slots for the first friday that you offered me"
              -> ["14:00"], NO band-spent line, "And I've a few others that day"
    10:38:13  caller has to ask a SECOND time
    10:38:17  band 'afternoon friday 28 august 2026' is SPENT -> 12:00, 14:00

THE CAUSE. The writer and the reader disagreed about what absence means.

    _spoken_key_set               iterates `new` looking for CHANGE
                                  -> a day the payload omits is KEPT
    _spoken_starts_for_current..  iterated `new` looking for TRUST
                                  -> a day the payload omits is DISTRUSTED

`new` is built from session["available_days"], and spoken_starts_for_offer's own
docstring records that the availability builders call it while that key "still
holds the PREVIOUS fetch". So on the first return to Friday the payload in hand
was WEDNESDAY's, Friday was not a key in `new`, and the record B-101 had just
preserved was filtered straight back out.

WHY THE B-101 SUITE DID NOT CATCH IT, and the reason this file exists:
test_the_live_defect_a_detour_to_another_day_keeps_fridays_record restores the
Friday payload before asserting --

    session["available_days"] = [_day(WED, ...)]   # the detour
    session["available_days"] = [_day(FRI, ...)]   # "...and comes back"
    assert _iso(FRI, "14:00")[:19] in spoken_starts_for_offer(session)

-- which is the state at 10:38:17, the SECOND ask. Production reads at 10:38:03,
before the fetch it is feeding has overwritten anything. Same call, one moment
earlier, opposite answer. Every test below therefore asserts WITHOUT restoring
the payload; that omission is the whole point and must not be tidied away.

The veto is unchanged: a day the current payload DOES mention with a different
fingerprint has really moved, cannot vouch for what was heard on it, and is
still dropped. That is the B-97 protection the reset existed for.
"""
from __future__ import annotations

import datetime as _dt

from app.tools.receptionist_tools import _days_where_the_band_is_spent
from app.tools.slot_followup import (
    _SPOKEN_FP_KEY,
    _SPOKEN_KEY,
    record_spoken_slots,
    spoken_starts_for_offer,
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


def _dt_at(date: str, time: str) -> _dt.datetime:
    return _dt.datetime.fromisoformat(_iso(date, time))


def _mid_detour() -> dict:
    """The session state at 10:38:03: Friday heard, WEDNESDAY's payload in hand.

    Deliberately does NOT restore the Friday payload -- see the module
    docstring. This is what the availability builder sees at the moment it asks
    what the caller has already been told.
    """
    session: dict = {"available_days": [_day(FRI, ["14:00"], found=2)]}
    _heard(session, FRI, "14:00")
    session["available_days"] = [_day(WED, ["10:00", "14:00"], found=2)]
    _heard(session, WED, "14:00")
    return session


# ---------------------------------------------------------------------------
# The live defect
# ---------------------------------------------------------------------------
def test_the_live_defect_friday_is_still_vouched_for_mid_detour():
    assert _iso(FRI, "14:00")[:19] in spoken_starts_for_offer(_mid_detour()), (
        "Friday's two o'clock was invisible while Wednesday's payload was in "
        "hand -- B-98 cannot open the day on the first ask, so the caller has "
        "to request the hidden time a second time"
    )


def test_the_band_is_spent_on_the_first_return_not_the_second():
    """The end of the chain, and the thing the caller actually experiences."""
    spoken = spoken_starts_for_offer(_mid_detour())
    day_scoped = [(_dt_at(FRI, t), _dt_at(FRI, t)) for t in ("12:00", "14:00")]
    in_band = [(_dt_at(FRI, "14:00"), _dt_at(FRI, "14:00"))]

    assert _days_where_the_band_is_spent(day_scoped, in_band, spoken) == {
        _dt.date(2026, 8, 28)
    }, "the afternoon band was re-applied to a day it had already been served"


def test_the_detoured_to_day_is_vouched_for_too():
    assert _iso(WED, "14:00")[:19] in spoken_starts_for_offer(_mid_detour())


# ---------------------------------------------------------------------------
# The veto the reset existed for -- unchanged
# ---------------------------------------------------------------------------
def test_a_day_the_payload_mentions_with_moved_slots_is_still_dropped():
    """B-97: a stale record hides times. Absence is not change; CHANGE is.

    B-115 changed the fixture, not the subject — see the sibling test of the
    same name in test_asking_about_one_day_does_not_erase_another. Friday used
    to GAIN a 09:00 here, which is growth and is what B-98 does to every day it
    opens. The heard 14:00 now actually goes.
    """
    session = _mid_detour()
    session["available_days"] = [_day(FRI, ["09:00"], found=1)]

    assert _iso(FRI, "14:00")[:19] not in spoken_starts_for_offer(session)


def test_the_moved_day_does_not_take_the_others_with_it():
    session = _mid_detour()
    session["available_days"] = [_day(FRI, ["09:00", "14:00"], found=2)]

    assert _iso(WED, "14:00")[:19] in spoken_starts_for_offer(session)


# ---------------------------------------------------------------------------
# Fail-closed edges
# ---------------------------------------------------------------------------
def test_a_day_nothing_ever_vouched_for_is_not_trusted():
    """Appearing in the record is not enough -- `old` has to have seen it."""
    session = _mid_detour()
    session[_SPOKEN_KEY] = list(session[_SPOKEN_KEY]) + ["2026-12-25T09:00:00"]

    assert "2026-12-25T09:00:00" not in spoken_starts_for_offer(session)


def test_the_pre_b101_string_shape_still_verifies_nothing():
    session = _mid_detour()
    session[_SPOKEN_FP_KEY] = "12|a|b"

    assert spoken_starts_for_offer(session) == set()


def test_an_empty_payload_is_absence_not_change():
    """Nothing in hand is no evidence of movement, so the record stands -- the
    same answer the writer gives, where `changed` is empty and all is kept."""
    session = _mid_detour()
    session["available_days"] = []

    assert _iso(FRI, "14:00")[:19] in spoken_starts_for_offer(session)


def test_no_record_means_no_opinion():
    session = _mid_detour()
    session[_SPOKEN_KEY] = []

    assert spoken_starts_for_offer(session) == set()


def test_the_accessor_never_mutates():
    session = _mid_detour()
    before = (list(session[_SPOKEN_KEY]), dict(session[_SPOKEN_FP_KEY]))

    spoken_starts_for_offer(session)

    assert (session[_SPOKEN_KEY], session[_SPOKEN_FP_KEY]) == before


def test_it_never_raises_on_junk():
    for junk in (
        {},
        {"available_days": None},
        {_SPOKEN_FP_KEY: None},
        {_SPOKEN_FP_KEY: {}, _SPOKEN_KEY: None},
        {_SPOKEN_FP_KEY: {}, _SPOKEN_KEY: [None, 5, ""]},
    ):
        assert isinstance(spoken_starts_for_offer(junk), set), junk
