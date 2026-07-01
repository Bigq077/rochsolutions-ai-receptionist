"""
tests/test_greeting_disclosure_guard.py
----------------------------------------
TDD red-phase tests for the "press 1 to speak to Mark" disclosure guard
(production sign-off sweep, CALL 1 / Issue 1, 2026-06-30).

Bug: the v3 greeting is queued as a single string in _inject_greeting()
and split into two TTS sub-chunks by split_tts_text(). A stray STT partial
during playback of the second sub-chunk ("...press 1, otherwise how can I
help you today?") can trigger the "confirmed barge-in with real content"
path in _on_partial_transcript/_resolve_barge_in, which cuts the audio via
a Twilio `clear` event BEFORE the mandatory press-1 disclosure finishes
playing. conversation_history already recorded the full greeting text
eagerly at queue-time, so the next LLM turn has no idea the disclosure
was never actually heard, and skips straight to "How can I help you?".

Fix under test (NOT YET IMPLEMENTED as of writing): a new
_BARGE_PROTECTED_MARKER sentinel, prepended to the v3 greeting text at the
source (_inject_greeting), stripped in the TTS loop's existing marker block
(mirrors _WATCHDOG_REASK_MARKER / ACK_FILLER_MARKER / PRE_SLOT_MARKER), and
used to arm a dedicated self._barge_protected_active flag for that one
queued item only -- checked alongside the existing _clinical_response_active
guard in _on_partial_transcript so the disclosure can't be clipped.

These tests are expected to FAIL right now (ImportError on
_BARGE_PROTECTED_MARKER, which does not exist in connection.py yet) --
that failure IS the red phase. They should pass once the marker constant,
the per-chunk strip/arm step, and the extended guard check are implemented.

Covers:
  1. Marker is stripped from chunk_text and arms _barge_protected_active
  2. An unmarked chunk (no marker) never arms the flag -- this is the
     regression guard against the rejected "bare substring match on
     'press 1'" approach, which would have also caught the DTMF clinic-
     selection ladder ("press 1 for Awlstuh", CALL 2 in the sweep) and
     made it falsely barge-in-immune.
  3. The flag is self-clearing: armed for the marked item's iteration
     only, back to False on the very next (unmarked) item -- guards
     against barge-in being silently disabled for the rest of the call.
  4. The _on_partial_transcript guard: teardown (Twilio clear / TTS
     cancel / _barge_in_pending) is suppressed while protected, and
     proceeds normally once unprotected.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.media_streams.connection import _BARGE_PROTECTED_MARKER


# ---------------------------------------------------------------------------
# Stub 1 -- mirrors the per-chunk marker-strip block in _tts_loop
# (connection.py ~9601-9647 for the existing markers, ~9761-9786 for the
# arm/reset step alongside _clinical_response_active).
# ---------------------------------------------------------------------------

class _FakeTTSLoopChunkHandler:
    """
    Minimal stub of ONE outer _tts_loop iteration (one dequeued
    tts_text_queue item), scoped to marker stripping + flag arming.

    self._barge_protected_active is reset (not just conditionally set)
    on every call to process_chunk(), the same way the real
    self._clinical_response_active is unconditionally reassigned each
    iteration -- this is what makes the flag self-clearing rather than
    something that has to be remembered to be turned off.
    """

    def __init__(self):
        self._barge_protected_active = False

    def process_chunk(self, chunk_text: str) -> str:
        is_protected = chunk_text.startswith(_BARGE_PROTECTED_MARKER)
        if is_protected:
            chunk_text = chunk_text[len(_BARGE_PROTECTED_MARKER):]
        self._barge_protected_active = is_protected
        return chunk_text


# ---------------------------------------------------------------------------
# Stub 2 -- mirrors the extended guard check in _on_partial_transcript
# (connection.py ~10453: "if self._clinical_response_active: ... return").
# ---------------------------------------------------------------------------

class _FakePartialTranscriptGuard:
    """
    Minimal stub of the barge-in teardown guard. Real teardown (Twilio
    `clear`, _tts_task.cancel(), arming _barge_in_pending) is represented
    here by setting self.teardown_called / self._barge_in_pending --
    suppressed whenever _clinical_response_active OR
    _barge_protected_active is True, exactly like the real `if` at
    connection.py ~10453 will be extended to check.
    """

    def __init__(self, clinical_active: bool = False, barge_protected_active: bool = False):
        self._clinical_response_active = clinical_active
        self._barge_protected_active = barge_protected_active
        self._barge_in_pending = False
        self.teardown_called = False

    def on_partial_transcript(self) -> None:
        if self._clinical_response_active or self._barge_protected_active:
            return  # suppressed -- TTS keeps playing, nothing torn down
        self.teardown_called = True
        self._barge_in_pending = True


# ---------------------------------------------------------------------------
# Test 1 -- marker stripped, flag armed
# ---------------------------------------------------------------------------

class TestMarkerStripAndArm:
    def test_marked_chunk_is_stripped_and_arms_flag(self):
        handler = _FakeTTSLoopChunkHandler()
        greeting = (
            "Hi there, I'm Susie, Theorem Health's AI receptionist — "
            "to speak to Mark directly press 1, otherwise how can I help you today?"
        )
        result = handler.process_chunk(_BARGE_PROTECTED_MARKER + greeting)

        assert result == greeting, "marker must be stripped, text unchanged otherwise"
        assert not result.startswith(_BARGE_PROTECTED_MARKER)
        assert handler._barge_protected_active is True

    def test_unmarked_chunk_never_arms_flag(self):
        """Regression guard: plain text containing 'press 1' (e.g. the DTMF
        clinic-selection ladder, 'press 1 for Awlstuh') must NOT arm the
        guard just because it shares wording with the protected greeting --
        only the explicit marker should, never content-sniffing."""
        handler = _FakeTTSLoopChunkHandler()
        result = handler.process_chunk(
            "No problem at all — on your keypad, just press 1 for Awlstuh, or 2 for Redditch."
        )

        assert result == "No problem at all — on your keypad, just press 1 for Awlstuh, or 2 for Redditch."
        assert handler._barge_protected_active is False


# ---------------------------------------------------------------------------
# Test 2 -- flag is self-clearing across subsequent queue items
# ---------------------------------------------------------------------------

class TestFlagAutoClears:
    def test_flag_clears_on_next_unmarked_item(self):
        handler = _FakeTTSLoopChunkHandler()
        handler.process_chunk(_BARGE_PROTECTED_MARKER + "protected greeting text")
        assert handler._barge_protected_active is True, "sanity check: armed first"

        handler.process_chunk("How can I help you today?")
        assert handler._barge_protected_active is False, (
            "flag must NOT leak past the protected item's own iteration -- "
            "otherwise barge-in stays disabled for the rest of the call"
        )

    def test_flag_does_not_arm_for_consecutive_unmarked_items(self):
        handler = _FakeTTSLoopChunkHandler()
        for text in ("Right — Alcester it is.", "Which day works best for you?"):
            handler.process_chunk(text)
            assert handler._barge_protected_active is False


# ---------------------------------------------------------------------------
# Test 3 -- _on_partial_transcript guard suppresses/permits teardown
# ---------------------------------------------------------------------------

class TestBargeInGuardHonoursProtectedFlag:
    def test_teardown_suppressed_when_barge_protected(self):
        guard = _FakePartialTranscriptGuard(barge_protected_active=True)
        guard.on_partial_transcript()

        assert guard.teardown_called is False
        assert guard._barge_in_pending is False

    def test_teardown_proceeds_when_not_protected(self):
        """Sanity check the guard isn't unconditionally suppressing
        everything -- normal barge-in elsewhere in the call (Bug A / G24)
        must still work."""
        guard = _FakePartialTranscriptGuard(barge_protected_active=False)
        guard.on_partial_transcript()

        assert guard.teardown_called is True
        assert guard._barge_in_pending is True

    def test_existing_clinical_guard_still_honoured(self):
        """The new flag is additive -- it must not regress the existing
        _clinical_response_active guard it sits alongside."""
        guard = _FakePartialTranscriptGuard(clinical_active=True, barge_protected_active=False)
        guard.on_partial_transcript()

        assert guard.teardown_called is False
        assert guard._barge_in_pending is False
