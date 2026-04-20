"""
Focused tests for keypad (DTMF) phone-capture hardening.

Covers:
  - normal keypad 11-digit completion routes to CONFIRM_PHONE
  - explicit "reset the keypad" intent clears buffers, stays in keypad mode,
    re-prompts with the reset acknowledgement
  - explicit voice-switch ("can I say it instead") intent exits keypad mode,
    clears buffers, emits the voice-recommendation prompt
  - booking / reschedule / cancel flows all honour the above

Run with:  pytest tests/test_keypad_phone_capture.py -v
"""
from __future__ import annotations

import copy
from typing import Any, Dict, List, Tuple

import pytest

pytestmark = pytest.mark.asyncio


class _FakeTTS:
    def __init__(self):
        self.items: List[str] = []
    async def put(self, text: str) -> None:
        self.items.append(text)
    def last(self) -> str:
        return self.items[-1] if self.items else ""
    def all_text(self) -> str:
        return " | ".join(self.items)


async def _noop_llm(instruction: str, allow_tools: bool = True) -> str:
    return ""


def _make_engine(flow_name: str, extra: Dict[str, Any] | None = None) -> Tuple[Any, _FakeTTS]:
    """
    Build a FlowEngine positioned at COLLECT_PHONE with phone_awaiting_dtmf=True,
    for any of the three flows (booking / reschedule / cancel).
    """
    from app.media_streams.flow import (
        FlowEngine,
        BOOKING_FLOW,
        RESCHEDULE_FLOW,
        CANCEL_FLOW,
        _COLLECT_PHONE_INDEX,
        _RESCHEDULE_COLLECT_PHONE_INDEX,
        _CANCEL_COLLECT_PHONE_INDEX,
    )
    from app.media_streams.session import DEFAULT_MS_SESSION

    flow_map = {
        "booking":    (BOOKING_FLOW,    _COLLECT_PHONE_INDEX),
        "reschedule": (RESCHEDULE_FLOW, _RESCHEDULE_COLLECT_PHONE_INDEX),
        "cancel":     (CANCEL_FLOW,     _CANCEL_COLLECT_PHONE_INDEX),
    }
    flow, idx = flow_map[flow_name]

    session = copy.deepcopy(DEFAULT_MS_SESSION)
    session.update({
        "full_name":           "Jane Smith",
        "state":               "COLLECT_PHONE",
        "flow_state":          "COLLECT_PHONE",
        "flow_step":           idx,
        "phone_from_twilio":   True,
        "phone_confirmed":     False,
        "phone_confirm_armed": False,
        "phone_dtmf_buffer":   "",
        "phone_digits_buffer": "",
        "phone_awaiting_dtmf": True,
        "selected_location":   "alcester",
    })
    if extra:
        session.update(extra)

    tts = _FakeTTS()
    engine = FlowEngine(session, tts, _noop_llm)
    engine._active_flow = flow
    engine._intent_detected = True
    return engine, tts


# ---------------------------------------------------------------------------
# 1. Normal keypad completion (synthetic 11-digit transcript routed to flow)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("flow_name", ["booking", "reschedule", "cancel"])
async def test_keypad_completion_arms_confirm_phone(flow_name):
    engine, _tts = _make_engine(flow_name)
    await engine.handle_transcript("07912345678")
    assert engine.session.get("state") == "CONFIRM_PHONE"
    assert engine.session.get("phone_confirm_armed") is True
    assert engine.session.get("phone_readback_pending") is True


# ---------------------------------------------------------------------------
# 2. Reset-keypad intent — explicit phrases
# ---------------------------------------------------------------------------

RESET_PHRASES = [
    "i have just realised i made a mistake",
    "i made a mistake",
    "reset the keypad",
    "can you reset it",
    "start again",
    "clear that",
]

@pytest.mark.parametrize("phrase", RESET_PHRASES)
@pytest.mark.parametrize("flow_name", ["booking", "reschedule", "cancel"])
async def test_reset_keypad_intent(flow_name, phrase):
    engine, tts = _make_engine(flow_name, {"phone_dtmf_buffer": "0794"})
    await engine.handle_transcript(phrase)

    # Buffer cleared, keypad mode preserved, not advanced to CONFIRM_PHONE
    assert engine.session.get("phone_dtmf_buffer", "") == ""
    assert engine.session.get("phone_digits_buffer", "") == ""
    assert engine.session.get("phone_awaiting_dtmf") is True
    assert engine.session.get("state") == "COLLECT_PHONE"
    assert engine.session.get("phone_confirmed") is False

    # Acknowledgement phrasing — may be emitted via TTS (hard-gate path) or
    # set on last_question (global-repair path that relies on repair_requested).
    last = (tts.last() or engine.session.get("last_question", "")).lower()
    assert "reset" in last
    assert "keypad" in last


# ---------------------------------------------------------------------------
# 3. Voice-switch intent
# ---------------------------------------------------------------------------

VOICE_SWITCH_PHRASES = [
    "i'll say it instead",
    "can i say it",
    "i'd rather say it",
    "i want to say it",
    "let me say it",
]

@pytest.mark.parametrize("phrase", VOICE_SWITCH_PHRASES)
@pytest.mark.parametrize("flow_name", ["booking", "reschedule", "cancel"])
async def test_voice_switch_intent(flow_name, phrase):
    engine, tts = _make_engine(flow_name, {"phone_dtmf_buffer": "0794"})
    await engine.handle_transcript(phrase)

    # Keypad buffer cleared, keypad mode cleanly exited
    assert engine.session.get("phone_dtmf_buffer", "") == ""
    assert engine.session.get("phone_awaiting_dtmf") is False
    assert engine.session.get("phone_voice_attempts", 0) == 0
    # Still in COLLECT_PHONE — spoken collection begins from scratch
    assert engine.session.get("state") == "COLLECT_PHONE"

    # Acknowledgement: must acknowledge "say it" and recommend keypad
    last = tts.last().lower()
    assert "say it" in last
    assert "keypad" in last


# ---------------------------------------------------------------------------
# 4. Voice-switch is NOT mistaken for a digit entry
# ---------------------------------------------------------------------------

async def test_voice_switch_not_digit_fallback():
    engine, tts = _make_engine("reschedule", {"phone_dtmf_buffer": ""})
    await engine.handle_transcript("can i say it instead")
    # Must NOT have routed to CONFIRM_PHONE
    assert engine.session.get("state") == "COLLECT_PHONE"
    # Must NOT have used the generic "say the full number" fallback
    assert "rather say it" not in tts.last().lower() or "recommend" in tts.last().lower()


# ---------------------------------------------------------------------------
# 5. Reset intent does not leak digits into a subsequent entry
# ---------------------------------------------------------------------------

async def test_reset_then_fresh_entry_has_only_new_digits():
    engine, _tts = _make_engine("reschedule", {"phone_dtmf_buffer": "0794"})
    await engine.handle_transcript("reset the keypad")
    assert engine.session.get("phone_dtmf_buffer", "") == ""

    # Simulate connection.py pushing a synthetic transcript after the caller
    # types a full 11-digit number.
    await engine.handle_transcript("07700900123")

    assert engine.session.get("state") == "CONFIRM_PHONE"
    captured = (
        engine.session.get("phone_candidate")
        or engine.session.get("phone_number")
        or engine.session.get("phone")
        or ""
    )
    assert "0794" not in captured
    assert captured.endswith("07700900123") or captured == "07700900123"
