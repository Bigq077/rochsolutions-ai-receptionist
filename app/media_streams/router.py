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

import asyncio
import json as _json
import time as _time

from fastapi import APIRouter, Depends, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, Response

from .config import (
    ASSEMBLYAI_API_KEY,
    ASSEMBLYAI_WS_URL,
    ASSEMBLYAI_WS_URL_V2,
    ASSEMBLYAI_USE_V2,
    MEDIA_STREAMS_ENABLED,
    RENDER_EXTERNAL_URL,
    LEGACY_VOICE_URL,
)
from .connection import WebSocketCallHandler, _active_handlers
from .stt_stream import _mask_key, _close_info, _is_garbage_transcript

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Security: Twilio request signature validation (shared with /twilio/ routes)
# ---------------------------------------------------------------------------

async def _verify_twilio_signature_ms(request: Request) -> None:
    """Validate Twilio webhook signature on /ms/incoming POST requests."""
    from app.config import TWILIO_AUTH_TOKEN
    if not TWILIO_AUTH_TOKEN or request.method != "POST":
        return

    from twilio.request_validator import RequestValidator

    signature = request.headers.get("X-Twilio-Signature", "")
    base = os.getenv("RENDER_EXTERNAL_URL", "").rstrip("/")
    if not base:
        base = os.getenv("BASE_URL", "").rstrip("/")
    if base:
        canonical_url = f"{base}{request.url.path}"
        if request.url.query:
            canonical_url += f"?{request.url.query}"
    else:
        proto = request.headers.get("x-forwarded-proto", request.url.scheme)
        host = request.headers.get("x-forwarded-host", request.url.netloc)
        canonical_url = f"{proto}://{host}{request.url.path}"
        if request.url.query:
            canonical_url += f"?{request.url.query}"

    form = await request.form()
    params = {k: str(v) for k, v in form.items()}

    validator = RequestValidator(TWILIO_AUTH_TOKEN)
    if not validator.validate(canonical_url, params, signature):
        logger.warning("Twilio signature INVALID on /ms/incoming: url=%s", canonical_url)
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="Forbidden")


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

@router.post("/ms/incoming",  dependencies=[Depends(_verify_twilio_signature_ms)])
@router.post("/ms/incomings", dependencies=[Depends(_verify_twilio_signature_ms)])
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

        # Cache From and To from the Twilio HTTP POST body into Redis so the
        # WebSocket handler can retrieve them on the "start" event.
        # Twilio does NOT reliably forward customParameters or caller numbers
        # through the WebSocket start payload — Redis is the only reliable
        # channel for passing this data from the HTTP leg to the WS leg.
        call_sid      = ""
        caller_number = ""
        to_number     = ""
        try:
            form = await request.form()
            call_sid      = form.get("CallSid", "")
            caller_number = form.get("From", "") or form.get("from", "")
            to_number     = form.get("To",   "") or form.get("to",   "")
            if call_sid:
                from .session import _get_redis
                _redis = _get_redis()
                if _redis:
                    if caller_number:
                        await _redis.setex(f"ms_caller:{call_sid}", 300, caller_number)
                    if to_number:
                        await _redis.setex(f"ms_to:{call_sid}", 300, to_number)
                    else:
                        logger.warning(
                            "[ms_router] to_number EMPTY — ms_to not cached call_sid=%s "
                            "(Twilio form keys: %s)",
                            call_sid, list(form.keys()),
                        )
                    logger.info(
                        "[ms_router] cached call_sid=%s from=%s to=%s",
                        call_sid, caller_number, to_number,
                    )
        except Exception as _exc:
            logger.warning("[ms_router] Redis cache failed: %r", _exc)

        # Pass To/From as Stream <Parameter> elements so connection.py can
        # resolve clinic_id from the dialled number on the "start" event.
        # Without this, twilio_to is always empty and clinic_id defaults to "demo".
        _params_xml = ""
        if to_number:
            _params_xml += f'<Parameter name="twilio_to" value="{to_number}"/>'
        if caller_number:
            _params_xml += f'<Parameter name="twilio_from" value="{caller_number}"/>'

        twiml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            "<Response>"
            "<Connect>"
            f'<Stream url="{ws_url}">'
            f"{_params_xml}"
            "</Stream>"
            "</Connect>"
            # <Hangup/> prevents Twilio re-requesting this URL when the
            # WebSocket closes, which would otherwise create a reconnect loop.
            "<Hangup/>"
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


