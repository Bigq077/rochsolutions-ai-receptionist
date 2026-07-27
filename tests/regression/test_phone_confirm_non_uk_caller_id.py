# tests/regression/test_phone_confirm_non_uk_caller_id.py
"""
Non-UK caller-ID could never voice-confirm its number → book_appointment looped.

Reproduced 2026-07-26 on `latency-eval` (verification sweep, Block A gate, 0/3
booked). The caller was on a French `+33` number. Susie read it back and asked
"is that the best number for the booking?"; the caller said "yes"; she re-read the
number and asked again — 23 times across two calls, never booking.

Chain:
  * `connection.py` line 5421 only sets `twilio_from_local` when the caller-ID
    starts with `+44`. For a `+33` number it stays empty.
  * The verbal phone-confirm interceptor (the `v3_phone_dtmf_active` branch) took
    the number to store from `twilio_from_local` alone. Empty → the
    `_caller_num and _is_use_this_number(...)` guard was falsy → the "yes" fell
    through to the conversational-exit branch → `phone_confirmed` never set.
  * `f302ddb` (shipped the same afternoon) then hard-requires `phone_confirmed is
    True` before booking → `[book] BLOCKED — phone not confirmed (A1)` → the model
    re-read the number → unbounded loop.

`_is_use_this_number("yes")` was never the problem (a bare yes is accepted). The
break was purely that the caller-ID number resolved to "" for non-UK numbers.

Fix: `_confirm_caller_number(session)` prefers the UK-local form but falls back to
the full E.164 caller-ID, so any caller-ID number can be voice-confirmed. The UK
path is unchanged (local form still preferred), so this only affects non-UK IDs.
"""
from __future__ import annotations

from app.media_streams.connection import _confirm_caller_number


def test_uk_number_prefers_local_form_unchanged():
    """UK path must be byte-for-byte unchanged: the 0-prefixed local form wins."""
    session = {"twilio_from_local": "07700900456", "twilio_from": "+447700900456"}
    assert _confirm_caller_number(session) == "07700900456"


def test_non_uk_number_falls_back_to_e164():
    """The regression: a +33 caller-ID has no local form → must fall back to E.164
    so the voice confirm can set phone_confirmed instead of looping."""
    session = {"twilio_from": "+33617769867"}  # no twilio_from_local (non-+44)
    assert _confirm_caller_number(session) == "+33617769867"


def test_empty_local_string_still_falls_back():
    """twilio_from_local present but empty (the actual failing-call state)."""
    session = {"twilio_from_local": "", "twilio_from": "+33617769867"}
    assert _confirm_caller_number(session) == "+33617769867"


def test_no_caller_id_returns_empty():
    """No caller-ID at all → empty, so the confirm guard stays falsy (keypad path)."""
    assert _confirm_caller_number({}) == ""
    assert _confirm_caller_number({"twilio_from_local": "", "twilio_from": ""}) == ""
