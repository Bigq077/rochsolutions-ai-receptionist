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
import logging
import time
from datetime import datetime

import httpx
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
# Total: ~15-20s minimum. Use 30s to be safe.
GREETING_WAIT_SECONDS = 30

# How long to wait (seconds) after each patient response for Susie to reply
# before playing the next patient response.
TURN_WAIT_SECONDS = 15


class CallRunner:
    def __init__(self, scenario: dict, shared_server):
        self.scenario = scenario
        self._shared_server = shared_server
        self.call_sid = None
        self.current_turn = 0
        self.call_complete = asyncio.Event()
        self.susie_said = []
        self.test_said = []
        self.call_start_time = None
        self.call_end_time = None
        self.end_reason = "unknown"
        self._webhook_url: str = ""
        self._audio_files: dict = {}
        self._recording_url: str | None = None
        self._recording_sid: str | None = None

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
        logger.info("[%s] Call started: %s", self.scenario["id"], self.call_sid)

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
                client.calls(self.call_sid).update(status="completed")
            except Exception:
                pass

        self.call_end_time = time.time()

        # Deregister — no more callbacks needed
        self._shared_server.current_runner = None

        # Wait a moment for Susie's session to be saved to Redis
        logger.info("[%s] Waiting for session to be saved...", self.scenario["id"])
        await asyncio.sleep(5)

        # Find the inbound call SID (Susie's side) — the session is stored under this SID
        inbound_sid = await self._find_inbound_call_sid(client)
        logger.info("[%s] Inbound call SID: %s", self.scenario["id"], inbound_sid or "not found")

        # Retrieve what Susie said from her Redis session
        await self._fetch_session_from_server(inbound_sid)

        return self._build_result()

    async def _find_inbound_call_sid(self, client: Client) -> str | None:
        """
        Find the inbound call SID for Susie's side of the call.

        When the test makes an outbound call to Susie, Twilio creates:
          - Outbound leg (test's side): self.call_sid
          - Inbound leg (Susie's side): a separate inbound call SID

        Susie's session is stored under the inbound SID.
        We find it by listing recent calls TO Susie's number and filtering
        for inbound calls (direction not outbound-api).
        """
        try:
            # List recent calls to Susie's number (both inbound and outbound)
            calls = client.calls.list(to=SUSIE_NUMBER, limit=10)
            for call in calls:
                # Skip our own outbound call
                if call.sid == self.call_sid:
                    continue
                # The inbound call is the one that's NOT outbound-api
                if call.direction != "outbound-api":
                    logger.info(
                        "[%s] Found inbound call SID: %s (direction=%s)",
                        self.scenario["id"], call.sid, call.direction,
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
        Build the complete TwiML script for the outbound call.

        Strategy:
          1. Pause to let Susie answer and deliver her greeting
          2. Play patient's first response
          3. Pause to let Susie reply
          4. Play patient's second response
          ... repeat for all scripted responses
          5. Pause briefly then hang up

        This works with ALL Susie pipeline modes (legacy, Realtime, Media Streams)
        because we don't try to capture Susie's audio via Gather — we read
        directly from Susie's Redis session after the call.
        """
        responses = self.scenario.get("responses", [])
        lines = ['<?xml version="1.0" encoding="UTF-8"?>', "<Response>"]

        # Wait for Susie's greeting
        lines.append(f"  <Pause length=\"{GREETING_WAIT_SECONDS}\"/>")

        for i, response in enumerate(responses):
            text = response if isinstance(response, str) else response.get("text", "")
            if not text.strip():
                # Silence turn — just pause
                lines.append(f"  <Pause length=\"{TURN_WAIT_SECONDS}\"/>")
                continue

            audio_filename = self._audio_filename(i)
            audio_path = RESULTS_DIR / audio_filename

            # Play patient response — use ElevenLabs audio if available,
            # otherwise fall back to Twilio Polly TTS so the call still works
            # even when ElevenLabs is unavailable or the API key is invalid.
            if audio_path.exists():
                audio_url = f"{self._webhook_url}/audio/{audio_filename}"
                lines.append(f"  <Play>{audio_url}</Play>")
            else:
                safe_text = (
                    text.replace("&", "&amp;")
                        .replace("<", "&lt;")
                        .replace(">", "&gt;")
                        .replace("'", "&apos;")
                        .replace('"', "&quot;")
                )
                lines.append(f'  <Say voice="Polly.Amy">{safe_text}</Say>')

            # Wait for Susie to reply (except after last response)
            if i < len(responses) - 1:
                lines.append(f"  <Pause length=\"{TURN_WAIT_SECONDS}\"/>")

        # Wait for Susie to respond to the last patient turn before hanging up
        lines.append(f"  <Pause length=\"{TURN_WAIT_SECONDS}\"/>")
        lines.append("  <Hangup/>")
        lines.append("</Response>")
        return "\n".join(lines)

    def _twiml_hangup(self) -> str:
        return (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            "<Response>\n"
            "  <Hangup/>\n"
            "</Response>"
        )

    # ── Audio generation ─────────────────────────────────────────────────────

    def _audio_filename(self, turn_index: int) -> str:
        """Return the audio filename for a given turn index."""
        turn_label = f"{self.scenario['id'].replace('.', '_')}_{turn_index}"
        return f"audio_{turn_label}.mp3"

    async def _pre_generate_audio(self) -> None:
        """Pre-generate ElevenLabs TTS audio for all patient responses."""
        responses = self.scenario.get("responses", [])
        for i, response in enumerate(responses):
            text = response if isinstance(response, str) else response.get("text", "")
            if not text.strip():
                continue
            try:
                await self._generate_audio_for_turn(text, i)
            except Exception as exc:
                logger.warning(
                    "[%s] Failed to generate audio for turn %d: %r — will use <Say> fallback",
                    self.scenario["id"], i, exc,
                )
            # Always record what the test intends to say, regardless of whether
            # ElevenLabs succeeded — the <Say> fallback will still speak the text.
            self.test_said.append({
                "turn": i,
                "text": text,
                "timestamp": time.time(),
            })

    async def _generate_audio_for_turn(self, text: str, turn_index: int) -> str:
        """Generate speech via ElevenLabs, save to results/, return local path."""
        audio_filename = self._audio_filename(turn_index)
        audio_path = RESULTS_DIR / audio_filename

        last_exc = None
        for attempt in range(3):
            try:
                async with httpx.AsyncClient(timeout=30) as client:
                    response = await client.post(
                        f"https://api.elevenlabs.io/v1/text-to-speech/"
                        f"{ELEVENLABS_PATIENT_VOICE_ID}",
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
                    response.raise_for_status()
                    audio_path.write_bytes(response.content)
                    self._audio_files[turn_index] = audio_path
                    logger.info(
                        "[%s] Generated audio for turn %d: %s",
                        self.scenario["id"], turn_index, audio_filename,
                    )
                    return str(audio_path)
            except Exception as exc:
                last_exc = exc
                logger.warning(
                    "[%s] ElevenLabs attempt %d/3 failed: %r",
                    self.scenario["id"], attempt + 1, exc,
                )
                if attempt < 2:
                    await asyncio.sleep(2)

        raise last_exc

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
                # Retry a few times — session may not be saved immediately
                for attempt in range(6):
                    resp = await http.get(url)
                    data = resp.json()

                    if data.get("ok"):
                        history = data.get("conversation_history", [])
                        turns_data = data.get("turns", [])

                        logger.info(
                            "[%s] Session retrieved: %d history entries, %d turns",
                            self.scenario["id"], len(history), len(turns_data),
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
                        return

                    error = data.get("error", "unknown")
                    logger.info(
                        "[%s] Session not ready yet (attempt %d/6): %s",
                        self.scenario["id"], attempt + 1, error,
                    )
                    if attempt < 5:
                        await asyncio.sleep(5)

                logger.warning(
                    "[%s] Session not found after 6 attempts",
                    self.scenario["id"],
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

        # Compute max silence gap between consecutive turns (used by no_dead_air check)
        all_ts = sorted(
            [t["timestamp"] for t in self.susie_said]
            + [t["timestamp"] for t in self.test_said]
        )
        max_gap = 0.0
        for i in range(1, len(all_ts)):
            gap = all_ts[i] - all_ts[i - 1]
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
        }
