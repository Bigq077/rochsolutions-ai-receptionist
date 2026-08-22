# app/routes/twilio.py
from __future__ import annotations

import os
import re
import uuid
from datetime import datetime
from fastapi import Depends, HTTPException, Request
from fastapi.responses import Response
import logging
from app.notifications.smart_sms_router import send_smart_followup_sms
logger = logging.getLogger(__name__)
from datetime import datetime
from app.tools.handoff import fire_and_forget_append_summary_row
from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import PlainTextResponse
from twilio.twiml.voice_response import VoiceResponse, Gather, Dial, Connect, Stream

from app.storage.redis_store import get_session, save_session, acquire_once_lock
from app.clinic_config import clinic_id_from_twilio_to, get_clinic
from app.config import TRANSFER_FALLBACK_NUMBER, TWILIO_AUTH_TOKEN


# --------------------------------------------------
# Security: Twilio request signature validation
# --------------------------------------------------

async def _verify_twilio_signature(request: Request) -> None:
    """
    Validate the X-Twilio-Signature header on every POST to /twilio/*.

    Only active when TWILIO_AUTH_TOKEN is set — safe no-op in local dev.
    Rejects forged or replayed webhook calls with HTTP 403.

    URL reconstruction accounts for Render's HTTPS reverse-proxy:
    - Uses BASE_URL env var if available (most reliable on Render)
    - Falls back to X-Forwarded-Proto / X-Forwarded-Host headers
    """
    if not TWILIO_AUTH_TOKEN or request.method != "POST":
        return

    from twilio.request_validator import RequestValidator

    signature = request.headers.get("X-Twilio-Signature", "")

    # Build the canonical URL Twilio used when signing this request.
    # On Render the app is behind an HTTPS load balancer; the raw request
    # arrives as HTTP internally, so the forwarded headers are the last
    # resort rather than the first choice.
    #
    # This MUST resolve the same origin app.routes.realtime advertises in the
    # <Dial> action URL, or Twilio signs one host and we check another. Until
    # 22 Aug this read BASE_URL then x-forwarded-host while the callback was
    # advertised at RENDER_EXTERNAL_URL, and call CA3b018519's missed-transfer
    # callback was refused 403 — the net fired and we shut the door on it.
    from app.config import public_base_url
    base = public_base_url()
    if base:
        path = request.url.path
        query = request.url.query
        canonical_url = f"{base}{path}{'?' + query if query else ''}"
    else:
        proto = request.headers.get("x-forwarded-proto", request.url.scheme)
        host = request.headers.get("x-forwarded-host", request.url.netloc)
        path = request.url.path
        query = request.url.query
        canonical_url = f"{proto}://{host}{path}{'?' + query if query else ''}"

    # Starlette caches parsed form data, so calling .form() here does not
    # consume the body — the route handler can still read it afterwards.
    form = await request.form()
    params = {k: str(v) for k, v in form.items()}

    validator = RequestValidator(TWILIO_AUTH_TOKEN)
    if not validator.validate(canonical_url, params, signature):
        logger.warning(
            "Twilio signature INVALID: url=%s sig=%s…",
            canonical_url,
            signature[:20],
        )
        raise HTTPException(status_code=403, detail="Forbidden")


router = APIRouter(
    prefix="/twilio",
    dependencies=[Depends(_verify_twilio_signature)],
)


# --------------------------------------------------
# Constants
# --------------------------------------------------

AI_NAME = "Susie"
# Fallback transfer number: sourced from env var TRANSFER_FALLBACK_NUMBER.
# Falls back to a default value if the env var is not set (see app/config.py).
# Clinics with a 'transfer_phone' key in their config always take priority over this.
TRANSFER_NUMBER_FALLBACK = TRANSFER_FALLBACK_NUMBER

# After location confirmed — hand off to triage
HOW_CAN_I_HELP = "How can I help you today?"


# --------------------------------------------------
# Clinic-aware greeting helpers
# --------------------------------------------------

def _build_reintro(clinic: dict) -> str:
    """
    Tier 1 re-introduction — used on first silence or first confused turn at TRIAGE.
    """
    name = clinic.get("sms_name") or clinic.get("display_name", "the clinic")
    return (
        f"Hello — this is Susie, {name}'s AI receptionist. "
        f"I can help you with: "
        f"booking an appointment, "
        f"information about our treatments and pricing, "
        f"insurance questions, "
        f"or details about our opening hours and location. "
        f"What can I help you with today?"
    )


def _build_menu() -> str:
    """
    Tier 2 explicit menu — used on second consecutive missed turn (silence or confusion).
    """
    return (
        "Let me give you a quick menu. "
        "Say book to book an appointment. "
        "Say treatments for information about our services and prices. "
        "Or say transfer and I'll put you straight through to the team. "
        "What would you like?"
    )


def _append_transfer(
    vr: VoiceResponse,
    announcement: str,
    request: Request,
    transfer_phone: str,
) -> None:
    """
    Speak the announcement then live-transfer the call to the clinic team.

    Uses a 20-second dial timeout so the caller is never left ringing forever.
    The Twilio action URL (/twilio/transfer-status) is called when the dial
    completes — if nobody answered, Susie re-engages the caller.
    """
    vr.say(announcement, language="en-GB")

    # Safety kill-switch (test sweeps). This is the legacy /twilio/voice Gather
    # path, not the media-stream one, but a kill-switch that covers only some
    # dial sites is worse than none — so guard it too. The announcement is still
    # spoken, so the call still *sounds* like a transfer; only the leg is dropped.
    from app.config import TRANSFER_DISABLED
    if TRANSFER_DISABLED:
        logger.warning(
            "[twilio] transfer SUPPRESSED — TRANSFER_DISABLED set; not dialing %s",
            transfer_phone,
        )
        return

    status_url = _abs_url(request, "/twilio/transfer-status")
    dial = Dial(action=status_url, method="POST", timeout=20)
    dial.number(transfer_phone)
    vr.append(dial)


def _build_greeting(clinic: dict) -> str:
    """
    Build the opening greeting from clinic config.
    - Custom 'greeting' field: used as-is (e.g. demo line).
    - All clinics: context-appropriate greeting from greeting_builder.
    """
    # Custom greeting overrides everything (set in clinic config)
    if clinic.get("greeting"):
        return clinic["greeting"]

    from app.greeting_builder import build_greeting
    return build_greeting()


def _build_location_confirmation(location_id: str, clinic: dict) -> str:
    """Return a confirmation string after the caller picks a location."""
    locations = clinic.get("locations", [])
    loc = next((l for l in locations if l["id"] == location_id), None)
    if loc:
        name = loc["name"]
        confirmations = {
            "alcester": f"Ok, thank you — I've noted you down for our Alcester clinic.",
            "redditch": f"Ok, thank you — I've noted you down for our Redditch clinic.",
        }
        return confirmations.get(location_id, f"Ok, thank you — I've noted you down for our {name} clinic.")
    return "Ok, thank you."


# --------------------------------------------------
# Helpers
# --------------------------------------------------

def xml(resp: VoiceResponse) -> PlainTextResponse:
    twiml_str = str(resp)
    logger.info("[twilio] TwiML out: %s", twiml_str[:600])
    return PlainTextResponse(twiml_str, media_type="application/xml")


def _abs_url(request: Request, path: str) -> str:
    base = str(request.base_url).rstrip("/")
    if not path.startswith("/"):
        path = "/" + path
    return base + path


def gather_speech(
    action_url: str,
    prompt: str | None = None,
    timeout: int = 5,
) -> Gather:
    g = Gather(
        input="speech dtmf",
        action=action_url,
        method="POST",
        language="en-GB",
        speech_timeout="auto",
        timeout=timeout,
        action_on_empty_result=True,
        barge_in=True,
        num_digits=1,
    )
    if prompt:
        g.say(prompt, language="en-GB")
    return g


def _append_turn(session: dict, role: str, text: str) -> dict:
    if not text:
        return session
    turns = session.get("turns", [])
    turns.append({"role": role, "text": text})
    session["turns"] = turns
    return session


def _ensure_clinic_on_session(session: dict, to_number: str | None) -> dict:
    if not session.get("clinic_id"):
        session["clinic_id"] = clinic_id_from_twilio_to(to_number)
    return session


def _init_session(session: dict, call_sid: str) -> dict:
    """Initialise fields that must exist from the very first request."""
    if not session.get("session_id"):
        session["session_id"] = str(uuid.uuid4())
    session["call_sid"] = call_sid
    # Location state — set by /twilio/location-select
    session.setdefault("location_selected", False)
    session.setdefault("selected_location", None)
    # Insurance state
    session.setdefault("insurance_info", {})
    # Miss counter (3-tier silence escalation)
    session.setdefault("miss_count", 0)
    # Consecutive silence counter — resets when user speaks or after callback phrase
    session.setdefault("consecutive_silence_count", 0)
    # Sidebar loop-prevention: tracks how many sidebar questions have been
    # answered per state so we cap at 1 per state.
    session.setdefault("sidebar_count_per_state", {})
    # Tone detection state — serialisable; live ToneDetector rebuilt each turn
    session.setdefault("_tone_state", {"word_counts": [], "tone": "standard", "locked": False})
    return session


