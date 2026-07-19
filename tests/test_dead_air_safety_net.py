"""
Deterministic regression harness for the dead-air safety net.

WHY THIS EXISTS
---------------
The dead-air / turn-taking subsystem (watchdog + silence handler + safety net)
shares mutable state and timing constants, so tuning one value used to silently
regress another — only discovered hours later on a live phone call.  This drives
the REAL `WebSocketCallHandler._silence_safety_net` loop against a virtual clock
and scripted event timelines, so the timing contract is verified in milliseconds
with zero phone calls.

DESIGN
------
- A `VClock` stands in for `connection.time` (monotonic/time).
- `asyncio.sleep` inside `connection` is replaced with `fake_sleep`, which jumps
  the virtual clock forward by the requested duration and fires any timeline
  callbacks that come due (a transcript arriving, the watchdog completing a
  re-ask, etc.).  The real loop therefore runs at full speed over virtual time.
- We assert INVARIANTS (did a fire happen? in what order? with what minimum
  spacing?), never exact wording or exact millisecond timing — so internal
  refactors stay free as long as the promises hold.

The promises locked here (each maps to a real bug or near-miss):
  INV1  silence past one interval → exactly one soft re-ask first (not a hangup)
  INV2  the graceful-close hangup never comes < ~1 interval after that re-ask
        (regression 2026-06-14: interval=5s hung up ~5s after the re-ask)
  INV3  a completed watchdog re-ask suppresses briefly but does NOT mute the
        net forever — the override still fires (the A2 fix)
  INV4  a caller transcript (anchor refreshed) before the threshold → no fire
  INV5  guards: llm busy / tts playing / phone-DTMF active → never fires
"""
from __future__ import annotations

import asyncio
import types

import pytest
from unittest.mock import patch

from app.media_streams import connection as conn

_MARKER = conn._WATCHDOG_REASK_MARKER
_T0 = 1000.0
_INTERVAL = 10.0  # must match _silence_safety_net._INTERVAL


# ---------------------------------------------------------------------------
# Virtual-clock infrastructure
# ---------------------------------------------------------------------------

class VClock:
    """Manually-advanced stand-in for the `time` module."""

    def __init__(self, t0: float = _T0) -> None:
        self.t = t0

    def monotonic(self) -> float:
        return self.t

    def time(self) -> float:
        return self.t


class RecQueue:
    """Records (virtual_time, text) for every prompt the safety net emits."""

    def __init__(self, clock: VClock) -> None:
        self.clock = clock
        self.items: list[tuple[float, str]] = []

    async def put(self, x: str) -> None:
        self.items.append((self.clock.t, x))


class StubSilenceHandler:
    def __init__(self) -> None:
        self._tts_playing = False
        self._q_gen = 1
        self._reask_completed = False
        self._no_input_watchdog_task = None
        self.last_question = "Is there a particular day or time that works best for you?"

    def _prompt_contains_question(self, s: str) -> bool:
        return "?" in s


def _make_stub_self(clock: VClock, session: dict | None = None) -> types.SimpleNamespace:
    """A minimal object exposing exactly what `_silence_safety_net` touches."""
    s = types.SimpleNamespace()
    s._stop_event = asyncio.Event()
    s._last_audio_or_transcript_ts = clock.t
    s._llm_busy = False
    s._silence_handler = StubSilenceHandler()
    s.session = session if session is not None else {}
    s.tts_text_queue = RecQueue(clock)

    async def _wait_for_start(_name: str) -> None:
        return None

    s._wait_for_start = _wait_for_start
    return s


async def _run(stub, clock: VClock, timeline, horizon: float):
    """Drive the real safety-net loop over virtual time.

    `timeline` is a list of (virtual_time, callable) side-effects (e.g. a caller
    transcript refreshing the anchor).  The loop is stopped at `horizon`.
    Returns the list of (time, stripped_text) prompts emitted.
    """
    real_sleep = asyncio.sleep
    tl = sorted(timeline, key=lambda e: e[0])
    tl.append((horizon, lambda: stub._stop_event.set()))
    tl.sort(key=lambda e: e[0])

    async def fake_sleep(duration, *args, **kwargs):
        target = clock.t + (duration or 0.0)
        while tl and tl[0][0] <= target:
            t, fn = tl.pop(0)
            clock.t = t
            fn()
        clock.t = target
        await real_sleep(0)  # yield so the loop body runs

    fake_time = types.SimpleNamespace(monotonic=clock.monotonic, time=clock.time)
    with patch.object(conn, "time", fake_time), \
         patch.object(conn.asyncio, "sleep", fake_sleep):
        task = asyncio.create_task(
            conn.WebSocketCallHandler._silence_safety_net(stub)
        )
        try:
            await asyncio.wait_for(task, timeout=5.0)  # real wall-clock safeguard
        except asyncio.TimeoutError:  # pragma: no cover - loop failed to terminate
            task.cancel()
            raise

    return [(t, txt.replace(_MARKER, "")) for (t, txt) in stub.tts_text_queue.items]


