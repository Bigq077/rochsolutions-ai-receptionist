"""
call_runner.py — Makes a single Twilio call and runs a test scenario.

Architecture (WebSocket-compatible):
  - Pre-generates ElevenLabs TTS audio for all patient responses
  - Builds a single TwiML script that plays responses with timed pauses
    (waits for Susie to finish speaking, then plays patient response)
  - After call ends, queries Susie's /admin/test/session/{call_sid} endpoint
    to retrieve the conversation history from Redis
  - This approach works with ALL Susie pipeline modes because it reads
    directly from Susie's session store, not from call recordings

The webhook server (ngrok + uvicorn) is managed externally by SharedServer
in server_manager.py — a single server is shared across all scenarios to
prevent the ngrok cascade failures that occurred when a new tunnel was
created per test.
"""

import asyncio
import base64
import json
import logging
import subprocess
import tempfile
import time
import wave
from datetime import datetime

import httpx
import numpy as np
from twilio.rest import Client

from .config import (
    ELEVENLABS_API_KEY,
    ELEVENLABS_PATIENT_VOICE_ID,
    MAX_CALL_DURATION_SECONDS,
    RENDER_SERVER_URL,
    RESULTS_DIR,
    SUSIE_NUMBER,
    TWILIO_ACCOUNT_SID,
    TWILIO_AUTH_TOKEN,
    TWILIO_TEST_NUMBER,
)

logger = logging.getLogger(__name__)

# Exported so server_manager can import it without circular issues
MAX_TURNS_PER_CALL = 20

# How long to wait (seconds) for Susie to finish her greeting before
# the patient starts speaking. Must be long enough for:
#   - Twilio to connect the call (~2s)
#   - Susie's WebSocket pipeline to initialise (~3-5s)
#   - ElevenLabs TTS to generate and stream the greeting (~3-5s)
#   - The greeting audio to play (~5-8s)
# Total: ~13-18s. Use 20s — gives the full greeting time to play and
# ensures patient audio does not start before Susie has finished speaking,
# which would confuse AssemblyAI's end-of-turn detection.
GREETING_WAIT_SECONDS = 20

# How long to wait (seconds) after each patient response for Susie to reply
# before playing the next patient response.
# Must cover: AssemblyAI end-of-turn detection (~0.3s silence after Polly ends)
# + LLM round-trip (~3-8s) + ElevenLabs TTS generation (~2-4s)
# + TTS audio playback (~3-8s depending on Susie's response length).
# Total worst case: ~20s. Use 22s for safety margin.
TURN_WAIT_SECONDS = 25

# Module-level flag — set to False after first 401 to skip further ElevenLabs
# attempts without burning 3 retries × N turns per scenario.
_ELEVENLABS_AVAILABLE = True


