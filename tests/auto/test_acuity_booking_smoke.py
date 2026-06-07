"""
test_acuity_booking_smoke.py — Live Acuity booking creation smoke test.

Creates a real test booking against the Acuity API, verifies it succeeds,
then immediately cancels it.  Catches every form-field / 400-error class of
bug before a demo without requiring a manual phone call.

Run with:
    python -m pytest tests/auto/test_acuity_booking_smoke.py -v -s

Requires ACUITY_USER_ID and ACUITY_API_KEY in tests/auto/.env or environment.
Skipped automatically if credentials are missing.

SAFE: every booking created by this test is cancelled immediately.
The booking note "TEST BOOKING — IGNORE" also makes it identifiable in Acuity.
"""

import os
import sys
import asyncio
from datetime import date, timedelta
from pathlib import Path

import pytest
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).parent / ".env", override=True)

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

ACUITY_USER_ID = os.getenv("ACUITY_USER_ID", "").strip()
ACUITY_API_KEY = os.getenv("ACUITY_API_KEY", "").strip()

# Both Theorem Health appointment types
LOCATIONS = [
    {
        "name": "Redditch",
        "appointment_type_id": "acuity_33801703",
        "calendar_env": "ACUITY_CALENDAR_ID_REDDITCH",
        "calendar_fallback": "",
    },
    {
        "name": "Alcester",
        "appointment_type_id": "acuity_15823699",
        "calendar_env": "ACUITY_CALENDAR_ID_ALCESTER",
        "calendar_fallback": "4256627",
    },
]

_skip = pytest.mark.skipif(
    not (ACUITY_USER_ID and ACUITY_API_KEY),
    reason="ACUITY_USER_ID / ACUITY_API_KEY not set — skipping live booking test",
)


@pytest.fixture
def adapter():
    from app.booking.booking.providers.acuity import AcuityAdapter
    return AcuityAdapter(
        user_id=ACUITY_USER_ID,
        api_key=ACUITY_API_KEY,
        clinic_id="theorem_smoke_test",
    )


async def _book_and_cancel(adapter, location: dict) -> None:
    """Core logic: find first available slot, create booking, cancel it."""
    from app.booking.booking.models import BookingRequest

    name = location["name"]
    type_id = location["appointment_type_id"]
    cal_id = os.getenv(location["calendar_env"], location["calendar_fallback"]).strip()
    practitioner_id = f"acuity_cal_{cal_id}" if cal_id else None

    # ── 1. Get first available slot ──────────────────────────────────────────
    start = date.today() + timedelta(days=1)
    end   = date.today() + timedelta(days=30)
    slots = await adapter.get_available_slots(
        appointment_type_id=type_id,
        start_date=start,
        end_date=end,
        practitioner_id=practitioner_id,
    )
    assert slots, (
        f"[{name}] No slots available in the next 30 days — "
        "cannot run booking smoke test. Check Acuity availability."
    )
    slot = slots[0]
    print(f"\n[{name}] Using slot: {slot.start_time}")

    # ── 2. Create booking ────────────────────────────────────────────────────
    request = BookingRequest(
        appointment_type_id=type_id,
        slot_start=slot.start_time,
        location_id=name.lower(),
        patient_first_name="Test",
        patient_last_name="Booking",
        patient_phone="+447000000000",
        patient_email="voicebooking+smoke@theorem-health.com",
        notes="TEST BOOKING — IGNORE — created by automated smoke test",
        practitioner_id=practitioner_id,
        call_sid="smoke_test_000",
        session_id="smoke_test_000",
    )

    booking = await adapter.create_booking(request)
    print(f"[{name}] Booking created: id={booking.provider_booking_id}")

    assert booking.provider_booking_id, f"[{name}] Booking returned no ID"

    # ── 3. Cancel immediately ────────────────────────────────────────────────
    cancelled = await adapter.cancel_booking(booking.provider_booking_id)
    print(f"[{name}] Booking cancelled: {cancelled}")
    assert cancelled, f"[{name}] Failed to cancel test booking {booking.provider_booking_id}"


@_skip
@pytest.mark.asyncio
async def test_redditch_booking_roundtrip(adapter):
    """
    Create and immediately cancel a test booking for Redditch (type 33801703).
    Catches all required-form-field 400 errors for this appointment type.
    """
    await _book_and_cancel(adapter, LOCATIONS[0])


@_skip
@pytest.mark.asyncio
async def test_alcester_booking_roundtrip(adapter):
    """
    Create and immediately cancel a test booking for Alcester (type 15823699).
    Catches all required-form-field 400 errors for this appointment type.
    """
    await _book_and_cancel(adapter, LOCATIONS[1])


@_skip
@pytest.mark.asyncio
async def test_required_fields_redditch(adapter):
    """
    Verify _get_required_form_fields returns the expected field IDs for Redditch.
    Fails immediately (no API call) if the hardcoded map is wrong.
    """
    fields = await adapter._get_required_form_fields("acuity_33801703")
    ids = {f["id"] for f in fields}
    print(f"\n[Redditch] Required field IDs: {ids}")

    assert 10610285 in ids, "Missing T&C checkbox (10610285)"
    assert 13388219 in ids, "Missing 'agree to terms' checkbox (13388219)"
    assert 8494898  in ids, "Missing D.O.B field (8494898)"
    assert 8871008  in ids, "Missing Address field (8871008)"


@_skip
@pytest.mark.asyncio
async def test_required_fields_alcester(adapter):
    """
    Verify _get_required_form_fields returns the expected field IDs for Alcester.
    """
    fields = await adapter._get_required_form_fields("acuity_15823699")
    ids = {f["id"] for f in fields}
    print(f"\n[Alcester] Required field IDs: {ids}")

    assert 10610285 in ids, "Missing T&C checkbox (10610285)"
    assert 13388219 in ids, "Missing 'agree to terms' checkbox (13388219)"
    assert 8494898  in ids, "Missing D.O.B field (8494898)"
    assert 8871008  in ids, "Missing Address field (8871008)"
