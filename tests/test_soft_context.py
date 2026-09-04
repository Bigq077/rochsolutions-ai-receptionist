"""
Tests for _update_soft_context() in connection.py.

MAKES REAL HAIKU API CALLS. Opt-in only — see the gate below.
save_session is mocked so Redis is not required.
"""
import os

import pytest
from unittest.mock import AsyncMock, patch


# ---------------------------------------------------------------------------
# OPT-IN: this is the only file in the suite that touches a live model.
# ---------------------------------------------------------------------------
# Left ungated it makes an Anthropic request on every full-suite run, and its
# result depends on the network, the rate limiter, and whether the model
# happens to phrase "evenings after six" as a time preference this time. That
# is a quality probe, not a regression gate, and it poisons the one measurement
# this repo relies on: the failing SET, diffed between two runs.
#
# It did exactly that on 2026-09-04. Two sessions measured the same commit and
# got 98 and 97 failures. The gap was this single test, and chasing it cost
# most of an afternoon before the real cause surfaced -- a truncated capture on
# one side and a missing ANTHROPIC_API_KEY on the other. A gate that is +/-1
# for reasons unrelated to the code cannot answer "did I break anything".
#
# Skipped by default it is DETERMINISTIC: the baseline is a fixed number and a
# set diff means what it says. Nothing is hidden -- the feature
# (_update_soft_context, theorem_v3 only) is exercised on every real call, and
# this file still runs on demand:
#
#     RUN_LIVE_LLM_TESTS=1 pytest tests/test_soft_context.py
#
# Truthy set copied from tests/auto/config.py's RUN_LIVE_CALL_TESTS gate rather
# than invented, so there is one spelling of "opt in" in this repo.
_LIVE_LLM_OPT_IN = os.getenv("RUN_LIVE_LLM_TESTS", "").strip().lower() in (
    "1", "true", "yes", "on",
)

pytestmark = pytest.mark.skipif(
    not _LIVE_LLM_OPT_IN,
    reason=(
        "makes a real Haiku API call; set RUN_LIVE_LLM_TESTS=1 to run. "
        "Skipped by default so the suite stays offline and the failing set "
        "stays deterministic."
    ),
)


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