def safe_error_response(request: Request) -> PlainTextResponse:
    """Return a graceful TwiML error response — never crash the call."""
    vr = VoiceResponse()
    turn_url = _abs_url(request, "/twilio/turn")
    vr.append(
        gather_speech(
            turn_url,
            "Something went wrong on my end — bear with me and I'll get that sorted.",
        )
    )
    return xml(vr)


# --------------------------------------------------
# TTS helper (ElevenLabs with Polly fallback)
# --------------------------------------------------

async def _say(
    vr: VoiceResponse,
    text: str,
    request: Request,
    gather_action: str | None = None,
) -> None:
    """
    Attempt ElevenLabs TTS; fall back to Polly.
    If gather_action is given, wraps the audio in a Gather so the
    caller can barge in.
    """
    try:
        from app.routes.tts_eleven import _eleven_tts_bytes_async
        from pathlib import Path as _Path
        audio_bytes = await _eleven_tts_bytes_async(text)
        _tts_dir = _Path("/tmp/tts")
        _tts_dir.mkdir(parents=True, exist_ok=True)
        _filename = f"{uuid.uuid4().hex}.mp3"
        (_tts_dir / _filename).write_bytes(audio_bytes)
        _base = str(request.base_url).rstrip("/")
        audio_url = f"{_base}/avatar/audio/{_filename}"
        logger.info("[twilio] _say audio_url=%s len_bytes=%d", audio_url, len(audio_bytes))
        if gather_action:
            g = Gather(
                input="speech dtmf",
                action=gather_action,
                method="POST",
                language="en-GB",
                speech_timeout="auto",
                timeout=5,
                action_on_empty_result=True,
                barge_in=True,
            )
            g.play(audio_url)
            vr.append(g)
        else:
            vr.play(audio_url)
    except Exception as e:
        logger.error("[twilio] TTS_ELEVEN ERROR: %r", e)
        if gather_action:
            vr.append(gather_speech(gather_action, text))
        else:
            vr.say(text, language="en-GB")


def attach_status_callback(vr: VoiceResponse, request: Request) -> None:
    vr.status_callback = _abs_url(request, "/twilio/status")
    vr.status_callback_method = "POST"


# --------------------------------------------------
# Location helper
# --------------------------------------------------

def _detect_location(speech: str, clinic: dict) -> str | None:
    """
    Return a location id from caller's speech, or None.
    Reads location names from clinic config so it works for any clinic.
    Falls back to Theorem-specific phonetic variants for robustness.
    """
    s = speech.lower()
    locations = clinic.get("locations", [])

    # First pass: match against each location's name from clinic config
    for loc in locations:
        loc_name = loc["name"].lower()
        if loc_name in s:
            return loc["id"]

    # Second pass: Theorem-specific phonetic variants
    # (Alcester is pronounced "ALL-ster" — speech recognition varies widely)
    _alcester_variants = (
        "alcester", "alce", "alchester", "alcest",
        "allster", "alster", "all ster", "all chester",
        "awlster", "olster", "ulster",
    )
    _redditch_variants = (
        "redditch", "reditch", "reddich", "red ditch",
        "red witch", "reddit",
    )

    if any(k in s for k in _alcester_variants) or s.strip() in ("1", "one"):
        return "alcester"
    if any(k in s for k in _redditch_variants) or s.strip() in ("2", "two"):
        return "redditch"

    return None


# --------------------------------------------------
# Insurance keyword detection
# --------------------------------------------------

_INSURANCE_KEYWORDS = (
    "insurance", "insured", "insurer", "claim", "policy",
    "cover", "covered", "bupa", "axa", "vitality", "aviva",
    "wpa", "health insurance", "private health", "simply health",
    "cigna", "benenden", "nuffield",
)


def _mentions_insurance(text: str) -> bool:
    t = text.lower()
    return any(kw in t for kw in _INSURANCE_KEYWORDS)

# COMPLETE /status ENDPOINT FOR app/routes/twilio.py
# Replace your entire @router.post("/status") function with this:

@router.post("/status")
async def status(request: Request) -> PlainTextResponse:
    """
    Called by Twilio when call ends.
    - Builds call summary
    - Sends actionable data to Google Sheets
    - Sends smart follow-up SMS based on call outcome
    """
    form = await request.form()
    call_sid = (form.get("CallSid") or "").strip()
    call_status = (form.get("CallStatus") or "").strip().lower()
    
    # Only process ended calls
    if call_status not in (
        "completed", "busy", "failed",
        "no-answer", "no_answer", "canceled", "cancelled",
    ):
        return PlainTextResponse("ok")
    
    if not call_sid:
        return PlainTextResponse("missing CallSid", status_code=400)

    # ── FIX #1 / #5: Atomic idempotency guard ────────────────────────────────
    # Twilio can fire /status more than once for the same call (e.g. network
    # retries).  Two parallel requests would both read call_summary_logged=False
    # and both write to Sheets / send SMS — a classic TOCTOU race.
    # Redis SET NX is atomic: only the first request wins the lock.
    status_lock_key = f"status_lock:{call_sid}"
    if not await acquire_once_lock(status_lock_key, ttl_seconds=300):
        logger.info("Duplicate /status for %s — skipping (lock held)", call_sid)
        return PlainTextResponse("already processing")
    # ─────────────────────────────────────────────────────────────────────────

    # Import required modules
    try:
        from app.tools.call_summary import build_call_summary
        from app.tools.actionable_summary import build_actionable_summary_row
        from app.tools.handoff import fire_and_forget_append_summary_row
        from app.notifications.smart_sms_router import send_smart_followup_sms
    except Exception as e:
        logger.error(f"STATUS IMPORT ERROR: {e}")
        return PlainTextResponse("ok")
    
    # Get session from Redis
    try:
        session = await get_session(call_sid) or {}
    except Exception:
        session = {}

    # ── T-2: do not summarise a call this route knows nothing about ──────────
    # 2026-08-05. Reproduced five times on the Theorem acceptance run:
    #
    #   📊 Row built — outcome=abandoned            name=None         phone=no
    #   📊 Row built — outcome=reached_confirmation name=Quentin Rook phone=yes
    #
    # Two paths summarise every call. This one fires FIRST, against a session
    # that the media-streams connection has not written back yet, so it reads
    # an empty session and concludes the call was abandoned. The connection
    # cleanup then writes the truthful row. Consequences, in order of how much
    # they hurt: Mark's sheet shows every call twice, once as abandoned; the
    # caller gets a second, wrong follow-up SMS; and `abandoned_call` is wired
    # IMMEDIATE/sms in app/obs/alerts.py, so an operator is paged about a
    # SUCCESSFUL booking. Alerts that fire on success get ignored within a
    # week, which is how a real one gets missed.
    #
    # Absence of data is not evidence of abandonment. On a `completed` call the
    # media-streams path owns the record and always runs its cleanup, so this
    # route stands down. Dropped statuses (busy/no-answer/failed) are NOT
    # skipped: no WebSocket ever opened for those, no cleanup will run, and the
    # missed-call row is the only record there will be.
    #
    # The `call_summary_logged` check is the guard connection.py's comment has
    # always claimed existed. It did not — this route set the flag and never
    # read it — so it covers the reverse ordering too.
    _session_has_substance = bool(
        (session.get("collected") or {})
        or session.get("conversation_history")
        or session.get("stream_sid")
        or session.get("selected_slot")
    )
    if session.get("call_summary_logged") or (
        call_status == "completed" and not _session_has_substance
    ):
        logger.info(
            "[T-2] /status standing down for %s (status=%s, already_logged=%s, "
            "session_substance=%s) — media-streams cleanup owns this record",
            call_sid, call_status,
            bool(session.get("call_summary_logged")), _session_has_substance,
        )
        return PlainTextResponse("ok")

    # Add Twilio metadata to session
    ended_at = datetime.utcnow().isoformat() + "Z"
    session["call_sid"] = call_sid
    session["call_status"] = call_status
    session["ended_at_utc"] = ended_at
    session["twilio_from"] = (form.get("From") or "").strip()
    session["twilio_to"] = (form.get("To") or "").strip()
    session["twilio_direction"] = (form.get("Direction") or "").strip()
    session["twilio_duration_sec"] = (form.get("CallDuration") or "").strip()
    session["twilio_timestamp"] = (form.get("Timestamp") or "").strip()
    
    try:
        session["twilio_status_payload"] = {k: str(v) for k, v in form.items()}
    except Exception:
        session["twilio_status_payload"] = {}
    
    session = _ensure_clinic_on_session(session, session.get("twilio_to"))

    # ========================================================================
    # AUTO-FLAG CALL DROPS WITH PARTIAL DATA AS MANUAL FOLLOWUP
    # If the call ended abnormally (not completed) but the caller already gave
    # us their name, reason, or phone number, flag for manual follow-up so the
    # clinic team gets a heads-up and an SMS is sent to the caller.
    # ========================================================================
    _dropped_statuses = {"busy", "failed", "no-answer", "no_answer", "canceled", "cancelled"}
    if call_status in _dropped_statuses and not session.get("manual_followup_needed"):
        _collected = session.get("collected") or {}
        _has_data  = any([
            (_collected.get("name") or "").strip(),
            (_collected.get("reason") or "").strip(),
            (_collected.get("phone") or "").strip(),
            session.get("twilio_from", "").strip(),
        ])
        if _has_data:
            session["manual_followup_needed"] = True
            session["manual_followup_reason"] = "call_dropped"
            logger.info(
                f"📵 Call dropped ({call_status}) with partial data — "
                f"flagged as manual followup for {call_sid}"
            )

    # ========================================================================
    # BUILD CALL SUMMARY & SEND TO GOOGLE SHEETS
    # ========================================================================
    summary = None
    try:
        # Build technical summary
        summary = build_call_summary(session)
        
        # ✅ CRITICAL: Pass raw session so actionable_summary can extract data
        summary["_raw_session"] = session
        
        # Convert to actionable row for Mark
        row = await build_actionable_summary_row(summary)
        
        # Send to Google Sheets (non-blocking)
        fire_and_forget_append_summary_row(row)
        
        logger.info(f"✅ Summary sent to Sheets: {call_sid}")
        
    except Exception as e:
        logger.error(f"❌ SUMMARY ERROR: {e}", exc_info=True)
    
    # ========================================================================
    # SEND SMART FOLLOW-UP SMS
    # ========================================================================
    try:
        if summary:
            # Smart router chooses appropriate template
            await send_smart_followup_sms(session=session, summary=summary)
        else:
            logger.warning(f"⚠️  No summary - skipping SMS for {call_sid}")
    
    except Exception as e:
        logger.error(f"⚠️  SMS ERROR: {e}", exc_info=True)
        # Don't fail status callback if SMS fails
    
    # ========================================================================
    # MARK AS LOGGED
    # ========================================================================
    try:
        session["call_summary_logged"] = True
        await save_session(call_sid, session)
    except Exception as e:
        logger.error(f"Failed to save session: {e}")
    
    return PlainTextResponse("ok")




