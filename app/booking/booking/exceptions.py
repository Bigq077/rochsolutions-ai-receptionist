"""
Exception hierarchy for booking subsystem.
"""


class BookingError(Exception):
    """Base exception for booking subsystem."""
    
    def __init__(self, message: str, provider: str = None, safe_metadata: dict = None):
        super().__init__(message)
        self.provider = provider
        self.safe_metadata = safe_metadata or {}


class ProviderAuthError(BookingError):
    """Provider authentication/authorization failed (401, 403)."""
    pass


class ProviderRateLimited(BookingError):
    """Provider rate limit exceeded (429)."""
    
    def __init__(self, message: str, provider: str = None, retry_after: int = None, safe_metadata: dict = None):
        super().__init__(message, provider, safe_metadata)
        self.retry_after = retry_after  # seconds


class ProviderUnavailable(BookingError):
    """Provider service unavailable (5xx, timeout, network error)."""
    pass


class SlotUnavailable(BookingError):
    """Requested slot is no longer available (race condition or stale cache)."""
    pass


class InvalidBookingRequest(BookingError):
    """Booking request validation failed."""
    pass
