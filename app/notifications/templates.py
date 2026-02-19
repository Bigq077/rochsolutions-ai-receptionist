# test_sms_system.py
"""
Test script for Theorem Health Smart SMS Router System.
Tests all 10 SMS templates with realistic call scenarios.
"""

import asyncio
import os
from datetime import datetime, timedelta
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Import SMS functions
from app.tools.call_summary import build_call_summary

def format_booking_confirmation(
    patient_name: str,
    appointment_time: datetime,
    location: str,
    service: str = "physiotherapy",
    practitioner: Optional[str] = None,
    is_new_patient: bool = False,
    has_insurance: bool = False,
    insurer: Optional[str] = None,
) -> str:
    """
    Booking confirmation SMS - called by booking_sms.py
    Kept for backwards compatibility.
    """
    day_name = appointment_time.strftime("%A")
    day_num = appointment_time.strftime("%d").lstrip("0")
    month = appointment_time.strftime("%b")
    time_str = appointment_time.strftime("%I:%M%p").lstrip("0").lower()
    
    message = f"Hi {patient_name}, your {service} appointment is confirmed for {day_name} {day_num} {month} at {time_str} at our {location} clinic"
    
    if practitioner:
        message += f" with {practitioner}"
    
    message += ". We look forward to seeing you! Theorem Health - 07870 166861"
    
    return message
# ============================================================================
# TEST CONFIGURATION
# ============================================================================

# YOUR TEST PHONE NUMBER - UPDATE THIS!
TEST_PHONE = "+447870166861"  # ← Change to your UK mobile number

# Test patient details
TEST_PATIENT_NAME = "Sarah"
TEST_APPOINTMENT_TIME = datetime.now() + timedelta(days=2, hours=14)


# ============================================================================
# HELPER FUNCTION - CREATE TEST SESSION
# ============================================================================

def create_test_session(
    outcome: str,
    phone: str = TEST_PHONE,
    name: str = TEST_PATIENT_NAME,
    reason: str = "",
    insurer: str = "",
    handoff_reason: str = "",
    faq_questions: list = None,
    duration: int = 60,
):
    """Create a realistic test session for different scenarios."""
    
    session = {
        "call_sid": f"TEST_{outcome}_{datetime.now().timestamp()}",
        "session_id": "test_session",
        "state": "TRIAGE",
        "selected_location": "alcester",
        "collected": {
            "name": name,
            "phone": phone,
            "patient_type": "NEW",
        },
        "turns": [],
        "call_status": "completed",
        "ended_at_utc": datetime.utcnow().isoformat() + "Z",
        "twilio_duration_sec": str(duration),
    }
    
    if reason:
        session["collected"]["reason"] = reason
    
    if insurer:
        session["collected"]["insurer"] = insurer
    
    if faq_questions:
        session["turns"] = [
            {"user": q, "assistant": "Here's the answer..."} 
            for q in faq_questions
        ]
    
    # Create summary from session
    summary = build_call_summary(session)
    
    # Override outcome if needed
    summary["outcome"] = outcome
    
    # Add handoff data if needed
    if handoff_reason:
        summary["handoff"] = {
            "manual_followup_needed": True,
            "reason": handoff_reason,
        }
    
    return session, summary


# ============================================================================
# TEST 1: ABANDONED BOOKING
# ============================================================================

async def test_abandoned_booking():
    """Test: Person started booking but didn't finish."""
    print("\n🧪 TEST 1: Abandoned Booking")
    print("=" * 60)
    print("Scenario: Patient gave name, phone, and reason but hung up")
    print("Expected SMS: Encourages them to complete booking")
    
    session, summary = create_test_session(
        outcome="abandoned",
        reason="lower back pain",
        duration=90,
    )
    
    success = await send_smart_followup_sms(session, summary)
    
    if success:
        print("✅ SMS sent successfully!")
        print(f"   Message type: Abandoned booking")
        print(f"   Sent to: {TEST_PHONE}")
        print(f"   Expected: 'Hi Sarah, thanks for calling about lower back pain...'")
    else:
        print("❌ Failed to send SMS")
    
    return success


# ============================================================================
# TEST 2: PRICE INQUIRY
# ============================================================================

