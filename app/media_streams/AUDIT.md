# Media Streams Pipeline — Full Codebase Audit

Audit date: 2026-03-15
Auditor: Claude Sonnet 4.6
Source project: C:\Users\quent\OneDrive\Documents\GitHub\rochsolutions-ai-receptionist

---

## 1. What realtime.py currently contains (and how much is already built)

**File:** `app/routes/realtime.py` (~1400 lines)

This file is a COMPLETE, production-ready WebSocket voice pipeline. It is NOT a skeleton — it is the fully operational system currently handling live calls. Key facts:

### What is built and working

**Architecture:**
- FastAPI WebSocket endpoint at `/twilio/media-stream`
- Two concurrent asyncio tasks: `_twilio_to_assemblyai` and `_assemblyai_events`
- Both tasks run via `asyncio.gather()` for true concurrent I/O

**STT (_twilio_to_assemblyai):**
- Accepts Twilio G.711 µ-law 8kHz audio via WebSocket
- Decodes base64 payload -> raw µ-law bytes
- Converts µ-law -> PCM16 via `audioop.ulaw2lin`
- Upsamples 8kHz -> 16kHz via `audioop.ratecv` (for v3 compatibility)
- Buffers 3 Twilio frames (60ms) before forwarding to AssemblyAI
- Handles "clearing" state during barge-in (drops audio)
- Supports both v3 (16kHz PCM16, universal-streaming-english) and v2 (8kHz, ASSEMBLYAI_USE_V2 flag)
- Reconnect on AssemblyAI disconnect (up to 2 attempts, 0.5s/1.0s backoff)

**LLM (_llm_turn):**
- Claude Sonnet 4.6 (CLAUDE_MODEL = "claude-sonnet-4-6") non-streaming
- Full tool-calling loop (up to MAX_TOOL_ITERATIONS=6)
- Anthropic native format (input_schema, not parameters)
- All TOOL_SCHEMAS from receptionist_tools.py used directly
- Pre-tool TTS: text alongside tool calls played immediately while tools execute
- GPT-4.1-mini automatic fallback when Claude returns 529/500
- 5-second filler guard ("Just one moment...") if Claude takes too long
  - Rate-limited to once per 20 seconds (_last_filler_at tracking)
- Prompt caching: system prompt sent with `cache_control: ephemeral`
- Singleton `AsyncAnthropic` client (persistent httpx connection pool)

**TTS (_tts_to_twilio):**
- ElevenLabs Flash v2.5 (eleven_flash_v2_5)
- voice_id from env var `ELEVENLABS_VOICE_ID` (default: kBag1HOZlaVBH7ICPE8x)
- output_format=pcm_16000 as URL query param (NOT body field — body silently ignored)
- Streaming response chunks: 640 bytes each (20ms of 16kHz PCM16)
- audioop.tomono: 2:1 anti-aliased decimation 16kHz -> 8kHz
  (treats mono 16kHz as stereo 8kHz, averages L+R = null at 4kHz Nyquist)
- audioop.lin2ulaw: PCM16 -> G.711 µ-law 8-bit
- Persistent httpx.AsyncClient (connection pooling, avoids TLS re-handshake per call)
- Runs as asyncio Task so it can be cancelled on barge-in
- On CancelledError: sends Twilio `clear` event to drain Twilio's audio buffer

**Barge-in:**
- PartialTranscript event with non-empty text -> cancel TTS task, send Twilio clear
- FinalTranscript resets _clearing=False
- _clearing flag prevents new audio being sent during buffer drain

**Session management:**
- Loads from Redis via `get_session(call_sid)` on "start" event
- Saves to Redis via `save_session(call_sid, session)` after each tool round and on disconnect
- Uses existing `call:` prefix keys in Redis

**Greeting injection:**
- On "start" event: plays greeting via ElevenLabs TTS without LLM round-trip
- Saves ~500ms on first word of the call
- Seeds conversation_history with user=[call connected] / assistant=[greeting]

**AssemblyAI event handling:**
- v2: PartialTranscript + FinalTranscript events
- v3: Turn events (end_of_turn=True/False unified)
- Garbage transcript detection (_is_garbage_transcript): noise-only words, no real words
- Bad-line phrase: played once per call after 10s silence gap
- LLM-busy guard: drops FinalTranscript if LLM is already processing

