# tests/regression/test_call_state_asserts_the_agreed_slot.py
"""
CALL STATE must tell the model which slot the caller has agreed to.

CA6e1024db (2 Aug 2026), the only caller lost mid-booking in the 2 Aug sweep.
Name, phone and slot were all collected; the model then said

    "Wait — I don't actually have a slot confirmed yet. Do you have a
     preference for when you'd like to come in…"

and restarted the booking from the timing question. The caller hung up.

That sentence is nowhere in the codebase — it was generated. And it was
generatable because CALL STATE never asserted the agreed slot:
v3_confirmed_slot_phrase was read in exactly one place in the prompt layer
(the PHONE STEP OUTSTANDING steer) and only as a BOOLEAN. Its content was
never shown to the model, so the model's sole anchor was conversation history
and the engine had no mechanism to correct it when that history misled.

On this call the history was corrupted by the keypad-rejection defect fixed in
b922675: an unresolved read-back rejection, nine discarded digits, a spurious
"Sorry — I can't quite hear you", and a spoken number nobody acknowledged.
b922675 removes that particular corruption; this assertion closes the gap it
exposed, which is independent of it — any other history confusion reproduces
the same loss.

Suppressed while v3_slot_phrase_superseded is set: there the caller has asked
for a different day and the phrase names the day they are LEAVING. Asserting
it would drive the model back onto an abandoned day — CAb81fe651, where three
callers heard the wrong day and hung up. Deliberately the same signal the
Gate-5 date guard uses, so the two can never disagree.
"""
from __future__ import annotations

import pytest

from app.prompts.clinic_template_prompt import _b7_call_state

SLOT = "Saturday the 8th of August at half past ten in the morning"


def _state(**over) -> str:
    session = dict({"v3_confirmed_slot_phrase": SLOT}, **over)
    return _b7_call_state(session, {}, {})


class TestTheAgreedSlotIsAsserted:
    def test_the_slot_phrase_reaches_the_model(self):
        out = _state()
        assert "SLOT ALREADY AGREED" in out
        assert SLOT in out

    def test_the_model_is_told_not_to_deny_the_slot(self):
        """The exact failure: the model announced it had no slot."""
        assert "NEVER say you have no slot confirmed" in _state()

    def test_it_does_not_ask_for_a_day_or_time_again(self):
        assert "Do NOT ask for a day or time again" in _state()

    def test_a_change_of_mind_is_still_allowed(self):
        """The assertion must not trap a caller who wants a different slot —
        that would trade a lost booking for a wrong one."""
        assert "change it" in _state()


class TestItIsSilentWhenItCannotBeSure:
    def test_no_confirmed_phrase_renders_nothing(self):
        session = {}
        assert "SLOT ALREADY AGREED" not in _b7_call_state(session, {}, {})

    def test_an_empty_phrase_renders_nothing(self):
        for empty in ("", "   ", None):
            assert "SLOT ALREADY AGREED" not in _state(
                v3_confirmed_slot_phrase=empty
            )

    def test_a_superseded_phrase_is_never_asserted(self):
        """CAb81fe651. The caller has asked for a different day, so the phrase
        names the day they are leaving. Asserting it would push the model back
        onto an abandoned day."""
        out = _state(v3_slot_phrase_superseded=True)
        assert "SLOT ALREADY AGREED" not in out
        assert SLOT not in out

    def test_it_reappears_once_the_new_day_is_captured(self):
        """The supersede flag is cleared on capture/refresh, so the assertion
        must come back on the NEW slot rather than staying suppressed."""
        new = "Tuesday the 11th of August at nine in the morning"
        out = _state(v3_confirmed_slot_phrase=new, v3_slot_phrase_superseded=False)
        assert "SLOT ALREADY AGREED" in out
        assert new in out


class TestItDoesNotDisturbTheExistingState:
    def test_the_phone_steer_still_renders_alongside_it(self):
        """Both can be true at once — a slot agreed and the phone outstanding.
        The new line must not suppress or be suppressed by the phone steer."""
        session = {
            "v3_confirmed_slot_phrase": SLOT,
            "patient_name": "Clementine",
            "booking_flow_active": True,
        }
        out = _b7_call_state(session, {}, {})
        assert "SLOT ALREADY AGREED" in out
        assert "PHONE STEP OUTSTANDING" in out

    def test_a_first_turn_session_is_untouched(self):
        """An empty session renders the GREETING line and nothing else — the
        new assertion must not appear before a slot has been agreed."""
        out = _b7_call_state({}, {}, {})
        assert "GREETING" in out
        assert "SLOT ALREADY AGREED" not in out

    @pytest.mark.parametrize("phrase", [SLOT, "Friday the 7th at six"])
    def test_the_block_is_still_a_single_call_state_string(self, phrase):
        out = _state(v3_confirmed_slot_phrase=phrase)
        assert out.startswith("CALL STATE: ")
        assert out.count("CALL STATE:") == 1
