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
from urllib.parse import quote as _url_quote

from fastapi import APIRouter, Depends, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, Response

from .config import (
    ASSEMBLYAI_API_KEY,
    ASSEMBLYAI_WS_URL,
    ASSEMBLYAI_WS_URL_V2,
    ASSEMBLYAI_USE_V2,
    ASSEMBLYAI_USE_U35,
    MEDIA_STREAMS_ENABLED,
    RENDER_EXTERNAL_URL,
    LEGACY_VOICE_URL,
    assemblyai_ws_url,
)
from .connection import WebSocketCallHandler, _active_handlers
from .stt_stream import (
    _mask_key, _close_info, _is_garbage_transcript, build_keyterms,
)

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


def _abs_ms_url(request: Request, path: str) -> str:
    """Absolute https:// URL for a Twilio action/whisper callback on this host —
    mirrors _build_ws_url's domain resolution but keeps http(s) for REST hooks."""
    domain = RENDER_EXTERNAL_URL.strip().rstrip("/")
    if not domain:
        host = request.headers.get("host", "localhost")
        domain = f"https://{host}"
    elif not domain.startswith("http"):
        domain = f"https://{domain}"
    return f"{domain}{path}"


async def _cache_call_ids(call_sid: str, caller_number: str, to_number: str) -> None:
    """Cache From/To keyed by CallSid so the WebSocket handler can resolve them on
    the 'start' event (Twilio does not forward them reliably through the socket)."""
    if not call_sid:
        return
    try:
        from .session import _get_redis
        _redis = _get_redis()
        if _redis:
            if caller_number:
                await _redis.setex(f"ms_caller:{call_sid}", 300, caller_number)
            if to_number:
                await _redis.setex(f"ms_to:{call_sid}", 300, to_number)
            else:
                logger.warning(
                    "[ms_router] to_number EMPTY — ms_to not cached call_sid=%s",
                    call_sid,
                )
            logger.info(
                "[ms_router] cached call_sid=%s from=%s to=%s",
                call_sid, caller_number, to_number,
            )
    except Exception as _exc:
        logger.warning("[ms_router] Redis cache failed: %r", _exc)


async def _cached_caller_number(call_sid: str) -> str:
    """The caller's number for *call_sid*, read back from `_cache_call_ids`.

    Returns "" on EVERY failure path — no sid, no Redis, no key, a decode
    error. This is called from the screening whisper, which runs after the
    practitioner has already picked up, so every millisecond here is silence in
    their ear. Degrading to the caller-less whisper is always better than
    delaying it.
    """
    if not call_sid:
        return ""
    try:
        from .session import _get_redis
        _redis = _get_redis()
        if not _redis:
            return ""
        raw = await _redis.get(f"ms_caller:{call_sid}")
    except Exception as _exc:
        logger.warning("[ms_router] whisper caller-id read failed: %r", _exc)
        return ""
    if raw is None:
        return ""
    if isinstance(raw, (bytes, bytearray)):
        try:
            raw = raw.decode("utf-8", "ignore")
        except Exception:
            return ""
    return str(raw).strip()


def _whisper_caller_phrase(raw: str) -> str:
    """Spoken form of the caller's number for the screening whisper, or "".

    Empty means "say nothing about who is calling", and the practitioner hears
    exactly the whisper they hear today. Empty is returned for a withheld
    number, a number we cannot pace confidently, and any exception — announcing
    nothing is always safe, announcing something wrong is not.

    Paced through `tts_stream._pace_digit_groups`, which its own docstring names
    as the single owner of what a spoken phone number sounds like here. Reusing
    it means the number the practitioner hears in the whisper is grouped
    identically to the one Susie reads back to the caller.

    Both imports are lazy. `.connection` is warm — `router` imports it at module
    scope. `.tts_stream` and the `.receptionist_tools` that `_is_usable_caller_id`
    reaches for are NOT guaranteed warm by importing `router`; measured cold they
    cost 4ms and 76ms respectively, so the worst case is ~80ms of added silence,
    once per worker, on the first overflow call after a cold start. Measured
    rather than assumed, because this runs on an answered leg — if either module
    ever grows an expensive import, re-measure before assuming it is still fine.
    """
    raw = (raw or "").strip()
    if not raw:
        return ""
    try:
        from .connection import _is_usable_caller_id
        if not _is_usable_caller_id(raw):
            return ""
        digits = "".join(ch for ch in raw if ch.isdigit())
        # Spoken back in the 0-form a UK practitioner recognises, exactly as
        # _spell_phone does — never "plus four four".
        if digits.startswith("44"):
            digits = "0" + digits[2:]
        # Deliberately narrow: only numbers we can group the way every other
        # read-back on the call is grouped. A non-UK caller falls through to
        # the caller-less whisper rather than being read out in a shape nobody
        # can check.
        if not digits.startswith("0") or not (10 <= len(digits) <= 11):
            return ""
        from .tts_stream import _pace_digit_groups
        return _pace_digit_groups([digits[:5], digits[5:8], digits[8:]])
    except Exception as _exc:
        logger.warning("[ms_router] whisper caller phrasing failed: %r", _exc)
        return ""


