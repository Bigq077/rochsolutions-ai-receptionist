"""Vital Edge: availability = working envelope MINUS the diary.

CAdafca484696fce9a538695f2a95ee04e (8 Aug 2026). `vitaledgetherapy@gmail.com`
records the work Jonathan has BOOKED, not the slots he has published, and
`_check_availability_published` treats every timed event as offerable. On that
call Susie offered "four in the morning, three in the afternoon, eleven in the
evening" on Sunday 9 August — 04:00 was his "Ibiza trip for 6 days" and 15:00
was "Flying to Ibiza". She offered a caller his flight as a massage appointment.

`_check_availability_diary` inverts it: propose inside an envelope, subtract
everything in the diary. These tests pin the properties that make that safe.

No test here touches Google. The calendar is a fixture, so a passing run means
the LOGIC is right, not that the credentials happen to work.
"""

import asyncio
from datetime import datetime, timedelta

import pytest

from app.tools import receptionist_tools as rt
from app.tools.receptionist_tools import LONDON_TZ


def _clinic(**over):
    """VE's real shape: 09:00–18:00 last start, every day, 5-minute gap."""
    hours = {d: (9.0, 19.0) for d in ("mon", "tue", "wed", "thu", "fri", "sat", "sun")}
    clinic = {
        "clinic_id": "vital_edge",
        "practitioner": "Jonathan",
        "booking_system": "google_calendar_provisional",
        "availability_mode": "diary",
        "calendar_id": "vitaledgetherapy@gmail.com",
        "working_hours": hours,
        "slot_minutes": 60,
        "slot_break_minutes": 5,
        "slot_increment_minutes": 60,
        "days_ahead": 17,
        "allow_same_day": False,
    }
    clinic.update(over)
    return clinic


def _day(offset_days: int):
    """A date safely inside the window (offset from today)."""
    return (datetime.now(LONDON_TZ) + timedelta(days=offset_days)).date()


def _timed(day, start_h, end_h, summary="Massage with Roger"):
    s = LONDON_TZ.localize(datetime.combine(day, datetime.min.time())) + timedelta(hours=start_h)
    e = LONDON_TZ.localize(datetime.combine(day, datetime.min.time())) + timedelta(hours=end_h)
    return {"start": s, "end": e, "summary": summary}


def _install(monkeypatch, busy_events=(), all_day=(), fail=False, tokens=True, n_events=None):
    """Wire a fake calendar. busy_events → freebusy; all_day → the events list."""
    async def _tok():
        return {"access_token": "x"} if tokens else None

    def _freebusy(*a, **k):
        if fail:
            raise RuntimeError("calendar unavailable")
        return [
            {"start": e["start"].isoformat(), "end": e["end"].isoformat()}
            for e in busy_events
        ]

    def _events(*a, **k):
        items = [
            {"start": {"date": s}, "end": {"date": e}, "summary": t}
            for s, e, t in all_day
        ]
        if n_events:  # pad to simulate a truncated read
            items += [{"start": {"dateTime": "2026-01-01T09:00:00+00:00"}}] * n_events
        return items

    async def _save(*a, **k):
        return None

    monkeypatch.setattr(rt, "_get_tokens", _tok)
    monkeypatch.setattr(rt, "_save_gcal_tokens", _save)
    monkeypatch.setattr("app.tools.calendar_google.freebusy", _freebusy)
    monkeypatch.setattr("app.tools.calendar_google.list_upcoming_events", _events)


def _run(clinic=None, args=None, session=None):
    return asyncio.run(
        rt._check_availability_diary(
            args or {"date_hint": "any"}, session if session is not None else {}, clinic or _clinic()
        )
    )


def _offered(out):
    """Every (iso_date, HH:MM) offered."""
    return {
        (d.get("date"), t)
        for d in (out.get("available_days") or [])
        for t in (d.get("slot_times") or [])
    }


# ── the defect itself ───────────────────────────────────────────────────────

def test_a_booked_appointment_is_never_offered(monkeypatch):
    """The whole point. An entry in the diary is time he is BUSY."""
    day = _day(3)
    _install(monkeypatch, busy_events=[_timed(day, 11, 12, "Massage with Roger")])
    offered = _offered(_run())
    assert (day.isoformat(), "11:00") not in offered
    # …and the rest of that day is still available, so we blocked the entry and
    # not the day.
    assert (day.isoformat(), "14:00") in offered


