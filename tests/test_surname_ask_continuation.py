"""Context-aware continuation after a suppressed surname-ask (end-of-flow name).

When the caller gives only a first name and the LLM's whole reply is
"Thanks [name] — and your surname?", the surname-ask is suppressed (owner
policy: never audibly re-ask the surname).  Under the end-of-flow name design
the name is collected right before the phone step, so the spoken continuation
must drive the PHONE question — not the old name-first "how can I help you
today?", which looped the caller back to the greeting (live call 2026-07-15).
"""
from app.media_streams.llm_stream import (
    _surname_ask_continuation,
    _rewrite_surname_ask_reply,
    _SURNAME_ASK_CONTINUATION,
)

USE_NUM = (
    "Is the number you're calling on the best one for your booking? "
    "If so, just say use this number."
)
KEYPAD = (
    "Could you type your number on your keypad? You can press the "
    "star key to reset at any time."
)


class TestContinuationSelection:
    def test_booking_with_caller_id_offers_use_this_number(self):
        s = {"booking_flow_active": True, "phone_confirmed": False,
             "twilio_from_local": "07502211207"}
        assert _surname_ask_continuation(s) == USE_NUM

    def test_booking_without_caller_id_sends_to_keypad(self):
        s = {"booking_flow_active": True, "phone_confirmed": False}
        assert _surname_ask_continuation(s) == KEYPAD

    def test_phone_already_confirmed_falls_back(self):
        s = {"booking_flow_active": True, "phone_confirmed": True}
        assert _surname_ask_continuation(s) == _SURNAME_ASK_CONTINUATION

    def test_not_in_booking_falls_back(self):
        assert _surname_ask_continuation({}) == _SURNAME_ASK_CONTINUATION


class TestReplyRewrite:
    def test_bare_ack_plus_surname_ask_advances_to_phone(self):
        reply = "Thanks Quentin — and could I take your surname?"
        new, changed = _rewrite_surname_ask_reply(reply, USE_NUM)
        assert changed
        assert new == f"Thanks Quentin. {USE_NUM}"
        assert "how can I help" not in new  # the bug: no greeting loop

    def test_default_continuation_is_legacy_line(self):
        reply = "Thanks Quentin — and your surname?"
        new, _ = _rewrite_surname_ask_reply(reply)
        assert new == f"Thanks Quentin. {_SURNAME_ASK_CONTINUATION}"

    def test_no_surname_ask_left_untouched(self):
        reply = "Thanks Quentin — is the number you're calling on the best one?"
        new, changed = _rewrite_surname_ask_reply(reply, USE_NUM)
        assert changed is False
        assert new == reply
