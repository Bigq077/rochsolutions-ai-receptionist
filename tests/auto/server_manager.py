"""
server_manager.py — Shared FastAPI + ngrok server for the whole test run.

Instead of creating a new ngrok tunnel and uvicorn instance for every scenario
(which caused cascade failures when ngrok crashed), we start ONE server at the
beginning of the run and keep it alive throughout.

Each CallRunner simply sets `shared_server.current_runner = self` before the
call starts so the route handlers know which runner to delegate to.

Architecture (bidirectional Media Streams injection):
  /twiml/start returns <Connect><Stream url="wss://.../patient-ws"/> TwiML.
  The /patient-ws WebSocket endpoint injects G.711 µ-law audio from the test
  caller's direction — Twilio delivers it as inbound caller audio to Susie's
  Media Streams pipeline.  This is the only approach that gets audio into
  Susie's inbound track; <Say>/<Play> verbs only affect the downlink (outbound
  to caller's earpiece) and are invisible to Susie's inbound stream.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from typing import TYPE_CHECKING

import httpx
import uvicorn
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from pyngrok import ngrok

from .config import RESULTS_DIR

if TYPE_CHECKING:
    from .call_runner import CallRunner

logger = logging.getLogger(__name__)

_HANGUP_XML = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    "<Response>\n  <Hangup/>\n</Response>"
)


class SharedServer:
    """
    Long-lived FastAPI + ngrok server shared across all test scenarios.

    Lifecycle:
        server = SharedServer()
        await server.start()          # once, before any tests
        ...
        server.current_runner = runner
        await runner.run_call()       # no server setup in here
        ...
        await server.stop()           # once, after all tests
    """

    def __init__(self):
        self.current_runner: "CallRunner | None" = None
        self.webhook_url: str = ""
        self._server: uvicorn.Server | None = None
        self._server_task: asyncio.Task | None = None
        self._ngrok_tunnel = None
        self._ngrok_port: int = 0

    # ── lifecycle ────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Start uvicorn + ngrok. Call once before running any scenarios."""
        app = self._build_app()

        config = uvicorn.Config(app, host="0.0.0.0", port=0, log_level="warning")
        self._server = uvicorn.Server(config)
        self._server_task = asyncio.create_task(self._server.serve())

        # Wait for uvicorn to bind a port
        for _ in range(40):
            if self._server.started:
                break
            await asyncio.sleep(0.1)

        self._ngrok_port = (
            self._server.servers[0].sockets[0].getsockname()[1]
        )
        logger.info("[server] uvicorn bound on port %d", self._ngrok_port)

        await self._connect_ngrok()

    async def stop(self) -> None:
        """Shut down ngrok and uvicorn."""
        try:
            if self._ngrok_tunnel:
                ngrok.disconnect(self._ngrok_tunnel.public_url)
        except Exception:
            pass
        try:
            ngrok.kill()
        except Exception:
            pass
        try:
            if self._server:
                self._server.should_exit = True
                await asyncio.sleep(0.5)
        except Exception:
            pass
        if self._server_task:
            self._server_task.cancel()

    async def ensure_tunnel_alive(self) -> bool:
        """
        Check that the ngrok tunnel is still alive.
        If it has died, restart it.  Returns True if the tunnel is OK.
        """
        try:
            async with httpx.AsyncClient(timeout=5) as hc:
                resp = await hc.get("http://127.0.0.1:4040/api/tunnels")
            tunnels = resp.json().get("tunnels", [])
            urls = {
                t.get("public_url", "").replace("https://", "http://")
                for t in tunnels
            }
            our_http = self.webhook_url.replace("https://", "http://")
            if our_http in urls:
                return True
        except Exception:
            pass

        # Tunnel is gone — try to restart it
        logger.warning("[server] ngrok tunnel gone — restarting")
        try:
            ngrok.kill()
        except Exception:
            pass
        await asyncio.sleep(3)
        try:
            await self._connect_ngrok()
            logger.info("[server] ngrok restarted — new URL: %s", self.webhook_url)
            return True
        except Exception as exc:
            logger.error("[server] ngrok restart failed: %r", exc)
            return False

    # ── internal ─────────────────────────────────────────────────────────────

    async def _connect_ngrok(self) -> None:
        """Connect (or reconnect) the ngrok tunnel."""
        # Kill any leftover ngrok process from a previous run before starting.
        # This prevents ERR_NGROK_334 ("endpoint already online") when the
        # preflight or a prior run left a stale tunnel attached to the domain.
        try:
            ngrok.kill()
        except Exception:
            pass
        await asyncio.sleep(2)

        for attempt in range(8):  # up to ~3 minutes total
            try:
                self._ngrok_tunnel = ngrok.connect(self._ngrok_port, "http")
                self.webhook_url = self._ngrok_tunnel.public_url.replace(
                    "http://", "https://"
                )
                logger.info(
                    "[server] ngrok tunnel up: %s (attempt %d)",
                    self.webhook_url, attempt + 1,
                )
                return
            except Exception as exc:
                logger.warning(
                    "[server] ngrok connect attempt %d failed: %r", attempt + 1, exc
                )
                try:
                    ngrok.kill()
                except Exception:
                    pass
                # ERR_NGROK_334 means the static domain is still registered in the
                # ngrok cloud from a previous crashed run.  The cloud takes ~30s to
                # detect the dead agent and release the domain — wait accordingly.
                wait = 30 if "ERR_NGROK_334" in str(exc) else 5
                logger.info("[server] waiting %ds before retry %d…", wait, attempt + 2)
                await asyncio.sleep(wait)
        raise RuntimeError("ngrok failed to connect after 8 attempts")

    def _build_app(self) -> FastAPI:
        """Build the FastAPI app whose handlers delegate to self.current_runner."""
        app = FastAPI()

        # Serve ElevenLabs audio files generated for each turn
        app.mount(
            "/audio",
            StaticFiles(directory=str(RESULTS_DIR)),
            name="audio",
        )

        server = self  # closure

        @app.post("/twiml/start")
        async def twiml_start(request: Request):
            """
            Return the pre-built TwiML script for this scenario.

            The script plays all patient responses with timed pauses —
            no Gather-based turn detection. Works with all Susie pipeline modes.
            """
            runner = server.current_runner
            if runner is None:
                return Response(content=_HANGUP_XML, media_type="text/xml")
            logger.info("[%s] Call connected — serving pre-built TwiML", runner.scenario["id"])
            twiml = runner.build_start_twiml()
            logger.debug("[%s] TwiML:\n%s", runner.scenario["id"], twiml)
            return Response(content=twiml, media_type="text/xml")

        @app.post("/status")
        async def call_status(request: Request):
            runner = server.current_runner
            form = await request.form()
            status = form.get("CallStatus", "")
            callback_sid = form.get("CallSid", "")
            logger.info(
                "[%s] Call status: %s sid=%s",
                runner.scenario["id"] if runner else "?", status, callback_sid,
            )
            if runner and status in ("completed", "failed", "busy", "no-answer", "canceled"):
                # Guard: only end the current test if the callback SID matches.
                # Stale callbacks from previous test runs (retried by Twilio after
                # the ngrok tunnel was restarted) must not prematurely cancel the
                # inject_task for the NEW test call.
                if callback_sid and runner.call_sid and callback_sid != runner.call_sid:
                    logger.warning(
                        "[%s] Ignoring stale status callback sid=%s (current=%s)",
                        runner.scenario["id"], callback_sid, runner.call_sid,
                    )
                else:
                    runner._end_call(status)
            return {"ok": True}

        @app.post("/recording")
        async def recording_status(request: Request):
            runner = server.current_runner
            form = await request.form()
            recording_url = form.get("RecordingUrl", "")
            recording_sid = form.get("RecordingSid", "")
            recording_status = form.get("RecordingStatus", "")
            logger.info(
                "[%s] Recording callback: status=%s sid=%s url=%s",
                runner.scenario["id"] if runner else "?",
                recording_status, recording_sid, recording_url,
            )
            if runner:
                if recording_url:
                    runner._recording_url = recording_url + ".mp3"
                if recording_sid:
                    runner._recording_sid = recording_sid
            return {"ok": True}

        @app.websocket("/patient-ws")
        async def patient_ws(websocket: WebSocket):
            """
            Bidirectional Media Streams endpoint for the TEST CALLER leg.

            When the test caller's TwiML contains <Connect><Stream url="wss://.../patient-ws"/>,
            Twilio connects here.  We inject the patient's pre-generated G.711 µ-law audio
            frames at real-time rate (160 bytes = 20 ms per frame).  Twilio delivers these
            frames as the caller's inbound audio to Susie's Media Streams pipeline —
            which is the ONLY way to get audio into Susie's inbound track.

            Timeline:
              1. Accept WebSocket + wait for Twilio "start" event (contains stream_sid)
              2. Sleep GREETING_WAIT_SECONDS — let Susie deliver her greeting
              3. For each patient response:
                   a. Send mulaw frames at 20 ms/frame (real-time rate)
                   b. Sleep TURN_WAIT_SECONDS — let Susie process and reply
              4. Close WebSocket → Twilio moves to <Hangup/> → call ends
            """
            from .call_runner import GREETING_WAIT_SECONDS, TURN_WAIT_SECONDS

            await websocket.accept()
            runner = server.current_runner
            if runner is None:
                logger.warning("[patient-ws] No active runner — closing immediately")
                await websocket.close()
                return

            scenario_id = runner.scenario.get("id", "?")
            stream_sid: str | None = None

            # ── Wait for Twilio "connected" + "start" events ─────────────────
            try:
                deadline = time.time() + 30
                while time.time() < deadline:
                    remaining = deadline - time.time()
                    raw = await asyncio.wait_for(
                        websocket.receive_text(), timeout=max(remaining, 0.1)
                    )
                    msg = json.loads(raw)
                    event = msg.get("event")
                    if event == "connected":
                        logger.info("[%s] patient-ws: Twilio connected", scenario_id)
                    elif event == "start":
                        stream_sid = msg["start"]["streamSid"]
                        logger.info(
                            "[%s] patient-ws: stream started, sid=%s",
                            scenario_id, stream_sid,
                        )
                        break
                    elif event == "stop":
                        logger.info("[%s] patient-ws: got early stop", scenario_id)
                        return
                else:
                    logger.warning(
                        "[%s] patient-ws: timed out waiting for start event", scenario_id
                    )
                    return
            except Exception as exc:
                logger.warning("[%s] patient-ws: error waiting for start: %r", scenario_id, exc)
                try:
                    await websocket.close()
                except Exception:
                    pass
                return

            # ── Consume any incoming Twilio media/stop events in background ──
            # (We don't use them, but draining prevents back-pressure disconnects.)
            async def _drain_incoming():
                try:
                    while True:
                        raw = await asyncio.wait_for(websocket.receive_text(), timeout=300)
                        msg = json.loads(raw)
                        if msg.get("event") == "stop":
                            break
                except Exception:
                    pass

            drain_task = asyncio.create_task(_drain_incoming())

            try:
                # ── 1. Wait for Susie's greeting ──────────────────────────────
                logger.info(
                    "[%s] patient-ws: waiting %ds for Susie's greeting",
                    scenario_id, GREETING_WAIT_SECONDS,
                )
                await asyncio.sleep(GREETING_WAIT_SECONDS)

                # ── 2. Inject patient responses one by one ────────────────────
                responses = runner.scenario.get("responses", [])
                FRAME_SIZE = 160        # 20 ms of 8 kHz µ-law
                FRAME_SLEEP = 0.020     # 20 ms inter-frame gap

                for i, response in enumerate(responses):
                    text = response if isinstance(response, str) else response.get("text", "")
                    mulaw_bytes = runner._audio_mulaw.get(i)

                    if mulaw_bytes:
                        duration_s = len(mulaw_bytes) / 8000
                        logger.info(
                            "[%s] patient-ws: injecting turn %d (%d bytes = %.1fs): %.40s…",
                            scenario_id, i, len(mulaw_bytes), duration_s, text,
                        )
                        # Send audio frames at real-time rate
                        for offset in range(0, len(mulaw_bytes), FRAME_SIZE):
                            frame = mulaw_bytes[offset: offset + FRAME_SIZE]
                            # Pad last frame with silence (0xFF = µ-law zero)
                            if len(frame) < FRAME_SIZE:
                                frame = frame + bytes([0xFF] * (FRAME_SIZE - len(frame)))
                            payload = base64.b64encode(frame).decode()
                            try:
                                await websocket.send_text(json.dumps({
                                    "event": "media",
                                    "streamSid": stream_sid,
                                    "media": {"payload": payload},
                                }))
                            except WebSocketDisconnect:
                                logger.info(
                                    "[%s] patient-ws: Twilio disconnected during turn %d",
                                    scenario_id, i,
                                )
                                return
                            await asyncio.sleep(FRAME_SLEEP)
                    elif text.strip():
                        # No audio generated — pause for estimated speaking duration
                        words = len(text.split())
                        est_s = max(2.0, words / 2.5)
                        logger.warning(
                            "[%s] patient-ws: no audio for turn %d — pausing %.1fs",
                            scenario_id, i, est_s,
                        )
                        await asyncio.sleep(est_s)
                    # else: silence turn — nothing to inject

                    # Wait for Susie to process and respond
                    logger.info(
                        "[%s] patient-ws: waiting %ds for Susie's response to turn %d",
                        scenario_id, TURN_WAIT_SECONDS, i,
                    )
                    await asyncio.sleep(TURN_WAIT_SECONDS)

                logger.info("[%s] patient-ws: all responses injected — closing", scenario_id)

            finally:
                drain_task.cancel()
                try:
                    await websocket.close()
                except Exception:
                    pass

        return app