# =========================================================
# MAIN VOICE ENTRYPOINT
# Improvement 1: Susie greeting + location question
# =========================================================

@router.api_route("/voice", methods=["GET", "POST", "HEAD"])
async def voice(request: Request):
    if request.method in ("HEAD", "GET"):
        return Response(status_code=200)

    # FORCE_TEST_SAY=1 — bypass all logic, return a hardcoded <Say> for audio path testing.
    if os.getenv("FORCE_TEST_SAY") == "1":
        logger.info("[twilio] FORCE_TEST_SAY active — returning hardcoded Say")
        _vr = VoiceResponse()
        _vr.say("Hello, this is a test.", language="en-GB")
        return xml(_vr)

    form        = await request.form()
    call_sid    = (form.get("CallSid") or "").strip()
    to_number   = (form.get("To")      or "").strip() or None
    from_number = (form.get("From")    or "").strip()  # caller ID — captured once, used in both paths

    _is_twilio_client = from_number.startswith("client:")
    logger.info(
        "[twilio] /voice call_sid=%s from=%s to=%s client=%s",
        call_sid, from_number, to_number, _is_twilio_client,
    )

    try:
        session = await get_session(call_sid) or {}
    except Exception:
        session = {}

    session = _init_session(session, call_sid)
    session = _ensure_clinic_on_session(session, to_number)

    # Persist inbound numbers so realtime.py can recover clinic routing even
    # if Twilio's customParameters are empty (Redis-backed fallback).
    if from_number:
        session["twilio_from"] = from_number
    if to_number:
        session["twilio_to"] = to_number

    # ── OpenAI Realtime path ──────────────────────────────────────────────
    # When REALTIME_ENABLED=true, hand the call off to the WebSocket bridge
    # instead of using the legacy Gather→/turn HTTP round-trip flow.
    # The media-stream handler loads the session from Redis itself, so we
    # only need to persist the initialised fields here.
    from app.config import REALTIME_ENABLED
    if REALTIME_ENABLED:
        base_url = os.getenv("BASE_URL", "").strip().rstrip("/")
        if not base_url:
            # Derive from request if BASE_URL not set (local dev)
            base_url = str(request.base_url).strip().rstrip("/")

        # Convert https:// → wss:// for the WebSocket URL
        ws_base = base_url.replace("https://", "wss://").replace("http://", "ws://")
        stream_url = f"{ws_base}/twilio/media-stream"

        await save_session(call_sid, session)

        vr = VoiceResponse()
        connect = Connect()
        stream = Stream(url=stream_url)
        stream.parameter(name="twilio_to",   value=to_number or "")
        stream.parameter(name="twilio_from", value=from_number)
        connect.append(stream)
        vr.append(connect)

        logger.info(
            "[realtime] /voice → Connect stream call_sid=%s stream_url=%s",
            call_sid, stream_url,
        )
        return xml(vr)
    # ── End Realtime path ─────────────────────────────────────────────────

    clinic    = get_clinic(session.get("clinic_id"))
    locations = clinic.get("locations", [])
    greeting  = _build_greeting(clinic)

    attach_status_callback(VoiceResponse(), request)  # register status callback

    vr = VoiceResponse()
    attach_status_callback(vr, request)

    # All clinics go directly to /turn — location is detected contextually.
    # Single-location clinics mark location resolved immediately.
    if not locations:
        session["location_selected"] = True
        session["selected_location"] = "default"

    # Seed conversation_history with the greeting so the LLM knows
    # it has already introduced itself and must NOT re-greet on its first reply.
    history = session.setdefault("conversation_history", [])
    if not history:
        history.append({"role": "assistant", "content": greeting})

    turn_url = _abs_url(request, "/twilio/turn")
    await _say(vr, greeting, request, gather_action=turn_url)

    await save_session(call_sid, session)

    # Fallback redirect if caller says nothing
    vr.redirect(_abs_url(request, "/twilio/voice"), method="POST")
    return xml(vr)


# =========================================================
# LOCATION SELECTION
# Improvement 1 + 2: Detect Alcester / Redditch, store in session
# =========================================================

@router.post("/location-select")
async def location_select(request: Request):
    form     = await request.form()
    call_sid = (form.get("CallSid") or "").strip()
    speech   = (form.get("SpeechResult") or "").strip()
    digits   = (form.get("Digits") or "").strip()
    user_said = speech or digits

    try:
        session = await get_session(call_sid) or {}
    except Exception:
        session = {}

    session = _init_session(session, call_sid)
    session = _ensure_clinic_on_session(session, (form.get("To") or "").strip() or None)
    clinic = get_clinic(session.get("clinic_id"))

    location_url = _abs_url(request, "/twilio/location-select")
    turn_url     = _abs_url(request, "/twilio/turn")

    location = _detect_location(user_said, clinic) if user_said else None

    if not location:
        # Could not detect — ask again politely (max 2 retries)
        miss = int(session.get("location_miss", 0)) + 1
        session["location_miss"] = miss
        await save_session(call_sid, session)

        vr_resp = VoiceResponse()
        if miss <= 2:
            await _say(
                vr_resp,
                "Sorry, I didn't quite catch that. "
                "Are you calling about the Alcester clinic or the Redditch clinic?",
                request,
                gather_action=location_url,
            )
        else:
            # After 2 failed attempts, default to Alcester and carry on
            session["selected_location"] = "alcester"
            session["location_selected"] = True
            await save_session(call_sid, session)
            await _say(
                vr_resp,
                "No problem — I'll put you through to our general line. "
                + HOW_CAN_I_HELP,
                request,
                gather_action=turn_url,
            )
        return xml(vr_resp)

    # ✅ Location detected
    session["selected_location"] = location
    session["location_selected"] = True
    session["location_miss"]     = 0
    await save_session(call_sid, session)

    # Confirm location then ask how we can help
    confirmation = _build_location_confirmation(location, clinic) + " " + HOW_CAN_I_HELP

    vr_resp = VoiceResponse()
    await _say(vr_resp, confirmation, request, gather_action=turn_url)
    vr_resp.redirect(_abs_url(request, "/twilio/voice"), method="POST")
    return xml(vr_resp)


# =========================================================
# TURN HANDLER
# Improvements 3-6: Location-aware triage, knowledge base,
# insurance detection, service explanations
# =========================================================

