"""
Notifications subsystem for SMS and other alerts.
"""

from .sms import SMSService, send_sms

__all__ = ["SMSService", "send_sms"]
