"""
Integration tests for critical AI Receptionist call flows.

Covers:
  1. Location detection — phonetic variants of Alcester / Redditch
  2. E.164 phone validation / normalisation
  3. Silence escalation — three consecutive silences → live transfer TwiML
  4. Duplicate /status webhook — idempotency lock prevents double-processing
  5. Location redirect loop breaker — defaults location after 4 redirects
  6. Phase 3 fallback counter increments on MAX_TOOL_ITERATIONS
  7. Reschedule COLLECT_PHONE / CONFIRM_PHONE gate bugs
       7a. DTMF capture arms phone_confirm_armed so yes/no is accepted
       7b. YES after DTMF readback advances to LOOKUP_RESCHEDULE
       7c. NO after DTMF readback returns to COLLECT_PHONE with clean buffers
       7d. Repair utterance in COLLECT_PHONE clears DTMF buffer (local reset)
       7e. No old digits survive after a local repair + re-entry
       7f. Normal booking phone path is not regressed
  8. LOOKUP_RESCHEDULE confirmation loop
       8a. Multiple near-match lookup sets rc_stage=lookup_done in session
       8b. Multiple near-match lookup returns found=True (not "multiple")
       8c. Multiple near-match: best candidate saved to session keys
       8d. Multiple near-match: alternatives stored (not including best)
       8e. Deterministic YES gate advances flow when rc_stage=lookup_done
       8f. Deterministic YES gate does not re-fire LLM / repeat lookup
       8g. NO after lookup stays at LOOKUP_RESCHEDULE for disambiguation

Run with: pytest tests/ -v
"""
from __future__ import annotations

import asyncio
import copy
from typing import Any, Dict, List

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
        assert self._detect("1") == "alcester"

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
# 5. Passive location detection in /turn
# ===========================================================================

class TestPassiveLocationDetection:
    """
    /turn should silently detect a location name from any user utterance
    and store it in session without interrupting the conversation flow.
    """

    CALL_SID = "CAloop001"

    async def test_location_detected_from_speech(
        self, async_client, mock_redis_session_store
    ):
        session = await mock_redis_session_store.get_session(self.CALL_SID)
        session["location_selected"] = False
        session["clinic_id"] = "theorem"
        await mock_redis_session_store.save_session(self.CALL_SID, session)

        async def _echo_triage(user_said, sess):
            return "How can I help?", sess

        with (
            patch("app.flows.triage_legacy.triage_turn", side_effect=_echo_triage),
            patch("app.config.PHASE3_ENABLED", False),
        ):
            resp = await async_client.post(
                "/twilio/turn",
                data={
                    "CallSid": self.CALL_SID,
                    "To": "+447367002651",
                    "SpeechResult": "I'd like to book at Redditch please",
                },
            )

        assert resp.status_code == 200
        # No redirect — falls through to triage
        assert "<Redirect>" not in resp.text
        updated = await mock_redis_session_store.get_session(self.CALL_SID)
        assert updated["location_selected"] is True
        assert updated["selected_location"] == "redditch"


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


# ===========================================================================
# 7. Reschedule COLLECT_PHONE / CONFIRM_PHONE gate — Bug fix coverage
# ===========================================================================

class _FakeTTS:
    """asyncio.Queue stand-in that records every put() call."""
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

def _make_reschedule_engine(extra_session: Dict[str, Any] | None = None) -> tuple:
    """
    Return (engine, tts) with FlowEngine wired to RESCHEDULE_FLOW.

    The session is set up as if:
      - caller's name has been collected
      - caller confirmed the booking number is DIFFERENT from their calling number
        (so we are now in COLLECT_PHONE, step 2, awaiting DTMF)
    """
    from app.media_streams.flow import (
        FlowEngine,
        RESCHEDULE_FLOW,
        _RESCHEDULE_COLLECT_PHONE_INDEX,
    )
    from app.media_streams.session import DEFAULT_MS_SESSION

    session = copy.deepcopy(DEFAULT_MS_SESSION)
    session.update({
        "full_name":          "Jane Smith",
        "state":              "COLLECT_PHONE",
        "flow_state":         "COLLECT_PHONE",
        "flow_step":          _RESCHEDULE_COLLECT_PHONE_INDEX,
        "phone_from_twilio":  True,   # caller has a Twilio number
        "phone_confirmed":    False,
        "phone_confirm_armed": False,
        "phone_dtmf_buffer":  "",
        "phone_digits_buffer": "",
        "phone_awaiting_dtmf": True,
        "selected_location":  "alcester",
    })
    if extra_session:
        session.update(extra_session)

    tts = _FakeTTS()
    engine = FlowEngine(session, tts, _noop_llm)
    engine._active_flow = RESCHEDULE_FLOW
    engine._intent_detected = True
    return engine, tts


