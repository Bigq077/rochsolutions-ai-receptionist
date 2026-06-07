"""
Google Calendar provider adapter (wrapper around existing implementation).
Placeholder - not needed for Theorem which uses Acuity.
"""

import logging
from typing import List, Optional
from datetime import datetime, date

from ..models import Slot, Booking, AppointmentType, Practitioner, BookingRequest
from ..exceptions import ProviderUnavailable

logger = logging.getLogger(__name__)


class GoogleCalendarAdapter:
    """
    Placeholder wrapper for Google Calendar.
    Not needed for Theorem Health (uses Acuity).
    """
    
    def __init__(self, calendar_id: str, clinic_id: str):
        raise NotImplementedError("Google Calendar adapter not implemented for Theorem")
    
    async def get_appointment_types(self) -> List[AppointmentType]:
        raise NotImplementedError()
    
    async def get_practitioners(self) -> List[Practitioner]:
        raise NotImplementedError()
    
    async def get_available_slots(
        self,
        appointment_type_id: str,
        start_date: date,
        end_date: date,
        practitioner_id: Optional[str] = None,
    ) -> List[Slot]:
        raise NotImplementedError()
    
    async def create_booking(self, request: BookingRequest) -> Booking:
        raise NotImplementedError()
    
    async def cancel_booking(self, provider_booking_id: str) -> bool:
        raise NotImplementedError()
