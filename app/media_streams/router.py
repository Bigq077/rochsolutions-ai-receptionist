# app/media_streams/router.py
"""
FastAPI routes for the parallel Media Streams voice pipeline.

Route 1 — TwiML response for incoming test calls:
  POST /ms/incoming
  Returns TwiML that tells Twilio to connect to the Media Streams WebSocket:
    <Response>
      <Connect>
        <Stream url="wss://YOUR_DOMAIN/ms/stream"/>
      </Connect>
    </Response>

  YOUR_DOMAIN is resolved from environment variable RENDER_EXTERNAL_URL.
  Falls back to the Host header if RENDER_EXTERNAL_URL is not set.

  Kill switch: if MEDIA_STREAMS_ENABLED=false, returns a TwiML <Redirect>
  to the existing /twilio/voice route immediately — zero dead air.

Route 2 — Media Streams WebSocket endpoint:
  GET /ms/stream  (WebSocket upgrade)
  Accepts the Twilio WebSocket connection and hands off to WebSocketCallHandler.

  Error handling:
    - If handler.handle() raises before call reaches stable state:
      logs "UNSTABLE CALL", attempts graceful WebSocket close.
    - If WebSocket is already disconnected: silently ignores close errors.
    - If MEDIA_STREAMS_ENABLED=false: closes immediately (1001).

Registration in main.py:
  from app.media_streams.router import router as media_streams_router
  from app.media_streams.config import MEDIA_STREAMS_ENABLED

  if MEDIA_STREAMS_ENABLED:
      app.include_router(media_streams_router)

Both routes use the prefix /ms (set in main.py include_router call).
The existing /twilio/media-stream route (realtime.py) is completely untouched.
"""
from __future__ import annotations

import logging
import os
import traceback

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response

from .config import (
    MEDIA_STREAMS_ENABLED,
    RENDER_EXTERNAL_URL,
    LEGACY_VOICE_URL,
)
from .connection import WebSocketCallHandler

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Helper: build WebSocket URL from domain
# ---------------------------------------------------------------------------

def _build_ws_url(request: Request) -> str:
    """
    Return the wss:// URL for the Media Streams WebSocket endpoint.

    Priority:
      1. RENDER_EXTERNAL_URL env var (set automatically on Render)
      2. Host header from the current request

    Always returns wss:// (never ws://) because Twilio requires TLS.
    """
    domain = RENDER_EXTERNAL_URL.strip().rstrip("/")
    if not domain:
        host = request.headers.get("host", "localhost")
        domain = f"https://{host}"

    # Strip scheme so we can re-add wss://
    domain = domain.replace("https://", "").replace("http://", "")
    return f"wss://{domain}/ms/stream"


# ---------------------------------------------------------------------------
# Route 1: TwiML response for incoming calls
# ---------------------------------------------------------------------------

@router.post("/ms/incoming")
async def ms_incoming(request: Request) -> Response:
    """
    Twilio calls this when a call arrives on the test number.

    Returns TwiML that connects the call to the Media Streams WebSocket.

    If MEDIA_STREAMS_ENABLED=false (kill switch), returns a TwiML <Redirect>
    to the existing /twilio/voice route so the caller is handled by the
    production system with no dead air.

    If anything raises during TwiML construction, returns the redirect as a
    safe fallback — never leave the caller with a silent empty response.
    """
    if not MEDIA_STREAMS_ENABLED:
        logger.info("[ms_router] MEDIA_STREAMS_ENABLED=false — redirecting to legacy system")
        twiml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            "<Response>"
            f"<Redirect>{LEGACY_VOICE_URL}</Redirect>"
            "</Response>"
        )
        return Response(content=twiml, media_type="application/xml")

    try:
        ws_url = _build_ws_url(request)
        logger.info("[ms_router] incoming call — stream URL: %s", ws_url)

        twiml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            "<Response>"
            "<Connect>"
            f'<Stream url="{ws_url}"/>'
            "</Connect>"
            "</Response>"
        )
        return Response(content=twiml, media_type="application/xml")

    except Exception as exc:
        logger.error("[ms_router] TwiML build failed: %r — falling back to legacy", exc)
        fallback = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            "<Response>"
            f"<Redirect>{LEGACY_VOICE_URL}</Redirect>"
            "</Response>"
        )
        return Response(content=fallback, media_type="application/xml")


# ---------------------------------------------------------------------------
# Route 2: Media Streams WebSocket endpoint
# ---------------------------------------------------------------------------

@router.websocket("/ms/stream")
async def ms_stream(websocket: WebSocket) -> None:
    """
    Twilio connects here via the WebSocket URL returned by /ms/incoming.

    Lifecycle:
      1. Check MEDIA_STREAMS_ENABLED kill switch
      2. Instantiate WebSocketCallHandler (one per call, all state on instance)
      3. Run handler.handle() — runs until call ends or error
      4. If handle() raises before _call_stable: log "UNSTABLE CALL"
      5. Attempt graceful WebSocket close in all cases

    Error policy:
      - Unstable call (no complete STT->LLM->TTS cycle): log at ERROR level
        for monitoring. The handler's _cleanup() still saves whatever session
        state exists, and the watchdog/pipeline failure message has already
        been played if possible.
      - handler.handle() never raises (it catches everything internally).
        The try/except here is a final safety net.
    """
    if not MEDIA_STREAMS_ENABLED:
        logger.warning("[ms_router] WebSocket hit but MEDIA_STREAMS_ENABLED=false — closing")
        await websocket.close(code=1001, reason="Media Streams pipeline disabled")
        return

    handler = WebSocketCallHandler(websocket)

    try:
        await handler.handle()

    except WebSocketDisconnect:
        # Twilio disconnected — normal call end
        logger.info("[ms_router] WebSocket disconnected cleanly")

    except Exception as exc:
        logger.error(
            "[ms_router] UNHANDLED EXCEPTION in handler: %r\n%s",
            exc, traceback.format_exc(),
        )

        if not handler._call_stable:
            logger.error(
                "[ms_router] UNSTABLE CALL call_sid=%s — pipeline failed before first stable turn",
                handler.call_sid,
            )
            # Try to play a failure message before the line drops
            try:
                await handler.play_pipeline_failure()
            except Exception:
                pass

        # Attempt graceful WebSocket close
        try:
            await websocket.close(code=1011, reason="Internal server error")
        except Exception:
            pass  # Already disconnected — silently ignore

    finally:
        if not handler._call_stable:
            logger.warning(
                "[ms_router] call ended without reaching stable state call_sid=%s",
                handler.call_sid,
            )
