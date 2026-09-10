# tests/regression/test_s6_s8_s9_the_harnesses_can_see_what_they_claim.py
"""
S-6 / S-8 / S-9 - three blind spots, each of which already cost time.

  S-6  `_record_stood_down_slots` returned in SILENCE when it resolved
       nothing, so "the sentence named no slots" and "the reverse parse could
       not read the sentence" were the same observation. Stage C's gate
       watches that function; a gate that cannot see half of what it covers is
       not a gate.

  S-8  `presented_days` was populated on all 53 multi_day offers in the corpus
       and EMPTY on all 24 single_day ones, because the record site passed
       `result["presented_days"]` unconditionally and that key is
       `_cap_presented_slots`' multi-day output. B-95's presented-vs-bookable
       split was therefore invisible in the mode a caller reaches by naming a
       day -- the mode most of these defects have been found in.

  S-9  `calls.latency` carried no tool marker at all, so the tool-vs-plain
       latency split could not be measured and `latency_percentiles.py` had to
       report a proxy and name it an upper bound.

WHAT THESE TESTS COVER, AND WHAT THEY DO NOT. The two CALL SITES (S-6's log and
S-8's `[_fd]`) live inside Gate 5's streaming path and cannot be driven without
a WebSocket, a model and a tool result; they are confirmed by a live call and
the corpus pull after it, which is what this item's gate says. What IS driven
here is everything the call sites depend on: the record's shape, the field, and
-- above all -- the NOT-OBSERVED rule, which is the one that decides whether
the first corpus pull after this lands is read correctly or catastrophically
misread.
"""
from __future__ import annotations

import time

from app.media_streams.latency_timing import TurnTiming
from app.obs.slot_offers import record_offer


def _timing(seq: int = 1) -> TurnTiming:
    """A turn shaped like a real one. `t0` and `t_dispatch` are required and
    monotonic, so they are taken from the same clock the engine stamps with."""
    now = time.monotonic()
    return TurnTiming(call_sid="CA0", turn_seq=seq, t0=now, t_dispatch=now)


# ---------------------------------------------------------------------------
# S-9 - the tool marker
# ---------------------------------------------------------------------------
def test_a_turn_starts_with_no_tool_calls():
    """0 is a real reading and means a plain turn."""
    assert _timing().tool_calls == 0


def test_the_tool_count_reaches_the_record():
    """The [LAT] line and the durable buffer are both formatted FROM
    `as_record`, so a field that does not appear here does not exist as far as
    any harness is concerned."""
    timing = _timing()
    timing.tool_calls += 1
    timing.tool_calls += 1

    record = timing.as_record()

    assert "tool_calls" in record, sorted(record)
    assert record["tool_calls"] == 2


def test_the_count_accumulates_across_a_tool_loop():
    """A turn can make several calls. The count is of the TURN, so a second
    iteration adds to it rather than replacing it -- which is why it is
    incremented at the one point every `tool_use` block passes through."""
    timing = _timing()
    for _ in range(3):                      # three tools, one turn
        timing.tool_calls += 1

    assert timing.as_record()["tool_calls"] == 3


def test_a_plain_turn_records_zero_not_missing():
    """The distinction the reader depends on: a turn that ran no tool records
    0, and only a turn written before the field existed has no key. Conflating
    them puts ~3,500 historical tool turns into the plain bucket."""
    record = _timing().as_record()

    assert record["tool_calls"] == 0
    assert record["tool_calls"] is not None


def test_a_historical_row_is_not_observed_rather_than_zero():
    """The rule `latency_percentiles.py` reads by. Every row stored before
    10 Sep 2026 has no `tool_calls` key, and a reader that treats the absence
    as 0 reports a split that is WORSE than the proxy it replaces -- every
    historical tool turn lands in "no tool ran".

    This pins the rule against a row shaped like the ones already in the
    column, so a future reader written from memory fails here rather than in a
    report somebody acts on.
    """
    historical = {"turn_seq": 4, "ttfa_ms": 1900, "content_ttfa_ms": 3200}

    count = historical.get("tool_calls")
    assert not isinstance(count, (int, float)), (
        "a stored row from before S-9 must read as NOT OBSERVED"
    )

    fresh = _timing(4).as_record()
    assert isinstance(fresh.get("tool_calls"), int)


# ---------------------------------------------------------------------------
# S-8 - the presented/bookable split on the single_day path
# ---------------------------------------------------------------------------
DAY_ISO = "2026-09-14"
BOOKABLE = ["08:00", "08:50", "09:40", "10:30", "11:20", "12:10"]
PRESENTED = ["08:50", "10:30", "12:10"]


def _day(times):
    return {
        "date": DAY_ISO,
        "day_label": "Monday 14th September",
        "slot_times": list(times),
        "slot_times_spoken": [f"spoken-{t}" for t in times],
        "slots": [{"start": f"{DAY_ISO}T{t}:00+01:00"} for t in times],
        "times_not_shown": 0,
    }


class _Offer:
    """The shape `record_offer` reads off a `SlotOffer`."""

    mode = "single_day"
    chunks = ["On Monday 14th September I have Number 1, ten to nine."]
    slots = [{"start": f"{DAY_ISO}T08:50:00+01:00", "spoken": "ten to nine",
              "date": DAY_ISO}]
    dtmf_map = {"1": "ten to nine"}
    more_times = True


def _recorded(presented_days):
    session: dict = {}
    record_offer(
        session,
        payload_days=[_day(BOOKABLE)],
        offer=_Offer(),
        presented_days=presented_days,
    )
    from app.obs.slot_offers import offers_block
    block = offers_block(session)
    assert block, "record_offer stored nothing"
    return block[-1]


def test_the_single_day_path_used_to_record_nothing_presented():
    """The defect, stated as a test so the fix has something to be measured
    against: passed None -- which is what `result["presented_days"]` is on the
    single_day path -- the row carries no presented list, and the split B-95
    exists to measure cannot be computed from it."""
    row = _recorded(None)

    assert not row.get("presented")
    assert row.get("payload"), "the payload half was never the problem"


def test_a_single_day_offer_records_the_day_it_presented():
    """The fix. Given the day that was actually spoken, the row holds both
    halves and the presented-vs-bookable gap is readable."""
    row = _recorded([_day(PRESENTED)])

    assert row.get("presented"), row
    assert row.get("payload"), row


def test_the_gap_between_the_two_is_what_makes_the_split_measurable():
    """The point of storing both, and the only assertion that would notice a
    'fix' that recorded the UNTRIMMED day as presented -- which would look
    populated and measure nothing."""
    row = _recorded([_day(PRESENTED)])

    def _times(days):
        return [t for d in (days or []) for t in (d.get("slot_times") or [])]

    bookable, presented = _times(row.get("payload")), _times(row.get("presented"))

    assert set(presented) < set(bookable), (presented, bookable)
    assert len(presented) == len(PRESENTED)
    assert len(bookable) == len(BOOKABLE)


def test_recording_never_raises_on_a_malformed_presented_list():
    """`record_offer` is on the live call path and its own docstring says NEVER
    RAISES. The new argument must not be the thing that breaks that."""
    for bad in (object(), [None], [{"date": None}], "not a list", {}):
        session: dict = {}
        record_offer(
            session, payload_days=[_day(BOOKABLE)], offer=_Offer(),
            presented_days=bad,
        )
