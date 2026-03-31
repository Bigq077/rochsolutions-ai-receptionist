"""
Tests for context-aware silence response selection.

Coverage:
  1. ask_phone state → phone-specific response, 6s threshold
  2. greeting state (TRIAGE) → greeting-specific response with clinic_name
  3. Unmapped state → default response
  4. Second consecutive silence → callback phrase appended
  5. consecutive_silence_count initialised at 0 in _init_session
  6. get_silence_response never raises on bad input
  7. get_silence_threshold never raises on bad input
  8. Media-streams SilenceHandler.set_state updates current_state
"""
from __future__ import annotations

import pytest
from unittest.mock import patch


# ---------------------------------------------------------------------------
# 1. ask_phone → phone-specific response and 6s threshold
# ---------------------------------------------------------------------------

def test_ask_phone_response():
    from app.silence_handler import get_silence_response
    result = get_silence_response("BOOK_PHONE", consecutive_count=0, clinic_name="Test Clinic")
    assert "rush" in result.lower() or "time" in result.lower()
    # Must NOT be the generic default
    assert "could you say that again" not in result.lower()


def test_ask_phone_threshold():
    from app.silence_handler import get_silence_threshold
    threshold = get_silence_threshold("BOOK_PHONE")
    assert threshold == 6.0


def test_resch_phone_also_maps_to_ask_phone():
    from app.silence_handler import get_silence_threshold
    assert get_silence_threshold("RESCH_PHONE") == 6.0


# ---------------------------------------------------------------------------
# 2. greeting state → greeting-specific response with formatted clinic_name
# ---------------------------------------------------------------------------

def test_greeting_response_contains_clinic_name():
    from app.silence_handler import get_silence_response
    result = get_silence_response("TRIAGE", consecutive_count=0, clinic_name="Theorem Health")
    assert "Theorem Health" in result


def test_greeting_response_format():
    from app.silence_handler import get_silence_response
    result = get_silence_response("TRIAGE", consecutive_count=0, clinic_name="Theorem Health")
    # Should start with "Hello?" or similar greeting
    assert "hello" in result.lower() or "susie" in result.lower()


# ---------------------------------------------------------------------------
# 3. Unmapped state → default response
# ---------------------------------------------------------------------------

def test_unmapped_state_returns_default():
    from app.silence_handler import get_silence_response
    result = get_silence_response("SOME_UNKNOWN_STATE", consecutive_count=0)
    from app.phrases import SILENCE_RESPONSES
    assert result == SILENCE_RESPONSES["default"]


def test_unmapped_state_threshold_is_default():
    from app.silence_handler import get_silence_threshold
    from app.phrases import SILENCE_THRESHOLDS
    result = get_silence_threshold("SOME_UNKNOWN_STATE")
    assert result == SILENCE_THRESHOLDS["default"]


# ---------------------------------------------------------------------------
# 4. Second consecutive silence → callback phrase appended
# ---------------------------------------------------------------------------

def test_second_consecutive_appends_callback():
    from app.silence_handler import get_silence_response
    result = get_silence_response("BOOK_PHONE", consecutive_count=1, clinic_name="Test Clinic")
    assert "call back" in result.lower()


def test_second_consecutive_still_has_base_response():
    from app.silence_handler import get_silence_response
    first = get_silence_response("BOOK_PHONE", consecutive_count=0, clinic_name="Test Clinic")
    second = get_silence_response("BOOK_PHONE", consecutive_count=1, clinic_name="Test Clinic")
    # Second should start with the same base as first
    assert second.startswith(first)


def test_first_silence_no_callback():
    from app.silence_handler import get_silence_response
    result = get_silence_response("BOOK_NAME", consecutive_count=0)
    assert "call back" not in result.lower()


# ---------------------------------------------------------------------------
# 5. consecutive_silence_count initialised at 0 in _init_session
# ---------------------------------------------------------------------------

def test_consecutive_silence_count_initialised():
    from app.routes.twilio import _init_session
    session = _init_session({}, "CAtest001")
    assert "consecutive_silence_count" in session
    assert session["consecutive_silence_count"] == 0


def test_consecutive_silence_count_default_zero_on_empty_session():
    from app.routes.twilio import _init_session
    # Even on a fresh empty dict it should be 0
    session = _init_session({}, "CAtest002")
    assert session["consecutive_silence_count"] == 0


# ---------------------------------------------------------------------------
# 6. get_silence_response never raises on bad input
# ---------------------------------------------------------------------------

def test_get_silence_response_none_state():
    from app.silence_handler import get_silence_response
    # Should not raise; returns default
    result = get_silence_response(None, 0, "Test")  # type: ignore[arg-type]
    assert isinstance(result, str)
    assert len(result) > 0


def test_get_silence_response_empty_string_state():
    from app.silence_handler import get_silence_response
    result = get_silence_response("", 0, "Test")
    assert isinstance(result, str)
    assert len(result) > 0


# ---------------------------------------------------------------------------
# 7. get_silence_threshold never raises on bad input
# ---------------------------------------------------------------------------

def test_get_silence_threshold_none_state():
    from app.silence_handler import get_silence_threshold
    result = get_silence_threshold(None)  # type: ignore[arg-type]
    assert isinstance(result, float)
    assert result > 0


# ---------------------------------------------------------------------------
# 8. SilenceHandler.set_state updates current_state
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_silence_handler_set_state():
    import asyncio
    from app.media_streams.connection import SilenceHandler

    q: asyncio.Queue = asyncio.Queue()
    handler = SilenceHandler(
        tts_text_queue=q,
        trigger_transfer_fn=lambda: None,
    )

    assert handler.current_state == "default"
    handler.set_state("BOOK_PHONE")
    assert handler.current_state == "BOOK_PHONE"


@pytest.mark.asyncio
async def test_silence_handler_set_state_ignores_empty():
    import asyncio
    from app.media_streams.connection import SilenceHandler

    q: asyncio.Queue = asyncio.Queue()
    handler = SilenceHandler(
        tts_text_queue=q,
        trigger_transfer_fn=lambda: None,
    )
    handler.set_state("BOOK_NAME")
    handler.set_state("")  # should be ignored
    assert handler.current_state == "BOOK_NAME"
