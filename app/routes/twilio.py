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
from twilio.twiml.voice_response import VoiceResponse, Gather, Dial

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
    - All clinics: simple greeting asking how we can help.
      Location is detected contextually during the conversation.
    """
    # Custom greeting overrides everything (set in clinic config)
    if clinic.get("greeting"):
        return clinic["greeting"]

    name = clinic.get("display_name", "the clinic")
    return f"Hi there! This is {AI_NAME}, {name}'s AI receptionist. How can I help you today?"


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
    return PlainTextResponse(str(resp), media_type="application/xml")


def _abs_url(request: Request, path: str) -> str:
    base = str(request.base_url).rstrip("/")
    if not path.startswith("/"):
        path = "/" + path
    return base + path


def gather_speech(
    action_url: str,
    prompt: str | None = None,
    timeout: int = 10,
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
    # Miss counter
    session.setdefault("miss_count", 0)
    return session


def safe_error_response(request: Request) -> PlainTextResponse:
    """Return a graceful TwiML error response — never crash the call."""
    vr = VoiceResponse()
    turn_url = _abs_url(request, "/twilio/turn")
    vr.append(
        gather_speech(
            turn_url,
            "I'm sorry, something went wrong on my end. "
            "Please bear with me — let me try that again.",
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
        from app.routes.tts_eleven import tts_eleven_url, TTSReq
        data = tts_eleven_url(TTSReq(text=text), request)
        audio_url = data["audio_url"]
        if gather_action:
            g = Gather(
                input="speech dtmf",
                action=gather_action,
                method="POST",
                language="en-GB",
                speech_timeout="auto",
                timeout=10,
                action_on_empty_result=True,
                barge_in=True,
            )
            g.play(audio_url)
            vr.append(g)
        else:
            vr.play(audio_url)
    except Exception as e:
        print("TTS_ELEVEN ERROR:", repr(e))
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

    form      = await request.form()
    call_sid  = (form.get("CallSid") or "").strip()
    to_number = (form.get("To")      or "").strip() or None

    try:
        session = await get_session(call_sid) or {}
    except Exception:
        session = {}

    session = _init_session(session, call_sid)
    session = _ensure_clinic_on_session(session, to_number)

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
    # Handle empty input  (3-tier escalation)
    # --------------------------------------------------
    if not user_said:
        miss = int(session.get("miss_count", 0)) + 1
        session["miss_count"] = miss
        await save_session(call_sid, session)

        _clinic_obj = get_clinic(session.get("clinic_id"))
        _cur_state  = session.get("state", "TRIAGE")

        # Tier 1 — first silence
        if miss == 1:
            if _cur_state == "TRIAGE":
                vr.append(gather_speech(turn_url, _build_reintro(_clinic_obj)))
            else:
                vr.append(gather_speech(turn_url, "Sorry — I didn't catch that. Could you say that again?"))
            return xml(vr)

        # Tier 2 — second silence: explicit menu
        if miss == 2:
            vr.append(gather_speech(turn_url, _build_menu()))
            return xml(vr)

        # Tier 3 — third silence: live transfer
        _clinic_obj2 = get_clinic(session.get("clinic_id"))
        _xfer_phone  = _clinic_obj2.get("transfer_phone") or TRANSFER_NUMBER_FALLBACK
        _append_transfer(
            vr,
            "Not to worry — let me put you through to the team right now. Please hold.",
            request,
            _xfer_phone,
        )
        return xml(vr)

    session["miss_count"] = 0

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
            from app.flows.conversation import handle_turn
            reply_text, session = await handle_turn(user_said, session)
        else:
            reply_text, session = await triage_turn(user_said, session)
        session.pop("error_count", None)   # reset on success
    except Exception as e:
        print("TRIAGE ERROR:", repr(e))

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
        "I'm sorry, the team doesn't seem to be available right now. "
        "But I'm here and I can probably help — "
        "did you want to book an appointment, or is there something else I can help with?"
    )

    await save_session(call_sid, session)
    await _say(vr, re_engage, request, gather_action=turn_url)
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
