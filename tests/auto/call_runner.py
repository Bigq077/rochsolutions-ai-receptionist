"""
call_runner.py — Makes a single Twilio call and runs a test scenario.

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
    RESULTS_DIR,
    SUSIE_NUMBER,
    TWILIO_ACCOUNT_SID,
    TWILIO_AUTH_TOKEN,
    TWILIO_TEST_NUMBER,
)

logger = logging.getLogger(__name__)

# Exported so server_manager can import it without circular issues
MAX_TURNS_PER_CALL = 20


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
        self._empty_gather_count: int = 0

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
            record=True,
            recording_status_callback=self._webhook_url + "/recording",
            recording_status_callback_method="POST",
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

        # Deregister — no more callbacks for this runner
        self._shared_server.current_runner = None

        return self._build_result()

    # ── TwiML builders ──────────────────────────────────────────────────────

    def _twiml_listen(self, timeout: int = 15) -> str:
        # NOTE: enhanced="true" is intentionally absent.
        # Enhanced STT uses strict voice-activity detection tuned for
        # human-microphone speech.  Susie's audio is TTS played through a
        # telephony chain — by the time it arrives here it has been
        # encoded/decoded several times and enhanced mode silently rejects
        # it as "not speech", giving 0 turns every time.
        # Standard mode is more permissive and works with synthesised audio.
        # language="en" (not "en-GB") accepts all English locale models so
        # Susie's TTS voice is not filtered out by a locale mismatch.
        return (
            f'<?xml version="1.0" encoding="UTF-8"?>\n'
            f"<Response>\n"
            f'  <Gather input="speech" timeout="{timeout}"\n'
            f'          action="{self._webhook_url}/twiml/gather"\n'
            f'          speechTimeout="2"\n'
            f'          language="en">\n'
            f"  </Gather>\n"
            f"</Response>"
        )

    def _twiml_play_then_listen(self, audio_url: str) -> str:
        return (
            f'<?xml version="1.0" encoding="UTF-8"?>\n'
            f"<Response>\n"
            f"  <Play>{audio_url}</Play>\n"
            f'  <Gather input="speech" timeout="25"\n'
            f'          action="{self._webhook_url}/twiml/gather"\n'
            f'          speechTimeout="2"\n'
            f'          language="en">\n'
            f"  </Gather>\n"
            f"</Response>"
        )

    def _twiml_say_then_listen(self, text: str) -> str:
        """Fallback when ElevenLabs is unavailable: use Twilio's <Say>."""
        safe_text = (
            text.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace('"', "&quot;")
        )
        return (
            f'<?xml version="1.0" encoding="UTF-8"?>\n'
            f"<Response>\n"
            f'  <Say voice="Polly.Brian" language="en-GB">{safe_text}</Say>\n'
            f'  <Gather input="speech" timeout="25"\n'
            f'          action="{self._webhook_url}/twiml/gather"\n'
            f'          speechTimeout="2"\n'
            f'          language="en">\n'
            f"  </Gather>\n"
            f"</Response>"
        )

    def _twiml_hangup(self) -> str:
        return (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            "<Response>\n"
            "  <Hangup/>\n"
            "</Response>"
        )

    # ── Audio generation ─────────────────────────────────────────────────────

    async def _generate_audio(self, text: str, webhook_url: str | None = None) -> str:
        """Generate speech via ElevenLabs, save to results/, return public URL."""
        url = webhook_url or self._webhook_url
        turn_label = f"{self.scenario['id'].replace('.', '_')}_{self.current_turn}"
        audio_filename = f"audio_{turn_label}.mp3"
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
                    self._audio_files[self.current_turn] = audio_path
                    return f"{url}/audio/{audio_filename}"
            except Exception as exc:
                last_exc = exc
                logger.warning(
                    "[%s] ElevenLabs attempt %d/3 failed: %r",
                    self.scenario["id"], attempt + 1, exc,
                )
                if attempt < 2:
                    await asyncio.sleep(2)

        raise last_exc

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
            "turns":         self.current_turn,
            "susie_said":    self.susie_said,
            "test_said":     self.test_said,
            "recording_url": self._recording_url,
            "timestamp":     datetime.utcnow().isoformat(),
            "max_gap_seconds": round(max_gap, 2),
        }
