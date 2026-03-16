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
  - Dead air rule: no more than 3 seconds of silence while Susie should be speaking
  - Watchdog timer fires rotating bridge phrases if LLM takes > 3s with no audio
  - Silence re-ask: if caller silent 5s after a question, re-ask (max 2 times)
  - After 2 failed re-asks: offer transfer
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
import re
import time
import traceback
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from fastapi import WebSocket, WebSocketDisconnect

from .config import (
    SAFE_FALLBACK_PHRASE,
    TWILIO_STARTED_TIMEOUT_SEC,
    WATCHDOG_SILENCE_SEC,
    WATCHDOG_PHRASES,
    QUESTION_SILENCE_SEC,
    MAX_REASK_ATTEMPTS,
    REASK_PREFIX,
    TRANSFER_OFFER_PHRASE,
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


# Prefixes that Susie sometimes opens her question with — strip them before
# prepending REASK_PREFIX so the re-ask never reads "Sorry about that — Sorry, ..."
_APOLOGY_PREFIXES = (
    "Sorry about that — ",
    "Sorry about that — ",   # em-dash variant (U+2014)
    "Sorry about that - ",
    "I'm sorry — ",
    "I'm sorry, ",
    "Sorry — ",
    "Sorry, ",
    "Apologies — ",
    "Apologies, ",
)

def _strip_apology_prefix(text: str) -> str:
    """
    Strip any leading apology phrase from a question so re-asks don't
    double-up: "Sorry about that — Sorry, could you...".
    """
    stripped = text
    for prefix in _APOLOGY_PREFIXES:
        if stripped.lower().startswith(prefix.lower()):
            stripped = stripped[len(prefix):].lstrip()
            break   # only strip once (no nested apologies)
    return stripped or text  # never return empty


# ---------------------------------------------------------------------------
# Hardcoded greeting (Bug 4 — fast startup, no LLM round-trip)
# ---------------------------------------------------------------------------

# Deterministic Theorem Health greeting. Used as the immediate default so
# TTS starts within one asyncio tick of call connect — before the dynamic
# _build_greeting import completes on first call. Falls back to the dynamic
# clinic-specific greeting if _build_greeting returns a valid string.
_THEOREM_GREETING = (
    "Hi there, this is Susie, Theorem Health's AI receptionist. "
    "Are you calling about our Alcester clinic or our Redditch one?"
)


# ---------------------------------------------------------------------------
# Question extraction helper (Bug 1 — store only question, not full response)
# ---------------------------------------------------------------------------

# Splits on sentence boundaries: after . ! ? followed by whitespace
_SENTENCE_END_RE = re.compile(r"(?<=[.!?])\s+")

# Opener affirmations to strip from the extracted question before storing.
# These are the phrases that Claude sometimes prepends even though they're
# banned — stripping here ensures the re-ask sounds natural regardless.
_RESPONSE_OPENER_PREFIXES = (
    "Absolutely, ",
    "Certainly, ",
    "Of course, ",
    "Sure, ",
    "Great, ",
    "Sorry, ",
)


def _extract_question(text: str) -> str:
    """
    Extract the question portion from an LLM response.

    Splits the response into sentences and returns the LAST sentence that
    ends with '?'.  If no sentence ends with '?', returns '' — meaning the
    response was a statement, not a question, and the re-ask watchdog should
    NOT be set (re-asking a statement like "Okay, that's noted." makes no sense).

    Also strips any banned opener affirmation from the front of the extracted
    question before returning it, so re-asks never sound like
    "Sorry about that — Absolutely, could you...".

    Examples:
      "Right, Alcester. And have you been to us before?"
        → "And have you been to us before?"
      "Okay, that's noted."
        → ""  (statement — no re-ask)
      "Absolutely, what time works best for you?"
        → "What time works best for you?"
      "Right, just checking what we have available around that time..."
        → ""  (no '?' — don't re-ask a bridge phrase)
    """
    if not text or "?" not in text:
        return ""

    # Split into individual sentences
    sentences = _SENTENCE_END_RE.split(text.strip())

    # Walk backwards: take the LAST question sentence
    question = ""
    for sentence in reversed(sentences):
        s = sentence.strip()
        if s.endswith("?"):
            question = s
            break

    if not question:
        return ""

    # Strip any banned opener prefix (case-insensitive, strip only once)
    for prefix in _RESPONSE_OPENER_PREFIXES:
        if question.lower().startswith(prefix.lower()):
            question = question[len(prefix):].lstrip()
            # Re-capitalise after stripping
            if question:
                question = question[0].upper() + question[1:]
            break

    return question.strip()


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
        self._last_audio_at:          float = 0.0   # epoch time of last audio sent to Twilio
        self._last_filler_at:         float = 0.0   # epoch time of last filler phrase played
        self._bad_line_played         = False        # once-per-call bad-line phrase guard
        self._last_audio_received_at: float = 0.0   # monotonic time of last inbound Twilio audio

        # ── Watchdog / silence tracking ────────────────────────────────────
        self._watchdog_phrase_idx: int  = 0     # cycles through WATCHDOG_PHRASES
        self._watchdog_armed:      bool = False  # only arm after greeting completes
        self._last_transcript_at:  float = 0.0  # time of last FinalTranscript
        self._last_question_text:  str   = ""    # last thing Susie said (question)
        self._last_question_at:    float = 0.0   # when last_question was asked
        self._reask_count:         int   = 0     # how many times we've re-asked

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
            asyncio.create_task(self._receive_loop(),       name="ms_receive"),
            asyncio.create_task(self._audio_in_loop(),      name="ms_audio_in"),
            asyncio.create_task(self._stt_loop(),           name="ms_stt"),
            asyncio.create_task(self._llm_loop(),           name="ms_llm"),
            asyncio.create_task(self._tts_loop(),           name="ms_tts"),
            asyncio.create_task(self._send_loop(),          name="ms_send"),
            asyncio.create_task(self._watchdog_loop(),      name="ms_watchdog"),
            asyncio.create_task(self._silence_reask_loop(), name="ms_reask"),
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
    # LLM loop
    # ========================================================================

    async def _llm_loop(self) -> None:
        """
        Wait for the "start" event, then consume utterances from transcript_queue
        and run an LLM turn for each one.

        Error handling:
          - Claude API error: play CLAUDE_ERROR_PHRASE and allow one retry on the
            next transcript (the retry is implicit — the loop continues)
          - Repeated failures: each turn plays the error phrase; monitoring catches
            the error logs
          - On first successful complete turn: sets _call_stable = True
        """
        await self._wait_for_start("llm_loop")

        from .llm_stream import LLMStream
        llm = LLMStream()

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

                # Drop utterance if LLM is already mid-generation
                if self._llm_busy:
                    logger.info(
                        "[ms_conn] LLM busy — dropping utterance: %r",
                        utterance[:80],
                    )
                    continue

                logger.info("[ms_conn] TRANSCRIPT ← queue: %r", utterance[:120])
                self._llm_busy                         = True
                self.session["llm_generation_active"]  = True
                await save_session(self.call_sid, self.session)

                tts_had_output = False
                try:
                    logger.info(
                        "[ms_conn] LLM INPUT: %r  state=%s",
                        utterance[:120],
                        self.session.get("state", "UNKNOWN"),
                    )
                    await llm.run_turn(
                        user_text=utterance,
                        session=self.session,
                        call_sid=self.call_sid,
                        stream_sid=self.stream_sid,
                        tts_text_queue=self.tts_text_queue,
                        audio_out_queue=self.audio_out_queue,
                        websocket=self.websocket,
                        on_transfer=self._on_transfer_request,
                    )
                    tts_had_output = True
                    # Diagnostic: log the assembled LLM response
                    _resp = self.session.get("last_bot_prompt", "")
                    if _resp:
                        logger.info("[ms_conn] LLM response: %r", _resp[:120])

                except asyncio.CancelledError:
                    pass

                except Exception as exc:
                    logger.error("[ms_conn] LLM turn error: %r\n%s", exc, traceback.format_exc())
                    # Play error phrase so caller hears something, not dead air
                    await self.tts_text_queue.put(CLAUDE_ERROR_PHRASE)

                finally:
                    self._llm_busy                         = False
                    self.session["llm_generation_active"]  = False
                    await save_session(self.call_sid, self.session)

                # Mark call stable after first complete cycle
                if tts_had_output and not self._call_stable:
                    self._call_stable = True
                    logger.info("[ms_conn] call reached stable state")

                # Log call state after this turn
                logger.info("[ms_conn] state after turn: %s", self.session.get("state", "UNKNOWN"))

                # Record the QUESTION portion of Susie's last response for the
                # re-ask watchdog. If the response was a statement (no '?'),
                # nothing is stored — re-asking a statement makes no sense.
                last_prompt = self.session.get("last_bot_prompt", "")
                if last_prompt:
                    question = _extract_question(last_prompt)
                    if question:
                        logger.info("[ms_conn] last_question stored: %r", question)
                        self._record_question(question)
                    else:
                        logger.debug(
                            "[ms_conn] no question in LLM response — re-ask timer not set"
                        )

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
    # Watchdog timer loop
    # ========================================================================

    async def _watchdog_loop(self) -> None:
        """
        Dead-air watchdog: prevents silence > WATCHDOG_SILENCE_SEC while LLM is active.

        Runs every 0.5 seconds after the call starts. Fires a rotating bridge phrase
        onto tts_text_queue when ALL of:
          - LLM is actively generating (_llm_busy = True)
          - No audio sent to caller for > WATCHDOG_SILENCE_SEC seconds
          - No TTS task is currently running
          - audio_out_queue is empty

        The bridge phrase resets _last_audio_at to prevent cascading fires.
        Each fire advances to the next phrase in WATCHDOG_PHRASES.

        Example sequence on a slow Claude response:
          t=0.0s  LLM starts  (_llm_busy = True)
          t=3.0s  Watchdog fires "Just bear with me one moment..."
          t=3.5s  Phrase starts playing, _last_audio_at resets
          t=6.5s  If still no LLM output: "Let me just check that for you..."
        """
        await self._wait_for_start("watchdog_loop")

        try:
            while not self._stop_event.is_set():
                await asyncio.sleep(0.5)

                if not self._watchdog_armed:
                    continue
                if not self._llm_busy:
                    continue
                if self._clearing:
                    continue
                if self._last_audio_at <= 0:
                    continue
                if self._tts_task is not None and not self._tts_task.done():
                    continue
                if not self.audio_out_queue.empty():
                    continue

                silence_secs = time.monotonic() - self._last_audio_at
                if silence_secs < WATCHDOG_SILENCE_SEC:
                    continue

                # Fire a bridge phrase
                phrase = WATCHDOG_PHRASES[self._watchdog_phrase_idx % len(WATCHDOG_PHRASES)]
                self._watchdog_phrase_idx += 1
                logger.warning(
                    "[ms_watchdog] dead air %.1fs — playing bridge phrase: %r",
                    silence_secs, phrase,
                )
                # Reset timer so watchdog doesn't immediately re-fire
                self._last_audio_at = time.monotonic()
                await self.tts_text_queue.put(phrase)

        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error("[ms_conn] _watchdog_loop error: %r", exc)

    # ========================================================================
    # Silence / re-ask loop
    # ========================================================================

    async def _silence_reask_loop(self) -> None:
        """
        Re-ask watchdog: if caller has been silent for QUESTION_SILENCE_SEC
        seconds after Susie asked a question, re-ask (max MAX_REASK_ATTEMPTS times).

        After MAX_REASK_ATTEMPTS failed re-asks, play TRANSFER_OFFER_PHRASE
        and trigger a transfer attempt.

        Reset by _on_final_transcript_clear() whenever any FinalTranscript arrives.

        Checks every 1 second to keep latency acceptable (1s granularity is fine
        for a 5-second threshold).
        """
        await self._wait_for_start("silence_reask_loop")

        try:
            while not self._stop_event.is_set():
                await asyncio.sleep(1.0)

                # Only check if a question has been recorded
                if self._last_question_at <= 0 or not self._last_question_text:
                    continue

                # Don't interrupt while something is already playing/generating
                if self._llm_busy:
                    continue
                if self._tts_task is not None and not self._tts_task.done():
                    continue
                if not self.tts_text_queue.empty():
                    continue

                # Check if caller has been silent since the question was asked
                now = time.monotonic()
                since_question   = now - self._last_question_at
                since_transcript = now - self._last_transcript_at if self._last_transcript_at > 0 else float("inf")

                # Don't re-ask if Twilio is actively sending audio — the caller
                # is speaking right now and AssemblyAI hasn't finalised yet.
                if self._last_audio_received_at > 0:
                    since_audio = now - self._last_audio_received_at
                    if since_audio < 2.0:
                        continue

                # Must be silent for both thresholds
                if since_question < QUESTION_SILENCE_SEC:
                    continue
                if since_transcript < QUESTION_SILENCE_SEC:
                    continue

                # Decide: re-ask or transfer
                if self._reask_count >= MAX_REASK_ATTEMPTS:
                    logger.warning(
                        "[ms_reask] max re-asks reached (%d) — offering transfer",
                        MAX_REASK_ATTEMPTS,
                    )
                    # Set request_transfer=True so _should_allow_transfer() passes.
                    # (Incrementing failed_understanding_count alone never reached
                    # the >= 3 threshold — transfer was blocked and TTS said
                    # "let me transfer you" with nothing actually happening.)
                    self.session["request_transfer"] = True
                    await self.tts_text_queue.put(TRANSFER_OFFER_PHRASE)
                    # Attempt actual transfer after phrase plays
                    asyncio.create_task(self._on_transfer_request())
                    # Reset so this doesn't fire again
                    self._last_question_at = 0.0
                    self._last_question_text = ""
                    self._reask_count = 0
                    continue

                # Re-ask — strip any apology prefix from the stored question first
                # so we never get "Sorry about that — Sorry, could you..." double-up.
                reask_text = REASK_PREFIX + _strip_apology_prefix(self._last_question_text)
                self._reask_count    += 1
                self._last_question_at = time.monotonic()  # reset timer for next check
                logger.info(
                    "[ms_reask] firing re-ask #%d time_since_question=%.1fs last_question=%r",
                    self._reask_count, since_question, self._last_question_text[:80],
                )
                await self.tts_text_queue.put(reask_text)

        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error("[ms_conn] _silence_reask_loop error: %r", exc)

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
        Also resets the silence re-ask tracking.
        """
        self._clearing             = False
        self._last_transcript_at   = time.monotonic()
        # Receiving a transcript resets the re-ask counter and question timer
        self._reask_count          = 0
        self._last_question_at     = 0.0
        self._last_question_text   = ""

    # ========================================================================
    # Question tracking
    # ========================================================================

    def _record_question(self, text: str) -> None:
        """
        Record the last thing Susie said as a potential re-ask candidate.

        Called after each TTS phrase is sent and after each LLM turn.
        Resets the re-ask counter only when the text actually changes
        (prevents spamming the same question multiple times from one LLM turn).
        """
        text = text.strip()
        if not text:
            return

        if text != self._last_question_text:
            self._last_question_text = text
            self._last_question_at   = time.monotonic()
            # Only reset reask_count when a NEW question is recorded
            # (if the same question is re-asked by the watchdog, count stays)

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

        # Advance state: greeting done → now waiting for clinic selection.
        # This ensures the LLM never sees state=GREETING and re-introduces itself.
        from .session import advance_state, CallState
        advance_state(self.session, CallState.CLINIC_SELECTION)

        await save_session(self.call_sid, self.session)

        await self.tts_text_queue.put(greeting)
        # NOTE: do NOT call _record_question(greeting) here.
        # The greeting is not a re-askable question — storing it causes the
        # silence re-ask loop to replay the full greeting text verbatim after
        # 5 s of caller silence. The question tracker is set by the LLM loop
        # after the first real exchange.

        # Arm the watchdog now that the call is underway
        self._watchdog_armed = True

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
