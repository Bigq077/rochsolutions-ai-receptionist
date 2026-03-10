# app/routes/realtime.py
"""
OpenAI Realtime API voice bridge for Twilio Media Streams.

Architecture:
  Twilio call → <Connect><Stream url="wss://.../twilio/media-stream"/>
       ↓  (WebSocket, G.711 µ-law 8 kHz, bidirectional)
  /twilio/media-stream  (this module)
       ↓  (WebSocket)
  OpenAI Realtime API  (gpt-4o-realtime-preview)
       ↓  (function calls)
  TOOL_EXECUTORS  (receptionist_tools.py — Acuity, SMS, Sheets, etc.)
       ↓  (complex reasoning fallback)
  Claude Sonnet  (handle_turn via escalate_to_claude tool)

Key design points:
- ElevenLabs outputs ulaw_8000 natively — zero transcoding, direct forward to Twilio.
- Server-side VAD handles end-of-speech; no Twilio STT round-trip needed.
- Barge-in: speech_started → cancel response + clear Twilio audio buffer.
- Transfer: Twilio REST API calls(sid).update(twiml=…) — can't return TwiML mid-stream.
- Feature-gated: only active when REALTIME_ENABLED=true on Render.
- Old /twilio/turn HTTP flow remains fully intact as fallback.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
from typing import Any, Dict

import httpx
import websockets
import websockets.exceptions
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)

router = APIRouter()

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
REALTIME_MODEL = "gpt-4o-realtime-preview"
REALTIME_VOICE = os.getenv("REALTIME_VOICE", "coral")   # fallback; ElevenLabs overrides audio output
REALTIME_VAD_SILENCE_MS = int(os.getenv("REALTIME_VAD_SILENCE_MS", "800"))
OPENAI_WS_URL = f"wss://api.openai.com/v1/realtime?model={REALTIME_MODEL}"

# ElevenLabs TTS — Flash v2.5 replaces OpenAI's built-in voice for all Twilio audio output.
# OpenAI still generates audio internally (needed for response.audio_transcript.done event)
# but those audio deltas are suppressed and ElevenLabs audio is streamed to Twilio instead.
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "kBag1HOZlaVBH7ICPE8x")
ELEVENLABS_MODEL_ID = "eleven_flash_v2_5"


# ---------------------------------------------------------------------------
# ElevenLabs TTS  (Flash v2.5 → ulaw_8000 → Twilio, zero transcoding)
# ---------------------------------------------------------------------------

async def _tts_to_twilio(text: str, websocket, stream_sid: str) -> None:
    """
    Call ElevenLabs Flash v2.5 streaming TTS and forward µ-law chunks
    directly to Twilio. ulaw_8000 is G.711 µ-law 8 kHz — the native
    Twilio format — so no codec conversion is needed at all.

    Runs as an asyncio Task so it can be cancelled instantly on barge-in.
    """
    if not text.strip() or not stream_sid:
        return

    url = f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}/stream"
    headers = {
        "xi-api-key": ELEVENLABS_API_KEY,
        "Content-Type": "application/json",
    }
    body = {
        "text": text,
        "model_id": ELEVENLABS_MODEL_ID,
        "output_format": "ulaw_8000",          # native Twilio G.711 µ-law 8 kHz
        "voice_settings": {
            "stability": 0.5,
            "similarity_boost": 0.75,
        },
    }

    logger.info("[realtime] ElevenLabs TTS start: %d chars", len(text))
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            async with client.stream("POST", url, json=body, headers=headers) as resp:
                if resp.status_code != 200:
                    err = await resp.aread()
                    logger.error(
                        "[realtime] ElevenLabs TTS error %d: %s",
                        resp.status_code, err[:200],
                    )
                    return

                chunk_count = 0
                # 160 bytes = 20 ms at 8 kHz µ-law (1 byte/sample)
                async for chunk in resp.aiter_bytes(chunk_size=160):
                    if chunk:
                        await websocket.send_json({
                            "event": "media",
                            "streamSid": stream_sid,
                            "media": {"payload": base64.b64encode(chunk).decode()},
                        })
                        chunk_count += 1

                logger.info("[realtime] ElevenLabs TTS done: %d chunks sent", chunk_count)

    except asyncio.CancelledError:
        logger.info("[realtime] ElevenLabs TTS cancelled (barge-in)")
        raise  # propagate so the task is marked cancelled
    except Exception as exc:
        logger.error("[realtime] ElevenLabs TTS stream error: %r", exc)


# ---------------------------------------------------------------------------
# Tool schema conversion  (Anthropic → OpenAI Realtime format)
# ---------------------------------------------------------------------------

def _build_openai_tools() -> list:
    """
    Convert Anthropic-format TOOL_SCHEMAS to OpenAI Realtime function definitions.
    Also appends the special escalate_to_claude tool.
    """
    from app.tools.receptionist_tools import TOOL_SCHEMAS

    tools = []
    for tool in TOOL_SCHEMAS:
        tools.append({
            "type": "function",
            "name": tool["name"],
            "description": tool.get("description", ""),
            "parameters": tool.get("input_schema", {"type": "object", "properties": {}}),
        })

    # Extra tool: escalate to Claude Sonnet for complex multi-step reasoning
    tools.append({
        "type": "function",
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
    })

    return tools


# ---------------------------------------------------------------------------
# Tool executor: escalate_to_claude
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
# Transfer helper  (Twilio REST API — can't return TwiML mid-stream)
# ---------------------------------------------------------------------------

async def _handle_transfer(call_sid: str, session: Dict[str, Any]) -> None:
    """
    Inject <Say><Dial> into the live call using the Twilio REST API.
    Runs in a background thread so it doesn't block the event loop.
    """
    import asyncio
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
    auth_token = os.getenv("TWILIO_AUTH_TOKEN", "")

    def _do_transfer():
        try:
            client = TwilioClient(account_sid, auth_token)
            client.calls(call_sid).update(twiml=twiml)
            logger.info("[realtime] transfer initiated call_sid=%s → %s", call_sid, transfer_phone)
        except Exception as exc:
            logger.error("[realtime] transfer REST call failed: %r", exc)

    await asyncio.to_thread(_do_transfer)


# ---------------------------------------------------------------------------
# OpenAI session configuration
# ---------------------------------------------------------------------------

async def _configure_openai_session(
    openai_ws,
    session: Dict[str, Any],
) -> None:
    """
    Send session.update to OpenAI with Susie's system prompt + all tools.
    Called only after both session.created (OpenAI) and start (Twilio) events
    have been received, so get_system_prompt() has full session context.
    """
    from app.prompts.susie_system_prompt import get_system_prompt
    from app.clinic_config import get_clinic
    from app.routes.twilio import _build_greeting

    clinic = get_clinic(session.get("clinic_id"))
    greeting = _build_greeting(clinic)

    system_prompt = get_system_prompt(session)

    # Prepend a hard first-response rule so the exact greeting is always used
    instructions = (
        f"FIRST RESPONSE RULE: When the call starts your very first spoken words must be "
        f"EXACTLY this greeting, word for word: \"{greeting}\" — do not paraphrase, "
        f"do not add 'Hi there' or any other prefix, say ONLY those words.\n\n"
        + system_prompt
    )

    await openai_ws.send(json.dumps({
        "type": "session.update",
        "session": {
            "turn_detection": {
                "type": "server_vad",
                "threshold": 0.5,
                "silence_duration_ms": REALTIME_VAD_SILENCE_MS,
                "prefix_padding_ms": 300,
            },
            "input_audio_format": "g711_ulaw",
            "output_audio_format": "g711_ulaw",
            "voice": REALTIME_VOICE,
            "instructions": instructions,
            "tools": _build_openai_tools(),
            "tool_choice": "auto",
            "input_audio_transcription": {"model": "whisper-1"},
            "max_response_output_tokens": 300,  # phone replies are brief
        },
    }))


async def _inject_greeting(openai_ws, session: Dict[str, Any]) -> None:
    """
    Trigger Susie's opening greeting by injecting a synthetic 'call connected'
    user message and asking OpenAI to respond.
    """
    from app.clinic_config import get_clinic
    from app.routes.twilio import _build_greeting

    clinic = get_clinic(session.get("clinic_id"))
    greeting = _build_greeting(clinic)

    # Add a synthetic user turn so OpenAI has context to reply to
    await openai_ws.send(json.dumps({
        "type": "conversation.item.create",
        "item": {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": "[call connected — patient is on the line]"}],
        },
    }))

    # Instruct the model to speak the greeting verbatim as its first response
    await openai_ws.send(json.dumps({
        "type": "response.create",
        "response": {
            "modalities": ["text", "audio"],
            "instructions": (
                f"Speak ONLY these exact words and nothing else: \"{greeting}\" "
                "Do not change a single word. Do not add any prefix or suffix."
            ),
        },
    }))


# ---------------------------------------------------------------------------
# Main WebSocket handler
# ---------------------------------------------------------------------------

@router.websocket("/twilio/media-stream")
async def media_stream(websocket: WebSocket) -> None:
    """
    Bidirectional bridge: Twilio Media Stream ↔ OpenAI Realtime API.

    Lifecycle:
      1. Accept Twilio WebSocket
      2. Open OpenAI Realtime WebSocket
      3. Wait for both session.created (OpenAI) AND start (Twilio)
      4. Configure OpenAI session with Susie's prompt + tools
      5. Inject greeting
      6. Bridge audio in both directions concurrently
      7. Execute tool calls via TOOL_EXECUTORS
      8. Save session to Redis on disconnect
    """
    await websocket.accept()

    # Shared mutable state (all mutations happen in the async event loop —
    # no threading, so no locks needed)
    call_sid: str | None = None
    stream_sid: str | None = None
    session: Dict[str, Any] = {}

    # Synchronisation: both tasks must be ready before greeting fires
    _openai_session_ready = asyncio.Event()
    _twilio_start_received = asyncio.Event()

    # Barge-in drain flag
    _clearing = False

    # In-flight ElevenLabs TTS task — cancelled immediately on barge-in
    _elevenlabs_task: asyncio.Task | None = None

    # Pending function calls keyed by call_id (streaming args accumulation)
    _pending_calls: Dict[str, Dict] = {}

    openai_headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "OpenAI-Beta": "realtime=v1",
    }

    async def _connect_openai():
        """
        Open the OpenAI Realtime WebSocket.
        Returns the live connection or raises on failure.
        """
        return await websockets.connect(
            OPENAI_WS_URL,
            additional_headers=openai_headers,
            ping_interval=30,
            ping_timeout=10,
        )

    # -----------------------------------------------------------------------
    # Task: Twilio → OpenAI   (forward inbound audio; react to stream events)
    # -----------------------------------------------------------------------

    async def _twilio_to_openai(openai_ws) -> None:
        nonlocal call_sid, stream_sid, session, _clearing

        try:
            async for raw in websocket.iter_text():
                msg = json.loads(raw)
                event = msg.get("event", "")

                if event == "connected":
                    logger.info("[realtime] Twilio connected event received")

                elif event == "start":
                    start = msg.get("start", {})
                    call_sid = start.get("callSid", "")
                    stream_sid = start.get("streamSid", "")
                    custom = start.get("customParameters", {})
                    to_number = custom.get("to", "")

                    logger.info(
                        "[realtime] stream start call_sid=%s stream_sid=%s to=%s",
                        call_sid, stream_sid, to_number,
                    )

                    # ── Load & initialise session BEFORE anything else ─────
                    from app.storage.redis_store import get_session
                    from app.routes.twilio import _init_session, _ensure_clinic_on_session

                    session = await get_session(call_sid) or {}
                    session = _init_session(session, call_sid)
                    session = _ensure_clinic_on_session(session, to_number or None)

                    if not session.get("call_start_time"):
                        from datetime import datetime
                        session["call_start_time"] = datetime.utcnow().isoformat() + "Z"

                    # ── Signal: Twilio start is ready ─────────────────────
                    _twilio_start_received.set()

                    # ── Wait for OpenAI session before configuring ─────────
                    await _openai_session_ready.wait()
                    await _configure_openai_session(openai_ws, session)
                    await _inject_greeting(openai_ws, session)

                elif event == "media":
                    payload = msg.get("media", {}).get("payload", "")
                    if payload:
                        await openai_ws.send(json.dumps({
                            "type": "input_audio_buffer.append",
                            "audio": payload,
                        }))

                elif event == "stop":
                    logger.info("[realtime] Twilio stream stop call_sid=%s", call_sid)
                    break

        except WebSocketDisconnect:
            logger.info("[realtime] Twilio WebSocket disconnected call_sid=%s", call_sid)
        except Exception as exc:
            logger.error("[realtime] _twilio_to_openai error: %r", exc, exc_info=True)

    # -----------------------------------------------------------------------
    # Task: OpenAI → Twilio   (forward audio; handle function calls; barge-in)
    # -----------------------------------------------------------------------

    async def _openai_to_twilio(openai_ws) -> None:
        nonlocal session, _clearing, _elevenlabs_task

        try:
            async for raw in openai_ws:
                msg = json.loads(raw)
                msg_type = msg.get("type", "")

                # ── Session ready ─────────────────────────────────────────
                if msg_type == "session.created":
                    logger.info("[realtime] OpenAI session.created")
                    _openai_session_ready.set()

                # ── OpenAI audio output — suppressed; ElevenLabs handles TTS
                elif msg_type == "response.audio.delta":
                    pass  # intentionally discarded — ElevenLabs provides audio to Twilio

                # ── New response started → clear barge-in flag ────────────
                elif msg_type == "response.created":
                    _clearing = False

                # ── Barge-in: user started speaking ───────────────────────
                elif msg_type == "input_audio_buffer.speech_started":
                    logger.info("[realtime] barge-in detected call_sid=%s", call_sid)
                    _clearing = True

                    # Cancel any in-flight ElevenLabs TTS immediately
                    if _elevenlabs_task and not _elevenlabs_task.done():
                        _elevenlabs_task.cancel()
                    _elevenlabs_task = None

                    # Cancel current OpenAI response
                    try:
                        await openai_ws.send(json.dumps({"type": "response.cancel"}))
                    except Exception:
                        pass

                    # Clear Twilio audio buffer
                    if stream_sid:
                        try:
                            await websocket.send_json({
                                "event": "clear",
                                "streamSid": stream_sid,
                            })
                        except Exception:
                            pass

                # ── Function call arguments streaming ─────────────────────
                elif msg_type == "response.function_call_arguments.delta":
                    call_id = msg.get("call_id", "")
                    if call_id not in _pending_calls:
                        _pending_calls[call_id] = {"name": "", "arguments": ""}
                    _pending_calls[call_id]["arguments"] += msg.get("delta", "")

                # ── Function call: name assigned ──────────────────────────
                elif msg_type == "response.output_item.added":
                    item = msg.get("item", {})
                    if item.get("type") == "function_call":
                        call_id = item.get("call_id", "")
                        _pending_calls.setdefault(call_id, {"name": "", "arguments": ""})
                        _pending_calls[call_id]["name"] = item.get("name", "")

                # ── Function call: all arguments received → execute ────────
                elif msg_type == "response.function_call_arguments.done":
                    call_id = msg.get("call_id", "")
                    tool_name = msg.get("name", "") or _pending_calls.get(call_id, {}).get("name", "")
                    args_str = msg.get("arguments", "{}") or _pending_calls.get(call_id, {}).get("arguments", "{}")

                    _pending_calls.pop(call_id, None)

                    try:
                        args = json.loads(args_str)
                    except Exception:
                        args = {}

                    logger.info(
                        "[realtime] tool call: name=%s call_id=%s args=%s",
                        tool_name, call_id, json.dumps(args, default=str),
                    )

                    # Execute the tool
                    if tool_name == "escalate_to_claude":
                        result = await _exec_escalate_to_claude(args, session)
                    else:
                        from app.tools.receptionist_tools import TOOL_EXECUTORS
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

                    # Check for transfer request (set by transfer_to_human executor)
                    if session.pop("request_transfer", False):
                        if call_sid:
                            await _handle_transfer(call_sid, session)
                        return

                    # Send tool result back to OpenAI
                    await openai_ws.send(json.dumps({
                        "type": "conversation.item.create",
                        "item": {
                            "type": "function_call_output",
                            "call_id": call_id,
                            "output": json.dumps(result, default=str),
                        },
                    }))
                    await openai_ws.send(json.dumps({"type": "response.create"}))

                    # Persist session after each tool execution
                    if call_sid:
                        from app.storage.redis_store import save_session
                        await save_session(call_sid, session)

                # ── Transcript: caller ────────────────────────────────────
                elif msg_type == "conversation.item.input_audio_transcription.completed":
                    transcript = msg.get("transcript", "").strip()
                    if transcript:
                        session.setdefault("turns", []).append(
                            {"role": "caller", "text": transcript}
                        )
                        logger.info("[realtime] caller said: %r", transcript)

                # ── Susie's full response text → ElevenLabs TTS → Twilio ──
                elif msg_type == "response.audio_transcript.done":
                    transcript = msg.get("transcript", "").strip()
                    if transcript:
                        session.setdefault("turns", []).append(
                            {"role": "assistant", "text": transcript}
                        )
                        logger.info("[realtime] susie said: %r", transcript)

                        # Cancel any previous TTS task still running, then start new one
                        if _elevenlabs_task and not _elevenlabs_task.done():
                            _elevenlabs_task.cancel()
                        if not _clearing and stream_sid:
                            _elevenlabs_task = asyncio.create_task(
                                _tts_to_twilio(transcript, websocket, stream_sid)
                            )

                # ── Errors ────────────────────────────────────────────────
                elif msg_type == "error":
                    error_code = msg.get("error", {}).get("code", "")
                    if error_code == "response_cancel_not_active":
                        # Benign race: barge-in fired before OpenAI had an active response
                        logger.debug("[realtime] OpenAI %s (benign race, ignored)", error_code)
                    else:
                        logger.error("[realtime] OpenAI error event: %s", json.dumps(msg))

        except websockets.exceptions.ConnectionClosed as exc:
            logger.warning("[realtime] OpenAI WebSocket closed: %s", exc)
        except Exception as exc:
            logger.error("[realtime] _openai_to_twilio error: %r", exc, exc_info=True)

    # -----------------------------------------------------------------------
    # Main connection lifecycle with reconnect
    # -----------------------------------------------------------------------

    openai_ws = None
    reconnect_attempted = False

    try:
        try:
            openai_ws = await _connect_openai()
        except Exception as exc:
            logger.error("[realtime] Failed to connect to OpenAI Realtime: %r", exc)
            await websocket.close()
            return

        try:
            await asyncio.gather(
                _twilio_to_openai(openai_ws),
                _openai_to_twilio(openai_ws),
            )
        except websockets.exceptions.ConnectionClosed:
            # OpenAI dropped the connection mid-call — attempt one reconnect
            if not reconnect_attempted:
                reconnect_attempted = True
                logger.warning("[realtime] OpenAI WS dropped mid-call — attempting reconnect")
                try:
                    openai_ws = await asyncio.wait_for(_connect_openai(), timeout=5.0)
                    # Re-initialise with existing session context
                    _openai_session_ready.clear()
                    _twilio_start_received.set()  # already have call context
                    await asyncio.gather(
                        _twilio_to_openai(openai_ws),
                        _openai_to_twilio(openai_ws),
                    )
                except Exception as exc:
                    logger.error("[realtime] Reconnect failed: %r — ending call", exc)

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

        # Close OpenAI WebSocket cleanly
        if openai_ws:
            try:
                await openai_ws.close()
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
