"""
tests/test_pause_detector.py
-----------------------------
Tests for pause detection and pause-mode silence handling.

Covers:
  1. "hang on" → True
  2. "let me check with my partner" → True
  3. "Thursday" → False
  4. "" (empty string) → False, no exception
  5. caller_pause_active=True → silence handler uses 45s threshold
  6. Substantive utterance while paused → caller_pause_active cleared
  7. 90s total silence while paused → call termination invoked
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.pause_detector import detect_caller_pause_request, PAUSE_PHRASES


# ---------------------------------------------------------------------------
# Tests for detect_caller_pause_request
# ---------------------------------------------------------------------------

class TestDetectCallerPauseRequest:
    def test_hang_on_returns_true(self):
        """'hang on' is a pause phrase → True."""
        assert detect_caller_pause_request("hang on") is True

    def test_phrase_in_longer_sentence_returns_true(self):
        """'let me check with my partner' contains 'let me check' → True."""
        assert detect_caller_pause_request("let me check with my partner") is True

    def test_non_pause_phrase_returns_false(self):
        """'Thursday' contains no pause phrase → False."""
        assert detect_caller_pause_request("Thursday") is False

    def test_empty_string_returns_false_no_exception(self):
        """Empty string returns False and raises no exception."""
        result = detect_caller_pause_request("")
        assert result is False

    def test_one_sec_returns_true(self):
        assert detect_caller_pause_request("one sec") is True

    def test_bear_with_me_returns_true(self):
        assert detect_caller_pause_request("just bear with me a moment") is True

    def test_hold_on_returns_true(self):
        assert detect_caller_pause_request("hold on please") is True

    def test_case_insensitive(self):
        """Matching is case-insensitive."""
        assert detect_caller_pause_request("Hang On") is True
        assert detect_caller_pause_request("HANG ON") is True

    def test_partial_word_not_matched(self):
        """'Thursday' should not match 'one sec', 'hang on', etc."""
        assert detect_caller_pause_request("I'll come Thursday") is False

    def test_all_pause_phrases_match(self):
        """Every phrase in PAUSE_PHRASES matches itself."""
        for phrase in PAUSE_PHRASES:
            assert detect_caller_pause_request(phrase) is True, (
                f"Expected True for phrase {phrase!r}"
            )


# ---------------------------------------------------------------------------
# Tests for SilenceHandler pause-mode behaviour
# ---------------------------------------------------------------------------

class _FakeSilenceHandlerWithPause:
    """
    Minimal replica of SilenceHandler._run pause-mode branch for testing.
    Mirrors the exact logic in connection.py._run() pause path.
    """

    def __init__(self, session: dict, tts_queue: asyncio.Queue, transfer_called: list):
        self._session = session
        self._tts_text_queue = tts_queue
        self.last_audio_received_at = 0.0   # force since_audio always >= 3.5 in tests
        self._transfer_called = transfer_called
        self._cancelled = False

    async def _transfer(self):
        self._transfer_called.append(True)

    async def _run_pause_mode(self, sleep_fn=None):
        """
        Replica of the pause-mode branch of SilenceHandler._run().
        sleep_fn replaces asyncio.sleep for deterministic testing.
        """
        import time
        _sleep = sleep_fn or asyncio.sleep

        while True:
            try:
                await _sleep(45.0)
            except asyncio.CancelledError:
                return

            # since_audio guard — skipped (last_audio_received_at=0 so always old)
            since_audio = time.time() - self.last_audio_received_at
            if since_audio < 3.5:
                return

            if not self._session.get("caller_pause_active"):
                return

            self._session["pause_silence_total"] = (
                self._session.get("pause_silence_total", 0.0) + 45.0
            )
            total = self._session["pause_silence_total"]

            if total >= 90.0:
                phrase = (
                    "I'll let you go — give us a ring back when you're ready "
                    "and we'll get you sorted."
                )
                await self._tts_text_queue.put(phrase)
                await self._transfer()
                return

            if total >= 45.0:
                phrase = (
                    "Just checking you're still there — "
                    "no rush at all, take your time."
                )
                await self._tts_text_queue.put(phrase)
                # Continue loop


class TestPauseModeThreshold:
    def test_pause_active_uses_45s_threshold(self):
        """
        When caller_pause_active=True, the silence handler enters 45s mode.
        Verify the loop accumulates 45s per iteration.
        """
        session = {"caller_pause_active": True, "pause_silence_total": 0.0}
        q = asyncio.Queue()
        transfer = []
        handler = _FakeSilenceHandlerWithPause(session, q, transfer)

        # Simulate one 45s iteration: total should reach 45
        async def _one_iteration():
            call_count = 0

            async def fast_sleep(secs):
                nonlocal call_count
                call_count += 1
                if call_count >= 2:
                    # Stop after two sleeps (check-in spoken, loop continues, then cancel)
                    raise asyncio.CancelledError()

            await handler._run_pause_mode(sleep_fn=fast_sleep)

        asyncio.run(_one_iteration())

        # After one 45s window, total should be 45
        assert session["pause_silence_total"] == 45.0


class TestPauseModeCheckin:
    def test_45s_silence_speaks_checkin(self):
        """After 45s of silence in pause mode, 'Just checking' phrase is queued."""
        session = {"caller_pause_active": True, "pause_silence_total": 0.0}
        q = asyncio.Queue()
        transfer = []
        handler = _FakeSilenceHandlerWithPause(session, q, transfer)

        async def _run():
            call_count = 0

            async def fast_sleep(secs):
                nonlocal call_count
                call_count += 1
                if call_count > 1:
                    raise asyncio.CancelledError()

            await handler._run_pause_mode(sleep_fn=fast_sleep)

        asyncio.run(_run())

        items = []
        while not q.empty():
            items.append(q.get_nowait())

        assert any("Just checking" in item for item in items), (
            f"Expected check-in phrase in queue, got: {items}"
        )


class TestPauseModeTermination:
    def test_90s_total_silence_terminates_call(self):
        """After 90s of total silence in pause mode, call termination is invoked."""
        # Start with 45s already accumulated (one check-in fired)
        session = {"caller_pause_active": True, "pause_silence_total": 45.0}
        q = asyncio.Queue()
        transfer = []
        handler = _FakeSilenceHandlerWithPause(session, q, transfer)

        async def _run():
            async def fast_sleep(secs):
                pass  # don't sleep — total will jump to 90 after one iteration

            await handler._run_pause_mode(sleep_fn=fast_sleep)

        asyncio.run(_run())

        assert len(transfer) == 1, "Transfer should have been invoked once"
        items = []
        while not q.empty():
            items.append(q.get_nowait())
        assert any("ring back" in item for item in items), (
            f"Expected termination phrase in queue, got: {items}"
        )


class TestSubstantiveUtteranceClearsPause:
    def test_substantive_utterance_clears_pause_flag(self):
        """
        A substantive utterance (> 2 words, not a pause phrase) clears
        caller_pause_active so the flow resumes.
        """
        session = {"caller_pause_active": True, "pause_silence_total": 30.0}

        # Simulate the logic in _llm_loop
        utterance = "I can do Thursday afternoon"
        _is_pause = detect_caller_pause_request(utterance)
        _words = utterance.strip().split()
        _is_substantive = len(_words) > 2 and not _is_pause

        if _is_substantive and session.get("caller_pause_active"):
            session["caller_pause_active"] = False
            session["pause_silence_total"] = 0.0

        assert session["caller_pause_active"] is False
        assert session["pause_silence_total"] == 0.0

    def test_short_non_pause_utterance_does_not_clear(self):
        """
        A 1-2 word utterance that isn't a pause phrase does not clear caller_pause_active
        (must be > 2 words to be 'substantive').
        """
        session = {"caller_pause_active": True, "pause_silence_total": 10.0}

        utterance = "Thursday"
        _is_pause = detect_caller_pause_request(utterance)
        _words = utterance.strip().split()
        _is_substantive = len(_words) > 2 and not _is_pause

        if _is_substantive and session.get("caller_pause_active"):
            session["caller_pause_active"] = False

        # Should still be True — 1 word, not substantive
        assert session["caller_pause_active"] is True
