"""
Requested-day-full widening (gcal availability path).

When the caller names a day, the model passes day_window=1 (SPECIFIC DAY in the
template prompt). If that one day is full, the tool must widen the search and
return the miss TOGETHER with real alternatives — so Susie can say "Tuesday's
full, I've got Wednesday at seven" in a single turn instead of inventing an
alternative day she has no availability for.
"""
import asyncio
from datetime import datetime, timedelta

import pytest

import app.tools.calendar_google as calendar_google
import app.tools.receptionist_tools as rt
from app.tools.receptionist_tools import (
    LONDON_TZ,
    _NARROW_WINDOW_MAX_DAYS,
    _exec_check_availability,
    _spoken_day_label,
)


def _next_weekday(weekday: int, min_days_ahead: int = 3):
    """Next date with the given weekday (0=Mon) at least min_days_ahead away."""
    d = datetime.now(LONDON_TZ).date() + timedelta(days=min_days_ahead)
    while d.weekday() != weekday:
        d += timedelta(days=1)
    return d


def _session():
    return {"clinic_id": "jv_v1", "selected_location": "bolton"}


def _args(target_iso, day_window=1):
    return {
        "service": "msk_initial_assessment",
        "location": "bolton",
        "after_date": target_iso,
        "day_window": day_window,
    }


@pytest.fixture
def block_day(monkeypatch):
    """Mark one whole date busy; everything else free."""
    def _apply(blocked_date):
        def fake_freebusy(tokens, w_start, w_end, calendar_id):
            start = LONDON_TZ.localize(datetime.combine(blocked_date, datetime.min.time()))
            return [{
                "start": start.isoformat(),
                "end": (start + timedelta(days=1)).isoformat(),
            }]
        monkeypatch.setattr(calendar_google, "freebusy", fake_freebusy)
        monkeypatch.setattr(rt, "_get_tokens", lambda: _async({"access_token": "t"}))
        monkeypatch.setattr(rt, "_save_gcal_tokens", lambda *a, **k: _async(None))
        monkeypatch.setattr(rt, "_resolve_calendar_id", lambda *a, **k: "cal@example.com")
    return _apply


def _async(value):
    async def _c():
        return value
    return _c()


def _run(args, session):
    return asyncio.run(_exec_check_availability(args, session))


def test_full_day_widens_and_returns_real_alternatives(block_day):
    """The named day is full → miss flagged AND genuine alternatives attached."""
    tuesday = _next_weekday(1)
    block_day(tuesday)

    out = _run(_args(tuesday.isoformat()), _session())

    assert out.get("requested_day_empty") is True
    assert out.get("requested_date") == tuesday.isoformat()
    assert out.get("note") == "requested_day_full_widened"

    days = out.get("available_days") or []
    assert days, "widened search must return alternatives, not an empty result"
    # The day we just proved is full must never come back as an option.
    assert all(d["date"] != tuesday.isoformat() for d in days)
    # Alternatives must be real slots the caller can actually be booked into.
    assert all(d.get("slot_times") for d in days)


def test_widened_result_is_bookable(block_day):
    """Session slot state must be populated, or book_appointment can't resolve
    the slot the caller picks."""
    tuesday = _next_weekday(1)
    block_day(tuesday)
    session = _session()

    _run(_args(tuesday.isoformat()), session)

    assert session.get("last_offered_slots"), "last_offered_slots not set — booking would fail"
    assert session.get("slot_labels")
    assert session.get("available_days")


def test_spoken_label_is_prebuilt_for_the_missed_day(block_day):
    """The model must never render the date itself (same reason as
    slot_times_spoken)."""
    tuesday = _next_weekday(1)
    block_day(tuesday)

    out = _run(_args(tuesday.isoformat()), _session())

    assert out.get("requested_day_label") == _spoken_day_label(tuesday.isoformat())
    assert out["requested_day_label"].startswith("Tuesday ")


def test_default_window_does_not_widen(block_day):
    """An empty DEFAULT search genuinely means empty — never dress it up as a
    'requested day full' miss."""
    tuesday = _next_weekday(1)
    block_day(tuesday)

    # No day_window at all → default 7-day search, widening must not engage.
    args = _args(tuesday.isoformat())
    args.pop("day_window")
    out = _run(args, _session())

    assert out.get("requested_day_empty") is None


def test_wide_explicit_window_does_not_widen(block_day):
    """Only deliberately NARROW windows widen."""
    tuesday = _next_weekday(1)
    block_day(tuesday)

    out = _run(_args(tuesday.isoformat(), day_window=_NARROW_WINDOW_MAX_DAYS + 1), _session())

    assert out.get("requested_day_empty") is None


def test_relative_narrow_window_has_no_spoken_label(block_day):
    """'the next 2 days' names no day, so there is no day label to speak —
    the prompt falls back to a generic opener rather than inventing one."""
    tuesday = _next_weekday(1)
    block_day(tuesday)

    args = _args(tuesday.isoformat(), day_window=2)
    args.pop("after_date")          # relative window: no named date
    out = _run(args, _session())

    if out.get("requested_day_empty"):
        assert out.get("requested_day_label") == ""


def test_available_day_is_unaffected(block_day):
    """Sanity: a day with availability still returns the normal shape."""
    tuesday = _next_weekday(1)
    wednesday = tuesday + timedelta(days=1)
    block_day(tuesday)                      # Wednesday stays free

    out = _run(_args(wednesday.isoformat()), _session())

    assert out.get("requested_day_empty") is None
    assert out.get("available_days")
