# app/routes/realtime.py
"""
AssemblyAI Universal-Streaming STT + Claude Sonnet LLM + ElevenLabs TTS voice bridge.

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
  │    FinalTranscript              → OpenAI LLM → ElevenLabs TTS │
  └─────────────────────────────────────────────────────────────┘

  STT:  AssemblyAI Universal-2 (pcm_mulaw 8 kHz, format_turns=false)
  LLM:  Claude Sonnet (claude-sonnet-4-6, max_tokens=150, streaming)
  TTS:  ElevenLabs Flash v2.5  (pcm_16000 → audioop → µ-law 8 kHz → Twilio)

Key design points:
- AssemblyAI accepts pcm_mulaw at 8 kHz — Twilio audio forwarded as raw bytes
  (no codec conversion needed on the STT path).
- Barge-in: a non-empty PartialTranscript while TTS is playing cancels the TTS
  task and clears the Twilio audio buffer immediately.
- Claude tool-calling loop (Anthropic native format) drives all booking/info tools.
  Sentence-level TTS: first sentence fires immediately on boundary detection.
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
import re
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
# AssemblyAI v3 Universal Streaming — auth via Authorization header, NOT ?token= URL param.
# sample_rate=16000: Universal model works best at 16 kHz; Twilio audio
# (8 kHz µ-law) is upsampled before forwarding (see _twilio_to_assemblyai).
# v3 Universal Streaming (primary).
# Remove min_turn_silence/max_turn_silence — those are U3-Pro-only params.
# Minimal required params: speech_model + sample_rate + encoding.
ASSEMBLYAI_WS_URL = (
    "wss://streaming.assemblyai.com/v3/ws"
    "?speech_model=universal-streaming-english"
    "&sample_rate=16000"
    "&encoding=pcm_s16le"
    "&format_turns=false"
)

# v2 fallback (older, battle-tested, 8 kHz PCM16, no upsampling needed).
# Set ASSEMBLYAI_USE_V2=true on Render to use this instead.
ASSEMBLYAI_USE_V2 = os.getenv("ASSEMBLYAI_USE_V2", "false").lower() == "true"
ASSEMBLYAI_WS_URL_V2 = (
    "wss://api.assemblyai.com/v2/realtime/ws"
    "?sample_rate=8000"
    "&end_utterance_silence_threshold=700"
)

ANTHROPIC_API_KEY  = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL       = "claude-sonnet-4-6"
CLAUDE_MAX_TOKENS  = 1024
CLAUDE_TEMPERATURE = 0.4

# ---------------------------------------------------------------------------
# Sentence boundary detection  (streaming TTS — sentence-level chunking)
# ---------------------------------------------------------------------------

# Words that must NOT trigger a sentence split when followed by a period
_ABBREVS = frozenset({"dr", "mr", "mrs", "ms", "st", "no", "vs"})


def _find_sentence_boundary(buf: str, min_chars: int = 20) -> "int | None":
    """Return the index just after the first valid sentence boundary, or None.

    Rules:
    - Sentence ends with . ? ! followed by whitespace+uppercase or end of string.
    - Never split on: Dr. Mr. Mrs. Ms. St. No. vs. (common abbreviations).
    - Never split on decimal numbers like 9.30.
    - Need at least min_chars before the punctuation.
    """
    for m in re.finditer(r"[.!?]", buf):
        pos = m.start()
        end = m.end()

        if pos < min_chars:
            continue

        punct = m.group()
        after = buf[end:]

        # Must be followed by whitespace+uppercase, or end of string
        if after:
            stripped = after.lstrip()
            if stripped:
                if not stripped[0].isupper():
                    continue   # next word starts lowercase → not a sentence end
            # else only whitespace follows → valid boundary
        # else end of string → valid

        if punct == ".":
            # Check for abbreviation: look at last word before the dot
            word_m = re.search(r"(\w+)$", buf[:pos])
            if word_m:
                word = word_m.group(1).lower()
                if word in _ABBREVS:
                    continue
                # Single capital letter = initial (e.g. "J. Smith")
                if len(word) == 1 and word_m.group(1).isupper():
                    continue
            # Check for decimal: digit immediately before dot
            if pos > 0 and buf[pos - 1].isdigit():
                continue

        return end

    return None

# ElevenLabs TTS — Flash v2.5 (unchanged)
ELEVENLABS_API_KEY  = os.getenv("ELEVENLABS_API_KEY", "")
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "kBag1HOZlaVBH7ICPE8x")
ELEVENLABS_MODEL_ID = "eleven_flash_v2_5"

MAX_TOOL_ITERATIONS = 6
MAX_HISTORY_TURNS   = 20

_SAFE_FALLBACK = (
    "Sorry, I had a bit of a blip there -- "
    "could you give me just a moment and try again?"
)


# ---------------------------------------------------------------------------
# Anthropic client singleton
# ---------------------------------------------------------------------------

_anthropic_client = None


def _get_anthropic_client():
    """Return the shared AsyncAnthropic singleton, initialised on first call."""
    global _anthropic_client
    if _anthropic_client is None:
        from anthropic import AsyncAnthropic
        if not ANTHROPIC_API_KEY:
            raise RuntimeError("ANTHROPIC_API_KEY is not set.")
        _anthropic_client = AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
    return _anthropic_client


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
# Tool definitions  (Anthropic native format — used directly with Claude)
# ---------------------------------------------------------------------------

def _build_claude_tools() -> list:
    """
    Return tool definitions in Anthropic native format.
    TOOL_SCHEMAS from receptionist_tools.py is already in Anthropic format
    (uses input_schema, not parameters) — used directly without conversion.
    """
    from app.tools.receptionist_tools import TOOL_SCHEMAS

    tools = list(TOOL_SCHEMAS)  # already in Anthropic format

    # escalate_to_claude — delegates to Claude Sonnet conversation.py
    tools.append({
        "name": "escalate_to_claude",
        "description": (
            "Use ONLY for genuine clinical or legal complexity requiring deep reasoning. "
            "Never for standard greetings, FAQs, availability, booking, pricing, hours, "
            "or common conditions — handle all of those directly."
        ),
        "input_schema": {
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
    LLM round-trip — saves ~500 ms on the first word of the call.

    Stores the greeting in conversation_history as a user→assistant exchange
    so the LLM has full call context from the very first caller turn.
    """
    from app.clinic_config import get_clinic
    from app.routes.twilio import _build_greeting

    clinic   = get_clinic(session.get("clinic_id"))
    greeting = _build_greeting(clinic)

    logger.info("[realtime] injecting greeting: %r", greeting)

    session.setdefault("turns", []).append({"role": "assistant", "text": greeting})

    # Seed conversation history with a user→assistant exchange so every
    # subsequent LLM call has context about how the call opened.
    history = session.setdefault("conversation_history", [])
    history.append({"role": "user",      "content": "[call connected — patient is on the line]"})
    history.append({"role": "assistant", "content": greeting})

    session["last_bot_prompt"] = greeting

    if not stream_sid:
        return None

    return asyncio.create_task(_tts_to_twilio(greeting, websocket, stream_sid))


