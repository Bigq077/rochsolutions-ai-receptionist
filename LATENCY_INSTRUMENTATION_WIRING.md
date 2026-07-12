# Latency Instrumentation — Wiring Detail

**Companion to** `LATENCY_MEASUREMENT_SPEC.md` (which defines *what* to measure).
This defines *how* it's wired into the code. **Pseudocode, not final code.**
**Branch:** `latency-eval` (isolated). **Live untouched. Flag default OFF.**
**Date:** 2026-07-13

The measurement spec named six capture points across four concurrent coroutines.
The wiring problem is correlation: t1/t2 live in `llm_stream.py`; t0/t_dispatch/
t3/t4 live in `connection.py` on the handler. This spec threads a single record
through both with near-zero cost when the flag is OFF.

---

## 1. New module: `app/media_streams/latency_timing.py`

Self-contained, stdlib-only, no imports from the hot path.

```python
# PSEUDOCODE
import os, time, itertools, logging
from dataclasses import dataclass, field
from typing import Optional

LATENCY_TIMING = os.getenv("LATENCY_TIMING", "false").lower() == "true"
_lat_log = logging.getLogger("susie.latency")   # route to own sink; grep [LAT]
_turn_counter = itertools.count(1)

def new_turn(t0: float) -> Optional["TurnTiming"]:
    """Return a fresh record, or None when disabled (None short-circuits every
    stamp site → the OFF hot-path cost is one `if timing` falsy check)."""
    if not LATENCY_TIMING:
        return None
    return TurnTiming(turn_seq=next(_turn_counter), t0=t0, t_dispatch=time.monotonic())

@dataclass
class TurnTiming:
    turn_seq: int
    t0: float
    t_dispatch: float
    t1: Optional[float] = None          # first LLM token
    t2: Optional[float] = None          # first content chunk -> tts queue
    t3: Optional[float] = None          # first audio frame enqueued (any, incl. filler)
    t4: Optional[float] = None          # first audio frame sent to Twilio
    content_t3: Optional[float] = None  # first NON-filler audio enqueued
    content_t4: Optional[float] = None
    path: str = "llm"                   # llm | fast_path | filler | fallback
    outcome: str = "completed"          # completed | barged_in | abandoned | error
    eot_confident: Optional[bool] = None
    capture_phase: str = "conversation" # conversation | phone | name
    _emitted: bool = False

    def stamp(self, field_name: str, now: Optional[float] = None) -> None:
        # first-write-wins; asyncio is single-threaded so no lock needed
        if getattr(self, field_name) is None:
            setattr(self, field_name, now or time.monotonic())

    def emit(self) -> None:
        if self._emitted:
            return
        self._emitted = True
        d = lambda a, b: int((a - b) * 1000) if (a and b) else -1
        _lat_log.info(
            "[LAT] turn_seq=%d path=%s outcome=%s ttfa_ms=%d ep_dispatch_ms=%d "
            "llm_ttft_ms=%d chunk_gate_ms=%d tts_first_byte_ms=%d audio_wire_ms=%d "
            "eot_confident=%s capture_phase=%s",
            self.turn_seq, self.path, self.outcome,
            d(self.t4, self.t0), d(self.t_dispatch, self.t0),
            d(self.t1, self.t_dispatch), d(self.t2, self.t1),
            d(self.t3, self.t2), d(self.t4, self.t3),
            self.eot_confident, self.capture_phase,
        )
```

**Why `None` (not a null-object) when disabled:** the t1 site runs once *per token*.
A null-object would incur a method call every token; a `None` handle lets the site
short-circuit on a falsy check before any attribute access. Every stamp site uses
the guard form `if timing and timing.<field> is None:`.

---

## 2. Handler ownership + the two threading hops

The handler (`WebSocketCallHandler` in `connection.py`) owns `self._turn_timing`.
It is created at **t_dispatch** and read by the TTS/send loops via `self`. It is
**passed** into the LLM path (which lives in another module) as a parameter.

```
_llm_loop (connection)  ──creates self._turn_timing, passes it──▶ run_turn (llm_stream)
                                                                      │ forwards
                                                                      ▼
                                                                _stream_claude (llm_stream)
_tts_loop (connection)  ──reads self._turn_timing──▶ stamps t3/content_t3
_send_loop (connection) ──reads self._turn_timing──▶ stamps t4/content_t4, emits
```

t3/t4 need no parameter (same `self`); only t1/t2 cross the module boundary, so
only `run_turn`/`_stream_claude` gain an optional `timing` argument.

---

## 3. Stamp sites (exact anchors)

### t0 + t_dispatch — `connection.py::_llm_loop` (~:5367)

The transcript tuple already carries a monotonic stamp: `(ts, text) = await transcript_queue.get()`.

```python
# PSEUDOCODE — at the top of each dequeued turn, BEFORE run_turn(...)
ts, user_text = await self.transcript_queue.get()

# close any prior turn that never reached t4 (split-utterance / barge-in)
if self._turn_timing and not self._turn_timing._emitted:
    self._turn_timing.outcome = "abandoned"
    self._turn_timing.emit()

self._turn_timing = new_turn(t0=ts)          # None when flag OFF
if self._turn_timing:
    # capture_phase from current flow/collection state (no PII — enum only)
    self._turn_timing.capture_phase = _current_capture_phase(self.session)
```

Then pass it down:

