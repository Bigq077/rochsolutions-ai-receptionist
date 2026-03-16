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


# ---------------------------------------------------------------------------
# Hardcoded greeting (fast startup, no LLM round-trip)
# ---------------------------------------------------------------------------

# Single-site deployment: no clinic selection question in the greeting.
_THEOREM_GREETING = (
    "Hi there, this is Susie, Theorem Health's AI receptionist. "
    "How can I help you today?"
)


# ---------------------------------------------------------------------------
# SilenceHandler — re-ask after 4 s of caller silence
# ---------------------------------------------------------------------------

class SilenceHandler:
    """
    Fires a re-ask phrase if the caller has been silent for 4 seconds after
    Susie asked a question.

    on_audio_received()         — call on every inbound Twilio audio chunk
    on_question_asked(text)     — call whenever any text is sent to TTS
    on_transcript_received()    — call whenever a FinalTranscript arrives
    cancel()                    — call when the call ends

    Behaviour:
      - 1st silence  → "Sorry, I didn't quite catch that — <question>"
      - 2nd silence  → "Sorry about that — <question>"
      - 3rd silence  → transfer phrase + trigger_transfer()
      - Timer resets whenever the caller speaks (on_transcript_received)
      - Timer postpones if caller audio was received < 3 s ago
    """

    def __init__(
        self,
        tts_text_queue: asyncio.Queue,
        trigger_transfer_fn,
    ) -> None:
        self.reask_count:             int   = 0
        self.last_audio_received_at:  float = time.time()
        self.last_question:           str   = ""
        self._task: Optional[asyncio.Task]  = None
        self._tts_text_queue                = tts_text_queue
        self._trigger_transfer              = trigger_transfer_fn

    # ── public API ─────────────────────────────────────────────────────────

    def on_audio_received(self) -> None:
        """Call every time a Twilio audio chunk arrives."""
        self.last_audio_received_at = time.time()

    def on_question_asked(self, question: str) -> None:
        """Call whenever any text is sent to TTS (so it can be re-asked)."""
        if not question or not question.strip():
            return
        self.last_question = question.strip()
        self.reask_count   = 0
        self._restart_timer()

    def on_transcript_received(self) -> None:
        """Call whenever a FinalTranscript arrives from STT."""
        self.reask_count = 0
        self._cancel_timer()

    def cancel(self) -> None:
        """Cancel the timer. Call when the call ends."""
        self._cancel_timer()

    # ── internal ───────────────────────────────────────────────────────────

    def _restart_timer(self) -> None:
        self._cancel_timer()
        self._task = asyncio.create_task(self._run(), name="ms_silence_timer")

    def _cancel_timer(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = None

    async def _run(self) -> None:
        try:
            await asyncio.sleep(4.0)
        except asyncio.CancelledError:
            return

        # If caller audio arrived very recently, they may be mid-speech —
        # postpone and wait another 4 s rather than interrupting.
        since_audio = time.time() - self.last_audio_received_at
        if since_audio < 3.0:
            self._task = asyncio.create_task(self._run(), name="ms_silence_timer")
            return

        self.reask_count += 1
        q = self.last_question.strip()

        if self.reask_count == 1:
            phrase = f"Sorry, I didn't quite catch that — {q}"
        elif self.reask_count == 2:
            phrase = f"Sorry about that — {q}"
        else:
            await self._transfer()
            return

        logger.info("[ms_silence] reask #%d: %r", self.reask_count, phrase[:80])
        await self._tts_text_queue.put(phrase)
        # Restart timer for the next silence window
        self._task = asyncio.create_task(self._run(), name="ms_silence_timer")

    async def _transfer(self) -> None:
        logger.info("[ms_silence] max reasks reached — transferring")
        await self._tts_text_queue.put(
            "I'm sorry, I'm having a little trouble "
            "hearing you — let me get someone to help."
        )
        try:
            await self._trigger_transfer()
        except Exception as exc:
            logger.error("[ms_silence] transfer error: %r", exc)


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

        # ── Silence handler (4-second re-ask) ─────────────────────────────
        # Created eagerly so _handle_media can call on_audio_received() before
        # the LLM loop starts.  tts_text_queue exists from __init__ so it's
        # safe to pass here.
        self._silence_handler = SilenceHandler(
            tts_text_queue=self.tts_text_queue,
            trigger_transfer_fn=self._on_transfer_request,
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
        async def _llm_fn(instruction: str) -> str:
            return await llm.run_instruction(
                instruction=instruction,
                session=self.session,
                tts_text_queue=self.tts_text_queue,
                call_sid=self.call_sid,
                stream_sid=self.stream_sid,
                audio_out_queue=self.audio_out_queue,
                websocket=self.websocket,
                on_transfer=self._on_transfer_request,
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
                self._last_audio_at     = time.monotonic()
                self.session["llm_generation_active"] = True
                await save_session(self.call_sid, self.session)

                try:
                    if not self.session.get("flow_started"):
                        # First caller utterance — detect intent then kick off the flow.
                        # ask_current_question() is a no-op for DETECT_INTENT (no question
                        # to play); handle_transcript() classifies the utterance and routes
                        # to the correct flow, which then asks its first question.
                        self.session["flow_started"] = True
                        logger.info(
                            "[ms_conn] flow start — first utterance: %r", utterance[:80],
                        )
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

                    # Arm the silence handler with whatever question was just asked
                    last_q = self.session.get("last_question", "")
                    if last_q:
                        self._silence_handler.on_question_asked(last_q)

                    await save_session(self.call_sid, self.session)
                    logger.info(
                        "[ms_conn] flow_step=%s after turn",
                        self.session.get("flow_step", 0),
                    )

                except asyncio.CancelledError:
                    pass
                except Exception as exc:
                    logger.error(
                        "[ms_conn] flow error: %r\n%s", exc, traceback.format_exc(),
                    )
                    await self.tts_text_queue.put(CLAUDE_ERROR_PHRASE)
                finally:
                    self._llm_busy                        = False
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

                # BUG 2 FIX: skip consecutive identical chunks.
                # This prevents fast-path interim phrases ("Let me check for you")
                # from playing twice when the LLM response starts with the same text.
                if chunk_text.strip().lower() == _last_tts_chunk.lower():
                    logger.info(
                        "[ms_conn] TTS dedup: skipping duplicate chunk %r",
                        chunk_text[:80],
                    )
                    continue
                _last_tts_chunk = chunk_text.strip()

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

                try:
                    await self.websocket.send_json({
                        "event":     "media",
                        "streamSid": self.stream_sid,
                        "media":     {"payload": b64_payload},
                    })
                    now = time.monotonic()
                    self._last_audio_at                = now
                    self.session["last_audio_sent_at"] = _iso_now()

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

        # Fast path: use hardcoded constant so TTS starts within one asyncio
        # tick of the start event — no imports, no function calls required.
        # _build_greeting is still called to support multi-clinic deployments;
        # its result overrides the constant only when it returns a valid string.
        greeting = _THEOREM_GREETING
        try:
            from app.clinic_config import get_clinic
            from app.routes.twilio import _build_greeting
            clinic   = get_clinic(self.session.get("clinic_id"))
            _dynamic = _build_greeting(clinic)
            if _dynamic and len(_dynamic.strip()) > 20:
                greeting = _dynamic
        except Exception:
            pass  # fall through to hardcoded _THEOREM_GREETING

        logger.info("[ms_conn] greeting: %r", greeting[:80])

        self.session.setdefault("turns", []).append({"role": "assistant", "text": greeting})
        history = self.session.setdefault("conversation_history", [])
        history.append({"role": "user",      "content": "[call connected — patient is on the line]"})
        history.append({"role": "assistant", "content": greeting})
        self.session["last_bot_prompt"]    = greeting
        self.session["greeting_delivered"] = True

        # State stays at GREETING after the initial greeting plays.
        # Gate3 handles the GREETING → COLLECT_REASON transition by playing
        # BOOKING_OPEN on the caller's first utterance.
        # (No state advance here — keep GREETING until caller speaks.)

        await save_session(self.call_sid, self.session)

        await self.tts_text_queue.put(greeting)
        # NOTE: do NOT call silence_handler.on_question_asked(greeting) here.
        # The greeting is not a re-askable question — the SilenceHandler timer
        # is only armed after the first real question in the conversation.

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
            or self.session.get("request_transfer") is True  # set by transfer_to_human tool
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