class CallRunner:
    def __init__(self, scenario: dict, shared_server):
        self.scenario = scenario
        self._shared_server = shared_server
        self.call_sid = None
        self.inbound_sid: str | None = None  # Susie's inbound call SID — set by injection loop
        self.call_placed_at = None   # UTC datetime when outbound call was created
        self.current_turn = 0
        self.call_complete = asyncio.Event()
        self.susie_said = []
        self.test_said = []
        self.call_start_time = None
        self.call_end_time = None
        self.end_reason = "unknown"
        self._webhook_url: str = ""
        self._audio_files: dict = {}
        # Raw G.711 µ-law bytes (8000 Hz mono) for each patient response turn.
        # Sent through the /patient-ws WebSocket so Twilio delivers them as
        # caller audio to Susie's inbound Media Stream track.
        self._audio_mulaw: dict[int, bytes] = {}
        self._recording_url: str | None = None
        self._recording_sid: str | None = None
        # Flow-engine progress fields — read from admin session endpoint
        self._flow_step: int | None = None
        self._flow_started: bool | None = None
        self._booking_confirmed: bool | None = None
        self._reschedule_confirmed: bool | None = None
        self._cancel_confirmed: bool | None = None
        self._selected_slot: str | None = None
        self._full_name: str | None = None
        self._intent: str | None = None
        self._reason: str | None = None

    async def run(self) -> dict:
        """Make the call, run the scenario, return full result dict."""
        self.call_start_time = time.time()
        self.call_complete.clear()

        # Check ngrok tunnel is still alive before starting
        tunnel_ok = await self._shared_server.ensure_tunnel_alive()
        if not tunnel_ok:
            self.end_reason = "ngrok_died"
            self.call_end_time = time.time()
            return self._build_result()

        self._webhook_url = self._shared_server.webhook_url

        # Pre-generate all patient response audio files before making the call
        # so the TwiML script can reference them immediately
        logger.info("[%s] Pre-generating patient audio files...", self.scenario["id"])
        await self._pre_generate_audio()

        # Register this runner as the active one
        self._shared_server.current_runner = self

        logger.info("[%s] Webhook: %s", self.scenario["id"], self._webhook_url)

        # Make outbound call via Twilio
        client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        call = client.calls.create(
            to=SUSIE_NUMBER,
            from_=TWILIO_TEST_NUMBER,
            url=self._webhook_url + "/twiml/start",
            status_callback=self._webhook_url + "/status",
            status_callback_method="POST",
        )
        self.call_sid = call.sid
        import datetime as _dt
        self.call_placed_at = _dt.datetime.utcnow()
        logger.info("[%s] Call started: %s", self.scenario["id"], self.call_sid)

        # Launch transcript injection in the background.
        # This coroutine finds Susie's inbound call SID, waits for the greeting,
        # then POSTs each patient response to /ms/test/inject-transcript/{sid}
        # — bypassing STT and going straight to Susie's LLM pipeline.
        inject_task = asyncio.create_task(
            self._run_transcript_injection(client),
            name=f"inject_{self.scenario['id']}",
        )

        # Wait for call to complete or timeout
        try:
            await asyncio.wait_for(
                self.call_complete.wait(),
                timeout=MAX_CALL_DURATION_SECONDS,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "[%s] Call timed out after %ds",
                self.scenario["id"], MAX_CALL_DURATION_SECONDS,
            )
            self._end_call("timeout")
            try:
                await asyncio.to_thread(
                    client.calls(self.call_sid).update, status="completed"
                )
            except Exception:
                pass
        finally:
            inject_task.cancel()
            try:
                await inject_task
            except asyncio.CancelledError:
                pass

        self.call_end_time = time.time()

        # Deregister — no more callbacks needed
        self._shared_server.current_runner = None

        # Wait for Susie's session to be fully saved to Redis.
        # 15 seconds gives the server time to process the final turn and flush all
        # Redis writes — a 5-second wait was too short for longer calls (9+ turns).
        logger.info("[%s] Waiting for session to be saved...", self.scenario["id"])
        await asyncio.sleep(15)

        # Use the inbound SID captured during injection (set by _run_transcript_injection).
        # Fall back to a fresh lookup only if injection never ran (e.g. call failed before greeting).
        inbound_sid = self.inbound_sid or await self._find_inbound_call_sid(client)
        logger.info("[%s] Inbound call SID: %s", self.scenario["id"], inbound_sid or "not found")

        # Retrieve what Susie said from her Redis session
        await self._fetch_session_from_server(inbound_sid)

        return self._build_result()

    async def _find_inbound_call_sid(
        self, client: Client, status: str | None = None
    ) -> str | None:
        """
        Find the inbound call SID for Susie's side of the call.

        When the test makes an outbound call to Susie, Twilio creates:
          - Outbound leg (test's side): self.call_sid
          - Inbound leg (Susie's side): a separate inbound call SID

        Susie's session is stored under the inbound SID.
        We find it by listing recent calls TO Susie's number and filtering
        for inbound calls (direction not outbound-api).

        Pass status="in-progress" when calling during an active call to avoid
        picking up a stale completed inbound SID from a previous test scenario.
        """
        import datetime as _dt
        try:
            # Filter by from_=TWILIO_TEST_NUMBER so we only match calls placed
            # by OUR test number, not other concurrent calls to Susie's line.
            list_kwargs: dict = {"to": SUSIE_NUMBER, "limit": 10}
            if TWILIO_TEST_NUMBER:
                list_kwargs["from_"] = TWILIO_TEST_NUMBER
            if status:
                list_kwargs["status"] = status
            calls = client.calls.list(**list_kwargs)
            for call in calls:
                # Skip our own outbound call
                if call.sid == self.call_sid:
                    continue
                # The inbound call is the one that's NOT outbound-api
                if call.direction != "outbound-api":
                    # Reject stale SIDs from previous test scenarios — only
                    # accept inbound calls created after our outbound call was placed.
                    if self.call_placed_at and call.date_created:
                        created = call.date_created
                        if hasattr(created, "tzinfo") and created.tzinfo:
                            import pytz
                            placed = self.call_placed_at.replace(tzinfo=pytz.UTC)
                        else:
                            placed = self.call_placed_at
                        if created < placed - _dt.timedelta(seconds=30):
                            logger.info(
                                "[%s] Skipping stale inbound SID %s (created %s, placed at %s)",
                                self.scenario["id"], call.sid, created, placed,
                            )
                            continue
                    logger.info(
                        "[%s] Found inbound call SID: %s (direction=%s status=%s)",
                        self.scenario["id"], call.sid, call.direction,
                        getattr(call, "status", "?"),
                    )
                    return call.sid
        except Exception as exc:
            logger.warning(
                "[%s] Failed to find inbound call SID: %r",
                self.scenario["id"], exc,
            )
        return None

    # ── TwiML builders ──────────────────────────────────────────────────────

    def build_start_twiml(self) -> str:
        """
        Build the TwiML script for the outbound (test caller) leg.

        Strategy — transcript injection via HTTP:
          The test caller's leg just holds the call open with a long <Pause>.
          Meanwhile, the test runner's _run_transcript_injection() background
          coroutine POSTs patient utterances directly to Susie's
          /ms/test/inject-transcript/{call_sid} endpoint, which puts them
          straight onto Susie's transcript_queue — bypassing STT entirely.

          This is the correct approach for Susie's Media Streams pipeline:
          - <Say>/<Play> only affect the downlink (what the test caller hears),
            not the uplink (what Susie's inbound track receives).
          - Bidirectional <Connect><Stream> injects into the test caller's
            outbound audio (what the test caller hears), not toward Susie.
          - Direct transcript injection bypasses audio routing entirely and
            feeds text straight into Susie's LLM pipeline.

        The test runner hangs up via Twilio REST API after all transcripts
        are injected, well before the <Pause> expires.
        """
        # Keep the call alive long enough for all scenario turns
        max_call_seconds = (
            GREETING_WAIT_SECONDS
            + len(self.scenario.get("responses", [])) * (TURN_WAIT_SECONDS + 10)
            + 60
        )
        return "\n".join([
            '<?xml version="1.0" encoding="UTF-8"?>',
            "<Response>",
            f'  <Pause length="{max_call_seconds}"/>',
            "  <Hangup/>",
            "</Response>",
        ])

    def _twiml_hangup(self) -> str:
        return (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            "<Response>\n"
            "  <Hangup/>\n"
            "</Response>"
        )

    # ── Transcript injection ──────────────────────────────────────────────────

    async def _run_transcript_injection(self, client: Client) -> None:
        """
        Background coroutine: drives the conversation by POSTing patient
        utterances directly to Susie's /ms/test/inject-transcript/{call_sid}.

        This bypasses Twilio audio routing entirely — the text goes straight
        onto Susie's transcript_queue → LLM → TTS pipeline.

        Timeline:
          1. Poll for the inbound call SID (Susie's side) — needed for the endpoint
          2. Wait GREETING_WAIT_SECONDS for Susie's greeting to play
          3. For each patient response:
               a. POST text to inject endpoint
               b. Wait TURN_WAIT_SECONDS for Susie to reply
          4. Hang up via Twilio REST API → triggers /status callback → call ends
        """
        # ── 1. Find inbound call SID (Susie's side) ──────────────────────
        # Filter by status="in-progress" so we never pick up a stale completed
        # inbound SID from the previous scenario running moments earlier.
        await asyncio.sleep(5)  # give Twilio a few seconds to route the call
        inbound_sid: str | None = None
        for attempt in range(20):  # 20 × 2 s = 40 s budget
            inbound_sid = await self._find_inbound_call_sid(
                client, status="in-progress"
            )
            if inbound_sid:
                logger.info(
                    "[%s] Transcript injection: found inbound SID %s (attempt %d)",
                    self.scenario["id"], inbound_sid, attempt + 1,
                )
                break
            logger.debug(
                "[%s] Inbound SID not found yet (attempt %d) — retrying in 2s",
                self.scenario["id"], attempt + 1,
            )
            await asyncio.sleep(2)

        if not inbound_sid:
            logger.error(
                "[%s] Transcript injection aborted — could not find inbound SID after 15 tries",
                self.scenario["id"],
            )
            return

        # Save for session fetch after call ends — prevents picking up a
        # different call's session if multiple inbound calls exist.
        self.inbound_sid = inbound_sid

        # ── 2. Wait for Susie's greeting ──────────────────────────────────
        logger.info(
            "[%s] Waiting %ds for Susie's greeting...",
            self.scenario["id"], GREETING_WAIT_SECONDS,
        )
        await asyncio.sleep(GREETING_WAIT_SECONDS)

        # ── 3. Inject patient responses one by one ────────────────────────
        responses = self.scenario.get("responses", [])
        for i, response in enumerate(responses):
            text = response if isinstance(response, str) else response.get("text", "")
            if text.strip():
                # Record timestamp BEFORE the inject POST so that slow server
                # response times (e.g. Render processing a tool call) don't
                # inflate the gap and trip the no_dead_air check.  The gap then
                # measures ~TURN_WAIT_SECONDS between consecutive utterances.
                if i < len(self.test_said):
                    self.test_said[i]["timestamp"] = time.time()
                await self._inject_transcript(inbound_sid, text, i)
            # Wait for Susie to process and respond before next injection
            logger.info(
                "[%s] Waiting %ds for Susie's response to turn %d",
                self.scenario["id"], TURN_WAIT_SECONDS, i,
            )
            await asyncio.sleep(TURN_WAIT_SECONDS)

        # ── 4. Hang up the call ───────────────────────────────────────────
        logger.info("[%s] All transcripts injected — hanging up", self.scenario["id"])
        try:
            await asyncio.to_thread(
                lambda: client.calls(self.call_sid).update(status="completed")
            )
        except Exception as exc:
            logger.warning("[%s] Hang-up via REST failed: %r", self.scenario["id"], exc)

    async def _inject_transcript(
        self, inbound_sid: str, text: str, turn_index: int
    ) -> None:
        """POST a patient utterance to Susie's inject-transcript endpoint."""
        url = f"{RENDER_SERVER_URL}/ms/test/inject-transcript/{inbound_sid}"
        logger.info(
            "[%s] Injecting turn %d → %s: %.60s",
            self.scenario["id"], turn_index, inbound_sid[:8], text,
        )
        for attempt in range(2):
            try:
                async with httpx.AsyncClient(timeout=15) as http:
                    resp = await http.post(url, json={"text": text})
                    data = resp.json()
                    if data.get("ok"):
                        diag = data.get("diag", {})
                        logger.info(
                            "[%s] Injected turn %d OK  diag=%s",
                            self.scenario["id"], turn_index, diag,
                        )
                    else:
                        logger.warning(
                            "[%s] Inject turn %d failed: %s",
                            self.scenario["id"], turn_index, data.get("error"),
                        )
                break  # success — no retry needed
            except (httpx.ConnectTimeout, httpx.ConnectError) as exc:
                if attempt == 0:
                    logger.warning(
                        "[%s] Inject turn %d connection error (attempt 1): %r — retrying in 10s",
                        self.scenario["id"], turn_index, exc,
                    )
                    await asyncio.sleep(10)
                else:
                    logger.warning(
                        "[%s] Inject turn %d error after retry: %r",
                        self.scenario["id"], turn_index, exc,
                    )
            except Exception as exc:
                logger.warning(
                    "[%s] Inject turn %d error: %r", self.scenario["id"], turn_index, exc
                )
                break  # non-connection errors — don't retry

    # ── Audio generation ─────────────────────────────────────────────────────

    def _audio_filename(self, turn_index: int) -> str:
        """Return the audio filename for a given turn index (kept for compatibility)."""
        turn_label = f"{self.scenario['id'].replace('.', '_')}_{turn_index}"
        return f"audio_{turn_label}.mp3"

    async def _pre_generate_audio(self) -> None:
        """
        Pre-generate G.711 µ-law audio for all patient responses.

        µ-law bytes are stored in self._audio_mulaw[turn_index] and sent
        directly through the /patient-ws WebSocket so Twilio delivers them as
        caller audio to Susie's inbound Media Stream track.

        Priority order per turn:
          1. ElevenLabs ulaw_8000 — best voice quality, instant if credits available
          2. Windows SAPI (PowerShell) — good fallback, always available on Windows
          3. Estimated silence — last resort; test will fail but not hang
        """
        responses = self.scenario.get("responses", [])
        for i, response in enumerate(responses):
            text = response if isinstance(response, str) else response.get("text", "")
            if not text.strip():
                # Silence turn — no audio needed; timestamp will remain None
                # (no inject happens so there's no meaningful inject time to record)
                self.test_said.append({"turn": i, "text": "", "timestamp": None})
                continue

            mulaw_bytes = await self._generate_mulaw_for_turn(text, i)
            if mulaw_bytes:
                self._audio_mulaw[i] = mulaw_bytes
            else:
                logger.error(
                    "[%s] All audio generation methods failed for turn %d — "
                    "Susie will hear silence; test will likely fail",
                    self.scenario["id"], i,
                )

            # Record what the test intends to say.
            # Timestamp is intentionally None here — it will be updated to the
            # actual inject time in _run_transcript_injection() so that
            # max_gap_seconds reflects real conversation pacing, not pre-gen
            # timing (which happens before the call even starts).
            self.test_said.append({
                "turn": i,
                "text": text,
                "timestamp": None,
            })

    async def _generate_mulaw_for_turn(self, text: str, turn_index: int) -> bytes | None:
        """
        Generate G.711 µ-law bytes for one patient response.
        Returns None only if every generation method fails.
        """
        # ── 1. ElevenLabs ulaw_8000 ───────────────────────────────────────
        global _ELEVENLABS_AVAILABLE
        if _ELEVENLABS_AVAILABLE and ELEVENLABS_API_KEY:
            try:
                mulaw = await self._elevenlabs_mulaw(text)
                if mulaw:
                    logger.info(
                        "[%s] ElevenLabs µ-law audio for turn %d: %d bytes",
                        self.scenario["id"], turn_index, len(mulaw),
                    )
                    return mulaw
            except Exception as exc:
                logger.warning(
                    "[%s] ElevenLabs µ-law failed for turn %d: %r — trying SAPI",
                    self.scenario["id"], turn_index, exc,
                )

        # ── 2. Windows SAPI via PowerShell ───────────────────────────────
        try:
            mulaw = await self._sapi_mulaw(text)
            if mulaw:
                logger.info(
                    "[%s] SAPI µ-law audio for turn %d: %d bytes",
                    self.scenario["id"], turn_index, len(mulaw),
                )
                return mulaw
        except Exception as exc:
            logger.warning(
                "[%s] SAPI µ-law failed for turn %d: %r — using silence estimate",
                self.scenario["id"], turn_index, exc,
            )

        # ── 3. Estimated silence (last resort) ───────────────────────────
        words = len(text.split())
        duration_s = max(2.0, words / 2.5)  # ~150 wpm = 2.5 words/s
        silence_bytes = bytes([0xFF] * int(duration_s * 8000))
        logger.warning(
            "[%s] Using silence for turn %d (%d s, %d bytes)",
            self.scenario["id"], turn_index, duration_s, len(silence_bytes),
        )
        return silence_bytes

    async def _elevenlabs_mulaw(self, text: str) -> bytes | None:
        """
        Fetch G.711 µ-law audio from ElevenLabs.
        Returns raw mulaw bytes (no WAV header) or None on error.
        Sets _ELEVENLABS_AVAILABLE=False on 401 (credits exhausted).
        """
        global _ELEVENLABS_AVAILABLE
        if not _ELEVENLABS_AVAILABLE:
            return None

        last_exc = None
        for attempt in range(3):
            try:
                async with httpx.AsyncClient(timeout=30) as client:
                    response = await client.post(
                        f"https://api.elevenlabs.io/v1/text-to-speech/"
                        f"{ELEVENLABS_PATIENT_VOICE_ID}"
                        f"?output_format=ulaw_8000",
                        headers={"xi-api-key": ELEVENLABS_API_KEY},
                        json={
                            "text": text,
                            "model_id": "eleven_flash_v2_5",
                            "voice_settings": {
                                "stability": 0.5,
                                "similarity_boost": 0.8,
                            },
                        },
                    )
                    if response.status_code == 401:
                        _ELEVENLABS_AVAILABLE = False
                        logger.error(
                            "ElevenLabs 401 — credits exhausted, disabling ElevenLabs for this run"
                        )
                        return None
                    response.raise_for_status()
                    return response.content  # raw µ-law bytes
            except Exception as exc:
                last_exc = exc
                if not _ELEVENLABS_AVAILABLE:
                    return None
                logger.warning("ElevenLabs attempt %d/3 failed: %r", attempt + 1, exc)
                if attempt < 2:
                    await asyncio.sleep(2)

        raise last_exc

    async def _sapi_mulaw(self, text: str) -> bytes | None:
        """
        Generate 8kHz PCM16 WAV via Windows SAPI (PowerShell), then convert
        to G.711 µ-law using numpy.  Returns mulaw bytes or None if unavailable.
        """
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            wav_path = f.name

        # Escape single-quotes in text for PowerShell
        ps_text = text.replace("'", "''")
        ps_script = (
            "Add-Type -AssemblyName System.Speech; "
            "$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
            "$fmt = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo("
            "8000, "
            "[System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, "
            "[System.Speech.AudioFormat.AudioChannel]::Mono); "
            f"$synth.SetOutputToWaveFile('{wav_path}', $fmt); "
            f"$synth.Speak('{ps_text}'); "
            "$synth.SetOutputToDefaultAudioDevice()"
        )

        proc = await asyncio.to_thread(
            subprocess.run,
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
            capture_output=True,
            timeout=30,
        )
        if proc.returncode != 0:
            stderr = proc.stderr.decode(errors="replace")
            raise RuntimeError(f"PowerShell SAPI failed (rc={proc.returncode}): {stderr[:200]}")

        # Read the WAV and extract PCM16 bytes
        try:
            with wave.open(wav_path, "rb") as wf:
                if wf.getnchannels() != 1 or wf.getsampwidth() != 2:
                    raise ValueError(
                        f"Unexpected WAV format: ch={wf.getnchannels()} sw={wf.getsampwidth()}"
                    )
                pcm_bytes = wf.readframes(wf.getnframes())
        finally:
            try:
                import os
                os.unlink(wav_path)
            except Exception:
                pass

        return self._pcm16_to_ulaw(pcm_bytes)

    @staticmethod
    def _pcm16_to_ulaw(pcm_bytes: bytes) -> bytes:
        """
        Convert 16-bit signed PCM (8 kHz mono) to G.711 µ-law bytes using numpy.

        Implements the standard G.711 µ-law compression formula:
          µ = 255 (standard telephony µ-law)
          bias = 33 (standard); we use the ITU-T G.711 variant with BIAS = 132

        Compatible with Python 3.14 (audioop removed in that release).
        """
        MULAW_BIAS = 132
        MULAW_MAX  = 32767

        samples = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.int32)

        # Sign bit: 0x80 for negative samples, 0x00 for positive
        sign = np.where(samples < 0, np.int32(0x80), np.int32(0x00))
        samples = np.abs(samples)
        samples = np.clip(samples + MULAW_BIAS, 0, MULAW_MAX)

        # Exponent lookup table (maps sample >> 7 → exponent 0..7)
        exp_lut = np.array([
            0, 0, 1, 1, 2, 2, 2, 2, 3, 3, 3, 3, 3, 3, 3, 3,
            4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4,
            5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5,
            5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5,
            6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6,
            6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6,
            6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6,
            6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6,
            7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7,
            7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7,
            7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7,
            7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7,
            7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7,
            7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7,
            7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7,
            7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7,
        ], dtype=np.int32)

        exponent = exp_lut[np.clip(samples >> 7, 0, 255)]
        mantissa = ((samples >> (exponent + 3)) & 0x0F)
        ulawbyte = (~(sign | (exponent << 4) | mantissa)) & 0xFF
        return ulawbyte.astype(np.uint8).tobytes()

    # ── Session retrieval ─────────────────────────────────────────────────────

    async def _fetch_session_from_server(self, inbound_sid: str | None = None) -> None:
        """
        Retrieve what Susie said by querying her Redis session via the
        /admin/test/session/{call_sid} endpoint on the Render server.

        The session is stored under the INBOUND call SID (Susie's side),
        not the outbound SID (test's side). We try the inbound SID first,
        then fall back to the outbound SID.

        The session contains conversation_history with assistant turns
        (what Susie said) and user turns (what the caller said).
        """
        # Try inbound SID first (Susie's side), then outbound SID as fallback
        sids_to_try = []
        if inbound_sid:
            sids_to_try.append(inbound_sid)
        if self.call_sid:
            sids_to_try.append(self.call_sid)

        if not sids_to_try:
            logger.warning("[%s] No call SID — cannot fetch session", self.scenario["id"])
            return

        # Use the first SID to try (inbound preferred)
        sid_to_use = sids_to_try[0]
        url = f"{RENDER_SERVER_URL}/admin/test/session/{sid_to_use}"
        logger.info("[%s] Fetching session from: %s", self.scenario["id"], url)

        try:
            async with httpx.AsyncClient(timeout=30) as http:
                # Poll all 6 attempts and use the most complete session
                # (longest conversation_history). A partial session from an
                # early attempt would miss turns from longer calls.
                best_data: dict | None = None
                for attempt in range(6):
                    resp = await http.get(url)
                    data = resp.json()

                    if data.get("ok"):
                        history = data.get("conversation_history", [])
                        prev_len = len((best_data or {}).get("conversation_history", []))
                        if best_data is None or len(history) > prev_len:
                            best_data = data
                        logger.info(
                            "[%s] Session attempt %d/6: %d history entries (best so far: %d)",
                            self.scenario["id"], attempt + 1, len(history),
                            len(best_data.get("conversation_history", [])),
                        )
                    else:
                        error = data.get("error", "unknown")
                        logger.info(
                            "[%s] Session not ready yet (attempt %d/6): %s",
                            self.scenario["id"], attempt + 1, error,
                        )
                    if attempt < 5:
                        await asyncio.sleep(5)

                if best_data is None:
                    logger.warning(
                        "[%s] Session not found after 6 attempts",
                        self.scenario["id"],
                    )
                    return

                data = best_data
                history = data.get("conversation_history", [])
                turns_data = data.get("turns", [])

                # Read flow-engine progress fields
                self._flow_step             = data.get("flow_step")
                self._flow_started          = data.get("flow_started")
                self._booking_confirmed     = data.get("booking_confirmed")
                self._reschedule_confirmed  = data.get("reschedule_confirmed")
                self._cancel_confirmed      = data.get("cancel_confirmed")
                self._selected_slot         = data.get("selected_slot")
                self._full_name             = data.get("full_name")
                self._intent                = data.get("intent")
                self._reason                = data.get("reason")

                logger.info(
                    "[%s] Using best session: %d history entries, %d turns "
                    "flow_step=%s booking_confirmed=%s",
                    self.scenario["id"], len(history), len(turns_data),
                    self._flow_step, self._booking_confirmed,
                )

                # Extract Susie's turns from conversation_history
                turn_index = 0
                for entry in history:
                    if entry.get("role") == "assistant":
                        text = entry.get("content", "").strip()
                        if text:
                            self.susie_said.append({
                                "turn": turn_index,
                                "text": text,
                                "confidence": 1.0,
                                "timestamp": self.call_start_time or time.time(),
                            })
                            turn_index += 1

                # If no history, try turns field
                if not self.susie_said and turns_data:
                    for entry in turns_data:
                        if entry.get("role") == "assistant":
                            text = entry.get("text", "").strip()
                            if text:
                                self.susie_said.append({
                                    "turn": turn_index,
                                    "text": text,
                                    "confidence": 1.0,
                                    "timestamp": self.call_start_time or time.time(),
                                })
                                turn_index += 1

                self.current_turn = turn_index
                logger.info(
                    "[%s] Extracted %d Susie turns from session",
                    self.scenario["id"], len(self.susie_said),
                )

        except Exception as exc:
            logger.error(
                "[%s] Failed to fetch session: %r",
                self.scenario["id"], exc,
            )

    # ── Scenario logic ───────────────────────────────────────────────────────

    def _get_next_response(self, susie_text: str) -> str | None:
        """Return next scripted response, or None when scenario is done."""
        responses = self.scenario.get("responses", [])

        if callable(responses):
            return responses(self.current_turn, susie_text)

        if self.current_turn >= len(responses):
            return None

        entry = responses[self.current_turn]
        if isinstance(entry, dict):
            return entry.get("text")
        return entry

    def _end_call(self, reason: str):
        if not self.call_complete.is_set():
            self.end_reason = reason
            self.call_complete.set()

    def _build_result(self) -> dict:
        duration = (
            self.call_end_time - self.call_start_time
            if self.call_end_time and self.call_start_time
            else 0.0
        )

        # Compute max gap between consecutive patient inject times.
        # Only test_said entries updated by _run_transcript_injection() have valid
        # (non-None) timestamps — pre-gen entries start as None and stay None for
        # silence turns.  susie_said timestamps are excluded because they are all
        # set to call_start_time (pre-call) and would produce a spuriously large gap.
        valid_ts = sorted(
            t["timestamp"]
            for t in self.test_said
            if t.get("timestamp") is not None
        )
        max_gap = 0.0
        for i in range(1, len(valid_ts)):
            gap = valid_ts[i] - valid_ts[i - 1]
            if gap > max_gap:
                max_gap = gap

        return {
            "scenario_id":   self.scenario["id"],
            "scenario_name": self.scenario["name"],
            "phase":         self.scenario.get("phase", "Unknown"),
            "scenario":      self.scenario,
            "call_sid":      self.call_sid,
            "end_reason":    self.end_reason,
            "duration_seconds": round(duration, 2),
            "turns":         len(self.susie_said),
            "susie_said":    self.susie_said,
            "test_said":     self.test_said,
            "recording_url": self._recording_url,
            "timestamp":     datetime.utcnow().isoformat(),
            "max_gap_seconds": round(max_gap, 2),
            # Flow-engine progress fields from admin session endpoint
            "flow_step":              self._flow_step,
            "flow_started":           self._flow_started,
            "booking_confirmed":      self._booking_confirmed,
            "reschedule_confirmed":   self._reschedule_confirmed,
            "cancel_confirmed":       self._cancel_confirmed,
            "selected_slot":          self._selected_slot,
            "full_name":              self._full_name,
            "intent":                 self._intent,
            "reason":                 self._reason,
        }