@router.api_route("/turn", methods=["POST"], name="turn")
async def turn(request: Request):
    vr   = VoiceResponse()
    form = await request.form()

    call_sid  = (form.get("CallSid")  or "").strip()
    to_number = (form.get("To")       or "").strip() or None
    digits    = (form.get("Digits")   or "").strip() or None
    speech    = (form.get("SpeechResult") or "").strip()
    unstable  = (form.get("UnstableSpeechResult") or "").strip()

    user_said = digits if digits else (speech or unstable)

    # Normalise simple yes/no
    if user_said:
        t = re.sub(r"\s+", " ", user_said.lower())
        if re.fullmatch(r"(yes|yeah|yep|ok|okay|confirm)[\W_]*", t):
            user_said = "yes"
        elif re.fullmatch(r"(no|nope|cancel|stop)[\W_]*", t):
            user_said = "no"

    from app.flows.triage_legacy import triage_turn
    from app.config import PHASE3_ENABLED

    try:
        session = await get_session(call_sid) or {}
    except Exception:
        session = {}

    session = _init_session(session, call_sid)
    session = _ensure_clinic_on_session(session, to_number)

    miss = int(session.get("miss_count", 0))

    turn_url = _abs_url(request, "/twilio/turn")

    # --------------------------------------------------
    # Passive location detection — silently store whichever clinic
    # the caller mentions in any turn, without interrupting the flow.
    # --------------------------------------------------
    if user_said and not session.get("location_selected"):
        _clinic_for_loc = get_clinic(session.get("clinic_id"))
        if _clinic_for_loc.get("locations"):
            _detected_loc = _detect_location(user_said, _clinic_for_loc)
            if _detected_loc:
                session["selected_location"] = _detected_loc
                session["location_selected"] = True
                logger.info("Location passively detected for %s: %s", call_sid, _detected_loc)

    # --------------------------------------------------
    # Handle empty input  (3-tier escalation with context-aware responses)
    # --------------------------------------------------
    if not user_said:
        from app.silence_handler import get_silence_response, get_silence_threshold, log_silence_event

        miss = int(session.get("miss_count", 0)) + 1
        session["miss_count"] = miss

        _current_state  = session.get("state", "TRIAGE")
        _consecutive    = int(session.get("consecutive_silence_count", 0))
        _clinic_name    = os.getenv("CLINIC_NAME", "the clinic")
        _silence_thresh = get_silence_threshold(_current_state)

        # Tier 3 — third silence: live transfer (unchanged)
        if miss >= 3:
            _clinic_obj2 = get_clinic(session.get("clinic_id"))
            _xfer_phone  = _clinic_obj2.get("transfer_phone") or TRANSFER_NUMBER_FALLBACK
            _xfer_silence_msg = (
                "I'm having a little trouble hearing you — "
                "let me transfer you to someone who can help."
            )
            log_silence_event(_current_state, 0.0, _xfer_silence_msg, _consecutive)
            _ch = session.get("conversation_history", [])
            _ch.append({"role": "assistant", "content": _xfer_silence_msg})
            session["conversation_history"] = _ch
            await save_session(call_sid, session)
            _append_transfer(vr, _xfer_silence_msg, request, _xfer_phone)
            return xml(vr)

        # Tiers 1 & 2 — context-aware silence response
        recovery = get_silence_response(_current_state, _consecutive, _clinic_name)

        # Manage consecutive_silence_count
        if _consecutive >= 1:
            # Second consecutive silence: callback suffix already in recovery — reset
            session["consecutive_silence_count"] = 0
        else:
            session["consecutive_silence_count"] = _consecutive + 1

        log_silence_event(_current_state, 0.0, recovery, _consecutive)

        _ch = session.get("conversation_history", [])
        _ch.append({"role": "assistant", "content": recovery})
        session["conversation_history"] = _ch
        await save_session(call_sid, session)
        vr.append(gather_speech(turn_url, recovery, timeout=int(_silence_thresh)))
        return xml(vr)

    session["miss_count"] = 0
    session["consecutive_silence_count"] = 0

    # Record utterance for tone detection (first two turns lock the tone)
    try:
        from app.tone_detector import ToneDetector
        _td = session.get("tone_detector")
        if not isinstance(_td, ToneDetector):
            _td = ToneDetector.from_dict(session.get("_tone_state") or {})
        _td.record_utterance(user_said)
        session["_tone_state"] = _td.to_dict()
        # Never store the live ToneDetector object — it is not JSON-serialisable.
        # get_tone_instruction_from_session() rebuilds it from _tone_state each turn.
    except Exception as _td_err:
        logger.warning("ToneDetector record failed (non-fatal): %r", _td_err)

    # --------------------------------------------------
    # Transfer shortcut — bypass LLM entirely for explicit human requests.
    # Saves ~4-5 seconds (two Anthropic round-trips) for the most common
    # "speak to someone" intent where there is zero ambiguity.
    # --------------------------------------------------
    _transfer_phrases = (
        "speak to someone", "speak to a person", "speak to a human",
        "speak to someone real", "talk to someone", "talk to a person",
        "talk to a human", "real person", "actual person",
        "put me through", "transfer me", "transfer please",
        "speak to mark", "talk to mark", "get mark",
        "speak to the team", "talk to the team",
        "speak to reception", "speak to staff",
        "i want a human", "need a human", "want to speak to someone",
    )
    if any(phrase in user_said.lower() for phrase in _transfer_phrases):
        _clinic_xfer  = get_clinic(session.get("clinic_id"))
        _xfer_phone   = _clinic_xfer.get("transfer_phone") or TRANSFER_NUMBER_FALLBACK
        _xfer_msg     = "Of course — let me put you straight through to the team."
        session        = _append_turn(session, "caller", user_said)
        session        = _append_turn(session, "assistant", _xfer_msg)
        await save_session(call_sid, session)
        _append_transfer(vr, _xfer_msg, request, _xfer_phone)
        return xml(vr)

    # --------------------------------------------------
    # Improvement 5: Insurance detection — intercept before triage
    # so we can flag it explicitly in session before triage_turn sees it
    # --------------------------------------------------
    if _mentions_insurance(user_said) and not session.get("insurance_flagged"):
        session["insurance_flagged"] = True
        # Let triage_turn handle the full insurance flow,
        # but prime the session so it knows to start the calm explanation
        session["insurance_info"]["mentioned"] = True
        session["insurance_info"]["trigger_explanation"] = True

    # --------------------------------------------------
    # Improvement 2: Location-aware keyword shortcuts
    # Handle hours / address / parking before hitting triage AI
    # so we get instant, accurate, location-specific answers
    # --------------------------------------------------
    quick_reply = _try_quick_answer(user_said, session)
    if quick_reply:
        session = _append_turn(session, "caller", user_said)
        session = _append_turn(session, "assistant", quick_reply)
        await save_session(call_sid, session)
        vr.append(gather_speech(turn_url, quick_reply))
        return xml(vr)

    # --------------------------------------------------
    # Main triage turn
    # session["selected_location"] is available to triage_turn
    # so all recommendations / availability checks are location-aware
    # --------------------------------------------------
    session = _append_turn(session, "caller", user_said)

    try:
        if PHASE3_ENABLED:
            import asyncio as _asyncio
            from app.flows.conversation import handle_turn
            from app.fast_path import resolve_fast_path

            _fp = resolve_fast_path(session, user_said)
            if _fp is not None:
                reply_text, session = _fp
            else:
                try:
                    reply_text, session = await _asyncio.wait_for(
                        handle_turn(user_said, session),
                        timeout=13.0,
                    )
                except _asyncio.TimeoutError:
                    logger.warning(
                        "handle_turn wall-clock timeout (call_sid=%s)", call_sid
                    )
                    reply_text = "Bear with me one moment — I'll get that sorted for you."
        else:
            reply_text, session = await triage_turn(user_said, session)
        session.pop("error_count", None)   # reset on success

        # Hard guard: if patient_type is already set but Claude still produced
        # the new/returning question, strip it before it reaches the caller.
        # This catches cases where the LLM ignores the inline prompt conditional.
        _pt = (session.get("collected") or {}).get("patient_type")
        if _pt and "been to us before" in reply_text.lower():
            _cleaned = re.sub(
                r"[.–—\-]?\s*(?:(?:And|and)\s+)?[Hh]ave you been to us before\??",
                "",
                reply_text,
            ).strip().rstrip(",").strip()
            if _cleaned:
                logger.warning(
                    "Stripped duplicate new/returning question from LLM reply "
                    "(patient_type=%s call_sid=%s)", _pt, call_sid
                )
                reply_text = _cleaned
    except Exception as e:
        logger.error("[twilio] TRIAGE ERROR call_sid=%s: %r", call_sid, e, exc_info=True)

        error_count = int(session.get("error_count", 0)) + 1
        session["error_count"] = error_count

        if error_count >= 2:
            # Second failure → enter manual capture mode
            from app.flows.triage_legacy import _build_manual_capture_message, MANUAL_CAPTURE
            from app.clinic_config import get_clinic as _get_clinic
            session["pre_error_state"] = session.get("state", "TRIAGE")
            session["state"] = MANUAL_CAPTURE
            _clinic = _get_clinic(session.get("clinic_id"))
            capture_msg = _build_manual_capture_message(session, _clinic)
            await save_session(call_sid, session)
            vr.append(gather_speech(turn_url, capture_msg))
        else:
            # First failure → retry once, preserve current state
            await save_session(call_sid, session)
            vr.append(
                gather_speech(turn_url, "Sorry, something went wrong. Let me try that again.")
            )
        return xml(vr)

    session = _append_turn(session, "assistant", reply_text)

    # Tier 3 escalation — triage signalled a live transfer
    if session.pop("request_transfer", False):
        _clinic_xfer = get_clinic(session.get("clinic_id"))
        _xfer_phone  = _clinic_xfer.get("transfer_phone") or TRANSFER_NUMBER_FALLBACK
        await save_session(call_sid, session)
        _append_transfer(vr, reply_text, request, _xfer_phone)
        return xml(vr)

    # Guard: never send an empty string to gather_speech — it creates a silent Gather
    if not reply_text or not reply_text.strip():
        logger.error("Empty reply_text — using fallback (call_sid=%s)", call_sid)
        reply_text = "Something went wrong on my end — could you repeat that?"

    logger.info("[twilio] /turn reply_text=%r call_sid=%s", reply_text[:200], call_sid)
    await save_session(call_sid, session)
    vr.append(gather_speech(turn_url, reply_text))
    return xml(vr)


