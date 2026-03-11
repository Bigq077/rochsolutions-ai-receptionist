# app/routes/realtime.py
"""
AssemblyAI Universal-Streaming STT + Groq LLM + ElevenLabs TTS voice bridge.

Architecture:
  Twilio call → <Connect><Stream url="wss://.../twilio/media-stream"/>
       ↓  (WebSocket, G.711 µ-law 8 kHz, bidirectional)
  /twilio/media-stream  (this module)
  ┌─────────────────────────────────────────────────────────────┐
  │  Task 1: _twilio_to_assemblyai                              │
  │    Twilio audio (µ-law bytes) → AssemblyAI WebSocket        │
  │                                                             │
  │  Task 2: _assemblyai_events                                 │
  │    PartialTranscript (non-empty) → barge-in: cancel TTS     │
  │    FinalTranscript              → Groq LLM → ElevenLabs TTS │
  └─────────────────────────────────────────────────────────────┘

  STT:  AssemblyAI Universal-2 (pcm_mulaw 8 kHz, format_turns=false)
  LLM:  Groq  meta-llama/llama-4-maverick-17b-128e-instruct  (max_tokens=150)
  TTS:  ElevenLabs Flash v2.5  (pcm_16000 → audioop → µ-law 8 kHz → Twilio)

Key design points:
- AssemblyAI accepts pcm_mulaw at 8 kHz — Twilio audio forwarded as raw bytes
  (no codec conversion needed on the STT path).
- Barge-in: a non-empty PartialTranscript while TTS is playing cancels the TTS
  task and clears the Twilio audio buffer immediately.
- Groq tool-calling loop reuses the same TOOL_EXECUTORS as before.
  escalate_to_claude → Claude Sonnet (conversation.py) is completely unchanged.
- Greeting is injected directly via ElevenLabs TTS on call start — no LLM
  round-trip required (saves ~500 ms on the opening greeting latency).
- Feature-gated: only active when REALTIME_ENABLED=true on Render.
- Old /twilio/turn HTTP flow remains fully intact as fallback.

ElevenLabs TTS pipeline (unchanged):
- Flash v2.5 silently ignores the ulaw_8000 output_format body field and
  returns audio/mpeg (MP3) instead — confirmed via Content-Type header logging.
  Fix: request pcm_16000 as a URL query parameter, then transcode in Python.
- audioop.tomono(pcm_16k, width=2, lfactor=0.5, rfactor=0.5) treats the
  mono 16 kHz stream as if it were stereo 8 kHz and averages adjacent sample
  pairs.  This is a 2-tap FIR box filter — null at 4 kHz (Nyquist for 8 kHz),
  preventing aliasing while keeping all speech frequencies intact.
"""
from __future__ import annotations

import asyncio
import audioop
import base64
import json
import logging
import os
from typing import Any, Dict, Tuple

import httpx
import websockets
import websockets.exceptions
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)

router = APIRouter()

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

ASSEMBLYAI_API_KEY = os.getenv("ASSEMBLYAI_API_KEY", "")
# URL built at runtime so the API key can be embedded as a query parameter.
# AssemblyAI official examples use ?token= not an Authorization header.
# sample_rate=16000: Universal model works best at 16 kHz; Twilio audio
# (8 kHz µ-law) is upsampled before forwarding (see _twilio_to_assemblyai).
def _assemblyai_ws_url() -> str:
    return (
        "wss://streaming.assemblyai.com/v3/ws"
        f"?speech_model=universal&sample_rate=16000&token={ASSEMBLYAI_API_KEY}"
    )

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = "meta-llama/llama-4-maverick-17b-128e-instruct"
GROQ_MAX_TOKENS = 150

# ElevenLabs TTS — Flash v2.5 (unchanged)
ELEVENLABS_API_KEY  = os.getenv("ELEVENLABS_API_KEY", "")
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "kBag1HOZlaVBH7ICPE8x")
ELEVENLABS_MODEL_ID = "eleven_flash_v2_5"

MAX_TOOL_ITERATIONS = 6
MAX_HISTORY_TURNS   = 20

_SAFE_FALLBACK = (
    "I'm sorry, something went wrong on my end. "
    "Could you give me just a moment and try again?"
)


# ---------------------------------------------------------------------------
# Groq client singleton
# ---------------------------------------------------------------------------

_groq_client = None