def _is_graceful_close(text: str) -> bool:
    return "call back" in text.lower()


# ---------------------------------------------------------------------------
# INV1 — first dead-air event is a single soft re-ask, not a hangup
# ---------------------------------------------------------------------------

async def test_first_fire_is_soft_reask_not_hangup():
    clock = VClock()
    stub = _make_stub_self(clock)
    # Caller silent the whole time; stop just after the first interval fires.
    fires = await _run(stub, clock, timeline=[], horizon=_T0 + _INTERVAL + 1)

    assert len(fires) == 1, f"expected exactly one fire, got {fires}"
    t, text = fires[0]
    assert not _is_graceful_close(text), "first fire must be a re-ask, never the hangup"
    # Fires at the first interval boundary (~10s), never before.
    assert t >= _T0 + _INTERVAL - 0.01


async def test_no_fire_before_one_interval():
    clock = VClock()
    stub = _make_stub_self(clock)
    fires = await _run(stub, clock, timeline=[], horizon=_T0 + _INTERVAL - 0.5)
    assert fires == [], f"safety net fired before one interval of silence: {fires}"


# ---------------------------------------------------------------------------
# INV2 — hangup never comes too soon after the re-ask  (the 2026-06-14 regression)
# ---------------------------------------------------------------------------

async def test_graceful_close_not_too_fast_after_reask():
    clock = VClock()
    stub = _make_stub_self(clock)
    # Let it run long enough for both the soft re-ask and the graceful close.
    fires = await _run(stub, clock, timeline=[], horizon=_T0 + (_INTERVAL * 3))

    reask = next((f for f in fires if not _is_graceful_close(f[1])), None)
    close = next((f for f in fires if _is_graceful_close(f[1])), None)
    assert reask is not None, f"expected a soft re-ask, got {fires}"
    assert close is not None, f"expected a graceful close, got {fires}"

    gap = close[0] - reask[0]
    # The whole point of reverting interval 5s -> 10s: a caller must get a real
    # window between "I can't hear you" and being hung up on.
    assert gap >= _INTERVAL - 0.01, (
        f"hangup came {gap:.1f}s after the re-ask — too fast (regression)"
    )


async def test_hangup_sets_stop_event_only_after_close_phrase():
    clock = VClock()
    stub = _make_stub_self(clock)
    fires = await _run(stub, clock, timeline=[], horizon=_T0 + (_INTERVAL * 3))
    assert any(_is_graceful_close(t) for _, t in fires)
    assert stub._stop_event.is_set(), "graceful close must terminate the call"
    # The close phrase is the LAST thing emitted before hangup.
    assert _is_graceful_close(fires[-1][1])


# ---------------------------------------------------------------------------
# INV3 — a completed watchdog re-ask suppresses briefly but does not mute forever
# ---------------------------------------------------------------------------

async def test_completed_watchdog_reask_still_allows_override():
    clock = VClock()
    stub = _make_stub_self(clock)
    stub._silence_handler._reask_completed = True  # watchdog already re-asked
    fires = await _run(stub, clock, timeline=[], horizon=_T0 + _INTERVAL + 1)
    # Override must still fire once silence exceeds the suppression window —
    # otherwise the 25s dead-air hole (Bug A2) returns.
    assert len(fires) == 1, f"override failed to fire after watchdog re-ask: {fires}"
    assert not _is_graceful_close(fires[0][1])


# ---------------------------------------------------------------------------
# INV4 — a caller transcript before the threshold cancels the fire
# ---------------------------------------------------------------------------

async def test_transcript_before_threshold_prevents_fire():
    clock = VClock()
    stub = _make_stub_self(clock)

    def caller_spoke():
        # Mirrors on_transcript_received refreshing the dead-air anchor.
        stub._last_audio_or_transcript_ts = clock.t

    # Caller speaks at +5s; we stop at +12s.  Without the refresh a fire would
    # land at +10s; with it, _since is only ~7s at the +12s tick → no fire.
    fires = await _run(
        stub, clock,
        timeline=[(_T0 + 5.0, caller_spoke)],
        horizon=_T0 + 12.0,
    )
    assert fires == [], f"safety net fired despite a fresh transcript: {fires}"