# ---------------------------------------------------------------------------
# Claude Sonnet LLM turn  (streaming tool-calling loop)
# ---------------------------------------------------------------------------

async def _llm_turn(
    user_text: str,
    session: Dict[str, Any],
    call_sid: str | None,
    websocket=None,
    stream_sid: str | None = None,
) -> Tuple[str, bool, "asyncio.Task | None"]:
    """
    Run one caller turn through Claude Sonnet with streaming.

    Sentence-level TTS: first sentence fires to ElevenLabs as soon as the
    sentence boundary is detected in the streamed tokens.

    Returns:
      (reply_text, transfer_initiated, tts_task)
    """
    from app.prompts.susie_system_prompt import get_system_prompt
    from app.tools.receptionist_tools import TOOL_EXECUTORS

    client        = _get_anthropic_client()
    system_prompt = get_system_prompt(session)
    tools         = _build_claude_tools()

    history: list  = session.setdefault("conversation_history", [])
    messages: list = list(history[-MAX_HISTORY_TURNS:])
    messages.append({"role": "user", "content": user_text})

    reply_text         = _SAFE_FALLBACK
    transfer_initiated = False
    tts_task: "asyncio.Task | None" = None

    for iteration in range(1, MAX_TOOL_ITERATIONS + 1):
        logger.info("[realtime] LLM iteration=%d model=%s", iteration, CLAUDE_MODEL)

        text_parts: list = []
        tool_uses:  list = []
        current_block    = None
        stop_reason: str | None = None
        sentence_buf     = ""
        first_tts_task: "asyncio.Task | None" = None

        try:
            async with client.messages.stream(
                model=CLAUDE_MODEL,
                system=system_prompt,
                messages=messages,
                tools=tools,
                max_tokens=CLAUDE_MAX_TOKENS,
                temperature=CLAUDE_TEMPERATURE,
            ) as stream:
                async for event in stream:
                    etype = getattr(event, "type", "")

                    if etype == "content_block_start":
                        blk = event.content_block
                        if blk.type == "text":
                            current_block = {"type": "text", "text": ""}
                        elif blk.type == "tool_use":
                            current_block = {
                                "type":       "tool_use",
                                "id":         blk.id,
                                "name":       blk.name,
                                "input_json": "",
                            }

                    elif etype == "content_block_delta":
                        d = event.delta
                        if (
                            d.type == "text_delta"
                            and current_block
                            and current_block["type"] == "text"
                        ):
                            chunk = d.text
                            text_parts.append(chunk)
                            current_block["text"] += chunk
                            sentence_buf += chunk
                            if first_tts_task is None and websocket and stream_sid:
                                boundary = _find_sentence_boundary(sentence_buf)
                                if boundary is not None:
                                    first_sent   = sentence_buf[:boundary].strip()
                                    sentence_buf = sentence_buf[boundary:].lstrip()
                                    if first_sent:
                                        logger.info(
                                            "[realtime] sentence TTS sent1: %r",
                                            first_sent[:60],
                                        )
                                        first_tts_task = asyncio.create_task(
                                            _tts_to_twilio(first_sent, websocket, stream_sid)
                                        )
                        elif (
                            d.type == "input_json_delta"
                            and current_block
                            and current_block["type"] == "tool_use"
                        ):
                            current_block["input_json"] += d.partial_json

                    elif etype == "content_block_stop":
                        if current_block:
                            if current_block["type"] == "tool_use":
                                tool_uses.append(current_block)
                            current_block = None

                    elif etype == "message_delta":
                        stop_reason = getattr(event.delta, "stop_reason", None)

        except Exception as exc:
            logger.error(
                "\n" + "="*60 + "\n"
                "[PIPELINE ERROR] Claude LLM stream failed\n"
                "  model     : %s\n"
                "  iteration : %d\n"
                "  error     : %r\n"
                + "="*60,
                CLAUDE_MODEL, iteration, exc,
            )
            return _SAFE_FALLBACK, False, None

        full_content = "".join(text_parts)
        logger.info(
            "[realtime] LLM stop_reason=%s tool_calls=%d content_len=%d",
            stop_reason, len(tool_uses), len(full_content),
        )

        # Final response (no tool calls)
        if not tool_uses:
            reply_text = full_content.strip() or _SAFE_FALLBACK
            remaining  = sentence_buf.strip()
            if websocket and stream_sid:
                if first_tts_task and remaining:
                    async def _chain(
                        t=first_tts_task, rest=remaining,
                        ws=websocket, sid=stream_sid,
                    ):
                        await t
                        await _tts_to_twilio(rest, ws, sid)
                    tts_task = asyncio.create_task(_chain())
                elif first_tts_task:
                    tts_task = first_tts_task
            break

        # Build assistant message (Anthropic content-blocks format)
        assistant_content: list = []
        if full_content:
            assistant_content.append({"type": "text", "text": full_content})
        for tu in tool_uses:
            try:
                input_data = json.loads(tu["input_json"]) if tu["input_json"] else {}
            except Exception:
                input_data = {}
            assistant_content.append({
                "type":  "tool_use",
                "id":    tu["id"],
                "name":  tu["name"],
                "input": input_data,
            })
        messages.append({"role": "assistant", "content": assistant_content})

        # Execute tool calls
        tool_result_blocks: list = []
        for tu in tool_uses:
            tool_name = tu["name"]
            try:
                input_data = json.loads(tu["input_json"]) if tu["input_json"] else {}
            except Exception:
                input_data = {}

            logger.info(
                "[realtime] tool call: name=%s id=%s args=%s",
                tool_name, tu["id"], json.dumps(input_data, default=str)[:200],
            )

            if tool_name == "escalate_to_claude":
                result = await _exec_escalate_to_claude(input_data, session)
            else:
                executor = TOOL_EXECUTORS.get(tool_name)
                if executor:
                    try:
                        result = await executor(input_data, session)
                    except Exception as exc:
                        logger.error("[realtime] tool %s error: %r", tool_name, exc)
                        result = {"error": str(exc)}
                else:
                    logger.warning("[realtime] unknown tool: %s", tool_name)
                    result = {"error": f"Unknown tool: {tool_name}"}

            logger.info(
                "[realtime] tool result: name=%s result=%s",
                tool_name, json.dumps(result, default=str)[:200],
            )
            tool_result_blocks.append({
                "type":        "tool_result",
                "tool_use_id": tu["id"],
                "content":     json.dumps(result, default=str),
            })

        # Tool results go back as a user message (Anthropic format)
        messages.append({"role": "user", "content": tool_result_blocks})

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
            tts_task           = None
            break

    else:
        logger.warning("[realtime] LLM hit MAX_TOOL_ITERATIONS")
        reply_text = _SAFE_FALLBACK
        tts_task   = None

    # Persist clean text turns to conversation history
    if not transfer_initiated:
        history.append({"role": "user",      "content": user_text})
        history.append({"role": "assistant", "content": reply_text})
        if len(history) > MAX_HISTORY_TURNS:
            history = history[-MAX_HISTORY_TURNS:]
        session["conversation_history"] = history
        session["last_bot_prompt"]      = reply_text

    return reply_text, transfer_initiated, tts_task