**What is NOT yet built (the gap this package fills):**
- Streaming LLM output (currently waits for full response before TTS starts)
- Chunked TTS delivery (currently sends full response text to ElevenLabs in one shot)
- The result: first audio is heard ~3-6 seconds after caller finishes speaking (full LLM + full TTS generation)
- The goal of the parallel architecture: first audio heard within ~1-2 seconds (first text chunk -> TTS chunk pipeline)

---

## 2. Exact gaps and estimated complexity

### Gap 1: Streaming LLM output
**Current:** `client.messages.create()` — waits for complete Claude response before TTS starts.
**Target:** `client.messages.stream()` — stream tokens, feed to chunker, start TTS on first chunk.
**Complexity: HIGH**
- Requires refactoring the tool-calling loop to work with streaming
- Tool calls still require response buffering (Claude API limitation)
- Text alongside tool calls needs to be extracted and streamed before tool executes
- The streaming event loop must interleave with barge-in handling

### Gap 2: Text chunker
**Current:** Full response text sent to ElevenLabs as one string.
**Target:** `chunker.py` splits streaming tokens into 15-50 word speakable chunks.
**Complexity: MEDIUM**
- Word counting must be accurate across token boundaries
- Sentence boundary detection must work correctly for UK English phrasing
- "..." (ellipsis) must be treated as sentence end
- Flush logic needed for end of stream when buffer has fewer than MIN_CHUNK_WORDS

### Gap 3: Chunked TTS pipeline
**Current:** One ElevenLabs request per LLM response.
**Target:** One ElevenLabs request per text chunk; audio delivery begins on first chunk.
**Complexity: MEDIUM**
- Each chunk needs its own ElevenLabs streaming request
- Must maintain chunk ordering (chunk 1 audio plays before chunk 2 starts)
- Barge-in must cancel the current chunk's TTS task AND discard queued chunks
- Connection pooling must be managed across multiple concurrent chunk requests

### Gap 4: Session module separation
**Current:** realtime.py uses `app.storage.redis_store` directly (call: prefix).
**Target:** media_streams/session.py uses ms_session: prefix.
**Complexity: LOW**
- session.py is now fully implemented
- The key change is the prefix to prevent collision with legacy sessions

### Gap 5: Router module
**Current:** realtime.py registers its own route directly.
**Target:** router.py registers /twilio/media-stream-v2.
**Complexity: LOW**
- Placeholder implemented
- Full implementation requires wiring up the pipeline modules

### Gap 6: Fast-path integration
**Current:** realtime.py does NOT call resolve_fast_path (gap in current implementation).
**Target:** fast_path.py adapter tries resolve_fast_path before LLM each turn.
**Complexity: LOW**
- fast_path.py adapter is implemented
- Integration into llm_stream.py is straightforward

---

## 3. Conflicts and dependencies with existing webhook system

### Redis key namespace
- Legacy keys: `call:{call_sid}` and `call:{call_sid}:{hmac8}` (when SESSION_SECRET set)
- Media Streams keys: `ms_session:{call_sid}` (separate prefix, no collision)
- IMPORTANT: Both pipelines can be active simultaneously without interference
- The /status webhook (twilio.py) reads legacy `call:` keys — if the media_streams pipeline
  saves only to `ms_session:`, the status callback will get a fresh (empty) session
  when it calls `get_session(call_sid)` from redis_store.py
- FIX REQUIRED: Either (a) mirror-save to the `call:` key as well, or (b) update
  /status to also check `ms_session:` prefix, or (c) save to both on disconnect

### Twilio route registration
- Legacy: /twilio/media-stream (realtime.py) — active when REALTIME_ENABLED=true
- New: /twilio/media-stream-v2 (media_streams/router.py) — active when MEDIA_STREAMS_ENABLED=true
- TwiML switch needed in twilio.py to point `<Stream url>` at the new endpoint
- Both can coexist — switching is just changing the URL string in one place

### Tool executors
- Both pipelines use the SAME `app.tools.receptionist_tools.TOOL_EXECUTORS`
- No changes needed to tools
- Session passed by reference — tool mutations are visible immediately

### System prompt
- Both pipelines use the SAME `app.prompts.susie_system_prompt.get_system_prompt(session)`
- No changes needed to the prompt builder

### Greeting
- realtime.py calls `app.routes.twilio._build_greeting(clinic)` — an internal function
- media_streams will need to either import this or duplicate the minimal logic
- Low risk: the function is simple (returns clinic.greeting or a default string)

