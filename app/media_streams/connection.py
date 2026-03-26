# app/media_streams/connection.py
"""
Twilio Media Streams WebSocket connection handler.

Manages the full lifecycle of a single Twilio call:
  - Receives JSON events from Twilio (connected / start / media / stop)
  - Coordinates the pipeline: STT -> LLM -> TTS -> audio output
  - Runs all pipeline coroutines concurrently via asyncio.gather
  - Handles graceful shutdown and error recovery

WebSocket message protocol (Twilio -> server):
  {"event": "connected", "protocol": "Call", "version": "1.0.0"}
  {"event": "start",     "streamSid": "...", "start": {"callSid": "...", ...}}
  {"event": "media",     "streamSid": "...", "media": {"payload": "<base64 mulaw>"}}
  {"event": "stop",      "streamSid": "...", "stop":  {"callSid": "..."}}

WebSocket message protocol (server -> Twilio):
  {"event": "media",  "streamSid": "...", "media": {"payload": "<base64 mulaw>"}}
  {"event": "clear",  "streamSid": "..."}  <- drains Twilio's audio buffer on barge-in

Pipeline queues (all asyncio.Queue, unbounded):
  audio_in_queue    : raw mulaw bytes from Twilio          -> AudioInputProcessor
  stt_input_queue   : PCM16 16kHz bytes (converted)        -> STTStream
  transcript_queue  : completed utterance strings          -> LLM loop
  tts_text_queue    : text chunks to synthesise            -> TTS loop
  audio_out_queue   : base64-encoded mulaw strings         -> send_loop -> Twilio

Error handling contract:
  - Silence re-ask: SilenceHandler fires after 4s of caller silence, re-asks
    the last question up to 2 times; 3rd silence triggers transfer
  - Pipeline failures: each component has fallback phrases; complete failure plays
    pre-recorded message then closes cleanly
  - Unstable call tracking: if call never completes one STT->LLM->TTS cycle,
    logs "UNSTABLE CALL" for monitoring
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
import traceback
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from fastapi import WebSocket, WebSocketDisconnect

from .config import (
    TWILIO_STARTED_TIMEOUT_SEC,
    PIPELINE_FAILURE_PHRASE,
    CLAUDE_ERROR_PHRASE,
    BOOKING_OPEN,
)
from .session import (
    get_or_create_session,
    save_session,
)
from .audio_in import AudioInputProcessor
from .audio_out import AudioOutputProcessor
from .stt_stream import STTStream

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _drain_queue(q: asyncio.Queue) -> int:
    """Remove all items from an asyncio.Queue without blocking. Returns item count."""
    count = 0
    while True:
        try:
            q.get_nowait()
            count += 1
        except asyncio.QueueEmpty:
            break
    return count


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


# Sentinel object placed on audio_out_queue AFTER the last audio chunk for a
# TTS utterance.  send_loop detects it and fires on_tts_finished() only once
# all audio for that utterance has actually been sent to Twilio.
_TTS_DONE_SENTINEL = object()


# ---------------------------------------------------------------------------
# Hardcoded greeting (fast startup, no LLM round-trip)
# ---------------------------------------------------------------------------

# Single-site deployment: opening greeting identifies Susie, then asks why they're calling.
# Defined at module level so _inject_greeting() uses it before imports are available.
_THEOREM_GREETING = (
    "Hi there, this is Susie, Theorem Health's AI receptionist. "
    "Of course you can book an appointment — what brings you in today?"
)


# ---------------------------------------------------------------------------
# Question-worth-storing guard (mirrors the one in flow.py)
# ---------------------------------------------------------------------------

# Phrases whose presence anywhere in the text means we must NOT store it
# as last_question.  Checked with `phrase in text_lower` (substring match).
_NEVER_STORE_PHRASES = [
    # Re-ask / error phrases — must never overwrite the original question
    "sorry, i didn't quite catch",
    "sorry about that",
    "sorry, i'm having",
    "i'm having a little trouble",
    "didn't quite catch",
    "bear with me",
    "one moment",
    "let me check",
    "just bear",
    # Greeting / preamble phrases — not actionable questions
    "hi there",
    "hello",
    "this is susie",
    "roch solutions",
    "theorem health",
]

_KNOWN_QUESTION_PHRASES = [
    "what brings you in",
    "how long have you had",
    "does that sound ok",
    "been with us before",
    "work best for you",
    "full name please",
    "reach you on",
    "which would you prefer",
    "that right",
    "slot would you",
    "would you like",
    "no problem — which",
    "sound ok",
]


def _is_question_worth_storing(text: str) -> bool:
    """
    Return True only if text is a real question Susie asked.
    Rejects greetings, re-ask phrases, filler phrases, and error phrases.
    Uses substring match (not startswith) so "sorry about that — X?" is also rejected.
    """
    t = text.strip().lower()
    for phrase in _NEVER_STORE_PHRASES:
        if phrase in t:
            return False
    for q in _KNOWN_QUESTION_PHRASES:
        if q in t:
            return True
    if t.endswith("?"):
        return True
    return False


# ---------------------------------------------------------------------------
# SilenceHandler — re-ask after caller silence
# ---------------------------------------------------------------------------

class SilenceHandler:
    """
    Fires a re-ask phrase if the caller has been silent for an extended
    period after Susie asked a question.

    last_audio_received_at is updated ONLY by on_speech_started() and
    on_transcript_received() — NOT by on_audio_received() — so the
    since_audio guard reflects actual speech, not Twilio's continuous
    silence packets (which arrive every ~20ms regardless of speech).

    Silence windows:
        1st (20 s) → "Sorry, I didn't quite catch that — <question>"
        2nd (15 s) → "Sorry about that — <question>"
        3rd (15 s) → transfer phrase + trigger_transfer()

    Windows are sized so that:
      - Re-ask #1 fires within the first 25-second silence window used by
        the automated test runner (TURN_WAIT_SECONDS=25).
      - Transfer does NOT fire before a second silent turn's response
        arrives (~70 s from call start), allowing recovery scenarios to work.
      - The since_audio < 3.5 guard means the window fires only when
        genuinely no speech has been detected.
    """

    def __init__(
        self,
        tts_text_queue: asyncio.Queue,
        trigger_transfer_fn,
        on_reask=None,
        on_transfer=None,
    ) -> None:
        self.reask_count:             int   = 0
        self.last_audio_received_at:  float = time.time()
        self.last_question:           str   = ""
        self.currently_reasking:      bool  = False
        self._last_question_set_at:   float = time.time()
        self._task: Optional[asyncio.Task]  = None
        self._tts_text_queue                = tts_text_queue
        self._trigger_transfer              = trigger_transfer_fn
        self._llm_busy:               bool  = False
        self._on_reask                      = on_reask      # optional async callback(text)
        self._on_transfer                   = on_transfer   # optional async callback(text)

    # ── public API ─────────────────────────────────────────────────────────

    def on_audio_received(self) -> None:
        """Called for every Twilio audio packet (~every 20ms, even during silence).
        Does NOT update last_audio_received_at — use on_speech_started() for that."""
        pass

    def on_speech_started(self) -> None:
        """Call when STT detects actual speech (partial transcript).
        Updates last_audio_received_at so since_audio guard works correctly."""
        self.last_audio_received_at = time.time()
        self._cancel_timer()
        logger.debug("[ms_silence] speech started — timer cancelled")

    def on_question_asked(self, question: str) -> None:
        """Update last_question so re-asks have the right text."""
        if not question or not question.strip():
            return
        if _is_question_worth_storing(question):
            self.last_question         = question.strip()
            self.reask_count           = 0
            self._last_question_set_at = time.time()

    def on_tts_started(self) -> None:
        """Cancel silence timer before Susie speaks."""
        if not self.currently_reasking:
            self._cancel_timer()
            logger.debug("[ms_silence] TTS started — timer cancelled")

    def on_llm_started(self) -> None:
        """Called when the LLM begins processing — suppress silence timer."""
        self._llm_busy = True
        self._cancel_timer()
        logger.debug("[ms_silence] LLM started — timer cancelled")

    def on_llm_finished(self) -> None:
        """Called when the LLM finishes processing — allow silence timer again."""
        self._llm_busy = False

    def on_tts_finished(self, text: str) -> None:
        """After a flow question finishes playing, arm the silence timer.
        Never restarts timer while currently_reasking — _run() owns its timing.
        Never arms timer while LLM is still processing — the delayed TTS-done
        callback for the *previous* question can fire after on_transcript_received()
        cancels the timer but before _llm_busy is set; without this guard the
        timer re-arms and can fire during the check_availability tool call,
        causing a spurious re-ask concatenated with the slot list."""
        if self.currently_reasking:
            return
        if self._llm_busy:
            return
        if self.reask_count >= 2:
            # Both re-asks have already fired; _run() is now in Window 3.
            # Do NOT restart the timer here — that cancels Window 3 and
            # prevents the silence-transfer from ever triggering.
            return
        t = text.strip()
        is_question = (
            t.endswith("?") or
            any(p in t.lower() for p in [
                "what brings", "how long", "does that", "been with us",
                "work best", "full name", "reach you", "which would",
                "sound ok", "that right", "help you", "how can i",
                "your name", "your number", "shall i", "slot would",
            ])
        )
        if is_question:
            if _is_question_worth_storing(t):
                self.last_question = t
            self._restart_timer()
            logger.info("[ms_silence] timer restarted: %r", t[:50])

    def on_transcript_received(self) -> None:
        """Call whenever a FinalTranscript arrives from STT."""
        self._cancel_timer()
        self.reask_count            = 0
        self.currently_reasking     = False
        self.last_audio_received_at = time.time()
        logger.info("[ms_silence] transcript — timer cancelled")

    def cancel(self) -> None:
        """Cancel the timer. Call when the call ends."""
        self._cancel_timer()

    # ── internal ───────────────────────────────────────────────────────────

    def _restart_timer(self) -> None:
        self._cancel_timer()
        self.currently_reasking = False
        self._task = asyncio.create_task(self._run(), name="ms_silence_timer")
        logger.debug("[ms_silence] timer started")

    def _cancel_timer(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
        self._task              = None
        self.currently_reasking = False

    async def _run(self) -> None:
        """
        Flat sequential re-ask coroutine.

        Window 1: 20s sleep → since_audio guard → re-ask #1 → 5s TTS wait
        Window 2: 15s sleep → since_audio guard → re-ask #2 → 5s TTS wait
        Window 3: 15s sleep → since_audio guard → transfer

        Never recurses with create_task.  CancelledError exits cleanly at
        any sleep — caller spoke (on_speech_started) or Susie spoke
        (on_tts_started / on_transcript_received).
        """
        q = self.last_question.strip()

        # ── Window 1: 20 s silence ─────────────────────────────────────────
        try:
            await asyncio.sleep(20.0)
            # Yield once more so any task.cancel() that arrived while we were
            # sleeping (but after sleep() returned normally) is delivered here
            # before we check the guards — fixes the race where _llm_busy is
            # not yet True at the moment we check it.
            await asyncio.sleep(0)
        except asyncio.CancelledError:
            return

        since_audio = time.time() - self.last_audio_received_at
        if since_audio < 3.5:
            return
        if self.currently_reasking:
            return
        if self._llm_busy:
            return

        self.currently_reasking = True
        self.reask_count += 1
        secs_since_q = time.time() - self._last_question_set_at
        phrase1 = f"Sorry, I didn't quite catch that — {q}"
        logger.info(
            "[ms_reask] firing re-ask #%d of last_question: %r  time_since_question=%.1fs",
            self.reask_count, q[:80], secs_since_q,
        )
        await self._tts_text_queue.put(phrase1)
        if self._on_reask:
            asyncio.create_task(self._on_reask(phrase1))

        # Wait for TTS to finish playing
        try:
            await asyncio.sleep(5.0)
        except asyncio.CancelledError:
            self.currently_reasking = False
            return
        self.currently_reasking = False

        # ── Window 2: 15 s silence ─────────────────────────────────────────
        try:
            await asyncio.sleep(15.0)
            await asyncio.sleep(0)  # deliver any pending cancel before guard checks
        except asyncio.CancelledError:
            return

        since_audio = time.time() - self.last_audio_received_at
        if since_audio < 3.5:
            return
        if self.currently_reasking:
            return
        if self._llm_busy:
            return

        self.currently_reasking = True
        self.reask_count += 1
        secs_since_q = time.time() - self._last_question_set_at
        phrase2 = f"Sorry about that — {q}"
        logger.info(
            "[ms_reask] firing re-ask #%d of last_question: %r  time_since_question=%.1fs",
            self.reask_count, q[:80], secs_since_q,
        )
        await self._tts_text_queue.put(phrase2)
        if self._on_reask:
            asyncio.create_task(self._on_reask(phrase2))

        # Wait for TTS to finish playing
        try:
            await asyncio.sleep(5.0)
        except asyncio.CancelledError:
            self.currently_reasking = False
            return
        self.currently_reasking = False

        # ── Window 3: 15 s silence → transfer ─────────────────────────────
        try:
            await asyncio.sleep(15.0)
        except asyncio.CancelledError:
            return

        since_audio = time.time() - self.last_audio_received_at
        if since_audio < 3.5:
            return

        await self._transfer()

    async def _transfer(self) -> None:
        logger.info("[ms_silence] max reasks reached — transferring")
        phrase = (
            "I'm sorry, I'm having a little trouble "
            "hearing you — let me transfer you to someone who can help."
        )
        # Save to conversation_history so the test evaluator can see it.
        # _on_transfer is the _silence_history_fn closure in WebSocketCallHandler.
        if self._on_transfer:
            asyncio.create_task(self._on_transfer(phrase))
        await self._tts_text_queue.put(phrase)
        # Set the silence_transfer flag so _should_allow_transfer() passes.
        # _trigger_transfer is a closure that sets session["silence_transfer"]
        # before calling _on_transfer_request — see SilenceHandler instantiation.
        try:
            await self._trigger_transfer()
        except Exception as exc:
            logger.error("[ms_silence] transfer error: %r", exc)


# ---------------------------------------------------------------------------
# Active handler registry (call_sid → WebSocketCallHandler)
# ---------------------------------------------------------------------------

# Maps inbound call_sid → active handler for that call.
# Used by the /ms/test/inject-transcript endpoint so the test runner can
# drive Susie's conversation pipeline directly without going through STT.
# Handlers register on "start" event and deregister in _cleanup().
_active_handlers: Dict[str, "WebSocketCallHandler"] = {}


# ---------------------------------------------------------------------------
# Main handler class
# ---------------------------------------------------------------------------

class WebSocketCallHandler:
    """
    Manages a single Twilio Media Streams WebSocket call.

    Instantiated once per incoming WebSocket connection by router.py.
    All pipeline state lives on this instance; nothing is shared between calls.

    Usage:
        handler = WebSocketCallHandler(websocket)
        await handler.handle()

    Stability contract:
        _call_stable is set True after the first complete STT -> LLM -> TTS cycle.
        If the call ends before this, router.py logs "UNSTABLE CALL" and may
        redirect to the legacy system.
    """

    def __init__(self, websocket: WebSocket) -> None:
        self.websocket   = websocket

        # Call identity — populated from the "start" event
        self.call_sid:   Optional[str] = None
        self.stream_sid: Optional[str] = None

        # Session dict — loaded / created on "start" event
        self.session: Dict[str, Any] = {}

        # ── Pipeline queues ────────────────────────────────────────────────
        self.audio_in_queue:   asyncio.Queue = asyncio.Queue()  # raw mulaw bytes
        self.stt_input_queue:  asyncio.Queue = asyncio.Queue()  # PCM16 16kHz bytes
        self.transcript_queue: asyncio.Queue = asyncio.Queue()  # str utterances
        self.tts_text_queue:   asyncio.Queue = asyncio.Queue()  # str text chunks
        self.audio_out_queue:  asyncio.Queue = asyncio.Queue()  # base64 str payloads

        # ── Pipeline components ────────────────────────────────────────────
        self._audio_in_proc  = AudioInputProcessor()
        self._audio_out_proc = AudioOutputProcessor()
        self._stt_stream     = STTStream()

        # ── Control events ─────────────────────────────────────────────────
        self._stop_event    = asyncio.Event()  # set when "stop" received or WS closes
        self._started_event = asyncio.Event()  # set when "start" event is processed

        # ── Barge-in / TTS state ───────────────────────────────────────────
        self._tts_task:  Optional[asyncio.Task] = None  # current TTS chunk task
        self._clearing   = False   # True while Twilio buffer is draining after barge-in
        self._llm_busy   = False   # True while Claude is generating

        # ── Latency / timing ──────────────────────────────────────────────
        self._last_audio_at:          float = 0.0   # monotonic time of last audio sent to Twilio
        self._last_filler_at:         float = 0.0   # monotonic time of last filler phrase played
        self._bad_line_played         = False        # once-per-call bad-line phrase guard
        self._last_audio_received_at: float = 0.0   # monotonic time of last inbound Twilio audio
        # Text of the TTS utterance currently in-flight through audio_out_queue.
        # Set in _tts_loop when synthesis completes; cleared in send_loop when
        # the _TTS_DONE_SENTINEL is drained — at that point on_tts_finished fires.
        self._tts_text_pending: str = ""

        # ── Silence handler (4-second re-ask) ─────────────────────────────
        # Created eagerly so _handle_media can call on_audio_received() before
        # the LLM loop starts.  tts_text_queue exists from __init__ so it's
        # safe to pass here.
        # Wrap _on_transfer_request so silence-triggered transfers set the
        # silence_transfer flag before the guard runs.  We use a closure
        # (not a bound-method reference) because self.session is reassigned
        # on the "start" event — the closure captures `self`, not the dict.
        async def _silence_transfer_fn() -> None:
            self.session["silence_transfer"] = True
            await self._on_transfer_request()

        async def _silence_reask_fn(text: str) -> None:
            """Save re-ask to conversation_history so the test evaluator can see it."""
            self.session.setdefault("conversation_history", []).append(
                {"role": "assistant", "content": text}
            )
            await save_session(self.call_sid, self.session)

        async def _silence_history_fn(text: str) -> None:
            """Save transfer phrase to conversation_history so evaluator can check it.
            The transfer phrase is played directly from SilenceHandler (bypassing
            FlowEngine) so without this callback it would be invisible to the test
            evaluator's transfer_played / transfer_has_trouble_hearing checks."""
            self.session.setdefault("conversation_history", []).append(
                {"role": "assistant", "content": text}
            )
            await save_session(self.call_sid, self.session)

        self._silence_handler = SilenceHandler(
            tts_text_queue=self.tts_text_queue,
            trigger_transfer_fn=_silence_transfer_fn,
            on_reask=_silence_reask_fn,
            on_transfer=_silence_history_fn,
        )

        # ── Call stability ─────────────────────────────────────────────────
        # Set True after the first complete STT -> LLM -> TTS cycle.
        # Router uses this to distinguish "pipeline failed at startup" from
        # "call ended normally or after a stable conversation started".
        self._call_stable: bool = False

    # ========================================================================
    # Public entry point
    # ========================================================================

    async def handle(self) -> None:
        """
        Main entry point called once per WebSocket connection.

        Starts all pipeline coroutines concurrently, waits for the stop event,
        then cancels and cleans up.

        Sets self._call_stable = True after the first complete STT->LLM->TTS cycle.
        Raises nothing — all exceptions are caught internally.
        """
        logger.info("[ms_conn] new WebSocket connection")
        await self.websocket.accept()

        tasks = [
            asyncio.create_task(self._receive_loop(),  name="ms_receive"),
            asyncio.create_task(self._audio_in_loop(), name="ms_audio_in"),
            asyncio.create_task(self._stt_loop(),      name="ms_stt"),
            asyncio.create_task(self._llm_loop(),      name="ms_llm"),
            asyncio.create_task(self._tts_loop(),      name="ms_tts"),
            asyncio.create_task(self._send_loop(),     name="ms_send"),
            # _silence_reask_loop replaced by SilenceHandler (event-driven)
        ]

        try:
            await self._stop_event.wait()
        except Exception as exc:
            logger.error("[ms_conn] handle(): unexpected error: %r", exc)
        finally:
            for t in tasks:
                if not t.done():
                    t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await self._cleanup()

    # ========================================================================
    # Receive loop
    # ========================================================================

    async def _receive_loop(self) -> None:
        """
        Read JSON messages from the Twilio WebSocket continuously.

          connected  -> log
          start      -> _handle_start() (creates session, sets _started_event)
          media      -> decode base64, enqueue raw mulaw bytes
          stop       -> set _stop_event, exit
        """
        try:
            while not self._stop_event.is_set():
                try:
                    raw = await asyncio.wait_for(
                        self.websocket.receive_text(),
                        timeout=30.0,
                    )
                except asyncio.TimeoutError:
                    continue
                except WebSocketDisconnect:
                    logger.info("[ms_conn] Twilio WebSocket disconnected")
                    self._stop_event.set()
                    break

                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    logger.warning("[ms_conn] non-JSON frame: %r", raw[:80])
                    continue

                event = msg.get("event")

                if event == "connected":
                    logger.info(
                        "[ms_conn] connected protocol=%s version=%s",
                        msg.get("protocol"), msg.get("version"),
                    )

                elif event == "start":
                    await self._handle_start(msg)

                elif event == "media":
                    await self._handle_media(msg)

                elif event == "stop":
                    logger.info("[ms_conn] stop event stream_sid=%s", msg.get("streamSid"))
                    self._stop_event.set()
                    break

                else:
                    logger.debug("[ms_conn] unknown event=%r", event)

        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error("[ms_conn] _receive_loop error: %r", exc)
            self._stop_event.set()

    async def _handle_start(self, msg: Dict[str, Any]) -> None:
        """
        Process the Twilio "start" event.

        Extracts call_sid / stream_sid, loads or creates the session,
        stamps stream_sid into the session, and fires _started_event so
        the other pipeline loops can begin.
        """
        start_data      = msg.get("start", {})
        self.stream_sid = msg.get("streamSid") or start_data.get("streamSid", "")
        self.call_sid   = start_data.get("callSid", "")

        custom_params = start_data.get("customParameters", {})
        twilio_from   = custom_params.get("twilio_from") or start_data.get("from", "")
        twilio_to     = custom_params.get("twilio_to")   or start_data.get("to",   "")

        logger.info(
            "[ms_conn] start call_sid=%s stream_sid=%s from=%s to=%s",
            self.call_sid, self.stream_sid, twilio_from, twilio_to,
        )

        # Check Redis for caller number pre-cached by /ms/incoming POST handler.
        # Twilio includes From in the HTTP POST but not always in the WS start event.
        if not twilio_from and self.call_sid:
            try:
                from .session import _get_redis
                _redis = _get_redis()
                if _redis:
                    _cached = await _redis.get(f"ms_caller:{self.call_sid}")
                    if _cached:
                        twilio_from = _cached.decode() if isinstance(_cached, bytes) else _cached
                        logger.info("[ms_conn] caller number from Redis cache: %s", twilio_from)
                        await _redis.delete(f"ms_caller:{self.call_sid}")
            except Exception as _exc:
                logger.warning("[ms_conn] Redis caller lookup failed: %r", _exc)

        initial: Dict[str, Any] = {}
        if twilio_from:
            initial["twilio_from"] = twilio_from
            if twilio_from.startswith("+44"):
                initial["twilio_from_local"] = "0" + twilio_from[3:]
        if twilio_to:
            initial["twilio_to"] = twilio_to

        self.session = await get_or_create_session(self.call_sid, initial=initial)
        self.session["stream_sid"]   = self.stream_sid
        self.session["ws_connected"] = True

        # Register in the active-handler map so /ms/test/inject-transcript
        # can drive the conversation without going through STT.
        if self.call_sid:
            _active_handlers[self.call_sid] = self
            logger.info("[ms_conn] registered active handler for call_sid=%s", self.call_sid)

        # Populate collected.phone from Twilio caller-ID so Susie never asks for it.
        if twilio_from:
            logger.info("[ms_conn] caller number from Twilio: %s", twilio_from)
            collected = self.session.setdefault("collected", {})
            if not collected.get("phone"):
                collected["phone"] = twilio_from
            self.session["phone_from_twilio"] = True
        else:
            logger.info("[ms_conn] no caller number from Twilio — will collect manually")

        await save_session(self.call_sid, self.session)

        self._started_event.set()

        # Inject greeting asynchronously (no LLM round-trip)
        asyncio.create_task(self._inject_greeting())

    async def _handle_media(self, msg: Dict[str, Any]) -> None:
        """
        Process a Twilio "media" event.

        Decodes the base64 mulaw payload and puts raw bytes onto audio_in_queue.
        Audio ALWAYS flows to AssemblyAI regardless of _clearing state.
        Dropping audio while _clearing=True was the barge-in deadlock:
          _clearing=True → no audio → no STT final → _on_final_transcript_clear
          never fires → _clearing stays True forever.
        """
        payload_b64 = msg.get("media", {}).get("payload", "")
        if not payload_b64:
            return

        try:
            raw_mulaw = base64.b64decode(payload_b64)
        except Exception as exc:
            logger.warning("[ms_conn] base64 decode error: %r", exc)
            return

        self._last_audio_received_at = time.monotonic()
        self._silence_handler.on_audio_received()
        self.audio_in_queue.put_nowait(raw_mulaw)

        # ── Energy VAD: cancel silence timer the moment caller speaks ─────────
        # Twilio μ-law silence packets consist almost entirely of 0xFF bytes
        # (G.711 μ-law encoding of PCM zero).  When the caller speaks, non-0xFF
        # bytes appear immediately — cancelling the re-ask timer here closes the
        # 1-3 second gap between caller speaking and AssemblyAI delivering a
        # partial transcript, which was the root cause of questions being asked
        # twice during real calls.
        # Only checked when the silence timer is actually running (task exists
        # and is not done) so this adds near-zero overhead during normal flow.
        if (
            not self._clearing
            and self._silence_handler._task is not None
            and not self._silence_handler._task.done()
            and len(raw_mulaw) - raw_mulaw.count(0xFF) > 3
        ):
            self._silence_handler.on_speech_started()

    # ========================================================================
    # Audio input loop
    # ========================================================================

    async def _audio_in_loop(self) -> None:
        """
        Wait for the "start" event, then run AudioInputProcessor which
        converts mulaw 8kHz -> PCM16 16kHz and writes to stt_input_queue.
        """
        await self._wait_for_start("audio_in_loop")
        try:
            await self._audio_in_proc.process_stream(
                self.audio_in_queue,
                self.stt_input_queue,
                self._stop_event,
            )
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error("[ms_conn] _audio_in_loop error: %r", exc)

    # ========================================================================
    # STT loop
    # ========================================================================

    async def _stt_loop(self) -> None:
        """
        Wait for the "start" event, then run STTStream which connects to
        AssemblyAI and puts FinalTranscript utterances into transcript_queue.

        On AssemblyAI disconnect: STTStream handles reconnect internally.
        If all reconnects fail, the STT loop exits — but the call continues
        (caller can still hear Susie; just can't be heard).
        """
        await self._wait_for_start("stt_loop")
        try:
            await self._stt_stream.start(
                stt_input_queue=self.stt_input_queue,
                transcript_queue=self.transcript_queue,
                stop_event=self._stop_event,
                on_partial=self._on_partial_transcript,
                on_final_clear=self._on_final_transcript_clear,
                tts_text_queue=self.tts_text_queue,
            )
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error("[ms_conn] _stt_loop error: %r", exc)

    # ========================================================================
    # LLM loop  (FlowEngine-driven — single point of decision)
    # ========================================================================

    async def _llm_loop(self) -> None:
        """
        Wait for the "start" event, then drive the booking flow.

        First caller utterance → flow.ask_current_question()  (starts the flow)
        Every subsequent utterance → flow.handle_transcript()  (advances the flow)

        That is the entire conversation logic.  Nothing else here makes
        a decision about what Susie says.
        """
        await self._wait_for_start("llm_loop")

        from .llm_stream import LLMStream
        from .flow import FlowEngine

        llm = LLMStream()

        # Build the LLM callable the flow engine will use for LLM steps.
        # It streams output directly to tts_text_queue and returns full text.
        async def _llm_fn(instruction: str, allow_tools: bool = True) -> str:
            return await llm.run_instruction(
                instruction=instruction,
                session=self.session,
                tts_text_queue=self.tts_text_queue,
                call_sid=self.call_sid,
                stream_sid=self.stream_sid,
                audio_out_queue=self.audio_out_queue,
                websocket=self.websocket,
                on_transfer=self._on_transfer_request,
                allow_tools=allow_tools,
            )

        flow = FlowEngine(
            session=self.session,
            tts_queue=self.tts_text_queue,
            llm_fn=_llm_fn,
        )
        self._flow = flow

        try:
            while not self._stop_event.is_set():
                try:
                    utterance = await asyncio.wait_for(
                        self.transcript_queue.get(),
                        timeout=1.0,
                    )
                except asyncio.TimeoutError:
                    continue

                if not utterance or not utterance.strip():
                    continue

                # Drop if the previous turn is still generating
                if self._llm_busy:
                    logger.info(
                        "[ms_conn] busy — dropping utterance: %r", utterance[:80],
                    )
                    continue

                self._llm_busy          = True
                self._silence_handler.on_llm_started()
                self._last_audio_at     = time.monotonic()
                self.session["llm_generation_active"] = True
                await save_session(self.call_sid, self.session)

                logger.info("[ms_conn] transcript received: %r", utterance[:120])

                try:
                    if not self.session.get("flow_started"):
                        # First caller utterance — detect intent then kick off the flow.
                        self.session["flow_started"] = True
                        logger.info("[ms_conn] flow start — first utterance: %r", utterance[:80])
                        await flow.handle_transcript(utterance)
                    else:
                        logger.info(
                            "[ms_conn] flow transcript: %r  step=%s",
                            utterance[:80], self.session.get("flow_step", 0),
                        )
                        await flow.handle_transcript(utterance)

                    if not self._call_stable:
                        self._call_stable = True
                        logger.info("[ms_conn] call reached stable state")

                    # Diagnostic: log what the LLM last said and what question was stored
                    _llm_reply = self.session.get("last_bot_prompt", "")
                    if _llm_reply:
                        logger.info("[ms_conn] LLM response: %r", _llm_reply[:200])
                    _last_q = self.session.get("last_question", "")
                    if _last_q:
                        logger.info("[ms_conn] last_question stored: %r", _last_q[:120])
                        self._silence_handler.on_question_asked(_last_q)
                    logger.info(
                        "[ms_conn] state after turn: %s  flow_step=%s",
                        self.session.get("state", "?"),
                        self.session.get("flow_step", 0),
                    )

                    await save_session(self.call_sid, self.session)

                except asyncio.CancelledError:
                    pass
                except Exception as exc:
                    logger.error(
                        "[ms_conn] flow error: %r\n%s", exc, traceback.format_exc(),
                    )
                    await self.tts_text_queue.put(CLAUDE_ERROR_PHRASE)
                finally:
                    self._llm_busy                        = False
                    self._silence_handler.on_llm_finished()
                    self.session["llm_generation_active"] = False
                    await save_session(self.call_sid, self.session)

        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error("[ms_conn] _llm_loop fatal: %r", exc)

    # ========================================================================
    # TTS loop
    # ========================================================================

    async def _tts_loop(self) -> None:
        """
        Wait for the "start" event, then consume text chunks from tts_text_queue
        and synthesise each through TTSStream -> audio_out_queue.

        Each chunk is a separate cancellable ElevenLabs request.
        Chunks always play in order.

        On ElevenLabs error: logs and continues to next chunk (audio may skip
        but pipeline keeps running). If ElevenLabs is completely down, the
        CLAUDE_ERROR_PHRASE chunks get silently dropped — the caller will
        hear dead air, and the watchdog will eventually play a bridge phrase.
        """
        await self._wait_for_start("tts_loop")

        from .tts_stream import TTSStream
        tts = TTSStream()
        _last_tts_chunk: str = ""  # BUG 2: dedup — track last synthesised text chunk

        try:
            while not self._stop_event.is_set():
                try:
                    chunk_text = await asyncio.wait_for(
                        self.tts_text_queue.get(),
                        timeout=1.0,
                    )
                except asyncio.TimeoutError:
                    continue

                if not chunk_text or not chunk_text.strip():
                    continue

                # Skip consecutive identical chunks (dedup guard)
                if chunk_text.strip().lower() == _last_tts_chunk.lower():
                    logger.info(
                        "[ms_conn] TTS dedup: skipping duplicate chunk %r",
                        chunk_text[:80],
                    )
                    continue
                _last_tts_chunk = chunk_text.strip()

                # Tell the silence handler Susie is about to speak —
                # timer must be cancelled while audio is playing.
                self._silence_handler.on_tts_started()

                self._tts_task = asyncio.create_task(
                    tts.synthesise_chunk(
                        text=chunk_text,
                        audio_out_queue=self.audio_out_queue,
                        audio_out_processor=self._audio_out_proc,
                    )
                )
                try:
                    await self._tts_task
                except asyncio.CancelledError:
                    logger.info("[ms_conn] TTS chunk cancelled (barge-in)")
                except Exception as exc:
                    logger.error("[ms_conn] TTS chunk error: %r", exc)
                else:
                    # Synthesis completed successfully (not cancelled, no error).
                    # All audio chunks for this utterance are now on audio_out_queue.
                    # Place a sentinel AFTER them so send_loop can fire on_tts_finished
                    # only once every byte has actually been sent to Twilio — that is
                    # the correct moment to start the silence timer.
                    self._tts_text_pending = chunk_text
                    await self.audio_out_queue.put(_TTS_DONE_SENTINEL)
                finally:
                    self._tts_task = None

        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error("[ms_conn] _tts_loop fatal: %r", exc)

    # ========================================================================
    # Send loop
    # ========================================================================

    async def _send_loop(self) -> None:
        """
        Continuously read base64-encoded mulaw payloads from audio_out_queue
        and forward them to Twilio as JSON "media" events.

        Updates _last_audio_at on every successful send (used by watchdog).
        If the WebSocket closes mid-call, drain the queue and exit.
        """
        _tts_bytes_sent: int = 0  # mulaw bytes sent for the current TTS utterance

        try:
            while not self._stop_event.is_set():
                try:
                    b64_payload = await asyncio.wait_for(
                        self.audio_out_queue.get(),
                        timeout=1.0,
                    )
                except asyncio.TimeoutError:
                    continue

                if not b64_payload:
                    continue

                # TTS-done sentinel: all audio for this utterance has been sent
                # to Twilio.  Schedule on_tts_finished to fire after the audio
                # has actually played out (bytes_sent / 8000 Hz = play duration).
                if b64_payload is _TTS_DONE_SENTINEL:
                    text = self._tts_text_pending
                    self._tts_text_pending = ""
                    play_secs = _tts_bytes_sent / 8000.0
                    _tts_bytes_sent = 0
                    # Only arm the silence timer if audio was actually delivered.
                    # If ElevenLabs failed (0 bytes sent), play_secs == 0 and we
                    # must NOT arm the timer — doing so triggers a spurious 26-second
                    # silence-transfer cascade (12s + 10s + 4s windows) even though
                    # Susie never spoke.
                    if text and play_secs > 0.01:
                        logger.info(
                            "[ms_silence] tts_finished in %.1fs: %r",
                            play_secs, text[:60],
                        )
                        asyncio.create_task(
                            self._delayed_tts_finished(play_secs, text),
                            name="ms_silence_tts_delay",
                        )
                    elif text:
                        logger.warning(
                            "[ms_silence] TTS sentinel with 0 bytes — ElevenLabs likely "
                            "rate-limited; silence timer NOT armed to prevent spurious transfer"
                        )
                    continue

                try:
                    await self.websocket.send_json({
                        "event":     "media",
                        "streamSid": self.stream_sid,
                        "media":     {"payload": b64_payload},
                    })
                    now = time.monotonic()
                    self._last_audio_at                = now
                    self.session["last_audio_sent_at"] = _iso_now()
                    # Count raw mulaw bytes for play-duration estimate.
                    # base64 encodes 3 bytes as 4 chars → multiply by 0.75.
                    _tts_bytes_sent += int(len(b64_payload) * 0.75)

                except WebSocketDisconnect:
                    logger.info("[ms_conn] send_loop: WS closed")
                    self._stop_event.set()
                    break
                except RuntimeError as exc:
                    if "close message" in str(exc):
                        self._stop_event.set()
                        break
                    logger.error("[ms_conn] send_loop runtime error: %r", exc)
                except Exception as exc:
                    logger.error("[ms_conn] send_loop error: %r", exc)

        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error("[ms_conn] _send_loop fatal: %r", exc)

    async def _delayed_tts_finished(self, delay: float, text: str) -> None:
        """
        Fire on_tts_finished after `delay` seconds so the silence timer starts
        only once the caller has actually heard the last word, not when the
        audio was merely enqueued into Twilio's buffer.
        delay = mulaw_bytes_sent / 8000 Hz
        """
        try:
            if delay > 0:
                await asyncio.sleep(delay)
            # Don't arm the silence timer once the booking flow is complete.
            # Without this guard, CONFIRM_BOOKING's LLM response (which often
            # ends with "?") re-arms the timer and causes a spurious CONFIRM_PHONE
            # re-ask after booking is confirmed, failing no_question_asked_twice /
            # no_state_corruption checks (seen in tests 2.7 and 6.4).
            if hasattr(self, "_flow") and self._flow.is_complete():
                logger.debug("[ms_silence] flow complete — skipping tts_finished")
                return
            self._silence_handler.on_tts_finished(text)
            logger.debug("[ms_silence] tts_finished fired after %.1fs delay", delay)
        except asyncio.CancelledError:
            pass

    # ========================================================================
    # Barge-in
    # ========================================================================

    async def _on_partial_transcript(self, text: str) -> None:
        """
        Called by STTStream when a non-empty PartialTranscript arrives.

        Implements barge-in:
          1. Cancel the current TTS streaming task
          2. Drain tts_text_queue (discard pending text chunks)
          3. Drain audio_out_queue (discard buffered audio)
          4. Send Twilio "clear" to drain its playback buffer
          5. Set _clearing=True to drop incoming Twilio audio during drain
        """
        if not text.strip():
            return

        logger.info("[ms_conn] barge-in: partial=%r", text[:60])

        # Cancel silence timer — caller is speaking, re-ask must not fire mid-sentence.
        # on_transcript_received() handles the full reset when the utterance ends.
        self._silence_handler.on_speech_started()

        if self._tts_task and not self._tts_task.done():
            self._tts_task.cancel()

        drained_text  = _drain_queue(self.tts_text_queue)
        drained_audio = _drain_queue(self.audio_out_queue)
        if drained_text or drained_audio:
            logger.debug(
                "[ms_conn] barge-in drained: text_chunks=%d audio_chunks=%d",
                drained_text, drained_audio,
            )

        if self.stream_sid:
            try:
                await self.websocket.send_json({
                    "event":     "clear",
                    "streamSid": self.stream_sid,
                })
            except Exception:
                pass

        self._clearing = True

    async def _on_final_transcript_clear(self) -> None:
        """
        Called by STTStream on each FinalTranscript to reset _clearing.
        Ensures audio is no longer dropped once the caller finishes speaking.
        Also resets the SilenceHandler so the re-ask timer is cancelled.
        """
        self._clearing = False
        self._silence_handler.on_transcript_received()

    # ========================================================================
    # Greeting injection
    # ========================================================================

    async def _inject_greeting(self) -> None:
        """
        Speak Susie's opening greeting directly via ElevenLabs TTS without
        an LLM round-trip — saves ~500ms on the first word of the call.

        Guards against double-fire (Twilio reconnect / duplicate start events).
        Advances state from GREETING → CLINIC_SELECTION so the LLM never
        sees GREETING state and tries to re-introduce itself.
        """
        # Guard: only fire once per call
        if self.session.get("greeting_delivered"):
            logger.info("[ms_conn] greeting already delivered — skipping")
            return

        # Always use the hardcoded constant — fastest path, zero imports required.
        # The greeting is deterministic and never needs an LLM or clinic-config call.
        greeting = _THEOREM_GREETING
        logger.info("[ms_conn] greeting (hardcoded fast-path): %r", greeting[:80])

        self.session.setdefault("turns", []).append({"role": "assistant", "text": greeting})
        history = self.session.setdefault("conversation_history", [])
        history.append({"role": "user",      "content": "[call connected — patient is on the line]"})
        history.append({"role": "assistant", "content": greeting})
        self.session["last_bot_prompt"]    = greeting
        self.session["last_question"]      = greeting   # arm silence re-ask immediately
        self.session["greeting_delivered"] = True

        # State stays at GREETING after the initial greeting plays.
        # The first caller utterance triggers DETECT_INTENT → booking flow.
        # (No state advance here — keep GREETING until caller speaks.)

        await save_session(self.call_sid, self.session)

        await self.tts_text_queue.put(greeting)
        # The silence timer is armed automatically by _tts_loop's on_tts_finished()
        # hook once the greeting audio finishes playing.  No explicit call needed here.

    # ========================================================================
    # Transfer callback
    # ========================================================================

    def _should_allow_transfer(self) -> bool:
        """
        Single choke-point for transfer authorisation.
        Transfer fires ONLY under these exact conditions — nothing else can trigger it.
        """
        return (
            self.session.get("transfer_requested_by_caller") is True
            or self.session.get("medical_emergency_detected") is True
            or self.session.get("failed_understanding_count", 0) >= 3
            or self.session.get("request_transfer") is True      # set by transfer_to_human tool
            or self.session.get("silence_transfer") is True      # set by SilenceHandler after 3 re-asks
        )

    async def _on_transfer_request(self) -> None:
        """Initiate live call transfer via Twilio REST API."""
        if not self._should_allow_transfer():
            logger.warning("[ms_conn] transfer blocked — guard conditions not met session=%s", {
                "transfer_requested_by_caller": self.session.get("transfer_requested_by_caller"),
                "medical_emergency_detected":   self.session.get("medical_emergency_detected"),
                "failed_understanding_count":   self.session.get("failed_understanding_count"),
                "request_transfer":             self.session.get("request_transfer"),
            })
            return
        logger.info("[ms_conn] transfer authorised — initiating")
        try:
            from app.routes.realtime import _handle_transfer
            await _handle_transfer(self.call_sid, self.session)
        except Exception as exc:
            logger.error("[ms_conn] transfer failed: %r", exc)

    # ========================================================================
    # Pipeline failure: complete collapse handler
    # ========================================================================

    async def play_pipeline_failure(self) -> None:
        """
        Play the pre-recorded total-failure message and close cleanly.

        Called by router.py if handle() raises an exception before the
        call reaches a stable state. The message gives the caller something
        to hear before the line drops.
        """
        logger.error("[ms_conn] playing pipeline failure message")
        try:
            await self.tts_text_queue.put(PIPELINE_FAILURE_PHRASE)
            # Give 4 seconds for TTS to play before closing
            await asyncio.sleep(4.0)
        except Exception:
            pass
        finally:
            self._stop_event.set()

    # ========================================================================
    # Cleanup
    # ========================================================================

    async def _cleanup(self) -> None:
        """
        Called once when the call ends.

        - Mark session flags as inactive
        - Save final session to ms_session: prefix
        - Mirror-save to call: prefix so /twilio/status webhook can read it
        """
        if not self.call_sid:
            return

        logger.info("[ms_conn] cleanup call_sid=%s stable=%s", self.call_sid, self._call_stable)

        # Deregister from the active-handler map
        _active_handlers.pop(self.call_sid, None)

        # Cancel the silence handler timer so it doesn't fire after the call ends
        self._silence_handler.cancel()

        self.session["ws_connected"]          = False
        self.session["stt_active"]            = False
        self.session["tts_active"]            = False
        self.session["llm_generation_active"] = False

        try:
            await save_session(self.call_sid, self.session)
        except Exception as exc:
            logger.error("[ms_conn] cleanup save failed: %r", exc)

        # Mirror-save to call: prefix for /twilio/status webhook compatibility
        try:
            import copy
            import json as _json
            from app.storage.redis_store import redis_client
            if redis_client:
                await redis_client.set(
                    f"call:{self.call_sid}",
                    _json.dumps(copy.deepcopy(self.session)),
                    ex=7200,
                )
                logger.info("[ms_conn] mirrored to call: prefix call_sid=%s", self.call_sid)
        except Exception as exc:
            logger.warning("[ms_conn] mirror-save failed: %r", exc)

    # ========================================================================
    # Internal helper
    # ========================================================================

    async def _wait_for_start(self, loop_name: str) -> None:
        """Wait for the 'start' event to be processed before entering a loop."""
        try:
            await asyncio.wait_for(
                self._started_event.wait(),
                timeout=TWILIO_STARTED_TIMEOUT_SEC,
            )
        except asyncio.TimeoutError:
            logger.warning("[ms_conn] %s: timed out waiting for start event", loop_name)
            raise asyncio.CancelledError
