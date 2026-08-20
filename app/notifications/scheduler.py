# app/notifications/scheduler.py
"""
Appointment reminder scheduler.
Handles scheduling and sending of 24-hour and same-day reminders.

IMPORTANT: Redis is optional. If Redis is not available, scheduler
functions will gracefully fail without crashing the app.
"""

import logging
import re
from datetime import datetime, timedelta, timezone
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

    # redis_client is an async (redis.asyncio) client — can't ping synchronously.
    # Presence of the object is sufficient; connection errors surface at first await.
    if _redis_client:
        redis_client = _redis_client
        REDIS_AVAILABLE = True
        logger.info("✅ Redis client available - scheduler enabled")
    else:
        logger.warning("⚠️ Redis client is None - scheduler disabled")
except ImportError:
    logger.warning("⚠️ Redis module not available - scheduler disabled")


# ============================================================================
# REDIS KEYS
# ============================================================================

REMINDERS_KEY_PREFIX = "reminder:"
PENDING_REMINDERS_SET = "pending_reminders"
PENDING_NAME_REMINDERS_SET = "pending_name_reminders"
PENDING_ADDRESS_REMINDERS_SET = "pending_address_reminders"


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
    clinic_name: Optional[str] = None,
    clinic_phone: Optional[str] = None,
    from_number: Optional[str] = None,
    appointment_noun: Optional[str] = None,
    clinic_id: Optional[str] = None,
    confirm_gate: Optional[Dict[str, Any]] = None,
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
        # appointment_time may be tz-aware (Acuity → Europe/London) or naive
        # (legacy paths). Normalise both sides to UTC-aware before comparing so
        # we never hit "can't compare offset-naive and offset-aware", which would
        # be swallowed by the except below and silently abort all scheduling.
        _appt = appointment_time
        if _appt.tzinfo is None:
            _appt = _appt.replace(tzinfo=timezone.utc)

        # Calculate reminder times
        reminder_24hr = _appt - timedelta(hours=24)
        reminder_2hr = _appt - timedelta(hours=2)

        now = datetime.now(timezone.utc)

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
                clinic_name=clinic_name,
                clinic_phone=clinic_phone,
                from_number=from_number,
                appointment_noun=appointment_noun,
                clinic_id=clinic_id,
                confirm_gate=confirm_gate,
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
                clinic_name=clinic_name,
                clinic_phone=clinic_phone,
                from_number=from_number,
                appointment_noun=appointment_noun,
                clinic_id=clinic_id,
                confirm_gate=confirm_gate,
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
    clinic_name: Optional[str] = None,
    clinic_phone: Optional[str] = None,
    from_number: Optional[str] = None,
    appointment_noun: Optional[str] = None,
    clinic_id: Optional[str] = None,
    confirm_gate: Optional[Dict[str, Any]] = None,
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
        "clinic_name": clinic_name,
        "clinic_phone": clinic_phone,
        "from_number": from_number or "",
        # Modality noun for the SMS body ("physiotherapy", "massage", or "").
        # Resolved from clinic.json at BOOKING time and carried in the payload:
        # the worker that drains this queue may belong to another tenant on a
        # shared Redis and cannot look the booking clinic's config up.
        "appointment_noun": appointment_noun or "",
        "clinic_id": clinic_id or "",
        # Provisional clinics only — {"event_id": ..., "calendar_id": ...}. The
        # sender re-reads that event and stays silent while it is still titled
        # PENDING CONFIRMATION. See _confirmed_enough_to_remind().
        "confirm_gate": confirm_gate or None,
        "status": "pending",
        "created_at": datetime.utcnow().isoformat(),
    }

    # The key must outlive the moment it is due. A flat 7-day TTL silently
    # dropped every reminder for an appointment booked more than 8 days out:
    # the key expired, and process_due_reminders treats a missing key as an
    # orphan and quietly zrem's it. Nobody was ever texted and nothing was
    # logged. Keep the key until a week PAST its due time instead, so the
    # already-sent record is still inspectable afterwards.
    _ttl = int((reminder_time - datetime.now(timezone.utc)).total_seconds()) + 60 * 60 * 24 * 7

    await redis_client.setex(
        name=reminder_id,
        time=max(_ttl, 60 * 60 * 24 * 7),
        value=json.dumps(reminder_data),
    )

    await redis_client.zadd(
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
        keys = await redis_client.keys(pattern)

        if not keys:
            return 0

        await redis_client.delete(*keys)
        await redis_client.zrem(PENDING_REMINDERS_SET, *keys)
        
        logger.info(f"Cancelled {len(keys)} reminders for {patient_phone}")
        return len(keys)
    
    except Exception as e:
        logger.error(f"Failed to cancel reminders: {e}", exc_info=True)
        return 0


def _phone_core(p: str) -> str:
    """
    Reduce a phone number to its comparable UK core so local and E.164 forms
    match: '07502211207' and '+447502211207' both -> '7502211207'.
    Mirrors receptionist_tools._phone_key. Returns '' for empty/garbage.
    """
    d = re.sub(r"\D", "", p or "")
    if d.startswith("44"):
        d = d[2:]
    return d.lstrip("0")


async def cancel_reminders_for_appointment(
    patient_phone: str,
    appointment_time: datetime,
) -> int:
    """
    Cancel pending reminders for ONE specific appointment instant.

    Matches on normalised phone core AND the appointment instant (within 60s),
    comparing datetimes rather than raw isoformat strings. This is robust to:
      - phone-format drift between booking and cancellation (+44 vs 0),
      - timezone-string drift (a stored 'Europe/London' time vs a 'Z' UTC time
        for the same instant).

    Because it targets a single instant, a reschedule's just-created NEW
    reminders (a different time) are preserved while the OLD ones are removed.
    Safe no-op if Redis is unavailable.
    """
    if not REDIS_AVAILABLE or not redis_client:
        return 0

    target_phone = _phone_core(patient_phone)
    if not target_phone or appointment_time is None:
        return 0

    try:
        appt = appointment_time
        if appt.tzinfo is None:
            appt = appt.replace(tzinfo=timezone.utc)
        target_ts = appt.timestamp()

        reminder_ids = await redis_client.zrange(PENDING_REMINDERS_SET, 0, -1)
        removed = 0
        for reminder_id in reminder_ids:
            try:
                data_json = await redis_client.get(reminder_id)
                if not data_json:
                    # Orphaned set entry — clean it up.
                    await redis_client.zrem(PENDING_REMINDERS_SET, reminder_id)
                    continue
                data = json.loads(data_json)
                if _phone_core(data.get("patient_phone", "")) != target_phone:
                    continue
                appt_str = data.get("appointment_time", "")
                if not appt_str:
                    continue
                stored = datetime.fromisoformat(appt_str.replace("Z", "+00:00"))
                if stored.tzinfo is None:
                    stored = stored.replace(tzinfo=timezone.utc)
                if abs(stored.timestamp() - target_ts) <= 60:
                    await redis_client.delete(reminder_id)
                    await redis_client.zrem(PENDING_REMINDERS_SET, reminder_id)
                    removed += 1
            except Exception as _inner:
                logger.warning(
                    "cancel_reminders_for_appointment: skipping %r: %r",
                    reminder_id, _inner,
                )
                continue

        if removed:
            logger.info(
                "Cancelled %d pending reminder(s) for ***%s @ %s",
                removed, target_phone[-4:], appt.isoformat(),
            )
        return removed

    except Exception as e:
        logger.error("cancel_reminders_for_appointment failed: %r", e, exc_info=True)
        return 0


# ============================================================================
# PROCESS REMINDERS (Background Worker)
# ============================================================================

async def process_due_reminders() -> int:
    """Process all reminders that are due to be sent."""
    if not REDIS_AVAILABLE or not redis_client:
        return 0
    
    try:
        # tz-aware UTC so the POSIX timestamp is correct regardless of the
        # server's local timezone (reminder scores are aware-UTC timestamps).
        now = datetime.now(timezone.utc)
        now_timestamp = now.timestamp()

        due_reminder_ids = await redis_client.zrangebyscore(
            PENDING_REMINDERS_SET,
            min=0,
            max=now_timestamp,
        )

        if not due_reminder_ids:
            return 0

        processed = 0

        for reminder_id in due_reminder_ids:
            try:
                reminder_data_json = await redis_client.get(reminder_id)

                if not reminder_data_json:
                    # Was silent. A due entry whose key has vanished means a
                    # reminder nobody received, which is exactly the failure
                    # worth seeing in the Render log rather than inferring.
                    logger.warning(
                        "[reminders] due entry %r has no payload (key expired) "
                        "— dropped WITHOUT sending", reminder_id,
                    )
                    await redis_client.zrem(PENDING_REMINDERS_SET, reminder_id)
                    continue

                reminder_data = json.loads(reminder_data_json)
                outcome = await _send_reminder(reminder_data)

                # Record what actually happened. This used to stamp "sent"
                # unconditionally and drop the entry, so a Twilio rejection, a
                # suppressed send and a real delivery were indistinguishable
                # afterwards — the same false-success the booking confirmation
                # SMS had until 2026-08-18.
                reminder_data["status"] = outcome
                reminder_data["sent_at"] = datetime.utcnow().isoformat()
                await redis_client.setex(
                    name=reminder_id,
                    time=60 * 60 * 24 * 7,
                    value=json.dumps(reminder_data),
                )

                if outcome != "sent":
                    logger.warning(
                        "[reminders] %s reminder for ***%s @ %s was NOT sent (%s)",
                        reminder_data.get("reminder_type"),
                        str(reminder_data.get("patient_phone") or "")[-4:],
                        reminder_data.get("appointment_time"),
                        outcome,
                    )

                await redis_client.zrem(PENDING_REMINDERS_SET, reminder_id)
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


# Provisional bookings are written to the calendar with this title prefix and
# keep it until the practitioner confirms with the client. Same literal the
# booking and cancel paths write in receptionist_tools.
_PENDING_TITLE_PREFIX = "PENDING CONFIRMATION"


async def _confirmed_enough_to_remind(gate: Optional[Dict[str, Any]]) -> bool:
    """
    True when a reminder may go out.

    Only provisional clinics carry a gate. There the booking is a REQUEST: the
    caller is texted "not yet confirmed - {practitioner} will be in touch", and
    the calendar event stays titled "PENDING CONFIRMATION - ..." until he
    confirms. Texting "your appointment is tomorrow at 3pm" for one of those
    would contradict the text they already have and might name a slot that was
    never agreed, so the event is re-read at send time and a still-pending one
    stays silent.

    Fails OPEN on an unreadable calendar (no tokens, API error, event gone).
    The alternative is silently withholding reminders from a clinic whose
    Google auth has lapsed, which looks identical to the feature being off.
    A confirmed appointment reminded about is recoverable; a confirmed
    appointment silently NOT reminded about is the failure this whole change
    exists to fix.
    """
    if not gate:
        return True

    event_id = (gate.get("event_id") or "").strip()
    calendar_id = (gate.get("calendar_id") or "").strip()
    clinic_id = (gate.get("clinic_id") or "").strip()
    if not event_id or not clinic_id:
        return True

    try:
        from app.storage.redis_store import redis_get_json
        from app.tools.calendar_google import get_event, resolve_tokens_key

        # Per-clinic token key (google_tokens:<clinic_id>). Resolved here rather
        # than reusing receptionist_tools._get_tokens to keep the notifications
        # package free of a dependency on the tools package.
        tokens = await redis_get_json(await resolve_tokens_key(clinic_id))
        if not tokens:
            logger.warning(
                "[reminders] provisional gate: no Google tokens for clinic %r "
                "— sending anyway rather than going silent", clinic_id,
            )
            return True

        event = await asyncio.to_thread(get_event, tokens, event_id, calendar_id)
        if not event:
            logger.info(
                "[reminders] provisional gate: event %r is gone — not sending",
                event_id,
            )
            return False

        summary = (event.get("summary") or "").strip().upper()
        if summary.startswith(_PENDING_TITLE_PREFIX):
            logger.info(
                "[reminders] provisional gate: %r still reads %r — staying "
                "silent, the practitioner has not confirmed it",
                event_id, event.get("summary"),
            )
            return False
        return True
    except Exception as e:
        logger.warning(
            "[reminders] provisional gate check failed (%r) — sending anyway", e,
        )
        return True


async def _send_reminder(reminder_data: Dict[str, Any]) -> str:
    """
    Send a single reminder SMS.

    Returns one of "sent" / "suppressed" / "failed" so the caller can record
    what actually happened instead of assuming success.
    """
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
    clinic_name = reminder_data.get("clinic_name")
    clinic_phone = reminder_data.get("clinic_phone")
    # Pinned booking-clinic sender (falls back to env for legacy/absent entries)
    from_number = reminder_data.get("from_number") or None
    appointment_noun = reminder_data.get("appointment_noun")

    if not await _confirmed_enough_to_remind(reminder_data.get("confirm_gate")):
        return "suppressed"

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
                clinic_name=clinic_name,
                clinic_phone=clinic_phone,
                from_number=from_number,
                appointment_noun=appointment_noun,
            )
        elif reminder_type == "2hr":
            success = await send_same_day_reminder(
                patient_phone=patient_phone,
                patient_name=patient_name,
                appointment_time=appointment_time,
                location=location,
                clinic_name=clinic_name,
                clinic_phone=clinic_phone,
                from_number=from_number,
                appointment_noun=appointment_noun,
            )
        else:
            logger.error(f"Unknown reminder type: {reminder_type}")
            return "failed"

        return "sent" if success else "suppressed"

    except Exception as e:
        logger.error(f"Failed to send reminder: {e}", exc_info=True)
        return "failed"


