"""CA66bd0930 (14 Sep 2026, JV, live). OPEN_DEFECTS_2026-09-14.md rows #3 / #3b.

    Susie   "Anything else you'd like to know?"
    Caller  "yes i am a private patient"
    Susie   "Got it — which insurer are you with?"          <-- #3b: never raised
    Caller  "my buka"
    Model   book_appointment(...)  BLOCKED (already booked)  <-- #3: wrong tool
    Susie   "Noted — that's Bupa. I'll make sure Marcus gets that…"
    Gate5cb "One moment — I'll get that logged for Jonathan now."   (#2)
            … nothing followed. No tool call, no text, no note.

#3b — a private patient in UK physio is a self-payer. The insurer question
is asked only when the caller's OWN words raise insurance. Engine half:
Gate 5ins in turn_handler. Prompt half: the trigger is now defined.

#3 — after the booking there is no followup_note to attach the insurer to.
The already-done rule and the prompt now point at request_callback, and
CALL STATE carries the unfulfilled promise until a callback write confirms.
"""
from unittest.mock import patch

import pytest

from app.media_streams.turn_handler import (
    _INSURER_GATE_RESIDUE,
    _apply_callback_promise_gate,
    _apply_insurer_question_gate,
    sanitise_response,
)

INSURER_Q = "Got it — which insurer are you with?"


# ── #3b, the engine half ────────────────────────────────────────────────────

@pytest.mark.parametrize("caller", [
    "yes i am a private patient",
    "i'm self funding",
    "i'll be paying myself",
    "yes",
])
def test_the_insurer_question_is_stripped_when_insurance_was_never_raised(caller):
    s = {"_turn_user_text": caller, "conversation_history": []}
    out = _apply_insurer_question_gate(INSURER_Q, s)
    assert "insurer" not in out.lower()
    assert "?" in out, "a turn that asks nothing is dead air"
    assert out == _INSURER_GATE_RESIDUE


def test_substantive_content_survives_the_strip():
    s = {"_turn_user_text": "private patient"}
    out = _apply_insurer_question_gate(
        "Noted. Which insurer are you with? Is that the best number for you?", s
    )
    assert out == "Noted. Is that the best number for you?"
    assert "_gate5ins_substituted" not in s


@pytest.mark.parametrize("caller", [
    "i want to use my insurance",
    "i'm with bupa",
    "it's through my work",
    "can i claim this on my policy",
    "do you take aviva",
])
def test_the_insurer_question_stands_when_the_caller_raised_it(caller):
    s = {"_turn_user_text": caller}
    assert _apply_insurer_question_gate(INSURER_Q, s) == INSURER_Q


def test_insurance_raised_earlier_in_the_call_still_counts():
    s = {"_turn_user_text": "yes please",
         "conversation_history": [{"role": "user", "content": "i've got private insurance"}]}
    assert _apply_insurer_question_gate(INSURER_Q, s) == INSURER_Q


def test_sanitise_wires_gate_5ins():
    s = {"_turn_user_text": "yes i am a private patient", "clinic_id": "jv_v1"}
    out = sanitise_response(INSURER_Q, s)
    assert "insurer" not in out.lower()


# ── #3, the promise ─────────────────────────────────────────────────────────

def test_the_resteer_leaves_the_promise_outstanding_until_a_write_confirms():
    from app.prompts.clinic_template_prompt import _b7_call_state
    from app.clinic_config import get_clinic
    clinic = get_clinic("jv_v1")
    s = {"clinic_id": "jv_v1", "collected": {}}
    _apply_callback_promise_gate(
        "Marcus will be in touch about the Bupa details — you're all sorted.", s
    )
    assert s.get("_callback_promise_outstanding") is True
    state = _b7_call_state(s, clinic, {"practitioner": "Marcus", "clinic_name": "JV"})
    assert "NOT LOGGED" in state and "request_callback" in state and "Marcus" in state

    s["callback_write_confirmed"] = True
    state = _b7_call_state(s, clinic, {"practitioner": "Marcus", "clinic_name": "JV"})
    assert "NOT LOGGED" not in state


def test_the_already_booked_rule_points_at_request_callback():
    from app.media_streams.llm_stream import _WRITE_ALREADY_DONE_RULE, WRITE_FAMILY_BOOKING
    assert "request_callback" in _WRITE_ALREADY_DONE_RULE[WRITE_FAMILY_BOOKING]


# ── #3b + #3, the prompt half ───────────────────────────────────────────────

def test_the_jv_prompt_defines_the_trigger_and_the_post_booking_path():
    from app.prompts.clinic_template_prompt import build_clinic_prompt
    from app.clinic_config import get_clinic
    text = " ".join(build_clinic_prompt({"clinic_id": "jv_v1"}, get_clinic("jv_v1")))
    assert "'Private patient', 'self-funding', 'paying myself' mean SELF-PAY" in text
    assert "If insurance comes up AFTER the appointment is already booked" in text
    assert "request_callback" in text
