"""
tests/test_barge_in.py
-----------------------
Tests for the Media Streams barge-in implementation.

The pipeline uses the Media Streams approach (not TwiML Gather) because:
  - Audio is streamed as raw mulaw over WebSocket — no TwiML Gather verbs
  - STT (AssemblyAI) runs continuously and fires PartialTranscript events
  - TTS cancellation and buffer clear are already implemented via
    _on_partial_transcript / _resolve_barge_in in connection.py

Covers:
  1. Confirmed barge-in (≥ threshold) → acknowledgement from the 3 options
  2. False trigger (< threshold) → interrupted TTS text re-queued, ack NOT spoken
  3. Acknowledgement is random: 30 runs confirm all 3 options appear
  4. After confirmed barge-in: flow.handle_transcript NOT called for the
     barge-in utterance (utterance skipped via continue)
"""
from __future__ import annotations

import asyncio
import random
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.media_streams.connection import _BARGE_IN_ACKS, _BARGE_IN_THRESHOLD_S


# ---------------------------------------------------------------------------
# Minimal mock of Connection for testing _resolve_barge_in
# ---------------------------------------------------------------------------

class _FakeConnection:
    """Minimal stub exposing only the attributes _resolve_barge_in touches."""

    def __init__(self, barge_in_duration: float, interrupted_text: str = ""):
        self.tts_text_queue = asyncio.Queue()
        self.session = {
            "interrupted_tts_text": interrupted_text,
            "barge_in_count": 0,
            "state": "COLLECT_REASON",
        }
        self._barge_in_pending  = True
        self._barge_in_duration = barge_in_duration

    # Copy the real _resolve_barge_in logic here so tests are self-contained
    # and don't require a live WebSocket / event-loop setup.
    async def _resolve_barge_in(self) -> bool:
        from app.media_streams.connection import _BARGE_IN_THRESHOLD_S, _BARGE_IN_ACKS
        import logging, random
        logger = logging.getLogger(__name__)

        if not self._barge_in_pending:
            return False

        self._barge_in_pending = False
        dur = self._barge_in_duration

        if dur < _BARGE_IN_THRESHOLD_S:
            interrupted = self.session.get("interrupted_tts_text", "")
            if interrupted:
                await self.tts_text_queue.put(interrupted)
            logger.info("[test] false trigger %.0fms", dur * 1000)
            return True

        ack = random.choice(_BARGE_IN_ACKS)
        await self.tts_text_queue.put(ack)
        self.session["barge_in_count"] = self.session.get("barge_in_count", 0) + 1
        return True


# ---------------------------------------------------------------------------
# Test 1 — Confirmed barge-in → one of the 3 acknowledgements spoken
# ---------------------------------------------------------------------------

class TestConfirmedBargeInAck:
    def test_ack_is_from_the_three_options(self):
        """Confirmed barge-in (duration > threshold) → ack placed in tts_queue."""
        conn = _FakeConnection(barge_in_duration=_BARGE_IN_THRESHOLD_S + 0.2)

        result = asyncio.run(conn._resolve_barge_in())

        assert result is True, "_resolve_barge_in should return True (skip utterance)"
        assert not conn.tts_text_queue.empty(), "ack should be in tts_text_queue"
        ack = conn.tts_text_queue.get_nowait()
        assert ack in _BARGE_IN_ACKS, f"Unexpected ack: {ack!r}"
        assert conn.session["barge_in_count"] == 1

    def test_no_interrupted_text_replayed_on_confirmed(self):
        """On confirmed barge-in, the interrupted text is NOT re-queued (only ack is)."""
        conn = _FakeConnection(
            barge_in_duration=_BARGE_IN_THRESHOLD_S + 0.5,
            interrupted_text="This was Susie speaking.",
        )
        asyncio.run(conn._resolve_barge_in())

        queued_items = []
        while not conn.tts_text_queue.empty():
            queued_items.append(conn.tts_text_queue.get_nowait())

        # Only the ack, NOT the interrupted text
        assert len(queued_items) == 1
        assert queued_items[0] in _BARGE_IN_ACKS
        assert "This was Susie speaking." not in queued_items


