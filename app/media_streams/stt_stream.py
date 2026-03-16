# app/media_streams/stt_stream.py
"""
AssemblyAI Universal Streaming STT integration.

v3 (primary): wss://streaming.assemblyai.com/v3/ws
  Auth:   ?token=API_KEY as a URL query parameter (NOT Authorization header)
  Input:  PCM16 16kHz mono (upsampled from Twilio 8kHz in audio_in.py)
  Events: session_begins, PartialTranscript, FinalTranscript, Turn, error

v2 (fallback, ASSEMBLYAI_USE_V2=true): wss://api.assemblyai.com/v2/realtime/ws
  Auth:   Authorization: API_KEY header
  Input:  PCM16 8kHz mono
  Events: PartialTranscript, FinalTranscript, error

Audio gating:
  _send_audio_loop blocks until connection_ready is set.
  connection_ready is set when SessionBegins is received from AssemblyAI.
  This prevents sending audio before the session handshake completes.

Reconnect classification:
  Immediate disconnect (< 0.5s after connect) → auth/config error.
    After 3 consecutive immediate disconnects: play failure phrase and give up.
  Late disconnect (>= 0.5s) → network drop.
    Retry up to ASSEMBLYAI_MAX_RECONNECTS times with backoff.

STT failure fallback:
  On permanent failure: put STT_FAILURE_PHRASE on tts_text_queue so the
  caller hears something, then return (STT loop exits; call continues briefly
  so TTS can play the phrase before the caller hangs up).
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

# Played to the caller when STT has permanently failed.
_STT_FAILURE_PHRASE = (
    "I'm really sorry, I'm having a small technical issue right now. "
    "Please call back in a moment and I'll be ready to help you."
)

# Immediate-disconnect threshold: connections that close faster than this are
# treated as auth/config rejections rather than normal network drops.
_IMMEDIATE_THRESHOLD_SEC = 0.5

# Maximum consecutive immediate disconnects before giving up entirely.
_MAX_IMMEDIATE_STREAK = 3


def _is_garbage_transcript(text: str) -> bool:
    """Return True if transcript contains no recognisable words."""
    if not text.strip():
        return True
    words = re.findall(r"[a-zA-Z]{2,}", text.lower())
    real_words = [w for w in words if w not in NOISE_ONLY_WORDS]
    return len(real_words) == 0


def _mask_key(url: str, key: str) -> str:
    """Replace the full API key in a URL with first-8-chars + '...' for safe logging."""
    if not key:
        return url
    masked = key[:8] + "..." if len(key) > 8 else "***"
    return url.replace(key, masked)


class STTStream:
    """
    Manages one AssemblyAI WebSocket session per call.

    start() opens the WebSocket and runs send + receive loops concurrently.
    Audio is held until SessionBegins is received (connection_ready gate).
    Reconnects on network drops; gives up fast on auth/config rejections.
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
        on_partial       : async(text: str) called on non-empty PartialTranscript
        on_final_clear   : async() called on each FinalTranscript
        tts_text_queue   : If provided, failure phrase is played here on fatal error
        """
        # ── Build authenticated URL ────────────────────────────────────────────
        # v3: token must be a URL query parameter (?token=KEY).
        #     AssemblyAI v3 does NOT accept the Authorization header — it closes
        #     the WebSocket immediately (< 500ms) if the key is missing from the URL.
        # v2: uses the Authorization header (legacy, battle-tested).
        base_url = ASSEMBLYAI_WS_URL_V2 if ASSEMBLYAI_USE_V2 else ASSEMBLYAI_WS_URL
        if ASSEMBLYAI_USE_V2:
            url         = base_url
            ws_headers  = {"Authorization": ASSEMBLYAI_API_KEY}
        else:
            sep         = "&" if "?" in base_url else "?"
            url         = f"{base_url}{sep}token={ASSEMBLYAI_API_KEY}"
            ws_headers  = {}

        masked_url = _mask_key(url, ASSEMBLYAI_API_KEY)
        audio_fmt  = "pcm_s16le @ 16000 Hz" if not ASSEMBLYAI_USE_V2 else "pcm_s16le @ 8000 Hz"
        logger.info(
            "[ms_stt] STT init — url=%s audio_format=%s",
            masked_url, audio_fmt,
        )

        attempt                  = 0
        immediate_streak         = 0   # consecutive immediate disconnects
        backoff_delays           = [0.5, 1.0, 2.0]

        while not stop_event.is_set():
            attempt += 1
            # Fresh ready-event for this connection attempt.
            connection_ready = asyncio.Event()
            connect_time     = 0.0

            logger.info(
                "[ms_stt] connecting attempt=%d url=%s",
                attempt, masked_url,
            )

            try:
                async with websockets.connect(
                    url,
                    additional_headers=ws_headers,
                    ping_interval=5,
                    ping_timeout=10,
                    close_timeout=5,
                ) as ws:
                    self._ws   = ws
                    connect_time = time.monotonic()
                    logger.info("[ms_stt] connected to AssemblyAI attempt=%d", attempt)

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
                            "[ms_stt] immediate disconnect (%.3fs) — "
                            "likely auth/config rejection "
                            "(streak=%d/%d)",
                            duration, immediate_streak, _MAX_IMMEDIATE_STREAK,
                        )
                        if immediate_streak >= _MAX_IMMEDIATE_STREAK:
                            logger.error(
                                "[ms_stt] FATAL: %d consecutive immediate disconnects — "
                                "AssemblyAI is rejecting the connection. "
                                "Check API key and URL configuration. "
                                "Masked URL: %s",
                                immediate_streak, masked_url,
                            )
                            _notify_stt_failure(tts_text_queue)
                            return
                    else:
                        # Genuine network drop — reset streak, log and retry
                        immediate_streak = 0
                        logger.warning(
                            "[ms_stt] WebSocket closed after %.1fs — will reconnect",
                            duration,
                        )

            except websockets.exceptions.ConnectionClosedError as exc:
                logger.warning("[ms_stt] ConnectionClosedError: %r", exc)
                # Count as immediate if we never got past the handshake
                if connect_time > 0 and (time.monotonic() - connect_time) < _IMMEDIATE_THRESHOLD_SEC:
                    immediate_streak += 1
                    if immediate_streak >= _MAX_IMMEDIATE_STREAK:
                        logger.error(
                            "[ms_stt] FATAL: %d consecutive immediate disconnects — "
                            "check API key and URL. Masked URL: %s",
                            immediate_streak, masked_url,
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

            # Longer back-off for auth-style failures to avoid hammering the API
            if immediate_streak > 0:
                delay = 2.0
            else:
                delay = backoff_delays[min(attempt - 1, len(backoff_delays) - 1)]

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
    ) -> None:
        """
        Wait for SessionBegins (connection_ready), then stream PCM16 to AssemblyAI.

        Blocks all audio until AssemblyAI confirms the session is open.
        If SessionBegins doesn't arrive within 5 s, logs a warning and sends anyway
        (defensive fallback — should not happen with a valid key and URL).

        Sends 10ms of silence as a keepalive when the queue is empty to prevent
        AssemblyAI from timing out the connection during natural pauses.
        """
        # Block until AssemblyAI session is ready
        try:
            await asyncio.wait_for(connection_ready.wait(), timeout=5.0)
            logger.info("[ms_stt] send: SessionBegins received — audio stream open")
        except asyncio.TimeoutError:
            logger.warning(
                "[ms_stt] send: no SessionBegins within 5s — "
                "proceeding anyway (check session_begins event handling)"
            )

        KEEPALIVE = bytes(320)  # 10ms silence at 16kHz PCM16 (2 * 16000 * 0.01 = 320 bytes)

        try:
            while not stop_event.is_set():
                try:
                    pcm_chunk = await asyncio.wait_for(
                        stt_input_queue.get(), timeout=0.1,
                    )
                except asyncio.TimeoutError:
                    # Queue empty — send keepalive to hold the connection open
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
        Route AssemblyAI events:

          session_begins / SessionBegins  -> set connection_ready, log session_id
          PartialTranscript (non-empty)   -> on_partial callback (barge-in trigger)
          FinalTranscript (non-garbage)   -> transcript_queue
          Turn (end_of_turn=True)         -> transcript_queue (v3 equivalent of Final)
          error                           -> log and exit
        """
        try:
            async for raw_msg in ws:
                if stop_event.is_set():
                    break

                try:
                    msg = json.loads(raw_msg)
                except json.JSONDecodeError:
                    logger.warning("[ms_stt] non-JSON: %r", str(raw_msg)[:80])
                    continue

                msg_type = msg.get("message_type") or msg.get("type") or ""
                text      = (msg.get("text") or "").strip()

                if msg_type in ("SessionBegins", "session_begins"):
                    logger.info(
                        "[ms_stt] SessionBegins session_id=%s — "
                        "connection ready, unblocking audio stream",
                        msg.get("session_id"),
                    )
                    if connection_ready is not None:
                        connection_ready.set()

                elif msg_type == "PartialTranscript":
                    if text and on_partial:
                        try:
                            await on_partial(text)
                        except Exception as exc:
                            logger.warning("[ms_stt] on_partial error: %r", exc)

                elif msg_type == "FinalTranscript":
                    if on_final_clear:
                        try:
                            await on_final_clear()
                        except Exception:
                            pass
                    self._last_final_at = time.monotonic()
                    if not text:
                        logger.debug("[ms_stt] empty FinalTranscript -- ignoring")
                        continue
                    if _is_garbage_transcript(text):
                        logger.info("[ms_stt] garbage transcript: %r", text)
                        continue
                    logger.info("[ms_stt] final: %r", text)
                    self._put_transcript(transcript_queue, text)

                elif msg_type == "Turn":
                    if msg.get("end_of_turn") and text:
                        if on_final_clear:
                            try:
                                await on_final_clear()
                            except Exception:
                                pass
                        self._last_final_at = time.monotonic()
                        if not _is_garbage_transcript(text):
                            logger.info("[ms_stt] turn final: %r", text)
                            self._put_transcript(transcript_queue, text)

                elif msg_type == "error":
                    logger.error("[ms_stt] AssemblyAI error: %s", msg.get("error"))
                    return

                else:
                    logger.debug("[ms_stt] unhandled type=%r", msg_type)

        except websockets.exceptions.ConnectionClosed:
            logger.info("[ms_stt] receive: connection closed")
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error("[ms_stt] _receive_results_loop error: %r", exc)

    @staticmethod
    def _put_transcript(q: asyncio.Queue, text: str) -> None:
        """Put text onto transcript_queue; discard oldest entry if full."""
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
# STT failure helper (module-level, no class access needed)
# ---------------------------------------------------------------------------

def _notify_stt_failure(tts_text_queue: Optional[asyncio.Queue]) -> None:
    """
    Put the STT failure phrase onto tts_text_queue so the caller hears something.
    Does NOT set stop_event — the TTS will play the phrase and then the caller
    will hang up naturally (or Twilio will close the connection after inactivity).
    """
    if tts_text_queue is not None:
        try:
            tts_text_queue.put_nowait(_STT_FAILURE_PHRASE)
            logger.info("[ms_stt] STT failure phrase queued for TTS playback")
        except Exception as exc:
            logger.warning("[ms_stt] could not queue failure phrase: %r", exc)
