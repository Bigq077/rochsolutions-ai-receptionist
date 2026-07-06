# test_sms_system.py
"""
Test script for Theorem Health SMS notification system.
Run this to verify all SMS functionality is working correctly.
"""

import asyncio
import os
from datetime import datetime, timedelta
from dotenv import load_dotenv
import pytest

# Load environment variables
load_dotenv()

# Sends real SMS via live Twilio — never run in CI. Excluded by pytest.ini's
# default `-m "not integration"`; run explicitly with `pytest -m integration`.
pytestmark = pytest.mark.integration

# Import SMS functions
from app.notifications.booking_sms import (
    send_booking_confirmation,
    send_24hr_reminder,
    send_same_day_reminder,
    send_cancellation_confirmation,
    send_reschedule_confirmation,
    send_callback_confirmation,
    send_first_visit_welcome,
    send_insurance_receipt_notification,
)
from app.notifications.scheduler import (
    schedule_appointment_reminders,
    cancel_appointment_reminders,
    send_test_reminder,
    get_pending_reminders_count,
    get_upcoming_reminders,
)


# ============================================================================
# TEST CONFIGURATION
# ============================================================================

# YOUR TEST PHONE NUMBER - UPDATE THIS!
TEST_PHONE = "+447870166861"  # ← Change to your UK mobile number

# Test patient details
TEST_PATIENT = "Sarah"
TEST_APPOINTMENT_TIME = datetime.now() + timedelta(days=2, hours=14)  # 2 days from now at 2pm
TEST_LOCATION = "Alcester"


# ============================================================================
# INDIVIDUAL TEST FUNCTIONS
# ============================================================================

async def test_booking_confirmation():
    """Test 1: Booking confirmation SMS"""
    print("\n🧪 TEST 1: Booking Confirmation")
    print("=" * 50)
    
    success = await send_booking_confirmation(
        patient_phone=TEST_PHONE,
        patient_name=TEST_PATIENT,
        appointment_time=TEST_APPOINTMENT_TIME,
        location=TEST_LOCATION,
        service="physiotherapy",
        practitioner="Mark",
        is_new_patient=False,
        has_insurance=False,
    )
    
    if success:
        print("✅ Booking confirmation sent successfully!")
        print(f"   Sent to: {TEST_PHONE}")
        print(f"   Check your phone for the message.")
    else:
        print("❌ Failed to send booking confirmation")
    
    return success


async def test_24hr_reminder():
    """Test 2: 24-hour reminder SMS"""
    print("\n🧪 TEST 2: 24-Hour Reminder")
    print("=" * 50)
    
    success = await send_24hr_reminder(
        patient_phone=TEST_PHONE,
        patient_name=TEST_PATIENT,
        appointment_time=TEST_APPOINTMENT_TIME,
        location=TEST_LOCATION,
        is_new_patient=True,  # Will include "what to bring"
        has_insurance=False,
    )
    
    if success:
        print("✅ 24-hour reminder sent successfully!")
        print(f"   Sent to: {TEST_PHONE}")
    else:
        print("❌ Failed to send 24-hour reminder")
    
    return success


async def test_same_day_reminder():
    """Test 3: Same-day reminder SMS"""
    print("\n🧪 TEST 3: Same-Day Reminder")
    print("=" * 50)
    
    success = await send_same_day_reminder(
        patient_phone=TEST_PHONE,
        patient_name=TEST_PATIENT,
        appointment_time=TEST_APPOINTMENT_TIME,
        location=TEST_LOCATION,
    )
    
    if success:
        print("✅ Same-day reminder sent successfully!")
        print(f"   Sent to: {TEST_PHONE}")
    else:
        print("❌ Failed to send same-day reminder")
    
    return success


async def test_insurance_booking():
    """Test 4: Insurance booking confirmation"""
    print("\n🧪 TEST 4: Insurance Booking Confirmation")
    print("=" * 50)
    
    success = await send_booking_confirmation(
        patient_phone=TEST_PHONE,
        patient_name=TEST_PATIENT,
        appointment_time=TEST_APPOINTMENT_TIME,
        location=TEST_LOCATION,
        has_insurance=True,
        insurer="AXA Health",
    )
    
    if success:
        print("✅ Insurance booking confirmation sent!")
        print(f"   Sent to: {TEST_PHONE}")
    else:
        print("❌ Failed to send insurance booking confirmation")
    
    return success


