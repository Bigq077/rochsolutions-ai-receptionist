"""
BookingService orchestration layer.

Handles idempotency, locking, caching, and logging.
"""

import json
import logging
from typing import List, Optional, Dict, Any
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
import redis.asyncio as redis

from .models import Slot, Booking, AppointmentType, Practitioner, BookingRequest
from .interface import BookingProvider
from .exceptions import (
    BookingError,
    ProviderAuthError,
    ProviderRateLimited,
    ProviderUnavailable,
    SlotUnavailable,
)
from .utils import generate_idempotency_key, to_utc, ensure_london_tz

logger = logging.getLogger(__name__)


class BookingService:
    """
    Orchestrates booking operations across providers.
    
    Responsibilities:
    - Idempotency via Redis
    - Distributed locking to prevent race conditions
    - Caching availability with TTL
    - Structured logging for KPIs
    - Graceful error handling for Twilio webhook safety
    """
    
    CACHE_TTL_SECONDS = 300  # 5 minutes
    LOCK_TTL_SECONDS = 30  # 30 seconds max lock hold time
    LOCK_ACQUIRE_TIMEOUT = 10  # 10 seconds to acquire lock
    
    def __init__(
        self,
        provider: BookingProvider,
        redis_client: redis.Redis,
        clinic_id: str,
    ):
        """
        Initialize BookingService.
        
        Args:
            provider: Booking provider implementation (Acuity, Google Calendar, etc.)
            redis_client: Async Redis client
            clinic_id: Clinic identifier for multi-tenant context
        """
        self.provider = provider
        self.redis = redis_client
        self.clinic_id = clinic_id
    
    def _cache_key(self, prefix: str, *parts: str) -> str:
        """Generate Redis cache key."""
        return f"booking:{self.clinic_id}:{prefix}:" + ":".join(parts)
    
    async def _get_cached(self, key: str) -> Optional[Any]:
        """Get cached value from Redis."""
        try:
            data = await self.redis.get(key)
            if data:
                return json.loads(data)
        except Exception as e:
            logger.warning(f"Cache get failed: {e}", extra={"key": key})
        return None
    
    async def _set_cached(self, key: str, value: Any, ttl: int):
        """Set cached value in Redis with TTL."""
        try:
            await self.redis.setex(key, ttl, json.dumps(value))
        except Exception as e:
            logger.warning(f"Cache set failed: {e}", extra={"key": key})
    
    async def _bust_availability_cache(self, appointment_type_id: str):
        """
        Invalidate availability cache after booking.

        FIX #10: Upgraded warning → critical so a failure is immediately
        visible (stale cached slots could cause overbooking).
        FIX (bonus): Uses cursor-based SCAN instead of KEYS so this never
        blocks Redis on large keyspaces.
        """
        pattern = self._cache_key("availability", appointment_type_id, "*")
        try:
            deleted_count = 0
            cursor = 0
            while True:
                cursor, keys = await self.redis.scan(cursor, match=pattern, count=100)
                if keys:
                    await self.redis.delete(*keys)
                    deleted_count += len(keys)
                if cursor == 0:
                    break
            if deleted_count:
                logger.info(
                    "Busted availability cache for %s (%d keys removed)",
                    appointment_type_id,
                    deleted_count,
                    extra={"key_count": deleted_count},
                )
        except Exception as e:
            logger.critical(
                "CRITICAL: Availability cache bust FAILED after booking. "
                "Stale slots may be offered to the next caller. "
                "appointment_type_id=%s clinic=%s error=%s",
                appointment_type_id,
                self.clinic_id,
                e,
                exc_info=True,
            )
    
    async def get_appointment_types(self) -> List[AppointmentType]:
        """
        Fetch available appointment types.
        
        Cached with TTL to reduce provider API calls.
        """
        cache_key = self._cache_key("appointment_types", "all")
        
        # Try cache first
        cached = await self._get_cached(cache_key)
        if cached:
            return [AppointmentType(**item) for item in cached]
        
        # Fetch from provider
        try:
            types = await self.provider.get_appointment_types()
            
            # Cache result
            await self._set_cached(
                cache_key,
                [t.dict() for t in types],
                self.CACHE_TTL_SECONDS,
            )
            
            return types
        
        except ProviderAuthError as e:
            logger.error(
                "Provider auth error fetching appointment types",
                extra={
                    "clinic_id": self.clinic_id,
                    "provider": e.provider,
                    "error": str(e),
                },
            )
            raise
        
        except (ProviderRateLimited, ProviderUnavailable) as e:
            logger.error(
                "Provider error fetching appointment types",
                extra={
                    "clinic_id": self.clinic_id,
                    "provider": e.provider,
                    "error": str(e),
                },
            )
            raise
    
    async def get_available_slots(
        self,
        appointment_type_id: str,
        start_date: date,
        end_date: date,
        practitioner_id: Optional[str] = None,
    ) -> List[Slot]:
        """
        Fetch available slots with caching.
        
        Cache key includes date range and practitioner to avoid stale data.
        """
        cache_key = self._cache_key(
            "availability",
            appointment_type_id,
            start_date.isoformat(),
            end_date.isoformat(),
            practitioner_id or "any",
        )
        
        # Try cache first
        cached = await self._get_cached(cache_key)
        if cached:
            logger.info(
                "Returning cached availability",
                extra={
                    "clinic_id": self.clinic_id,
                    "appointment_type_id": appointment_type_id,
                    "cache_hit": True,
                },
            )
            return [Slot(**item) for item in cached]
        
        # Fetch from provider
        try:
            slots = await self.provider.get_available_slots(
                appointment_type_id,
                start_date,
                end_date,
                practitioner_id,
            )
            
            # Cache result
            await self._set_cached(
                cache_key,
                [s.dict() for s in slots],
                self.CACHE_TTL_SECONDS,
            )
            
            logger.info(
                "Fetched availability from provider",
                extra={
                    "clinic_id": self.clinic_id,
                    "appointment_type_id": appointment_type_id,
                    "slot_count": len(slots),
                    "cache_hit": False,
                },
            )
            
            return slots
        
        except (ProviderAuthError, ProviderRateLimited, ProviderUnavailable) as e:
            logger.error(
                "Provider error fetching availability",
                extra={
                    "clinic_id": self.clinic_id,
                    "provider": e.provider,
                    "appointment_type_id": appointment_type_id,
                    "error": str(e),
                },
            )
            raise
    
    async def create_booking(self, request: BookingRequest) -> Booking:
        """
        Create a booking with idempotency and locking.
        
        Returns existing booking if idempotency key matches.
        Uses distributed lock to prevent race conditions.
        """
        # Generate idempotency key
        idempotency_key = generate_idempotency_key(
            clinic_id=self.clinic_id,
            call_sid=request.call_sid,
            session_id=request.session_id,
            appointment_type_id=request.appointment_type_id,
            slot_start_iso=request.slot_start.isoformat(),
            location_id=request.location_id,
            practitioner_id=request.practitioner_id,
        )
        
        # Check if booking already exists (idempotency)
        existing = await self._get_cached(idempotency_key)
        if existing:
            logger.info(
                "Returning existing booking (idempotent)",
                extra={
                    "clinic_id": self.clinic_id,
                    "idempotency_key": idempotency_key,
                    "booking_id": existing.get("id"),
                },
            )
            return Booking(**existing)
        
        # Acquire distributed lock
        lock_key = f"{idempotency_key}:lock"
        lock_acquired = False

        try:
            # FIX #4: redis.set(nx=True) returns True on success or None on
            # failure.  Normalise to an explicit bool so the finally block's
            # `if lock_acquired:` guard is unambiguous regardless of Redis
            # library version or decode_responses setting.
            lock_acquired = bool(
                await self.redis.set(
                    lock_key,
                    "locked",
                    nx=True,  # Only set if not exists
                    ex=self.LOCK_TTL_SECONDS,
                )
            )
            
            if not lock_acquired:
                # Another request is processing this booking
                logger.warning(
                    "Could not acquire lock, potential concurrent booking",
                    extra={
                        "clinic_id": self.clinic_id,
                        "idempotency_key": idempotency_key,
                    },
                )
                
                # Wait briefly and check if booking was created
                import asyncio
                await asyncio.sleep(2)
                
                existing = await self._get_cached(idempotency_key)
                if existing:
                    return Booking(**existing)
                
                # Lock timeout - proceed with caution
                raise BookingError(
                    "Booking lock timeout - please try again",
                    safe_metadata={"idempotency_key": idempotency_key},
                )
            
            # Create booking via provider
            logger.info(
                "Creating booking",
                extra={
                    "clinic_id": self.clinic_id,
                    "appointment_type_id": request.appointment_type_id,
                    "slot_start": request.slot_start.isoformat(),
                    "call_sid": request.call_sid,
                },
            )
            
            booking = await self.provider.create_booking(request)
            
            # Cache booking result for idempotency
            await self._set_cached(
                idempotency_key,
                booking.dict(),
                ttl=86400,  # 24 hours
            )
            
            # Bust availability cache
            await self._bust_availability_cache(request.appointment_type_id)
            
            # Log KPI event
            await self._log_booking_kpi(booking, request)
            
            logger.info(
                "Booking created successfully",
                extra={
                    "clinic_id": self.clinic_id,
                    "booking_id": booking.id,
                    "provider_booking_id": booking.provider_booking_id,
                    "appointment_type": booking.appointment_type_name,
                },
            )
            
            return booking
        
        except SlotUnavailable as e:
            logger.warning(
                "Slot no longer available",
                extra={
                    "clinic_id": self.clinic_id,
                    "appointment_type_id": request.appointment_type_id,
                    "slot_start": request.slot_start.isoformat(),
                    "provider": e.provider,
                },
            )
            raise
        
        except (ProviderAuthError, ProviderRateLimited, ProviderUnavailable) as e:
            logger.error(
                "Provider error creating booking",
                extra={
                    "clinic_id": self.clinic_id,
                    "provider": e.provider,
                    "error": str(e),
                    "safe_metadata": e.safe_metadata,
                },
            )
            raise
        
        finally:
            # Release lock
            if lock_acquired:
                try:
                    await self.redis.delete(lock_key)
                except Exception as e:
                    logger.warning(f"Failed to release lock: {e}")
    
    async def _log_booking_kpi(self, booking: Booking, request: BookingRequest):
        """
        Log structured KPI event for booking analytics.
        
        Includes all fields needed for attribution and reporting.
        """
        kpi_event = {
            "event": "booking_created",
            "booking_id": booking.id,
            "provider_booking_id": booking.provider_booking_id,
            "provider": booking.provider,
            "clinic_id": self.clinic_id,
            "call_sid": request.call_sid,
            "session_id": request.session_id,
            "appointment_type_id": booking.appointment_type_id,
            "appointment_type_name": booking.appointment_type_name,
            "location_id": booking.location_id,
            "practitioner_id": booking.practitioner_id,
            "practitioner_name": booking.practitioner_name,
            "start_time_utc": to_utc(booking.start_time).isoformat(),
            "start_time_local": booking.start_time.isoformat(),
            "booking_source": "ai_receptionist",
            "created_at_utc": booking.created_at.isoformat(),
            "patient_phone": request.patient_phone,
            "has_insurance": booking.insurance_info is not None,
        }
        
        logger.info(
            "KPI: Booking created",
            extra=kpi_event,
        )
    
    async def cancel_booking(
        self,
        provider_booking_id: str,
        appointment_type_id: str,
    ) -> bool:
        """
        Cancel a booking.
        
        Busts availability cache and logs cancellation.
        """
        try:
            success = await self.provider.cancel_booking(provider_booking_id)
            
            if success:
                # Bust availability cache
                await self._bust_availability_cache(appointment_type_id)
                
                logger.info(
                    "Booking cancelled",
                    extra={
                        "clinic_id": self.clinic_id,
                        "provider_booking_id": provider_booking_id,
                    },
                )
            
            return success
        
        except Exception as e:
            logger.error(
                "Error cancelling booking",
                extra={
                    "clinic_id": self.clinic_id,
                    "provider_booking_id": provider_booking_id,
                    "error": str(e),
                },
            )
            return False