# =========================================================
# TRANSFER STATUS CALLBACK
# Called by Twilio when a <Dial action=...> completes.
# If nobody answered, Susie re-engages the caller.
# =========================================================

@router.post("/transfer-status")
async def transfer_status(request: Request):
    """
    Twilio calls this URL when the <Dial> in _append_transfer finishes.

    DialCallStatus values:
      completed  — someone answered and the call has ended  → do nothing
      no-answer  — nobody picked up within the timeout      → re-engage
      busy       — line is busy                             → re-engage
      failed     — technical failure                        → re-engage
      canceled   — caller hung up before anyone answered    → do nothing
    """
    form        = await request.form()
    call_sid    = (form.get("CallSid")        or "").strip()
    dial_status = (form.get("DialCallStatus") or "").strip().lower()

    logger.info("transfer-status: call_sid=%s dial_status=%s", call_sid, dial_status)

    vr = VoiceResponse()

    # Call connected and ended normally — caller has gone, nothing to do
    if dial_status in ("completed", "canceled"):
        return xml(vr)

    # Nobody answered (no-answer, busy, failed) — bring Susie back
    try:
        session = await get_session(call_sid) or {}
    except Exception:
        session = {}

    session["transfer_attempted"]      = True
    session["transfer_failed_status"]  = dial_status

    # Notify the clinic that they missed a transfer — caller number front and centre
    try:
        from app.clinic_config import get_clinic
        from app.notifications.sms import send_sms
        _clinic        = get_clinic(session.get("clinic_id"))
        _transfer_phone = _clinic.get("transfer_phone", "")
        if _transfer_phone:
            _collected     = session.get("collected") or {}
            _caller_name   = _collected.get("name", "")
            _caller_number = session.get("twilio_from", "") or _collected.get("phone", "")
            _lines = ["📵 Missed patient transfer via Susie."]
            if _caller_number and not _caller_number.startswith("client:"):
                _lines.append(f"📱 Call back: {_caller_number}")
            if _caller_name:
                _lines.append(f"Name: {_caller_name}")
            await send_sms(to=_transfer_phone, message="\n".join(_lines))
    except Exception as e:
        logger.warning("Missed-transfer SMS failed (non-fatal): %r", e)

    turn_url = _abs_url(request, "/twilio/turn")

    re_engage = (
        "Looks like the team isn't available at the moment, but I'm here — "
        "did you want to book an appointment, or is there something else I can help with?"
    )

    await save_session(call_sid, session)
    await _say(vr, re_engage, request, gather_action=turn_url)
    return xml(vr)


@router.post("/transfer-miss")
async def transfer_miss(request: Request):
    """
    Action handler for the v3 Media Streams transfer <Dial> (_handle_transfer).

    Fires when that <Dial> finishes. On a missed transfer (no-answer/busy/
    failed) it notifies the clinic by SMS so they can call the patient back,
    then takes a voicemail. On answered/canceled it simply ends the call.

    Distinct from /transfer-status (the legacy HTTP flow, which re-engages the
    old Susie) — the v3 call has already left the Media Streams socket, so here
    we capture a message rather than try to resume the bot.
    """
    form        = await request.form()
    call_sid    = (form.get("CallSid")        or "").strip()
    dial_status = (form.get("DialCallStatus") or "").strip().lower()
    from_num    = (form.get("From")           or "").strip()

    logger.info("transfer-miss: call_sid=%s dial_status=%s", call_sid, dial_status)

    vr = VoiceResponse()

    # Mark answered (call ended) or caller hung up before pickup — nothing to do.
    if dial_status in ("completed", "canceled"):
        return xml(vr)

    # Missed (no-answer / busy / failed) — notify the clinic by SMS.
    try:
        session = await get_session(call_sid) or {}
    except Exception:
        session = {}

    try:
        from app.clinic_config import get_clinic
        from app.notifications.sms import send_sms
        _clinic         = get_clinic(session.get("clinic_id"))
        _transfer_phone = _clinic.get("transfer_phone", "")
        if _transfer_phone:
            _collected     = session.get("collected") or {}
            _caller_name   = _collected.get("name", "")
            _caller_number = (
                session.get("twilio_from", "")
                or from_num
                or _collected.get("phone", "")
            )
            _lines = ["📵 Missed patient transfer via Susie."]
            if _caller_number and not _caller_number.startswith("client:"):
                _lines.append(f"📱 Call back: {_caller_number}")
            if _caller_name:
                _lines.append(f"Name: {_caller_name}")
            await send_sms(to=_transfer_phone, message="\n".join(_lines))
            # "requested", not "notified": send_sms may divert this (see
            # redirect_staff_sms) and the diversion is invisible from here. The
            # [sms] line for this call names the number actually dialled — that
            # is the delivery record; this one is only the intent.
            logger.info(
                "transfer-miss: clinic notify requested for %s "
                "(the [sms] line for this call names the destination used)",
                _transfer_phone,
            )
    except Exception as e:
        logger.warning("transfer-miss SMS failed (non-fatal): %r", e)

    # No pickup — invite a voicemail. The recording POSTs to /twilio/voicemail-done.
    vr.say(
        "I'm sorry, the team isn't available to take your call right now. "
        "Please leave a short message after the tone and we'll get back to "
        "you as soon as we can.",
        language="en-GB",
    )
    vr.record(
        max_length=120,
        play_beep=True,
        finish_on_key="#",
        timeout=5,
        action=_abs_url(request, "/twilio/voicemail-done"),
        method="POST",
    )
    # Safety fallback if the <Record> action is somehow not reached.
    vr.hangup()
    return xml(vr)


@router.post("/voicemail-done")
async def voicemail_done(request: Request):
    """
    Record action callback for a missed-transfer voicemail. Twilio POSTs here
    once the caller finishes recording (presses #, hangs up, or 5s silence).
    SMSes the clinic the recording (link + duration), then thanks the caller.
    """
    form          = await request.form()
    call_sid      = (form.get("CallSid")           or "").strip()
    recording_url = (form.get("RecordingUrl")      or "").strip()
    duration      = (form.get("RecordingDuration") or "0").strip()
    from_num      = (form.get("From")              or "").strip()

    logger.info("voicemail-done: call_sid=%s duration=%s", call_sid, duration)

    try:
        _dur = int(duration)
    except ValueError:
        _dur = 0

    vr = VoiceResponse()

    if recording_url and _dur >= 1:
        try:
            session = await get_session(call_sid) or {}
        except Exception:
            session = {}
        try:
            from app.clinic_config import get_clinic
            from app.notifications.sms import send_sms
            _clinic         = get_clinic(session.get("clinic_id"))
            _transfer_phone = _clinic.get("transfer_phone", "")
            if _transfer_phone:
                _collected = session.get("collected") or {}
                _name      = _collected.get("name", "")
                _number    = session.get("twilio_from", "") or from_num
                _lines = ["🎙️ Voicemail from a missed transfer."]
                if _number and not _number.startswith("client:"):
                    _lines.append(f"📱 {_number}")
                if _name:
                    _lines.append(f"Name: {_name}")
                _lines.append(f"▶️ {recording_url}.mp3 ({_dur}s)")
                await send_sms(to=_transfer_phone, message="\n".join(_lines))
                logger.info("voicemail-done: voicemail SMS sent to %s", _transfer_phone)
        except Exception as e:
            logger.warning("voicemail-done SMS failed (non-fatal): %r", e)
        vr.say(
            "Thank you — we've got your message and will be in touch. Goodbye.",
            language="en-GB",
        )
    else:
        vr.say(
            "We didn't catch a message — please call us back. Goodbye.",
            language="en-GB",
        )

    vr.hangup()
    return xml(vr)


# =========================================================
# QUICK ANSWER HANDLER
# Improvement 2: Location-specific instant answers
# for hours, address, parking — no AI needed
# =========================================================