def _get_groq_client():
    """Return the shared AsyncGroq singleton, initialised on first call."""
    global _groq_client
    if _groq_client is None:
        from groq import AsyncGroq
        if not GROQ_API_KEY:
            raise RuntimeError("GROQ_API_KEY is not set.")
        _groq_client = AsyncGroq(api_key=GROQ_API_KEY)
    return _groq_client


# ---------------------------------------------------------------------------
# ElevenLabs TTS  (pcm_16000 → audioop tomono 2:1 decimation → lin2ulaw → Twilio)
# ---------------------------------------------------------------------------

async def _tts_to_twilio(text: str, websocket, stream_sid: str) -> None:
    """
    Call ElevenLabs Flash v2.5 streaming TTS (pcm_16000) and forward audio
    to Twilio as G.711 µ-law 8 kHz.

    Transcode pipeline per chunk:
      1. Align to 4-byte boundary (2 PCM16 samples) across chunk boundaries
      2. audioop.tomono  — anti-aliased 2:1 decimation: 16 kHz → 8 kHz
      3. audioop.lin2ulaw — PCM16 → µ-law (8-bit)
      4. base64-encode and send to Twilio as media event

    Runs as an asyncio Task so it can be cancelled instantly on barge-in.
    """
    if not text.strip() or not stream_sid:
        return

    # output_format is a QUERY PARAMETER, not a body field.
    # Placing it in the body causes ElevenLabs to silently ignore it and
    # return audio/mpeg (MP3) — confirmed via Content-Type header logging.
    url = (
        f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}/stream"
        f"?output_format=pcm_16000"
    )
    headers = {
        "xi-api-key": ELEVENLABS_API_KEY,
        "Content-Type": "application/json",
    }
    body = {
        "text": text,
        "model_id": ELEVENLABS_MODEL_ID,
        "voice_settings": {
            "stability": 0.5,
            "similarity_boost": 0.75,
        },
    }

    logger.info(
        "[realtime][diag] TTS request — voice=%s model=%s format=pcm_16000 text=%r",
        ELEVENLABS_VOICE_ID, ELEVENLABS_MODEL_ID, text[:80],
    )
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            async with client.stream("POST", url, json=body, headers=headers) as resp:
                logger.info(
                    "[realtime][diag] ElevenLabs response status=%d content-type=%r",
                    resp.status_code,
                    resp.headers.get("content-type", "MISSING"),
                )

                if resp.status_code != 200:
                    err = await resp.aread()
                    logger.error(
                        "[realtime] ElevenLabs TTS error %d: %s",
                        resp.status_code, err[:300],
                    )
                    return

                # tomono requires chunks aligned to 4 bytes (2 samples × 2 bytes)
                remainder   = b""
                chunk_count = 0
                total_bytes = 0

                # 640 bytes in → 160 µ-law bytes out (20 ms at 8 kHz)
                async for chunk in resp.aiter_bytes(chunk_size=640):
                    if not chunk:
                        continue

                    chunk    = remainder + chunk
                    leftover = len(chunk) % 4
                    if leftover:
                        remainder = chunk[-leftover:]
                        chunk     = chunk[:-leftover]
                    else:
                        remainder = b""

                    if len(chunk) < 4:
                        continue

                    if chunk_count == 0:
                        logger.info(
                            "[realtime][diag] first chunk: len=%d hex_head=%s",
                            len(chunk), chunk[:16].hex(),
                        )

                    # Anti-aliased 2:1 decimation: treat mono 16 kHz as
                    # stereo 8 kHz and average L+R → mono 8 kHz
                    downsampled = audioop.tomono(chunk, 2, 0.5, 0.5)

                    # PCM16 → G.711 µ-law
                    ulaw_chunk = audioop.lin2ulaw(downsampled, 2)

                    total_bytes += len(ulaw_chunk)
                    await websocket.send_json({
                        "event": "media",
                        "streamSid": stream_sid,
                        "media": {"payload": base64.b64encode(ulaw_chunk).decode()},
                    })
                    chunk_count += 1

                logger.info(
                    "[realtime][diag] TTS done: chunks=%d ulaw_bytes=%d approx_ms=%d",
                    chunk_count, total_bytes, (total_bytes * 1000 // 8000),
                )

    except asyncio.CancelledError:
        logger.info("[realtime] ElevenLabs TTS cancelled (barge-in)")
        raise  # propagate so the task is marked cancelled
    except RuntimeError as exc:
        # Happens when the Twilio WebSocket closes while TTS is still streaming.
        # Not a real error — just the connection going away before we finished.
        if "close message" in str(exc):
            logger.warning("[realtime] ElevenLabs TTS aborted (WebSocket already closed)")
        else:
            logger.error("[realtime] ElevenLabs TTS runtime error: %r", exc)
    except Exception as exc:
        logger.error("[realtime] ElevenLabs TTS stream error: %r", exc)


# ---------------------------------------------------------------------------
# Tool schema conversion  (Anthropic → Groq / OpenAI Chat Completions format)
# ---------------------------------------------------------------------------

def _build_groq_tools() -> list:
    """
    Convert Anthropic-format TOOL_SCHEMAS to Groq/OpenAI tool definitions.
    Also appends the special escalate_to_claude tool.

    Groq uses the OpenAI Chat Completions format:
      {"type": "function", "function": {"name": ..., "description": ..., "parameters": ...}}
    (Different from the OpenAI Realtime format which omits the nested "function" key.)
    """
    from app.tools.receptionist_tools import TOOL_SCHEMAS

    tools = []
    for tool in TOOL_SCHEMAS:
        tools.append({
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool.get("description", ""),
                "parameters": tool.get("input_schema", {"type": "object", "properties": {}}),
            },
        })

    # escalate_to_claude — delegates to Claude Sonnet for complex reasoning
    tools.append({
        "type": "function",
        "function": {
            "name": "escalate_to_claude",
            "description": (
                "Use ONLY when you need complex multi-step reasoning that is beyond a simple "
                "conversational reply — for example, unusual edge cases in the booking flow, "
                "complex insurance questions, or when you are genuinely unsure how to proceed. "
                "Pass the patient's question or situation as 'question'. "
                "Do NOT use this for standard greetings, FAQs, availability checks, or bookings."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "The caller's question or situation requiring deep reasoning.",
                    }
                },
                "required": ["question"],
            },
        },
    })

    return tools


