"""
tests/test_emergency_reask_suppression.py
-----------------------------------------
F23 — the dead-air re-ask must NOT chirp "how can I help today?" right after an
emergency (999 / A&E) escalation.

Sweep finding F23 (docs/sweep_findings.md, Call 10): after firm 999/A&E
instructions, caller silence triggered _silence_safety_net's generic reset
"Sorry, I can't quite hear you — how can I help today?", which undercuts the
gravity of the emergency.

medical_emergency_detected is never set on the LLM red-flag path (the 999 text
is model-generated), so the reliable signal is the content of the last response:
last_bot_prompt holds the full reply and contains 999 / A and E / emergency
service. _emergency_reask_override returns a calm re-anchor in that case, else
None (normal turns are unaffected).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.media_streams.connection import WebSocketCallHandler


def test_emergency_last_response_overrides_chirpy_reask():
    session = {
        "last_bot_prompt": (
            "Please call 999 or go to A and E straight away. We're not an "
            "emergency service, so please don't wait."
        )
    }
    phrase = WebSocketCallHandler._emergency_reask_override(session)
    assert phrase is not None
    assert "999" in phrase
    assert "how can i help today" not in phrase.lower()


def test_ae_ampersand_marker_detected():
    session = {"last_bot_prompt": "This is a medical emergency — go to A&E now."}
    assert WebSocketCallHandler._emergency_reask_override(session) is not None


def test_normal_prompt_no_override():
    session = {"last_bot_prompt": "Which day or time works best for you?"}
    assert WebSocketCallHandler._emergency_reask_override(session) is None


def test_empty_prompt_no_override():
    assert WebSocketCallHandler._emergency_reask_override({}) is None
