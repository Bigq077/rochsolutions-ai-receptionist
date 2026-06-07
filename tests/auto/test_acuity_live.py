"""
test_acuity_live.py — Standalone live Acuity availability test.

Calls the real Acuity API directly (not via the call runner or WebSocket).
Verifies that `get_available_slots` actually returns slots for the next 30 days,
using the real appointment type ID and calendar IDs configured for Theorem Health.

Run with:
    python -m pytest tests/auto/test_acuity_live.py -v -s

Requires ACUITY_USER_ID and ACUITY_API_KEY to be set in tests/auto/.env or environment.
This test is skipped automatically if credentials are missing.
"""

import os
import sys
import asyncio
from datetime import date, timedelta
from pathlib import Path

import pytest

# Load the test .env so credentials are available
from dotenv import load_dotenv
load_dotenv(dotenv_path=Path(__file__).parent / ".env", override=True)

# Resolve project root so `app` is importable
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

ACUITY_USER_ID = os.getenv("ACUITY_USER_ID", "").strip()
ACUITY_API_KEY = os.getenv("ACUITY_API_KEY", "").strip()

APPOINTMENT_TYPE_ID = "acuity_15823699"   # Theorem Health — 50 min physiotherapy assessment
CALENDAR_IDS = {
    "alcester":  os.getenv("ACUITY_CALENDAR_ID_ALCESTER", "4256627"),
    "redditch":  os.getenv("ACUITY_CALENDAR_ID_REDDITCH", ""),
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def acuity_adapter():
    from app.booking.booking.providers.acuity import AcuityAdapter
    return AcuityAdapter(
        user_id=ACUITY_USER_ID,
        api_key=ACUITY_API_KEY,
        clinic_id="theorem",
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not (ACUITY_USER_ID and ACUITY_API_KEY),
    reason="ACUITY_USER_ID / ACUITY_API_KEY not set — skipping live test",
)
@pytest.mark.asyncio
async def test_acuity_slots_returned(acuity_adapter):
    """
    Real Acuity API call: verify at least 1 slot is returned over the next 30 days.

    This tests the per-day query fix — if the endDate bug were still present the
    response would be empty even though the Acuity dashboard shows availability.
    """
    start = date.today() + timedelta(days=1)
    end   = date.today() + timedelta(days=31)

    slots = await acuity_adapter.get_available_slots(
        appointment_type_id=APPOINTMENT_TYPE_ID,
        start_date=start,
        end_date=end,
        practitioner_id=f"acuity_cal_{CALENDAR_IDS['alcester']}",
    )

    print(f"\n[acuity_live] Slots returned: {len(slots)}")
    if slots:
        print(f"[acuity_live] First slot: {slots[0]}")
        print(f"[acuity_live] Last  slot: {slots[-1]}")

    assert len(slots) > 0, (
        f"Acuity returned zero slots for the next 30 days "
        f"(appointmentTypeID=15823699, calendarID={CALENDAR_IDS['alcester']}). "
        "Check Render logs for '{date}: N slots' lines from the per-day query loop."
    )


@pytest.mark.skipif(
    not (ACUITY_USER_ID and ACUITY_API_KEY),
    reason="ACUITY_USER_ID / ACUITY_API_KEY not set — skipping live test",
)
@pytest.mark.asyncio
async def test_acuity_slot_structure(acuity_adapter):
    """
    Verify the slot objects returned by get_available_slots have the fields
    that flow.py and receptionist_tools.py depend on.
    """
    start = date.today() + timedelta(days=1)
    end   = date.today() + timedelta(days=14)

    slots = await acuity_adapter.get_available_slots(
        appointment_type_id=APPOINTMENT_TYPE_ID,
        start_date=start,
        end_date=end,
        practitioner_id=f"acuity_cal_{CALENDAR_IDS['alcester']}",
    )

    if not slots:
        pytest.skip("No slots available in the next 14 days — can't verify structure")

    slot = slots[0]
    print(f"\n[acuity_live] Slot structure: {slot}")

    # These are the fields that receptionist_tools._exec_check_availability reads
    assert hasattr(slot, "start") or "start" in (slot if isinstance(slot, dict) else {}), \
        "Slot missing 'start' field"


@pytest.mark.skipif(
    not (ACUITY_USER_ID and ACUITY_API_KEY),
    reason="ACUITY_USER_ID / ACUITY_API_KEY not set — skipping live test",
)
@pytest.mark.asyncio
async def test_acuity_no_calendar_id(acuity_adapter):
    """
    Without a calendarID, Acuity should still return slots (all practitioners combined).
    """
    start = date.today() + timedelta(days=1)
    end   = date.today() + timedelta(days=31)

    slots = await acuity_adapter.get_available_slots(
        appointment_type_id=APPOINTMENT_TYPE_ID,
        start_date=start,
        end_date=end,
        practitioner_id=None,
    )

    print(f"\n[acuity_live] Slots without calendarID: {len(slots)}")
    assert len(slots) > 0, (
        "Acuity returned zero slots even without a calendarID filter. "
        "Appointment type ID may be wrong or clinic has no availability."
    )


@pytest.mark.skipif(
    not (ACUITY_USER_ID and ACUITY_API_KEY),
    reason="ACUITY_USER_ID / ACUITY_API_KEY not set — skipping live test",
)
@pytest.mark.asyncio
async def test_acuity_per_day_query_logs(acuity_adapter, capsys):
    """
    Smoke test: run a 7-day query and verify the per-day debug output format.
    The per-day loop prints '{date}: N slots' — this test captures that output
    so you can visually verify which days have slots.
    """
    start = date.today() + timedelta(days=1)
    end   = start + timedelta(days=7)

    slots = await acuity_adapter.get_available_slots(
        appointment_type_id=APPOINTMENT_TYPE_ID,
        start_date=start,
        end_date=end,
        practitioner_id=f"acuity_cal_{CALENDAR_IDS['alcester']}",
    )

    captured = capsys.readouterr()
    print(f"\n[acuity_live] 7-day query output:\n{captured.out}")
    print(f"[acuity_live] Total slots in 7 days: {len(slots)}")

    # The print() calls in get_available_slots should have produced at least
    # one line in the format '2026-04-03: N slots'
    import re
    day_lines = re.findall(r"\d{4}-\d{2}-\d{2}: \d+ slots", captured.out)
    assert len(day_lines) >= 1, (
        "Per-day query loop did not produce expected '2026-04-xx: N slots' output. "
        "The print() statements may have been removed from get_available_slots."
    )
    print(f"[acuity_live] Day lines found: {day_lines}")