# ============================================================================
# BACKGROUND WORKER (OPTIONAL)
# ============================================================================

# ============================================================================
# NAME-CONFIRMATION REMINDER (30-minute nudge)
# Uses async Redis directly — independent of the legacy sync scheduler above.
# ============================================================================

async def schedule_name_confirm_reminder(
    phone: str,
    first_name: str,
    delay_minutes: int = 30,
    from_number: Optional[str] = None,
) -> None:
    """
    Schedule a name-confirmation nudge SMS to be sent delay_minutes from now.
    Stores phone+first_name in a Redis sorted set scored by send-at timestamp.
    Safe no-op if Redis is unavailable. from_number pins the sender to the
    booking clinic's own line (shared-Redis tenant safety); None → env fallback.
    """
    from app.storage.redis_store import redis_client as _ar
    if not _ar:
        logger.warning("[NAME_REMINDER] Redis unavailable — cannot schedule nudge for %r", phone)
        return
    try:
        send_at = (datetime.utcnow() + timedelta(minutes=delay_minutes)).timestamp()
        payload = json.dumps({"phone": phone, "first_name": first_name,
                              "from_number": from_number or ""})
        await _ar.zadd(PENDING_NAME_REMINDERS_SET, {payload: send_at})
        logger.info(
            "[NAME_REMINDER] scheduled: phone=%r delay=%dmin send_at=%s",
            phone, delay_minutes, datetime.utcfromtimestamp(send_at).isoformat(),
        )
    except Exception as _e:
        logger.error("[NAME_REMINDER] schedule failed (non-fatal): %r", _e)


