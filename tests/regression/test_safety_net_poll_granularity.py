# tests/regression/test_safety_net_poll_granularity.py
"""
JV Bolton live call (2026-07-24, CA3e342642f57273e29e618074b87ba181) — 17 s of
dead air immediately after Susie said "Sorry, I didn't catch that".

Live-call trace:
    18:03:14.920  re-ask audio finishes
    18:03:17.001  [ms_watchdog] WATCHDOG_RETIRE q_gen=1 reason=audible_reask_done
                  (nothing re-arms for this q_gen by design — the 10 s dead-air
                   safety net is the next line of defence)
    18:03:31.990  [ms_safety_net] ... _since=15.0s ... "10s dead-air — emitting
                  safety re-ask"

Root cause: `_silence_safety_net` used ONE constant (`_INTERVAL = 10.0`) as both
the poll cadence and the dead-air threshold.  A 10 s threshold sampled on a 10 s
grid fires anywhere in [10 s, 20 s] depending on how the grid happens to align
with the silence.  This call hit the bad end: ticks landed at :21.99
(_since≈5.0, under threshold → skipped) and :31.99 (_since=15.0 → fired).

Why the obvious "just lower _INTERVAL" is wrong, and why a previous attempt at
5.0 was reverted: the same constant is ALSO the spacing between fire 1 (soft
re-ask) and fire 2 (graceful close + hangup), because fire 1 resets
_last_audio_or_transcript_ts and fire 2 must clear the threshold again.  At 5.0
the system hung up ~5 s after the re-ask and cut off a caller mid-booking.

Fix: split the constant.  `_DEAD_AIR_SEC` stays 10.0 (so nothing — re-ask or
hangup — can arrive sooner than it could before) and `_POLL_INTERVAL` drops to
2.0 so the threshold is actually honoured to within a couple of seconds.

The first test drives the real `_silence_safety_net` loop; the rest assert the
structural invariants that must not be re-conflated.
"""

import ast
import asyncio
import inspect
import textwrap

import pytest

from app.media_streams import connection as conn
from app.media_streams.connection import WebSocketCallHandler


# ── constant extraction ──────────────────────────────────────────────────────

def _safety_net_constants() -> dict:
    """Read the loop's timing constants straight out of its source AST."""
    src = textwrap.dedent(inspect.getsource(WebSocketCallHandler._silence_safety_net))
    tree = ast.parse(src)
    found = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            tgt = node.targets[0]
            if isinstance(tgt, ast.Name) and isinstance(node.value, ast.Constant):
                if isinstance(node.value.value, (int, float)):
                    found[tgt.id] = float(node.value.value)
    missing = {"_POLL_INTERVAL", "_DEAD_AIR_SEC"} - set(found)
    assert not missing, (
        f"_silence_safety_net is missing {sorted(missing)} — its poll cadence "
        "and dead-air threshold have been re-conflated into a single constant "
        f"(found: {sorted(found)}). Keep them separate: a 10s threshold "
        "sampled on a 10s grid fires anywhere in [10s, 20s], which is what "
        "produced 17s of dead air on JV Bolton 2026-07-24."
    )
    return found


# ── stub handler for the behavioural test ────────────────────────────────────

class _StubHandler:
    """Minimal stand-in exposing only what `_silence_safety_net` touches.

    The real method is bound to this, so the loop under test is the production
    one — only its collaborators are stubbed.
    """

    def __init__(self, dead_air_already: float):
        self._stop_event = asyncio.Event()
        self._llm_busy = False
        self._tts_playout_end_mono = 0.0
        self.tts_text_queue = asyncio.Queue()
        self.session = {}
        self._silence_handler = type(
            "_SH", (), {
                "_tts_playing": False,
                "_no_input_watchdog_task": None,   # watchdog retired
                "_q_gen": 1,
                "_reask_completed": True,          # ← the retired-watchdog state
                "last_question": "How can I help today?",
            },
        )()
        self._dead_air_already = dead_air_already

    async def _wait_for_start(self, _name):
        return None

    async def run(self):
        # `_silence_safety_net` seeds the anchor to "now" right after
        # _wait_for_start; rewind it so the threshold is nearly met already and
        # the test does not have to sit through a full dead-air window.
        task = asyncio.create_task(
            WebSocketCallHandler._silence_safety_net(self)
        )
        await asyncio.sleep(0.05)
        import time as _t
        self._last_audio_or_transcript_ts = _t.monotonic() - self._dead_air_already
        return task


async def test_safety_net_fires_promptly_once_the_threshold_is_met():
    """With 9 s of silence already elapsed, the re-ask must land in ~1-3 s.

    Pre-fix the loop slept a full 10 s between looks, so this took ~10 s even
    though the threshold was crossed one second in.  That granularity is what
    produced the 17 s hole on the live call.
    """
    stub = _StubHandler(dead_air_already=9.0)
    task = await stub.run()
    try:
        phrase = await asyncio.wait_for(stub.tts_text_queue.get(), timeout=6.0)
    except asyncio.TimeoutError:
        pytest.fail(
            "safety net did not emit a re-ask within 6s of the 10s dead-air "
            "threshold being crossed — the poll cadence is coarser than the "
            "threshold it gates (JV Bolton 2026-07-24: 17s of dead air)."
        )
    finally:
        stub._stop_event.set()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    assert conn._WATCHDOG_REASK_MARKER in phrase, (
        f"emitted phrase is not a watchdog re-ask: {phrase!r}"
    )


def test_poll_cadence_is_finer_than_the_dead_air_threshold():
    """The defect in one line: you cannot honour a 10 s threshold on a 10 s grid."""
    c = _safety_net_constants()
    assert c["_POLL_INTERVAL"] < c["_DEAD_AIR_SEC"], (
        f"poll cadence ({c['_POLL_INTERVAL']}s) is not finer than the dead-air "
        f"threshold ({c['_DEAD_AIR_SEC']}s); worst-case detection is therefore "
        f"{c['_DEAD_AIR_SEC'] + c['_POLL_INTERVAL']}s, not ~{c['_DEAD_AIR_SEC']}s."
    )


def test_worst_case_detection_stays_under_thirteen_seconds():
    """Observed failure was 15 s of silence before the net spoke; cap the worst case.

    Worst case is threshold + one poll interval.
    """
    c = _safety_net_constants()
    worst = c["_DEAD_AIR_SEC"] + c["_POLL_INTERVAL"]
    assert worst <= 13.0, (
        f"worst-case dead-air detection is {worst}s — the live incident was "
        "15s and felt like an abandoned call."
    )


def test_dead_air_threshold_is_not_lowered():
    """The threshold must stay at 10 s — it is also the re-ask -> hangup spacing.

    Fire 1 resets the dead-air anchor and fire 2 (graceful close + hangup) must
    clear the same threshold again.  Lowering it to 5.0 previously hung up ~5 s
    after the re-ask and cut off a caller mid-booking.  Fixing the poll cadence
    must not smuggle that regression back in.
    """
    c = _safety_net_constants()
    assert c["_DEAD_AIR_SEC"] >= 10.0, (
        f"dead-air threshold lowered to {c['_DEAD_AIR_SEC']}s — this also "
        "shortens the gap between the safety re-ask and the graceful hangup, "
        "which previously cut off a caller mid-booking."
    )
