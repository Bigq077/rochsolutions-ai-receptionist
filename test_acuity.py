#!/usr/bin/env python3
"""
test_acuity.py — Acuity Scheduling connectivity & configuration diagnostic.

Run this once Mark's credentials are set in .env (or export them first):

    python test_acuity.py

It will print:
  1. Connection status
  2. All appointment types (with IDs — needed for ACUITY_APPOINTMENT_TYPE_ID_*)
  3. All calendars       (with IDs — needed for ACUITY_CALENDAR_ID_*)
  4. Available slots for the next 14 days (per calendar)

Once you have the calendar IDs, add them to Render env vars:
  ACUITY_CALENDAR_ID_ALCESTER  = <id of the Alcester calendar>
  ACUITY_CALENDAR_ID_REDDITCH  = <id of the Redditch calendar>
"""

import asyncio
import os
import sys
from datetime import date, timedelta

# Load .env if present
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

ACUITY_USER_ID = os.getenv("ACUITY_USER_ID", "").strip()
ACUITY_API_KEY = os.getenv("ACUITY_API_KEY", "").strip()

ALCESTER_CAL  = os.getenv("ACUITY_CALENDAR_ID_ALCESTER", "").strip()
REDDITCH_CAL  = os.getenv("ACUITY_CALENDAR_ID_REDDITCH", "").strip()


def _sep(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


async def main():
    if not ACUITY_USER_ID or not ACUITY_API_KEY:
        print("\n❌  ACUITY_USER_ID or ACUITY_API_KEY not set.")
        print("    Add them to your .env file or export them, then re-run.\n")
        print("    Where to find them:")
        print("    Acuity → Business Settings → Integrations → API")
        sys.exit(1)

    # Import after dotenv so paths resolve
    sys.path.insert(0, os.path.dirname(__file__))
    from app.booking.booking.providers.acuity import AcuityAdapter

    adapter = AcuityAdapter(
        user_id=ACUITY_USER_ID,
        api_key=ACUITY_API_KEY,
        clinic_id="theorem",
    )

    # ------------------------------------------------------------------ #
    # 1. Appointment types
    # ------------------------------------------------------------------ #
    _sep("APPOINTMENT TYPES")
    try:
        types = await adapter.get_appointment_types()
        if not types:
            print("  ⚠️  No appointment types found — check Acuity account.")
        for t in types:
            print(f"  ID={t.provider_id:>10}  name={t.name!r}  duration={t.duration_minutes}min")
    except Exception as e:
        print(f"  ❌  Failed: {e}")
        sys.exit(1)

    # ------------------------------------------------------------------ #
    # 2. Calendars (practitioners / locations)
    # ------------------------------------------------------------------ #
    _sep("CALENDARS  (set these as env vars)")
    try:
        practitioners = await adapter.get_practitioners()
        if not practitioners:
            print("  ⚠️  No calendars found.")
        for p in practitioners:
            marker = ""
            if ALCESTER_CAL and p.provider_id == ALCESTER_CAL:
                marker = "  ← ACUITY_CALENDAR_ID_ALCESTER ✓"
            elif REDDITCH_CAL and p.provider_id == REDDITCH_CAL:
                marker = "  ← ACUITY_CALENDAR_ID_REDDITCH ✓"
            print(f"  calendarID={p.provider_id:>10}  name={p.name!r}{marker}")

        print()
        if not ALCESTER_CAL:
            print("  ⚠️  ACUITY_CALENDAR_ID_ALCESTER not set — look above and add to Render")
        else:
            print(f"  ✅  ACUITY_CALENDAR_ID_ALCESTER = {ALCESTER_CAL}")
        if not REDDITCH_CAL:
            print("  ⚠️  ACUITY_CALENDAR_ID_REDDITCH not set — look above and add to Render")
        else:
            print(f"  ✅  ACUITY_CALENDAR_ID_REDDITCH = {REDDITCH_CAL}")
    except Exception as e:
        print(f"  ❌  Failed: {e}")

    # ------------------------------------------------------------------ #
    # 3. Availability check — first appointment type, no calendar filter
    # ------------------------------------------------------------------ #
    _sep("AVAILABILITY  (next 14 days, all calendars)")
    if types:
        first_type = types[0]
        try:
            today = date.today()
            slots = await adapter.get_available_slots(
                appointment_type_id=first_type.id,
                start_date=today,
                end_date=today + timedelta(days=14),
            )
            if not slots:
                print(f"  ⚠️  No slots for {first_type.name!r} in next 14 days.")
                print("      Check Acuity calendar has availability set.")
            else:
                print(f"  Found {len(slots)} slots for {first_type.name!r}.")
                for s in slots[:5]:
                    print(f"    {s.start_time.strftime('%a %d %b %H:%M')}")
                if len(slots) > 5:
                    print(f"    ... and {len(slots)-5} more")
        except Exception as e:
            print(f"  ❌  Availability check failed: {e}")

    # ------------------------------------------------------------------ #
    # 4. Availability per calendar
    # ------------------------------------------------------------------ #
    for cal_label, cal_id in [
        ("ALCESTER", ALCESTER_CAL),
        ("REDDITCH", REDDITCH_CAL),
    ]:
        if not cal_id:
            print(f"\n  ⚠️  {cal_label} calendar ID not set — skipping per-calendar check.")
            continue
        _sep(f"AVAILABILITY — {cal_label} calendar (ID={cal_id})")
        if types:
            try:
                today = date.today()
                slots = await adapter.get_available_slots(
                    appointment_type_id=types[0].id,
                    start_date=today,
                    end_date=today + timedelta(days=14),
                    practitioner_id=f"acuity_cal_{cal_id}",
                )
                if not slots:
                    print(f"  ⚠️  No slots for {cal_label} in next 14 days.")
                else:
                    print(f"  Found {len(slots)} slots.")
                    for s in slots[:3]:
                        print(f"    {s.start_time.strftime('%a %d %b %H:%M')}")
            except Exception as e:
                print(f"  ❌  {e}")

    await adapter.close()

    _sep("SUMMARY")
    print(f"  ACUITY_USER_ID          = {'✅ set' if ACUITY_USER_ID else '❌ MISSING'}")
    print(f"  ACUITY_API_KEY          = {'✅ set' if ACUITY_API_KEY else '❌ MISSING'}")
    print(f"  ACUITY_CALENDAR_ID_ALCESTER = {'✅ ' + ALCESTER_CAL if ALCESTER_CAL else '⚠️  not set'}")
    print(f"  ACUITY_CALENDAR_ID_REDDITCH = {'✅ ' + REDDITCH_CAL if REDDITCH_CAL else '⚠️  not set'}")
    print()


if __name__ == "__main__":
    asyncio.run(main())