def _stream_twiml(
    request: Request, to_number: str, caller_number: str, overflow: bool = False
) -> str:
    """Build the <Connect><Stream> TwiML that hands the call to Susie. Shared by
    /ms/incoming (AI-first) and /ms/after-dial (AI overflow after a missed ring)."""
    ws_url = _build_ws_url(request)
    _params_xml = ""
    if to_number:
        _params_xml += f'<Parameter name="twilio_to" value="{to_number}"/>'
    if caller_number:
        _params_xml += f'<Parameter name="twilio_from" value="{caller_number}"/>'
    if overflow:
        _params_xml += '<Parameter name="overflow" value="true"/>'
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response><Connect>"
        f'<Stream url="{ws_url}">{_params_xml}</Stream>'
        # <Hangup/> prevents Twilio re-requesting this URL when the WebSocket
        # closes, which would otherwise create a reconnect loop.
        "</Connect><Hangup/></Response>"
    )


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

    # Warm the ElevenLabs TLS pool now, off the critical path. This webhook
    # fires ~450ms before the greeting is synthesised on the WebSocket leg —
    # the only point early enough to absorb DNS+TLS setup. Fire-and-forget:
    # the TwiML response below must not wait on it.
    try:
        import asyncio as _asyncio
        from .tts_stream import prewarm as _tts_prewarm
        _asyncio.create_task(_tts_prewarm(), name="ms_tts_prewarm")
    except Exception as _pw_exc:
        logger.warning("[ms_router] TTS prewarm not scheduled: %r", _pw_exc)

    try:
        # From/To cached to Redis so the WS handler can resolve them on the
        # "start" event (Twilio doesn't forward them reliably through the socket).
        form          = await request.form()
        call_sid      = form.get("CallSid", "")
        caller_number = form.get("From", "") or form.get("from", "")
        to_number     = form.get("To",   "") or form.get("to",   "")

        # Diagnostic: on a FORWARDED call this says, in one line, whether the
        # carrier passed the original caller through (From = the patient — good)
        # or rewrote it to the forwarding number (From = the practitioner — the
        # caller-ID guard in connection.py then suppresses it). Twilio sets
        # ForwardedFrom when the call reached us via a diversion.
        logger.info(
            "[ms_router] inbound params: From=%s To=%s ForwardedFrom=%s CallerName=%s",
            caller_number or "(none)",
            to_number or "(none)",
            form.get("ForwardedFrom", "") or "(none)",
            form.get("CallerName", "") or "(none)",
        )
        await _cache_call_ids(call_sid, caller_number, to_number)

        # ── Human-first overflow ────────────────────────────────────────────
        # If this clinic enables call_overflow, ring the practitioner's own
        # phone FIRST and only hand the call to Susie if they don't press 1 to
        # take it. Gated per-clinic via clinic.json — every other clinic keeps
        # answering with Susie immediately (no behaviour change).
        # The clinic.json value is now only the DEFAULT: a clinic can text OFF
        # to their Susie number to ring their own phone first until midnight
        # (app/clinic_call_mode.py). resolve_overflow never raises and falls
        # back to this same config value, so a Redis fault degrades to today's
        # behaviour rather than to a failed webhook.
        _human_first, _mode_reason = False, "config"
        try:
            from app.clinic_config import clinic_id_from_twilio_to, get_clinic
            from app.clinic_call_mode import resolve_overflow
            _cid      = clinic_id_from_twilio_to(to_number)
            _clinic   = get_clinic(_cid) or {}
            _overflow = _clinic.get("call_overflow") or {}
            _human_first, _mode_reason = await resolve_overflow(_cid, _clinic)
        except Exception as _cx:
            logger.warning("[ms_router] overflow config lookup failed: %r", _cx)
            _clinic, _overflow = {}, {}

        _dial_phone = (_overflow.get("dial_phone") or "").strip()
        if _human_first and _dial_phone:
            _timeout   = int(_overflow.get("ring_timeout", 20) or 20)
            # callerId must be a number we own — use the dialled clinic number
            # so the practitioner sees it's a work call.
            _caller_id = (to_number or _clinic.get("phone", "")).replace(" ", "")
            _screen    = _abs_ms_url(request, f"/ms/screen?parent={call_sid}")
            _action    = _abs_ms_url(request, "/ms/after-dial")
            logger.info(
                "[ms_router] overflow ON (%s) — ringing %s first (timeout=%ss) "
                "call_sid=%s",
                _mode_reason, _dial_phone, _timeout, call_sid,
            )
            dial_twiml = (
                '<?xml version="1.0" encoding="UTF-8"?>'
                "<Response>"
                f'<Dial answerOnBridge="true" timeout="{_timeout}" '
                f'callerId="{_caller_id}" action="{_action}" method="POST">'
                f'<Number url="{_screen}">{_dial_phone}</Number>'
                "</Dial>"
                "</Response>"
            )
            return Response(content=dial_twiml, media_type="application/xml")

        # Twilio-side dual-channel recording (DEFAULT OFF — see audio_capture.py
        # for the GDPR note). Scheduled as a background task, never awaited: a
        # slow webhook delays the whole call, and this is a diagnostic.
        try:
            import asyncio as _rec_asyncio  # self-contained: the alias above is
                                            # bound inside a different try block
            from .audio_capture import start_twilio_recording, twilio_recording_enabled
            if twilio_recording_enabled() and call_sid:
                _rec_asyncio.create_task(
                    start_twilio_recording(call_sid), name="ms_twilio_recording",
                )
        except Exception as _rec_exc:
            logger.warning("[ms_router] call recording not scheduled: %r", _rec_exc)

        # Default (AI-first): connect straight to Susie.
        logger.info("[ms_router] incoming call — stream URL: %s", _build_ws_url(request))
        return Response(
            content=_stream_twiml(request, to_number, caller_number),
            media_type="application/xml",
        )

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
# Overflow screening routes (only reached when a clinic enables call_overflow)
# ---------------------------------------------------------------------------

