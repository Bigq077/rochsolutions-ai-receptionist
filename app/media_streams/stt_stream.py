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
  Every message received from AssemblyAI is logged at DEBUG level for the
  first 10 messages after connect, so close-before-Begin can be diagnosed.
  Close frame codes and reasons are logged on all disconnects.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any, Callable, Coroutine, Optional

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

logger = logging.getLogger(__name__)

AsyncCallback = Callable[..., Coroutine[Any, Any, None]]

_STT_FAILURE_PHRASE = (
    "I'm really sorry, I'm having a small technical issue right now. "
    "Please call back in a moment and I'll be ready to help you."
)

# Connections that close faster than this are treated as protocol/auth rejections.
_IMMEDIATE_THRESHOLD_SEC = 0.5
_MAX_IMMEDIATE_STREAK    = 3


def _is_garbage_transcript(text: str) -> bool:
    """Return True if transcript contains no recognisable words."""
    if not text.strip():
        return True
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


class STTStream:
    """
    Manages one AssemblyAI WebSocket session per call.

    start() opens the WebSocket and runs send + receive loops concurrently.
    Audio is held until the "Begin" message is received (connection_ready gate).
    """

    def __init__(self) -> None:
        self._ws: Optional[Any] = None
        self._last_final_at: float = 0.0

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
        on_final_clear   : async() called on each end-of-turn to reset _clearing
        tts_text_queue   : If set, failure phrase is played here on fatal STT error
        """
        # ── Auth: raw API key in Authorization header (server-to-server) ──────
        # ?token= in the URL is for *temporary* browser tokens obtained from the
        # /v3/token endpoint — NOT the raw API key. The Authorization header is
        # the correct method for server-to-server use.
        url        = ASSEMBLYAI_WS_URL_V2 if ASSEMBLYAI_USE_V2 else ASSEMBLYAI_WS_URL
        ws_headers = {"Authorization": ASSEMBLYAI_API_KEY}

        masked_url = _mask_key(url, ASSEMBLYAI_API_KEY)
        audio_fmt  = "pcm_s16le@16kHz" if not ASSEMBLYAI_USE_V2 else "pcm_s16le@8kHz"
        logger.info(
            "[ms_stt] init — url=%s audio=%s",
            masked_url, audio_fmt,
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
                            ws, stt_input_queue, stop_event, connection_ready,
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
                                "AssemblyAI rejecting the connection. "
                                "Check API key (Authorization header) and URL params. "
                                "URL: %s",
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
                logger.warning(
                    "[ms_stt] ConnectionClosedError %s",
                    _close_info(exc),
                )
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
            logger.info("[ms_stt] reconnecting in %.1fs (attempt %d)...", delay, attempt + 1)
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
    ) -> None:
        """
        Wait for the "Begin" message (connection_ready), then stream PCM16 bytes.

        Blocks all audio until AssemblyAI confirms the session is open.
        Times out after 5s with a warning (defensive — should not happen).
        Sends 10ms silence keepalives when the queue is empty to hold the connection.
        """
        try:
            await asyncio.wait_for(connection_ready.wait(), timeout=5.0)
            logger.info("[ms_stt] send: Begin received — audio stream open")
        except asyncio.TimeoutError:
            logger.warning(
                "[ms_stt] send: no Begin within 5s — "
                "sending audio anyway (check Begin message handling)"
            )

        # 10ms silence at 16kHz PCM16: 16000 Hz * 2 bytes/sample * 0.01s = 320 bytes
        KEEPALIVE = bytes(320)

        try:
            while not stop_event.is_set():
                try:
                    pcm_chunk = await asyncio.wait_for(
                        stt_input_queue.get(), timeout=0.1,
                    )
                except asyncio.TimeoutError:
                    try:
                        await ws.send(KEEPALIVE)
                    except Exception:
                        return
                    continue

                if not pcm_chunk:
                    continue

                try:
                    await ws.send(pcm_chunk)
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
          Begin       → set connection_ready, log full message for diagnostics
          Turn        → end_of_turn=false: partial (barge-in)
                        end_of_turn=true:  final   (enqueue transcript)
                        text is in "transcript" field (NOT "text")
          Termination → session ended normally
          error       → log and exit

        v2 message types (field: "message_type") handled for fallback:
          PartialTranscript, FinalTranscript
        """
        msg_count = 0   # diagnostic counter: log first N messages verbatim

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

                # v3 transcript text is in "transcript"; v2 uses "text"
                text = (
                    msg.get("transcript") or msg.get("text") or ""
                ).strip()

                # ── v3: Begin (session ready) ──────────────────────────────────
                if msg_type == "Begin":
                    logger.info(
                        "[ms_stt] Begin received — session_id=%s expires_at=%s — "
                        "unblocking audio stream",
                        msg.get("id"), msg.get("expires_at"),
                    )
                    if connection_ready is not None:
                        connection_ready.set()

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
                        # Partial — trigger barge-in if caller started speaking
                        if text and on_partial:
                            try:
                                await on_partial(text)
                            except Exception as exc:
                                logger.warning("[ms_stt] on_partial error: %r", exc)
                    else:
                        # Final — enqueue for LLM
                        if on_final_clear:
                            try:
                                await on_final_clear()
                            except Exception:
                                pass
                        self._last_final_at = time.monotonic()
                        if not text:
                            logger.debug("[ms_stt] empty Turn final — ignoring")
                            continue
                        if _is_garbage_transcript(text):
                            logger.info("[ms_stt] garbage transcript: %r", text)
                            continue
                        logger.info("[ms_stt] final: %r", text)
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
                            await on_final_clear()
                        except Exception:
                            pass
                    self._last_final_at = time.monotonic()
                    if not text:
                        logger.debug("[ms_stt] empty FinalTranscript — ignoring")
                        continue
                    if _is_garbage_transcript(text):
                        logger.info("[ms_stt] garbage transcript: %r", text)
                        continue
                    logger.info("[ms_stt] final: %r", text)
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
        """Put text onto transcript_queue; discard oldest if full."""
        try:
            q.put_nowait(text)
        except asyncio.QueueFull:
            try:
                q.get_nowait()
            except asyncio.QueueEmpty:
                pass
            q.put_nowait(text)
            logger.warning("[ms_stt] transcript_queue full -- discarded oldest")


# ---------------------------------------------------------------------------
# STT failure helper
# ---------------------------------------------------------------------------

def _notify_stt_failure(tts_text_queue: Optional[asyncio.Queue]) -> None:
    """
    Put the failure phrase on tts_text_queue so the caller hears something.
    Does not set stop_event — TTS plays the phrase and the caller hangs up.
    """
    if tts_text_queue is not None:
        try:
            tts_text_queue.put_nowait(_STT_FAILURE_PHRASE)
            logger.info("[ms_stt] STT failure phrase queued")
        except Exception as exc:
            logger.warning("[ms_stt] could not queue failure phrase: %r", exc)
