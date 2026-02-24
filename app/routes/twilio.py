# app/routes/twilio.py
from __future__ import annotations

import re
import uuid
from datetime import datetime
from fastapi import Request
from fastapi.responses import Response
import logging
from app.notifications.smart_sms_router import send_smart_followup_sms
logger = logging.getLogger(__name__)
from datetime import datetime
from app.tools.handoff import fire_and_forget_append_summary_row
from fastapi import APIRouter, Request, Response
from fastapi.responses import PlainTextResponse
from twilio.twiml.voice_response import VoiceResponse, Gather

from app.storage.redis_store import get_session, save_session
from app.clinic_config import clinic_id_from_twilio_to, get_clinic

router = APIRouter(prefix="/twilio")


# --------------------------------------------------
# Constants
# --------------------------------------------------

AI_NAME = "Susie"

# After location confirmed — hand off to triage
HOW_CAN_I_HELP = "How can I help you today?"


# --------------------------------------------------
# Clinic-aware greeting helpers
# --------------------------------------------------

def _build_greeting(clinic: dict) -> str:
    """
    Build the opening greeting from clinic config.
    - Custom 'greeting' field: used as-is (e.g. demo line).
    - Multi-location clinic: introduce Susie and ask which location.
    - Single-location clinic: simple greeting using display_name.
    """
    # Custom greeting overrides everything (set in clinic config)
    if clinic.get("greeting"):
        return clinic["greeting"]

    name = clinic.get("display_name", "the clinic")
    locations = clinic.get("locations", [])
    if locations:
        loc_names = " or ".join(loc["name"] for loc in locations)
        return (
            f"Hi there! My name is {AI_NAME}, {name}'s AI receptionist. "
            f"Quick question before I start — "
            f"are you calling about our {loc_names} clinic?"
        )
    return f"Hi there! This is {AI_NAME}, {name}'s AI receptionist. How can I help you today?"


def _build_location_confirmation(location_id: str, clinic: dict) -> str:
    """Return a confirmation string after the caller picks a location."""
    locations = clinic.get("locations", [])
    loc = next((l for l in locations if l["id"] == location_id), None)
    if loc:
        name = loc["name"]
        confirmations = {
            "alcester": "Great! I've got you down for the Alcester clinic.",
            "redditch": "Perfect! I've got you down for the Redditch clinic.",
        }
        return confirmations.get(location_id, f"Got it — the {name} clinic.")
    return "Got it."


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
    
    # Don't process same call twice
    if session.get("call_summary_logged"):
        return PlainTextResponse("already logged")
    
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
    # BUILD CALL SUMMARY & SEND TO GOOGLE SHEETS
    # ========================================================================
    summary = None
    try:
        # Build technical summary
        summary = build_call_summary(session)
        
        # ✅ CRITICAL: Pass raw session so actionable_summary can extract data
        summary["_raw_session"] = session
        
        # Convert to actionable row for Mark
        row = build_actionable_summary_row(summary)
        
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

    if locations:
        # Multi-location clinic (e.g. Theorem) — ask which location first
        location_url = _abs_url(request, "/twilio/location-select")
        await _say(vr, greeting, request, gather_action=location_url)
    else:
        # Single-location clinic (e.g. demo) — skip location question entirely
        session["location_selected"] = True
        session["selected_location"] = "default"
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

    from app.flows.triage import triage_turn

    try:
        session = await get_session(call_sid) or {}
    except Exception:
        session = {}

    session = _init_session(session, call_sid)
    session = _ensure_clinic_on_session(session, to_number)

    miss = int(session.get("miss_count", 0))

    turn_url = _abs_url(request, "/twilio/turn")
    voice_url = _abs_url(request, "/twilio/voice")

    # --------------------------------------------------
    # Safety net: if location was never set, send back
    # to the greeting so Susie can ask again
    # --------------------------------------------------
    if not session.get("location_selected"):
        await save_session(call_sid, session)
        vr.redirect(voice_url, method="POST")
        return xml(vr)

    # --------------------------------------------------
    # Handle empty input
    # --------------------------------------------------
    if not user_said:
        miss = int(session.get("miss_count", 0)) + 1
        session["miss_count"] = miss
        await save_session(call_sid, session)

        if miss == 1:
            vr.append(gather_speech(turn_url, "Sorry — I didn't catch that. Could you repeat?"))
            return xml(vr)

        if miss == 2:
            vr.append(
                gather_speech(
                    turn_url,
                    "Are you looking to book, ask about a treatment, "
                    "or get information about opening times or location?",
                )
            )
            return xml(vr)

        # 3rd miss — offer to take a message
        vr.append(
            gather_speech(
                turn_url,
                "Not to worry. I can take a message for the team. "
                "Please say your name, your number, "
                "and what you'd like help with.",
            )
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
        reply_text, session = await triage_turn(user_said, session)
    except Exception as e:
        print("TRIAGE ERROR:", repr(e))
        vr.append(
            gather_speech(turn_url, "Sorry, something went wrong. Let me try that again.")
        )
        return xml(vr)

    session = _append_turn(session, "assistant", reply_text)
    await save_session(call_sid, session)

    vr.append(gather_speech(turn_url, reply_text))
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
