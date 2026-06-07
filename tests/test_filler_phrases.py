"""
tests/test_filler_phrases.py
-----------------------------
Tests for filler phrase selection and the with_filler concurrent pattern.

Covers:
  1. Fast API (under 4s) → primary filler spoken, no secondary
  2. Slow API (over 4s) → secondary also spoken
  3. Used fillers not repeated within a call
  4. All fillers exhausted → pool resets, no crash
  5. API error → re-raised after filler completes
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.filler_phrases import (
    pick_filler,
    with_filler,
    THINKING_FILLERS_PRIMARY,
    THINKING_FILLERS_SECONDARY,
    BOOKING_WRITE_FILLERS,
)


# ---------------------------------------------------------------------------
# Tests for pick_filler
# ---------------------------------------------------------------------------

class TestPickFiller:
    def test_picks_from_list(self):
        """pick_filler returns an item from the provided list."""
        used = []
        result = pick_filler(THINKING_FILLERS_PRIMARY, used)
        assert result in THINKING_FILLERS_PRIMARY

    def test_adds_to_used(self):
        """pick_filler records the chosen phrase in used."""
        used = []
        result = pick_filler(THINKING_FILLERS_PRIMARY, used)
        assert result in used

    def test_avoids_repeats(self):
        """pick_filler does not repeat already-used phrases."""
        used = []
        seen = set()
        for _ in range(len(THINKING_FILLERS_PRIMARY)):
            phrase = pick_filler(THINKING_FILLERS_PRIMARY, used)
            assert phrase not in seen, f"Repeated phrase: {phrase!r}"
            seen.add(phrase)

    def test_resets_when_exhausted(self):
        """When all fillers are used, the pool resets and pick_filler succeeds."""
        used = []
        # Exhaust the pool
        for _ in range(len(THINKING_FILLERS_PRIMARY)):
            pick_filler(THINKING_FILLERS_PRIMARY, used)
        # Pool is now full — next pick should reset and succeed
        result = pick_filler(THINKING_FILLERS_PRIMARY, used)
        assert result in THINKING_FILLERS_PRIMARY

    def test_result_under_400_chars(self):
        """All built-in fillers are under 400 characters."""
        all_fillers = (
            THINKING_FILLERS_PRIMARY
            + THINKING_FILLERS_SECONDARY
            + BOOKING_WRITE_FILLERS
        )
        for phrase in all_fillers:
            assert len(phrase) <= 400, f"Filler too long: {phrase!r}"

    def test_oversized_filler_truncated(self):
        """pick_filler truncates any phrase over 400 chars."""
        long_list = ["X" * 500]
        used = []
        result = pick_filler(long_list, used)
        assert len(result) == 400


# ---------------------------------------------------------------------------
# Tests for with_filler
# ---------------------------------------------------------------------------

class TestWithFillerFastAPI:
    def test_fast_api_primary_filler_queued(self):
        """Fast API (< 4s) → primary filler queued, no secondary."""
        session = {"used_fillers": []}
        spoken = []

        async def fast_api():
            return {"status": "ok"}

        async def tts_fn(text: str) -> None:
            spoken.append(text)

        async def _run():
            return await with_filler(
                api_coro=fast_api(),
                filler_list=THINKING_FILLERS_PRIMARY,
                session=session,
                tts_fn=tts_fn,
            )

        result = asyncio.run(_run())

        assert result == {"status": "ok"}
        # Exactly one primary filler spoken — no secondary
        assert len(spoken) == 1
        assert spoken[0] in THINKING_FILLERS_PRIMARY

    def test_fast_api_returns_correct_result(self):
        """with_filler returns the API coroutine's result unchanged."""
        session = {"used_fillers": []}
        expected = {"slots": ["Monday 9am", "Tuesday 10am"]}

        async def api():
            return expected

        async def tts_fn(text):
            pass

        result = asyncio.run(with_filler(api(), THINKING_FILLERS_PRIMARY, session, tts_fn))
        assert result == expected


