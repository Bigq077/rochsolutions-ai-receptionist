"""
Provider interface definition.
"""

from typing import Protocol, List, Optional
from datetime import datetime, date

from .models import Slot, Booking, AppointmentType, Practitioner, BookingRequest


class BookingProvider(Protocol):
    """
    Protocol defining the interface all booking providers must implement.
    
    Providers are responsible for:
    - Making API calls to their respective services
    - Mapping responses to our domain models
    - Raising appropriate exceptions from our exception hierarchy
    - Handling provider-specific authentication
    
    Providers should NOT:
    - Implement caching (handled by BookingService)
    - Implement idempotency (handled by BookingService)
    - Implement locking (handled by BookingService)
    """
    
    async def get_appointment_types(self) -> List[AppointmentType]:
        """Fetch available appointment types."""
        ...
    
    async def get_practitioners(self) -> List[Practitioner]:
        """Fetch available practitioners (optional for simple providers)."""
        ...
    
    async def get_available_slots(
        self,
        appointment_type_id: str,
        start_date: date,
        end_date: date,
        practitioner_id: Optional[str] = None,
    ) -> List[Slot]:
        """
        Fetch available slots for given appointment type and date range.
        
        Args:
            appointment_type_id: ID of appointment type
            start_date: Start of search range (inclusive)
            end_date: End of search range (inclusive)
            practitioner_id: Optional filter by specific practitioner
            
        Returns:
            List of available slots (timezone-aware Europe/London)
        """
        ...
    
    async def create_booking(self, request: BookingRequest) -> Booking:
        """
        Create a new booking.
        
        This should be idempotent at the provider level if possible.
        BookingService handles additional idempotency layer.
        
        Args:
            request: Booking request with patient details and slot
            
        Returns:
            Confirmed booking
            
        Raises:
            SlotUnavailable: If slot is no longer available
            ProviderAuthError: If authentication fails
            ProviderRateLimited: If rate limit hit
            ProviderUnavailable: If provider service down
        """
        ...
    
    async def cancel_booking(self, provider_booking_id: str) -> bool:
        """
        Cancel an existing booking.
        
        Args:
            provider_booking_id: Provider's booking reference
            
        Returns:
            True if cancelled successfully
        """
        ...
