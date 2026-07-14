# app/media_streams/stt_stream.py
"""
AssemblyAI Universal Streaming STT integration.

v3 (primary): wss://streaming.assemblyai.com/v3/ws
  Auth:    Authorization: <api_key> header  (server-to-server, recommended by AssemblyAI)
           NOTE: ?token= is for TEMPORARY tokens from /v3/token, NOT the raw API key.
  Input:   PCM16 16kHz mono (upsampled from Twilio 8kHz in audio_in.py)
  Events (v3 message types):
    {"type": "Begin",       "id": "...", "expires_at": ...}           → session ready
    {"type": "Turn",        "transcript": "...", "end_of_turn": bool} → speech
    {"type": "Termination", "audio_duration_seconds": ...}            → session ended

v2 (fallback, ASSEMBLYAI_USE_V2=true): wss://api.assemblyai.com/v2/realtime/ws
  Auth:    Authorization: <api_key> header
  Input:   PCM16 8kHz mono
  Events:  PartialTranscript, FinalTranscript

Audio chunk sizing (AssemblyAI v3 requires 50–1000ms per send):
  Twilio mulaw is 20ms frames → audio_in.py converts to PCM16 and buffers to 60ms.
  The keepalive was 10ms (320 bytes), violating the 50ms minimum.
  AudioChunkBuffer accumulates all audio — real chunks and keepalives — to 100ms
  (3200 bytes) before sending. The buffer lives on the STTStream instance so it
  persists across reconnects (no audio lost during the reconnect window).

Audio gating:
  _send_audio_loop blocks until connection_ready is set.
  connection_ready is set when the "Begin" message arrives from AssemblyAI.
  Prevents sending audio before the session handshake completes.

Reconnect classification:
  Immediate disconnect (< 0.5s) → auth/config rejection.
    3 consecutive immediate disconnects → log FATAL, play failure phrase, give up.
  Late disconnect (>= 0.5s) → network drop.
    Retry up to ASSEMBLYAI_MAX_RECONNECTS times.

Diagnostics:
  First 10 messages received from AssemblyAI are logged verbatim at DEBUG level.
  Close frame codes and reasons are logged on all disconnects.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any, Callable, Coroutine, Optional
from urllib.parse import quote as _url_quote

import websockets
import websockets.exceptions

from .config import (
    ASSEMBLYAI_API_KEY,
    ASSEMBLYAI_WS_URL,
    ASSEMBLYAI_WS_URL_V2,
    ASSEMBLYAI_USE_V2,
    ASSEMBLYAI_MAX_RECONNECTS,
    NOISE_ONLY_WORDS,
)
# WS-C endpoint-latency measurement (latency-eval branch). Read-once flag; when
# OFF every stamp below is skipped on one falsy check — no hot-path cost.
from .latency_timing import LATENCY_TIMING as _LAT_ON

logger = logging.getLogger(__name__)

AsyncCallback = Callable[..., Coroutine[Any, Any, None]]

# ---------------------------------------------------------------------------
# Word boost — sent once after the AssemblyAI v3 Begin message
# Improves recognition of physio terms, British idioms, and Northern English
# ---------------------------------------------------------------------------

_WORD_BOOST_TERMS: list[str] = [
    # Northern English / informal affirmatives (Bug #8)
    "aye", "yeah", "yep", "nah", "nowt",
    "owt", "summat", "reight", "sorted",
    "champion", "mint", "sound",
    "me back", "me knee", "me shoulder",
    "gi'o'er", "nesh", "gradely", "mardy",
    "ta", "cheers", "right",
    # Body parts and physio conditions
    "knee", "hip", "shoulder", "ankle",
    "spine", "elbow", "wrist", "neck",
    "back", "hamstring", "calf", "achilles",
    "rotator", "cuff", "sciatic", "sciatica",
    "plantar", "fasciitis", "tendon", "tendonitis",
    "frozen shoulder", "tennis elbow", "golfer's elbow",
    "ligament", "meniscus", "cartilage",
    # Physio / clinical terms
    "physiotherapy", "physio", "assessment",
    "physiotherapist", "musculoskeletal",
    "rehabilitation", "osteopath", "acupuncture",
    "shockwave", "psychotherapy", "laser",
    "prescribing", "Pilates",
    # Clinic / proper nouns
    "Theorem", "Alcester", "Redditch",
    "Kinwarton", "Bromsgrove", "Greig",
    "Mark", "Leanne", "Dyer",
    # Location / nearby places
    "Stratford", "Birmingham", "Evesham",
    "Warwick", "Bromsgrove", "Worcester",
    # British phrases
    "fortnight", "whilst", "fortnightly",
    "go on then", "right then", "fair enough",
    "no bother", "no worries", "half past",
    "quarter past", "quarter to",
]


async def _send_word_boost(ws: Any) -> None:
    """
    Send word boost configuration to AssemblyAI v3 after the Begin handshake.

    AssemblyAI v3 Universal Streaming accepts a JSON config message on the
    WebSocket after the Begin event to improve recognition of domain-specific
    vocabulary.  If the server does not support this message type it will
    silently ignore it or return an error, which is handled gracefully here.

    Ref: https://www.assemblyai.com/docs/speech-to-text/streaming
    """
    try:
        msg = json.dumps({"word_boost": _WORD_BOOST_TERMS})
        await ws.send(msg)
        logger.info(
            "[ms_stt] word boost sent: %d terms", len(_WORD_BOOST_TERMS)
        )
    except websockets.exceptions.ConnectionClosed:
        logger.debug("[ms_stt] word boost: connection closed before send")
    except Exception as exc:
        # v3 may not support word_boost in this message format — non-fatal
        logger.warning(
            "[ms_stt] word boost not supported in v3 streaming — skipping (%r)", exc
        )


_STT_FAILURE_PHRASE = (
    "I'm really sorry, I'm having a small technical issue right now. "
    "Please call back in a moment and I'll be ready to help you."
)

# Connections that close faster than this are treated as protocol/auth rejections.
_IMMEDIATE_THRESHOLD_SEC = 0.5
_MAX_IMMEDIATE_STREAK    = 3


# ---------------------------------------------------------------------------
# Audio chunk buffer
# ---------------------------------------------------------------------------

class AudioChunkBuffer:
    """
    Accumulates PCM16 audio bytes until a full 100ms chunk is ready to send.

    AssemblyAI v3 requires chunks of 50–1000ms.
    Incoming chunks are smaller:
      - Real audio: ~60ms bursts from audio_in.py
      - Keepalive:   10ms silence (320 bytes) — the direct cause of the error

    Both real audio and keepalives flow through this buffer so AssemblyAI
    always receives correctly-sized frames.

    Math (PCM16 @ 16kHz):
      TARGET_BYTES = 16000 Hz * 2 bytes/sample * 100ms / 1000 = 3200 bytes
      MIN_BYTES    = 16000 Hz * 2 bytes/sample *  50ms / 1000 = 1600 bytes
    """
    SAMPLE_RATE        = 16000
    BYTES_PER_SAMPLE   = 2            # pcm_s16le = 16-bit = 2 bytes per sample
    TARGET_DURATION_MS = 100
    TARGET_BYTES       = (SAMPLE_RATE * BYTES_PER_SAMPLE * TARGET_DURATION_MS) // 1000
    # = 16000 * 2 * 100 / 1000 = 3200 bytes
    MIN_BYTES          = (SAMPLE_RATE * BYTES_PER_SAMPLE * 50) // 1000
    # = 16000 * 2 *  50 / 1000 = 1600 bytes

    def __init__(self) -> None:
        self.buffer = bytearray()

    def add(self, chunk: bytes) -> Optional[bytes]:
        """
        Append chunk to the internal buffer.
        Returns a 100ms chunk if enough data has accumulated; None otherwise.
        Excess bytes are kept for the next call.
        """
        self.buffer.extend(chunk)
        if len(self.buffer) >= self.TARGET_BYTES:
            output       = bytes(self.buffer[:self.TARGET_BYTES])
            self.buffer  = bytearray(self.buffer[self.TARGET_BYTES:])
            return output
        return None

    def flush(self) -> Optional[bytes]:
        """
        Return whatever remains in the buffer if it meets the 50ms minimum.
        Discards the buffer in either case (too-small remainder would be
        rejected by AssemblyAI with an Input Duration Violation error).
        """
        if len(self.buffer) >= self.MIN_BYTES:
            output      = bytes(self.buffer)
            self.buffer = bytearray()
            return output
        # Too small to send — discard to avoid the duration violation
        if self.buffer:
            logger.debug(
                "[ms_stt] flush: discarding %d bytes (< 50ms minimum)",
                len(self.buffer),
            )
        self.buffer = bytearray()
        return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_garbage_transcript(text: str) -> bool:
    """Return True if transcript contains no recognisable words.

    Phone numbers, postcodes and dates are digit-heavy — allow them through
    rather than silently discarding.  3+ consecutive digits is a reliable
    signal that the caller said something meaningful (not just noise).
    """
    if not text.strip():
        return True
    # Allow digit-heavy input (phone fragments, postcodes, dates, etc.)
    if re.search(r'\d{3,}', text):
        return False
    words = re.findall(r"[a-zA-Z]{2,}", text.lower())
    real_words = [w for w in words if w not in NOISE_ONLY_WORDS]
    return len(real_words) == 0


def _mask_key(url_or_str: str, key: str) -> str:
    """Mask API key to first-8-chars + '...' for safe logging."""
    if not key:
        return url_or_str
    masked = key[:8] + "..." if len(key) > 8 else "***"
    return url_or_str.replace(key, masked)


def _close_info(exc: websockets.exceptions.ConnectionClosed) -> str:
    """Extract close code + reason from a ConnectionClosed exception."""
    if exc.rcvd:
        return f"code={exc.rcvd.code} reason={exc.rcvd.reason!r}"
    return "no close frame"


# ---------------------------------------------------------------------------
# STTStream
# ---------------------------------------------------------------------------

class STTStream:
    """
    Manages one AssemblyAI WebSocket session per call.

    start() opens the WebSocket and runs send + receive loops concurrently.
    Audio is held until the "Begin" message is received (connection_ready gate).
    The AudioChunkBuffer lives on the instance so it survives reconnects.
    """

    def __init__(self) -> None:
        self._ws:            Optional[Any]      = None
        self._last_final_at: float              = 0.0
        # Instance-level buffer: persists across reconnects so no audio is
        # lost during the reconnect window.
        self._chunk_buffer:  AudioChunkBuffer   = AudioChunkBuffer()
        # WS-C endpoint-latency measurement (only touched when _LAT_ON).
        # _t_last_partial: monotonic time of the most recent non-empty partial;
        # _last_endpoint_wait_ms: t_end_of_turn - t_last_partial for the last
        # final, read by connection at turn dispatch. -1 = not yet measured.
        self._t_last_partial:        float      = 0.0
        self._last_endpoint_wait_ms: int        = -1

    async def start(
        self,
        stt_input_queue: asyncio.Queue,
        transcript_queue: asyncio.Queue,
        stop_event: asyncio.Event,
        on_partial: Optional[AsyncCallback] = None,
        on_final_clear: Optional[AsyncCallback] = None,
        tts_text_queue: Optional[asyncio.Queue] = None,
    ) -> None:
        """
        Open AssemblyAI WebSocket and run send + receive concurrently.

        Parameters
        ----------
        stt_input_queue  : Queue of PCM16 bytes to forward to AssemblyAI
        transcript_queue : Queue where final transcript strings are placed
        stop_event       : Set when the call ends
        on_partial       : async(text: str) called on partial Turn (barge-in)
        on_final_clear   : async(text: str) called on each end-of-turn to reset _clearing
        tts_text_queue   : If set, failure phrase is played here on fatal STT error
        """
        # ── Auth: raw API key in Authorization header (server-to-server) ──────
        # ?token= in the URL is for *temporary* browser tokens — NOT the raw key.
        url        = ASSEMBLYAI_WS_URL_V2 if ASSEMBLYAI_USE_V2 else ASSEMBLYAI_WS_URL
        # min_turn_silence is set to 200ms directly in ASSEMBLYAI_WS_URL (config.py).
        # keyterms_prompt: JSON-encoded array boosted at the STT session level.
        # Improves recognition of proper nouns not in the standard vocabulary.
        # v3 only — v2 does not support this parameter.
        if not ASSEMBLYAI_USE_V2:
            url += "&keyterms_prompt=" + _url_quote(
                json.dumps(["Alcester", "Redditch"])
            )
        ws_headers = {"Authorization": ASSEMBLYAI_API_KEY}

        masked_url = _mask_key(url, ASSEMBLYAI_API_KEY)
        audio_fmt  = "pcm_s16le@16kHz" if not ASSEMBLYAI_USE_V2 else "pcm_s16le@8kHz"
        logger.info(
            "[ms_stt] init — url=%s audio=%s chunk_target=%dms(%dB)",
            masked_url, audio_fmt,
            AudioChunkBuffer.TARGET_DURATION_MS, AudioChunkBuffer.TARGET_BYTES,
        )

        attempt          = 0
        immediate_streak = 0
        backoff_delays   = [0.5, 1.0, 2.0]

        while not stop_event.is_set():
            attempt          += 1
            connection_ready  = asyncio.Event()   # fresh per attempt
            connect_time      = 0.0

            logger.info("[ms_stt] connecting attempt=%d", attempt)

            try:
                async with websockets.connect(
                    url,
                    additional_headers=ws_headers,
                    ping_interval=5,
                    ping_timeout=10,
                    close_timeout=5,
                ) as ws:
                    self._ws     = ws
                    connect_time = time.monotonic()
                    logger.info("[ms_stt] TCP+TLS connected attempt=%d", attempt)

                    send_task = asyncio.create_task(
                        self._send_audio_loop(
                            ws, stt_input_queue, stop_event,
                            connection_ready, self._chunk_buffer,
                        ),
                        name="stt_send",
                    )
                    recv_task = asyncio.create_task(
                        self._receive_results_loop(
                            ws, transcript_queue, stop_event,
                            on_partial=on_partial,
                            on_final_clear=on_final_clear,
                            connection_ready=connection_ready,
                        ),
                        name="stt_recv",
                    )

                    done, pending = await asyncio.wait(
                        {send_task, recv_task},
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    for t in pending:
                        t.cancel()
                    await asyncio.gather(*pending, return_exceptions=True)

                    if stop_event.is_set():
                        return

                    for t in done:
                        try:
                            exc = t.exception()
                            if exc:
                                logger.warning("[ms_stt] task raised: %r", exc)
                        except (asyncio.CancelledError, asyncio.InvalidStateError):
                            pass

                    # ── Classify disconnect ────────────────────────────────────
                    duration = time.monotonic() - connect_time
                    if duration < _IMMEDIATE_THRESHOLD_SEC:
                        immediate_streak += 1
                        logger.warning(
                            "[ms_stt] immediate disconnect %.3fs — "
                            "likely protocol/config rejection "
                            "(streak=%d/%d) url=%s",
                            duration, immediate_streak, _MAX_IMMEDIATE_STREAK,
                            masked_url,
                        )
                        if immediate_streak >= _MAX_IMMEDIATE_STREAK:
                            logger.error(
                                "[ms_stt] FATAL: %d immediate disconnects — "
                                "AssemblyAI rejecting connection. "
                                "Check API key and URL params. URL: %s",
                                immediate_streak, masked_url,
                            )
                            _notify_stt_failure(tts_text_queue)
                            return
                    else:
                        immediate_streak = 0
                        logger.warning(
                            "[ms_stt] connection closed after %.1fs — will reconnect",
                            duration,
                        )

            except websockets.exceptions.ConnectionClosedError as exc:
                logger.warning("[ms_stt] ConnectionClosedError %s", _close_info(exc))
                if connect_time > 0 and (time.monotonic() - connect_time) < _IMMEDIATE_THRESHOLD_SEC:
                    immediate_streak += 1
                    if immediate_streak >= _MAX_IMMEDIATE_STREAK:
                        logger.error(
                            "[ms_stt] FATAL: %d consecutive immediate disconnects",
                            immediate_streak,
                        )
                        _notify_stt_failure(tts_text_queue)
                        return
            except websockets.exceptions.WebSocketException as exc:
                logger.error("[ms_stt] WebSocketException: %r", exc)
            except OSError as exc:
                logger.error("[ms_stt] OS/network error: %r", exc)
            except Exception as exc:
                logger.error("[ms_stt] unexpected error: %r", exc)
            finally:
                self._ws = None

            if stop_event.is_set():
                return
            if attempt > ASSEMBLYAI_MAX_RECONNECTS:
                logger.error(
                    "[ms_stt] max reconnects (%d) reached — giving up",
                    ASSEMBLYAI_MAX_RECONNECTS,
                )
                _notify_stt_failure(tts_text_queue)
                return

            delay = 2.0 if immediate_streak > 0 else (
                backoff_delays[min(attempt - 1, len(backoff_delays) - 1)]
            )
            logger.info(
                "[ms_stt] reconnecting in %.1fs (attempt %d)...",
                delay, attempt + 1,
            )
            await asyncio.sleep(delay)

    # -------------------------------------------------------------------------
    # Send loop
    # -------------------------------------------------------------------------

    async def _send_audio_loop(
        self,
        ws: Any,
        stt_input_queue: asyncio.Queue,
        stop_event: asyncio.Event,
        connection_ready: asyncio.Event,
        chunk_buffer: AudioChunkBuffer,
    ) -> None:
        """
        Wait for the "Begin" message, then stream buffered PCM16 to AssemblyAI.

        All audio — both real speech and keepalive silence — passes through
        chunk_buffer before being sent. This ensures every send is exactly
        100ms (3200 bytes), which is safely within AssemblyAI's 50–1000ms window.

        chunk_buffer is the instance-level AudioChunkBuffer from STTStream so it
        persists across reconnects — buffered audio is not lost on reconnect.
        """
        # Block until AssemblyAI confirms the session is ready
        try:
            await asyncio.wait_for(connection_ready.wait(), timeout=5.0)
            logger.info("[ms_stt] send: Begin received — audio stream open")
        except asyncio.TimeoutError:
            logger.warning(
                "[ms_stt] send: no Begin within 5s — "
                "sending audio anyway (check Begin message handling)"
            )

        # Flush any audio that accumulated in the buffer DURING the handshake.
        # On reconnects this prevents stale pre-Begin audio from corrupting the
        # new session (which would make the first real word appear cut-off because
        # AssemblyAI was confused by out-of-context audio at the session start).
        # flush() sends if >= 50ms, silently discards if too short.
        pre_begin = chunk_buffer.flush()
        if pre_begin:
            logger.info(
                "[ms_stt] post-reconnect flush: %d bytes (%.1fms) → new session",
                len(pre_begin),
                len(pre_begin) / (AudioChunkBuffer.SAMPLE_RATE
                                  * AudioChunkBuffer.BYTES_PER_SAMPLE) * 1000,
            )
            try:
                await ws.send(pre_begin)
            except Exception:
                return
        else:
            if chunk_buffer.buffer:
                logger.debug(
                    "[ms_stt] post-reconnect: discarded %d bytes (< 50ms minimum)",
                    len(chunk_buffer.buffer),
                )
                chunk_buffer.buffer = bytearray()

        # 10ms silence at 16kHz PCM16 = 320 bytes.
        # Goes through chunk_buffer so it never reaches AssemblyAI at 10ms size.
        KEEPALIVE_BYTES = bytes(320)

        first_send_done = False

        try:
            while not stop_event.is_set():
                try:
                    pcm_chunk = await asyncio.wait_for(
                        stt_input_queue.get(), timeout=0.1,
                    )
                except asyncio.TimeoutError:
                    # No audio — feed silence keepalive through the buffer
                    buffered = chunk_buffer.add(KEEPALIVE_BYTES)
                    if buffered is not None:
                        try:
                            await ws.send(buffered)
                        except Exception:
                            return
                    continue

                if not pcm_chunk:
                    continue

                # Route real audio through buffer
                buffered = chunk_buffer.add(pcm_chunk)
                if buffered is not None:
                    if not first_send_done:
                        first_send_done = True
                        logger.info(
                            "[ms_stt] first chunk sent: %d bytes = %.1fms",
                            len(buffered),
                            len(buffered) / (AudioChunkBuffer.SAMPLE_RATE
                                             * AudioChunkBuffer.BYTES_PER_SAMPLE) * 1000,
                        )
                    try:
                        await ws.send(buffered)
                    except websockets.exceptions.ConnectionClosed:
                        logger.warning("[ms_stt] send: connection closed")
                        return
                    except Exception as exc:
                        logger.error("[ms_stt] send error: %r", exc)
                        return

        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error("[ms_stt] _send_audio_loop error: %r", exc)
        finally:
            # Flush any remaining buffered audio before closing (call ended).
            # Only attempt if stop_event is set (clean call end) so we don't
            # try to send on an already-dead reconnect socket.
            if stop_event.is_set():
                remainder = chunk_buffer.flush()
                if remainder:
                    try:
                        await ws.send(remainder)
                        logger.debug(
                            "[ms_stt] flushed %d bytes on call end", len(remainder),
                        )
                    except Exception:
                        pass  # WebSocket may already be closing — best effort only

    # -------------------------------------------------------------------------
    # Receive loop
    # -------------------------------------------------------------------------

    async def _receive_results_loop(
        self,
        ws: Any,
        transcript_queue: asyncio.Queue,
        stop_event: asyncio.Event,
        on_partial: Optional[AsyncCallback] = None,
        on_final_clear: Optional[AsyncCallback] = None,
        connection_ready: Optional[asyncio.Event] = None,
    ) -> None:
        """
        Route AssemblyAI v3 events.

        v3 message types (field: "type"):
          Begin       → set connection_ready, log session details
          Turn        → end_of_turn=false: partial (barge-in trigger)
                        end_of_turn=true:  final   (enqueue transcript)
                        NOTE: text is in "transcript" field (NOT "text")
          Termination → session ended normally
          error       → log and exit

        v2 message types (field: "message_type") handled for fallback:
          PartialTranscript, FinalTranscript
        """
        msg_count = 0  # log first N messages verbatim for diagnostics

        try:
            async for raw_msg in ws:
                if stop_event.is_set():
                    break

                try:
                    msg = json.loads(raw_msg)
                except json.JSONDecodeError:
                    logger.warning("[ms_stt] non-JSON: %r", str(raw_msg)[:80])
                    continue

                # Log first 10 messages in full for connection diagnostics
                msg_count += 1
                if msg_count <= 10:
                    logger.debug("[ms_stt] msg#%d raw=%r", msg_count, msg)

                # v3 uses "type"; v2 uses "message_type"
                msg_type = msg.get("type") or msg.get("message_type") or ""

                # v3 text is in "transcript"; v2 uses "text"
                text = (
                    msg.get("transcript") or msg.get("text") or ""
                ).strip()

                # Per-message diagnostic log (debug level — high frequency)
                if msg_type not in ("Begin", "SessionBegins", "session_begins", "Termination"):
                    logger.debug("[ms_stt] msg type=%s text=%r", msg_type, text[:60] if text else "")

                # ── v3: Begin (session ready) ──────────────────────────────────
                if msg_type == "Begin":
                    logger.info(
                        "[ms_stt] Begin received — session_id=%s expires_at=%s — "
                        "unblocking audio stream",
                        msg.get("id"), msg.get("expires_at"),
                    )
                    if connection_ready is not None:
                        connection_ready.set()
                    # NOTE: _send_word_boost(ws) was called here but AssemblyAI
                    # v3 rejects the post-Begin JSON message with close code 3006
                    # (invalid message type), killing the STT connection.  Word
                    # boost for v3 must be configured via URL query parameters
                    # at connection time — not as a WebSocket message.
                    # See _WORD_BOOST_TERMS and ASSEMBLYAI_WS_URL in config.py.

                # ── v2 compat: SessionBegins ───────────────────────────────────
                elif msg_type in ("SessionBegins", "session_begins"):
                    logger.info(
                        "[ms_stt] SessionBegins session_id=%s — unblocking audio stream",
                        msg.get("session_id"),
                    )
                    if connection_ready is not None:
                        connection_ready.set()

                # ── v3: Turn (partial or final) ────────────────────────────────
                elif msg_type == "Turn":
                    end_of_turn = msg.get("end_of_turn", False)

                    if not end_of_turn:
                        # Partial — trigger barge-in if caller is speaking
                        if text and on_partial:
                            try:
                                await on_partial(text)
                            except Exception as exc:
                                logger.warning("[ms_stt] on_partial error: %r", exc)
                        # WS-C: mark the last time the caller was still speaking,
                        # so the endpoint silence (this → final) can be measured.
                        if _LAT_ON and text:
                            self._t_last_partial = time.monotonic()
                    else:
                        # Final — enqueue for LLM
                        if on_final_clear:
                            try:
                                await on_final_clear(text)
                            except Exception:
                                pass
                        self._last_final_at = time.monotonic()
                        # WS-C: endpoint_wait = silence the endpointer imposed
                        # after the caller's last word. Reset for the next turn.
                        if _LAT_ON and self._t_last_partial:
                            self._last_endpoint_wait_ms = int(
                                (self._last_final_at - self._t_last_partial) * 1000
                            )
                            self._t_last_partial = 0.0
                        if not text:
                            logger.debug("[ms_stt] empty Turn final — ignoring")
                            continue
                        if _is_garbage_transcript(text):
                            logger.info("[ms_stt] garbage transcript: %r", text)
                            continue
                        logger.info("[ms_stt] FINAL → queue: %r", text)
                        self._put_transcript(transcript_queue, text)

                # ── v2 compat: PartialTranscript ───────────────────────────────
                elif msg_type == "PartialTranscript":
                    if text and on_partial:
                        try:
                            await on_partial(text)
                        except Exception as exc:
                            logger.warning("[ms_stt] on_partial error: %r", exc)

                # ── v2 compat: FinalTranscript ─────────────────────────────────
                elif msg_type == "FinalTranscript":
                    if on_final_clear:
                        try:
                            await on_final_clear(text)
                        except Exception:
                            pass
                    self._last_final_at = time.monotonic()
                    if not text:
                        logger.debug("[ms_stt] empty FinalTranscript — ignoring")
                        continue
                    if _is_garbage_transcript(text):
                        logger.info("[ms_stt] garbage transcript: %r", text)
                        continue
                    logger.info("[ms_stt] FINAL → queue: %r", text)
                    self._put_transcript(transcript_queue, text)

                # ── v3: Termination (normal session end) ───────────────────────
                elif msg_type == "Termination":
                    logger.info(
                        "[ms_stt] Termination audio_duration=%.1fs session_duration=%.1fs",
                        msg.get("audio_duration_seconds", 0),
                        msg.get("session_duration_seconds", 0),
                    )
                    return

                # ── Error (any version) ────────────────────────────────────────
                elif msg_type == "error":
                    logger.error("[ms_stt] AssemblyAI error: %s", msg.get("error"))
                    return

                else:
                    logger.debug("[ms_stt] unhandled type=%r msg=%r", msg_type, msg)

        except websockets.exceptions.ConnectionClosedError as exc:
            logger.info("[ms_stt] receive: connection closed %s", _close_info(exc))
        except websockets.exceptions.ConnectionClosedOK as exc:
            logger.info("[ms_stt] receive: connection closed OK %s", _close_info(exc))
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error("[ms_stt] _receive_results_loop error: %r", exc)

    @staticmethod
    def _put_transcript(q: asyncio.Queue, text: str) -> None:
        """Put (enqueue_timestamp, text) onto transcript_queue; discard oldest if full."""
        item = (time.monotonic(), text)
        try:
            q.put_nowait(item)
        except asyncio.QueueFull:
            try:
                q.get_nowait()
            except asyncio.QueueEmpty:
                pass
            q.put_nowait(item)
            logger.warning("[ms_stt] transcript_queue full -- discarded oldest")


# ---------------------------------------------------------------------------
# STT failure helper
# ---------------------------------------------------------------------------

def _notify_stt_failure(tts_text_queue: Optional[asyncio.Queue]) -> None:
    """
    Put the failure phrase on tts_text_queue so the caller hears something.
    Does not set stop_event — TTS plays the phrase and the caller hangs up naturally.
    """
    if tts_text_queue is not None:
        try:
            tts_text_queue.put_nowait(_STT_FAILURE_PHRASE)
            logger.info("[ms_stt] STT failure phrase queued")
        except Exception as exc:
            logger.warning("[ms_stt] could not queue failure phrase: %r", exc)
