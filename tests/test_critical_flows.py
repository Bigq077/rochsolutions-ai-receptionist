"""
Integration tests for critical AI Receptionist call flows.

Covers:
  1. Location detection — phonetic variants of Alcester / Redditch
  2. E.164 phone validation / normalisation
  3. Silence escalation — three consecutive silences → live transfer TwiML
  4. Duplicate /status webhook — idempotency lock prevents double-processing
  5. Location redirect loop breaker — defaults location after 4 redirects
  6. Phase 3 fallback counter increments on MAX_TOOL_ITERATIONS

Run with: pytest tests/ -v
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

pytestmark = pytest.mark.asyncio


# ===========================================================================
# 1. Location detection — phonetic variants
# ===========================================================================

class TestLocationDetection:
    """_detect_location() must map phonetic variants to canonical location ids."""

    CLINIC = {
        "locations": [
            {"id": "alcester", "name": "Alcester"},
            {"id": "redditch", "name": "Redditch"},
        ]
    }

    def _detect(self, speech: str) -> str | None:
        from app.routes.twilio import _detect_location
        return _detect_location(speech, self.CLINIC)

    # --- Alcester ---
    def test_alcester_exact(self):
        assert self._detect("alcester") == "alcester"

    def test_alcester_mixed_case(self):
        assert self._detect("Alcester please") == "alcester"

    def test_alcester_allster(self):
        """Common speech-recognition mishearing of 'Alcester'."""
        assert self._detect("allster") == "alcester"

    def test_alcester_alchester(self):
        assert self._detect("alchester") == "alcester"

    def test_alcester_olster(self):
        assert self._detect("olster") == "alcester"

    def test_alcester_dtmf_1(self):
        assert self._detect("1", self.CLINIC) == "alcester"

    def test_alcester_dtmf_one(self):
        assert self._detect("one") == "alcester"

    # --- Redditch ---
    def test_redditch_exact(self):
        assert self._detect("redditch") == "redditch"

    def test_redditch_reditch(self):
        assert self._detect("reditch") == "redditch"

    def test_redditch_red_witch(self):
        assert self._detect("red witch") == "redditch"

    def test_redditch_reddit(self):
        assert self._detect("reddit") == "redditch"

    def test_redditch_dtmf_2(self):
        assert self._detect("2") == "redditch"

    def test_redditch_dtmf_two(self):
        assert self._detect("two") == "redditch"

    # --- Unrecognised ---
    def test_unrecognised_returns_none(self):
        assert self._detect("Birmingham") is None

    def test_empty_returns_none(self):
        assert self._detect("") is None


# ===========================================================================
# 2. E.164 phone validation / normalisation
# ===========================================================================

class TestE164:
    """is_valid_e164 and normalise_to_e164 must handle UK numbers correctly."""

    def test_valid_uk_e164(self):
        from app.utils import is_valid_e164
        assert is_valid_e164("+447712345678") is True

    def test_valid_us_e164(self):
        from app.utils import is_valid_e164
        assert is_valid_e164("+12025551234") is True

    def test_invalid_no_plus(self):
        from app.utils import is_valid_e164
        assert is_valid_e164("447712345678") is False

    def test_invalid_too_short(self):
        from app.utils import is_valid_e164
        assert is_valid_e164("+123") is False

    def test_invalid_empty(self):
        from app.utils import is_valid_e164
        assert is_valid_e164("") is False

    def test_invalid_letters(self):
        from app.utils import is_valid_e164
        assert is_valid_e164("+44abc12345") is False

    def test_normalise_uk_national(self):
        from app.utils import normalise_to_e164
        assert normalise_to_e164("07712345678") == "+447712345678"

    def test_normalise_uk_with_spaces(self):
        from app.utils import normalise_to_e164
        assert normalise_to_e164("07712 345 678") == "+447712345678"

    def test_normalise_already_e164(self):
        from app.utils import normalise_to_e164
        assert normalise_to_e164("+447712345678") == "+447712345678"

    def test_normalise_international_no_plus(self):
        from app.utils import normalise_to_e164
        assert normalise_to_e164("447712345678") == "+447712345678"

    def test_normalise_garbage_returns_none(self):
        from app.utils import normalise_to_e164
        assert normalise_to_e164("not-a-number") is None

    def test_normalise_empty_returns_none(self):
        from app.utils import normalise_to_e164
        assert normalise_to_e164("") is None


# ===========================================================================
# 3. Silence escalation — three silences → live-transfer TwiML
# ===========================================================================

class TestSilenceEscalation:
    """
    Posting to /twilio/turn with no speech/digits increments miss_count.
    After three misses the response should contain a <Dial> element.
    """

    CALL_SID = "CAsilence001"

    async def _turn(self, async_client, speech: str = "", digits: str = ""):
        return await async_client.post(
            "/twilio/turn",
            data={
                "CallSid": self.CALL_SID,
                "To": "+447367002651",   # theorem clinic number
                "From": "+447900000001",
                "SpeechResult": speech,
                "Digits": digits,
            },
        )

    async def test_first_silence_returns_reintro(
        self, async_client, mock_redis_session_store
    ):
        # Seed a session that already has location set so we skip that logic
        session = await mock_redis_session_store.get_session(self.CALL_SID)
        session["location_selected"] = True
        session["selected_location"] = "alcester"
        session["clinic_id"] = "theorem"
        await mock_redis_session_store.save_session(self.CALL_SID, session)

        resp = await self._turn(async_client)
        assert resp.status_code == 200
        xml = resp.text
        # First silence: should offer a re-introduction, not a Dial
        assert "<Dial>" not in xml
        # miss_count should now be 1
        updated = await mock_redis_session_store.get_session(self.CALL_SID)
        assert updated["miss_count"] == 1

    async def test_third_silence_triggers_transfer(
        self, async_client, mock_redis_session_store
    ):
        # Seed a session with miss_count already at 2 (one more silence → transfer)
        session = await mock_redis_session_store.get_session(self.CALL_SID)
        session["location_selected"] = True
        session["selected_location"] = "alcester"
        session["clinic_id"] = "theorem"
        session["miss_count"] = 2
        await mock_redis_session_store.save_session(self.CALL_SID, session)

        resp = await self._turn(async_client)
        assert resp.status_code == 200
        xml = resp.text
        # Third silence: TwiML must contain a <Dial> for live transfer
        assert "<Dial" in xml


# ===========================================================================
# 4. Duplicate /status webhook — idempotency lock prevents double-processing
# ===========================================================================

class TestStatusIdempotency:
    """
    Two simultaneous /status POSTs for the same CallSid must only process once.
    The second request should be rejected with 'already processing'.
    """

    CALL_SID = "CAstatus001"

    async def _status(self, async_client, call_status: str = "completed"):
        return await async_client.post(
            "/twilio/status",
            data={
                "CallSid": self.CALL_SID,
                "CallStatus": call_status,
                "From": "+447900000001",
                "To": "+447367002651",
            },
        )

    async def test_second_status_skipped(
        self, async_client, mock_redis_session_store
    ):
        # Simulate the lock already being held (first request won)
        await mock_redis_session_store.acquire_once_lock(
            f"status_lock:{self.CALL_SID}"
        )

        # The lock is held → route returns early before any imports.
        # No side-effect mocking needed.
        resp = await self._status(async_client)

        assert resp.status_code == 200
        # The lock was already held → second request should be skipped
        assert "already processing" in resp.text

    async def test_first_status_acquires_lock(
        self, async_client, mock_redis_session_store
    ):
        # Imports inside the status function are done at call-time,
        # so patch the source modules directly (not app.routes.twilio.*).
        with (
            patch("app.tools.call_summary.build_call_summary",
                  return_value={"_raw_session": {}}),
            patch("app.tools.actionable_summary.build_actionable_summary_row",
                  new_callable=AsyncMock, return_value=[]),
            patch("app.tools.handoff.fire_and_forget_append_summary_row"),
            patch("app.notifications.smart_sms_router.send_smart_followup_sms",
                  new_callable=AsyncMock),
        ):
            resp = await self._status(async_client)

        assert resp.status_code == 200
        assert resp.text == "ok"
        # Lock should now be in the store
        assert f"status_lock:{self.CALL_SID}" in mock_redis_session_store._locks


# ===========================================================================
# 5. Location redirect loop breaker
# ===========================================================================

class TestLocationRedirectLoopBreaker:
    """
    Arriving at /turn without a location set increments location_redirect_count.
    After 4 redirects the session should default the location and fall through
    to normal turn handling rather than redirecting again.
    """

    CALL_SID = "CAloop001"

    async def _turn_no_speech(self, async_client, mock_redis_session_store):
        session = await mock_redis_session_store.get_session(self.CALL_SID)
        # Ensure location has NOT been selected yet
        session["location_selected"] = False
        session["clinic_id"] = "theorem"
        await mock_redis_session_store.save_session(self.CALL_SID, session)

        return await async_client.post(
            "/twilio/turn",
            data={
                "CallSid": self.CALL_SID,
                "To": "+447367002651",
                "SpeechResult": "something",
            },
        )

    async def test_loop_redirects_under_limit(
        self, async_client, mock_redis_session_store
    ):
        session = await mock_redis_session_store.get_session(self.CALL_SID)
        session["location_selected"] = False
        session["location_redirect_count"] = 1   # 2nd redirect
        session["clinic_id"] = "theorem"
        await mock_redis_session_store.save_session(self.CALL_SID, session)

        resp = await async_client.post(
            "/twilio/turn",
            data={"CallSid": self.CALL_SID, "To": "+447367002651"},
        )
        assert resp.status_code == 200
        # Should redirect to /voice, not fall through
        assert "<Redirect>" in resp.text

    async def test_loop_breaks_at_four(
        self, async_client, mock_redis_session_store
    ):
        # Seed session at 3 redirects — next arrival (4th) should break the loop
        session = await mock_redis_session_store.get_session(self.CALL_SID)
        session["location_selected"] = False
        session["location_redirect_count"] = 3
        session["clinic_id"] = "theorem"
        await mock_redis_session_store.save_session(self.CALL_SID, session)

        with (
            patch("app.flows.triage_legacy.triage_turn", new_callable=AsyncMock,
                  return_value=("How can I help?", session)),
            patch("app.config.PHASE3_ENABLED", False),
        ):
            resp = await async_client.post(
                "/twilio/turn",
                data={"CallSid": self.CALL_SID, "To": "+447367002651", "SpeechResult": "hello"},
            )

        assert resp.status_code == 200
        # Should NOT redirect — loop broken, normal turn handling
        updated = await mock_redis_session_store.get_session(self.CALL_SID)
        assert updated["location_selected"] is True
        # Defaulted to first location in clinic config (alcester)
        assert updated["selected_location"] == "alcester"


# ===========================================================================
# 6. Phase 3 fallback counter
# ===========================================================================

class TestPhase3FallbackCounter:
    """
    _increment_fallback_counter() must increment the Redis counter key.
    """

    async def test_counter_incremented_on_max_iterations(self):
        """When handle_turn hits MAX_TOOL_ITERATIONS, fallback counter increments."""
        from unittest.mock import AsyncMock, MagicMock

        mock_redis = MagicMock()
        mock_redis.incr = AsyncMock(return_value=1)

        # redis_client is imported at call-time inside _increment_fallback_counter,
        # so we must patch it on the storage module, not the conversation module.
        with patch("app.storage.redis_store.redis_client", mock_redis):
            from app.flows.conversation import _increment_fallback_counter
            await _increment_fallback_counter()

        mock_redis.incr.assert_called_once_with("metrics:phase3:fallbacks")

    async def test_counter_no_op_without_redis(self):
        """_increment_fallback_counter() must not raise if Redis is None."""
        with patch("app.storage.redis_store.redis_client", None):
            from app.flows.conversation import _increment_fallback_counter
            await _increment_fallback_counter()   # must not raise