# ---------------------------------------------------------------------------
# Tool executor: escalate_to_claude  (unchanged — still delegates to Claude Sonnet)
# ---------------------------------------------------------------------------

async def _exec_escalate_to_claude(args: Dict[str, Any], session: Dict[str, Any]) -> Dict:
    """
    Delegates to Claude Sonnet handle_turn() for complex reasoning.
    Logs every invocation so we can reduce reliance over time.
    """
    question = args.get("question", "")
    logger.info("[realtime] escalate_to_claude fired: question=%r", question)

    try:
        from app.flows.conversation import handle_turn
        reply_text, updated_session = await handle_turn(question, session)
        session.update(updated_session)
        return {"reply": reply_text}
    except Exception as exc:
        logger.error("[realtime] escalate_to_claude error: %r", exc)
        return {"reply": "I'm sorry, I had a little trouble with that. Could you give me a moment?"}


# ---------------------------------------------------------------------------
# Transfer helper  (unchanged — Twilio REST API, can't return TwiML mid-stream)
# ---------------------------------------------------------------------------

async def _handle_transfer(call_sid: str, session: Dict[str, Any]) -> None:
    """
    Inject <Say><Dial> into the live call using the Twilio REST API.
    Runs in a background thread so it doesn't block the event loop.
    """
    from twilio.rest import Client as TwilioClient
    from app.clinic_config import get_clinic
    from app.config import TRANSFER_FALLBACK_NUMBER

    clinic = get_clinic(session.get("clinic_id"))
    transfer_phone = clinic.get("transfer_phone") or TRANSFER_FALLBACK_NUMBER

    twiml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        '<Say language="en-GB">Of course, let me put you straight through to the team now. Please hold.</Say>'
        f"<Dial timeout=\"20\">{transfer_phone}</Dial>"
        "</Response>"
    )

    account_sid = os.getenv("TWILIO_ACCOUNT_SID", "")
    auth_token  = os.getenv("TWILIO_AUTH_TOKEN", "")

    def _do_transfer():
        try:
            client = TwilioClient(account_sid, auth_token)
            client.calls(call_sid).update(twiml=twiml)
            logger.info("[realtime] transfer initiated call_sid=%s → %s", call_sid, transfer_phone)
        except Exception as exc:
            logger.error("[realtime] transfer REST call failed: %r", exc)

    await asyncio.to_thread(_do_transfer)