async def process_name_confirm_reminders() -> int:
    """
    Send any name-confirmation nudge SMSes whose send-at time has passed.
    Only sends if the pending_name record still has status='pending'.
    Called by the background worker every interval.
    """
    from app.storage.redis_store import redis_client as _ar
    if not _ar:
        return 0
    try:
        now_ts = datetime.utcnow().timestamp()
        due: list = await _ar.zrangebyscore(PENDING_NAME_REMINDERS_SET, 0, now_ts)
        if not due:
            return 0

        from app.storage.redis_store import get_pending_name_confirmation
        from app.notifications.sms import send_sms

        sent = 0
        for payload_str in due:
            try:
                data = json.loads(payload_str)
                phone = data.get("phone", "")
                first_name = data.get("first_name") or "there"
                from_number = data.get("from_number") or None

                pending = await get_pending_name_confirmation(phone)
                if pending and pending.get("status") == "pending":
                    await send_sms(
                        to=phone,
                        message=(
                            f"Hi {first_name}, just a reminder — please reply to this "
                            "message with your full first name and surname to confirm "
                            "your appointment with us."
                        ),
                        from_number=from_number,
                    )
                    logger.info("[NAME_REMINDER] nudge sent: phone=%r", phone)
                else:
                    logger.info(
                        "[NAME_REMINDER] skipped (already completed or expired): phone=%r", phone
                    )

                await _ar.zrem(PENDING_NAME_REMINDERS_SET, payload_str)
                sent += 1
            except Exception as _e:
                logger.error("[NAME_REMINDER] error for payload=%r: %r", payload_str, _e)

        return sent
    except Exception as _e:
        logger.error("[NAME_REMINDER] process_name_confirm_reminders failed: %r", _e)
        return 0