async def test_new_patient_welcome():
    """Test 5: First-time patient welcome"""
    print("\n🧪 TEST 5: New Patient Welcome")
    print("=" * 50)
    
    success = await send_booking_confirmation(
        patient_phone=TEST_PHONE,
        patient_name=TEST_PATIENT,
        appointment_time=TEST_APPOINTMENT_TIME,
        location=TEST_LOCATION,
        is_new_patient=True,
    )
    
    if success:
        print("✅ New patient welcome sent!")
        print(f"   Sent to: {TEST_PHONE}")
    else:
        print("❌ Failed to send new patient welcome")
    
    return success


async def test_cancellation():
    """Test 6: Cancellation confirmation"""
    print("\n🧪 TEST 6: Cancellation Confirmation")
    print("=" * 50)
    
    success = await send_cancellation_confirmation(
        patient_phone=TEST_PHONE,
        patient_name=TEST_PATIENT,
        appointment_time=TEST_APPOINTMENT_TIME,
        is_late_cancellation=False,
    )
    
    if success:
        print("✅ Cancellation confirmation sent!")
        print(f"   Sent to: {TEST_PHONE}")
    else:
        print("❌ Failed to send cancellation confirmation")
    
    return success


async def test_late_cancellation():
    """Test 7: Late cancellation warning"""
    print("\n🧪 TEST 7: Late Cancellation Warning")
    print("=" * 50)
    
    success = await send_cancellation_confirmation(
        patient_phone=TEST_PHONE,
        patient_name=TEST_PATIENT,
        appointment_time=TEST_APPOINTMENT_TIME,
        is_late_cancellation=True,
    )
    
    if success:
        print("✅ Late cancellation warning sent!")
        print(f"   Sent to: {TEST_PHONE}")
    else:
        print("❌ Failed to send late cancellation warning")
    
    return success


async def test_reschedule():
    """Test 8: Reschedule confirmation"""
    print("\n🧪 TEST 8: Reschedule Confirmation")
    print("=" * 50)
    
    old_time = TEST_APPOINTMENT_TIME
    new_time = TEST_APPOINTMENT_TIME + timedelta(days=1)
    
    success = await send_reschedule_confirmation(
        patient_phone=TEST_PHONE,
        patient_name=TEST_PATIENT,
        old_time=old_time,
        new_time=new_time,
        location=TEST_LOCATION,
    )
    
    if success:
        print("✅ Reschedule confirmation sent!")
        print(f"   Sent to: {TEST_PHONE}")
    else:
        print("❌ Failed to send reschedule confirmation")
    
    return success


async def test_callback_confirmation():
    """Test 9: Callback request confirmation"""
    print("\n🧪 TEST 9: Callback Confirmation")
    print("=" * 50)
    
    success = await send_callback_confirmation(
        patient_phone=TEST_PHONE,
        patient_name=TEST_PATIENT,
    )
    
    if success:
        print("✅ Callback confirmation sent!")
        print(f"   Sent to: {TEST_PHONE}")
    else:
        print("❌ Failed to send callback confirmation")
    
    return success


async def test_insurance_receipt():
    """Test 10: Insurance receipt notification"""
    print("\n🧪 TEST 10: Insurance Receipt Notification")
    print("=" * 50)
    
    success = await send_insurance_receipt_notification(
        patient_phone=TEST_PHONE,
        patient_name=TEST_PATIENT,
        insurer="AXA Health",
    )
    
    if success:
        print("✅ Insurance receipt notification sent!")
        print(f"   Sent to: {TEST_PHONE}")
    else:
        print("❌ Failed to send insurance receipt notification")
    
    return success


