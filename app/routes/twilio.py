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
    # arrives as HTTP internally, so we must trust the forwarded headers.
    base = os.getenv("BASE_URL", "").rstrip("/")
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

    # Caller-ID fallback for the end-of-call follow-up SMS.
    # Twilio's /status callback always carries the caller's From number, but
    # the smart_sms router only uses it when phone_from_twilio is set — a flag
    # armed mid-call ONLY when caller-ID is captured at call start, and lost
    # when the caller declines "use this number".  So an incomplete call (no
    # confirmed booking phone) fell through to "No phone number" even though we
    # know the number they rang from.  Re-arm the flag here from the callback's
    # From so the follow-up texts that number.  Skips withheld / SIP callers
    # (From not starting "+") and any number the caller explicitly rejected in
    # favour of a different one (phone_confirmed is False).
    _from_raw = session["twilio_from"]
    if _from_raw.startswith("+") and session.get("phone_confirmed") is not False:
        session["phone_from_twilio"] = True
        if not session.get("twilio_from_local"):
            session["twilio_from_local"] = (
                "0" + _from_raw[3:] if _from_raw.startswith("+44") else _from_raw
            )

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
            logger.info("transfer-miss: clinic notified at %s", _transfer_phone)
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


# Inbound-SMS opt-out keywords — the text is still forwarded to staff, but we
# send no auto-acknowledgement back (carrier compliance).
_SMS_OPT_OUT = {"stop", "stopall", "unsubscribe", "quit"}


async def _handle_general_inbound_sms(
    *, sender: str, norm_phone: str, body: str, message_sid: str,
    clinic: dict, send_sms,
) -> None:
    """
    A general inbound patient text (NOT an in-flow name reply). Never dropped:
    forward it to the clinic's staff number so a human can respond, log it to
    Sheets, and send the patient a light acknowledgement. Every step is
    clinic-aware and independently non-fatal.
    """
    sms_name        = (clinic.get("sms_name") or clinic.get("display_name")
                       or "the clinic")
    staff_phone     = (clinic.get("transfer_phone") or "").strip()
    reception_phone = (clinic.get("phone") or "").strip()
    _is_opt_out     = body.strip().lower() in _SMS_OPT_OUT

    # Loop guard: never forward/ack a text that came FROM one of our own numbers.
    _own = {p.replace(" ", "") for p in (staff_phone, reception_phone) if p}
    if norm_phone and norm_phone.replace(" ", "") in _own:
        logger.info("[SMS_INBOUND] sender is a clinic number — skipping forward/ack")
        return

    # If this number recently booked, label the forward with their appointment.
    ctx = None
    try:
        from app.storage.redis_store import get_recent_booking_context
        ctx = await get_recent_booking_context(norm_phone)
    except Exception:
        ctx = None

    if ctx:
        _n    = ctx.get("name") or "A patient"
        _svc  = ctx.get("service") or "their appointment"
        _slot = ctx.get("slot") or ""
        fwd_msg = (f"New text from {_n} (booked {_svc}, {_slot}):\n"
                   f"\"{body}\"\nReply to them on {sender}.")
    else:
        fwd_msg = (f"New text to {sms_name} from {sender}:\n"
                   f"\"{body}\"\nReply to them directly on {sender}.")

    # 1. Forward to staff so a human can respond.
    if staff_phone:
        try:
            await send_sms(to=staff_phone, message=fwd_msg)
            logger.info("[SMS_INBOUND] forwarded to staff ***%s", staff_phone[-4:])
        except Exception as _fe:
            logger.warning("[SMS_INBOUND] forward-to-staff failed (non-fatal): %r", _fe)
    else:
        logger.warning("[SMS_INBOUND] no transfer_phone configured — inbound text NOT forwarded")

    # 2. Log to Sheets (best-effort; non-fatal).
    try:
        import asyncio as _asyncio
        from app.tools.handoff import send_to_sheet
        await _asyncio.to_thread(
            send_to_sheet, "", sender, "inbound_sms", body, message_sid, sms_name,
        )
    except Exception as _le:
        logger.warning("[SMS_INBOUND] sheet log failed (non-fatal): %r", _le)

    # 3. Light acknowledgement to the patient — no timeframe promise, phone-first
    #    nudge. Suppressed for opt-out keywords.
    if _is_opt_out:
        logger.info("[SMS_INBOUND] opt-out keyword — forwarded but no auto-ack")
        return
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
    # once so we never double-forward to staff, double-ack, or double-PUT Acuity.
    if message_sid and not await acquire_once_lock(
        f"sms_inbound:{message_sid}", ttl_seconds=600
    ):
        logger.info("[SMS_INBOUND] duplicate sid=%r — skipping", message_sid)
        return PlainTextResponse("ok")

    # Resolve which clinic this number belongs to so forwarding/branding is
    # clinic-aware (falls back to {} → safe generic copy).
    _clinic = get_clinic(clinic_id_from_twilio_to(form.get("To") or "")) or {}

    try:
        from app.flows.triage_legacy import normalize_phone
        from app.storage.redis_store import (
            get_pending_name_confirmation,
            complete_pending_name_confirmation,
        )
        from app.notifications.sms import send_sms

        norm_phone = normalize_phone(sender)
        pending = await get_pending_name_confirmation(norm_phone)

        if not pending:
            # No in-flow name reply pending — this is a general patient text.
            # Do NOT drop it: forward to staff, log it, acknowledge.
            await _handle_general_inbound_sms(
                sender=sender, norm_phone=norm_phone, body=body,
                message_sid=message_sid, clinic=_clinic, send_sms=send_sms,
            )
            return PlainTextResponse("ok")

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
            return PlainTextResponse("ok")

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

    return PlainTextResponse("ok")