@router.post("/ms/screen", dependencies=[Depends(_verify_twilio_signature_ms)])
async def ms_screen(request: Request) -> Response:
    """Whisper played on the PRACTITIONER's leg after they answer, before the
    call bridges. Asks them to press 1 to accept; no key press (or hang-up, or
    their carrier voicemail) falls through to <Hangup/> so the caller reaches
    Susie via the <Dial> action. `parent` = the caller-leg CallSid, carried in
    the query string because this leg has a different CallSid.

    The whisper also announces the caller's number when it can be said
    confidently, because <Dial callerId> shows the clinic's own line rather
    than the caller's — Twilio only presents numbers we own — so the
    practitioner would otherwise answer with no idea who is on the other end.

    Nothing here may raise or dawdle: it runs after the practitioner has picked
    up, so any delay is silence in their ear and any exception drops a call
    they just answered. Every lookup degrades to the caller-less whisper, and
    the press-1 contract is unchanged — that is what keeps voicemail from
    swallowing the call."""
    parent = request.query_params.get("parent", "")
    _whisper, _with_caller = "", ""
    try:
        form  = await request.form()
        _from = form.get("From", "") or ""  # the clinic callerId on this leg
        from app.clinic_config import clinic_id_from_twilio_to, get_clinic
        _clinic   = get_clinic(clinic_id_from_twilio_to(_from)) or {}
        _overflow = _clinic.get("call_overflow") or {}
        _whisper     = _overflow.get("whisper_text") or ""
        _with_caller = _overflow.get("whisper_text_with_caller") or ""
    except Exception:
        _whisper, _with_caller = "", ""
    if not _whisper:
        _whisper = (
            "Business call from your Susie line. "
            "Press 1 to take it, or hang up and Susie will handle it."
        )
    if not _with_caller:
        _with_caller = (
            "Business call, from {caller}. "
            "Press 1 to take it, or hang up and Susie will handle it."
        )

    # Tell the practitioner WHO is calling. The <Dial callerId> is a number we
    # own — the clinic's own Susie line — because Twilio will not present the
    # caller's number, so without this the phone rings showing the clinic's own
    # number and they answer blind.
    #
    # `.replace`, never `.format`: this template comes from clinic.json, and a
    # stray brace in operator-edited config would raise, killing the whisper on
    # a live call. A template with no {caller} placeholder simply never gets a
    # caller announced, which is a legitimate way to switch this off per clinic.
    _caller_phrase = _whisper_caller_phrase(await _cached_caller_number(parent))
    if _caller_phrase and "{caller}" in _with_caller:
        _whisper = _with_caller.replace("{caller}", _caller_phrase)

    # The prompt got ~5s longer, and the gather timeout runs from the END of it.
    # Left at 8 the caller would wait that much longer on ringback before Susie
    # picks up a call the practitioner is ignoring. Digits are accepted DURING
    # the prompt, so the practitioner has strictly more time to press 1 than
    # before, not less — the shorter timeout only trims the dead tail.
    _timeout = 5 if _caller_phrase else 8

    _gather = _abs_ms_url(request, f"/ms/screen-gather?parent={parent}")
    # Escaped because it is operator-edited config interpolated into XML: one
    # ampersand in whisper_text is malformed TwiML, and malformed TwiML on this
    # leg drops the call the practitioner just answered.
    from xml.sax.saxutils import escape as _xml_escape
    twiml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        f'<Gather numDigits="1" timeout="{_timeout}" action="{_gather}" method="POST">'
        f'<Say language="en-GB">{_xml_escape(_whisper)}</Say>'
        "</Gather>"
        "<Hangup/>"
        "</Response>"
    )
    return Response(content=twiml, media_type="application/xml")


