"""
Regression: Susie refused a weekday that had four free slots.

Live on the JV line, 24 Aug 2026 (`CAfcb3130c`). The caller asked for Tuesday:

    tool:  check_availability {date_hint: "Tuesday"}     ← no after_date, no day_window
    Susie: "Tuesday isn't available at the moment, I'm afraid — but here's what
            we've got coming up — Monday 24th August… Thursday 27th August…"
    truth: Tuesday 1 September had 17:45, 18:30, 19:15 and 20:00 free.

`day_window` defaults to **7 days**, which contains exactly ONE occurrence of
each weekday. That Tuesday was full, so `_filter_tuples_by_preference` found no
Tuesday and — by design, so a caller always hears *something* — DISCARDED the
day filter instead of returning an empty list. Other days were presented in its
place, and the model read the absence of Tuesday as clinic state.

The next Tuesday was one day past the horizon. Nobody had looked.

Recorded as B-86 in `docs/plan/OPEN_DEFECTS_2026-08-22.md`, whose mechanism
section points at `_WEEK_ANCHORS` in `_check_availability_acuity`. That is the
wrong executor: `jv_v1` is `booking_system: google_calendar` and the dispatcher
only routes to the Acuity reader for `theorem*`. This is the Google-Calendar
door. (The B-86 number is also used by
`test_b86_trauma_trigger_shape.py` for an unrelated screening defect, which is
why this file is named for the behaviour instead.)

Two things must hold, and they are not the same thing:

  1. Look before refusing — widen once to `_WIDEN_WINDOW_DAYS` so the next
     occurrence of the named weekday is actually examined.
  2. Never assert what was not examined — when the widened search also finds
     nothing, the payload must forbid "that day is unavailable" and say how far
     the search actually reached.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest

import app.tools.calendar_google as calendar_google
import app.tools.receptionist_tools as rt
from app.tools.receptionist_tools import (
    LONDON_TZ,
    _WIDEN_WINDOW_DAYS,
    _exec_check_availability,
)

_WEEKDAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]


def _async(value):
    async def _c():
        return value
    return _c()


def _session():
    return {"clinic_id": "jv_v1", "selected_location": "bolton"}


def _target_weekday():
    """An OPEN weekday that is not today.

    jv_v1 is closed on Sunday, so Sunday is excluded — a closed day has no
    candidate slots and would prove nothing. Not-today guarantees the first
    occurrence is 1–6 days out (inside the 7-day window) and the second is
    8–13 days out (outside it, inside the 14-day one). That gap IS the defect.
    """
    today = datetime.now(LONDON_TZ).date()
    for offset in range(1, 7):
        d = today + timedelta(days=offset)
        if d.weekday() <= 5:          # Mon–Sat
            return d, _WEEKDAY_NAMES[d.weekday()]
    raise AssertionError("no open weekday in the next six days")


@pytest.fixture
def calendar(monkeypatch):
    """Mark whole dates busy; everything else free. Returns the call counter."""
    calls = {"freebusy": 0}

    def _apply(blocked_dates):
        blocked = set(blocked_dates)

        def fake_freebusy(tokens, w_start, w_end, calendar_id):
            calls["freebusy"] += 1
            out = []
            for d in blocked:
                start = LONDON_TZ.localize(
                    datetime.combine(d, datetime.min.time())
                )
                out.append({
                    "start": start.isoformat(),
                    "end": (start + timedelta(days=1)).isoformat(),
                })
            return out

        monkeypatch.setattr(calendar_google, "freebusy", fake_freebusy)
        monkeypatch.setattr(
            rt, "_get_tokens", lambda *a, **k: _async({"access_token": "t"})
        )
        monkeypatch.setattr(rt, "_save_gcal_tokens", lambda *a, **k: _async(None))
        monkeypatch.setattr(
            rt, "_resolve_calendar_id", lambda *a, **k: "cal@example.com"
        )
        # Deterministic: the all-day scan is a second Google round trip and has
        # its own regression suite.
        monkeypatch.setattr(
            rt, "_all_day_blocks_for_window", lambda *a, **k: _async([])
        )
        return calls

    return _apply


def _run(date_hint, session=None, **extra):
    args = {
        "service": "msk_initial_assessment",
        "location": "bolton",
        "date_hint": date_hint,
    }
    args.update(extra)
    return asyncio.run(_exec_check_availability(args, session or _session()))


def _days_on(out, weekday_index):
    return [
        d for d in (out.get("available_days") or [])
        if datetime.fromisoformat(d["date"]).weekday() == weekday_index
    ]


# ---------------------------------------------------------------------------
# The defect itself
# ---------------------------------------------------------------------------
def test_a_full_first_occurrence_does_not_refuse_the_day(calendar):
    """The failing case. Red before the fix: the 7-day window holds one
    occurrence of the named weekday, it is full, and the caller is told the day
    is unavailable while the next one sits a day past the horizon."""
    first, name = _target_weekday()
    calendar([first])

    out = _run(name)

    matching = _days_on(out, first.weekday())
    assert matching, (
        f"{name} was refused: the only {name} inside the 7-day window is full and "
        f"the next one was never examined. available_days="
        f"{[d['date'] for d in (out.get('available_days') or [])]}"
    )
    assert matching[0]["date"] != first.isoformat()
    assert matching[0].get("slot_times"), "offered day must carry real times"


def test_the_widened_day_is_actually_bookable(calendar):
    """A day Susie speaks but cannot book into is worse than the refusal."""
    first, name = _target_weekday()
    calendar([first])
    session = _session()

    _run(name, session)

    assert session.get("last_offered_slots"), "booking would fail"
    assert session.get("slot_labels")
    assert session.get("available_days")


def test_the_payload_says_how_far_it_looked(calendar):
    first, name = _target_weekday()
    calendar([first])

    out = _run(name)

    assert out.get("day_requested") == name.lower()
    assert out.get("day_requested_found") is True
    assert out.get("window_examined_days") == _WIDEN_WINDOW_DAYS


# ---------------------------------------------------------------------------
# Never assert what was not examined
# ---------------------------------------------------------------------------
def test_a_genuinely_empty_weekday_is_reported_as_not_seen_not_as_closed(calendar):
    """Both occurrences full. The honest answer is "I can't see anything on
    that day in the next couple of weeks" — NOT "that day is unavailable",
    which is a claim about the clinic that nothing in this payload supports."""
    first, name = _target_weekday()
    calendar([first, first + timedelta(days=7)])

    out = _run(name)

    assert out.get("day_requested_found") is False
    guidance = (out.get("guidance") or "").lower()
    assert guidance, "no guidance — the model is free to invent clinic state"
    assert "do not say" in guidance
    assert "fully booked" in guidance
    assert str(out.get("window_examined_days")) in guidance
    # It must still give the caller somewhere to go.
    assert out.get("available_days"), "a miss with no alternative is a dead end"


# ---------------------------------------------------------------------------
# Containment — the widen must not fire on every call
# ---------------------------------------------------------------------------
def test_an_available_weekday_does_not_trigger_a_second_calendar_read(calendar):
    """The common case is one Google round trip. This runs while the caller is
    waiting to hear times; a second read on every call is latency for nothing."""
    _first, name = _target_weekday()
    calls = calendar([])            # nothing blocked — the day is free

    out = _run(name)

    assert calls["freebusy"] == 1, "widened unnecessarily"
    assert out.get("day_requested_found") is True
    assert out.get("window_examined_days") == 7


def test_no_weekday_named_is_left_completely_alone(calendar):
    """A vague hint must not acquire day_* fields, and must not widen."""
    calls = calendar([])

    out = _run("evening")

    assert calls["freebusy"] == 1
    assert "day_requested" not in out
    assert "guidance" not in out


def test_an_explicit_day_window_is_not_overridden(calendar):
    """A model-supplied day_window is a deliberate narrowing, and the
    requested_day_empty path already owns that case. Widening underneath it
    would answer a question the caller did not ask."""
    first, name = _target_weekday()
    calls = calendar([first])

    _run(name, after_date=first.isoformat(), day_window=1)

    # The requested_day_empty path may widen on its own; what must NOT happen is
    # this fix adding a widen of its own on top of an explicit window.
    assert calls["freebusy"] <= 2


# ---------------------------------------------------------------------------
# Robustness — this is a live-call path
# ---------------------------------------------------------------------------
def test_a_failed_widen_keeps_the_narrow_answer(calendar, monkeypatch):
    """Google failing on the second read must not kill the turn: the caller
    still hears the real alternatives, and the payload still refuses to claim
    the day is unavailable."""
    first, name = _target_weekday()
    calendar([first])

    real = calendar_google.freebusy
    state = {"n": 0}

    def flaky(*a, **k):
        state["n"] += 1
        if state["n"] > 1:
            raise RuntimeError("google is down")
        return real(*a, **k)

    monkeypatch.setattr(calendar_google, "freebusy", flaky)

    out = _run(name)

    assert out.get("available_days"), "the turn died"
    assert out.get("day_requested_found") is False
    assert out.get("guidance")
