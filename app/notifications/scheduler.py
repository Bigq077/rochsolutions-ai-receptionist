# app/notifications/scheduler.py
"""
Appointment reminder scheduler.
Handles scheduling and sending of 24-hour and same-day reminders.

IMPORTANT: Redis is optional. If Redis is not available, scheduler
functions will gracefully fail without crashing the app.
"""

import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
import asyncio
import json
import os

logger = logging.getLogger(__name__)


# ============================================================================
# REDIS CONNECTION (OPTIONAL)
# ============================================================================

REDIS_AVAILABLE = False
redis_client = None

try:
    from app.storage.redis_store import redis_client as _redis_client
    
    # Test Redis connection
    if _redis_client:
        try:
            _redis_client.ping()
            redis_client = _redis_client
            REDIS_AVAILABLE = True
            logger.info("✅ Redis connected - scheduler enabled")
        except Exception as e:
            logger.warning(f"⚠️ Redis not available - scheduler disabled: {e}")
except ImportError:
    logger.warning("⚠️ Redis module not available - scheduler disabled")


# ============================================================================
# REDIS KEYS
# ============================================================================

REMINDERS_KEY_PREFIX = "reminder:"
PENDING_REMINDERS_SET = "pending_reminders"


# ============================================================================
# SCHEDULE REMINDERS
# ============================================================================

async def schedule_appointment_reminders(
    patient_phone: str,
    patient_name: str,
    appointment_time: datetime,
    location: str,
    is_new_patient: bool = False,
    has_insurance: bool = False,
    insurer: Optional[str] = None,
) -> bool:
    """
    Schedule all appointment reminders (24hr and same-day).
    
    If Redis is not available, this will log a warning and return False
    but won't crash the application.
    
    Args:
        patient_phone: Patient's mobile number
        patient_name: Patient's first name
        appointment_time: Appointment datetime
        location: Location name
        is_new_patient: True if first visit
        has_insurance: True if patient has insurance
        insurer: Insurance company name
    
    Returns:
        True if scheduled successfully, False if Redis unavailable
    """
    if not REDIS_AVAILABLE:
        logger.warning(
            "Cannot schedule reminders - Redis not available. "
            "Booking will succeed but reminders won't be sent automatically."
        )
        return False
    
    try:
        # Calculate reminder times
        reminder_24hr = appointment_time - timedelta(hours=24)
        reminder_2hr = appointment_time - timedelta(hours=2)
        
        now = datetime.utcnow()
        
        # Only schedule future reminders
        reminders_scheduled = []
        
        # Schedule 24-hour reminder
        if reminder_24hr > now:
            reminder_id_24hr = await _schedule_reminder(
                reminder_time=reminder_24hr,
                reminder_type="24hr",
                patient_phone=patient_phone,
                patient_name=patient_name,
                appointment_time=appointment_time,
                location=location,
                is_new_patient=is_new_patient,
                has_insurance=has_insurance,
                insurer=insurer,
            )
            reminders_scheduled.append(reminder_id_24hr)
            logger.info(f"24hr reminder scheduled for {reminder_24hr}")
        
        # Schedule 2-hour reminder
        if reminder_2hr > now:
            reminder_id_2hr = await _schedule_reminder(
                reminder_time=reminder_2hr,
                reminder_type="2hr",
                patient_phone=patient_phone,
                patient_name=patient_name,
                appointment_time=appointment_time,
                location=location,
                is_new_patient=False,
                has_insurance=False,
                insurer=None,
            )
            reminders_scheduled.append(reminder_id_2hr)
            logger.info(f"2hr reminder scheduled for {reminder_2hr}")
        
        logger.info(
            f"Scheduled {len(reminders_scheduled)} reminders for {patient_phone}",
            extra={
                "patient_name": patient_name,
                "appointment_time": appointment_time.isoformat(),
                "reminders": reminders_scheduled,
            }
        )
        
        return True
    
    except Exception as e:
        logger.error(f"Failed to schedule reminders: {e}", exc_info=True)
        return False


async def _schedule_reminder(
    reminder_time: datetime,
    reminder_type: str,
    patient_phone: str,
    patient_name: str,
    appointment_time: datetime,
    location: str,
    is_new_patient: bool = False,
    has_insurance: bool = False,
    insurer: Optional[str] = None,
) -> str:
    """Schedule a single reminder in Redis."""
    if not REDIS_AVAILABLE or not redis_client:
        raise Exception("Redis not available")
    
    reminder_id = f"reminder:{patient_phone}:{appointment_time.isoformat()}:{reminder_type}"
    
    reminder_data = {
        "reminder_id": reminder_id,
        "reminder_type": reminder_type,
        "reminder_time": reminder_time.isoformat(),
        "patient_phone": patient_phone,
        "patient_name": patient_name,
        "appointment_time": appointment_time.isoformat(),
        "location": location,
        "is_new_patient": is_new_patient,
        "has_insurance": has_insurance,
        "insurer": insurer,
        "status": "pending",
        "created_at": datetime.utcnow().isoformat(),
    }
    
    redis_client.setex(
        name=reminder_id,
        time=60 * 60 * 24 * 7,
        value=json.dumps(reminder_data),
    )
    
    redis_client.zadd(
        PENDING_REMINDERS_SET,
        {reminder_id: reminder_time.timestamp()}
    )
    
    return reminder_id


# ============================================================================
# CANCEL REMINDERS
# ============================================================================

