"""Regression guard for the deterministic booking phone step (Phase 1).

The booking phone-confirm question is otherwise LLM-driven (the PHONE HAND-OFF
prompt block) and the LLM sometimes skips it after a slot is chosen, jumping
straight to "shall I book that in?" — the caller then has to prompt for it
(live call 2026-07-12 19:14). Phase 1 injects the question deterministically.

The injector lives deep in the async transcript loop, but its correctness
hinges on ONE portable invariant: the wording of `_booking_phone_q()` must
carry the exact tokens the downstream handlers parse. If that drifts, the
caller's "use this number" reply is never resolved and the booking is lost:

  • caller-ID present → must contain "number you're calling on" AND
    "use this number" (the booking verbal-confirm handler ~connection.py:5603
    keys off these), and must NOT mention the keypad.
  • no caller-ID → must contain "keypad" (the keypad auto-activate path keys
    off that word), and must NOT offer the meaningless "use this number"
    shortcut (the caller's number never reached us).

We call the unbound method against a minimal stub so the test stays free of
the class's heavy constructor.
"""
from __future__ import annotations

from app.media_streams.connection import WebSocketCallHandler


class _Stub:
    """Minimal carrier for the only attribute _booking_phone_q reads."""
    def __init__(self, session: dict):
        self.session = session


def _phone_q(session: dict) -> str:
    return WebSocketCallHandler._booking_phone_q(_Stub(session))


def test_caller_id_present_offers_use_this_number():
    q = _phone_q({"twilio_from_local": "+447502211207"}).lower()
    assert "number you're calling on" in q
    assert "use this number" in q
    assert "keypad" not in q


def test_no_caller_id_routes_to_keypad():
    q = _phone_q({}).lower()
    assert "keypad" in q
    # The "use this number" shortcut is meaningless with no caller-ID on file.
    assert "use this number" not in q


def test_wording_is_a_single_question_each():
    # Exactly one question mark — the PHONE HAND-OFF lines are each a single
    # question; extra sentences would desync the downstream parsers.
    assert _phone_q({"twilio_from_local": "+447502211207"}).count("?") == 1
    assert _phone_q({}).count("?") == 1