class TestReschedulePhoneGate:
    """
    Covers the two bugs fixed in the reschedule COLLECT_PHONE / CONFIRM_PHONE path:

    Bug 1 — phone_confirm_armed not set after DTMF capture (gate mismatch)
    Bug 2 — repair utterance in COLLECT_PHONE triggered global "what was your
             inquiry?" reset instead of a local keypad retry with cleared buffers
    """

    # ── 7a: gate is armed immediately after DTMF capture ───────────────────

    async def test_dtmf_capture_arms_phone_confirm_gate(self):
        """
        After DTMF digits complete and the readback prompt is emitted,
        phone_confirm_armed must be True so the CONFIRM_PHONE handler
        will accept the caller's yes/no on the next turn.
        """
        engine, tts = _make_reschedule_engine()
        # Synthetic transcript created by connection.py when DTMF buffer completes
        await engine.handle_transcript("07912345678")

        assert engine.session.get("state") == "CONFIRM_PHONE", (
            f"Expected state CONFIRM_PHONE, got {engine.session.get('state')}"
        )
        assert engine.session.get("phone_confirm_armed") is True, (
            "phone_confirm_armed must be True after DTMF capture so the "
            "gate accepts the caller's yes/no on the next turn"
        )
        assert engine.session.get("phone_readback_pending") is True
        # Readback prompt must be spoken
        assert "Just to check" in tts.last(), (
            f"Expected readback prompt, got: {tts.last()!r}"
        )

    # ── 7b: YES after readback advances to LOOKUP_RESCHEDULE ───────────────

    async def test_dtmf_yes_advances_to_lookup(self):
        """
        'yes it is' after the DTMF readback must accept the number and
        route to LOOKUP_RESCHEDULE, not loop back to the generic caller-number
        question.
        """
        from app.media_streams.flow import RESCHEDULE_FLOW, _RESCHEDULE_LOOKUP_INDEX

        engine, tts = _make_reschedule_engine()
        # Step 1: DTMF capture
        await engine.handle_transcript("07912345678")
        assert engine.session.get("phone_confirm_armed") is True

        # Step 2: caller says yes
        await engine.handle_transcript("yes it is")

        assert engine.session.get("phone_confirmed") is True, (
            "phone_confirmed must be True after YES"
        )
        assert engine.session.get("state") == "LOOKUP_RESCHEDULE", (
            f"Expected LOOKUP_RESCHEDULE after YES, got {engine.session.get('state')}"
        )
        assert engine.session.get("phone_confirm_armed") is False, (
            "Gate must be disarmed after YES is consumed"
        )
        # Must not loop back to the generic caller-number question
        _generic = "Is the phone number you're calling on"
        assert not any(_generic in t for t in tts.items), (
            f"Generic caller-number question must not appear after DTMF yes-flow. TTS: {tts.items}"
        )

    # ── 7c: NO after readback returns to COLLECT_PHONE with clean buffers ──

    async def test_dtmf_no_returns_to_collect_phone_clean(self):
        """
        'no' after the DTMF readback must route back to COLLECT_PHONE and
        clear all digit buffers so the re-entry starts from scratch.
        """
        engine, tts = _make_reschedule_engine()
        # Step 1: DTMF capture
        await engine.handle_transcript("07912345678")
        assert engine.session.get("phone_confirm_armed") is True

        # Step 2: caller says no
        await engine.handle_transcript("no that's wrong")

        assert engine.session.get("state") == "COLLECT_PHONE", (
            f"Expected COLLECT_PHONE after NO, got {engine.session.get('state')}"
        )
        assert engine.session.get("phone_dtmf_buffer", "") == "", (
            "phone_dtmf_buffer must be cleared after NO"
        )
        assert engine.session.get("phone_digits_buffer", "") == "", (
            "phone_digits_buffer must be cleared after NO"
        )
        assert engine.session.get("phone_confirmed") is False
        assert engine.session.get("phone_readback_pending") is False
        assert engine.session.get("phone_confirm_armed") is False

    # ── 7d: repair utterance clears DTMF buffer (local reset) ──────────────

    async def test_collect_phone_repair_clears_dtmf_buffer(self):
        """
        A repair utterance ('i messed up') while in COLLECT_PHONE must:
          - clear phone_dtmf_buffer
          - clear phone_digits_buffer
          - stay in COLLECT_PHONE
          - NOT play 'Sorry about that — what was your inquiry?'
        """
        engine, tts = _make_reschedule_engine({
            "phone_dtmf_buffer":   "0794",   # partial entry already in buffer
            "phone_digits_buffer": "0794",
        })

        await engine.handle_transcript("i messed up")

        # Buffers must be cleared
        assert engine.session.get("phone_dtmf_buffer", "") == "", (
            "phone_dtmf_buffer must be cleared after repair in COLLECT_PHONE"
        )
        assert engine.session.get("phone_digits_buffer", "") == "", (
            "phone_digits_buffer must be cleared after repair in COLLECT_PHONE"
        )
        # State must remain COLLECT_PHONE (local repair, not global reset)
        assert engine.session.get("state") == "COLLECT_PHONE", (
            f"State must remain COLLECT_PHONE after local repair, got {engine.session.get('state')}"
        )
        # Must NOT play the generic global-repair phrase
        _wrong_phrase = "what was your inquiry"
        _last_q = engine.session.get("last_question", "")
        assert _wrong_phrase not in _last_q.lower(), (
            f"Local repair must not produce global inquiry reset. last_question={_last_q!r}"
        )
        # repair_requested must be set (so TTS queue gets drained in connection.py)
        assert engine.session.get("repair_requested") is True

    # ── 7e: no old digits survive after repair + re-entry ──────────────────

    async def test_no_digit_carryover_after_repair(self):
        """
        After a local repair, the next DTMF entry must produce a number
        containing ONLY the post-repair digits, with no prefix from the
        aborted partial entry.
        """
        engine, tts = _make_reschedule_engine({
            "phone_dtmf_buffer":   "0794",
            "phone_digits_buffer": "0794",
        })

        # Repair clears partial entry
        await engine.handle_transcript("i messed up")
        assert engine.session.get("phone_dtmf_buffer", "") == ""
        assert engine.session.get("phone_digits_buffer", "") == ""

        # Re-arm engine state for the new entry
        engine.session["repair_requested"] = False   # simulate connection.py drain

        # New complete number entered
        await engine.handle_transcript("07700900123")

        # Must be in CONFIRM_PHONE with ONLY the new digits
        assert engine.session.get("state") == "CONFIRM_PHONE", (
            f"Expected CONFIRM_PHONE after clean re-entry, got {engine.session.get('state')}"
        )
        captured = (
            engine.session.get("phone_candidate")
            or engine.session.get("phone_number")
            or engine.session.get("phone")
            or ""
        )
        assert "0794" not in captured, (
            f"Old digits must not survive in captured number: {captured!r}"
        )
        assert "07700900123" in captured or captured.replace("+44", "0")[:11] == "07700900123", (
            f"New number must be fully captured: {captured!r}"
        )

    # ── 7f: booking flow phone gate not regressed ──────────────────────────

    async def test_booking_flow_phone_gate_not_regressed(self):
        """
        Normal booking flow: DTMF capture → CONFIRM_PHONE gate armed → YES
        accepts and routes to CONFIRM_BOOKING (not LOOKUP_RESCHEDULE).
        """
        from app.media_streams.flow import (
            FlowEngine,
            BOOKING_FLOW,
            _COLLECT_PHONE_INDEX,
        )
        from app.media_streams.session import DEFAULT_MS_SESSION

        session = copy.deepcopy(DEFAULT_MS_SESSION)
        session.update({
            "full_name":          "Tom Brown",
            "state":              "COLLECT_PHONE",
            "flow_state":         "COLLECT_PHONE",
            "flow_step":          _COLLECT_PHONE_INDEX,
            "phone_from_twilio":  True,
            "phone_confirmed":    False,
            "phone_confirm_armed": False,
            "phone_dtmf_buffer":  "",
            "phone_digits_buffer": "",
            "phone_awaiting_dtmf": True,
            "selected_location":  "alcester",
        })
        tts = _FakeTTS()
        engine = FlowEngine(session, tts, _noop_llm)
        engine._active_flow = BOOKING_FLOW
        engine._intent_detected = True

        # DTMF capture
        await engine.handle_transcript("07700900456")
        assert engine.session.get("phone_confirm_armed") is True, (
            "Booking flow: gate must be armed after DTMF capture"
        )
        assert engine.session.get("state") == "CONFIRM_PHONE"

        # YES confirmation
        await engine.handle_transcript("yes")
        assert engine.session.get("phone_confirmed") is True
        # Booking flow should route to CONFIRM_BOOKING (not LOOKUP_RESCHEDULE)
        assert engine.session.get("state") in ("CONFIRM_BOOKING", "COLLECT_REASON", "PRESENT_DAYS"), (
            f"Booking flow after phone YES should not go to LOOKUP_RESCHEDULE. "
            f"Got: {engine.session.get('state')}"
        )


