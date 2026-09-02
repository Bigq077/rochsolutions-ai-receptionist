"""
A caller who picks an offered slot by weekday has chosen, not filtered.

Vital Edge, 2026-09-02. Susie read the offer out; the caller said

    "the saturday at 6 in the evening works"

and nothing on the main path resolved it as a pick:

  * `utterance_is_slot_selection` is containment against the full spoken
    label ("Saturday the 5th at six in the evening"). The caller dropped the
    date, so the containment missed on that alone.
  * `slot_accepted_by_caller` pins a day by list POSITION or by the payload's
    full `day_label`. A bare weekday is a PARTIAL naming, which
    `day_named_by_caller` documents as Tier 2 and declines.

So an acceptance was read as a fresh time-of-day filter: `evenings` was banked
at the HARD tier, every later prompt asserted "TIME OF DAY CONFIRMED (caller
stated this explicitly)" about something the caller never said, and the model
called `check_availability` a second time. That call was refused
("already_retrieved"), which left it with no tool result and no scripted next
step - and an improvised turn is where the four-ask name-and-number defect
(test_one_booking_step_per_turn.py) came from.

THE RULE ADDED. Of the two or three days ALREADY READ OUT, does the weekday the
caller said pick exactly one? That is not the date parsing Tier 2 needs: there
is no calendar involved and no question of which week, because the candidates
are the offer itself. Deny-by-default is preserved at both ends - one weekday
word in the speech, one offered day falling on it.
"""

import pytest

from app.media_streams.connection import (
    _time_preference_tier,
    utterance_is_slot_selection,
)
from app.tools.slot_followup import (
    _offered_day_by_weekday,
    record_spoken_slots,
    slot_accepted_by_caller,
)


PICK = "the saturday at 6 in the evening works"


def _session(days_spec):
    """A session in the state it is in when the caller answers an offer."""
    days, offered, spoken, labels = [], [], [], []
    for date, label, times in days_spec:
        days.append({
            "date": date,
            "day_label": label,
            "slots": [{"start": f"{date}T{t}:00"} for t in times],
        })
        for t in times:
            offered.append({"start": f"{date}T{t}:00", "end": f"{date}T{t}:00"})
            spoken.append({"start": f"{date}T{t}:00"})
            labels.append(f"{label} at {t}")
    session = {
        "last_offered_slots": offered,
        "available_days": days,
        "slot_labels": labels,
    }
    record_spoken_slots(session, spoken)
    return session


# The offer as it was read out: one Saturday, one Monday.
OFFER = [
    ("2026-09-05", "Saturday 5th September", ["18:00"]),
    ("2026-09-07", "Monday 7th September", ["09:10"]),
]

# A fortnight's offer, where "the saturday" names two days.
TWO_SATURDAYS = [
    ("2026-09-05", "Saturday 5th September", ["18:00"]),
    ("2026-09-12", "Saturday 12th September", ["18:00"]),
]


# -- the reproduction ------------------------------------------------------

def test_the_weekday_pick_resolves_to_the_offered_slot():
    assert slot_accepted_by_caller(_session(OFFER), PICK) == "2026-09-05T18:00:00"


def test_the_containment_test_still_misses_it():
    """Stated so the fix cannot be mistaken for a change to the other door.
    `utterance_is_slot_selection` is unchanged and still says False here - the
    caller dropped the date. The resolver is what answers this now."""
    assert utterance_is_slot_selection(PICK, _session(OFFER)) is False


def test_a_resolved_acceptance_is_not_a_time_preference():
    """The gate that banked `evenings`. A pick earns tier "none", so nothing
    is written to time_of_day_preference or soft_context."""
    assert _time_preference_tier(PICK, is_slot_pick=True) == "none"
    # ...and without the resolver's verdict it would have been latched HARD,
    # which is the sentence that claimed the caller had stated it.
    assert _time_preference_tier(PICK, is_slot_pick=False) == "hard"


def test_the_gate_reads_the_resolver_and_not_only_containment():
    """The wiring, pinned against the shape of the bug: the verdict was
    computed forty lines above the gate and not read."""
    import inspect

    from app.media_streams import connection

    src = inspect.getsource(connection.WebSocketCallHandler)
    assert "is_slot_pick=bool(_is_slot_pick or _accepted)" in src, (
        "the preference gate no longer consults the accepted-slot resolver; "
        "a pick phrased in the caller's own words will be banked as a filter"
    )


# -- deny by default -------------------------------------------------------

def test_two_offered_saturdays_decline():
    """Ambiguity declines rather than guessing the nearer one."""
    assert slot_accepted_by_caller(_session(TWO_SATURDAYS), PICK) is None


def test_two_weekdays_named_is_a_comparison_not_a_pick():
    session = _session(OFFER)
    assert slot_accepted_by_caller(session, "is it saturday or monday?") is None


def test_a_weekday_not_in_the_offer_declines():
    assert slot_accepted_by_caller(_session(OFFER), "thursday works") is None


def test_a_request_for_more_slots_is_never_a_pick():
    """Step 1 of the resolver still owns this, and must keep owning it: reading
    a 'more times' request as a pick sets a filter that deletes slots (B-90)."""
    session = _session(OFFER)
    assert slot_accepted_by_caller(session, "anything else on the saturday?") is None


@pytest.mark.parametrize("date,weekday", [
    ("2026-09-05", "saturday"),
    ("2026-09-07", "monday"),
])
def test_the_weekday_is_computed_from_the_date_not_the_label(date, weekday):
    """The label is generated text and could drift; the date cannot."""
    offered = [{"start": f"{date}T18:00:00"}]
    assert _offered_day_by_weekday(offered, f"the {weekday} works") == date


def test_the_helper_never_raises_on_rubbish():
    """A caller mid-booking must not lose their turn to a resolver."""
    for offered in (None, "nonsense", [None], [{"start": "not-a-date"}], [{}]):
        assert _offered_day_by_weekday(offered, "saturday") is None
    assert _offered_day_by_weekday([{"start": "2026-09-05T18:00:00"}], None) is None


# -- the adjacent flaw this opened a second door onto ----------------------

def test_a_named_band_that_contradicts_the_only_slot_declines():
    """Step 3 returned the day's only spoken slot without checking it against
    the time the caller actually named. "The last day at 6 in the evening"
    against a day holding only 09:10 resolved to 09:10 - a slot the caller
    never chose, pinned into the next readout and read back as their
    appointment. Reachable before this change by position, and reachable by
    weekday afterwards, so it is guarded rather than left."""
    session = _session(OFFER)
    assert slot_accepted_by_caller(session, "the last day at 6 in the evening works") is None
    assert slot_accepted_by_caller(session, "the saturday at 2 in the afternoon works") is None


def test_an_agreeing_band_still_resolves():
    """The guard declines a contradiction, not a confirmation."""
    session = _session(OFFER)
    assert slot_accepted_by_caller(
        session, "the monday morning works"
    ) == "2026-09-07T09:10:00"


def test_a_pick_with_no_band_named_is_unaffected():
    session = _session(OFFER)
    assert slot_accepted_by_caller(session, "the saturday works") == "2026-09-05T18:00:00"
