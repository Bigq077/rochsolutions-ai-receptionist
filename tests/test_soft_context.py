"""
Tests for _update_soft_context() in connection.py.
Makes real Haiku API calls — requires ANTHROPIC_API_KEY in .env.
save_session is mocked so Redis is not required.
"""
import pytest
from unittest.mock import AsyncMock, patch


@pytest.mark.asyncio
async def test_time_preference_extracted():
    session = {
        "call_sid": "test",
        "soft_context": {k: None for k in [
            "time_preference", "location_preference", "condition_notes",
            "emotional_state", "name", "service", "is_returning", "insurer",
        ]},
    }

    with patch("app.media_streams.connection.save_session", new_callable=AsyncMock):
        from app.media_streams.connection import _update_soft_context
        await _update_soft_context(
            session,
            user_text="I'm usually free evenings after six",
            bot_text="Let me check evening slots for you.",
        )

    assert session["soft_context"]["time_preference"] is not None, (
        f"time_preference not extracted — soft_context={session['soft_context']}"
    )
    print("PASS:", session["soft_context"])


@pytest.mark.asyncio
async def test_does_not_overwrite_existing():
    session = {
        "call_sid": "test",
        "soft_context": {
            "time_preference": "mornings",  # already set — must not be overwritten
            "location_preference": None,
            "condition_notes": None,
            "emotional_state": None,
            "name": None,
            "service": None,
            "is_returning": None,
            "insurer": None,
        },
    }

    with patch("app.media_streams.connection.save_session", new_callable=AsyncMock):
        from app.media_streams.connection import _update_soft_context
        await _update_soft_context(
            session,
            user_text="Actually evenings are fine too",
            bot_text="OK, let me check.",
        )

    assert session["soft_context"]["time_preference"] == "mornings", (
        f"Existing value was overwritten — soft_context={session['soft_context']}"
    )
    print("PASS: value preserved")
