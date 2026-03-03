"""
Shared utility functions for the AI Receptionist application.
"""
from __future__ import annotations

import re
from typing import Optional

# ============================================================================
# Phone number validation / normalisation (#14)
# ============================================================================

# E.164 pattern: starts with +, then 7-15 digits (ITU-T E.164 spec).
_E164_RE = re.compile(r"^\+[1-9]\d{6,14}$")


def is_valid_e164(phone: str) -> bool:
    """
    Return True if ``phone`` is a valid E.164 phone number.

    E.164 format: ``+<country_code><number>`` with 7–15 digits total (no spaces,
    no dashes).  Examples: ``+447712345678``, ``+12025551234``.
    """
    if not phone:
        return False
    return bool(_E164_RE.match(phone))


def normalise_to_e164(raw: str, default_country: str = "GB") -> Optional[str]:
    """
    Best-effort conversion of a user-supplied phone number to E.164 format.

    Handles:
    - UK mobiles in national format: ``07xxx xxxxxx``  →  ``+447xxxxxxxxxx``
    - International without leading ``+``:  ``447xxxxxxxxxx``  →  ``+447xxxxxxxxxx``
    - Numbers already in E.164:  ``+447xxxxxxxxxx``  →  unchanged

    Returns the normalised E.164 string, or None if the number cannot be
    converted to a valid E.164 format.
    """
    if not raw:
        return None

    # Strip all non-digit characters except a leading +
    stripped = raw.strip()
    plus_prefix = stripped.startswith("+")
    digits = re.sub(r"\D", "", stripped)

    if not digits:
        return None

    if default_country == "GB":
        if digits.startswith("07") and len(digits) == 11:
            # UK mobile national format → E.164
            candidate = "+44" + digits[1:]
        elif digits.startswith("447") and 11 <= len(digits) <= 13:
            # Already international, just needs a +
            candidate = "+" + digits
        elif digits.startswith("44") and 10 <= len(digits) <= 13:
            candidate = "+" + digits
        elif plus_prefix:
            candidate = "+" + digits
        else:
            # Last-resort: hope it's already usable
            candidate = "+" + digits
    else:
        # Non-GB: just prepend + if not already present
        candidate = "+" + digits if not plus_prefix else "+" + digits

    return candidate if is_valid_e164(candidate) else None
