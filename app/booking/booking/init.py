"""
Booking subsystem for healthcare automation.

Provides unified interface for scheduling appointments across multiple providers
(Google Calendar, Acuity Scheduling) with idempotency, caching, and observability.
"""

from .models import Slot, Booking, AppointmentType, Practitioner, BookingRequest, InsuranceInfo
from .service import BookingService
from .exceptions import (
    BookingError,
    ProviderAuthError,
    ProviderRateLimited,
    ProviderUnavailable,
    SlotUnavailable,
)

__all__ = [
    "Slot",
    "Booking",
    "AppointmentType",
    "Practitioner",
    "BookingRequest",
    "InsuranceInfo",
    "BookingService",
    "BookingError",
    "ProviderAuthError",
    "ProviderRateLimited",
    "ProviderUnavailable",
    "SlotUnavailable",
]