async def test_price_inquiry():
    """Test: Person asked about pricing."""
    print("\n🧪 TEST 2: Price Inquiry")
    print("=" * 60)
    print("Scenario: Patient asked 'How much does a session cost?'")
    print("Expected SMS: Provides pricing info (£75/50min)")
    
    session, summary = create_test_session(
        outcome="faq_only",
        faq_questions=[
            "How much does a physiotherapy session cost?",
            "What are your prices?",
        ],
        duration=45,
    )
    
    # Add FAQ data to summary
    summary["faq"] = [
        {"question": "How much does a physiotherapy session cost?"},
        {"question": "What are your prices?"},
    ]
    
    success = await send_smart_followup_sms(session, summary)
    
    if success:
        print("✅ SMS sent successfully!")
        print(f"   Message type: Price inquiry")
        print(f"   Sent to: {TEST_PHONE}")
        print(f"   Expected: '...£75 for 50 minutes...'")
    else:
        print("❌ Failed to send SMS")
    
    return success


# ============================================================================
# TEST 3: INSURANCE INQUIRY (GENERAL)
# ============================================================================

async def test_insurance_inquiry():
    """Test: Person asked about insurance."""
    print("\n🧪 TEST 3: Insurance Inquiry (AXA)")
    print("=" * 60)
    print("Scenario: Patient asked about using AXA Health insurance")
    print("Expected SMS: Explains self-pay then claim back")
    
    session, summary = create_test_session(
        outcome="faq_only",
        insurer="AXA Health",
        faq_questions=["Do you accept AXA Health insurance?"],
        duration=50,
    )
    
    summary["insurance"] = {"insurer_name": "AXA Health"}
    summary["faq"] = [{"question": "Do you accept AXA Health insurance?"}]
    
    success = await send_smart_followup_sms(session, summary)
    
    if success:
        print("✅ SMS sent successfully!")
        print(f"   Message type: Insurance inquiry (AXA)")
        print(f"   Sent to: {TEST_PHONE}")
        print(f"   Expected: '...self-pay then claim back from AXA Health...'")
    else:
        print("❌ Failed to send SMS")
    
    return success


# ============================================================================
# TEST 4: BUPA INQUIRY (REJECTED)
# ============================================================================

async def test_bupa_inquiry():
    """Test: Person asked about Bupa (which we don't accept)."""
    print("\n🧪 TEST 4: Bupa Inquiry (Not Accepted)")
    print("=" * 60)
    print("Scenario: Patient asked about Bupa insurance")
    print("Expected SMS: Politely explains we don't accept Bupa")
    
    session, summary = create_test_session(
        outcome="faq_only",
        insurer="Bupa",
        faq_questions=["Do you accept Bupa?"],
        duration=40,
    )
    
    summary["insurance"] = {"insurer_name": "Bupa"}
    summary["faq"] = [{"question": "Do you accept Bupa?"}]
    
    success = await send_smart_followup_sms(session, summary)
    
    if success:
        print("✅ SMS sent successfully!")
        print(f"   Message type: Bupa rejection")
        print(f"   Sent to: {TEST_PHONE}")
        print(f"   Expected: '...unfortunately we don't accept Bupa directly...'")
    else:
        print("❌ Failed to send SMS")
    
    return success


# ============================================================================
# TEST 5: GENERAL INFO ONLY
# ============================================================================

async def test_general_info():
    """Test: Person just asked general questions."""
    print("\n🧪 TEST 5: General Info Questions")
    print("=" * 60)
    print("Scenario: Patient asked about hours and location")
    print("Expected SMS: Thank you with general info")
    
    session, summary = create_test_session(
        outcome="faq_only",
        faq_questions=[
            "What are your opening hours?",
            "Where are you located?",
        ],
        duration=35,
    )
    
    summary["faq"] = [
        {"question": "What are your opening hours?"},
        {"question": "Where are you located?"},
    ]
    
    success = await send_smart_followup_sms(session, summary)
    
    if success:
        print("✅ SMS sent successfully!")
        print(f"   Message type: General info")
        print(f"   Sent to: {TEST_PHONE}")
        print(f"   Expected: '...glad we could help with your questions...'")
    else:
        print("❌ Failed to send SMS")
    
    return success


# ============================================================================
# TEST 6: NO SUITABLE TIME
# ============================================================================

async def test_no_suitable_time():
    """Test: Couldn't find appointment time that worked."""
    print("\n🧪 TEST 6: No Suitable Time Found")
    print("=" * 60)
    print("Scenario: Patient wanted to book but no suitable times")
    print("Expected SMS: Offers callback to find alternative")
    
    session, summary = create_test_session(
        outcome="manual_followup",
        reason="knee pain",
        handoff_reason="couldn't find suitable time slot",
        duration=120,
    )
    
    success = await send_smart_followup_sms(session, summary)
    
    if success:
        print("✅ SMS sent successfully!")
        print(f"   Message type: No suitable time")
        print(f"   Sent to: {TEST_PHONE}")
        print(f"   Expected: '...sorry we couldn't find a suitable time...'")
    else:
        print("❌ Failed to send SMS")
    
    return success