```python
await self._llm.run_turn(user_text, self.session, ..., tts_text_queue,
                         audio_out_queue, websocket, timing=self._turn_timing)
```

### t1 + t2 — `llm_stream.py::_stream_claude` (existing flags = free anchors)

`run_turn` gains `timing=None` and forwards it to `_stream_claude(..., timing=timing)`.
Two existing booleans mark exactly the moments we want (`llm_stream.py:~1303`, `~1310`):

```python
# PSEUDOCODE — inside the `async for event in stream` token loop
if not got_first_chunk:
    got_first_chunk = True
    if timing and timing.t1 is None:          # t1 = first LLM token
        timing.stamp("t1")
    ...  # (existing filler-cancel logic unchanged)

chunk = chunker.add_token(token)
if chunk:
    if not _first_tts_emitted:
        _first_tts_emitted = True
        ...  # (existing interim-strip logic unchanged)
    chunk = sanitise_response(chunk, session)
    if chunk:
        if timing and timing.t2 is None:      # t2 = first content chunk to TTS
            timing.stamp("t2")
        await tts_text_queue.put(PRE_SLOT_MARKER + chunk)
```

Fast-path / fallback branches (`run_turn:310`, `:408`…) that put a canned string
straight to `tts_text_queue` set `timing.path` accordingly and stamp `t1`+`t2`
together (no real token stream):

```python
if timing:
    timing.path = "fast_path"      # or "fallback"
    timing.stamp("t1"); timing.stamp("t2")
```

### t3 — `connection.py::_tts_loop` / `_put_audio` (~:10910)

First audio frame onto `audio_out_queue`. Two cases — filler vs content:

```python
# PSEUDOCODE — _put_audio (filler clip path) and the synthesise put both funnel here
if self._turn_timing and self._turn_timing.t3 is None:
    self._turn_timing.stamp("t3")             # any first audio (filler counts)
    if _is_filler_frame:
        self._turn_timing.path = "filler"

# in _tts_loop, when the first NON-marker synthesised chunk yields audio:
if self._turn_timing and self._turn_timing.content_t3 is None and not _is_filler:
    self._turn_timing.stamp("content_t3")
```

### t4 — `connection.py::_send_loop` (~:10992) — also the emit site

First `media` payload sent to Twilio closes the record:

```python
# PSEUDOCODE — right after the first successful ws.send(media) of the turn
if self._turn_timing and self._turn_timing.t4 is None:
    self._turn_timing.stamp("t4")
    if _is_content_frame and self._turn_timing.content_t4 is None:
        self._turn_timing.stamp("content_t4")
    self._turn_timing.emit()                  # completed turn
```

---

## 4. Outcome closing (edge cases from the measurement spec)

| Event | Where | Action |
|---|---|---|
| Normal completion | `_send_loop` first frame | `outcome=completed`, `emit()` |
| Barge-in before t4 | existing barge-in handler | `outcome="barged_in"`, `emit()` (partial stamps, rest `-1`) |
| Split utterance / superseded | `_llm_loop` next t_dispatch | prior record `outcome="abandoned"`, `emit()` before replacing |
| TTS/LLM error, dead air | turn-end / error path | `outcome="error"`, `emit()` |

Emit is idempotent (`_emitted` guard), so a record closed by barge-in and then
reached by a late send frame won't double-log.

---

## 5. WS-C fields (populate when that lever runs; harmless otherwise)

- `eot_confident` — set at the `end_of_turn=true` site (`stt_stream.py:636`) by comparing elapsed silence at fire-time vs the configured floor: fired near `min_turn_silence` ⇒ confidence-driven; near `max_turn_silence` ⇒ fallback. Requires carrying this bool alongside the transcript tuple (extend it to `(ts, text, eot_confident)`), OR stashing it on `self` for `_llm_loop` to read at t_dispatch. **Prefer stashing on `self`** to avoid changing the transcript-tuple contract that DTMF/structured paths also use.
- `capture_phase` — read from the flow/collection state at t_dispatch (§3, t0 block). Enum only, never the digits/name themselves.

---

## 6. Cost when OFF (must be ~nil)

- `new_turn` returns `None` → `self._turn_timing = None`.
- Every stamp site is `if timing and …` / `if self._turn_timing and …` → one falsy check, no allocation, no logging, no attribute walk.
- No new task, no new queue, no I/O on the hot path.

This is what keeps the side branch byte-behaviour-identical to live until a lever
flag is flipped — satisfying the plan's isolation rule and CLAUDE.md's "no change
to real-time latency or the live flow's behaviour."

---

## 7. Build order (this doc → implementation)

1. `latency_timing.py` module (§1).
2. `self._turn_timing` field on the handler; create + close in `_llm_loop` (§3 t0/t_dispatch, §4).
3. `timing` param through `run_turn` → `_stream_claude`; t1/t2 stamps (§3).
4. t3/content_t3 in `_tts_loop`/`_put_audio`; t4/content_t4 + `emit()` in `_send_loop` (§3).
5. Outcome closers on barge-in / error paths (§4).
6. Verify OFF = identical behaviour; then flip `LATENCY_TIMING=ON` on the side branch and capture the all-levers-OFF **baseline** — the number WS-A/B/C are judged against.
7. Offline `[LAT]` parser → p50/p90/p95 table.

WS-C fields (§5) can be deferred to when that lever is worked; they default to
`None`/`conversation` and never block the baseline.