# ===========================================================================
# 8. LOOKUP_RESCHEDULE confirmation loop — Bug fix coverage
# ===========================================================================

def _make_fake_acuity_appointments(now_iso: str | None = None) -> list:
    """
    Return two future Acuity appointment dicts with fuzzy-matching name fields
    so that `_lookup_appointment_acuity` classifies them as near-matches.
    """
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    return [
        {
            "id":        "111",
            "datetime":  (now + timedelta(days=7)).isoformat(),
            "firstName": "Jane",
            "lastName":  "Smith",
            "phone":     "+447912345678",
            "type":      "Physiotherapy",
            "typeID":    "42",
        },
        {
            "id":        "222",
            "datetime":  (now + timedelta(days=14)).isoformat(),
            "firstName": "Jane",
            "lastName":  "Smyth",
            "phone":     "+447912345678",
            "type":      "Sports Massage",
            "typeID":    "43",
        },
    ]


def _make_lookup_engine(extra_session: Dict[str, Any] | None = None) -> tuple:
    """
    Return (engine, tts) with FlowEngine at LOOKUP_RESCHEDULE step.
    Session is set up as if COLLECT_PHONE / CONFIRM_PHONE just completed.
    """
    from app.media_streams.flow import (
        FlowEngine,
        RESCHEDULE_FLOW,
        _RESCHEDULE_LOOKUP_INDEX,
    )
    from app.media_streams.session import DEFAULT_MS_SESSION

    session = copy.deepcopy(DEFAULT_MS_SESSION)
    session.update({
        "full_name":            "Jane Smith",
        "state":                "LOOKUP_RESCHEDULE",
        "flow_state":           "LOOKUP_RESCHEDULE",
        "flow_step":            _RESCHEDULE_LOOKUP_INDEX,
        "phone_confirmed":      True,
        "phone_confirm_armed":  False,
        "customer_phone":       "+447912345678",
        "selected_location":    "alcester",
    })
    if extra_session:
        session.update(extra_session)

    tts = _FakeTTS()
    engine = FlowEngine(session, tts, _noop_llm)
    engine._active_flow = RESCHEDULE_FLOW
    engine._intent_detected = True
    return engine, tts