def _try_quick_answer(user_said: str, session: dict) -> str | None:
    """
    Return a canned, location-specific answer for common factual questions.
    Reads hours, address, parking, prices and policies from clinic config.
    Returns None if the question should go to triage_turn.
    """
    clinic      = get_clinic(session.get("clinic_id"))
    location_id = session.get("selected_location", "")
    locations   = clinic.get("locations", [])
    t           = user_said.lower()

    # Find location-specific config block if this clinic has multiple locations
    loc_cfg = next((l for l in locations if l["id"] == location_id), None)

    # If this is a location-specific question but we don't know which clinic yet,
    # ask before giving the wrong information.
    _location_specific_kws = (
        "hour", "open", "close", "when are you", "what time",
        "address", "where are you", "find you", "directions", "park",
        "bus", "train", "transport", "get there", "travel", "far from",
        "how far", "miles", "minutes away", "near", "station",
    )
    if (
        any(kw in t for kw in _location_specific_kws)
        and len(locations) > 1
        and loc_cfg is None
    ):
        loc_names = " or ".join(loc["name"] for loc in locations)
        return (
            f"Of course! Just so I give you the right details — "
            f"are you asking about our {loc_names} clinic?"
        )

    # Opening hours
    if any(kw in t for kw in ("hour", "open", "close", "when are you", "what time")):
        hours = (
            loc_cfg.get("hours_summary")
            if loc_cfg
            else clinic.get("hours_summary", "")
        )
        if hours:
            suffix = (
                f"or would you like to book an appointment at {loc_cfg['name']}?"
                if loc_cfg
                else "or would you like to book an appointment?"
            )
            return f"{hours} Is there anything else I can help you with, {suffix}"

    # Address / directions
    if any(kw in t for kw in ("address", "where are you", "location", "find you", "directions")):
        address = (
            loc_cfg.get("address")
            if loc_cfg
            else clinic.get("address", "")
        )
        if address:
            return (
                f"We're at {address}. "
                "Would you like to book an appointment, or is there anything else I can help with?"
            )

    # Parking
    if "park" in t:
        parking = (
            loc_cfg.get("parking")
            if loc_cfg
            else clinic.get("parking", "")
        )
        if parking:
            return f"{parking} Can I help you with anything else?"

    # Transport / getting here
    if any(kw in t for kw in ("bus", "train", "transport", "get there", "travel", "station", "far from", "how far", "miles")):
        transport = (
            loc_cfg.get("transport")
            if loc_cfg
            else clinic.get("transport", "")
        )
        if transport:
            return f"{transport} Is there anything else I can help with?"

    # What to wear / bring
    if any(kw in t for kw in ("wear", "bring", "prepare", "what should i")):
        what_to_bring = clinic.get("what_to_bring", "")
        if what_to_bring:
            return f"{what_to_bring} Anything else I can help with?"

    # Cancellation policy
    if any(kw in t for kw in ("cancellation policy", "cancel policy", "late fee", "no show")):
        policy = clinic.get("cancellation_policy", "")
        if policy:
            return f"{policy} Is there anything else I can help you with?"

    # Price / cost
    if any(kw in t for kw in ("cost", "price", "how much", "fee", "charge")):
        pricing = clinic.get("pricing_summary", "")
        if pricing:
            return f"{pricing} Would you like to book an appointment?"

    return None


# =========================================================
# BOOKING SERVICE FACTORY
# =========================================================

async def create_booking_service_for_clinic(
    clinic_id: str,
    redis_client,
):
    """Factory to create BookingService with Acuity provider."""
    from app.booking import BookingService
    from app.booking.providers.acuity import AcuityAdapter
    from app.clinic_config import get_acuity_config

    acuity_config = get_acuity_config(clinic_id)

    provider = AcuityAdapter(
        user_id=acuity_config["user_id"],
        api_key=acuity_config["api_key"],
        clinic_id=clinic_id,
    )

    return BookingService(
        provider=provider,
        redis_client=redis_client,
        clinic_id=clinic_id,
    )
# ============================================================================
# SMS TEST ENDPOINT (for testing SMS notifications)
# ============================================================================

@router.get("/test-sms")
async def test_sms_endpoint(phone: str = "+447870166861"):
    """
    Test endpoint to verify SMS is working.
    
    Usage: https://your-app.onrender.com/twilio/test-sms?phone=%2B447xxxxxxxxxx
    
    Returns: Success/failure message
    """
    try:
        from app.notifications.booking_sms import send_booking_confirmation
        from datetime import datetime, timedelta
        
        success = await send_booking_confirmation(
            patient_phone=phone,
            patient_name="Test Patient",
            appointment_time=datetime.now() + timedelta(days=2),
            location="Alcester",
            service="physiotherapy",
            practitioner="Mark",
        )
        
        return {
            "success": success,
            "message": f"SMS sent to {phone}" if success else "SMS failed - check logs",
            "phone": phone
        }
    
    except Exception as e:
        logger.error(f"Test SMS error: {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e),
            "message": "Check Render logs for details"
        }


# ---------------------------------------------------------------------------
# Stage 1: Inbound SMS webhook
# ---------------------------------------------------------------------------

_NAME_JUNK = {
    "yes", "no", "ok", "okay", "thanks", "thank you", "test",
    "hi", "hello", "yep", "nope", "sure", "great",
}

# Carrier opt-out keywords — suppress the automated patient acknowledgement for
# these (don't text someone who asked to stop). Deliberately does NOT include
# "cancel"/"end" — a patient texting "cancel" almost certainly means their
# appointment, which must still reach the practitioner.
_SMS_OPT_OUT = {"stop", "stopall", "unsubscribe", "quit"}


# Rough UK-postcode matcher — used only to decide whether a home-visit patient's
# reply looks like an address worth writing onto their calendar event.
_UK_POSTCODE_RE = re.compile(r"\b[A-Za-z]{1,2}\d[A-Za-z\d]?\s*\d[A-Za-z]{2}\b")

# Street-type words that mark a message as address content even without a
# postcode (so a split address — street line then postcode on a separate text —
# is still captured in full onto the calendar).
_ADDRESS_KEYWORDS = (
    " road", " street", " lane", " avenue", " ave", " close", " drive",
    " way", " court", " place", " terrace", " crescent", " flat",
    " apartment", " house", " gardens", " grove", " hill", " square", " row",
)


def _looks_like_address(body: str) -> bool:
    """True when an inbound text looks like part of a home-visit address:
    a UK postcode, OR a numbered line (a digit + at least two words), OR a
    street-type keyword. Bare acknowledgements ('thanks', 'yes please') do not
    match, so they still forward to the practitioner but are not written to the
    calendar."""
    low = f" {(body or '').lower().strip()} "
    if _UK_POSTCODE_RE.search(body or ""):
        return True
    if any(kw in low for kw in _ADDRESS_KEYWORDS):
        return True
    if any(c.isdigit() for c in (body or "")) and len((body or "").split()) >= 2:
        return True
    return False


_ADDR_LABEL = "Home visit address (via SMS):"


async def _attach_address_to_event(
    ctx: dict, address_body: str, norm_phone: str, finalize: bool = True,
) -> bool:
    """Best-effort: append a texted home-visit address onto the booking's calendar
    event and grow the stored description so a SPLIT address (street on one text,
    postcode on the next) accumulates in full rather than the last part
    overwriting the first. The first part is written under the label; later parts
    append as continuation lines. finalize=True (a postcode arrived → address
    complete) marks the context done so nothing further is appended; while
    finalize=False the window stays open for the remaining part(s). Non-fatal."""
    event_id = ctx.get("event_id")
    if not event_id or ctx.get("address_captured"):
        return False
    try:
        import asyncio as _asyncio
        from app.tools.receptionist_tools import _get_tokens
        from app.tools.calendar_google import update_event
        from app.storage.redis_store import set_recent_booking_context
        toks = await _get_tokens()
        if not toks:
            return False
        base = ctx.get("description") or ""
        if _ADDR_LABEL in base:
            new_desc = base + f"\n{address_body}"                 # continuation line
        else:
            new_desc = base + f"\n\n{_ADDR_LABEL} {address_body}"  # first part
        await _asyncio.to_thread(
            update_event, toks, event_id, None, new_desc, ctx.get("calendar_id") or "primary",
        )
        # Persist the GROWN description so the next part appends to it (never
        # overwrites); keep the window open until a postcode finalises it.
        await set_recent_booking_context(
            norm_phone,
            {**ctx, "description": new_desc,
             "address_captured": bool(finalize),
             "is_home_visit": (not finalize)},
        )
        logger.info(
            "[SMS_INBOUND] home-visit address %s to event %s",
            "written" if finalize else "part appended", event_id,
        )
        return True
    except Exception as _ee:
        logger.warning("[SMS_INBOUND] event address-attach failed (non-fatal): %r", _ee)
        return False


def _sms_relay_target(clinic: dict) -> str:
    """The number that receives a verbatim copy of every inbound text, in E.164.

    Resolution order: SMS_RELAY_TO env var (per-deployment override; set it to an
    empty string to switch the relay off entirely) → the clinic's `sms_relay_to`
    key. Returns "" when no relay is configured.
    """
    _env = os.getenv("SMS_RELAY_TO")
    raw = _env if _env is not None else (clinic.get("sms_relay_to") or "")
    return raw.replace(" ", "").strip()


async def _relay_inbound_sms(
    *, sender: str, body: str, clinic: dict, send_sms,
) -> None:
    """Copy an inbound patient text to the oversight relay number.

    Runs for EVERY inbound message, on both branches of the webhook — the
    in-flow name confirmation as well as the general path — so the relay is a
    complete record of what patients text the line, not just the subset that
    reaches a human today. Entirely non-fatal: a relay failure must never change
    what the patient or the practitioner receives.

    Two things it deliberately does not do:
      * It does not relay a message sent FROM the relay number. The copy arrives
        from the clinic's own Twilio line, so replying to it lands right back on
        this webhook — without this guard, the relay would echo itself.
      * It does not fire when the relay number IS the practitioner's transfer
        number. That person already receives the (richer, booking-labelled)
        forward from _handle_general_inbound_sms; a second copy would be noise.
    """
    relay_to = _sms_relay_target(clinic)
    if not relay_to:
        return

    staff_phone = (clinic.get("transfer_phone") or "").replace(" ", "").strip()
    if relay_to == staff_phone:
        logger.info("[SMS_RELAY] relay number is the practitioner number — skipping copy")
        return

    if (sender or "").replace(" ", "").strip() == relay_to:
        logger.info("[SMS_RELAY] sender is the relay number — skipping (no echo)")
        return

    sms_name = (clinic.get("sms_name") or clinic.get("display_name")
                or clinic.get("clinic_name") or "the clinic")
    msg = (f"📨 Copy — text to {sms_name}\n"
           f"From: {sender}\n"
           f"\"{body}\"\n"
           f"(monitoring copy — the patient does not see replies to this)")
    try:
        await send_sms(to=relay_to, message=msg)
        logger.info("[SMS_RELAY] inbound copied to ***%s", relay_to[-4:])
    except Exception as _re:
        logger.warning("[SMS_RELAY] relay copy failed (non-fatal): %r", _re)


