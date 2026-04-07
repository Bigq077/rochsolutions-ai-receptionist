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
            logger.warning(
                f"Acuity client error: {operation}",
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
            print(f"{day_str}: {len(slots)} slots")
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
        Return a list of {"id": <int>, "value": "1"} for every required
        checkbox / signature intake-form field that belongs to this appointment
        type.

        Priority:
          1. Env var override  ACUITY_FORM_FIELDS_<TYPE_ID>  (comma-separated
             field IDs) — set this if auto-detection misbehaves.
          2. Auto-detection: fetch all forms from Acuity, keep only forms whose
             `appointmentTypes` array includes this type ID, then collect every
             required checkbox/signature field from those forms.

        Results are cached per adapter instance after first fetch.
        """
        import os as _os
        raw_type_id = appointment_type_id.replace("acuity_", "")
        if raw_type_id in self._required_fields_cache:
            return self._required_fields_cache[raw_type_id]

        # ── 1. Manual override via env var ────────────────────────────────────
        env_key = f"ACUITY_FORM_FIELDS_{raw_type_id}"
        raw_val = _os.getenv(env_key, "").strip()
        if raw_val:
            required_fields: list = []
            for token in raw_val.split(","):
                token = token.strip()
                if token:
                    try:
                        required_fields.append({"id": int(token), "value": "1"})
                    except ValueError:
                        logger.warning(
                            "Acuity: invalid field ID %r in %s — skipping",
                            token, env_key,
                        )
            logger.info(
                "Acuity form fields for type %s: %s (env var %s)",
                raw_type_id, [f["id"] for f in required_fields], env_key,
            )
            self._required_fields_cache[raw_type_id] = required_fields
            return required_fields

        # ── 2. Auto-detection — fetch ALL forms and filter client-side ────────
        # The Acuity /api/v1/forms endpoint returns all forms for the account.
        # Each form has an `appointmentTypes` list.  Two cases apply this form
        # to our booking:
        #   a) appointmentTypes is empty / null  → "global" form, applies to ALL types
        #   b) appointmentTypes explicitly contains our raw_type_id
        _CHECKBOX_TYPES = {"checkbox", "checkboxlist", "signature", "yesno"}
        required_fields = []
        try:
            response = await self._request_with_retry("GET", "/forms")
            forms = response.json() if isinstance(response.json(), list) else []
            logger.info(
                "Acuity forms API: %d form(s) returned for type %s — %s",
                len(forms),
                raw_type_id,
                [
                    {"form_id": f.get("id"), "name": str(f.get("name", ""))[:60],
                     "appointmentTypes": f.get("appointmentTypes", [])}
                    for f in forms
                ],
            )
            for form in forms:
                form_type_ids = [str(t) for t in (form.get("appointmentTypes") or [])]
                # Empty list = global form (applies to all appointment types).
                # Non-empty list = only apply to the listed types.
                is_global = len(form_type_ids) == 0
                is_for_this_type = raw_type_id in form_type_ids
                if not (is_global or is_for_this_type):
                    continue
                for field in form.get("fields", []):
                    if not (
                        field.get("required")
                        and field.get("type", "").lower() in _CHECKBOX_TYPES
                    ):
                        continue
                    field_id = field["id"]
                    # Safety guard: if this is a global form but the field is
                    # known NOT to exist on this appointment type (discovered
                    # via a previous failed booking), skip it.  The correct
                    # field ID for this type can be set via env var override
                    # ACUITY_FORM_FIELDS_<TYPE_ID>.
                    _known_bad = {
                        # field 12885419 belongs to Alcester (15823699) only;
                        # injecting it for Redditch (33801703) causes a 400.
                        "33801703": {12885419},
                    }
                    if field_id in _known_bad.get(raw_type_id, set()):
                        logger.info(
                            "Acuity auto-detect: type=%s skipping field_id=%s "
                            "(known not present on this appointment type)",
                            raw_type_id, field_id,
                        )
                        continue
                    required_fields.append({"id": field_id, "value": "1"})
                    logger.info(
                        "Acuity auto-detect: type=%s form=%r (global=%s) "
                        "field_id=%s name=%r",
                        raw_type_id,
                        form.get("name", "")[:60],
                        is_global,
                        field_id,
                        field.get("name", "")[:80],
                    )
        except Exception as exc:
            logger.warning(
                "Acuity _get_required_form_fields failed (non-fatal): %r", exc
            )

        logger.info(
            "Acuity form fields for type %s: %s (auto-detected)",
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