# ============================================================================
# TEST 7: RESCHEDULE REQUEST
# ============================================================================

async def test_reschedule_request():
    """Test: Person wants to reschedule appointment."""
    print("\n🧪 TEST 7: Reschedule Request")
    print("=" * 60)
    print("Scenario: Patient called to reschedule existing appointment")
    print("Expected SMS: Confirms we'll help reschedule")
    
    session, summary = create_test_session(
        outcome="manual_followup",
        handoff_reason="wants to reschedule appointment",
        duration=60,
    )
    
    success = await send_smart_followup_sms(session, summary)
    
    if success:
        print("✅ SMS sent successfully!")
        print(f"   Message type: Reschedule request")
        print(f"   Sent to: {TEST_PHONE}")
        print(f"   Expected: '...thanks for calling about rescheduling...'")
    else:
        print("❌ Failed to send SMS")
    
    return success


# ============================================================================
# TEST 8: CANCELLATION REQUEST
# ============================================================================

async def test_cancellation_request():
    """Test: Person wants to cancel appointment."""
    print("\n🧪 TEST 8: Cancellation Request")
    print("=" * 60)
    print("Scenario: Patient called to cancel appointment")
    print("Expected SMS: Asks them to call to confirm")
    
    session, summary = create_test_session(
        outcome="manual_followup",
        handoff_reason="wants to cancel appointment",
        duration=45,
    )
    
    success = await send_smart_followup_sms(session, summary)
    
    if success:
        print("✅ SMS sent successfully!")
        print(f"   Message type: Cancellation request")
        print(f"   Sent to: {TEST_PHONE}")
        print(f"   Expected: '...thanks for calling about cancelling...'")
    else:
        print("❌ Failed to send SMS")
    
    return success


# ============================================================================
# TEST 9: TECHNICAL ISSUE
# ============================================================================

async def test_technical_issue():
    """Test: Call had technical problems."""
    print("\n🧪 TEST 9: Technical Issue / Failed Call")
    print("=" * 60)
    print("Scenario: Call dropped due to technical issue")
    print("Expected SMS: Apologizes and asks them to call back")
    
    session, summary = create_test_session(
        outcome="failed",
        duration=30,
    )
    
    success = await send_smart_followup_sms(session, summary)
    
    if success:
        print("✅ SMS sent successfully!")
        print(f"   Message type: Technical issue")
        print(f"   Sent to: {TEST_PHONE}")
        print(f"   Expected: '...sorry - we had a technical issue...'")
    else:
        print("❌ Failed to send SMS")
    
    return success


# ============================================================================
# TEST 10: GENERAL CALLBACK
# ============================================================================

async def test_general_callback():
    """Test: Generic callback needed."""
    print("\n🧪 TEST 10: General Callback Request")
    print("=" * 60)
    print("Scenario: Patient needs callback for unspecified reason")
    print("Expected SMS: Confirms callback will happen")
    
    session, summary = create_test_session(
        outcome="manual_followup",
        handoff_reason="needs callback to discuss options",
        duration=75,
    )
    
    success = await send_smart_followup_sms(session, summary)
    
    if success:
        print("✅ SMS sent successfully!")
        print(f"   Message type: General callback")
        print(f"   Sent to: {TEST_PHONE}")
        print(f"   Expected: '...thanks for your message. One of our team...'")
    else:
        print("❌ Failed to send SMS")
    
    return success


# ============================================================================
# TEST 11: SKIP - NO PHONE
# ============================================================================

async def test_no_phone_skip():
    """Test: Should skip SMS when no phone collected."""
    print("\n🧪 TEST 11: No Phone Collected (Should Skip)")
    print("=" * 60)
    print("Scenario: Patient didn't provide phone number")
    print("Expected: No SMS sent (returns False)")
    
    session, summary = create_test_session(
        outcome="abandoned",
        phone="",  # No phone
        duration=60,
    )
    
    session["collected"]["phone"] = ""  # Clear phone
    
    success = await send_smart_followup_sms(session, summary)
    
    if not success:  # Should return False
        print("✅ Correctly skipped SMS (no phone number)")
        print(f"   Expected behavior: Don't send if no phone")
        return True
    else:
        print("❌ Incorrectly sent SMS without phone number")
        return False


# ============================================================================
# TEST 12: SKIP - TOO SHORT
# ============================================================================

