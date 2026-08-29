"""
Regression: the published reader spoke two days and recorded three.

Phase 4, anchored 2026-08-29. `_check_availability_published` was the one
producer of seven that called neither `_cap_presented_slots` nor
`_sync_last_offered_to_spoken`. Two consequences, and the second is the one
that matters:

  * it returned every published day at once, with no `presentation_mode`, no
    `more_times`, and none of the caps the other six apply - a wall of times.
  * `session["last_offered_slots"]` was left holding `_select_presented_tuples`
    (up to three days) while speech named at most two. That record is indexed
    BY POSITION by `_try_slot_selection` and `_resolve_slot_iso`, so "the
    third one" resolved to a date the caller was never read out. This is
    B-108b - the same defect the Acuity reader had - through the seventh door.

DORMANT, NOT LATENT, and the difference is worth stating. `vital_edge` moved to
`availability_mode: "diary"` on 8 Aug (its own clinic.json says the published
mode "is wrong for this clinic and must not be restored"), so no clinic reads
this path today. It is still the DEFAULT for a provisional clinic - the
dispatch in `_exec_check_availability` falls through to it whenever
`availability_mode` is unset - so the next provisional clinic onboarded lands
here, and VE's fallback is one config key.

The `end` restore at the bottom of the reader is deliberate: the model-facing
slots have `end` stripped (a published window is a start-time marker, not a
session length - the 2026-07-24 abandoned 90-minute booking), and the aligner
rebuilds the record from those, so the end is put back from the events.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

from app.fast_path import _try_slot_selection
from app.tools import receptionist_tools as rt
from app.tools.receptionist_tools import LONDON_TZ


def _clinic(**over):
    clinic = {
        "clinic_id": "vital_edge",
        "practitioner": "Jonathan",
        "booking_system": "google_calendar_provisional",
        "availability_mode": "published",
        "calendar_id": "availability@example.com",
        "slot_minutes": 60,
        "days_ahead": 30,
        "allow_same_day": False,
    }
    clinic.update(over)
    return clinic


def _event(when: datetime, minutes: int = 60):
    return {
        "id": when.isoformat(),
        "summary": "Available",
        "start": {"dateTime": when.isoformat()},
        "end": {"dateTime": (when + timedelta(minutes=minutes)).isoformat()},
    }


def _published(days, hours=(10,)):
    """One published event per (day offset, hour), off the real clock.

    Anchored on today rather than a fixed date: a pin naming a weekday dies at
    midnight (b55), and the reader's own window is measured from `now`.
    """
    base = datetime.now(LONDON_TZ).replace(minute=0, second=0, microsecond=0)
    return [
        _event(base + timedelta(days=d, hours=h - base.hour))
        for d in days for h in hours
    ]


def _install(monkeypatch, events):
    async def _tok(*a, **k):
        return {"access_token": "x"}

    async def _save(*a, **k):
        return None

    monkeypatch.setattr(rt, "_get_tokens", _tok)
    monkeypatch.setattr(rt, "_save_gcal_tokens", _save)
    monkeypatch.setattr("app.tools.calendar_google.list_upcoming_events",
                        lambda *a, **k: list(events))


def _run(monkeypatch, events, args=None, clinic=None):
    _install(monkeypatch, events)
    session = {"clinic_id": "vital_edge", "call_sid": "TEST"}
    result = asyncio.run(
        rt._check_availability_published(args or {}, session, clinic or _clinic())
    )
    return result, session


def _spoken_starts(result):
    """Every slot start this payload will actually be read out from.

    single_day speaks first_day's times; multi_day speaks ONE time per
    presented day, which is what `_sync_last_offered_to_spoken` records.
    """
    if result.get("presentation_mode") == "single_day":
        return [s["start"] for s in (result.get("first_day") or {}).get("slots") or []]
    out = []
    for day in result.get("presented_days") or []:
        slots = day.get("slots") or []
        if slots:
            out.append(slots[0]["start"])
    return out


# ---------------------------------------------------------------------------
# The defect
# ---------------------------------------------------------------------------
def test_the_record_holds_only_what_was_spoken(monkeypatch):
    """Four published days, two spoken. The record must not hold the other two."""
    result, session = _run(monkeypatch, _published([2, 3, 4, 5]))

    spoken = _spoken_starts(result)
    assert spoken, "the reader presented nothing at all"
    assert [s["start"] for s in session["last_offered_slots"]] == spoken, (
        "the offer record names dates the caller was never read out - "
        "an ordinal indexes straight into them"
    )


def test_an_ordinal_cannot_reach_an_unspoken_date(monkeypatch):
    """The money test. Two days are spoken, so "the third" must resolve to
    nothing rather than to the third published day."""
    result, session = _run(monkeypatch, _published([2, 3, 4, 5]))
    third = sorted({d["date"] for d in result["available_days"]})[2]

    _try_slot_selection("would you like", "the third", "the third", session)
    picked = session.get("selected_slot")
    assert picked is None or not picked["start"].startswith(third), (
        f"an ordinal selected {picked!r} - {third} was never spoken"
    )


def test_the_reader_presents_rather_than_reading_out_everything(monkeypatch):
    """The other half: it now declares a presentation mode and honours the
    caps, like the other six producers. Without this the model was handed every
    published day and every time on it."""
    result, _ = _run(monkeypatch, _published([2, 3, 4, 5]))

    assert result.get("presentation_mode") in ("single_day", "multi_day")
    assert len(_spoken_starts(result)) <= rt._MAX_PRESENTED_DAYS
    assert result.get("more_times") is True, (
        "four days were cut to two and the model was not told there are more"
    )


def test_a_single_day_is_capped_and_flagged(monkeypatch):
    """Five times on one day: speak three, and say there are more."""
    result, session = _run(monkeypatch, _published([3], hours=(9, 10, 11, 14, 15)))

    assert result["presentation_mode"] == "single_day"
    assert len(result["first_day"]["slot_times"]) == rt._MAX_PRESENTED_TIMES_SINGLE_DAY
    assert result["first_day"].get("more_times") is True
    assert [s["start"] for s in session["last_offered_slots"]] == _spoken_starts(result)


# ---------------------------------------------------------------------------
# What must NOT change
# ---------------------------------------------------------------------------
def test_the_bookable_set_is_untouched(monkeypatch):
    """The caps trim SPEECH, never the bookable data. slot_followup and
    _resolve_slot_iso read available_days and must still see every real time."""
    result, session = _run(monkeypatch, _published([2, 3, 4, 5]))

    assert len(result["available_days"]) == 4
    assert result["total_days"] == 4
    assert session["available_days"] == result["available_days"]


def test_the_model_still_never_sees_a_slot_end(monkeypatch):
    """A published window is a start-time marker; the caller chooses 60 or 90.
    Exposing `end` made the model refuse 90-minute requests - the 2026-07-24
    Vital Edge abandoned booking. The cap must not put it back."""
    result, _ = _run(monkeypatch, _published([2, 3, 4], hours=(9, 10)))

    for day in result["available_days"]:
        for slot in day["slots"]:
            assert "end" not in slot


def test_the_record_still_carries_the_end(monkeypatch):
    """Internal slot resolution reads start+end, and every other reader's
    record carries both. The aligner rebuilds from the stripped model payload,
    so the reader puts the end back from the published events."""
    _, session = _run(monkeypatch, _published([2, 3, 4]))

    for slot in session["last_offered_slots"]:
        assert slot["end"], "the offer record lost the slot end"
        assert slot["end"] > slot["start"]


def test_the_slot_labels_match_the_record(monkeypatch):
    """connection.py matches the caller's words against slot_labels and the
    resolver indexes last_offered_slots. They are read together, so they have
    to be the same length or an ordinal and a label disagree."""
    _, session = _run(monkeypatch, _published([2, 3, 4, 5]))

    assert len(session["slot_labels"]) == len(session["last_offered_slots"])


def test_an_empty_calendar_still_says_so(monkeypatch):
    """No published slots is a waitlist answer, not a presentation."""
    result, _ = _run(monkeypatch, [])
    assert result["slots"] == []
    assert "waitlist" in result["error"]