# ---------------------------------------------------------------------------
# Main WebSocket handler
# ---------------------------------------------------------------------------

@router.websocket("/twilio/media-stream")
async def media_stream(websocket: WebSocket) -> None:
    """
    Bidirectional bridge: Twilio Media Stream → AssemblyAI STT → OpenAI LLM → ElevenLabs TTS.

    Lifecycle:
      1. Accept Twilio WebSocket
      2. Connect to AssemblyAI Universal-Streaming WebSocket
      3. On Twilio "start": initialise session, speak greeting via ElevenLabs TTS
      4. Task 1 (_twilio_to_assemblyai):
           Forward Twilio µ-law audio bytes → AssemblyAI (no codec conversion)
      5. Task 2 (_assemblyai_events):
           PartialTranscript (non-empty) → barge-in: cancel TTS + clear Twilio buffer
           FinalTranscript              → _llm_turn  → ElevenLabs TTS
      6. Save session to Redis on disconnect
    """
    await websocket.accept()

    # Shared mutable state (single asyncio event loop — no locks needed)
    call_sid:  str | None = None
    stream_sid: str | None = None
    session:   Dict[str, Any] = {}

    _clearing      = False   # True while draining audio after barge-in
    _llm_busy      = False   # True while LLM is processing a turn
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

    _ws_url = ASSEMBLYAI_WS_URL_V2 if ASSEMBLYAI_USE_V2 else ASSEMBLYAI_WS_URL
    logger.info("[realtime] AssemblyAI connect url=%s", _ws_url)
    try:
        assemblyai_ws = await websockets.connect(
            _ws_url,
            additional_headers={"Authorization": ASSEMBLYAI_API_KEY},
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
            exc, ASSEMBLYAI_WS_URL,
        )
        await websocket.close()
        return

    # ── Task 1: Twilio → AssemblyAI  (forward inbound audio) ──────────────

    async def _twilio_to_assemblyai() -> None:
        nonlocal call_sid, stream_sid, session, _elevenlabs_task, _clearing
        _ratecv_state = None  # per-stream ratecv state for 8 kHz → 16 kHz

        # AssemblyAI v3 requires audio chunks of 50–1000 ms.
        # Twilio sends 20ms frames, so we buffer 3 frames (60ms) before sending.
        # v3: 640 bytes/frame (PCM16 16kHz) → flush at 1920 bytes = 60ms
        # v2: 320 bytes/frame (PCM16  8kHz) → flush at  960 bytes = 60ms
        _pcm_buffer   = b""
        _pcm_flush_at = 320 * 3 if ASSEMBLYAI_USE_V2 else 640 * 3

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
                        ulaw_bytes = base64.b64decode(payload)
                        if ASSEMBLYAI_USE_V2:
                            # v2 wants 8 kHz PCM16 — no upsampling needed.
                            pcm_bytes = audioop.ulaw2lin(ulaw_bytes, 2)
                        else:
                            # v3 wants 16 kHz PCM16 — upsample from Twilio 8 kHz.
                            pcm_8k    = audioop.ulaw2lin(ulaw_bytes, 2)
                            pcm_bytes, _ratecv_state = audioop.ratecv(
                                pcm_8k, 2, 1, 8000, 16000, _ratecv_state
                            )
                        _pcm_buffer += pcm_bytes
                        if len(_pcm_buffer) >= _pcm_flush_at:
                            try:
                                await assemblyai_ws.send(_pcm_buffer)
                            except websockets.exceptions.ConnectionClosed:
                                logger.warning(
                                    "[realtime] AssemblyAI connection closed — stopping audio"
                                )
                                break
                            except Exception as exc:
                                logger.warning("[realtime] AssemblyAI send error: %r", exc)
                            _pcm_buffer = b""

                elif event == "stop":
                    logger.info("[realtime] Twilio stream stop call_sid=%s", call_sid)
                    break

        except WebSocketDisconnect:
            logger.info("[realtime] Twilio WebSocket disconnected call_sid=%s", call_sid)
        except Exception as exc:
            logger.error("[realtime] _twilio_to_assemblyai error: %r", exc, exc_info=True)
        finally:
            # Tell AssemblyAI the session is over.
            # v3 just needs the WebSocket closed — the terminate_session JSON message
            # is v2 only; sending it to v3 produces error 3006 (Invalid Message Type).
            if ASSEMBLYAI_USE_V2:
                try:
                    await assemblyai_ws.send(json.dumps({"terminate_session": True}))
                except Exception:
                    pass

    # ── Task 2: AssemblyAI events → Groq LLM → ElevenLabs TTS ────────────

    async def _assemblyai_events() -> None:
        nonlocal session, _clearing, _llm_busy, _elevenlabs_task

        try:
            async for raw in assemblyai_ws:
                msg      = json.loads(raw)
                # v3 uses "type" field; v2 uses "message_type" — handle both.
                msg_type = msg.get("type") or msg.get("message_type", "")

                # ── Session ready ──────────────────────────────────────────
                if msg_type in ("Begin", "SessionBegins"):
                    logger.info(
                        "[realtime] AssemblyAI session started: %s",
                        msg.get("session_id", ""),
                    )
                    _assemblyai_ready.set()  # allow audio forwarding to begin

                # ── Barge-in: user started speaking ───────────────────────
                elif msg_type == "PartialTranscript":
                    text = (msg.get("text") or "").strip()
                    if text:
                        logger.debug("[realtime] PartialTranscript: %r", text)
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
                    logger.info("[realtime] FinalTranscript: %r", text)

                    if not text:
                        continue  # silence / noise — ignore

                    logger.info("[realtime] caller said: %r", text)
                    session.setdefault("turns", []).append({"role": "caller", "text": text})

                    if _llm_busy:
                        logger.info("[realtime] LLM busy — dropping utterance: %r", text)
                        continue

                    # Safety guard: ensure session is initialised before first LLM call
                    try:
                        await asyncio.wait_for(_twilio_started.wait(), timeout=5.0)
                    except asyncio.TimeoutError:
                        logger.warning("[realtime] session not initialised after 5 s — skipping")
                        continue

                    _llm_busy = True
                    try:
                        reply, transfer, llm_tts = await _llm_turn(
                            text, session, call_sid, websocket, stream_sid
                        )
                    except Exception as exc:
                        logger.error(
                            "\n" + "="*60 + "\n"
                            "[PIPELINE ERROR] _llm_turn raised unexpectedly\n"
                            "  call_sid : %s\n"
                            "  error    : %r\n"
                            + "="*60,
                            call_sid, exc, exc_info=True,
                        )
                        reply, transfer, llm_tts = _SAFE_FALLBACK, False, None
                    finally:
                        _llm_busy = False

                    if transfer:
                        return  # call handed off — stop processing events

                    if not _clearing:
                        if llm_tts:
                            if _elevenlabs_task and not _elevenlabs_task.done():
                                _elevenlabs_task.cancel()
                            _elevenlabs_task = llm_tts
                        elif reply and stream_sid:
                            if _elevenlabs_task and not _elevenlabs_task.done():
                                _elevenlabs_task.cancel()
                            _elevenlabs_task = asyncio.create_task(
                                _tts_to_twilio(reply, websocket, stream_sid)
                            )
                        if reply:
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

                # ── AssemblyAI v3 Turn event ───────────────────────────────
                # v3 sends Turn events (not PartialTranscript/FinalTranscript).
                # end_of_turn=False → partial (barge-in); end_of_turn=True → run LLM.
                # The transcript text is in msg["transcript"], not msg["text"].
                elif msg_type == "Turn":
                    transcript  = (msg.get("transcript") or "").strip()
                    end_of_turn = msg.get("end_of_turn", False)

                    if not end_of_turn:
                        # Partial — barge-in if TTS is currently playing
                        if transcript and _elevenlabs_task and not _elevenlabs_task.done():
                            logger.info(
                                "[realtime] barge-in call_sid=%s partial=%r",
                                call_sid, transcript[:40],
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
                    else:
                        # End of turn — send utterance to LLM
                        _clearing = False
                        logger.info("[realtime] Turn complete: %r", transcript)

                        if not transcript:
                            pass  # silence — skip
                        elif _llm_busy:
                            logger.info("[realtime] LLM busy — dropping: %r", transcript)
                        else:
                            logger.info("[realtime] caller said: %r", transcript)
                            session.setdefault("turns", []).append(
                                {"role": "caller", "text": transcript}
                            )

                            try:
                                await asyncio.wait_for(_twilio_started.wait(), timeout=5.0)
                            except asyncio.TimeoutError:
                                logger.warning(
                                    "[realtime] session not ready after 5 s — skipping"
                                )
                                pass
                            else:
                                _llm_busy = True
                                try:
                                    reply, transfer, llm_tts = await _llm_turn(
                                        transcript, session, call_sid,
                                        websocket, stream_sid,
                                    )
                                except Exception as exc:
                                    logger.error(
                                        "[PIPELINE ERROR] _llm_turn: call_sid=%s error=%r",
                                        call_sid, exc, exc_info=True,
                                    )
                                    reply, transfer, llm_tts = _SAFE_FALLBACK, False, None
                                finally:
                                    _llm_busy = False

                                if transfer:
                                    return  # call handed off

                                if not _clearing:
                                    if llm_tts:
                                        if _elevenlabs_task and not _elevenlabs_task.done():
                                            _elevenlabs_task.cancel()
                                        _elevenlabs_task = llm_tts
                                    elif reply and stream_sid:
                                        if _elevenlabs_task and not _elevenlabs_task.done():
                                            _elevenlabs_task.cancel()
                                        _elevenlabs_task = asyncio.create_task(
                                            _tts_to_twilio(reply, websocket, stream_sid)
                                        )
                                    if reply:
                                        session.setdefault("turns", []).append(
                                            {"role": "assistant", "text": reply}
                                        )

                                if call_sid:
                                    try:
                                        from app.storage.redis_store import save_session
                                        await save_session(call_sid, session)
                                    except Exception as exc:
                                        logger.warning(
                                            "[realtime] session save failed: %r", exc
                                        )

                # ── AssemblyAI closed the session ──────────────────────────
                elif msg_type in ("Terminate", "SessionTerminated"):
                    logger.info("[realtime] AssemblyAI session terminated")
                    break

                # ── Errors ────────────────────────────────────────────────
                elif msg_type in ("Error", "RealtimeError"):
                    logger.error("[realtime] AssemblyAI error: %s", msg.get("error"))

                else:
                    # Log any unrecognised event so we can see what v3 actually sends
                    if msg_type:
                        logger.info(
                            "[realtime] AssemblyAI unrecognised msg_type=%r msg=%s",
                            msg_type, json.dumps(msg, default=str)[:200],
                        )

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

# ---------------------------------------------------------------------------
# Diagnostic endpoint  — GET /realtime/test/assemblyai
# ---------------------------------------------------------------------------

@router.get("/realtime/test/assemblyai")
async def test_assemblyai():
    """
    Diagnose AssemblyAI connection issues without making a real call.

    Visit  /realtime/test/assemblyai  in your browser (or Render shell) to see:
      - Whether ASSEMBLYAI_API_KEY is set
      - Whether the REST API accepts the key (invalid key → 401)
      - Whether the WebSocket connects and returns SessionBegins
      - The verdict: what is actually wrong

    Typical failures:
      rest_api 401  → API key is wrong — update ASSEMBLYAI_API_KEY on Render
      ws 1011       → API key is valid but plan does not include streaming,
                      OR the speech_model value is wrong for your plan.
                      Set ASSEMBLYAI_USE_V2=true on Render to fall back to v2.
      ws 1000/1001  → Clean close (should not happen immediately).
    """
    from fastapi.responses import JSONResponse

    result: Dict[str, Any] = {
        "api_key_set":    bool(ASSEMBLYAI_API_KEY),
        "api_key_prefix": (ASSEMBLYAI_API_KEY[:8] + "...") if ASSEMBLYAI_API_KEY else None,
        "use_v2":         ASSEMBLYAI_USE_V2,
        "active_ws_url":  ASSEMBLYAI_WS_URL_V2 if ASSEMBLYAI_USE_V2 else ASSEMBLYAI_WS_URL,
        "rest_api":       None,
        "websocket":      None,
        "verdict":        None,
    }

    if not ASSEMBLYAI_API_KEY:
        result["verdict"] = "FAIL: ASSEMBLYAI_API_KEY is not set. Add it on Render."
        return JSONResponse(result, status_code=500)

    # ── 1. REST API check (validates the key) ─────────────────────────────
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(
                "https://api.assemblyai.com/v2/transcript",
                headers={"Authorization": ASSEMBLYAI_API_KEY},
                params={"limit": 1},
            )
            result["rest_api"] = {
                "status_code": resp.status_code,
                "ok":          resp.status_code == 200,
                "body_preview": "(ok)" if resp.status_code == 200 else resp.text[:300],
            }
    except Exception as exc:
        result["rest_api"] = {"ok": False, "error": str(exc)}

    # ── 2. WebSocket connection test ───────────────────────────────────────
    ws_url = ASSEMBLYAI_WS_URL_V2 if ASSEMBLYAI_USE_V2 else ASSEMBLYAI_WS_URL
    try:
        _ws = await asyncio.wait_for(
            websockets.connect(
                ws_url,
                additional_headers={"Authorization": ASSEMBLYAI_API_KEY},
                ping_interval=None,
                open_timeout=5,
            ),
            timeout=6.0,
        )
        try:
            raw = await asyncio.wait_for(_ws.recv(), timeout=4.0)
            data = json.loads(raw)
            result["websocket"] = {
                "connected":          True,
                # v3 uses 'type'; v2 uses 'message_type'
                "first_message_type": data.get("type") or data.get("message_type"),
                "session_id":         data.get("id") or data.get("session_id"),
                "raw_message":        data,
            }
        except websockets.exceptions.ConnectionClosed as exc:
            rcvd = exc.rcvd
            result["websocket"] = {
                "connected":    False,
                "close_code":   rcvd.code   if rcvd else "?",
                "close_reason": rcvd.reason if rcvd else "?",
            }
        except asyncio.TimeoutError:
            result["websocket"] = {
                "connected": True,
                "note": "No message in 4 s — session may need audio before responding.",
            }
        try:
            await _ws.close()
        except Exception:
            pass
    except asyncio.TimeoutError:
        result["websocket"] = {"connected": False, "error": "connect timed out after 6 s"}
    except Exception as exc:
        result["websocket"] = {"connected": False, "error": repr(exc)}

    # ── Verdict ────────────────────────────────────────────────────────────
    rest_ok = (result["rest_api"] or {}).get("ok")
    ws_info  = result["websocket"] or {}
    ws_ok    = ws_info.get("connected") or ws_info.get("first_message_type")

    if not rest_ok:
        result["verdict"] = (
            "FAIL: API key rejected by REST API (status "
            + str((result["rest_api"] or {}).get("status_code", "?"))
            + "). Update ASSEMBLYAI_API_KEY on Render."
        )
    elif not ws_ok:
        code = ws_info.get("close_code", "?")
        result["verdict"] = (
            f"FAIL: REST API OK but WebSocket closed with code {code}. "
            "Likely cause: your AssemblyAI plan does not include real-time streaming, "
            "or the speech_model is not available on your plan. "
            "Try setting ASSEMBLYAI_USE_V2=true on Render (uses the older v2 endpoint)."
        )
    else:
        result["verdict"] = "OK: AssemblyAI is reachable and the session opened correctly."

    status = 200 if ws_ok else 500
    return JSONResponse(result, status_code=status)