# ---------------------------------------------------------------------------
# INV5 — guards: never fire while busy / speaking / collecting keypad digits
# ---------------------------------------------------------------------------

async def test_no_fire_while_llm_busy():
    clock = VClock()
    stub = _make_stub_self(clock)
    stub._llm_busy = True
    fires = await _run(stub, clock, timeline=[], horizon=_T0 + (_INTERVAL * 2) + 1)
    assert fires == [], f"fired while LLM busy: {fires}"


async def test_no_fire_while_tts_playing():
    clock = VClock()
    stub = _make_stub_self(clock)
    stub._silence_handler._tts_playing = True
    fires = await _run(stub, clock, timeline=[], horizon=_T0 + (_INTERVAL * 2) + 1)
    assert fires == [], f"fired while TTS playing: {fires}"


async def test_no_fire_while_phone_dtmf_active():
    clock = VClock()
    stub = _make_stub_self(clock, session={"v3_phone_dtmf_active": True})
    fires = await _run(stub, clock, timeline=[], horizon=_T0 + (_INTERVAL * 2) + 1)
    assert fires == [], f"fired during keypad phone entry: {fires}"


# ---------------------------------------------------------------------------
# Bug C — the replayed re-ask must not double up fillers
# ---------------------------------------------------------------------------

async def test_reask_does_not_double_filler():
    clock = VClock()
    stub = _make_stub_self(clock)
    # Stored question already opens with its own acknowledgement filler.
    stub._silence_handler.last_question = (
        "No problem — do you prefer mornings or afternoons?"
    )
    fires = await _run(stub, clock, timeline=[], horizon=_T0 + _INTERVAL + 1)

    assert len(fires) == 1, f"expected one re-ask, got {fires}"
    _, text = fires[0]
    low = text.lower()
    # It prefixes "Sorry — I can't quite hear you —"; the stored question's own
    # "No problem —" filler must be stripped so we don't stack two fillers.
    assert "no problem" not in low, f"double filler in re-ask: {text!r}"
    # The actual question content must survive.
    assert "mornings or afternoons" in low, f"lost question content: {text!r}"


# ---------------------------------------------------------------------------
# RC-3 — the capture-phase re-ask keeps context; never the greeting reset
# (2026-07-19 jv_v1 name-capture dead-air)
# ---------------------------------------------------------------------------

async def test_name_capture_reask_asks_for_name_not_greeting():
    clock = VClock()
    # Session is mid name-capture: the last prompt asked for the caller's name,
    # so capture_phase(session) == "name".
    stub = _make_stub_self(
        clock,
        session={"last_bot_prompt": "could I take your first name and surname?"},
    )
    fires = await _run(stub, clock, timeline=[], horizon=_T0 + _INTERVAL + 1)

    assert len(fires) == 1, f"expected exactly one re-ask, got {fires}"
    _, text = fires[0]
    low = text.lower()
    # Must re-ask for the NAME, not reset the flow with the greeting.
    assert ("name" in low or "surname" in low), f"name re-ask lost its subject: {text!r}"
    assert "how can i help" not in low, (
        f"name-capture dead-air fell back to the greeting reset (RC-3): {text!r}"
    )


async def test_phone_confirm_reask_keeps_number_context_not_greeting():
    clock = VClock()
    # Verbal phone-confirm step (NOT keypad DTMF, which is handled separately).
    stub = _make_stub_self(clock, session={"v3_awaiting_phone_confirm": True})
    fires = await _run(stub, clock, timeline=[], horizon=_T0 + _INTERVAL + 1)

    assert len(fires) == 1, f"expected exactly one re-ask, got {fires}"
    _, text = fires[0]
    low = text.lower()
    assert "number" in low, f"phone re-ask lost the number context: {text!r}"
    assert "how can i help" not in low, (
        f"phone-confirm dead-air fell back to the greeting reset (RC-3): {text!r}"
    )


async def test_conversation_dead_air_still_uses_generic_fallback():
    """Control: outside capture phases, behaviour is unchanged — a stored
    non-name question is replayed (RC-3 must not hijack normal turns)."""
    clock = VClock()
    stub = _make_stub_self(clock)  # default session {}, capture_phase == conversation
    fires = await _run(stub, clock, timeline=[], horizon=_T0 + _INTERVAL + 1)

    assert len(fires) == 1, f"expected exactly one re-ask, got {fires}"
    _, text = fires[0]
    low = text.lower()
    # The stub's last_question is the slot-timing question — it should be replayed,
    # not overridden by a name/phone capture phrase.
    assert "works best for you" in low, f"conversation re-ask changed: {text!r}"
