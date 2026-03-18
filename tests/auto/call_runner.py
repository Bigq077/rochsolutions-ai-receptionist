import asyncio
import logging
import time
from datetime import datetime

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from pyngrok import ngrok
from twilio.rest import Client

from .config import (
    ANTHROPIC_API_KEY,
    ELEVENLABS_API_KEY,
    ELEVENLABS_PATIENT_VOICE_ID,
    MAX_CALL_DURATION_SECONDS,
    MAX_TURNS_PER_CALL,
    RESULTS_DIR,
    SUSIE_NUMBER,
    TWILIO_ACCOUNT_SID,
    TWILIO_AUTH_TOKEN,
    TWILIO_TEST_NUMBER,
)

logger = logging.getLogger(__name__)


class CallRunner:
    def __init__(self, scenario: dict):
        self.scenario = scenario
        self.call_sid = None
        self.turns = []
        self.current_turn = 0
        self.call_complete = asyncio.Event()
        self.susie_said = []   # what Susie said each turn
        self.test_said = []    # what test caller said
        self.call_start_time = None
        self.call_end_time = None
        self.end_reason = "unknown"
        self._webhook_url = None
        self._ngrok_tunnel = None
        self._server = None
        self._audio_files = {}  # turn -> local path

    async def run(self) -> dict:
        """Make the call, run the scenario, return full result dict."""
        self.call_start_time = time.time()
        self.call_complete.clear()

        # Start local webhook server and expose via ngrok
        await self._start_webhook_server()

        logger.info(
            f"[{self.scenario['id']}] Webhook ready at {self._webhook_url}"
        )

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
        logger.info(f"[{self.scenario['id']}] Call started: {self.call_sid}")

        # Wait for call to complete or timeout
        try:
            await asyncio.wait_for(
                self.call_complete.wait(),
                timeout=MAX_CALL_DURATION_SECONDS,
            )
        except asyncio.TimeoutError:
            logger.warning(
                f"[{self.scenario['id']}] Call timed out after "
                f"{MAX_CALL_DURATION_SECONDS}s"
            )
            self._end_call("timeout")
            try:
                client.calls(self.call_sid).update(status="completed")
            except Exception:
                pass

        self.call_end_time = time.time()
        await self._cleanup()
        return self._build_result()

    async def _start_webhook_server(self):
        """Start FastAPI + ngrok, store public URL."""
        app = FastAPI()

        # Serve audio files the test caller generated
        app.mount(
            "/audio",
            StaticFiles(directory=str(RESULTS_DIR)),
            name="audio",
        )

        runner = self  # closure reference

        @app.post("/twiml/start")
        async def twiml_start(request: Request):
            """Twilio calls this when the call connects. Listen for Susie's greeting."""
            logger.info(f"[{runner.scenario['id']}] Call connected — listening for greeting")
            return Response(
                content=runner._twiml_listen(timeout=15),
                media_type="text/xml",
            )

        @app.post("/twiml/gather")
        async def twiml_gather(request: Request):
            """Twilio calls this after each speech-gather completes."""
            form = await request.form()
            susie_speech = form.get("SpeechResult", "")
            susie_confidence = float(form.get("Confidence", 0.0))

            logger.info(
                f"[{runner.scenario['id']}] Turn {runner.current_turn} — "
                f"Susie said: '{susie_speech}' (conf={susie_confidence:.2f})"
            )

            runner.susie_said.append(
                {
                    "turn": runner.current_turn,
                    "text": susie_speech,
                    "confidence": susie_confidence,
                    "timestamp": time.time(),
                }
            )

            # Guard against runaway calls
            if runner.current_turn >= MAX_TURNS_PER_CALL:
                logger.warning(
                    f"[{runner.scenario['id']}] Max turns reached — hanging up"
                )
                runner._end_call("max_turns")
                return Response(
                    content=runner._twiml_hangup(),
                    media_type="text/xml",
                )

            # Get next scripted response
            next_response = runner._get_next_response(susie_speech)

            if next_response is None:
                logger.info(
                    f"[{runner.scenario['id']}] Scenario complete — hanging up"
                )
                runner._end_call("complete")
                return Response(
                    content=runner._twiml_hangup(),
                    media_type="text/xml",
                )

            logger.info(
                f"[{runner.scenario['id']}] Turn {runner.current_turn} — "
                f"Saying: '{next_response}'"
            )

            runner.test_said.append(
                {
                    "turn": runner.current_turn,
                    "text": next_response,
                    "timestamp": time.time(),
                }
            )
            runner.current_turn += 1

            # Generate TTS audio and play it, then listen again
            audio_url = await runner._generate_audio(next_response)
            return Response(
                content=runner._twiml_play_then_listen(audio_url),
                media_type="text/xml",
            )

        @app.post("/status")
        async def call_status(request: Request):
            form = await request.form()
            status = form.get("CallStatus", "")
            logger.info(
                f"[{runner.scenario['id']}] Call status: {status}"
            )
            if status in ("completed", "failed", "busy", "no-answer", "canceled"):
                runner._end_call(status)
            return {"ok": True}

        @app.post("/recording")
        async def recording_status(request: Request):
            form = await request.form()
            recording_url = form.get("RecordingUrl", "")
            runner._recording_url = recording_url
            logger.info(
                f"[{runner.scenario['id']}] Recording available: {recording_url}"
            )
            return {"ok": True}

        # Pick a random free port and start uvicorn in background
        config = uvicorn.Config(
            app, host="0.0.0.0", port=0, log_level="warning"
        )
        self._server = uvicorn.Server(config)
        server_task = asyncio.create_task(self._server.serve())

        # Wait for uvicorn to actually bind
        for _ in range(40):
            if self._server.started:
                break
            await asyncio.sleep(0.1)

        port = self._server.servers[0].sockets[0].getsockname()[1]
        logger.info(f"[{self.scenario['id']}] Uvicorn listening on port {port}")

        # Expose via ngrok
        self._ngrok_tunnel = ngrok.connect(port, "http")
        self._webhook_url = self._ngrok_tunnel.public_url.replace("http://", "https://")
        self._server_task = server_task

    def _twiml_listen(self, timeout: int = 15) -> str:
        return (
            f'<?xml version="1.0" encoding="UTF-8"?>\n'
            f"<Response>\n"
            f'  <Gather input="speech" timeout="{timeout}"\n'
            f'          action="{self._webhook_url}/twiml/gather"\n'
            f'          speechTimeout="4"\n'
            f'          enhanced="true"\n'
            f'          language="en-GB">\n'
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
            f'          speechTimeout="4"\n'
            f'          enhanced="true"\n'
            f'          language="en-GB">\n'
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

    async def _generate_audio(self, text: str) -> str:
        """
        Generate speech via ElevenLabs, save to results/, return public URL.
        Retries up to 3 times on network errors.
        """
        import asyncio as _asyncio

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
                    return f"{self._webhook_url}/audio/{audio_filename}"
            except Exception as exc:
                last_exc = exc
                logger.warning(
                    f"[{self.scenario['id']}] ElevenLabs attempt {attempt + 1}/3 "
                    f"failed: {exc!r} — retrying in 2s"
                )
                if attempt < 2:
                    await _asyncio.sleep(2)

        raise last_exc

    def _get_next_response(self, susie_text: str) -> str | None:
        """
        Return the next scripted patient response, or None if the scenario
        is done. Supports both a flat list and a callable per-turn selector.
        """
        responses = self.scenario.get("responses", [])

        # Support callable responses (for dynamic scenarios)
        if callable(responses):
            return responses(self.current_turn, susie_text)

        if self.current_turn >= len(responses):
            return None

        entry = responses[self.current_turn]

        # Support dict entries with optional condition
        if isinstance(entry, dict):
            return entry.get("text")

        return entry  # plain string

    def _end_call(self, reason: str):
        if not self.call_complete.is_set():
            self.end_reason = reason
            self.call_complete.set()

    async def _cleanup(self):
        """Shut down ngrok and uvicorn."""
        try:
            if self._ngrok_tunnel:
                ngrok.disconnect(self._ngrok_tunnel.public_url)
        except Exception:
            pass
        try:
            if self._server:
                self._server.should_exit = True
                await asyncio.sleep(0.5)
        except Exception:
            pass

    def _build_result(self) -> dict:
        duration = (
            self.call_end_time - self.call_start_time
            if self.call_end_time and self.call_start_time
            else 0.0
        )
        return {
            "scenario_id": self.scenario["id"],
            "scenario_name": self.scenario["name"],
            "phase": self.scenario.get("phase", "Unknown"),
            "scenario": self.scenario,
            "call_sid": self.call_sid,
            "end_reason": self.end_reason,
            "duration_seconds": round(duration, 2),
            "turns": self.current_turn,
            "susie_said": self.susie_said,
            "test_said": self.test_said,
            "recording_url": getattr(self, "_recording_url", None),
            "timestamp": datetime.utcnow().isoformat(),
        }