### Status callback (/twilio/status)
- Runs AFTER call ends, reads session from Redis
- Builds call summary, sends to Google Sheets, sends SMS
- Currently reads from `call:{call_sid}` prefix
- If media_streams saves to `ms_session:` only, status callback gets empty session
- CRITICAL: Must bridge the session to `call:` prefix on disconnect

### Acuity booking idempotency
- acquire_once_lock is used in twilio.py to prevent double-booking
- media_streams should use the same lock key pattern for consistency

---

## 4. All session fields to carry over

From `redis_store.py DEFAULT_SESSION` (confirmed by audit):

### Core state
| Field | Type | Description |
|-------|------|-------------|
| intent | str/None | Caller intent (legacy triage) |
| state | str | "TRIAGE" (legacy triage state machine) |
| collected | dict | All collected booking data (name, phone, service, etc.) |
| miss_count | int | Consecutive missed/confused turns |
| error_count | int | Error count |
| last_bot_prompt | str | Last response Susie spoke (prevents repetition) |
| last_question | str | Mirrors last_bot_prompt for no-input recovery |
| call_sid | str | Twilio call SID |
| session_id | str | Internal UUID |
| clinic_id | str/None | Active clinic (theorem/demo) |

### Location state
| Field | Type | Description |
|-------|------|-------------|
| location_selected | bool | True once location confirmed |
| selected_location | str/None | "alcester" or "redditch" |
| location_miss | int | Failed location-select attempts |
| location_redirect_count | int | Guards against infinite /voice redirect |

### Conversation history
| Field | Type | Description |
|-------|------|-------------|
| conversation_history | list | [{role, content}] sent to Claude each turn |
| turns | list | [{role, text}] full call log used by SMS router |
| call_start_time | str/None | ISO timestamp for call summary |

### Insurance
| Field | Type | Description |
|-------|------|-------------|
| insurance_flagged | bool | Insurance mentioned on call |
| insurance_info | dict | Collected insurer/policy data |

### Booking state
| Field | Type | Description |
|-------|------|-------------|
| last_offered_slots | list | Slots offered to caller (for slot selection) |
| slot_labels | list | Human-readable slot labels |
| acuity_booking_id | str/None | Acuity booking ID after confirmed booking |
| calendar_status | str/None | Google Calendar booking status |

### Workflow flags
| Field | Type | Description |
|-------|------|-------------|
| manual_followup_needed | bool | Requires human follow-up |
| manual_followup_reason | str/None | Why manual follow-up is needed |
| confirmation_sms_sent | bool | Booking confirmation SMS already sent |
| call_summary_logged | bool | Idempotency flag for Sheets logging |
| transfer_attempted | bool | Transfer was attempted this call |
| transfer_failed_status | str/None | Transfer failure reason |
| request_transfer | bool | Signal from tool: initiate transfer now |

### Fast-path state
| Field | Type | Description |
|-------|------|-------------|
| phone_part_one | str/None | First 5 digits of caller-dictated phone |
| phone_part_two | str/None | Last 6 digits |
| selected_slot | dict/None | Slot chosen by fast-path slot-selection |
| _fast_path_phone_confirmed | bool | Caller confirmed caller-ID phone number |
| _fast_path_slot_confirmed | bool | Caller confirmed chosen slot |
| _fast_path_final_confirmed | bool | Caller confirmed final booking summary |
| _fast_path_correction_needed | bool | Caller said "no" to confirmation |
| _fast_path_full_phone | str/None | Assembled 11-digit phone |

### Caller phone numbers (set in realtime.py, not in DEFAULT_SESSION)
| Field | Type | Description |
|-------|------|-------------|
| twilio_from | str/None | Caller's number from Twilio (E.164) |
| twilio_from_local | str/None | UK local format (07xxxxxxxxx) |
| twilio_to | str/None | Dialled number (for clinic lookup) |

### New Media Streams fields (added in session.py)
| Field | Type | Description |
|-------|------|-------------|
| stream_sid | str/None | Twilio stream SID from "start" event |
| ws_connected | bool | True while Twilio WS is open |
| stt_active | bool | True while AssemblyAI session is live |
| tts_active | bool | True while ElevenLabs TTS is streaming |
| llm_generation_active | bool | True while Claude is generating tokens |
| current_chunk_index | int | Incremented for each TTS chunk sent |
| last_audio_sent_at | str/None | ISO timestamp of last audio packet |
| fast_path_last_resolved | str/None | FastPathTurnType of last fast-path match |

---

## 5. All external API integrations needing parallel versions

