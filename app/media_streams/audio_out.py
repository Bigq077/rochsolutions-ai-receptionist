# app/media_streams/audio_out.py
"""
Outbound audio processing: ElevenLabs PCM16 16kHz -> Twilio mulaw 8kHz.

ElevenLabs streams audio as PCM16 at 16kHz (requested via ?output_format=pcm_16000).
Twilio Media Streams requires G.711 mu-law, 8kHz, 8-bit, mono.

Conversion pipeline per chunk:
  1. Align to 4-byte boundary (2 PCM16 samples) -- audioop.tomono requires this
  2. audioop.tomono(chunk, 2, 0.5, 0.5)   -- 2:1 anti-aliased decimation 16kHz->8kHz
     (treats mono 16kHz as stereo 8kHz, averages L+R = null at 4kHz Nyquist)
  3. audioop.lin2ulaw(downsampled, 2)      -- PCM16 -> G.711 mu-law 8-bit
  4. base64-encode                         -- Twilio expects base64 payload

The tomono trick (verified in realtime.py) is a 2-tap FIR box filter that nulls
at 4kHz (Nyquist for 8kHz), preventing aliasing while keeping speech intact.

Also provides play_audio_file() for pre-recorded fallback audio.
"""
from __future__ import annotations

import asyncio
import audioop
import base64
import logging
import wave
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class AudioOutputProcessor:
    """
    Converts ElevenLabs PCM16 16kHz to base64-encoded mulaw 8kHz for Twilio.

    Maintains a 4-byte alignment remainder buffer across chunks so audioop.tomono
    never receives mis-aligned input.
    """

    def __init__(self) -> None:
        self._remainder = b""   # up to 3 bytes carried over from previous chunk

    def convert_chunk(self, pcm16k_chunk: bytes) -> Optional[str]:
        """
        Convert a PCM16 16kHz chunk to base64-encoded mulaw 8kHz.

        Returns the base64 string ready for the Twilio media event payload,
        or None if the chunk is too small to process after alignment.

        Steps:
          1. Prepend remainder from previous chunk
          2. Trim to 4-byte boundary; save overhang as new remainder
          3. audioop.tomono: 2:1 anti-aliased decimation (16kHz -> 8kHz)
          4. audioop.lin2ulaw: PCM16 -> 8-bit G.711 mu-law
          5. base64 encode
        """
        if not pcm16k_chunk:
            return None

        chunk = self._remainder + pcm16k_chunk
        leftover = len(chunk) % 4
        if leftover:
            self._remainder = chunk[-leftover:]
            chunk = chunk[:-leftover]
        else:
            self._remainder = b""

        if len(chunk) < 4:
            return None

        try:
            # Anti-aliased 2:1 decimation: treat mono 16kHz as stereo 8kHz
            # and average L+R sample pairs -> mono 8kHz
            downsampled = audioop.tomono(chunk, 2, 0.5, 0.5)
        except audioop.error as exc:
            logger.warning("[ms_audio_out] tomono error: %r", exc)
            self._remainder = b""
            return None

        try:
            ulaw_chunk = audioop.lin2ulaw(downsampled, 2)
        except audioop.error as exc:
            logger.warning("[ms_audio_out] lin2ulaw error: %r", exc)
            return None

        return base64.b64encode(ulaw_chunk).decode("ascii")

    def flush(self) -> Optional[str]:
        """
        Convert any remaining buffered bytes.
        Call after the last chunk from an ElevenLabs response to avoid
        dropping the final partial frame.
        """
        if not self._remainder:
            return None
        # Pad to 4-byte boundary with silence
        pad_needed = (4 - len(self._remainder) % 4) % 4
        padded = self._remainder + b"\x00" * pad_needed
        self._remainder = b""
        return self.convert_chunk(padded)

    def reset(self) -> None:
        """Reset alignment state. Call on barge-in between TTS chunks."""
        self._remainder = b""

    async def process_stream(
        self,
        tts_audio_queue: asyncio.Queue,
        audio_out_queue: asyncio.Queue,
        stop_event: asyncio.Event,
    ) -> None:
        """
        Read raw PCM16 chunks from tts_audio_queue, convert, and put
        base64-encoded mulaw strings onto audio_out_queue.

        A None sentinel on tts_audio_queue triggers a flush and exit.
        Used as an alternative integration path when TTSStream writes raw
        PCM to an intermediate queue (vs calling convert_chunk directly).
        """
        try:
            while not stop_event.is_set():
                try:
                    pcm_chunk = await asyncio.wait_for(
                        tts_audio_queue.get(), timeout=0.1,
                    )
                except asyncio.TimeoutError:
                    continue

                if pcm_chunk is None:
                    # Sentinel: flush remainder and stop
                    b64 = self.flush()
                    if b64:
                        await audio_out_queue.put(b64)
                    break

                if not pcm_chunk:
                    continue

                b64 = self.convert_chunk(pcm_chunk)
                if b64:
                    await audio_out_queue.put(b64)

        except asyncio.CancelledError:
            self.reset()
            raise
        except Exception as exc:
            logger.error("[ms_audio_out] process_stream error: %r", exc)
            raise


# ---------------------------------------------------------------------------
# Pre-recorded audio playback
# ---------------------------------------------------------------------------

async def play_audio_file(
    filepath: str,
    audio_out_queue: asyncio.Queue,
    chunk_bytes: int = 640,
) -> None:
    """
    Load a pre-recorded WAV or raw mulaw file and enqueue for Twilio playback.

    The file must be G.711 mu-law 8kHz, OR a standard WAV at any sample rate
    (auto-converted via audioop).

    Parameters
    ----------
    filepath        : Path to the audio file
    audio_out_queue : Queue where base64-encoded mulaw chunks are placed
    chunk_bytes     : Raw bytes per chunk (default 640 = 80ms mulaw at 8kHz)

    Used for filler phrases ("Just one moment...") or hard-coded fallback
    messages. If the file does not exist, logs a warning and returns silently.
    """
    path = Path(filepath)
    if not path.exists():
        logger.warning("[ms_audio_out] play_audio_file: not found: %s", filepath)
        return

    try:
        suffix = path.suffix.lower()

        if suffix == ".wav":
            with wave.open(str(path), "rb") as wf:
                n_channels   = wf.getnchannels()
                sample_width = wf.getsampwidth()
                frame_rate   = wf.getframerate()
                raw_pcm      = wf.readframes(wf.getnframes())

            # Convert stereo to mono
            if n_channels == 2:
                raw_pcm = audioop.tomono(raw_pcm, sample_width, 0.5, 0.5)

            # Resample to 8kHz
            if frame_rate != 8000:
                raw_pcm, _ = audioop.ratecv(raw_pcm, sample_width, 1, frame_rate, 8000, None)

            # Convert to 16-bit
            if sample_width != 2:
                raw_pcm = audioop.lin2lin(raw_pcm, sample_width, 2)

            ulaw_data = audioop.lin2ulaw(raw_pcm, 2)

        else:
            # Assume raw mulaw 8kHz (.ulaw or .raw)
            ulaw_data = path.read_bytes()

        # Enqueue in chunks
        offset = 0
        while offset < len(ulaw_data):
            chunk = ulaw_data[offset : offset + chunk_bytes]
            b64 = base64.b64encode(chunk).decode("ascii")
            await audio_out_queue.put(b64)
            offset += chunk_bytes

        logger.info(
            "[ms_audio_out] play_audio_file: enqueued %d bytes from %s",
            len(ulaw_data), filepath,
        )

    except Exception as exc:
        logger.error("[ms_audio_out] play_audio_file error: %r", exc)
