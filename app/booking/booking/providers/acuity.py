"""
Acuity Scheduling provider adapter.
"""

import asyncio
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

logger = logging.getLogger(__name__)


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
        )
    
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
                extra={"clinic_id": self.clinic_id, "message": error_message},
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
            logger.warning(
                f"Acuity client error: {operation}",
                extra={
                    "clinic_id": self.clinic_id,
                    "status": status,
                    "message": error_message,
                },
            )
            raise ProviderUnavailable(
                f"Acuity request error: {error_message}",
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
        
        params = {
            "appointmentTypeID": acuity_type_id,
            "date": start_date.isoformat(),
            "endDate": end_date.isoformat(),
            "timezone": "Europe/London",
        }
        
        if practitioner_id:
            acuity_cal_id = practitioner_id.replace("acuity_cal_", "")
            params["calendarID"] = acuity_cal_id
        
        response = await self._request_with_retry(
            "GET",
            "/availability/times",
            params=params,
        )
        data = response.json()
        
        slots = []
        for item in data:
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
        
        # Parse response times
        start_time = datetime.fromisoformat(data["datetime"])
        start_time = ensure_london_tz(start_time)
        end_time = datetime.fromisoformat(data["endTime"])
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
        """Cancel appointment in Acuity."""
        try:
            await self._request_with_retry(
                "PUT",
                f"/appointments/{provider_booking_id}/cancel",
                allow_retry=False,  # Don't retry cancellations
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