async def schedule_address_reminder(
    phone: str,
    first_name: str,
    delay_minutes: int = 30,
    from_number: Optional[str] = None,
    clinic_id: str = "",
) -> None:
    """
    Schedule a nudge SMS asking a home-visit patient to text their full address
    + postcode, delay_minutes from now. Stored in a Redis sorted set scored by
    send-at timestamp. Safe no-op if Redis is unavailable.

    from_number pins the sender to the BOOKING clinic's own Twilio line, so the
    reminder (and the patient's reply) stay on the number whose inbound webhook
    routes back to this clinic. The reminder queue is a GLOBAL key, so on a
    shared Redis another tenant's worker may process this entry — without a
    pinned from_number it would send from that worker's ambient
    TWILIO_PHONE_NUMBER and the reply would land on the wrong line and be lost.
    """
    from app.storage.redis_store import redis_client as _ar
    if not _ar:
        logger.warning("[ADDR_REMINDER] Redis unavailable — cannot schedule nudge for %r", phone)
        return
    try:
        send_at = (datetime.utcnow() + timedelta(minutes=delay_minutes)).timestamp()
        payload = json.dumps({
            "phone": phone, "first_name": first_name,
            "from_number": from_number or "", "clinic_id": clinic_id or "",
        })
        await _ar.zadd(PENDING_ADDRESS_REMINDERS_SET, {payload: send_at})
        logger.info(
            "[ADDR_REMINDER] scheduled: phone=%r delay=%dmin send_at=%s",
            phone, delay_minutes, datetime.utcfromtimestamp(send_at).isoformat(),
        )
    except Exception as _e:
        logger.error("[ADDR_REMINDER] schedule failed (non-fatal): %r", _e)


