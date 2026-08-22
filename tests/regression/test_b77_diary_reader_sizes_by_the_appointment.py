"""
Vital Edge's diary reader must size a reschedule by the appointment, not the
service the model named.

availability_mode "diary" (8 Aug) replaced the published-slot reader: instead of
offering events the practitioner published, it subtracts his diary from a working
envelope and builds its own candidate grid. That put a second
generate_candidate_slots call upstream of the B-77 override, which lived inline in
_exec_check_availability below the point where this clinic returns.

Consequence, against Vital Edge's real config (hourly starts, 5-minute break,
09:00-19:00): a 90-minute Deep Tissue Massage being moved, with the grid sized at
60, produces 45 overrunning offers in a single day — every time a diary entry
starts 5 to 25 minutes past the hour. filter_free_slots overlap-tests
[start, start+60+break); the write then puts 90 minutes in. The last half hour is
never checked against the diary, and Vital Edge's diary is the practitioner's real
life — client work, padel, a flight to Ibiza.

Not a double-booking of a confirmed appointment: these are PENDING CONFIRMATION
provisional requests he approves by hand. But offering a time he is not free is
the exact thing the diary reader exists to prevent.
"""

from datetime import datetime, timedelta

import pytest

pytz = pytest.importorskip("pytz")

from app.clinic_config import get_clinic
from app.tools import receptionist_tools as rt
from app.tools.slots import generate_candidate_slots, filter_free_slots

TZ = pytz.timezone("Europe/London")
DAY = datetime(2026, 8, 27)


def _ve():
    clinic = get_clinic("vital_edge") or {}
    if (clinic.get("availability_mode") or "").lower() != "diary":
        pytest.skip("this branch's vital_edge is not on the diary reader")
    return clinic


def _offers(clinic, duration_min, busy):
    """The grid this clinic's diary reader would build at `duration_min`."""
    return filter_free_slots(
        generate_candidate_slots(
            TZ.localize(DAY.replace(hour=0, minute=0)),
            TZ.localize(DAY.replace(hour=23, minute=59)),
            duration_min=duration_min,
            day_start_h=9,
            day_end_h=19,
            tz=TZ,
            clinic_working_hours=clinic.get("working_hours") or {},
            increment_min=clinic.get("slot_increment_minutes"),
            break_min=int(clinic.get("slot_break_minutes") or 0),
        ),
        busy,
        tz=TZ,
        break_min=int(clinic.get("slot_break_minutes") or 0),
    )


def _busy(hour, minute, length_min=60):
    start = TZ.localize(DAY.replace(hour=hour, minute=minute))
    return [(start, start + timedelta(minutes=length_min))]


def test_the_diary_reader_asks_the_override():
    """Wiring. The structural guard lives in the B-77 file; this pins the call
    site itself, because that file's check passes vacuously on a branch that has
    no diary reader at all."""
    import inspect

    src = inspect.getsource(rt._check_availability_diary)
    assert "_reschedule_duration_override(" in src, (
        "the diary grid no longer sizes a reschedule by the appointment"
    )


def test_a_ninety_minute_move_gridded_at_sixty_overruns_the_diary():
    """THE defect, with the arithmetic that makes it real."""
    clinic = _ve()
    busy = _busy(10, 5)                       # a real diary entry, 10:05-11:05
    overruns = [
        (s, e) for s, e in _offers(clinic, 60, busy)
        if s + timedelta(minutes=90) > busy[0][0] and s < busy[0][1]
    ]
    assert overruns, (
        "expected the 60-minute grid to offer a start whose 90-minute write "
        "collides with a 10:05 diary entry — if this no longer holds the "
        "reproduction has moved, not disappeared"
    )


def test_sizing_by_the_appointment_removes_them():
    """The fix: grid at the appointment's true 90 and the overrunning starts go."""
    clinic = _ve()
    busy = _busy(10, 5)
    starts_90 = {s for s, _ in _offers(clinic, 90, busy)}
    for s, _ in _offers(clinic, 60, busy):
        if s + timedelta(minutes=90) > busy[0][0] and s < busy[0][1]:
            assert s not in starts_90, (
                f"{s:%H:%M} still offered when the grid is sized at 90 minutes"
            )


def test_the_exposure_is_the_off_the_hour_entry():
    """
    Why a first sweep can read as clean. With hourly starts and hourly-aligned
    diary entries there is no exposure at all; it appears only when an entry
    starts 5-25 minutes past the hour. A regression here that only tested
    on-the-hour diary entries would have called this fixed while it was live.
    """
    clinic = _ve()
    exposed = []
    for minute in (0, 5, 15, 25, 30, 45):
        busy = _busy(10, minute)
        if any(
            s + timedelta(minutes=90) > busy[0][0] and s < busy[0][1]
            for s, _ in _offers(clinic, 60, busy)
        ):
            exposed.append(minute)
    assert 0 not in exposed, "an on-the-hour entry should never have been exposed"
    assert {5, 15, 25} <= set(exposed), (
        f"expected off-the-hour entries to be the exposed case, got {exposed}"
    )