async def test_too_short_skip():
    """Test: Should skip SMS for very short calls."""
    print("\n🧪 TEST 12: Call Too Short (Should Skip)")
    print("=" * 60)
    print("Scenario: Call lasted only 8 seconds (wrong number)")
    print("Expected: No SMS sent (returns False)")
    
    session, summary = create_test_session(
        outcome="abandoned",
        duration=8,  # Very short
    )
    
    success = await send_smart_followup_sms(session, summary)
    
    if not success:  # Should return False
        print("✅ Correctly skipped SMS (call too short)")
        print(f"   Expected behavior: Don't send for <15s calls")
        return True
    else:
        print("❌ Incorrectly sent SMS for short call")
        return False


# ============================================================================
# RUN ALL TESTS
# ============================================================================

async def run_all_tests():
    """Run all SMS router tests."""
    print("\n" + "=" * 60)
    print("   THEOREM HEALTH SMART SMS ROUTER TESTS")
    print("=" * 60)
    print(f"\nTest phone number: {TEST_PHONE}")
    print(f"⚠️  IMPORTANT: Make sure this is YOUR number!")
    print("\nThis will send ~10 SMS messages to test all scenarios.")
    print("Each test has a 3-second delay between sends.")
    print("\nStarting in 5 seconds...")
    print("(Ctrl+C to cancel)")
    
    await asyncio.sleep(5)
    
    results = []
    
    # Run each test with pause
    results.append(("Abandoned Booking", await test_abandoned_booking()))
    await asyncio.sleep(3)
    
    results.append(("Price Inquiry", await test_price_inquiry()))
    await asyncio.sleep(3)
    
    results.append(("Insurance (AXA)", await test_insurance_inquiry()))
    await asyncio.sleep(3)
    
    results.append(("Bupa Rejection", await test_bupa_inquiry()))
    await asyncio.sleep(3)
    
    results.append(("General Info", await test_general_info()))
    await asyncio.sleep(3)
    
    results.append(("No Suitable Time", await test_no_suitable_time()))
    await asyncio.sleep(3)
    
    results.append(("Reschedule Request", await test_reschedule_request()))
    await asyncio.sleep(3)
    
    results.append(("Cancellation Request", await test_cancellation_request()))
    await asyncio.sleep(3)
    
    results.append(("Technical Issue", await test_technical_issue()))
    await asyncio.sleep(3)
    
    results.append(("General Callback", await test_general_callback()))
    await asyncio.sleep(3)
    
    results.append(("Skip: No Phone", await test_no_phone_skip()))
    await asyncio.sleep(2)
    
    results.append(("Skip: Too Short", await test_too_short_skip()))
    
    # Summary
    print("\n" + "=" * 60)
    print("   TEST SUMMARY")
    print("=" * 60)
    
    passed = sum(1 for _, success in results if success)
    total = len(results)
    
    for test_name, success in results:
        status = "✅ PASS" if success else "❌ FAIL"
        print(f"{status} - {test_name}")
    
    print("\n" + "=" * 60)
    print(f"   {passed}/{total} tests passed")
    print("=" * 60)
    
    if passed == total:
        print("\n🎉 ALL TESTS PASSED! Smart SMS Router working perfectly.")
        print(f"\n📱 Check {TEST_PHONE} for {passed - 2} SMS messages!")
        print("   (2 tests correctly skipped - no phone & too short)")
    else:
        print(f"\n⚠️  {total - passed} test(s) failed. Check output above.")


async def run_single_test():
    """Run a single quick test."""
    print("\n📱 Quick SMS Router Test")
    print("=" * 60)
    print(f"Sending abandoned booking SMS to: {TEST_PHONE}")
    
    session, summary = create_test_session(
        outcome="abandoned",
        reason="lower back pain",
        duration=90,
    )
    
    success = await send_smart_followup_sms(session, summary)
    
    if success:
        print("\n✅ SMS sent successfully!")
        print("Check your phone for abandoned booking message.")
        print("Expected: 'Hi Sarah, thanks for calling about lower back pain...'")
    else:
        print("\n❌ Failed to send SMS")
        print("Check your Twilio credentials in .env file")


# ============================================================================
# MAIN
# ============================================================================

if __name__ == "__main__":
    print("\nTHEOREM HEALTH SMART SMS ROUTER TESTING")
    print("=" * 60)
    print("\nThis tests the new smart SMS system that sends different")
    print("messages based on call outcome (abandoned, price inquiry, etc.)")
    print("\nChoose test mode:")
    print("1. Quick test (single SMS - abandoned booking)")
    print("2. Full test suite (all 12 scenarios)")
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