async def _handle_general_inbound_sms(
    *, sender: str, norm_phone: str, body: str, message_sid: str,
    clinic: dict, send_sms,
) -> None:
    """
    A general inbound patient text (not an in-flow name reply). NEVER dropped:
    forward it to the clinic's practitioner so a human can respond, log it, and
    send the patient a light acknowledgement. Every step is non-fatal and
    clinic-aware. If the sender recently booked (any appointment type), the
    forward is labelled with that booking; a home-visit patient replying with an
    address gets it written onto their calendar event.
    """
    sms_name        = (clinic.get("sms_name") or clinic.get("display_name")
                       or clinic.get("clinic_name") or "the clinic")
    practitioner    = clinic.get("practitioner") or "the team"
    staff_phone     = (clinic.get("transfer_phone") or "").strip()
    reception_phone = (clinic.get("phone") or "").strip()
    _is_opt_out     = body.strip().lower() in _SMS_OPT_OUT

    # Loop guard: never process a text that came FROM one of our own numbers.
    # The relay number counts as one of ours: relay copies are sent from the
    # clinic's Twilio line, so a reply to that thread arrives here. Forwarding
    # it to the practitioner would push an operator's aside at the patient's
    # clinician, and acking it would text the operator back.
    _own = {p.replace(" ", "") for p in
            (staff_phone, reception_phone, _sms_relay_target(clinic)) if p}
    if norm_phone and norm_phone.replace(" ", "") in _own:
        logger.info("[SMS_INBOUND] sender is a clinic number — skipping forward/ack")
        return

    # Context: did this number recently book? Label the forward accordingly.
    ctx = None
    try:
        from app.storage.redis_store import get_recent_booking_context
        ctx = await get_recent_booking_context(norm_phone)
    except Exception:
        ctx = None

    fwd_msg = None
    ack_msg = None
    if ctx:
        _name    = ctx.get("name") or "A patient"
        _slot    = ctx.get("slot") or ""
        _service = ctx.get("service") or "their appointment"
        _is_home = bool(ctx.get("is_home_visit"))
        if _is_home and _looks_like_address(body):
            # Looks like (part of) the home-visit address → forward labelled +
            # write to event. A postcode marks the address complete (finalise);
            # a street-only part keeps the window open so the postcode text that
            # follows appends rather than the last part overwriting the first.
            _has_postcode = bool(_UK_POSTCODE_RE.search(body))
            fwd_msg = (f"🏠 Home-visit address from {_name} (visit {_slot}):\n"
                       f"\"{body}\"\nReply to them on {sender}.")
            ack_msg = (f"Got it, thanks — we've passed your address to {practitioner} "
                       f"for your home visit. — {sms_name}")
            await _attach_address_to_event(ctx, body, norm_phone, finalize=_has_postcode)
        elif _is_home:
            fwd_msg = (f"New text from {_name} (home visit {_slot}):\n"
                       f"\"{body}\"\nReply to them on {sender}.")
        else:
            fwd_msg = (f"New text from {_name} (booked {_service}, {_slot}):\n"
                       f"\"{body}\"\nReply to them on {sender}.")
    if fwd_msg is None:
        fwd_msg = (f"New text to {sms_name} from {sender}:\n"
                   f"\"{body}\"\nReply to them directly on {sender}.")

    # 1. Forward to the practitioner so a human can respond.
    if staff_phone:
        try:
            await send_sms(to=staff_phone, message=fwd_msg)
            logger.info("[SMS_INBOUND] forwarded to practitioner ***%s", staff_phone[-4:])
        except Exception as _fe:
            logger.warning("[SMS_INBOUND] forward-to-practitioner failed (non-fatal): %r", _fe)
    else:
        logger.warning("[SMS_INBOUND] no transfer_phone configured — inbound text NOT forwarded")

    # 2. Log to the Sheets Messages tab (best-effort; tab may not exist yet).
    try:
        import asyncio as _asyncio
        from app.tools.handoff import send_to_sheet
        await _asyncio.to_thread(
            send_to_sheet, "", sender, "inbound_sms", body, message_sid, sms_name,
        )
    except Exception as _le:
        logger.warning("[SMS_INBOUND] sheet log failed (non-fatal): %r", _le)

    # 3. Light acknowledgement to the patient — no timeframe promise; a phone-
    #    first nudge for generic texts. Suppressed for opt-out keywords.
    if _is_opt_out:
        logger.info("[SMS_INBOUND] opt-out keyword — forwarded but no auto-ack")
        return
    if ack_msg is None:
        ack_msg = "Thanks — we've received your message and passed it to the team."
        if reception_phone:
            ack_msg += f" For bookings or anything urgent, please call us on {reception_phone}."
        ack_msg += f" — {sms_name}"
    try:
        await send_sms(to=norm_phone, message=ack_msg)
        logger.info("[SMS_INBOUND] ack sent to patient ***%s",
                    norm_phone[-4:] if norm_phone else "????")
    except Exception as _ae:
        logger.warning("[SMS_INBOUND] patient ack failed (non-fatal): %r", _ae)


# ── Call-mode toggle over SMS ───────────────────────────────────────────────
# A clinic texts OFF to their own Susie number and calls ring their phone first
# until midnight; ON puts Susie back in front. See app/clinic_call_mode.py.

# Exact match on the whole body, case-insensitive. Substring matching is banned:
# "can you turn it off for tomorrow" from the practitioner must reach the clinic
# as an ordinary text, not silently reroute their phone.
#
# STOP is deliberately absent. _SMS_OPT_OUT above is the carrier-level opt-out
# set that Twilio itself intercepts — reusing one of those words would be both
# broken and a compliance problem.
_CALL_MODE_COMMANDS = {
    "off": "human_first",
    "susie off": "human_first",
    "front desk": "human_first",
    "on": "ai_first",
    "susie on": "ai_first",
    "status": "status",
    "susie status": "status",
}


def _parse_call_mode_command(body: str) -> "str | None":
    """Return "human_first" / "ai_first" / "status", or None.

    `Optional` is not imported in this module; the annotation is a string under
    `from __future__ import annotations`, but naming an undefined symbol would
    still break anything that resolves hints at runtime.
    """
    return _CALL_MODE_COMMANDS.get(" ".join((body or "").split()).lower())


def _call_mode_authorised_numbers(clinic: dict) -> set:
    """The numbers allowed to toggle this clinic's call routing.

    NOTE the `operational` fallback: get_clinic() flattens some keys to the top
    level but NOT owner_notification_sms, which reads back as None. Written as
    the build plan specified — top level only — that candidate would silently
    never match, and a clinic whose owner number differs from transfer_phone
    would find the toggle simply did not work for them.
    """
    op = clinic.get("operational") or {}
    candidates = (
        (clinic.get("call_overflow") or {}).get("dial_phone"),
        clinic.get("transfer_phone"),
        op.get("transfer_phone"),
        clinic.get("owner_notification_sms"),
        op.get("owner_notification_sms"),
    )
    from app.flows.triage_legacy import normalize_phone
    out = set()
    for c in candidates:
        c = (c or "").strip()
        if not c:
            continue  # empty must never match an empty sender
        out.add((normalize_phone(c) or c).replace(" ", ""))
    return out


def _sender_is_authorised(sender: str, clinic: dict) -> bool:
    from app.flows.triage_legacy import normalize_phone
    s = (normalize_phone(sender) or (sender or "")).strip().replace(" ", "")
    return bool(s) and s in _call_mode_authorised_numbers(clinic)


# ── Replying to a MESSAGING webhook ─────────────────────────────────────────
# Twilio parses the BODY of a messaging webhook response as TwiML and delivers
# whatever <Message> it finds. That is the opposite of the voice `/status`
# callback above, where the body really is ignored — and `return
# PlainTextResponse("ok")` was copied from there.
#
# Cost, measured on the Joint Venture line 2026-08-20: a patient texted
# "speak to marcus", got the intended acknowledgement, and then a second text
# reading just "ok". Nothing in this repo sends that — it is this webhook's own
# HTTP body coming back at the patient.
#
# Every reply on these paths is already sent out-of-band via the REST API
# (`send_sms`), so the correct TwiML answer is an EMPTY document: "received,
# say nothing". Returning 204 would also work, but an empty <Response/> is what
# Twilio documents and what `xml()` above already models.
_EMPTY_TWIML = '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'