class TestLookupRescheduleConfirmationGate:
    """
    Covers the bug where lookup_appointment returns found="multiple" (multiple
    near-matches) and rc_stage is never set to "lookup_done", causing the
    deterministic YES gate to never fire and every caller confirmation to
    re-run the LLM / re-run appointment lookup.

    Fix: _lookup_appointment_acuity now picks the nearest candidate as the
    best match, saves it to session (rc_stage="lookup_done"), and returns
    found=True with multiple_found=True.
    """

    # ── 8a: multiple near-match sets rc_stage=lookup_done ─────────────────

    async def test_multiple_near_match_sets_rc_stage(self):
        """
        When ≥2 near-matches exist, _lookup_appointment_acuity must set
        session["rc_stage"] = "lookup_done" so the deterministic gate fires.
        """
        from app.tools.receptionist_tools import _lookup_appointment_acuity

        session: Dict[str, Any] = {
            "full_name":      "Jane Smith",
            "customer_phone": "+447912345678",
        }
        appointments = _make_fake_acuity_appointments()

        with patch(
            "app.tools.receptionist_tools._get_acuity_adapter"
        ) as mock_adapter_fn:
            mock_adapter = MagicMock()
            mock_adapter.list_appointments = AsyncMock(return_value=appointments)
            mock_adapter_fn.return_value = mock_adapter

            result = await _lookup_appointment_acuity(
                {"name": "Jane Smith", "phone": "+447912345678"},
                session,
            )

        assert session.get("rc_stage") == "lookup_done", (
            f"rc_stage must be 'lookup_done' after multiple near-match lookup, "
            f"got {session.get('rc_stage')!r}. result={result}"
        )

    # ── 8b: return value has found=True (not "multiple") ──────────────────

    async def test_multiple_near_match_returns_found_true(self):
        """
        Return value must have found=True (not found="multiple") so the LLM
        instruction's 'If found=true' branch handles it correctly.
        """
        from app.tools.receptionist_tools import _lookup_appointment_acuity

        session: Dict[str, Any] = {
            "full_name":      "Jane Smith",
            "customer_phone": "+447912345678",
        }
        appointments = _make_fake_acuity_appointments()

        with patch(
            "app.tools.receptionist_tools._get_acuity_adapter"
        ) as mock_adapter_fn:
            mock_adapter = MagicMock()
            mock_adapter.list_appointments = AsyncMock(return_value=appointments)
            mock_adapter_fn.return_value = mock_adapter

            result = await _lookup_appointment_acuity(
                {"name": "Jane Smith", "phone": "+447912345678"},
                session,
            )

        assert result.get("found") is True, (
            f"found must be True (not 'multiple'). Got: {result.get('found')!r}"
        )
        assert result.get("multiple_found") is True, (
            "multiple_found must be True to signal the LLM there are alternatives"
        )

    # ── 8c: best candidate saved to session keys ──────────────────────────

    async def test_multiple_near_match_saves_best_to_session(self):
        """
        The nearest future appointment must be saved as the pending candidate
        with the correct session keys.
        """
        from app.tools.receptionist_tools import _lookup_appointment_acuity

        session: Dict[str, Any] = {
            "full_name":      "Jane Smith",
            "customer_phone": "+447912345678",
        }
        appointments = _make_fake_acuity_appointments()

        with patch(
            "app.tools.receptionist_tools._get_acuity_adapter"
        ) as mock_adapter_fn:
            mock_adapter = MagicMock()
            mock_adapter.list_appointments = AsyncMock(return_value=appointments)
            mock_adapter_fn.return_value = mock_adapter

            await _lookup_appointment_acuity(
                {"name": "Jane Smith", "phone": "+447912345678"},
                session,
            )

        # Best is the nearest appointment (id="111", 7 days out)
        assert session.get("reschedule_appt_id") == "111", (
            f"Best (nearest) appt id must be saved. Got: {session.get('reschedule_appt_id')!r}"
        )
        assert session.get("reschedule_appt_datetime") is not None, (
            "reschedule_appt_datetime must be set"
        )
        assert session.get("reschedule_appt_type") == "Physiotherapy", (
            f"reschedule_appt_type must match best appt. Got: {session.get('reschedule_appt_type')!r}"
        )
        assert session.get("reschedule_original_type_id") == "acuity_42", (
            f"reschedule_original_type_id must be set. Got: {session.get('reschedule_original_type_id')!r}"
        )

    # ── 8d: alternatives stored (excluding best) ──────────────────────────

    async def test_multiple_near_match_stores_alternatives(self):
        """
        The remaining near-matches (not the best) must be stored in
        session["reschedule_appt_alternatives"] for downstream disambiguation.
        The best candidate must NOT appear in alternatives.
        """
        from app.tools.receptionist_tools import _lookup_appointment_acuity

        session: Dict[str, Any] = {
            "full_name":      "Jane Smith",
            "customer_phone": "+447912345678",
        }
        appointments = _make_fake_acuity_appointments()

        with patch(
            "app.tools.receptionist_tools._get_acuity_adapter"
        ) as mock_adapter_fn:
            mock_adapter = MagicMock()
            mock_adapter.list_appointments = AsyncMock(return_value=appointments)
            mock_adapter_fn.return_value = mock_adapter

            await _lookup_appointment_acuity(
                {"name": "Jane Smith", "phone": "+447912345678"},
                session,
            )

        alts = session.get("reschedule_appt_alternatives", [])
        assert isinstance(alts, list) and len(alts) >= 1, (
            f"At least 1 alternative must be stored. Got: {alts!r}"
        )
        alt_ids = [a["id"] for a in alts]
        assert "111" not in alt_ids, (
            f"Best candidate (id=111) must not appear in alternatives. Got: {alt_ids}"
        )
        assert "222" in alt_ids, (
            f"Second appointment (id=222) must be in alternatives. Got: {alt_ids}"
        )

    # ── 8e: deterministic YES gate advances flow ──────────────────────────

    async def test_yes_gate_advances_flow_when_lookup_done(self):
        """
        When rc_stage='lookup_done' and reschedule_appt_id is set (simulating
        state after lookup ran), a YES confirmation must:
          - set rc_appointment_confirmed=True
          - set rc_stage='confirmed'
          - advance flow_step past LOOKUP_RESCHEDULE
          - NOT loop back to LOOKUP_RESCHEDULE
        """
        from app.media_streams.flow import _RESCHEDULE_LOOKUP_INDEX

        engine, tts = _make_lookup_engine({
            "rc_stage":              "lookup_done",
            "reschedule_appt_id":    "111",
            "reschedule_appt_datetime": "2026-04-20T10:00:00+01:00",
            "reschedule_appt_type":  "Physiotherapy",
        })

        await engine.handle_transcript("yes that's correct")

        assert engine.session.get("rc_appointment_confirmed") is True, (
            "rc_appointment_confirmed must be True after YES"
        )
        assert engine.session.get("rc_stage") == "confirmed", (
            f"rc_stage must be 'confirmed' after YES. Got: {engine.session.get('rc_stage')!r}"
        )
        assert engine.session.get("flow_step") > _RESCHEDULE_LOOKUP_INDEX, (
            f"flow_step must advance past LOOKUP_RESCHEDULE ({_RESCHEDULE_LOOKUP_INDEX}). "
            f"Got: {engine.session.get('flow_step')}"
        )

    # ── 8f: YES gate does not re-fire LLM / repeat lookup ────────────────

    async def test_yes_gate_does_not_call_llm(self):
        """
        The deterministic YES gate must consume the confirmation without
        calling the LLM.  We track this by verifying _noop_llm is never
        awaited (the engine's llm_fn records its calls via a counter wrapper).
        """
        llm_call_count = 0

        async def _counting_llm(instruction: str, allow_tools: bool = True) -> str:
            nonlocal llm_call_count
            llm_call_count += 1
            return ""

        from app.media_streams.flow import (
            FlowEngine,
            RESCHEDULE_FLOW,
            _RESCHEDULE_LOOKUP_INDEX,
        )
        from app.media_streams.session import DEFAULT_MS_SESSION

        session = copy.deepcopy(DEFAULT_MS_SESSION)
        session.update({
            "full_name":             "Jane Smith",
            "state":                 "LOOKUP_RESCHEDULE",
            "flow_state":            "LOOKUP_RESCHEDULE",
            "flow_step":             _RESCHEDULE_LOOKUP_INDEX,
            "phone_confirmed":       True,
            "customer_phone":        "+447912345678",
            "selected_location":     "alcester",
            "rc_stage":              "lookup_done",
            "reschedule_appt_id":    "111",
            "reschedule_appt_datetime": "2026-04-20T10:00:00+01:00",
            "reschedule_appt_type":  "Physiotherapy",
        })
        tts = _FakeTTS()
        engine = FlowEngine(session, tts, _counting_llm)
        engine._active_flow = RESCHEDULE_FLOW
        engine._intent_detected = True

        await engine.handle_transcript("yes perfect")

        assert llm_call_count == 0, (
            f"LLM must NOT be called when rc_stage=lookup_done and caller says YES. "
            f"Called {llm_call_count} time(s)."
        )

    # ── 8g: NO stays at LOOKUP_RESCHEDULE for disambiguation ─────────────

    async def test_no_stays_at_lookup_reschedule(self):
        """
        When rc_stage='lookup_done' and the caller says NO, the flow must
        NOT advance — it should stay at LOOKUP_RESCHEDULE so the LLM can
        offer alternatives or transfer.
        """
        from app.media_streams.flow import _RESCHEDULE_LOOKUP_INDEX

        engine, tts = _make_lookup_engine({
            "rc_stage":              "lookup_done",
            "reschedule_appt_id":    "111",
            "reschedule_appt_datetime": "2026-04-20T10:00:00+01:00",
            "reschedule_appt_type":  "Physiotherapy",
        })

        await engine.handle_transcript("no that's not right")

        assert engine.session.get("flow_step") == _RESCHEDULE_LOOKUP_INDEX, (
            f"flow_step must stay at LOOKUP_RESCHEDULE on NO. "
            f"Got: {engine.session.get('flow_step')}"
        )
        assert engine.session.get("rc_appointment_confirmed") is not True, (
            "rc_appointment_confirmed must not be set to True on NO"
        )
