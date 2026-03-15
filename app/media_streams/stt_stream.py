# app/media_streams/stt_stream.py
"""
AssemblyAI Universal Streaming STT integration.

v3 (primary): wss://streaming.assemblyai.com/v3/ws
  Auth:   Authorization header (NOT ?token= URL param)
  Input:  PCM16 16kHz mono (upsampled from Twilio 8kHz in audio_in.py)
  Events: SessionBegins, PartialTranscript, FinalTranscript, Turn, error

v2 (fallback, ASSEMBLYAI_USE_V2=true): wss://api.assemblyai.com/v2/realtime/ws
  Input:  PCM16 8kHz mono
  Events: PartialTranscript, FinalTranscript, error

End-of-speech: FinalTranscript IS the end-of-speech signal.
Barge-in:      non-empty PartialTranscript -> on_partial callback (async, immediate).
Garbage:       noise-only transcripts discarded before reaching LLM.
Reconnect:     up to ASSEMBLYAI_MAX_RECONNECTS attempts, 0.5s/1.0s backoff.
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


def _is_garbage_transcript(text: str) -> bool:
    """Return True if transcript contains no recognisable words."""
    if not text.strip():
        return True
    words = re.findall(r"[a-zA-Z]{2,}", text.lower())
    real_words = [w for w in words if w not in NOISE_ONLY_WORDS]
    return len(real_words) == 0


class STTStream:
    """
    Manages one AssemblyAI WebSocket session per call.

    start() opens the WebSocket and runs send + receive loops concurrently.
    Reconnects up to ASSEMBLYAI_MAX_RECONNECTS times on unexpected disconnect.
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
    ) -> None:
        """
        Open AssemblyAI WebSocket and run send + receive concurrently.

        Parameters
        ----------
        stt_input_queue  : Queue of PCM16 bytes to forward to AssemblyAI
        transcript_queue : Queue where final transcript strings are placed
        stop_event       : Set when the call ends
        on_partial       : async(text: str) called on non-empty PartialTranscript (barge-in)
        on_final_clear   : async() called on each FinalTranscript to reset _clearing flag
        """
        url = ASSEMBLYAI_WS_URL_V2 if ASSEMBLYAI_USE_V2 else ASSEMBLYAI_WS_URL
        headers = {"Authorization": ASSEMBLYAI_API_KEY}
        attempt = 0
        backoff_delays = [0.5, 1.0]

        while not stop_event.is_set():
            attempt += 1
            logger.info(
                "[ms_stt] connecting attempt=%d url=%s",
                attempt, url.split("?")[0],
            )
            try:
                async with websockets.connect(
                    url,
                    additional_headers=headers,
                    ping_interval=5,
                    ping_timeout=10,
                    close_timeout=5,
                ) as ws:
                    self._ws = ws
                    logger.info("[ms_stt] connected to AssemblyAI")

                    send_task = asyncio.create_task(
                        self._send_audio_loop(ws, stt_input_queue, stop_event),
                        name="stt_send",
                    )
                    recv_task = asyncio.create_task(
                        self._receive_results_loop(
                            ws, transcript_queue, stop_event,
                            on_partial=on_partial,
                            on_final_clear=on_final_clear,
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
                        exc = t.exception()
                        if exc:
                            logger.warning("[ms_stt] task raised: %r", exc)

                    logger.warning("[ms_stt] WebSocket closed unexpectedly")

            except websockets.exceptions.ConnectionClosedError as exc:
                logger.warning("[ms_stt] ConnectionClosedError: %r", exc)
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
                    "[ms_stt] max reconnects (%d) reached -- giving up",
                    ASSEMBLYAI_MAX_RECONNECTS,
                )
                return
            delay = backoff_delays[min(attempt - 1, len(backoff_delays) - 1)]
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
    ) -> None:
        """
        Read PCM16 chunks from stt_input_queue and send as binary WS frames.
        If queue is empty for > 100ms, send a silent keep-alive to prevent timeout.
        """
        KEEPALIVE = bytes(320)  # 10ms silence at 16kHz PCM16 (zeros)

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
    ) -> None:
        """
        Route AssemblyAI events:

          PartialTranscript (non-empty) -> on_partial callback (barge-in trigger)
          FinalTranscript (non-garbage) -> transcript_queue
          Turn (end_of_turn=True)       -> transcript_queue (v3 equivalent of Final)
          SessionBegins                 -> log session_id
          error                         -> log and exit
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
                    logger.info("[ms_stt] session_id=%s", msg.get("session_id"))

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