async def cancel_appointment_reminders(
    patient_phone: str,
    appointment_time: datetime,
) -> int:
    """Cancel all reminders for a specific appointment."""
    if not REDIS_AVAILABLE or not redis_client:
        logger.warning("Cannot cancel reminders - Redis not available")
        return 0
    
    try:
        pattern = f"reminder:{patient_phone}:{appointment_time.isoformat()}:*"
        keys = redis_client.keys(pattern)
        
        if not keys:
            return 0
        
        redis_client.delete(*keys)
        redis_client.zrem(PENDING_REMINDERS_SET, *keys)
        
        logger.info(f"Cancelled {len(keys)} reminders for {patient_phone}")
        return len(keys)
    
    except Exception as e:
        logger.error(f"Failed to cancel reminders: {e}", exc_info=True)
        return 0


# ============================================================================
# PROCESS REMINDERS (Background Worker)
# ============================================================================

async def process_due_reminders() -> int:
    """Process all reminders that are due to be sent."""
    if not REDIS_AVAILABLE or not redis_client:
        return 0
    
    try:
        now = datetime.utcnow()
        now_timestamp = now.timestamp()
        
        due_reminder_ids = redis_client.zrangebyscore(
            PENDING_REMINDERS_SET,
            min=0,
            max=now_timestamp,
        )
        
        if not due_reminder_ids:
            return 0
        
        processed = 0
        
        for reminder_id in due_reminder_ids:
            try:
                reminder_data_json = redis_client.get(reminder_id)
                
                if not reminder_data_json:
                    redis_client.zrem(PENDING_REMINDERS_SET, reminder_id)
                    continue
                
                reminder_data = json.loads(reminder_data_json)
                await _send_reminder(reminder_data)
                
                reminder_data["status"] = "sent"
                reminder_data["sent_at"] = datetime.utcnow().isoformat()
                redis_client.setex(
                    name=reminder_id,
                    time=60 * 60 * 24 * 7,
                    value=json.dumps(reminder_data),
                )
                
                redis_client.zrem(PENDING_REMINDERS_SET, reminder_id)
                processed += 1
                
            except Exception as e:
                logger.error(f"Failed to process reminder {reminder_id}: {e}", exc_info=True)
                continue
        
        if processed > 0:
            logger.info(f"Processed {processed} due reminders")
        
        return processed
    
    except Exception as e:
        logger.error(f"Failed to process due reminders: {e}", exc_info=True)
        return 0


async def _send_reminder(reminder_data: Dict[str, Any]) -> bool:
    """Send a single reminder SMS."""
    from app.notifications.booking_sms import (
        send_24hr_reminder,
        send_same_day_reminder,
    )
    
    reminder_type = reminder_data["reminder_type"]
    patient_phone = reminder_data["patient_phone"]
    patient_name = reminder_data["patient_name"]
    appointment_time = datetime.fromisoformat(reminder_data["appointment_time"])
    location = reminder_data["location"]
    is_new_patient = reminder_data.get("is_new_patient", False)
    has_insurance = reminder_data.get("has_insurance", False)
    insurer = reminder_data.get("insurer")
    
    try:
        if reminder_type == "24hr":
            success = await send_24hr_reminder(
                patient_phone=patient_phone,
                patient_name=patient_name,
                appointment_time=appointment_time,
                location=location,
                is_new_patient=is_new_patient,
                has_insurance=has_insurance,
                insurer=insurer,
            )
        elif reminder_type == "2hr":
            success = await send_same_day_reminder(
                patient_phone=patient_phone,
                patient_name=patient_name,
                appointment_time=appointment_time,
                location=location,
            )
        else:
            logger.error(f"Unknown reminder type: {reminder_type}")
            return False
        
        return success
    
    except Exception as e:
        logger.error(f"Failed to send reminder: {e}", exc_info=True)
        return False


# ============================================================================
# BACKGROUND WORKER (OPTIONAL)
# ============================================================================

async def start_reminder_worker(interval_seconds: int = 300):
    """Start background worker - only if Redis is available."""
    if not REDIS_AVAILABLE:
        logger.info("Reminder worker not started - Redis not available")
        return
    
    logger.info(f"Starting reminder worker (checking every {interval_seconds}s)")
    
    while True:
        try:
            processed = await process_due_reminders()
            if processed > 0:
                logger.info(f"Reminder worker processed {processed} reminders")
        except Exception as e:
            logger.error(f"Reminder worker error: {e}", exc_info=True)
        
        await asyncio.sleep(interval_seconds)


# ============================================================================
# ADMIN FUNCTIONS
# ============================================================================

async def get_pending_reminders_count() -> int:
    """Get count of pending reminders."""
    if not REDIS_AVAILABLE or not redis_client:
        return 0
    return redis_client.zcard(PENDING_REMINDERS_SET)


async def get_upcoming_reminders(limit: int = 10) -> list:
    """Get upcoming reminders for monitoring."""
    if not REDIS_AVAILABLE or not redis_client:
        return []
    
    try:
        reminder_ids = redis_client.zrange(PENDING_REMINDERS_SET, 0, limit - 1)
        reminders = []
        for reminder_id in reminder_ids:
            reminder_data_json = redis_client.get(reminder_id)
            if reminder_data_json:
                reminders.append(json.loads(reminder_data_json))
        return reminders
    except Exception as e:
        logger.error(f"Failed to get upcoming reminders: {e}", exc_info=True)
        return []


async def send_test_reminder(
    patient_phone: str,
    patient_name: str = "Test Patient",
) -> bool:
    """Send a test reminder immediately."""
    from app.notifications.booking_sms import send_24hr_reminder
    
    appointment_time = datetime.now() + timedelta(days=1)
    
    return await send_24hr_reminder(
        patient_phone=patient_phone,
        patient_name=patient_name,
        appointment_time=appointment_time,
        location="Alcester",
        is_new_patient=True,
        has_insurance=False,
    )
