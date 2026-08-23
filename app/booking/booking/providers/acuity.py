"""
Acuity Scheduling provider adapter.
"""

import asyncio
import json
import logging
from typing import List, Optional
from datetime import datetime, date, timedelta
import httpx
from zoneinfo import ZoneInfo

from ..models import Slot, Booking, AppointmentType, Practitioner, BookingRequest
from ..exceptions import (
    ProviderAuthError,
    ProviderRateLimited,
    ProviderUnavailable,
    SlotUnavailable,
)
from ..utils import LONDON_TZ, to_utc, ensure_london_tz
from app.storage.redis_store import redis_client

logger = logging.getLogger(__name__)

# TTL for per-date availability cache entries.
# Slot availability does not change meaningfully within a single call;
# 60s is conservative and safe for calls up to ~5 minutes.
_ACUITY_CACHE_TTL = 60


class AcuityAdapter:
    """
    Adapter for Acuity Scheduling API.
    
    Uses httpx AsyncClient with Basic Auth.
    Implements retry logic with exponential backoff for rate limits.
    """
    
    BASE_URL = "https://acuityscheduling.com/api/v1"
    MAX_RETRIES = 3
    INITIAL_BACKOFF = 1.0  # seconds
    
    def __init__(self, user_id: str, api_key: str, clinic_id: str):
        """
        Initialize Acuity adapter.
        
        Args:
            user_id: Acuity user ID for Basic Auth
            api_key: Acuity API key for Basic Auth
            clinic_id: Our internal clinic identifier (for logging)
        """
        self.user_id = user_id
        self.api_key = api_key
        self.clinic_id = clinic_id
        self.client = httpx.AsyncClient(
            base_url=self.BASE_URL,
            auth=(user_id, api_key),
            timeout=30.0,
            # Keep TCP connections alive so the first Acuity call after an
            # idle period doesn't pay the full TCP+TLS handshake cost.
            # limits: max 5 keepalive connections, each held for 90 s.
            limits=httpx.Limits(
                max_keepalive_connections=5,
                keepalive_expiry=90,
            ),
            headers={"Connection": "keep-alive"},
        )
        # Cache: appointment_type_id → list of {id, value} for required fields
        self._required_fields_cache: dict = {}
    
    async def close(self):
        """Close the HTTP client."""
        await self.client.aclose()
    
    async def __aenter__(self):
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()
    
    def _handle_error(self, response: httpx.Response, operation: str):
        """Map HTTP errors to our exception hierarchy."""
        status = response.status_code
        
        try:
            error_data = response.json()
            error_message = error_data.get("message", response.text)
        except Exception:
            error_message = response.text
        
        safe_metadata = {
            "status_code": status,
            "operation": operation,
            "clinic_id": self.clinic_id,
        }
        
        if status in (401, 403):
            logger.error(
                f"Acuity auth error: {operation}",
                extra={"clinic_id": self.clinic_id, "status": status},
            )
            raise ProviderAuthError(
                f"Acuity authentication failed: {error_message}",
                provider="acuity",
                safe_metadata=safe_metadata,
            )
        
        if status == 429:
            retry_after = None
            if "Retry-After" in response.headers:
                try:
                    retry_after = int(response.headers["Retry-After"])
                except ValueError:
                    pass
            
            logger.warning(
                f"Acuity rate limit hit: {operation}",
                extra={
                    "clinic_id": self.clinic_id,
                    "retry_after": retry_after,
                },
            )
            raise ProviderRateLimited(
                f"Acuity rate limit exceeded: {error_message}",
                provider="acuity",
                retry_after=retry_after,
                safe_metadata=safe_metadata,
            )
        
        if status >= 500:
            logger.error(
                f"Acuity server error: {operation}",
                extra={"clinic_id": self.clinic_id, "status": status},
            )
            raise ProviderUnavailable(
                f"Acuity service unavailable: {error_message}",
                provider="acuity",
                safe_metadata=safe_metadata,
            )
        
        # FIX #7: 409 Conflict means the slot was taken by someone else between
        # availability check and booking.  Raise SlotUnavailable so the caller
        # is offered alternative slots rather than seeing a generic error.
        if status == 409:
            logger.warning(
                f"Acuity slot conflict (409): {operation}",
                # NOTE: "message" is a reserved LogRecord attribute — use "error_msg"
                extra={"clinic_id": self.clinic_id, "error_msg": error_message},
            )
            raise SlotUnavailable(
                f"Slot no longer available: {error_message}",
                provider="acuity",
                safe_metadata=safe_metadata,
            )

        # Other 4xx (400 Bad Request, 422 Unprocessable Entity, etc.) — client
        # error, do not retry.  Use ProviderUnavailable so the service layer
        # surfaces a clean error message without leaking API details.
        if 400 <= status < 500:
            # `error_message` goes in the MESSAGE, not only in `extra`.
            # app/main.py configures logging with '...%(message)s' and nothing
            # else, so every `extra=` field is silently dropped in Render.  A
            # 400 used to surface as a bare "Acuity client error: /appointments
            # /<id>/cancel" with no reason attached anywhere, which made a live
            # cancellation failure impossible to diagnose from the logs alone
            # (2026-08-23 — the reason had to be recovered by querying the
            # Acuity API by hand).  Keep the `extra` for structured sinks.
            logger.warning(
                "Acuity client error: %s — HTTP %s: %s",
                operation, status, error_message,
                # NOTE: "message" is a reserved LogRecord attribute — use "error_msg"
                extra={
                    "clinic_id": self.clinic_id,
                    "status": status,
                    "error_msg": error_message,
                },
            )
            raise ProviderUnavailable(
                f"Acuity request error ({status}): {error_message}",
                provider="acuity",
                safe_metadata=safe_metadata,
            )
    
    async def _request_with_retry(
        self,
        method: str,
        endpoint: str,
        allow_retry: bool = True,
        **kwargs,
    ) -> httpx.Response:
        """
        Make HTTP request with retry logic for rate limits.
        
        Only retries on 429 (rate limit). Does NOT retry 5xx to avoid
        cascading failures in webhook context.
        """
        last_exception = None
        backoff = self.INITIAL_BACKOFF
        
        retries = self.MAX_RETRIES if allow_retry else 1
        
        for attempt in range(retries):
            try:
                response = await self.client.request(method, endpoint, **kwargs)
                
                if response.status_code == 429 and attempt < retries - 1:
                    # Extract retry-after or use backoff
                    retry_after = None
                    if "Retry-After" in response.headers:
                        try:
                            retry_after = int(response.headers["Retry-After"])
                        except ValueError:
                            pass
                    
                    wait_time = retry_after if retry_after else backoff
                    logger.info(
                        f"Rate limited, retrying after {wait_time}s",
                        extra={
                            "attempt": attempt + 1,
                            "wait_time": wait_time,
                        },
                    )
                    await asyncio.sleep(wait_time)
                    backoff *= 2  # Exponential backoff
                    continue
                
                response.raise_for_status()
                return response
            
            except httpx.HTTPStatusError as e:
                last_exception = e
                # Don't retry on non-429 errors
                break
            
            except (httpx.TimeoutException, httpx.NetworkError) as e:
                last_exception = e
                logger.error(
                    f"Acuity network error: {endpoint}",
                    extra={
                        "error": str(e),
                        "attempt": attempt + 1,
                    },
                )
                # Don't retry network errors in webhook context
                break
        
        # If we get here, all retries exhausted or non-retryable error
        if isinstance(last_exception, httpx.HTTPStatusError):
            self._handle_error(last_exception.response, endpoint)
        else:
            raise ProviderUnavailable(
                f"Acuity network error: {str(last_exception)}",
                provider="acuity",
                safe_metadata={"endpoint": endpoint, "clinic_id": self.clinic_id},
            )
    
    async def get_appointment_types(self) -> List[AppointmentType]:
        """Fetch appointment types from Acuity."""
        response = await self._request_with_retry("GET", "/appointment-types")
        data = response.json()
        
        appointment_types = []
        for item in data:
            appointment_types.append(
                AppointmentType(
                    id=f"acuity_{item['id']}",
                    name=item["name"],
                    duration_minutes=item["duration"],
                    description=item.get("description"),
                    provider_id=str(item["id"]),
                    price_gbp=0.0,  # Price comes from clinic_config
                )
            )
        
        logger.info(
            "Fetched appointment types from Acuity",
            extra={
                "clinic_id": self.clinic_id,
                "count": len(appointment_types),
            },
        )
        return appointment_types
    
    async def get_practitioners(self) -> List[Practitioner]:
        """Fetch calendars (practitioners) from Acuity."""
        response = await self._request_with_retry("GET", "/calendars")
        data = response.json()
        
        practitioners = []
        for item in data:
            practitioners.append(
                Practitioner(
                    id=f"acuity_cal_{item['id']}",
                    name=item["name"],
                    provider_id=str(item["id"]),
                )
            )
        
        logger.info(
            "Fetched practitioners from Acuity",
            extra={
                "clinic_id": self.clinic_id,
                "count": len(practitioners),
            },
        )
        return practitioners
    
    async def get_available_slots(
        self,
        appointment_type_id: str,
        start_date: date,
        end_date: date,
        practitioner_id: Optional[str] = None,
    ) -> List[Slot]:
        """
        Fetch available time slots from Acuity.
        
        Returns slots in Europe/London timezone.
        """
        # Extract provider ID from our composite ID
        acuity_type_id = appointment_type_id.replace("acuity_", "")
        acuity_cal_id = practitioner_id.replace("acuity_cal_", "") if practitioner_id else None

        # Query day-by-day in parallel: Acuity's range query is unreliable for
        # future dates, but per-day queries can be fired concurrently.
        num_days = max(1, (end_date - start_date).days)
        days = [start_date + timedelta(days=i) for i in range(num_days)]

        async def _fetch_day(day: date) -> list:
            day_str = day.isoformat()
            cache_key = (
                f"acuity_avail:{acuity_type_id}:{acuity_cal_id or ''}:{day_str}"
            )

            # ── Cache read ────────────────────────────────────────────────
            if redis_client:
                try:
                    cached = await redis_client.get(cache_key)
                    if cached is not None:
                        logger.debug("[acuity_cache] HIT %s", cache_key)
                        return json.loads(cached)
                except Exception as _cache_err:
                    logger.warning(
                        "[acuity_cache] Redis read error for %s: %r",
                        cache_key, _cache_err,
                    )

            logger.debug("[acuity_cache] MISS %s", cache_key)

            # ── Live Acuity request ───────────────────────────────────────
            params = {
                "appointmentTypeID": acuity_type_id,
                "date": day_str,
                "timezone": "Europe/London",
            }
            if acuity_cal_id:
                params["calendarID"] = acuity_cal_id
            try:
                response = await self._request_with_retry(
                    "GET",
                    "/availability/times",
                    params=params,
                )
                slots = response.json()
            except Exception as day_err:
                logger.warning("Acuity per-day query failed for %s: %r", day_str, day_err)
                slots = []

            # ── Cache write ───────────────────────────────────────────────
            if redis_client:
                try:
                    await redis_client.set(
                        cache_key,
                        json.dumps(slots),
                        ex=_ACUITY_CACHE_TTL,
                    )
                except Exception as _cache_err:
                    logger.warning(
                        "[acuity_cache] Redis write error for %s: %r",
                        cache_key, _cache_err,
                    )

            logger.debug("Acuity per-day: %s — %d slot(s)", day_str, len(slots))
            return slots

        results = await asyncio.gather(*(_fetch_day(d) for d in days))
        raw_items = [slot for day_slots in results for slot in day_slots]

        slots = []
        for item in raw_items:
            # Parse datetime from Acuity
            start_dt = datetime.fromisoformat(item["time"])
            start_dt = ensure_london_tz(start_dt)

            # End time is start + 50 mins (from clinic_config)
            end_dt = start_dt + timedelta(minutes=50)

            slots.append(
                Slot(
                    start_time=start_dt,
                    end_time=end_dt,
                    appointment_type_id=appointment_type_id,
                    practitioner_id=practitioner_id,
                    provider_slot_id=item.get("time"),
                )
            )

        logger.info(
            "Fetched available slots from Acuity",
            extra={
                "clinic_id": self.clinic_id,
                "appointment_type_id": appointment_type_id,
                "date_range": f"{start_date} to {end_date}",
                "slot_count": len(slots),
            },
        )
        return slots
    
    async def _get_required_form_fields(self, appointment_type_id: str) -> list:
        """
        Return [{"id": <int>, "value": <str>}] for every required intake-form
        field that must be submitted with this appointment type.

        Hardcoded per appointment type — auto-detection is unreliable because
        Theorem Health has two near-identical global "Client Details" forms
        (IDs 1611057 and 1500327) that the API cannot distinguish per booking
        type.  All field IDs were confirmed from /debug/acuity-forms on
        2026-04-07.

        Forms and fields:
          Form 1487657  "(A) Terms & Conditions inc Fees and Invoicing"
            10610285  PLEASE TICK to SIGN…  checkbox  required  → "1"
          Form 2385334  "Terms & Conditions and Fees"
            13388219  I have read and agree to the terms above  checkbox  → "1"
          Form 1500327  "Client Details Physio / TCM"  (Redditch)
            8494898   D.O.B          textbox   required  → "01/01/2000"
            8871008   Address        address   required  → "Phone booking"
          Form 1611057  "Client Details Physio"  (Alcester)
            8886352   D.O.B          textbox   required  → "01/01/2000"
            8886353   Address        address   required  → "Phone booking"
        """
        raw_type_id = appointment_type_id.replace("acuity_", "")
        if raw_type_id in self._required_fields_cache:
            return self._required_fields_cache[raw_type_id]

        # Checkbox fields shared by all Theorem physio appointment types
        _SHARED = [
            {"id": 10610285, "value": "1"},   # (A) T&C checkbox — form 1487657
            {"id": 13388219, "value": "1"},   # "I agree to terms" — form 2385334
        ]

        # Per-type fields (D.O.B + Address) keyed by raw Acuity appointment type ID
        _PER_TYPE: dict = {
            # Redditch physio assessment — form 1500327 "Client Details Physio / TCM"
            "33801703": [
                {"id": 8494898, "value": "01/01/2000"},  # D.O.B
                {"id": 8871008, "value": "Phone booking"},  # Address
            ],
            # Alcester physio assessment — also form 1500327 (confirmed by smoke test)
            "15823699": [
                {"id": 8494898, "value": "01/01/2000"},  # D.O.B
                {"id": 8871008, "value": "Phone booking"},  # Address
            ],
        }

        per_type = _PER_TYPE.get(raw_type_id, [])
        required_fields = _SHARED + per_type
        logger.info(
            "Acuity form fields for type %s: %s (hardcoded)",
            raw_type_id, [f["id"] for f in required_fields],
        )
        self._required_fields_cache[raw_type_id] = required_fields
        return required_fields

    async def create_booking(self, request: BookingRequest) -> Booking:
        """
        Create appointment in Acuity.

        This operation is NOT retried on failure to prevent double-booking.
        Idempotency is handled by BookingService layer.
        """
        # Extract provider ID
        acuity_type_id = request.appointment_type_id.replace("acuity_", "")
        
        # Format datetime for Acuity (ISO format in Europe/London)
        london_time = ensure_london_tz(request.slot_start)
        
        payload = {
            "appointmentTypeID": acuity_type_id,
            "datetime": london_time.isoformat(),
            "firstName": request.patient_first_name,
            "lastName": request.patient_last_name,
            "phone": request.patient_phone,
        }
        
        if request.patient_email:
            payload["email"] = request.patient_email
        
        if request.notes:
            payload["notes"] = request.notes
        
        if request.practitioner_id:
            acuity_cal_id = request.practitioner_id.replace("acuity_cal_", "")
            payload["calendarID"] = acuity_cal_id

        # Pre-check any required intake form checkbox fields.
        # Field IDs are configured per appointment type via env var
        # ACUITY_FORM_FIELDS_<TYPE_ID> (comma-separated integers).
        required_fields = await self._get_required_form_fields(request.appointment_type_id)
        if required_fields:
            payload["fields"] = required_fields
            logger.info(
                "Acuity booking: injecting %d required form field(s) for type %s",
                len(required_fields), request.appointment_type_id,
            )

        # NO RETRY on POST to prevent double-booking
        try:
            response = await self._request_with_retry(
                "POST",
                "/appointments",
                json=payload,
                allow_retry=False,  # Critical: no retry
            )
        except ProviderUnavailable as e:
            # Check if error indicates slot taken
            if "no longer available" in str(e).lower() or "already booked" in str(e).lower():
                raise SlotUnavailable(
                    "Slot no longer available",
                    provider="acuity",
                    safe_metadata=e.safe_metadata,
                )
            raise
        
        data = response.json()
        
        # Validate response has booking ID
        if "id" not in data:
            logger.error(
                "Acuity booking response missing ID",
                extra={
                    "clinic_id": self.clinic_id,
                    "response_keys": list(data.keys()),
                },
            )
            raise ProviderUnavailable(
                "Acuity returned invalid booking response",
                provider="acuity",
                safe_metadata={"clinic_id": self.clinic_id},
            )
        
        provider_booking_id = str(data["id"])
        
        # Parse response times.
        # data["datetime"] is ISO format (e.g. '2026-04-09T10:00:00+0100').
        # data["endTime"] is time-only 12-hour format (e.g. '10:50am') — NOT ISO.
        start_time = datetime.fromisoformat(data["datetime"])
        start_time = ensure_london_tz(start_time)
        try:
            end_time = datetime.fromisoformat(data["endTime"])
        except (ValueError, TypeError):
            # Parse time-only string like '10:50am', combine with start date
            from datetime import datetime as _dt
            t = _dt.strptime(data["endTime"].strip().upper(), "%I:%M%p")
            end_time = start_time.replace(
                hour=t.hour, minute=t.minute, second=0, microsecond=0
            )
        end_time = ensure_london_tz(end_time)
        
        booking = Booking(
            id=f"acuity_{provider_booking_id}",
            provider_booking_id=provider_booking_id,
            appointment_type_id=request.appointment_type_id,
            appointment_type_name=data.get("appointmentType", "Physiotherapy Session"),
            location_id=request.location_id,
            start_time=start_time,
            end_time=end_time,
            patient_first_name=request.patient_first_name,
            patient_last_name=request.patient_last_name,
            patient_phone=request.patient_phone,
            patient_email=request.patient_email,
            practitioner_id=request.practitioner_id,
            practitioner_name=data.get("calendar"),
            status="confirmed",
            provider="acuity",
            created_at=datetime.now(ZoneInfo("UTC")),
            notes=request.notes,
            insurance_info=request.insurance_info,
        )
        
        logger.info(
            "Created booking in Acuity",
            extra={
                "clinic_id": self.clinic_id,
                "booking_id": booking.id,
                "provider_booking_id": provider_booking_id,
                "appointment_type": booking.appointment_type_name,
                "start_time": booking.start_time.isoformat(),
            },
        )

        # ── Cache invalidation ────────────────────────────────────────────
        # Delete the cached availability for the booked date so any subsequent
        # check_availability call in the same conversation does not return the
        # slot that was just taken.
        if redis_client:
            _inv_cal_id = (
                request.practitioner_id.replace("acuity_cal_", "")
                if request.practitioner_id else ""
            )
            _inv_date = london_time.date().isoformat()
            _inv_key = f"acuity_avail:{acuity_type_id}:{_inv_cal_id}:{_inv_date}"
            try:
                await redis_client.delete(_inv_key)
                logger.debug("[acuity_cache] invalidated %s after booking", _inv_key)
            except Exception as _inv_err:
                logger.warning(
                    "[acuity_cache] Redis invalidation error for %s: %r",
                    _inv_key, _inv_err,
                )

        return booking
    
    async def list_appointments(
        self,
        min_date: Optional[date] = None,
        max_date: Optional[date] = None,
        calendar_id: Optional[str] = None,
    ) -> List[dict]:
        """
        List upcoming appointments from Acuity.

        Returns raw Acuity appointment dicts, each containing:
            id, firstName, lastName, datetime, endTime, type, calendar, calendarID
        Useful for finding a booking by patient name before cancel/reschedule.
        """
        params: dict = {}
        if min_date:
            params["minDate"] = min_date.isoformat()
        if max_date:
            params["maxDate"] = max_date.isoformat()
        if calendar_id:
            params["calendarID"] = calendar_id

        response = await self._request_with_retry("GET", "/appointments", params=params)
        data = response.json()

        logger.info(
            "Listed appointments from Acuity",
            extra={"clinic_id": self.clinic_id, "count": len(data) if isinstance(data, list) else 0},
        )
        return data if isinstance(data, list) else []

    async def cancel_booking(self, provider_booking_id: str) -> bool:
        """Cancel appointment in Acuity.

        `admin=true` is REQUIRED and must not be removed.

        Every Acuity appointment carries a `canClientCancel` flag, which the
        account flips to False once the booking falls inside the clinic's
        minimum-cancellation-notice window.  Without `admin`, Acuity enforces
        that flag and answers this endpoint with a flat 400 — so Susie could
        never cancel a short-notice appointment, which is the single most
        common thing a caller rings to do.  Proven live on 2026-08-23: two
        separate calls both 400'd cancelling a next-morning appointment
        (`canClientCancel: False`) on an account where a fortnight-out booking
        read True.

        `admin` is the correct semantic here, not a bypass of clinic policy.
        The flag distinguishes a client self-serving on the public booking page
        from the clinic's own reception acting on their behalf — and Susie
        authenticates with the clinic's own API credentials.  She is reception.
        The notice window still governs what patients can do unaided.
        """
        try:
            await self._request_with_retry(
                "PUT",
                f"/appointments/{provider_booking_id}/cancel",
                allow_retry=False,  # Don't retry cancellations
                params={"admin": "true"},
            )
            
            logger.info(
                "Cancelled booking in Acuity",
                extra={
                    "clinic_id": self.clinic_id,
                    "provider_booking_id": provider_booking_id,
                },
            )
            return True
        
        except ProviderUnavailable:
            # If cancellation fails, log but don't crash
            logger.error(
                "Failed to cancel booking in Acuity",
                extra={
                    "clinic_id": self.clinic_id,
                    "provider_booking_id": provider_booking_id,
                },
            )
            return False