def _twiml_noop() -> PlainTextResponse:
    """An empty TwiML document: acknowledge the webhook, send the caller nothing.

    Use this for EVERY return on an inbound-SMS path. A bare string body here is
    not inert — Twilio speaks it to the patient.
    """
    return PlainTextResponse(_EMPTY_TWIML, media_type="application/xml")


async def _handle_call_mode_command(
    *, cmd: str, sender: str, clinic: dict, clinic_id: str, to_number: str,
) -> PlainTextResponse:
    """Apply a call-mode command and confirm it. Always returns 200.

    The confirmation states the resulting CONDITION rather than acknowledging
    the command, so the clinic never has to remember which way the switch
    points.
    """
    from app.clinic_call_mode import (
        set_mode, clear_mode, current_mode, MODE_HUMAN_FIRST,
    )
    from app.notifications.sms import send_sms

    _FAIL = ("Sorry — I couldn't change that just now. Calls are still being "
             "answered as normal. Please try again shortly.")

    if cmd == "status":
        state = await current_mode(clinic_id, clinic)
        reply = ("Front desk mode, until midnight tonight."
                 if state["mode"] == MODE_HUMAN_FIRST
                 else "I'm answering all calls.")
        await _send_call_mode_reply(send_sms, sender, to_number, reply)
        return _twiml_noop()

    prior = await current_mode(clinic_id, clinic)
    payload = await set_mode(clinic_id, cmd, sender)
    if payload is None:
        # Redis unavailable — the override was NOT stored. Say so; never claim
        # a routing change that did not happen.
        await _send_call_mode_reply(send_sms, sender, to_number, _FAIL)
        return _twiml_noop()

    reply = (
        "Front desk mode on — calls will ring your phone first, and I'll pick "
        "up anything you miss. Back to normal at midnight, or text ON."
        if cmd == MODE_HUMAN_FIRST
        else "Back on — I'm answering all calls now."
    )
    sid = await _send_call_mode_reply(send_sms, sender, to_number, reply)
    if not sid:
        # A toggle that silently succeeds is worse than no toggle: the clinic
        # does not know whether their phone is about to ring. Revert.
        logger.error(
            "[call_mode] confirmation SMS returned no SID — reverting %s to %r",
            clinic_id, prior.get("mode"),
        )
        if prior.get("source") == "override":
            await set_mode(clinic_id, prior["mode"], sender)
        else:
            await clear_mode(clinic_id)
    return _twiml_noop()


async def _send_call_mode_reply(send_sms, sender: str, to_number: str, text: str):
    """Reply to the SENDER, not a configured clinic number — a locum toggling
    from their own phone must still get the confirmation. Returns the Twilio
    SID, or None if nothing was sent."""
    try:
        return await send_sms(to=sender, message=text, from_number=to_number)
    except Exception as exc:
        logger.error("[call_mode] confirmation SMS failed: %r", exc)
        return None


@router.post("/sms/inbound")
async def sms_inbound(request: Request) -> PlainTextResponse:
    """
    Receive inbound SMS from Twilio and process pending name confirmations.

    Flow:
      1. Log sender / body / SID.
      2. Normalize phone and fetch pending_name record.
      3. Validate the reply as a full name (≥2 words, not junk).
      4. On valid name: PUT to Acuity, mark record complete.
      5. On invalid name: send clarification SMS.
      Always returns 200.
    """
    form = await request.form()
    sender = form.get("From") or "unknown"
    body = (form.get("Body") or "").strip()
    message_sid = form.get("MessageSid") or ""

    logger.info(
        "[SMS_INBOUND] from=%r sid=%r body=%r",
        sender,
        message_sid,
        body,
    )

    # Idempotency: Twilio can retry the inbound webhook — process each message
    # once so we never double-forward to staff or double-ack the patient.
    if message_sid and not await acquire_once_lock(
        f"sms_inbound:{message_sid}", ttl_seconds=600
    ):
        logger.info("[SMS_INBOUND] duplicate sid=%r — skipping", message_sid)
        return _twiml_noop()

    # Resolve which clinic this number belongs to (jv/theorem/…) so forwarding
    # and branding are clinic-aware.
    _to_number = form.get("To") or ""
    _clinic_id = clinic_id_from_twilio_to(_to_number)
    _clinic = get_clinic(_clinic_id) or {}

    # ── Call-mode toggle ────────────────────────────────────────────────────
    # Placement is load-bearing in both directions:
    #   AFTER the idempotency lock above — a Twilio webhook retry must not
    #   double-send the confirmation or double-flip the routing;
    #   BEFORE the oversight relay and the pending-name branch below — a
    #   command is an instruction to us, not a patient text, and must never be
    #   forwarded to the practitioner as one.
    #
    # The authorisation check runs BEFORE dispatch, not after, so an
    # unauthorised sender's "ON" falls straight through to the existing patient
    # path unchanged. A patient who happens to text "on" sees no difference.
    #
    # A command from an authorised sender wins over a pending name confirmation.
    # Rare, and the right way round: the practitioner is asking for their phone
    # to ring, which is more urgent than a name capture that can be chased again.
    _cmd = _parse_call_mode_command(body)
    if _cmd and _sender_is_authorised(sender, _clinic):
        logger.info(
            "[call_mode] command %r from authorised sender ***%s for clinic %r",
            _cmd, (sender or "")[-4:], _clinic_id,
        )
        return await _handle_call_mode_command(
            cmd=_cmd, sender=sender, clinic=_clinic,
            clinic_id=_clinic_id, to_number=_to_number,
        )

    try:
        from app.flows.triage_legacy import normalize_phone
        from app.storage.redis_store import (
            get_pending_name_confirmation,
            complete_pending_name_confirmation,
        )
        from app.notifications.sms import send_sms

        norm_phone = normalize_phone(sender)

        # Oversight relay FIRST, before the pending-name branch: every inbound
        # text gets copied, including the name replies that never reach a human
        # otherwise. Deliberately not inside either branch — one call site, one
        # copy per message (the sid lock above already made this idempotent).
        await _relay_inbound_sms(
            sender=sender, body=body, clinic=_clinic, send_sms=send_sms,
        )

        pending = await get_pending_name_confirmation(norm_phone)

        if not pending:
            # No in-flow name reply pending — this is a general patient text.
            # Do NOT drop it: forward to the practitioner, log it, acknowledge.
            await _handle_general_inbound_sms(
                sender=sender,
                norm_phone=norm_phone,
                body=body,
                message_sid=message_sid,
                clinic=_clinic,
                send_sms=send_sms,
            )
            return _twiml_noop()

        # ── Name validation ──────────────────────────────────────────────
        # Accept single-word replies for first-name-only SMS requests
        # (e.g. when voice capture failed and we asked "reply with your first name").
        # Minimum: 1 word, 2+ characters, not a known junk word.
        words = body.split()
        is_junk = body.lower() in _NAME_JUNK
        if len(words) < 1 or is_junk or len(body) < 2:
            logger.info(
                "[SMS_INBOUND] invalid name reply=%r phone=%r — sending clarification",
                body,
                norm_phone,
            )
            try:
                await send_sms(
                    to=norm_phone,
                    message=(
                        "Sorry, we didn't catch that. "
                        "Please reply with your first name (and surname if you'd like to include it)."
                    ),
                )
            except Exception as _ce:
                logger.warning(
                    "[SMS_INBOUND] clarification SMS failed (non-fatal): %r", _ce
                )
            return _twiml_noop()

        # ── Valid name reply — update Acuity ─────────────────────────────
        # Single-word reply: first name only — update firstName, leave surname intact.
        # Multi-word reply: treat as "FirstName Surname(s)".
        full_name = body
        first_name = words[0]
        last_name = " ".join(words[1:])
        appointment_id = pending.get("appointment_id", "")

        logger.info(
            "[SMS_INBOUND] valid name=%r (words=%d) appt_id=%r phone=%r — updating Acuity",
            full_name,
            len(words),
            appointment_id,
            norm_phone,
        )

        # Build Acuity payload: only include lastName when explicitly provided
        _acuity_name_payload: dict = {"firstName": first_name}
        if last_name:
            _acuity_name_payload["lastName"] = last_name

        try:
            from app.tools.receptionist_tools import _get_acuity_adapter
            adapter = _get_acuity_adapter()
            if adapter and appointment_id:
                await adapter._request_with_retry(
                    "PUT",
                    f"/appointments/{appointment_id}",
                    json=_acuity_name_payload,
                    allow_retry=False,
                )
                logger.info(
                    "[SMS_INBOUND] Acuity updated: appt_id=%r name=%r",
                    appointment_id,
                    full_name,
                )
                await complete_pending_name_confirmation(norm_phone)
                logger.info(
                    "[SMS_INBOUND] pending record completed: phone=%r",
                    norm_phone,
                )
            else:
                logger.warning(
                    "[SMS_INBOUND] skipping Acuity update: adapter=%r appt_id=%r",
                    adapter,
                    appointment_id,
                )
        except Exception as _ae:
            logger.error(
                "[SMS_INBOUND] Acuity update failed (non-fatal): appt_id=%r err=%r",
                appointment_id,
                _ae,
            )

    except Exception as _outer:
        logger.error("[SMS_INBOUND] processing error (non-fatal): %r", _outer)

    return _twiml_noop()