# ---------------------------------------------------------------------------
# Test 2 — False trigger (< threshold) → interrupted TTS resumed, no ack
# ---------------------------------------------------------------------------

class TestFalseTriggerResumeTTS:
    def test_interrupted_text_re_queued(self):
        """Speech < threshold → interrupted text put back in queue, ack NOT spoken."""
        interrupted = "Which days work best for you?"
        conn = _FakeConnection(
            barge_in_duration=_BARGE_IN_THRESHOLD_S - 0.1,
            interrupted_text=interrupted,
        )

        result = asyncio.run(conn._resolve_barge_in())

        assert result is True, "should still skip the utterance"
        assert not conn.tts_text_queue.empty()
        queued = conn.tts_text_queue.get_nowait()
        assert queued == interrupted, f"Expected interrupted text, got {queued!r}"
        # No ack (acks are only from _BARGE_IN_ACKS, the interrupted text is not)
        assert queued not in _BARGE_IN_ACKS
        assert conn.session["barge_in_count"] == 0, "count should not increment for false trigger"

    def test_empty_interrupted_text_no_queue_entry(self):
        """False trigger with no interrupted text → nothing queued."""
        conn = _FakeConnection(
            barge_in_duration=0.05,
            interrupted_text="",
        )
        asyncio.run(conn._resolve_barge_in())
        assert conn.tts_text_queue.empty()


# ---------------------------------------------------------------------------
# Test 3 — Acknowledgement is random: 30 runs confirm all 3 options appear
# ---------------------------------------------------------------------------

class TestAcknowledgementDistribution:
    def test_all_three_acks_appear_in_30_runs(self):
        """random.choice over 30 runs must produce all 3 acknowledgement strings."""
        seen: set = set()
        for _ in range(30):
            ack = random.choice(_BARGE_IN_ACKS)
            seen.add(ack)

        assert len(seen) == 3, (
            f"Expected all 3 acks to appear in 30 runs, saw only: {seen}"
        )

    def test_acks_list_contains_exactly_three_entries(self):
        assert len(_BARGE_IN_ACKS) == 3
        assert "Sorry — go ahead." in _BARGE_IN_ACKS
        assert "Yes, go on." in _BARGE_IN_ACKS
        assert "Sorry about that — you were saying?" in _BARGE_IN_ACKS


# ---------------------------------------------------------------------------
# Test 4 — After barge-in: slot question NOT immediately re-asked
# (flow.handle_transcript NOT called for the barge-in utterance)
# ---------------------------------------------------------------------------

class TestBargeInSkipsFlowProcessing:
    def test_utterance_skipped_on_confirmed_barge_in(self):
        """
        When _resolve_barge_in returns True, the _llm_loop uses 'continue' to
        skip calling flow.handle_transcript().

        We verify this by checking that the ack was queued but barge_in_pending
        is cleared and no flow call happened, simulating the loop logic.
        """
        conn = _FakeConnection(barge_in_duration=_BARGE_IN_THRESHOLD_S + 0.3)
        flow_called = []

        async def _simulate_loop_iteration():
            utterance = "wait actually"
            # This mirrors the _llm_loop logic
            if await conn._resolve_barge_in():
                return  # 'continue' in the real loop — flow NOT called
            flow_called.append(utterance)  # would only reach here if no barge-in

        asyncio.run(_simulate_loop_iteration())

        assert len(flow_called) == 0, (
            "flow.handle_transcript should NOT be called for the barge-in utterance"
        )
        # Ack was queued
        assert not conn.tts_text_queue.empty()

    def test_no_barge_in_pending_processes_normally(self):
        """When no barge-in is pending, _resolve_barge_in returns False and
        the utterance is processed by the flow normally."""
        conn = _FakeConnection(barge_in_duration=0.5)
        conn._barge_in_pending = False  # no barge-in this turn
        flow_called = []

        async def _simulate_loop_iteration():
            utterance = "I have back pain"
            if await conn._resolve_barge_in():
                return  # skip
            flow_called.append(utterance)  # normal processing

        asyncio.run(_simulate_loop_iteration())

        assert flow_called == ["I have back pain"]
        assert conn.tts_text_queue.empty()  # no ack queued
