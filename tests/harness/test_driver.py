"""Self-tests for the headless driver.

TWO TIERS, DELIBERATELY
-----------------------
The cheap tests run on every `pytest` and touch no network at all.

The conversation tests spend real model tokens and are NON-DETERMINISTIC (the
engine does not ask the same questions in the same order twice), so they are
opt-in behind HARNESS_LIVE_LLM=1. Two reasons, both learned here the hard way:

  * The suite has a known-red baseline whose failing SET is not stable across
    identical runs. Work is judged by diffing failing sets. A test that costs
    money and can flake must not be able to move that baseline by default.
  * A live-API test that skips only when credentials are missing does not skip
    at all - conftest.py runs load_dotenv(override=True), so the keys are
    always present. Gating on an explicit opt-in flag is the only thing that
    actually holds. That lesson cost 60 real appointments in a live calendar.

Run them with:  HARNESS_LIVE_LLM=1 pytest tests/harness -q
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta

import pytest

from tests.harness.driver import ConversationDriver
from tests.harness.fake_clinic import FakeDiary, build_tool_executors
from tests.harness.netfence import EgressBlocked

CLINIC = "vital_edge"

live_llm = pytest.mark.skipif(
    os.getenv("HARNESS_LIVE_LLM") != "1",
    reason="spends model tokens and is non-deterministic; set HARNESS_LIVE_LLM=1",
)


def _diary(now: datetime) -> FakeDiary:
    return FakeDiary.weekly(
        start=now + timedelta(days=1), days=21,
        times=["09:00", "10:00", "14:00", "15:00", "16:00"],
        weekdays=[0, 1, 2, 3, 4],
    )


def _now() -> datetime:
    return datetime.now().replace(hour=9, minute=0, second=0, microsecond=0)


# ---------------------------------------------------------------------------
# Cheap: no network, no model
# ---------------------------------------------------------------------------

async def test_every_real_tool_has_a_stub():
    """The driver must refuse to run if any real executor is unstubbed.

    An unstubbed name falls through to the real table, and the real table
    reaches Acuity and Google Calendar. This is the check that makes "the
    harness cannot write to a calendar" a property rather than a hope.
    """
    from app.tools import receptionist_tools as rt

    fake = build_tool_executors(FakeDiary(), [])
    assert set(rt.TOOL_EXECUTORS) - set(fake) == set()


async def test_the_harness_cannot_reach_a_provider():
    import httpx

    async with ConversationDriver(clinic_id=CLINIC) as call:
        assert call.session["clinic_id"] == CLINIC
        for url in (
            "https://acuityscheduling.com/api/v1/appointments",
            "https://www.googleapis.com/calendar/v3/calendars/x/events",
            "https://api.twilio.com/2010-04-01/Accounts/x/Messages.json",
        ):
            with pytest.raises(EgressBlocked):
                async with httpx.AsyncClient() as client:
                    await client.post(url, json={})


async def test_the_fence_is_removed_when_the_call_ends():
    """A leaked patch would break every later test in the session."""
    import httpx

    async with ConversationDriver(clinic_id=CLINIC):
        pass

    with pytest.raises(Exception) as exc:
        async with httpx.AsyncClient() as client:
            await client.get("https://example.invalid", timeout=0.001)
    assert not isinstance(exc.value, EgressBlocked)


async def test_the_availability_payload_has_the_real_shape():
    """The fake reader must emit what the real readers emit.

    If this drifts, every slot assertion built on the harness is measuring a
    shape production never produces.
    """
    from app.media_streams.session import _fresh_session

    now = _now()
    diary = _diary(now)
    table = build_tool_executors(diary, [], now=now)
    session = _fresh_session()
    session["clinic_id"] = CLINIC
    # The fake runs the REAL _exec_check_availability, so the real gates apply.
    # A session that has not chosen a length gets `duration_choice_required`,
    # correctly — Vital Edge's massages are 60 or 90. Satisfy the gate rather
    # than bypass it; bypassing is how a harness stops testing the engine.
    session["_service_duration_choice"] = 60
    session["selected_location"] = "kingston"
    session["location_confirmed"] = True
    session["confirmed_location"] = "kingston"

    out = await table["check_availability"](
        {"service": "sports_massage", "date_hint": "Tuesday afternoon",
         "duration_minutes": 60, "location": "kingston"},
        session,
    )

    for key in ("available_days", "total_days", "presentation_mode",
                "presented_days", "more_times"):
        assert key in out, f"payload is missing {key!r}"

    day = out["available_days"][0]
    for key in ("date", "day_label", "slot_times", "slot_times_spoken", "slots",
                "times_found_on_day", "times_not_shown"):
        assert key in day, f"day summary is missing {key!r}"

    # The preference must have been honoured by the REAL filter.
    assert all(d["day_label"].startswith("Tuesday") for d in out["available_days"])
    assert all(t >= "12:00" for d in out["available_days"] for t in d["slot_times"])

    # And the offer record must have been synced, not left behind.
    assert session["last_offered_slots"]
    assert session["slot_labels"]


async def test_slots_are_london_aware():
    """Naive tuples raise inside _filter_tuples_by_preference, and the broad
    except turns that into "I'm having trouble with availability" - a provider
    outage that never happened."""
    now = _now()
    starts = [s for s, _ in _diary(now).free_tuples()]
    assert starts and all(s.tzinfo is not None for s in starts)
    # pytz .localize(), not replace(tzinfo=...): the latter yields LMT (-00:01).
    assert all(s.utcoffset().total_seconds() % 60 == 0 for s in starts)


# ---------------------------------------------------------------------------
# Live: real prompt, real model
# ---------------------------------------------------------------------------

@live_llm
async def test_a_full_booking_arc_writes_exactly_one_diary_entry():
    now = _now()
    diary = _diary(now)
    script = [
        "Hi there, I'd like to book a sports massage please",
        "It's for muscle recovery, I've been running a lot",
        "The sixty minute one please",
        "Next Tuesday afternoon if you have anything",
        "The first one sounds good",
        "It's Daniel Okafor",
        "Yeah that number's fine",
        "Yes please, go ahead and book it",
    ]

    async with ConversationDriver(clinic_id=CLINIC, diary=diary, now=now) as call:
        for line in script:
            await call.say(line)

        assert len(diary.bookings) == 1, call.transcript
        booked = diary.bookings[0]
        assert booked.name == "Daniel Okafor", call.transcript
        assert booked.duration_min == 60, call.transcript

        # Vital Edge is a PROVISIONAL clinic: the slot is written as PENDING and
        # Jonathan confirms it directly. `booking_confirmed` is deliberately NOT
        # set here - asserting it would be asserting that Susie lied to the
        # caller about a booking being final.
        assert call.session.get("provisional_booking") is True
        assert call.session.get("calendar_status") == "provisional"

        # The time written must be a time she actually read out.
        spoken_hours = {
            t["start"][11:16] for t in (call.session.get("last_offered_slots") or [])
        }
        assert booked.start[11:16] in spoken_hours or spoken_hours == set(), (
            f"booked {booked.start} was never offered: {spoken_hours}\n{call.transcript}"
        )


@live_llm
@pytest.mark.xfail(
    strict=False,
    reason=(
        "OPEN DEFECT, reproduced 3/3 on 2026-08-28. The caller asks for a "
        "sports massage; check_availability is correctly called with "
        "service='sports_massage'; but nothing latches it - "
        "session['selected_service'] is READ at receptionist_tools.py:6450 and "
        "WRITTEN NOWHERE. By book_appointment the model re-derives the service "
        "from context, where the prompt's deep-tissue framing dominates, and "
        "writes deep_tissue_massage to the diary. The service is never spoken "
        "back, so no caller can catch it. Same family as the duration defect "
        "fixed in 6d7d1b2c: capture the caller's choice in the ENGINE, not the "
        "model. Un-xfail with that fix."
    ),
)
async def test_the_service_booked_is_the_service_the_caller_asked_for():
    now = _now()
    diary = _diary(now)
    script = [
        "Hi there, I'd like to book a sports massage please",
        "It's for muscle recovery, I've been running a lot",
        "The sixty minute one please",
        "Next Tuesday afternoon if you have anything",
        "The first one sounds good",
        "It's Daniel Okafor",
        "Yeah that number's fine",
        "Yes please, go ahead and book it",
    ]

    async with ConversationDriver(clinic_id=CLINIC, diary=diary, now=now) as call:
        for line in script:
            await call.say(line)

        assert diary.bookings, call.transcript
        assert diary.bookings[0].service == "sports_massage", (
            f"caller asked for a sports massage, diary got "
            f"{diary.bookings[0].service!r}\n{call.transcript}"
        )