# ---------------------------------------------------------------------------
# Greeting injection  (direct TTS — no LLM round-trip needed)
# ---------------------------------------------------------------------------

async def _inject_greeting(
    session: Dict[str, Any],
    websocket,
    stream_sid: str,
) -> "asyncio.Task | None":
    """
    Speak Susie's opening greeting directly via ElevenLabs TTS without a
    Groq round-trip — saves ~500 ms on the first word of the call.

    Stores the greeting in conversation_history as a user→assistant exchange
    so Groq has full call context from the very first caller turn.
    """
    from app.clinic_config import get_clinic
    from app.routes.twilio import _build_greeting

    clinic   = get_clinic(session.get("clinic_id"))
    greeting = _build_greeting(clinic)

    logger.info("[realtime] injecting greeting: %r", greeting)

    session.setdefault("turns", []).append({"role": "assistant", "text": greeting})

    # Seed conversation history with a user→assistant exchange so every
    # subsequent Groq call has context about how the call opened.
    history = session.setdefault("conversation_history", [])
    history.append({"role": "user",      "content": "[call connected — patient is on the line]"})
    history.append({"role": "assistant", "content": greeting})

    session["last_bot_prompt"] = greeting

    if not stream_sid:
        return None

    return asyncio.create_task(_tts_to_twilio(greeting, websocket, stream_sid))


# ---------------------------------------------------------------------------
# Groq LLM turn  (tool-calling loop)
# ---------------------------------------------------------------------------