@router.post("/ms/screen-gather", dependencies=[Depends(_verify_twilio_signature_ms)])
async def ms_screen_gather(request: Request) -> Response:
    """Evaluate the practitioner's key press. '1' → mark the call accepted (a
    Redis flag keyed by the caller-leg CallSid) and return an empty response so
    the whisper leg completes and Twilio bridges the two parties. Anything else
    → hang up their leg so the call screens through to Susie."""
    parent = request.query_params.get("parent", "")
    form   = await request.form()
    digits = (form.get("Digits", "") or "").strip()
    if digits == "1" and parent:
        try:
            from .session import _get_redis
            _redis = _get_redis()
            if _redis:
                await _redis.setex(f"marcus_accepted:{parent}", 120, "1")
        except Exception as _exc:
            logger.warning("[ms_router] accept-flag set failed: %r", _exc)
        logger.info("[ms_router] overflow — practitioner accepted (parent=%s)", parent)
        return Response(
            content='<?xml version="1.0" encoding="UTF-8"?><Response/>',
            media_type="application/xml",
        )
    logger.info("[ms_router] overflow — not accepted (digits=%r) → screening to Susie", digits)
    return Response(
        content='<?xml version="1.0" encoding="UTF-8"?><Response><Hangup/></Response>',
        media_type="application/xml",
    )