async def test_scheduler():
    """Test 11: Reminder scheduler"""
    print("\n🧪 TEST 11: Reminder Scheduler")
    print("=" * 50)
    
    # Schedule reminders for test appointment
    success = await schedule_appointment_reminders(
        patient_phone=TEST_PHONE,
        patient_name=TEST_PATIENT,
        appointment_time=TEST_APPOINTMENT_TIME,
        location=TEST_LOCATION,
        is_new_patient=True,
        has_insurance=False,
    )
    
    if success:
        print("✅ Reminders scheduled successfully!")
        
        # Check how many reminders are pending
        count = await get_pending_reminders_count()
        print(f"   Total pending reminders: {count}")
        
        # Get upcoming reminders
        upcoming = await get_upcoming_reminders(limit=5)
        print(f"   Next {len(upcoming)} reminders:")
        for reminder in upcoming:
            reminder_time = datetime.fromisoformat(reminder["reminder_time"])
            print(f"     - {reminder['reminder_type']} at {reminder_time}")
        
        # Cancel the reminders we just scheduled (cleanup)
        cancelled = await cancel_appointment_reminders(
            patient_phone=TEST_PHONE,
            appointment_time=TEST_APPOINTMENT_TIME,
        )
        print(f"   Cancelled {cancelled} test reminders (cleanup)")
    
    else:
        print("❌ Failed to schedule reminders")
    
    return success


# ============================================================================
# RUN ALL TESTS
# ============================================================================

async def run_all_tests():
    """Run all SMS tests"""
    print("\n" + "=" * 50)
    print("   THEOREM HEALTH SMS SYSTEM TESTS")
    print("=" * 50)
    print(f"\nTest phone number: {TEST_PHONE}")
    print(f"Make sure this is YOUR number!")
    print("\nRunning tests in 3 seconds...")
    print("(Ctrl+C to cancel)")
    
    await asyncio.sleep(3)
    
    results = []
    
    # Run each test with a pause between
    results.append(("Booking Confirmation", await test_booking_confirmation()))
    await asyncio.sleep(2)
    
    results.append(("24hr Reminder", await test_24hr_reminder()))
    await asyncio.sleep(2)
    
    results.append(("Same-Day Reminder", await test_same_day_reminder()))
    await asyncio.sleep(2)
    
    results.append(("Insurance Booking", await test_insurance_booking()))
    await asyncio.sleep(2)
    
    results.append(("New Patient Welcome", await test_new_patient_welcome()))
    await asyncio.sleep(2)
    
    results.append(("Cancellation", await test_cancellation()))
    await asyncio.sleep(2)
    
    results.append(("Late Cancellation", await test_late_cancellation()))
    await asyncio.sleep(2)
    
    results.append(("Reschedule", await test_reschedule()))
    await asyncio.sleep(2)
    
    results.append(("Callback Confirmation", await test_callback_confirmation()))
    await asyncio.sleep(2)
    
    results.append(("Insurance Receipt", await test_insurance_receipt()))
    await asyncio.sleep(2)
    
    results.append(("Scheduler", await test_scheduler()))
    
    # Summary
    print("\n" + "=" * 50)
    print("   TEST SUMMARY")
    print("=" * 50)
    
    passed = sum(1 for _, success in results if success)
    total = len(results)
    
    for test_name, success in results:
        status = "✅ PASS" if success else "❌ FAIL"
        print(f"{status} - {test_name}")
    
    print("\n" + "=" * 50)
    print(f"   {passed}/{total} tests passed")
    print("=" * 50)
    
    if passed == total:
        print("\n🎉 ALL TESTS PASSED! SMS system is working perfectly.")
    else:
        print(f"\n⚠️  {total - passed} test(s) failed. Check the output above.")


async def run_single_test():
    """Run a single quick test"""
    print("\n📱 Quick SMS Test")
    print("=" * 50)
    print(f"Sending test booking confirmation to: {TEST_PHONE}")
    
    success = await send_booking_confirmation(
        patient_phone=TEST_PHONE,
        patient_name=TEST_PATIENT,
        appointment_time=TEST_APPOINTMENT_TIME,
        location=TEST_LOCATION,
        service="physiotherapy",
        practitioner="Mark",
    )
    
    if success:
        print("\n✅ SMS sent successfully!")
        print("Check your phone for the message.")
    else:
        print("\n❌ Failed to send SMS")
        print("Check your Twilio credentials in .env file")


# ============================================================================
# MAIN
# ============================================================================

if __name__ == "__main__":
    print("\nTHEOREM HEALTH SMS TESTING")
    print("=" * 50)
    print("\nChoose test mode:")
    print("1. Quick test (single SMS)")
    print("2. Full test suite (all SMS types)")
    print("3. Exit")
    
    choice = input("\nEnter choice (1-3): ").strip()
    
    if choice == "1":
        asyncio.run(run_single_test())
    elif choice == "2":
        asyncio.run(run_all_tests())
    elif choice == "3":
        print("Exiting...")
    else:
        print("Invalid choice. Exiting...")