async def _groq_turn(
    user_text: str,
    session: Dict[str, Any],
    call_sid: str | None,
) -> Tuple[str, bool]:
    """
    Run one caller turn through Groq (meta-llama/llama-4-maverick-17b-128e-instruct).

    Handles the full tool-calling loop, per-tool session persistence, and
    transfer detection.

    Returns:
      (reply_text, transfer_initiated)
      - reply_text        : spoken response for ElevenLabs TTS
                            (empty string if a transfer was triggered)
      - transfer_initiated: True if transfer_to_human fired
    """
    from app.prompts.susie_system_prompt import get_system_prompt
    from app.tools.receptionist_tools import TOOL_EXECUTORS

    client        = _get_groq_client()
    system_prompt = get_system_prompt(session)
    tools         = _build_groq_tools()

    history: list  = session.setdefault("conversation_history", [])
    messages: list = [{"role": "system", "content": system_prompt}]
    messages.extend(history[-MAX_HISTORY_TURNS:])
    messages.append({"role": "user", "content": user_text})

    reply_text         = _SAFE_FALLBACK
    transfer_initiated = False

    for iteration in range(1, MAX_TOOL_ITERATIONS + 1):
        logger.info("[realtime] Groq iteration=%d model=%s", iteration, GROQ_MODEL)

        try:
            response = await client.chat.completions.create(
                model=GROQ_MODEL,
                messages=messages,
                tools=tools,
                tool_choice="auto",
                max_tokens=GROQ_MAX_TOKENS,
                timeout=12.0,
            )
        except Exception as exc:
            logger.error(
                "\n" + "="*60 + "\n"
                "[PIPELINE ERROR] Groq LLM call failed\n"
                "  model     : %s\n"
                "  iteration : %d\n"
                "  error     : %r\n"
                + "="*60,
                GROQ_MODEL, iteration, exc,
            )
            return _SAFE_FALLBACK, False

        choice       = response.choices[0]
        message      = choice.message
        finish_reason = choice.finish_reason

        logger.info(
            "[realtime] Groq finish_reason=%s tool_calls=%d",
            finish_reason,
            len(message.tool_calls or []),
        )

        # Append assistant message to local working messages list
        assistant_msg: Dict = {
            "role":    "assistant",
            "content": message.content or "",
        }
        if message.tool_calls:
            assistant_msg["tool_calls"] = [
                {
                    "id":   tc.id,
                    "type": "function",
                    "function": {
                        "name":      tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in message.tool_calls
            ]
        messages.append(assistant_msg)

        # ── Final spoken response (no tool calls) ─────────────────────────
        if finish_reason == "stop" or not message.tool_calls:
            reply_text = (message.content or "").strip() or _SAFE_FALLBACK
            break

        # ── Execute tool calls ─────────────────────────────────────────────
        tool_results: list = []
        for tc in message.tool_calls:
            tool_name = tc.function.name
            try:
                args = json.loads(tc.function.arguments)
            except Exception:
                args = {}

            logger.info(
                "[realtime] tool call: name=%s call_id=%s args=%s",
                tool_name, tc.id, json.dumps(args, default=str),
            )

            if tool_name == "escalate_to_claude":
                result = await _exec_escalate_to_claude(args, session)
            else:
                executor = TOOL_EXECUTORS.get(tool_name)
                if executor:
                    try:
                        result = await executor(args, session)
                    except Exception as exc:
                        logger.error("[realtime] tool %s error: %r", tool_name, exc)
                        result = {"error": str(exc)}
                else:
                    logger.warning("[realtime] unknown tool: %s", tool_name)
                    result = {"error": f"Unknown tool: {tool_name}"}

            logger.info(
                "[realtime] tool result: name=%s result=%s",
                tool_name, json.dumps(result, default=str),
            )

            tool_results.append({
                "role":        "tool",
                "tool_call_id": tc.id,
                "content":     json.dumps(result, default=str),
            })

        messages.extend(tool_results)

        # Persist session after each tool round
        if call_sid:
            try:
                from app.storage.redis_store import save_session
                await save_session(call_sid, session)
            except Exception as exc:
                logger.warning("[realtime] session save failed: %r", exc)

        # Check for transfer request (set by transfer_to_human executor)
        if session.pop("request_transfer", False):
            logger.info("[realtime] transfer requested call_sid=%s", call_sid)
            if call_sid:
                await _handle_transfer(call_sid, session)
            transfer_initiated = True
            reply_text         = ""
            break

    else:
        logger.warning("[realtime] Groq hit MAX_TOOL_ITERATIONS")
        reply_text = _SAFE_FALLBACK

    # Persist clean text turns to conversation history (tool intermediates excluded)
    if not transfer_initiated:
        history.append({"role": "user",      "content": user_text})
        history.append({"role": "assistant", "content": reply_text})
        if len(history) > MAX_HISTORY_TURNS:
            history = history[-MAX_HISTORY_TURNS:]
        session["conversation_history"] = history
        session["last_bot_prompt"]      = reply_text

    return reply_text, transfer_initiated


# ---------------------------------------------------------------------------
# Main WebSocket handler
# ---------------------------------------------------------------------------

@router.websocket("/twilio/media-stream")
async def media_stream(websocket: WebSocket) -> None:
    """
    Bidirectional bridge: Twilio Media Stream → AssemblyAI STT → Groq LLM → ElevenLabs TTS.

    Lifecycle:
      1. Accept Twilio WebSocket
      2. Connect to AssemblyAI Universal-Streaming WebSocket
      3. On Twilio "start": initialise session, speak greeting via ElevenLabs TTS
      4. Task 1 (_twilio_to_assemblyai):
           Forward Twilio µ-law audio bytes → AssemblyAI (no codec conversion)
      5. Task 2 (_assemblyai_events):
           PartialTranscript (non-empty) → barge-in: cancel TTS + clear Twilio buffer
           FinalTranscript              → _groq_turn → ElevenLabs TTS
      6. Save session to Redis on disconnect
    """
    await websocket.accept()

    # Shared mutable state (single asyncio event loop — no locks needed)
    call_sid:  str | None = None
    stream_sid: str | None = None
    session:   Dict[str, Any] = {}

    _clearing      = False   # True while draining audio after barge-in
    _groq_busy     = False   # True while Groq is processing a turn
    _elevenlabs_task: asyncio.Task | None = None

    # Signal: Twilio "start" event received — session is ready
    _twilio_started = asyncio.Event()
    # Signal: AssemblyAI SessionBegins received — safe to forward audio
    _assemblyai_ready = asyncio.Event()

    # ── Connect to AssemblyAI ──────────────────────────────────────────────
    if not ASSEMBLYAI_API_KEY:
        logger.error("[realtime] ASSEMBLYAI_API_KEY is not set — closing connection")
        await websocket.close()
        return

    try:
        assemblyai_ws = await websockets.connect(
            _assemblyai_ws_url(),
            ping_interval=None,   # AssemblyAI handles keepalive server-side
            open_timeout=10,
        )
    except Exception as exc:
        logger.error(
            "\n" + "="*60 + "\n"
            "[PIPELINE ERROR] AssemblyAI connection failed\n"
            "  reason : %r\n"
            "  url    : %s\n"
            + "="*60,
            exc, _assemblyai_ws_url().split("token=")[0] + "token=<redacted>",
        )
        await websocket.close()
        return

    # ── Task 1: Twilio → AssemblyAI  (forward inbound audio) ──────────────

    async def _twilio_to_assemblyai() -> None:
        nonlocal call_sid, stream_sid, session, _elevenlabs_task, _clearing
        _ratecv_state = None  # per-stream ratecv state for 8 kHz → 16 kHz

        try:
            async for raw in websocket.iter_text():
                msg   = json.loads(raw)
                event = msg.get("event", "")

                if event == "connected":
                    logger.info("[realtime] Twilio connected event received")

                elif event == "start":
                    start      = msg.get("start", {})
                    call_sid   = start.get("callSid", "")
                    stream_sid = start.get("streamSid", "")
                    custom     = start.get("customParameters", {})
                    to_number  = custom.get("to", "")

                    logger.info(
                        "[realtime] stream start call_sid=%s stream_sid=%s to=%s",
                        call_sid, stream_sid, to_number,
                    )

                    from app.storage.redis_store import get_session
                    from app.routes.twilio import _init_session, _ensure_clinic_on_session
                    from datetime import datetime

                    session = await get_session(call_sid) or {}
                    session = _init_session(session, call_sid)
                    session = _ensure_clinic_on_session(session, to_number or None)

                    if not session.get("call_start_time"):
                        session["call_start_time"] = datetime.utcnow().isoformat() + "Z"

                    _twilio_started.set()

                    # Speak the greeting immediately via ElevenLabs TTS
                    _elevenlabs_task = await _inject_greeting(session, websocket, stream_sid)

                elif event == "media":
                    if not _assemblyai_ready.is_set():
                        continue  # drop until SessionBegins confirms session is live
                    payload = msg.get("media", {}).get("payload", "")
                    if payload and not _clearing:
                        # µ-law 8 kHz → PCM16 LE 16 kHz for AssemblyAI Universal.
                        # audioop.ratecv state is carried across frames to avoid
                        # discontinuities at chunk boundaries.
                        ulaw_bytes = base64.b64decode(payload)
                        pcm_8k     = audioop.ulaw2lin(ulaw_bytes, 2)
                        pcm_bytes, _ratecv_state = audioop.ratecv(
                            pcm_8k, 2, 1, 8000, 16000, _ratecv_state
                        )
                        try:
                            await assemblyai_ws.send(pcm_bytes)
                        except websockets.exceptions.ConnectionClosed:
                            logger.warning(
                                "[realtime] AssemblyAI connection closed — stopping audio"
                            )
                            break
                        except Exception as exc:
                            logger.warning("[realtime] AssemblyAI send error: %r", exc)

                elif event == "stop":
                    logger.info("[realtime] Twilio stream stop call_sid=%s", call_sid)
                    break

        except WebSocketDisconnect:
            logger.info("[realtime] Twilio WebSocket disconnected call_sid=%s", call_sid)
        except Exception as exc:
            logger.error("[realtime] _twilio_to_assemblyai error: %r", exc, exc_info=True)
        finally:
            # Tell AssemblyAI the session is over
            try:
                await assemblyai_ws.send(json.dumps({"terminate_session": True}))
            except Exception:
                pass

    # ── Task 2: AssemblyAI events → Groq LLM → ElevenLabs TTS ────────────

    async def _assemblyai_events() -> None:
        nonlocal session, _clearing, _groq_busy, _elevenlabs_task

        try:
            async for raw in assemblyai_ws:
                msg      = json.loads(raw)
                msg_type = msg.get("message_type", "")

                # ── Session ready ──────────────────────────────────────────
                if msg_type == "SessionBegins":
                    logger.info(
                        "[realtime] AssemblyAI session started: %s",
                        msg.get("session_id", ""),
                    )
                    _assemblyai_ready.set()  # allow audio forwarding to begin

                # ── Barge-in: user started speaking ───────────────────────
                elif msg_type == "PartialTranscript":
                    text = (msg.get("text") or "").strip()
                    if text and _elevenlabs_task and not _elevenlabs_task.done():
                        logger.info(
                            "[realtime] barge-in detected call_sid=%s partial=%r",
                            call_sid, text[:40],
                        )
                        _elevenlabs_task.cancel()
                        _elevenlabs_task = None
                        _clearing = True
                        if stream_sid:
                            try:
                                await websocket.send_json({
                                    "event":     "clear",
                                    "streamSid": stream_sid,
                                })
                            except Exception:
                                pass

                # ── Caller utterance complete → call Groq ─────────────────
                elif msg_type == "FinalTranscript":
                    text      = (msg.get("text") or "").strip()
                    _clearing = False

                    if not text:
                        continue  # silence / noise — ignore

                    logger.info("[realtime] caller said: %r", text)
                    session.setdefault("turns", []).append({"role": "caller", "text": text})

                    if _groq_busy:
                        logger.info("[realtime] Groq busy — dropping utterance: %r", text)
                        continue

                    # Safety guard: ensure session is initialised before first LLM call
                    try:
                        await asyncio.wait_for(_twilio_started.wait(), timeout=5.0)
                    except asyncio.TimeoutError:
                        logger.warning("[realtime] session not initialised after 5 s — skipping")
                        continue

                    _groq_busy = True
                    try:
                        reply, transfer = await _groq_turn(text, session, call_sid)
                    except Exception as exc:
                        logger.error(
                            "\n" + "="*60 + "\n"
                            "[PIPELINE ERROR] _groq_turn raised unexpectedly\n"
                            "  call_sid : %s\n"
                            "  error    : %r\n"
                            + "="*60,
                            call_sid, exc, exc_info=True,
                        )
                        reply, transfer = _SAFE_FALLBACK, False
                    finally:
                        _groq_busy = False

                    if transfer:
                        return  # call handed off — stop processing events

                    if reply and stream_sid and not _clearing:
                        if _elevenlabs_task and not _elevenlabs_task.done():
                            _elevenlabs_task.cancel()
                        _elevenlabs_task = asyncio.create_task(
                            _tts_to_twilio(reply, websocket, stream_sid)
                        )
                        session.setdefault("turns", []).append(
                            {"role": "assistant", "text": reply}
                        )

                    # Persist session after each completed turn
                    if call_sid:
                        try:
                            from app.storage.redis_store import save_session
                            await save_session(call_sid, session)
                        except Exception as exc:
                            logger.warning("[realtime] session save failed: %r", exc)

                # ── AssemblyAI closed the session ──────────────────────────
                elif msg_type == "SessionTerminated":
                    logger.info("[realtime] AssemblyAI session terminated")
                    break

                # ── Errors ────────────────────────────────────────────────
                elif msg_type == "RealtimeError":
                    logger.error("[realtime] AssemblyAI error: %s", msg.get("error"))

        except websockets.exceptions.ConnectionClosed as exc:
            rcvd   = exc.rcvd
            code   = rcvd.code   if rcvd else "?"
            reason = rcvd.reason if rcvd else "?"
            level  = logger.warning if code in (1000, 1001) else logger.error
            level(
                "\n" + "="*60 + "\n"
                "[PIPELINE ERROR] AssemblyAI WebSocket closed unexpectedly\n"
                "  close_code   : %s\n"
                "  close_reason : %r\n"
                "  call_sid     : %s\n"
                + "="*60,
                code, reason, call_sid,
            )
        except Exception as exc:
            logger.error("[realtime] _assemblyai_events error: %r", exc, exc_info=True)

    # ── Run both tasks concurrently ────────────────────────────────────────

    try:
        await asyncio.gather(
            _twilio_to_assemblyai(),
            _assemblyai_events(),
        )
    except Exception as exc:
        logger.error("[realtime] media_stream top-level error: %r", exc, exc_info=True)

    finally:
        # Persist session on disconnect so /status callback can build summary
        if call_sid and session:
            try:
                from app.storage.redis_store import save_session
                await save_session(call_sid, session)
                logger.info("[realtime] session saved on disconnect call_sid=%s", call_sid)
            except Exception as exc:
                logger.warning("[realtime] failed to save session: %r", exc)

        # Close AssemblyAI WebSocket cleanly
        try:
            await assemblyai_ws.close()
        except Exception:
            pass

        # Close Twilio WebSocket cleanly
        try:
            await websocket.close()
        except Exception:
            pass

        if call_sid:
            logger.info("[realtime] media_stream handler exited call_sid=%s", call_sid)
        else:
            logger.debug("[realtime] media_stream handler exited (no stream start received)")
