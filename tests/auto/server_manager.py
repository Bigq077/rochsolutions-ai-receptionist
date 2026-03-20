"""
server_manager.py — Shared FastAPI + ngrok server for the whole test run.

Instead of creating a new ngrok tunnel and uvicorn instance for every scenario
(which caused cascade failures when ngrok crashed), we start ONE server at the
beginning of the run and keep it alive throughout.

Each CallRunner simply sets `shared_server.current_runner = self` before the
call starts so the route handlers know which runner to delegate to.

Architecture (WebSocket-compatible):
  The /twiml/start endpoint now returns a pre-built TwiML script that plays
  all patient responses with timed pauses — no Gather-based turn detection.
  This works with ALL Susie pipeline modes (legacy, Realtime, Media Streams).
  Speech capture is done via Twilio recording + AssemblyAI transcription.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

import httpx
import uvicorn
from fastapi import FastAPI, Request
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
        for attempt in range(3):
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
                if attempt < 2:
                    try:
                        ngrok.kill()
                    except Exception:
                        pass
                    await asyncio.sleep(5)
        raise RuntimeError("ngrok failed to connect after 3 attempts")

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
            logger.info("[%s] Call status: %s", runner.scenario["id"] if runner else "?", status)
            if runner and status in ("completed", "failed", "busy", "no-answer", "canceled"):
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

        return app