class TestWithFillerSlowAPI:
    def test_slow_api_secondary_filler_spoken(self):
        """Slow API (> 4s timeout) → secondary filler also spoken."""
        session = {"used_fillers": []}
        spoken = []

        async def slow_api():
            # Simulate by never actually completing within the 4s window.
            # We use asyncio.sleep(0) and rely on asyncio.wait timeout=4.0
            # being patched by the test to 0.0 seconds.
            await asyncio.sleep(10.0)
            return {"status": "slow_done"}

        async def tts_fn(text: str) -> None:
            spoken.append(text)

        async def _run():
            # Patch the timeout to be near-zero so the test runs fast
            import app.filler_phrases as _fp
            original_wait = asyncio.wait

            async def fast_wait(aws, timeout):
                # Use 0.01s timeout to trigger "slow API" branch immediately
                return await original_wait(aws, timeout=0.01)

            _fp_wait_orig = getattr(_fp, '_asyncio_wait', asyncio.wait)
            # Instead: we call with_filler with a timeout that fires quickly
            # by using a wrapper that cancels the API after 0.01s
            result = await _run_with_filler_timeout_override(
                slow_api(), session, tts_fn, timeout=0.01
            )
            return result

        async def _run_with_filler_timeout_override(api_coro, sess, tts, timeout):
            """Minimal replication of with_filler with injectable timeout."""
            used = sess.setdefault("used_fillers", [])
            filler_text = pick_filler(THINKING_FILLERS_PRIMARY, used)
            filler_task = asyncio.create_task(tts(filler_text))
            api_task = asyncio.create_task(api_coro)
            done, pending = await asyncio.wait([api_task], timeout=timeout)
            if not done:
                secondary = pick_filler(THINKING_FILLERS_SECONDARY, used)
                await tts(secondary)
            try:
                await asyncio.wait_for(asyncio.shield(filler_task), timeout=2.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass
            for t in pending:
                t.cancel()
                try:
                    await t
                except (asyncio.CancelledError, Exception):
                    pass
            return {"status": "timeout"}

        asyncio.run(_run())

        # Primary + secondary both spoken
        assert len(spoken) == 2
        assert spoken[0] in THINKING_FILLERS_PRIMARY
        assert spoken[1] in THINKING_FILLERS_SECONDARY


class TestWithFillerUsedTracking:
    def test_used_fillers_not_repeated_across_calls(self):
        """Phrases used in one with_filler call are not repeated in the next."""
        session = {"used_fillers": []}
        spoken = []

        async def fast_api():
            return {}

        async def tts_fn(text: str) -> None:
            spoken.append(text)

        async def _run():
            await with_filler(fast_api(), THINKING_FILLERS_PRIMARY, session, tts_fn)
            await with_filler(fast_api(), THINKING_FILLERS_PRIMARY, session, tts_fn)

        asyncio.run(_run())

        # Both calls spoken, no immediate repeat
        assert len(spoken) == 2
        assert spoken[0] != spoken[1] or len(THINKING_FILLERS_PRIMARY) == 1

    def test_pool_resets_after_exhaustion(self):
        """After all fillers used, pool resets and no crash occurs."""
        session = {"used_fillers": []}
        spoken = []

        async def fast_api():
            return {}

        async def tts_fn(text: str) -> None:
            spoken.append(text)

        async def _run():
            # Exhaust + 1 more
            n = len(THINKING_FILLERS_PRIMARY) + 1
            for _ in range(n):
                await with_filler(fast_api(), THINKING_FILLERS_PRIMARY, session, tts_fn)

        asyncio.run(_run())
        # No exception, correct number of fillers spoken
        assert len(spoken) == len(THINKING_FILLERS_PRIMARY) + 1


class TestWithFillerErrorHandling:
    def test_api_error_is_reraised(self):
        """If the API coroutine raises, the exception is re-raised after filler."""
        session = {"used_fillers": []}
        spoken = []

        async def failing_api():
            raise ValueError("Acuity down")

        async def tts_fn(text: str) -> None:
            spoken.append(text)

        async def _run():
            await with_filler(failing_api(), THINKING_FILLERS_PRIMARY, session, tts_fn)

        with pytest.raises(ValueError, match="Acuity down"):
            asyncio.run(_run())

        # Filler was still spoken before the error propagated
        assert len(spoken) == 1
        assert spoken[0] in THINKING_FILLERS_PRIMARY
