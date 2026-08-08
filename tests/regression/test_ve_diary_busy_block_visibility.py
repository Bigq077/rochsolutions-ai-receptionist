"""
Vital Edge diary availability — the 9am question, 8 Aug 2026.

Susie's first offer on Friday 14 Aug was 10:00, on a morning the practitioner
believed was empty. The availability log said only "170 candidate(s) − 9 busy
block(s) = 98 free", which cannot distinguish an over-blocking bug from a diary
entry he cannot see (Google draws a multi-day TIMED event as a banner above the
hour grid, so a holiday whose return leg lands Friday morning leaves the grid
looking free while freebusy correctly reports it busy).

Two things are pinned here.

1. The ENVELOPE. 09:00 is a real candidate on every working day, and it is only
   removed by an interval reaching past 08:55. If a future change silently costs
   the clinic its first hour of the day, this fails.
2. The DIAGNOSTIC. Each subtracted interval is logged by name, so the next read
   answers "what blocked Friday morning?" outright instead of by inference.
"""

import logging
from datetime import datetime, timedelta

import pytest
import pytz

from app.tools import receptionist_tools as rt
from app.tools.slots import filter_free_slots, generate_candidate_slots

TZ = pytz.timezone("Europe/London")

# Vital Edge's envelope: 09:00 open, 18:00 last START, hourly, 5-minute gap.
# working_hours is stored end-shifted by slot_minutes, so 18:00-last-start is 19.0.
_WORKING_HOURS = {d: (9.0, 19.0) for d in
                  ("mon", "tue", "wed", "thu", "fri", "sat", "sun")}
_FRI = datetime(2026, 8, 14)


def _fri_candidates(duration=60):
    return generate_candidate_slots(
        TZ.localize(_FRI), TZ.localize(_FRI + timedelta(days=1)),
        duration_min=duration, clinic_working_hours=_WORKING_HOURS,
        increment_min=60, break_min=5,
    )


def _first_free(blocks, duration=60):
    free = filter_free_slots(_fri_candidates(duration), blocks, break_min=5)
    free = [s for s, _ in free if s.date() == _FRI.date()]
    return free[0].strftime("%H:%M") if free else None


def _trip_ending(hour, minute):
    """The Ibiza-shaped block: one timed event running Sunday → Friday morning."""
    return [(TZ.localize(datetime(2026, 8, 9, 6, 0)),
             TZ.localize(datetime(2026, 8, 14, hour, minute)))]


# ── 1. the envelope ────────────────────────────────────────────────────────

def test_nine_am_is_offered_on_a_clear_friday():
    assert _first_free([]) == "09:00"


def test_a_ninety_minute_service_still_starts_at_nine():
    assert _first_free([], duration=90) == "09:00"


def test_a_block_clearing_the_gap_leaves_nine_am_bookable():
    # Ends 08:55 — exactly the 5-minute gap before a 09:00 start.
    assert _first_free(_trip_ending(8, 55)) == "09:00"


@pytest.mark.parametrize("hour,minute", [(9, 0), (9, 30), (9, 55)])
def test_only_a_block_reaching_into_the_hour_costs_the_nine_am(hour, minute):
    # The observed live behaviour. 10:00 is CORRECT here — the practitioner is
    # not free at nine — which is why the log has to name what the block was.
    assert _first_free(_trip_ending(hour, minute)) == "10:00"


# ── 2. the diagnostic ──────────────────────────────────────────────────────

def _events(*specs):
    return [
        {"summary": s,
         "start": {"dateTime": TZ.localize(st).isoformat()},
         "end":   {"dateTime": TZ.localize(en).isoformat()}}
        for s, st, en in specs
    ]


def test_each_busy_block_is_logged_with_the_event_that_caused_it(caplog):
    events = _events(
        ("Ibiza — return flight", datetime(2026, 8, 9, 6, 0), datetime(2026, 8, 14, 9, 30)),
        ("Massage with Roger",    datetime(2026, 8, 15, 11, 0), datetime(2026, 8, 15, 12, 0)),
    )
    blocks = [
        (TZ.localize(datetime(2026, 8, 9, 6, 0)), TZ.localize(datetime(2026, 8, 14, 9, 30))),
        (TZ.localize(datetime(2026, 8, 15, 11, 0)), TZ.localize(datetime(2026, 8, 15, 12, 0))),
    ]
    with caplog.at_level(logging.INFO, logger=rt.logger.name):
        rt._log_busy_blocks(blocks, events, 5)

    text = caplog.text
    assert "Ibiza — return flight" in text
    assert "Massage with Roger" in text
    # The padded edge is what actually decides the 09:00 slot, so it is shown.
    assert "09:35" in text, "padded block end not logged — the 9am answer is still a guess"


def test_a_merged_block_names_every_event_inside_it(caplog):
    # freebusy merges overlapping events into one interval; naming only the
    # first would point at the wrong entry.
    events = _events(
        ("Padel",  datetime(2026, 8, 18, 9, 0),  datetime(2026, 8, 18, 10, 0)),
        ("Mark A", datetime(2026, 8, 18, 10, 0), datetime(2026, 8, 18, 11, 0)),
    )
    blocks = [(TZ.localize(datetime(2026, 8, 18, 9, 0)),
               TZ.localize(datetime(2026, 8, 18, 11, 0)))]
    with caplog.at_level(logging.INFO, logger=rt.logger.name):
        rt._log_busy_blocks(blocks, events, 5)
    assert "Padel" in caplog.text and "Mark A" in caplog.text


def test_an_unmatched_block_says_so_rather_than_naming_the_wrong_event(caplog):
    blocks = [(TZ.localize(datetime(2026, 8, 20, 9, 0)),
               TZ.localize(datetime(2026, 8, 20, 10, 0)))]
    with caplog.at_level(logging.INFO, logger=rt.logger.name):
        rt._log_busy_blocks(blocks, [], 5)
    assert "no timed event matched" in caplog.text


def test_logging_never_breaks_an_availability_read():
    # An availability read that dies in its logging offers the caller nothing.
    rt._log_busy_blocks([("not", "datetimes")], [{"start": None}], 5)
    rt._log_busy_blocks(None, None, 0)