@router.post("/ms/after-dial", dependencies=[Depends(_verify_twilio_signature_ms)])
async def ms_after_dial(request: Request) -> Response:
    """<Dial action> handler on the CALLER's leg after the ring finishes. If the
    practitioner accepted (Redis flag set on the same CallSid), the human call
    happened — just end. Otherwise hand the caller to Susie as the overflow
    receptionist. The accept flag — not DialCallStatus — is authoritative: a
    screened-out call still reports 'completed'."""
    form        = await request.form()
    call_sid    = form.get("CallSid", "")
    dial_status = (form.get("DialCallStatus", "") or "").strip().lower()

    accepted = False
    if call_sid:
        try:
            from .session import _get_redis
            _redis = _get_redis()
            if _redis:
                accepted = bool(await _redis.get(f"marcus_accepted:{call_sid}"))
        except Exception as _exc:
            logger.warning("[ms_router] accept-flag read failed: %r", _exc)

    if accepted:
        logger.info(
            "[ms_router] after-dial: accepted call ended (status=%s) — hangup call_sid=%s",
            dial_status, call_sid,
        )
        return Response(
            content='<?xml version="1.0" encoding="UTF-8"?><Response><Hangup/></Response>',
            media_type="application/xml",
        )

    caller_number = form.get("From", "") or form.get("from", "")
    to_number     = form.get("To",   "") or form.get("to",   "")
    await _cache_call_ids(call_sid, caller_number, to_number)
    logger.info(
        "[ms_router] after-dial: not accepted (status=%s) — Susie overflow call_sid=%s",
        dial_status, call_sid,
    )
    return Response(
        content=_stream_twiml(request, to_number, caller_number, overflow=True),
        media_type="application/xml",
    )


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

    # ── Handler lookup FIRST (before empty-text guard) ───────────────────────
    # IMPORTANT: this check must come before `not text` so that the handler-ready
    # probe (which sends an empty body {}) returns 404 while the handler is not yet
    # registered, and 400 only once the handler IS registered.  If the empty-text
    # guard ran first it would return 400 unconditionally — making the probe always
    # break immediately regardless of whether _active_handlers contains the sid.
    handler = _active_handlers.get(call_sid)
    if handler is None:
        logger.warning(
            "[ms_inject] INJECT lookup sid=%s found=False known_sids=%s",
            call_sid,
            list(_active_handlers.keys())[:5],
        )
        return JSONResponse(
            {"ok": False, "error": f"no active session for {call_sid}"},
            status_code=404,
        )

    logger.debug("[ms_inject] INJECT lookup sid=%s found=True", call_sid)

    if not text:
        # Handler exists but probe sent an empty body — this is the handler-ready signal.
        return JSONResponse({"ok": False, "error": "empty text"}, status_code=400)

    # If the caller requested STT-style filtering, run the garbage filter.
    # This simulates what stt_stream.py does before putting text on the queue —
    # so test scenarios can verify that noise-only input is dropped correctly.
    if via_filter and _is_garbage_transcript(text):
        logger.info("[ms_inject] via_filter=True — garbage dropped: %r", text)
        return JSONResponse({"ok": True, "filtered": True, "text": text})

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
            "flow_step":              sess.get("flow_step"),
            "flow_state":             sess.get("flow_state"),
            "flow_started":           sess.get("flow_started"),
            "reason":                 sess.get("reason"),
            "intent":                 sess.get("intent"),
            "state":                  sess.get("state"),
            "turns_len":              len(sess.get("turns", [])),
            "history_len":            len(sess.get("conversation_history", [])),
            "phone_readback_pending": sess.get("phone_readback_pending"),
            "phone_confirmed":        sess.get("phone_confirmed"),
            "booking_confirmed":      sess.get("booking_confirmed"),
            "_last_handled_by":       sess.get("_last_handled_by"),
            "_last_extracted_phone":  sess.get("_last_extracted_phone"),
            "_last_yes_detected":     sess.get("_last_yes_detected"),
            "_last_no_detected":      sess.get("_last_no_detected"),
            "_last_assistant_response": sess.get("_last_assistant_response"),
            # Two-clinic debug fields — show whether location gate will fire
            "twilio_to":              sess.get("twilio_to"),
            "needs_location":         sess.get("needs_location"),
            "selected_location":      sess.get("selected_location"),
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

    # Must mirror stt_stream.start() exactly — resolver AND keyterms. A
    # diagnostic that tests a different URL than the call uses is worse than no
    # diagnostic: v3 closes the socket instantly on an unknown query param, so
    # this endpoint is the pre-flight that proves a model/param combination
    # (notably universal-3-5-pro + keyterms_prompt) actually connects.
    url = assemblyai_ws_url()
    if not ASSEMBLYAI_USE_V2:
        url += "&keyterms_prompt=" + _url_quote(_json.dumps(build_keyterms(None)))
    ws_headers = {"Authorization": ASSEMBLYAI_API_KEY}
    masked_url = _mask_key(url, ASSEMBLYAI_API_KEY)

    result = {
        "connected":         False,
        "begin_received":    False,
        "messages_received": [],
        "close_info":        None,
        "stt_variant":       (
            "v2" if ASSEMBLYAI_USE_V2
            else "u3.5-pro" if ASSEMBLYAI_USE_U35
            else "universal-streaming-english"
        ),
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
