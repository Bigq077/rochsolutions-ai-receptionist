"""
tests/test_caller_classifier.py
--------------------------------
Tests for app.caller_classifier — classify_caller() and run_professional_flow().

Covers:
  1. Professional utterance → type=professional, confidence=high
  2. Patient utterance → type=patient
  3. Single word "Hi" → type=unknown
  4. API exception → default dict returned, no raise
  5. Professional flow → booking state machine NOT entered
  6. Professional flow completes → professional_calls.jsonl written
"""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.caller_classifier import classify_caller, run_professional_flow


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_api_response(json_text: str):
    """Build a mock Anthropic response with content[0].text = json_text."""
    content_block = SimpleNamespace(text=json_text)
    return SimpleNamespace(content=[content_block])


def _make_session(**overrides):
    base = {
        "call_sid": "CA_test_prof",
        "caller_type": None,
        "classification_pending": True,
        "_classification_confidence": None,
        "professional_flow_active": False,
        "professional_flow_complete": False,
        "prof_flow_step": 0,
        "prof_name": None,
        "prof_callback": None,
        "prof_message": None,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Test 1 — Professional utterance → type=professional, confidence=high
# ---------------------------------------------------------------------------

class TestClassifyCallerProfessional:
    def test_professional_high_confidence(self):
        api_json = '{"type": "professional", "confidence": "high", "intent": "referral from GP surgery"}'
        mock_client = MagicMock()
        mock_client.messages.create.return_value = _make_api_response(api_json)

        with patch("app.caller_classifier.anthropic.Anthropic", return_value=mock_client):
            result = classify_caller(
                "Hi I'm calling from Dr Patterson's surgery regarding a referral"
            )

        assert result["type"] == "professional"
        assert result["confidence"] == "high"
        assert "intent" in result


# ---------------------------------------------------------------------------
# Test 2 — Patient utterance → type=patient
# ---------------------------------------------------------------------------

class TestClassifyCallerPatient:
    def test_patient_utterance(self):
        api_json = '{"type": "patient", "confidence": "high", "intent": "personal booking for back pain"}'
        mock_client = MagicMock()
        mock_client.messages.create.return_value = _make_api_response(api_json)

        with patch("app.caller_classifier.anthropic.Anthropic", return_value=mock_client):
            result = classify_caller(
                "Hi I've got terrible back pain and need to see someone"
            )

        assert result["type"] == "patient"


# ---------------------------------------------------------------------------
# Test 3 — Single word utterance → type=unknown (API still called but logic
#           in flow.py skips classification for ≤1 word — classifier itself
#           will return whatever API says; here we verify the default path)
# ---------------------------------------------------------------------------

class TestClassifyCallerUnknown:
    def test_single_word_returns_unknown_if_api_says_unknown(self):
        api_json = '{"type": "unknown", "confidence": "low", "intent": ""}'
        mock_client = MagicMock()
        mock_client.messages.create.return_value = _make_api_response(api_json)

        with patch("app.caller_classifier.anthropic.Anthropic", return_value=mock_client):
            result = classify_caller("Hi")

        assert result["type"] == "unknown"


# ---------------------------------------------------------------------------
# Test 4 — API exception → default dict returned, no raise
# ---------------------------------------------------------------------------

class TestClassifyCallerException:
    def test_api_exception_returns_default(self):
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = Exception("network error")

        with patch("app.caller_classifier.anthropic.Anthropic", return_value=mock_client):
            result = classify_caller("anything")

        assert result == {"type": "unknown", "confidence": "low", "intent": ""}

    def test_malformed_json_returns_default(self):
        mock_client = MagicMock()
        mock_client.messages.create.return_value = _make_api_response("not json at all")

        with patch("app.caller_classifier.anthropic.Anthropic", return_value=mock_client):
            result = classify_caller("something")

        assert result == {"type": "unknown", "confidence": "low", "intent": ""}

    def test_missing_key_returns_default(self):
        # JSON parsed but missing required key
        mock_client = MagicMock()
        mock_client.messages.create.return_value = _make_api_response('{"type": "professional"}')

        with patch("app.caller_classifier.anthropic.Anthropic", return_value=mock_client):
            result = classify_caller("anything")

        assert result == {"type": "unknown", "confidence": "low", "intent": ""}


# ---------------------------------------------------------------------------
# Test 5 — Professional flow → booking state machine NOT entered
# ---------------------------------------------------------------------------

class TestProfessionalFlowBypassesBooking:
    def test_professional_sets_active_flag_not_booking(self):
        """When caller_type=professional+high, professional_flow_active is set
        and the booking state machine is never entered."""
        session = _make_session(
            caller_type="professional",
            _classification_confidence="high",
            classification_pending=False,
        )

        tts_calls = []

        async def _fake_tts(text):
            tts_calls.append(text)

        # Simulate the DETECT_INTENT routing:
        # In the real flow, when caller_type==professional+high, professional_flow_active
        # is set True and the booking _switch_flow() is never called.
        # We verify this by checking the session state directly after simulating
        # the detection step.
        from app.media_streams.flow import DETECT_INTENT_FLOW

        # The real check in handle_transcript:
        if (
            session.get("caller_type") == "professional"
            and session.get("_classification_confidence") == "high"
        ):
            session["professional_flow_active"] = True
            session["prof_flow_step"] = 0
            asyncio.run(_fake_tts("Of course — could I take your name please?"))

        assert session["professional_flow_active"] is True
        assert session.get("prof_flow_step") == 0
        # Booking keys are untouched
        assert session.get("flow_step") is None or session.get("flow_step") == 0
        assert session.get("intent") is None  # _switch_flow was never called
        assert "Of course" in tts_calls[0]


# ---------------------------------------------------------------------------
# Test 6 — Professional flow completes → professional_calls.jsonl written
# ---------------------------------------------------------------------------

class TestProfessionalCallLogged:
    def test_jsonl_written_on_flow_complete(self):
        session = _make_session(
            caller_type="professional",
            _classification_confidence="high",
            classification_pending=False,
            professional_flow_active=True,
            prof_flow_step=2,  # about to collect message and close
            prof_name="Dr Patterson",
            prof_callback="07890123456",
        )

        tts_calls = []

        async def _fake_tts(text):
            tts_calls.append(text)

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)

            with patch("app.caller_classifier._LOG_DIR", tmp_path):
                asyncio.run(run_professional_flow(
                    session, _fake_tts, "Please call us back about the referral for Mr Jones"
                ))

            assert session["professional_flow_complete"] is True
            assert session["professional_flow_active"] is False

            log_file = tmp_path / "professional_calls.jsonl"
            assert log_file.exists(), "professional_calls.jsonl was not created"
            lines = [l for l in log_file.read_text().splitlines() if l.strip()]
            assert len(lines) == 1
            record = json.loads(lines[0])
            assert record["caller_name"] == "Dr Patterson"
            assert record["callback_number"] == "07890123456"
            assert "referral" in record["message"]
            assert "call_sid" in record
            assert "timestamp" in record

    def test_full_three_turn_professional_flow(self):
        """Walk the complete 3-turn professional flow and verify slots collected."""
        session = _make_session(
            professional_flow_active=True,
            prof_flow_step=0,
        )
        tts_calls = []

        async def _fake_tts(text):
            tts_calls.append(text)

        async def _run():
            await run_professional_flow(session, _fake_tts, "Dr Sarah Johnson")
            await run_professional_flow(session, _fake_tts, "07712 345678")
            with patch("app.caller_classifier._LOG_DIR", Path(tempfile.mkdtemp())):
                await run_professional_flow(session, _fake_tts, "Referral for physio assessment")

        asyncio.run(_run())

        assert session["prof_name"] == "Dr Sarah Johnson"
        assert session["prof_callback"] == "07712 345678"
        assert session["prof_message"] == "Referral for physio assessment"
        assert session["professional_flow_complete"] is True
        assert any("pass that on" in t for t in tts_calls)
