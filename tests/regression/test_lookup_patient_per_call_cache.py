"""lookup_patient refetched the calendar three times in one call.

jv_v2 `CA38e560314294de4e5671168fc6975db5`, 2026-08-18 — same phone, same
appointment, identical result each time:

    11:26:35  lookup_patient -> 'Jonathan Moore' id=gsmelii4mu0g6sfs8q68efvv80
    11:27:02  lookup_patient -> 'Jonathan Moore' id=gsmelii4mu0g6sfs8q68efvv80
    11:27:48  lookup_patient -> 'Jonathan Moore' id=gsmelii4mu0g6sfs8q68efvv80

The third sat directly in front of the reschedule write, on the turn the caller
waited 10.9 s for. Each repeat is a ~250 ms Google Calendar round-trip.

The cache is the easy half. **The invalidation is the half that can hurt a
caller**, so most of this file is about that: a cached appointment that survives
a write would be read back as current after it had already moved.
"""
from __future__ import annotations

import pytest

from app.tools import receptionist_tools as rt


class _Counter:
    """Stands in for the Google Calendar fetch and counts round-trips."""

    def __init__(self, events):
        self.events = events
        self.calls = 0

    def __call__(self, tokens, days, limit, calendar_id):
        self.calls += 1
        return self.events


_EVENT = {
    "id": "gsmelii4mu0g6sfs8q68efvv80",
    "summary": "Jonathan Moore — Initial Assessment (Musculoskeletal)",
    "description": "Phone: 07502211207",
    "start": {"dateTime": "2026-08-24T18:45:00+01:00"},
}


@pytest.fixture
def patched(monkeypatch):
    counter = _Counter([_EVENT])
    monkeypatch.setattr(
        "app.tools.calendar_google.list_upcoming_events", counter, raising=False
    )
    monkeypatch.setattr(rt, "_get_tokens", _async_ret({"access_token": "x"}))
    monkeypatch.setattr(rt, "_save_gcal_tokens", _async_ret(None))
    monkeypatch.setattr(rt, "_resolve_calendar_id", lambda clinic, loc: "cal-1")
    monkeypatch.setattr(rt, "_resolve_clinic_id", lambda s: "jv_v1")
    monkeypatch.setattr("app.clinic_config.get_clinic", lambda cid: {}, raising=False)
    return counter


def _async_ret(value):
    async def _f(*a, **k):
        return value
    return _f


def _session():
    return {"clinic_id": "jv_v1"}


async def test_three_lookups_cost_one_round_trip(patched):
    """The live defect: same phone, same call, three lookups."""
    s = _session()
    for _ in range(3):
        r = await rt._lookup_patient_gcal({"phone": "07502211207"}, s)
        assert r["found"] is True

    assert patched.calls == 1, (
        f"expected 1 calendar round-trip across 3 lookups, got {patched.calls}"
    )


async def test_side_effects_still_run_on_a_cached_lookup(patched):
    """Only the FETCH is cached. Everything the rest of the turn reads must
    still be written, or the cache trades latency for a silent state bug."""
    s = _session()
    await rt._lookup_patient_gcal({"phone": "07502211207"}, s)

    # Wipe what _emit sets, then look up again off the cache.
    for k in ("_lookup_patient_name", "_lookup_appointment_id",
              "_lookup_appointment_datetime", "_lookup_appointment_type"):
        s.pop(k, None)

    await rt._lookup_patient_gcal({"phone": "07502211207"}, s)

    assert s["_lookup_appointment_id"] == "gsmelii4mu0g6sfs8q68efvv80"
    assert s["_lookup_appointment_datetime"] == "2026-08-24T18:45:00+01:00"
    assert s["_lookup_patient_name"]


async def test_a_write_invalidates_the_cache(patched):
    """The one that protects a caller: after a write the calendar has moved."""
    s = _session()
    await rt._lookup_patient_gcal({"phone": "07502211207"}, s)
    assert patched.calls == 1

    rt._invalidate_gcal_lookup_cache(s, "reschedule_appointment")

    await rt._lookup_patient_gcal({"phone": "07502211207"}, s)
    assert patched.calls == 2, "a lookup after a write must refetch, not serve stale"


async def test_a_FAILED_write_also_invalidates():
    """Not conditioned on success. A write that reports failure may still have
    mutated the calendar, and the costs are asymmetric: an extra refetch versus
    reading a moved appointment back to the caller as current.
    """
    calls = {"n": 0}

    async def _boom(args, session):
        calls["n"] += 1
        raise RuntimeError("provider exploded mid-write")

    wrapped = rt._invalidates_lookup_cache(_boom)
    s = {"clinic_id": "jv_v1", rt._GCAL_UPCOMING_CACHE_KEY: {"cal-1": [_EVENT]}}

    with pytest.raises(RuntimeError):
        await wrapped({}, s)

    assert calls["n"] == 1
    assert rt._GCAL_UPCOMING_CACHE_KEY not in s, (
        "a raising write left the cache in place — the next lookup would serve "
        "an appointment that may no longer exist"
    )


def test_all_three_write_tools_are_wrapped():
    """A new write tool registered unwrapped is the way this regresses."""
    for name in ("book_appointment", "cancel_appointment", "reschedule_appointment"):
        fn = rt.TOOL_EXECUTORS[name]
        assert getattr(fn, "__wrapped__", None) is not None, (
            f"{name} is registered in TOOL_EXECUTORS without "
            f"_invalidates_lookup_cache — it can strand a stale lookup"
        )


def test_the_cache_is_per_session_not_module_level():
    """Two concurrent calls must not share one caller's appointments."""
    a, b = _session(), _session()
    a[rt._GCAL_UPCOMING_CACHE_KEY] = {"cal-1": [_EVENT]}
    assert rt._GCAL_UPCOMING_CACHE_KEY not in b