async def process_address_reminders() -> int:
    """
    Send any home-visit address nudge SMSes whose send-at time has passed.
    Called by the background worker every interval.
    """
    from app.storage.redis_store import redis_client as _ar
    if not _ar:
        return 0
    try:
        now_ts = datetime.utcnow().timestamp()
        due: list = await _ar.zrangebyscore(PENDING_ADDRESS_REMINDERS_SET, 0, now_ts)
        if not due:
            return 0

        from app.notifications.sms import send_sms

        sent = 0
        for payload_str in due:
            try:
                data = json.loads(payload_str)
                phone = data.get("phone", "")
                first_name = data.get("first_name") or "there"
                # Send from the booking clinic's own line (pinned at schedule
                # time) so the reply routes back to the right number's inbound
                # webhook — never this worker's ambient number. Falls back to
                # env when absent (legacy entries queued before this change).
                from_number = data.get("from_number") or None
                await send_sms(
                    to=phone,
                    message=(
                        f"Hi {first_name}, just making sure we have everything for "
                        "your home visit — if you haven't already, please reply with "
                        "your full home address and postcode so we can finalise it. "
                        "Thanks!"
                    ),
                    from_number=from_number,
                )
                logger.info("[ADDR_REMINDER] nudge sent: phone=%r", phone)
                await _ar.zrem(PENDING_ADDRESS_REMINDERS_SET, payload_str)
                sent += 1
            except Exception as _e:
                logger.error("[ADDR_REMINDER] error for payload=%r: %r", payload_str, _e)

        return sent
    except Exception as _e:
        logger.error("[ADDR_REMINDER] process_address_reminders failed: %r", _e)
        return 0


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
            name_nudges = await process_name_confirm_reminders()
            if name_nudges > 0:
                logger.info("Reminder worker sent %d name-confirm nudge(s)", name_nudges)
            addr_nudges = await process_address_reminders()
            if addr_nudges > 0:
                logger.info("Reminder worker sent %d address nudge(s)", addr_nudges)
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
    return await redis_client.zcard(PENDING_REMINDERS_SET)


async def get_upcoming_reminders(limit: int = 10) -> list:
    """Get upcoming reminders for monitoring."""
    if not REDIS_AVAILABLE or not redis_client:
        return []
    
    try:
        reminder_ids = await redis_client.zrange(PENDING_REMINDERS_SET, 0, limit - 1)
        reminders = []
        for reminder_id in reminder_ids:
            reminder_data_json = await redis_client.get(reminder_id)
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
