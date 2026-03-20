"""
server_manager.py — Shared FastAPI + ngrok server for the whole test run.

Instead of creating a new ngrok tunnel and uvicorn instance for every scenario
(which caused cascade failures when ngrok crashed), we start ONE server at the
beginning of the run and keep it alive throughout.

Each CallRunner simply sets `shared_server.current_runner = self` before the
call starts so the route handlers know which runner to delegate to.
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

# Silence re-ask phrases — Susie uses these when she couldn't hear the caller.
# When detected we step back one turn so the same scripted response is replayed.
REASK_PHRASES = (
    "didn't quite catch",
    "catch that",
    "sorry about that",
    "i'm having a little trouble",
    "trouble hearing",
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
            runner = server.current_runner
            if runner is None:
                return Response(content=_HANGUP_XML, media_type="text/xml")
            logger.info("[%s] Call connected", runner.scenario["id"])
            return Response(
                content=runner._twiml_listen(timeout=60),
                media_type="text/xml",
            )

        @app.post("/twiml/gather")
        async def twiml_gather(request: Request):
            runner = server.current_runner
            if runner is None:
                return Response(content=_HANGUP_XML, media_type="text/xml")
            return await _handle_gather(runner, request, server.webhook_url)

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
            if runner:
                runner._recording_url = recording_url
            return {"ok": True}

        return app


# ── Gather handler (shared logic used by the shared server) ───────────────────

async def _handle_gather(
    runner: "CallRunner",
    request: Request,
    webhook_url: str,
) -> Response:
    """Process one Twilio gather callback.  Returns TwiML response."""
    from .call_runner import MAX_TURNS_PER_CALL  # avoid circular import

    form = await request.form()
    susie_speech = form.get("SpeechResult", "").strip()
    susie_confidence = float(form.get("Confidence", 0.0))

    # ── Empty gather: no speech detected yet ──────────────────────────────
    if not susie_speech:
        runner._empty_gather_count = getattr(runner, "_empty_gather_count", 0) + 1
        logger.info(
            "[%s] Empty gather #%d — re-listening",
            runner.scenario["id"], runner._empty_gather_count,
        )
        if runner._empty_gather_count >= 8:
            logger.warning("[%s] Too many empty gathers — ending call", runner.scenario["id"])
            runner._end_call("timeout_no_speech")
            return Response(content=runner._twiml_hangup(), media_type="text/xml")
        # Give extra time on first few empty gathers — Susie may still be waking up
        listen_timeout = 60 if runner._empty_gather_count <= 2 else 45
        return Response(
            content=runner._twiml_listen(timeout=listen_timeout),
            media_type="text/xml",
        )

    runner._empty_gather_count = 0

    # ── Re-ask detection ──────────────────────────────────────────────────
    susie_lower = susie_speech.lower()
    is_reask = any(p in susie_lower for p in REASK_PHRASES)
    if is_reask and runner.current_turn > 0:
        last_said = runner.test_said[-1]["text"] if runner.test_said else ""
        if last_said.strip():
            logger.info(
                "[%s] Re-ask detected — stepping back to turn %d",
                runner.scenario["id"], runner.current_turn - 1,
            )
            runner.current_turn -= 1
        else:
            logger.info(
                "[%s] Re-ask detected but previous was silence — advancing normally",
                runner.scenario["id"],
            )

    logger.info(
        "[%s] Turn %d — Susie: '%s' (conf=%.2f)",
        runner.scenario["id"], runner.current_turn,
        susie_speech[:80], susie_confidence,
    )

    runner.susie_said.append({
        "turn": runner.current_turn,
        "text": susie_speech,
        "confidence": susie_confidence,
        "timestamp": time.time(),
    })

    # Guard against runaway calls
    if runner.current_turn >= MAX_TURNS_PER_CALL:
        logger.warning("[%s] Max turns reached — hanging up", runner.scenario["id"])
        runner._end_call("max_turns")
        return Response(content=runner._twiml_hangup(), media_type="text/xml")

    # Get next scripted response
    next_response = runner._get_next_response(susie_speech)
    if next_response is None:
        logger.info("[%s] Scenario complete — hanging up", runner.scenario["id"])
        runner._end_call("complete")
        return Response(content=runner._twiml_hangup(), media_type="text/xml")

    logger.info("[%s] Turn %d — Saying: '%s'", runner.scenario["id"], runner.current_turn, next_response)

    runner.test_said.append({
        "turn": runner.current_turn,
        "text": next_response,
        "timestamp": time.time(),
    })
    runner.current_turn += 1

    # Silence turn — just listen (SilenceHandler will fire on Susie's end)
    if not next_response.strip():
        logger.info("[%s] Silence turn", runner.scenario["id"])
        return Response(
            content=runner._twiml_listen(timeout=30),
            media_type="text/xml",
        )

    # Generate TTS and play it, with <Say> fallback if ElevenLabs fails
    try:
        audio_url = await runner._generate_audio(next_response, webhook_url)
        return Response(
            content=runner._twiml_play_then_listen(audio_url),
            media_type="text/xml",
        )
    except Exception as exc:
        logger.warning("[%s] ElevenLabs failed — <Say> fallback: %r", runner.scenario["id"], exc)
        return Response(
            content=runner._twiml_say_then_listen(next_response),
            media_type="text/xml",
        )