def test_the_ibiza_trip_blocks_the_whole_holiday(monkeypatch):
    """A multi-day TIMED event — how the 8 Aug holiday was actually entered.

    "Ibiza trip for 6 days, 4am" spans Sun 9 → Fri 14. Every day inside it must
    disappear, not just the 04:00 the published reader offered.
    """
    start = LONDON_TZ.localize(datetime.combine(_day(2), datetime.min.time())) + timedelta(hours=4)
    end = start + timedelta(days=5)
    _install(monkeypatch, busy_events=[{"start": start, "end": end, "summary": "Ibiza trip for 6 days"}])
    offered_days = {d for d, _ in _offered(_run())}
    for n in range(2, 7):
        assert _day(n).isoformat() not in offered_days, f"offered a day inside the holiday: {_day(n)}"
    # Back at work the day the trip ends.
    assert _day(8).isoformat() in offered_days


def test_all_day_event_blocks_even_though_freebusy_calls_it_free(monkeypatch):
    """Google creates all-day events as FREE by default, so a holiday entered
    that way is invisible to freebusy. A day-long entry is never a time to put
    a client, so it blocks regardless of transparency or title."""
    day = _day(4)
    _install(
        monkeypatch,
        busy_events=[],  # freebusy reports nothing at all
        all_day=[(day.isoformat(), (day + timedelta(days=1)).isoformat(), "Annual leave")],
    )
    offered_days = {d for d, _ in _offered(_run())}
    assert day.isoformat() not in offered_days
    assert _day(5).isoformat() in offered_days  # end.date is exclusive


# ── fails closed ────────────────────────────────────────────────────────────
# The non-provisional path falls back to UNFILTERED candidates when the calendar
# errors. Here that would re-create the defect — a bare 9–19 grid, every entry
# of which he may already be working. Each of these must hand off instead.

@pytest.mark.parametrize(
    "kw, why",
    [
        ({"fail": True}, "freebusy raised"),
        ({"tokens": False}, "no google tokens"),
        ({"n_events": rt._MAX_DIARY_EVENTS}, "event read truncated"),
    ],
)
def test_unreadable_diary_hands_off_and_never_offers_a_grid(monkeypatch, kw, why):
    _install(monkeypatch, **kw)
    out = _run()
    assert out["error"] == "availability_handoff", why
    assert out["available_days"] == [], f"offered slots despite: {why}"
    assert out["slots"] == []


# ── window ──────────────────────────────────────────────────────────────────

def test_nothing_today(monkeypatch):
    """Owner requirement: bookable from tomorrow. He confirms by hand and
    cannot be held to same-day."""
    _install(monkeypatch)
    today = datetime.now(LONDON_TZ).date().isoformat()
    assert today not in {d for d, _ in _offered(_run())}


def test_nothing_beyond_the_horizon(monkeypatch):
    """Two to two-and-a-half weeks — 17 days."""
    _install(monkeypatch)
    horizon = (datetime.now(LONDON_TZ) + timedelta(days=17)).date()
    assert all(d <= horizon.isoformat() for d, _ in _offered(_run()))


def test_envelope_bounds_are_respected(monkeypatch):
    """09:00 first start, 18:00 last — a 60-minute session finishes at 19:00."""
    _install(monkeypatch)
    times = sorted({t for _, t in _offered(_run())})
    assert times, "no slots generated at all"
    assert min(times) >= "09:00"
    assert max(times) <= "18:00"


def test_five_minute_gap_is_enforced_against_real_appointments(monkeypatch):
    """A slot flush against an existing client is not offerable — that gap is
    the one thing the break setting is actually for."""
    day = _day(3)
    # 12:00–13:00 booked. A 60-min slot starting 13:00 leaves him no gap.
    _install(monkeypatch, busy_events=[_timed(day, 12, 13, "Massage with Ellie")])
    offered = _offered(_run())
    assert (day.isoformat(), "13:00") not in offered
    assert (day.isoformat(), "14:00") in offered


# ── contract with the rest of the engine ────────────────────────────────────

def test_session_available_days_is_populated(monkeypatch):
    """bac8bd4's day-acceptance suppression reads session['available_days'] to
    tell "Saturday works" (accepting) from "Saturday?" (asking). If this reader
    stopped populating it, that fix would silently regress to re-checking."""
    _install(monkeypatch)
    session: dict = {}
    _run(session=session)
    assert session.get("available_days"), "available_days not set — bac8bd4 regresses"
    assert session.get("last_offered_slots")


def test_handoff_mode_still_wins_over_diary(monkeypatch):
    """The fallback must be reachable by config alone."""
    from app.clinic_config import get_clinic

    assert (get_clinic("vital_edge") or {}).get("availability_mode") == "handoff"
