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

        # Monitor ngrok tunnel health in background.
        # If ngrok dies mid-call (heartbeat timeout / network blip) this
        # detects it within ~10 s and aborts the call rather than waiting
        # the full MAX_CALL_DURATION_SECONDS.
        monitor_task = asyncio.create_task(self._monitor_ngrok())

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
        finally:
            monitor_task.cancel()
            try:
                await monitor_task
            except (asyncio.CancelledError, Exception):
                pass

        self.call_end_time = time.time()
        await self._cleanup()
        return self._build_result()

    async def _monitor_ngrok(self):
        """
        Poll the local ngrok API every 10 s to verify the tunnel is still alive.

        If the ngrok process crashes (e.g. heartbeat timeout, DNS blip) the
        tunnel disappears from the API.  We detect this quickly and end the
        current call so the runner moves on to the next scenario instead of
        waiting the full MAX_CALL_DURATION_SECONDS.
        """
        await asyncio.sleep(10)  # give call time to connect first
        while not self.call_complete.is_set():
            try:
                async with httpx.AsyncClient(timeout=5) as hc:
                    resp = await hc.get("http://127.0.0.1:4040/api/tunnels")
                tunnels = resp.json().get("tunnels", [])
                urls = {
                    t.get("public_url", "").replace("https://", "http://")
                    for t in tunnels
                }
                our_http = self._webhook_url.replace("https://", "http://")
                if our_http not in urls:
                    logger.warning(
                        f"[{self.scenario['id']}] ngrok tunnel has disappeared "
                        f"— aborting call early"
                    )
                    self._end_call("ngrok_died")
                    return
            except asyncio.CancelledError:
                return
            except Exception:
                # ngrok API unreachable → process has died
                logger.warning(
                    f"[{self.scenario['id']}] ngrok API unreachable "
                    f"— aborting call early"
                )
                self._end_call("ngrok_died")
                return
            await asyncio.sleep(10)

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
            # 30s timeout: Susie's greeting (ElevenLabs TTS) can take ~10-12 seconds
            # to generate and play; 15s was too tight.
            return Response(
                content=runner._twiml_listen(timeout=30),
                media_type="text/xml",
            )

        # Phrases that indicate Susie is re-asking (silence handler fired).
        # When detected we step back so the same scripted response is replayed.
        _REASK_PHRASES = (
            "didn't quite catch",
            "catch that",
            "sorry about that",
            "i'm having a little trouble",
            "trouble hearing",
        )

        @app.post("/twiml/gather")
        async def twiml_gather(request: Request):
            """Twilio calls this after each speech-gather completes."""
            form = await request.form()
            susie_speech = form.get("SpeechResult", "").strip()
            susie_confidence = float(form.get("Confidence", 0.0))

            # ── Empty gather: Twilio timed out waiting for speech ──────────────────
            # This happens if Susie hasn't spoken yet (her greeting is still
            # generating/playing) or if there was genuine dead air.  Re-issue a
            # fresh listen rather than treating it as a scripted-response turn.
            if not susie_speech:
                runner._empty_gather_count = getattr(runner, "_empty_gather_count", 0) + 1
                logger.info(
                    f"[{runner.scenario['id']}] Empty gather #{runner._empty_gather_count} "
                    f"— re-listening"
                )
                if runner._empty_gather_count >= 5:
                    logger.warning(
                        f"[{runner.scenario['id']}] Too many empty gathers — ending call"
                    )
                    runner._end_call("timeout_no_speech")
                    return Response(content=runner._twiml_hangup(), media_type="text/xml")
                return Response(
                    content=runner._twiml_listen(timeout=30),
                    media_type="text/xml",
                )

            # Reset empty-gather counter on any real speech
            runner._empty_gather_count = 0

            # ── Re-ask detection ───────────────────────────────────────────────────
            # If Susie re-asked because she didn't hear the caller's REAL response,
            # step back so the same scripted response is replayed.
            # CRITICAL: do NOT step back if the previous turn was intentional silence
            # (empty string response) — silence scenarios depend on advancing past
            # each silence turn naturally when Susie re-asks.
            susie_lower = susie_speech.lower()
            is_reask = any(p in susie_lower for p in _REASK_PHRASES)
            if is_reask and runner.current_turn > 0:
                last_said = (
                    runner.test_said[-1]["text"] if runner.test_said else ""
                )
                if last_said.strip():
                    # Previous turn was a real spoken response — repeat it
                    logger.info(
                        f"[{runner.scenario['id']}] Re-ask detected at turn {runner.current_turn} "
                        f"— stepping back to repeat turn {runner.current_turn - 1}"
                    )
                    runner.current_turn -= 1
                else:
                    logger.info(
                        f"[{runner.scenario['id']}] Re-ask detected but previous turn was silence "
                        f"— advancing normally to turn {runner.current_turn}"
                    )

            logger.info(
                f"[{runner.scenario['id']}] Turn {runner.current_turn} — "
                f"Susie said: '{susie_speech[:80]}' (conf={susie_confidence:.2f})"
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

            # Empty string means the test caller is silent this turn —
            # just listen without playing anything so Susie's SilenceHandler fires.
            if not next_response.strip():
                logger.info(
                    f"[{runner.scenario['id']}] Turn {runner.current_turn - 1} — "
                    f"silence turn (no audio played)"
                )
                return Response(
                    content=runner._twiml_listen(timeout=30),
                    media_type="text/xml",
                )

            # Generate TTS audio and play it, then listen again.
            # Fall back to Twilio's built-in <Say> if ElevenLabs fails so the call
            # never crashes — Twilio's voice is less natural but keeps the flow alive.
            try:
                audio_url = await runner._generate_audio(next_response)
                return Response(
                    content=runner._twiml_play_then_listen(audio_url),
                    media_type="text/xml",
                )
            except Exception as exc:
                logger.warning(
                    f"[{runner.scenario['id']}] ElevenLabs failed — using Twilio <Say> "
                    f"fallback: {exc!r}"
                )
                return Response(
                    content=runner._twiml_say_then_listen(next_response),
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

        # Expose via ngrok — if the ngrok process is dead (e.g. after a
        # prior network blip) kill it and restart before connecting.
        for _attempt in range(2):
            try:
                self._ngrok_tunnel = ngrok.connect(port, "http")
                break
            except Exception as _err:
                if _attempt == 0:
                    logger.warning(
                        f"[{self.scenario['id']}] ngrok connect failed "
                        f"({_err!r}) — killing process and retrying"
                    )
                    try:
                        ngrok.kill()
                    except Exception:
                        pass
                    await asyncio.sleep(5)
                else:
                    raise

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

    def _twiml_say_then_listen(self, text: str) -> str:
        """
        Fallback when ElevenLabs is unavailable: use Twilio's built-in <Say>
        verb with a British voice, then listen for Susie's reply.
        """
        # Escape XML special characters
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
            f'          speechTimeout="4"\n'
            f'          enhanced="true"\n'
            f'          language="en-GB">\n'
            f"  </Gather>\n"
            f"</Response>"
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