### 5.1 AssemblyAI (STT)
- **Current:** `websockets.connect()` in realtime.py with direct audio forwarding
- **Version used:** v3 Universal Streaming (primary), v2 fallback
- **Auth:** `Authorization` header (NOT `?token=` URL param)
- **Input:** PCM16 at 16kHz (upsampled from Twilio 8kHz µ-law via audioop.ratecv)
- **v2 input:** PCM16 at 8kHz (direct µ-law -> PCM16 conversion, no upsampling)
- **Parallel version needed:** stt_stream.py — same WebSocket approach, same auth
- **No API changes needed:** same endpoint, same config

### 5.2 Anthropic (Claude LLM)
- **Current:** `AsyncAnthropic.messages.create()` (non-streaming, full response)
- **Singleton:** Module-level `_get_anthropic_client()` maintains connection pool
- **Tools:** TOOL_SCHEMAS from receptionist_tools.py (Anthropic native format)
- **Model:** claude-sonnet-4-6 (primary), claude-haiku-4-5-20251001 (fast turns in conversation.py)
- **Parallel version needed:** llm_stream.py — CHANGE to `messages.stream()` for streaming
- **Key difference:** Streaming API requires different event handling for tool calls
  - Streaming: accumulate tool_use blocks until complete, then execute
  - Text blocks: emit tokens to chunker as they arrive

### 5.3 ElevenLabs (TTS)
- **Current:** `httpx.AsyncClient.stream("POST", ...)` — full response as one request
- **Singleton:** Module-level `_get_elevenlabs_client()` maintains connection pool
- **Endpoint:** `/v1/text-to-speech/{voice_id}/stream?output_format=pcm_16000`
- **Model:** eleven_flash_v2_5
- **CRITICAL gotcha:** output_format MUST be URL query param, NOT body field
  Body field is silently ignored; confirmed via Content-Type header inspection
- **Parallel version needed:** tts_stream.py — SAME approach but one request per chunk
- **Connection pool:** Must be shared across chunk requests to avoid TLS overhead

### 5.4 Twilio WebSocket (Media Streams)
- **Current:** FastAPI `WebSocket` in realtime.py
- **Protocol:** Twilio sends JSON events over the WebSocket
  - "connected": initial connection
  - "start": call metadata (callSid, streamSid, customParameters)
  - "media": base64-encoded G.711 µ-law audio (20ms frames)
  - "stop": call ended
