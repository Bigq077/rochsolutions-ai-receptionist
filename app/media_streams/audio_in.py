# app/media_streams/audio_in.py
"""
Inbound audio processing: Twilio mulaw 8kHz -> PCM16 16kHz for AssemblyAI.

Conversion pipeline per chunk:
  1. audioop.ulaw2lin(mulaw_bytes, 2)                  -> PCM16 8kHz
  2. audioop.ratecv(pcm8k, 2, 1, 8000, 16000, state)  -> PCM16 16kHz (stateful)

ratecv state is preserved across chunks (audioop requirement).
AssemblyAI v2 fallback accepts 8kHz PCM16 directly (no upsampling).
Buffer strategy: accumulate PCM_FLUSH_FRAMES (3) before forwarding = 60ms bursts.
"""
from __future__ import annotations

import asyncio
import audioop
import logging
from typing import Optional, Tuple

from .config import (
    ASSEMBLYAI_USE_V2,
    PCM_FLUSH_FRAMES,
    PCM_FRAME_BYTES_V3,
    PCM_FRAME_BYTES_V2,
)

logger = logging.getLogger(__name__)


class AudioInputProcessor:
    """
    Converts Twilio G.711 mu-law 8kHz to PCM16 for AssemblyAI.
    Maintains ratecv state across chunks. Buffers PCM_FLUSH_FRAMES before forwarding.
    """

    def __init__(self) -> None:
        self._ratecv_state: Optional[Tuple] = None
        self._pcm_buffer = b""
        self._flush_bytes = (
            PCM_FLUSH_FRAMES * PCM_FRAME_BYTES_V2
            if ASSEMBLYAI_USE_V2
            else PCM_FLUSH_FRAMES * PCM_FRAME_BYTES_V3
        )

    def convert_chunk(self, mulaw_bytes: bytes) -> bytes:
        """
        Convert a raw mu-law chunk to PCM16 at the target sample rate.
        v3 path: PCM16 16kHz (ulaw2lin + ratecv 8kHz->16kHz).
        v2 path: PCM16 8kHz  (ulaw2lin only).
        """
        if not mulaw_bytes:
            return b""
        try:
            pcm8k = audioop.ulaw2lin(mulaw_bytes, 2)
        except audioop.error as exc:
            logger.warning("[ms_audio_in] ulaw2lin error: %r", exc)
            return b""
        if ASSEMBLYAI_USE_V2:
            return pcm8k
        try:
            pcm16k, self._ratecv_state = audioop.ratecv(
                pcm8k, 2, 1, 8000, 16000, self._ratecv_state,
            )
        except audioop.error as exc:
            logger.warning("[ms_audio_in] ratecv error: %r", exc)
            return b""
        return pcm16k

    async def process_stream(
        self,
        audio_in_queue: asyncio.Queue,
        stt_input_queue: asyncio.Queue,
        stop_event: asyncio.Event,
    ) -> None:
        """
        Read mu-law chunks from audio_in_queue, convert, buffer, and flush
        to stt_input_queue in PCM_FLUSH_FRAMES-sized bursts.
        """
        try:
            while not stop_event.is_set():
                try:
                    mulaw_chunk = await asyncio.wait_for(
                        audio_in_queue.get(), timeout=0.1,
                    )
                except asyncio.TimeoutError:
                    if self._pcm_buffer:
                        await self._flush(stt_input_queue)
                    continue
                if not mulaw_chunk:
                    continue
                pcm = self.convert_chunk(mulaw_chunk)
                if not pcm:
                    continue
                self._pcm_buffer += pcm
                if len(self._pcm_buffer) >= self._flush_bytes:
                    await self._flush(stt_input_queue)
        except asyncio.CancelledError:
            if self._pcm_buffer:
                await self._flush(stt_input_queue)
            raise
        except Exception as exc:
            logger.error("[ms_audio_in] process_stream error: %r", exc)
            raise

    async def _flush(self, q: asyncio.Queue) -> None:
        payload, self._pcm_buffer = self._pcm_buffer, b""
        try:
            q.put_nowait(payload)
        except asyncio.QueueFull:
            logger.warning("[ms_audio_in] stt_input_queue full -- dropping %d bytes", len(payload))

    def reset(self) -> None:
        """Reset resampler state. Call on barge-in to prevent stale state."""
        self._ratecv_state = None
        self._pcm_buffer   = b""


def detect_silence(chunk: bytes, threshold: int = 500) -> bool:
    """
    Return True if PCM16 chunk RMS amplitude is below threshold.
    threshold=500: clear speech ~2000+, background noise < 200.
    """
    if not chunk or len(chunk) < 2:
        return True
    try:
        return audioop.rms(chunk, 2) < threshold
    except audioop.error:
        return True
