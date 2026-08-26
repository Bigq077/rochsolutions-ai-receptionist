"""
Regression: an all-day entry in the practitioner's calendar must not be offered
as available.

`freebusy` honours each event's Busy/Free setting, and **Google creates all-day
events as FREE by default**. So a week blocked out as an all-day "holiday"
comes back from freebusy as free time, and Susie offers it. The practitioner
cannot see the fault from their side — their calendar shows the week blocked.

Vital Edge already had this fix (`ddd5318`, `_all_day_busy_blocks`) but it was
wired only into `_check_availability_diary`, VE's own availability reader. Every
clinic on the ordinary Google Calendar path — jv_v1 included — had the same
hole. This ports it to that path.

Timed events are deliberately NOT handled here: freebusy reports those
correctly, including multi-day ones (which Google draws as a banner above the
hour grid, so the grid looks free while freebusy correctly says busy).
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.tools.receptionist_tools import (
    _MAX_AVAILABILITY_EVENTS,
    _all_day_busy_blocks,
)
from app.tools.receptionist_tools import LONDON_TZ


def _win(start_day: str = "2026-08-10", days: int = 7):
    w_start = LONDON_TZ.localize(datetime.fromisoformat(f"{start_day}T00:00:00"))
    return w_start, w_start + timedelta(days=days)


def _all_day(start: str, end: str | None = None, summary: str = "Holiday"):
    ev = {"start": {"date": start}, "summary": summary}
    if end:
        ev["end"] = {"date": end}
    return ev


def _timed(start_iso: str, end_iso: str, summary: str = "Patient"):
    return {
        "start": {"dateTime": start_iso},
        "end": {"dateTime": end_iso},
        "summary": summary,
    }


# ---------------------------------------------------------------------------
# The defect itself
# ---------------------------------------------------------------------------
def test_an_all_day_holiday_is_blocked():
    """The failing case. Google reports this FREE; it must not be offered."""
    w_start, w_end = _win()
    blocks = _all_day_busy_blocks(
        [_all_day("2026-08-12", "2026-08-15", "Annual leave")], w_start, w_end
    )
    assert len(blocks) == 1
    s, e = blocks[0]
    assert s.date().isoformat() == "2026-08-12"
    # end.date is EXCLUSIVE in Google's all-day representation
    assert e.date().isoformat() == "2026-08-15"


def test_a_single_day_entry_with_no_end_blocks_that_whole_day():
    """`end.date` absent → one day. Missing this would silently block nothing."""
    w_start, w_end = _win()
    blocks = _all_day_busy_blocks([_all_day("2026-08-12")], w_start, w_end)
    assert len(blocks) == 1
    s, e = blocks[0]
    assert (e - s) == timedelta(days=1)
    assert s.date().isoformat() == "2026-08-12"


# ---------------------------------------------------------------------------
# Containment — this must not start blocking things freebusy already owns
# ---------------------------------------------------------------------------
def test_a_timed_event_is_ignored():
    """freebusy already reports timed events correctly. Double-counting them
    here would block real bookable time — a worse failure than the one being
    fixed, because it is invisible and permanent."""
    w_start, w_end = _win()
    blocks = _all_day_busy_blocks(
        [_timed("2026-08-12T09:00:00+01:00", "2026-08-12T10:00:00+01:00")],
        w_start, w_end,
    )
    assert blocks == []


@pytest.mark.parametrize(
    "start,end,why",
    [
        ("2026-08-01", "2026-08-10", "ends exactly at the window start"),
        ("2026-08-17", "2026-08-20", "starts exactly at the window end"),
        ("2026-07-01", "2026-07-05", "entirely before the window"),
    ],
)
def test_entries_outside_the_window_are_dropped(start, end, why):
    w_start, w_end = _win()  # 2026-08-10 → 2026-08-17
    assert _all_day_busy_blocks([_all_day(start, end)], w_start, w_end) == [], why


def test_an_entry_straddling_the_window_edge_is_kept():
    """Partial overlap must still block — a holiday that began yesterday is
    still a holiday today."""
    w_start, w_end = _win()
    blocks = _all_day_busy_blocks(
        [_all_day("2026-08-08", "2026-08-12")], w_start, w_end
    )
    assert len(blocks) == 1


# ---------------------------------------------------------------------------
# Robustness — this runs on a live call and must never raise
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "events",
    [
        None,
        [],
        [{}],                                    # no start at all
        [{"start": {}}],                         # start with neither key
        [{"start": {"date": "not-a-date"}}],     # unparseable
        [{"start": {"date": "2026-08-12"}, "end": {"date": "rubbish"}}],
    ],
)
def test_malformed_input_never_raises(events):
    w_start, w_end = _win()
    assert _all_day_busy_blocks(events, w_start, w_end) == []


def test_the_event_cap_is_bounded():
    """A runaway calendar must not stall an availability read mid-call."""
    assert 0 < _MAX_AVAILABILITY_EVENTS <= 2500


# ---------------------------------------------------------------------------
# Wiring — the function existing is not the fix; being CALLED is the fix
# ---------------------------------------------------------------------------
def test_every_google_calendar_read_adds_the_all_day_blocks():
    """EVERY freebusy site on this path, not a fixed number of them.

    A widen window is LONGER than the primary one, so it is more likely to
    contain a holiday — wiring only the primary path would leave the more
    exposed site unfixed.

    Asserted as a PAIRING rather than a count. This was `== 2` and went red the
    first time a third read was added (the named-weekday widen) — which had
    wired the scan correctly. A test that fails when the property it names is
    satisfied trains people to edit the number, and the next unwired site then
    passes.

    COMMENTS ARE NOT CODE. `src.count("freebusy,")` is a proxy for "call site",
    and on 26 Aug 2026 it counted a COMMENT — vitaledge-onboarding carries

        # persist any token refresh that happened inside freebusy, under THIS

    which canonical and jv_v2 do not. Three real reads paired with three scans
    read as 4-against-3, and the test went red on a branch whose wiring was
    correct: the exact failure this docstring warns about, one level down.
    Comment lines are dropped before counting so the proxy sees only code.
    """
    import inspect

    from app.tools import receptionist_tools as rt

    src = inspect.getsource(rt._exec_check_availability)
    _code_lines = [
        _l for _l in src.splitlines() if not _l.lstrip().startswith("#")
    ]
    scans = sum(_l.count("_all_day_blocks_for_window(") for _l in _code_lines)
    reads = sum(_l.count("freebusy,") for _l in _code_lines)
    assert reads >= 2, "expected at least the primary and widen freebusy reads"
    assert scans == reads, (
        f"{reads} freebusy read(s) on the Google Calendar path but {scans} "
        "all-day scan(s) — every read must pair with one or a blocked-out week "
        "is offered as available"
    )


def test_the_all_day_scan_runs_concurrently_with_freebusy():
    """This is a live-call path. A serial second Google round trip is dead air
    before the slots are read out, so both sites must gather, not await twice."""
    import inspect

    from app.tools import receptionist_tools as rt

    src = inspect.getsource(rt._exec_check_availability)
    assert src.count("asyncio.gather(") >= 2, (
        "the all-day scan must be gathered with freebusy, not awaited serially"
    )


async def test_the_scan_swallows_failures_and_returns_no_blocks():
    """A refinement of the busy set must never be able to kill the read."""
    from app.tools import receptionist_tools as rt

    w_start, w_end = _win()
    # No tokens, no calendar — the underlying call will raise inside the helper.
    out = await rt._all_day_blocks_for_window(None, "nope@example.com", w_start, w_end)
    assert out == []
