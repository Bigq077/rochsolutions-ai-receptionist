"""
Test SMS sending functionality.
"""

import asyncio
import os
from dotenv import load_dotenv
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.notifications.sms import SMSService
from app.notifications.templates import (
    booking_confirmation_sms,
    appointment_reminder_sms,
    insurance_followup_sms,
)

load_dotenv()


async def test_sms_service():
    """Test SMS service with real Twilio account."""
    
    # Your test phone number (use your own number for testing!)
    TEST_PHONE = "+447502211207"  # Replace with YOUR number
    
    print("🧪 Testing SMS Service...\n")
    
    # Initialize service
    try:
        sms_service = SMSService()
        print("✅ SMS service initialized")
    except ValueError as e:
        print(f"❌ Error: {e}")
        print("\nMake sure these are in your .env file:")
        print("  TWILIO_ACCOUNT_SID=ACxxxx")
        print("  TWILIO_AUTH_TOKEN=your_token")
        print("  TWILIO_PHONE_NUMBER=+44xxxx")
        return
    
    # Test 1: Simple message
    print("\n📱 Test 1: Sending simple test message...")
    message_sid = await sms_service.send_sms(
        to=TEST_PHONE,
        message="Test from Theorem Health booking system. This is a test message."
    )
    
    if message_sid:
        print(f"✅ Message sent! SID: {message_sid}")
    else:
        print("❌ Failed to send message")
        return
    
    # Wait a moment
    await asyncio.sleep(2)
    
    # Test 2: Booking confirmation
    print("\n📱 Test 2: Sending booking confirmation...")
    appointment_time = datetime.now(ZoneInfo("Europe/London")) + timedelta(days=3, hours=2)
    confirmation = booking_confirmation_sms(
        patient_first_name="John",
        appointment_date=appointment_time,
        location_name="Alcester",
        practitioner_name="Mark",
    )
    
    print(f"Message preview:\n  {confirmation}\n")
    
    message_sid = await sms_service.send_sms(
        to=TEST_PHONE,
        message=confirmation,
    )
    
    if message_sid:
        print(f"✅ Booking confirmation sent! SID: {message_sid}")
    else:
        print("❌ Failed to send booking confirmation")
    
    # Wait a moment
    await asyncio.sleep(2)
    
    # Test 3: Reminder
    print("\n📱 Test 3: Sending appointment reminder...")
    reminder = appointment_reminder_sms(
        patient_first_name="John",
        appointment_date=appointment_time,
        location_name="Alcester",
    )
    
    print(f"Message preview:\n  {reminder}\n")
    
    message_sid = await sms_service.send_sms(
        to=TEST_PHONE,
        message=reminder,
    )
    
    if message_sid:
        print(f"✅ Reminder sent! SID: {message_sid}")
    else:
        print("❌ Failed to send reminder")
    
    # Wait a moment
    await asyncio.sleep(2)
    
    # Test 4: Insurance follow-up
    print("\n📱 Test 4: Sending insurance follow-up...")
    insurance_msg = insurance_followup_sms(
        patient_first_name="John",
        insurance_provider="Aviva",
    )
    
    print(f"Message preview:\n  {insurance_msg}\n")
    
    message_sid = await sms_service.send_sms(
        to=TEST_PHONE,
        message=insurance_msg,
    )
    
    if message_sid:
        print(f"✅ Insurance follow-up sent! SID: {message_sid}")
    else:
        print("❌ Failed to send insurance follow-up")
    
    print("\n✅ All SMS tests completed!")
    print(f"\n📱 Check your phone ({TEST_PHONE}) for 4 test messages")


if __name__ == "__main__":
    asyncio.run(test_sms_service())
