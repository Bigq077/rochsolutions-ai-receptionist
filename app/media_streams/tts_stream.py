# app/media_streams/tts_stream.py
"""
ElevenLabs streaming TTS pipeline.

Two integration modes are provided:

MODE A -- HTTP streaming (REST, per-chunk):
  synthesise_chunk(text, audio_out_queue, audio_out_processor)
  Used by the tts_loop in connection.py.
  One POST request per text chunk from the LLM chunker.
  Shares a persistent httpx.AsyncClient (connection pooling, avoids TLS overhead).

MODE B -- WebSocket streaming (long-lived connection):
  start_ws(tts_text_queue, audio_out_queue)
  Keeps one ElevenLabs WebSocket open for the entire call.
  Sends text chunks incrementally; receives audio as it arrives.
  Better for very low-latency delivery of long responses.

Both modes convert ElevenLabs PCM16 16kHz output through AudioOutputProcessor
(tomono 2:1 decimation + lin2ulaw) and put base64 mulaw strings onto
audio_out_queue for the send_loop to forward to Twilio.

CRITICAL: output_format MUST be a URL query param, NOT a body field.
  Confirmed in realtime.py: body field is silently ignored and returns MP3.

ElevenLabs API details:
  HTTP endpoint: POST /v1/text-to-speech/{voice_id}/stream?output_format=pcm_16000
  WS endpoint:   wss://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream-input
                 ?model_id=eleven_flash_v2_5&output_format=pcm_16000
  Auth:          xi-api-key header (HTTP) or xi_api_key in init message (WS)
  Model:         eleven_flash_v2_5
  Chunk size:    640 bytes = 20ms of 16kHz PCM16
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re as _re
from pathlib import Path
from typing import Any, Optional

import httpx

from .config import (
    ELEVENLABS_API_KEY,
    ELEVENLABS_VOICE_ID,
    ELEVENLABS_MODEL_ID,
    ELEVENLABS_STABILITY,
    ELEVENLABS_SIMILARITY_BOOST,
    TTS_STREAM_CHUNK_SIZE,
)
from .audio_out import AudioOutputProcessor

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# TTS phonetic substitution table
# ---------------------------------------------------------------------------
# Applied to ALL text before it reaches any TTS engine (ElevenLabs or OpenAI).
# The LLM and routing logic always use the canonical spelling; only the audio
# synthesis layer receives the phonetic form.
#
# Alcester: British English /ˈɔːlstə/ — "AWL-stuh".
#   "Awlstuh" guides both ElevenLabs and OpenAI TTS to the correct sounds:
#     - "Awl"  → /ɔːl/ (rhymes with "ball")
#     - "stuh" → /stə/ (schwa — no hard trailing r)
#   Previous OpenAI-only attempt "Awlster" added a distinct /r/ sound and
#   was ElevenLabs-blind.  This replaces it for both engines.
# ElevenLabs path: the pronunciation dictionary alias handles Alcester
# natively, so "Awlstuh" must NOT be sent — it would double-convert.
_TTS_SUBSTITUTIONS_ELEVENLABS: list[tuple] = []

# OpenAI fallback path: no pronunciation dictionary is available, so
# the phonetic spelling is the only mechanism.
_TTS_SUBSTITUTIONS_OPENAI: list[tuple] = [
    # (compiled_pattern, replacement)
    (_re.compile(r"\bAlcester\b", _re.IGNORECASE), "Awlstuh"),
]


def _apply_tts_substitutions_elevenlabs(text: str) -> str:
    """
    Apply ElevenLabs-specific substitutions before synthesis.

    Alcester is intentionally NOT substituted here — the ElevenLabs
    pronunciation dictionary alias handles it natively.  Sending "Awlstuh"
    to ElevenLabs would cause a double-conversion artefact.
    """
    for pattern, replacement in _TTS_SUBSTITUTIONS_ELEVENLABS:
        text = pattern.sub(replacement, text)
    return text


def _apply_tts_substitutions_openai(text: str) -> str:
    """
    Apply OpenAI-fallback substitutions before synthesis.

    Alcester IS substituted to "Awlstuh" here because the OpenAI TTS
    engine has no pronunciation dictionary and needs the phonetic form.
    """
    for pattern, replacement in _TTS_SUBSTITUTIONS_OPENAI:
        text = pattern.sub(replacement, text)
    return text


# ---------------------------------------------------------------------------
# Shared httpx client singleton
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# ElevenLabs exhaustion flag (per process lifetime)
# ---------------------------------------------------------------------------

# Set True the first time ElevenLabs returns 401 (credits exhausted).
# All subsequent synthesise_chunk() calls skip ElevenLabs and go straight
# to the OpenAI TTS fallback.
_ELEVENLABS_EXHAUSTED: bool = False

# ---------------------------------------------------------------------------
# ElevenLabs pronunciation dictionary locator (loaded once at startup)
# ---------------------------------------------------------------------------

_PRON_DICT_LOCATOR: Optional[dict] = None   # populated by _get_pron_dict_locator()
_PRON_DICT_LOADED: bool = False              # True once we've attempted the load


def _get_pron_dict_locator() -> Optional[dict]:
    """
    Return the pronunciation_dictionary_locators entry for the shared
    ElevenLabs pronunciation dictionary, or None if the config file has
    not been created yet (run scripts/setup_pronunciation_dictionary.py).

    Loaded once and cached in _PRON_DICT_LOCATOR.  The file is at
    config/pronunciation_dict.json relative to the project root.

    Returns a single-element list suitable for the API body field, e.g.:
        [{"pronunciation_dictionary_id": "...", "version_id": "..."}]
    or None if the file is missing / malformed.
    """
    global _PRON_DICT_LOCATOR, _PRON_DICT_LOADED
    if _PRON_DICT_LOADED:
        return _PRON_DICT_LOCATOR

    _PRON_DICT_LOADED = True
    config_path = Path(__file__).resolve().parent.parent.parent / "config" / "pronunciation_dict.json"
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
        pid  = data.get("pronunciation_dictionary_id")
        vid  = data.get("version_id")
        if pid and vid:
            _PRON_DICT_LOCATOR = [{"pronunciation_dictionary_id": pid, "version_id": vid}]
            logger.info(
                "[ms_tts] Pronunciation dictionary loaded: id=%s version=%s", pid, vid
            )
        else:
            logger.warning(
                "[ms_tts] pronunciation_dict.json missing id/version_id — "
                "run scripts/setup_pronunciation_dictionary.py"
            )
    except FileNotFoundError:
        logger.info(
            "[ms_tts] config/pronunciation_dict.json not found — "
            "ElevenLabs pronunciation dictionary not active"
        )
    except Exception as exc:
        logger.warning("[ms_tts] Could not load pronunciation_dict.json: %r", exc)

    return _PRON_DICT_LOCATOR


_elevenlabs_client: Optional[httpx.AsyncClient] = None


def _get_http_client() -> httpx.AsyncClient:
    """
    Return the shared ElevenLabs HTTP client.

    Created once and reused for all TTS requests in the process.
    Persistent TLS connections save ~50-100ms per TTS call by avoiding
    re-handshake (same pattern as realtime.py).
    """
    global _elevenlabs_client
    if _elevenlabs_client is None or _elevenlabs_client.is_closed:
        _elevenlabs_client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0),
            limits=httpx.Limits(
                max_keepalive_connections=5,
                max_connections=10,
                keepalive_expiry=30.0,
            ),
        )
    return _elevenlabs_client


# ---------------------------------------------------------------------------
# TTSStream class
# ---------------------------------------------------------------------------

class TTSStream:
    """
    Handles ElevenLabs TTS for one phone call.

    Primary interface: synthesise_chunk() (MODE A, per-chunk HTTP streaming).
    Secondary interface: start_ws() (MODE B, persistent WebSocket).
    """

    def __init__(self, clinic_id: str = "") -> None:
        self._clinic_id = clinic_id       # Used for TTS_BYPASS_CLINIC gate
        self._ws: Optional[Any] = None   # WebSocket connection (MODE B only)

    # =========================================================================
    # MODE A -- HTTP streaming (per-chunk REST)
    # =========================================================================

    async def synthesise_chunk(
        self,
        text: str,
        audio_out_queue: asyncio.Queue,
        audio_out_processor: AudioOutputProcessor,
    ) -> None:
        """
        Send a single text chunk to ElevenLabs, stream PCM16 audio back,
        convert to mulaw 8kHz, and enqueue for Twilio delivery.

        This is a cancellable coroutine -- connection.py wraps it in an
        asyncio.Task so barge-in can cancel it cleanly.

        Transcode pipeline per raw chunk:
          ElevenLabs PCM16 16kHz -> AudioOutputProcessor.convert_chunk()
          -> base64 mulaw 8kHz -> audio_out_queue -> send_loop -> Twilio

        Parameters
        ----------
        text                : Text to synthesise (15-50 words from ResponseChunker)
        audio_out_queue     : Queue where base64 mulaw strings are placed
        audio_out_processor : Stateful AudioOutputProcessor (maintains 4-byte alignment)
        """
        if not text or not text.strip():
            return

        # Apply ElevenLabs-specific substitutions.  Alcester is NOT converted
        # to "Awlstuh" here — the pronunciation dictionary alias handles it.
        text = _apply_tts_substitutions_elevenlabs(text)

        # Dev bypass: TTS_BYPASS_CLINIC env var routes a specific clinic to
        # the OpenAI TTS fallback instead of ElevenLabs (cheaper for testing).
        _bypass_clinic = os.getenv("TTS_BYPASS_CLINIC", "")
        if _bypass_clinic and _bypass_clinic == self._clinic_id:
            logger.info(
                "[ms_tts] TTS_BYPASS_CLINIC=%r matches clinic_id — using OpenAI fallback",
                _bypass_clinic,
            )
            await self._synthesise_openai_fallback(text, audio_out_queue)
            return

        # Fast-path: ElevenLabs known-exhausted → use OpenAI TTS directly
        global _ELEVENLABS_EXHAUSTED
        if _ELEVENLABS_EXHAUSTED:
            await self._synthesise_openai_fallback(text, audio_out_queue)
            return

        url = (
            f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}/stream"
            f"?output_format=pcm_16000"
        )
        headers = {
            "xi-api-key":    ELEVENLABS_API_KEY,
            "Content-Type":  "application/json",
        }
        body: dict = {
            "text":       text,
            "model_id":   ELEVENLABS_MODEL_ID,
            "voice_settings": {
                "stability":        ELEVENLABS_STABILITY,
                "similarity_boost": ELEVENLABS_SIMILARITY_BOOST,
            },
        }
        _pron_locator = _get_pron_dict_locator()
        if _pron_locator:
            body["pronunciation_dictionary_locators"] = _pron_locator

        logger.info(
            "[ms_tts] synthesise_chunk: model=%s len=%d text=%r",
            ELEVENLABS_MODEL_ID, len(text), text[:60],
        )

        max_attempts = 3
        for attempt in range(max_attempts):
            try:
                async with _get_http_client().stream(
                    "POST", url, json=body, headers=headers,
                ) as resp:
                    if resp.status_code == 401:
                        # Credits exhausted — mark globally and fall back to OpenAI TTS
                        _ELEVENLABS_EXHAUSTED = True
                        logger.error(
                            "[ms_tts] ElevenLabs 401 — credits exhausted; "
                            "switching to OpenAI TTS fallback for this process"
                        )
                        await self._synthesise_openai_fallback(text, audio_out_queue)
                        return

                    if resp.status_code == 429:
                        err = await resp.aread()
                        retry_after = int(resp.headers.get("Retry-After", 5))
                        logger.warning(
                            "[ms_tts] ElevenLabs rate-limited (429) — waiting %ds "
                            "(attempt %d/%d): %s",
                            retry_after, attempt + 1, max_attempts, err[:200],
                        )
                        if attempt < max_attempts - 1:
                            await asyncio.sleep(retry_after)
                            continue  # retry the for loop
                        return  # all retries exhausted
                    if resp.status_code != 200:
                        err = await resp.aread()
                        logger.error(
                            "[ms_tts] ElevenLabs error %d: %s",
                            resp.status_code, err[:300],
                        )
                        return  # non-retryable error

                    logger.debug(
                        "[ms_tts] ElevenLabs response status=%d content-type=%r",
                        resp.status_code,
                        resp.headers.get("content-type", "MISSING"),
                    )

                    chunk_count = 0
                    async for raw_chunk in resp.aiter_bytes(chunk_size=TTS_STREAM_CHUNK_SIZE):
                        if not raw_chunk:
                            continue

                        b64 = audio_out_processor.convert_chunk(raw_chunk)
                        if b64:
                            await audio_out_queue.put(b64)
                            chunk_count += 1

                    # Flush any remaining bytes from the alignment buffer
                    b64 = audio_out_processor.flush()
                    if b64:
                        await audio_out_queue.put(b64)

                    logger.debug("[ms_tts] synthesise_chunk done: %d chunks", chunk_count)
                    return  # success — exit the retry loop

            except asyncio.CancelledError:
                # Barge-in: reset alignment state so the next chunk starts clean
                audio_out_processor.reset()
                logger.info("[ms_tts] synthesise_chunk cancelled (barge-in)")
                raise

            except RuntimeError as exc:
                if "close message" in str(exc):
                    logger.warning("[ms_tts] WebSocket closed during TTS")
                else:
                    logger.error("[ms_tts] runtime error: %r", exc)
                audio_out_processor.reset()
                return

            except Exception as exc:
                logger.error("[ms_tts] synthesise_chunk error: %r", exc)
                audio_out_processor.reset()
                return

    # =========================================================================
    # OpenAI TTS fallback (used when ElevenLabs is exhausted / unavailable)
    # =========================================================================

    async def _synthesise_openai_fallback(
        self,
        text: str,
        audio_out_queue: asyncio.Queue,
    ) -> None:
        """
        Synthesise text using OpenAI TTS when ElevenLabs is unavailable.

        OpenAI TTS returns PCM16 at 24kHz.  We convert to 8kHz mulaw for Twilio:
          audioop.ratecv(pcm_24k, 2, 1, 24000, 8000, state) → 8kHz PCM16
          audioop.lin2ulaw(pcm_8k, 2)                        → 8kHz mulaw
          base64.b64encode(mulaw)                            → Twilio payload

        NOTE: audio_out_processor is NOT used here because we produce mulaw
        directly.  The sentinel placed by _tts_loop in connection.py will
        still measure bytes-sent correctly via _send_loop._tts_bytes_sent.
        """
        from .config import OPENAI_API_KEY

        if not OPENAI_API_KEY:
            logger.error("[ms_tts_openai] OPENAI_API_KEY not set — cannot use fallback")
            return

        try:
            import audioop
        except ImportError:
            logger.error("[ms_tts_openai] audioop not available — cannot convert OpenAI PCM")
            return

        # Phonetic substitutions for OpenAI TTS — no pronunciation dictionary
        # available, so "Awlstuh" substitution is applied here only.
        text = _apply_tts_substitutions_openai(text)

        url = "https://api.openai.com/v1/audio/speech"
        headers = {
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type":  "application/json",
        }
        body = {
            "model":           "tts-1",
            "voice":           "nova",      # natural, clear female voice
            "input":           text,
            "response_format": "pcm",       # 24kHz PCM16 signed little-endian
        }

        logger.info("[ms_tts_openai] synthesising (fallback): %r", text[:60])

        try:
            _ratecv_state = None
            chunk_count   = 0

            async with _get_http_client().stream(
                "POST", url, json=body, headers=headers
            ) as resp:
                if resp.status_code != 200:
                    err = await resp.aread()
                    logger.error(
                        "[ms_tts_openai] error %d: %s",
                        resp.status_code, err[:300],
                    )
                    return

                # Stream PCM24k → convert → enqueue as mulaw
                # 1920 bytes = 40ms of 24kHz PCM16 stereo-equivalent chunk size
                async for raw_chunk in resp.aiter_bytes(chunk_size=1920):
                    if not raw_chunk:
                        continue
                    try:
                        # 3:1 rate conversion: 24000 Hz → 8000 Hz
                        pcm_8k, _ratecv_state = audioop.ratecv(
                            raw_chunk, 2, 1, 24000, 8000, _ratecv_state,
                        )
                        # PCM16 → G.711 mu-law
                        ulaw = audioop.lin2ulaw(pcm_8k, 2)
                        b64  = base64.b64encode(ulaw).decode("ascii")
                        await audio_out_queue.put(b64)
                        chunk_count += 1
                    except Exception as exc:
                        logger.warning("[ms_tts_openai] conversion error: %r", exc)

            logger.info("[ms_tts_openai] done: %d chunks", chunk_count)

        except asyncio.CancelledError:
            logger.info("[ms_tts_openai] cancelled (barge-in)")
            raise
        except Exception as exc:
            logger.error("[ms_tts_openai] error: %r", exc)

    # =========================================================================
    # MODE B -- WebSocket streaming (persistent connection)
    # =========================================================================

    async def start_ws(
        self,
        tts_text_queue: asyncio.Queue,
        audio_out_queue: asyncio.Queue,
        stop_event: asyncio.Event,
    ) -> None:
        """
        Open one persistent ElevenLabs WebSocket for the entire call.

        Sends text chunks as they arrive from tts_text_queue.
        Receives audio concurrently and puts it onto audio_out_queue.

        WebSocket protocol:
          SEND init:  {"text": " ", "voice_settings": {...}, "xi_api_key": "...",
                       "generation_config": {"chunk_length_schedule": [50, 100, 150]}}
          SEND text:  {"text": "<chunk>", "flush": false}
          SEND flush: {"text": "", "flush": true}   <- end of utterance
          RECV audio: {"audio": "<base64 pcm16k>"}  or {"isFinal": true}
        """
        import websockets
        import websockets.exceptions

        ws_url = (
            f"wss://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}"
            f"/stream-input?model_id={ELEVENLABS_MODEL_ID}&output_format=pcm_16000"
        )

        audio_proc = AudioOutputProcessor()
        reconnect_attempted = False

        while not stop_event.is_set():
            try:
                async with websockets.connect(ws_url, ping_interval=5, ping_timeout=10) as ws:
                    self._ws = ws
                    logger.info("[ms_tts_ws] connected to ElevenLabs WebSocket")

                    # Send initialisation message
                    await ws.send(json.dumps({
                        "text": " ",
                        "voice_settings": {
                            "stability":        ELEVENLABS_STABILITY,
                            "similarity_boost": ELEVENLABS_SIMILARITY_BOOST,
                        },
                        "xi_api_key":       ELEVENLABS_API_KEY,
                        "generation_config": {
                            "chunk_length_schedule": [50, 100, 150],
                        },
                    }))

                    send_task = asyncio.create_task(
                        self._ws_send_text_loop(ws, tts_text_queue, stop_event),
                        name="tts_ws_send",
                    )
                    recv_task = asyncio.create_task(
                        self._ws_receive_audio_loop(ws, audio_out_queue, audio_proc, stop_event),
                        name="tts_ws_recv",
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

                    logger.warning("[ms_tts_ws] WebSocket closed unexpectedly")

            except Exception as exc:
                logger.error("[ms_tts_ws] WebSocket error: %r", exc)

            finally:
                self._ws = None

            if stop_event.is_set():
                return

            if not reconnect_attempted:
                reconnect_attempted = True
                logger.info("[ms_tts_ws] attempting reconnect in 0.5s...")
                await asyncio.sleep(0.5)
            else:
                logger.error("[ms_tts_ws] reconnect failed -- giving up")
                return

    async def _ws_send_text_loop(
        self,
        ws: Any,
        tts_text_queue: asyncio.Queue,
        stop_event: asyncio.Event,
    ) -> None:
        """
        Continuously read text chunks from tts_text_queue and send to ElevenLabs.
        A None sentinel signals end of utterance -- send flush message.
        """
        try:
            while not stop_event.is_set():
                try:
                    chunk_text = await asyncio.wait_for(tts_text_queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue

                if chunk_text is None:
                    # End of utterance sentinel -- flush ElevenLabs buffer
                    await ws.send(json.dumps({"text": "", "flush": True}))
                    logger.debug("[ms_tts_ws] sent flush")
                    continue

                if not chunk_text or not chunk_text.strip():
                    continue

                # Apply ElevenLabs-specific substitutions (no Awlstuh — pron dict handles it).
                chunk_text = _apply_tts_substitutions_elevenlabs(chunk_text)
                await ws.send(json.dumps({"text": chunk_text, "flush": False}))
                logger.debug("[ms_tts_ws] sent text: %r", chunk_text[:40])

        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error("[ms_tts_ws] send error: %r", exc)

    async def _ws_receive_audio_loop(
        self,
        ws: Any,
        audio_out_queue: asyncio.Queue,
        audio_proc: AudioOutputProcessor,
        stop_event: asyncio.Event,
    ) -> None:
        """
        Receive audio from ElevenLabs WebSocket.

        {"audio": "<base64 pcm16k>"}  -> decode -> convert -> audio_out_queue
        {"isFinal": true}             -> put None sentinel onto audio_out_queue
        """
        import websockets.exceptions

        try:
            async for raw_msg in ws:
                if stop_event.is_set():
                    break

                try:
                    msg = json.loads(raw_msg)
                except json.JSONDecodeError:
                    continue

                if "audio" in msg and msg["audio"]:
                    try:
                        pcm_bytes = base64.b64decode(msg["audio"])
                    except Exception:
                        continue
                    b64 = audio_proc.convert_chunk(pcm_bytes)
                    if b64:
                        await audio_out_queue.put(b64)

                elif msg.get("isFinal"):
                    # Flush alignment buffer
                    b64 = audio_proc.flush()
                    if b64:
                        await audio_out_queue.put(b64)
                    await audio_out_queue.put(None)   # sentinel for downstream
                    logger.debug("[ms_tts_ws] isFinal received")

                elif "error" in msg:
                    logger.error("[ms_tts_ws] ElevenLabs error: %s", msg["error"])
                    return

        except asyncio.CancelledError:
            audio_proc.reset()
            raise
        except Exception as exc:
            logger.error("[ms_tts_ws] receive error: %r", exc)
            audio_proc.reset()