- **Outbound:** JSON media events with base64 µ-law audio payloads
  - event="media", streamSid=..., media.payload=base64(µ-law)
  - event="clear", streamSid=... (to drain Twilio's buffer on barge-in)
- **Parallel version needed:** connection.py handles same WebSocket protocol
- **No API changes needed:** same event format

### 5.5 OpenAI GPT-4.1-mini (fallback LLM)
- **Current:** `AsyncOpenAI.chat.completions.create()` — non-streaming fallback
- **Used:** Only when Claude returns 529 (overloaded) or hard error
- **Parallel version needed:** llm_stream.py — SAME approach but streaming
  (`AsyncOpenAI.chat.completions.create(stream=True)`)
- **Tool format:** OpenAI format (parameters not input_schema) — converted in _build_openai_tools()

### 5.6 Redis (session storage)
- **Current:** `app.storage.redis_store` — DIRECT usage via module imports
- **Key prefix:** `call:{call_sid}` (existing), `call:{call_sid}:{hmac8}` (when SESSION_SECRET set)
- **Parallel version needed:** session.py — uses `ms_session:{call_sid}` prefix
- **Shared infrastructure:** Same Redis instance, different key namespace
- **IMPORTANT:** /status webhook reads from `call:` prefix — bridging required (see Section 3)

### 5.7 Acuity Scheduling (booking)
- **Current:** Accessed via TOOL_EXECUTORS["book_appointment"], etc.
- **Adapter:** AcuityAdapter in receptionist_tools.py (httpx connection pool singleton)
- **Parallel version needed:** NONE — same TOOL_EXECUTORS are reused directly

### 5.8 Google Calendar (booking fallback / demo clinic)
- **Current:** Accessed via TOOL_EXECUTORS["book_appointment"] for demo clinic
- **OAuth tokens:** Stored in Redis under "google_tokens" key
- **Parallel version needed:** NONE — same TOOL_EXECUTORS are reused directly

### 5.9 Twilio REST API (live transfer)
- **Current:** `TwilioClient.calls(call_sid).update(twiml=...)` — mid-call TwiML injection
- **Used for:** Transfer to human (escalate_to_human tool)
- **Parallel version needed:** NONE — same `_handle_transfer()` logic can be reused
  (import from realtime.py or duplicate the ~20-line function)

### 5.10 Twilio SMS
- **Current:** `send_sms()` in notifications/booking_sms.py
- **Used for:** Post-call smart SMS via send_smart_followup_sms()
- **Parallel version needed:** NONE — same function, same session structure

---

## 6. Latency analysis: current vs target

### Current pipeline (realtime.py)
```
Caller stops speaking
  -> STT silence detection (1200ms threshold)       ~1.2s
  -> FinalTranscript received                        ~0.1s
  -> Claude API (full response, non-streaming)       ~2-4s
  -> ElevenLabs TTS (full text, streaming out)       ~0.5-1.5s
  -> First audio byte heard by caller                ~4-7s total
```

### Target pipeline (media_streams)
```
Caller stops speaking
  -> STT silence detection (1200ms threshold)       ~1.2s
  -> FinalTranscript received                        ~0.1s
  -> Claude API streams first ~20 words              ~0.8-1.5s
  -> chunker emits first chunk                       ~0.0s (immediate)
  -> ElevenLabs TTS first chunk (15-50 words)        ~0.3-0.8s
  -> First audio byte heard by caller                ~2.4-3.6s total
                                                     (1.5-3s improvement)
```

### Key insight
The improvement comes from PARALLELISING the end of LLM generation with the start of TTS.
While Claude is generating words 20-200, ElevenLabs is already synthesising words 1-19.

---

## 7. Files NOT modified by this package

The following files are explicitly NOT modified — the parallel pipeline runs independently:

- `app/routes/realtime.py` — unchanged
- `app/routes/twilio.py` — unchanged (TwiML route switch is a future step)
- `app/storage/redis_store.py` — unchanged (session.py uses same redis_client)
- `app/flows/conversation.py` — unchanged (escalate_to_claude still delegates here)
- `app/tools/receptionist_tools.py` — unchanged (TOOL_EXECUTORS reused directly)
- `app/prompts/susie_system_prompt.py` — unchanged
- `app/fast_path.py` — unchanged (fast_path.py adapter calls it)
- `app/clinic_config.py` — unchanged
- `app/config.py` — unchanged (media_streams/config.py reads same env vars)
- `app/main.py` — will need a one-line router registration (Phase 3)

---

## 8. Risk register

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Status webhook reads empty session (ms_session: not visible to call: reader) | HIGH | HIGH | Save to both prefixes on disconnect, or update status route to check both |
| ElevenLabs rate limits on multiple concurrent chunk requests | MEDIUM | MEDIUM | Shared connection pool; chunk requests are sequential per call |
| Claude streaming API tool-call handling complexity | HIGH | MEDIUM | Test tool calls with streaming; accumulate tool_use blocks before executing |
| Chunk boundary mid-word (e.g. "physio-" then "therapist") | LOW | LOW | chunker.py buffers full tokens; Claude emits full words |
| AssemblyAI disconnect during long turns | MEDIUM | HIGH | Reconnect logic already proven in realtime.py; copy to stt_stream.py |
| Barge-in race: new utterance arrives during chunk queue flush | MEDIUM | MEDIUM | Cancel all queued TTS chunks atomically; clear stream_sid state |

---

## 9. Key design decisions for the parallel pipeline

1. **Use SONNET constant (not the literal string)** — The model ID in realtime.py is "claude-sonnet-4-6" which is outdated. config.py correctly uses "claude-sonnet-4-20250514".

2. **Haiku for simple turns** — conversation.py already implements hybrid Haiku/Sonnet selection. llm_stream.py should adopt the same `_pick_model()` logic.

3. **Chunk ordering guarantee** — TTS chunks must play in order. Use `asyncio.Queue` to serialise chunk delivery even when LLM streams faster than TTS can consume.

4. **Barge-in atomicity** — On barge-in: (1) cancel current TTS task, (2) send Twilio clear, (3) drain the chunk queue, (4) set _clearing=True. All four steps must happen before any new LLM call starts.

5. **Connection pool reuse** — ElevenLabs httpx client must be module-level singleton, not created per chunk. This is critical for latency — TLS handshake = ~100ms.

6. **Session save frequency** — Save after each tool round (same as realtime.py). Also save to `call:` prefix on disconnect so /status webhook can read the final state.