# ---------------------------------------------------------------------------
# Route 3: Test-only transcript injection
# ---------------------------------------------------------------------------

@router.post("/ms/test/inject-transcript/{call_sid}")
async def inject_test_transcript(call_sid: str, request: Request) -> JSONResponse:
    """
    TEST-ONLY endpoint: inject a patient utterance directly into Susie's
    LLM pipeline for the specified call.

    Bypasses STT entirely — the text goes straight onto the transcript_queue
    that the LLM loop is waiting on.  Also calls on_speech_started() so the
    SilenceHandler does not fire a spurious re-ask between injections.

    Request body: {"text": "I have back pain"}
    Response:     {"ok": true, "text": "..."}  or  {"ok": false, "error": "..."}

    The endpoint is always registered (no kill switch needed) because Twilio
    has no way to hit this URL — only our local test runner knows about it.
    """
    try:
        body = await request.json()
    except Exception:
        body = {}
    text = (body.get("text") or "").strip()
    via_filter: bool = bool(body.get("via_filter", False))

    if not text:
        return JSONResponse({"ok": False, "error": "empty text"}, status_code=400)

    # If the caller requested STT-style filtering, run the garbage filter.
    # This simulates what stt_stream.py does before putting text on the queue —
    # so test scenarios can verify that noise-only input is dropped correctly.
    if via_filter and _is_garbage_transcript(text):
        logger.info("[ms_inject] via_filter=True — garbage dropped: %r", text)
        return JSONResponse({"ok": True, "filtered": True, "text": text})

    handler = _active_handlers.get(call_sid)
    if handler is None:
        logger.warning("[ms_inject] no active handler for call_sid=%s", call_sid)
        return JSONResponse(
            {"ok": False, "error": f"no active session for {call_sid}"},
            status_code=404,
        )

    try:
        # Diagnostic snapshot BEFORE injection
        stop_set    = handler._stop_event.is_set()
        started_set = handler._started_event.is_set()
        llm_busy    = handler._llm_busy
        q_before    = handler.transcript_queue.qsize()

        # If the LLM is still processing the previous turn, wait for it to finish
        # before injecting.  Without this guard, turns injected during PRESENT_SLOTS
        # (the slowest step — LLM + check_availability tool call + slot-list TTS) are
        # silently dropped by _llm_loop when _llm_busy=True, stalling the flow at
        # slot selection and causing slot_confirmed / flow_completed failures.
        _MAX_BUSY_WAIT = 60.0
        _busy_waited   = 0.0
        while handler._llm_busy and _busy_waited < _MAX_BUSY_WAIT and not handler._stop_event.is_set():
            await asyncio.sleep(0.25)
            _busy_waited += 0.25
        if _busy_waited > 0:
            logger.info(
                "[ms_inject] waited %.1fs for LLM to finish before injecting %r",
                _busy_waited, text[:60],
            )

        # Simulate a completed utterance so SilenceHandler fully resets:
        # on_transcript_received() cancels the timer AND resets reask_count,
        # currently_reasking, and last_audio_received_at — a strict superset
        # of on_speech_started() which only cancels the timer.  This prevents
        # a re-ask that fired on a previous turn from leaving reask_count=1,
        # which would make the next silence window 10 s instead of 28 s.
        handler._silence_handler.on_transcript_received()
        # Inject the transcript directly into the LLM pipeline
        handler.transcript_queue.put_nowait(text)

        q_after = handler.transcript_queue.qsize()

        # Snapshot key in-memory session fields to detect if flow is updating
        sess = handler.session
        sess_snap = {
            "flow_step":    sess.get("flow_step"),
            "flow_started": sess.get("flow_started"),
            "reason":       sess.get("reason"),
            "intent":       sess.get("intent"),
            "state":        sess.get("state"),
            "turns_len":    len(sess.get("turns", [])),
            "history_len":  len(sess.get("conversation_history", [])),
        }

        logger.info(
            "[ms_inject] injected call_sid=%s text=%r  "
            "stop=%s started=%s llm_busy=%s q_before=%d q_after=%d sess=%s",
            call_sid, text[:80], stop_set, started_set, llm_busy, q_before, q_after, sess_snap,
        )
        return JSONResponse({
            "ok":          True,
            "text":        text,
            "diag": {
                "stop_set":    stop_set,
                "started_set": started_set,
                "llm_busy":    llm_busy,
                "q_before":    q_before,
                "q_after":     q_after,
                "session":     sess_snap,
            },
        })
    except Exception as exc:
        logger.error("[ms_inject] injection failed call_sid=%s: %r", call_sid, exc)
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)


# ---------------------------------------------------------------------------
# Route 4: AssemblyAI connection diagnostic
# ---------------------------------------------------------------------------

@router.get("/ms/test-stt")
async def ms_test_stt() -> JSONResponse:
    """
    Standalone AssemblyAI connection test.

    Opens a WebSocket to AssemblyAI with the current URL and credentials,
    waits up to 5 seconds for any message, then closes cleanly.

    Returns JSON:
      {
        "connected": bool,
        "begin_received": bool,
        "messages_received": [...],
        "close_info": "...",
        "url_masked": "...",
        "error": "..." or null,
        "duration_ms": float
      }

    Use: GET https://your-domain.onrender.com/ms/test-stt
    """
    try:
        import websockets
        import websockets.exceptions
    except ImportError:
        return JSONResponse({"error": "websockets not installed"}, status_code=500)

    url        = ASSEMBLYAI_WS_URL_V2 if ASSEMBLYAI_USE_V2 else ASSEMBLYAI_WS_URL
    ws_headers = {"Authorization": ASSEMBLYAI_API_KEY}
    masked_url = _mask_key(url, ASSEMBLYAI_API_KEY)

    result = {
        "connected":         False,
        "begin_received":    False,
        "messages_received": [],
        "close_info":        None,
        "url_masked":        masked_url,
        "error":             None,
        "duration_ms":       0.0,
    }

    t0 = _time.monotonic()

    try:
        async with websockets.connect(
            url,
            additional_headers=ws_headers,
            open_timeout=5,
            close_timeout=3,
        ) as ws:
            result["connected"] = True
            logger.info("[ms_test_stt] connected to AssemblyAI — waiting for Begin")

            deadline = _time.monotonic() + 5.0
            while _time.monotonic() < deadline:
                remaining = deadline - _time.monotonic()
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=min(remaining, 1.0))
                except asyncio.TimeoutError:
                    continue

                try:
                    msg = _json.loads(raw)
                except Exception:
                    msg = {"raw": str(raw)[:200]}

                result["messages_received"].append(msg)
                logger.info("[ms_test_stt] received: %r", msg)

                msg_type = msg.get("type") or msg.get("message_type") or ""
                if msg_type in ("Begin", "SessionBegins", "session_begins"):
                    result["begin_received"] = True
                    break
                if msg_type == "error":
                    result["error"] = msg.get("error", "unknown AssemblyAI error")
                    break

            await ws.close()

    except websockets.exceptions.ConnectionClosedError as exc:
        info = _close_info(exc)
        result["close_info"] = info
        result["error"]      = f"Connection closed immediately: {info}"
        logger.warning("[ms_test_stt] ConnectionClosedError: %s", info)
    except websockets.exceptions.WebSocketException as exc:
        result["error"] = f"WebSocketException: {exc!r}"
        logger.error("[ms_test_stt] WebSocketException: %r", exc)
    except OSError as exc:
        result["error"] = f"OS/network error: {exc!r}"
        logger.error("[ms_test_stt] OSError: %r", exc)
    except Exception as exc:
        result["error"] = f"Unexpected: {exc!r}"
        logger.error("[ms_test_stt] error: %r", exc)

    result["duration_ms"] = round((_time.monotonic() - t0) * 1000, 1)
    logger.info("[ms_test_stt] result: %r", result)
    return JSONResponse(result)
