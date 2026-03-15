# Media Streams Pipeline -- Implementation Progress

## Status Key
- [x] Complete
- [-] In progress
- [ ] Not started

---

## Phase 1: Foundation

- [x] **Step 1: Codebase audit** -- Full read of all project files
  - realtime.py (1400+ lines): full pipeline implementation audited
  - redis_store.py: session structure, all field names documented
  - fast_path.py: all 7 slot handlers understood
  - conversation.py: tool-calling loop, Haiku/Sonnet hybrid model selection
  - receptionist_tools.py: all tool executors understood
  - susie_system_prompt.py: dynamic prompt builder audited
  - clinic_config.py: Theorem + demo clinic configs, Acuity setup
  - config.py: all env vars documented
  - twilio.py: webhook flow, session init, status callback
  - See AUDIT.md for full analysis

- [x] **Step 2: Folder structure** -- All 14 files created
  - AUDIT.md, PROGRESS.md, __init__.py, config.py, session.py
  - connection.py, audio_in.py, audio_out.py
  - stt_stream.py, llm_stream.py, tts_stream.py
  - chunker.py, fast_path.py, router.py

- [x] **Step 3: config.py** -- Fully implemented
  - All API keys from environment variables
  - ElevenLabs voice ID (kBag1HOZlaVBH7ICPE8x) and model (eleven_flash_v2_5)
  - AssemblyAI v3 streaming endpoint and config (v2 fallback flag)
  - Claude model constants: SONNET = "claude-sonnet-4-20250514", HAIKU = "claude-haiku-4-5-20251001"
  - FastPathTurnType enum (7 turn types)
  - Chunk config: MIN_CHUNK_WORDS=15, MAX_CHUNK_WORDS=50, SENTENCE_END_CHARS
  - WebSocket timeouts: STT_SILENCE_TIMEOUT_MS=1000, LLM_FIRST_CHUNK_TIMEOUT_MS=5000, TTS_CHUNK_TIMEOUT_MS=3000
  - Audio format constants: mulaw 8kHz 8bit mono (Twilio), pcm_16000 (ElevenLabs)
  - Session field name constants (F_ prefix, 50+ constants)

- [x] **Step 4: session.py** -- Fully implemented
  - Redis storage with key prefix "ms_session:"
  - All existing session fields from DEFAULT_SESSION carried over
  - New fields: stream_sid, ws_connected, stt_active, tts_active, current_chunk_index,
    last_audio_sent_at, llm_generation_active, fast_path_last_resolved
  - Functions: create_session, get_session, update_session, save_session,
    delete_session, get_or_create_session
  - JSON serialisable (with TypeError fallback handling)
  - TTL of 2 hours (7200 seconds)
  - field_already_set(session, field_name) -> bool
  - get_collected_field(session, field_name) -> Optional[str]

- [x] **Step 5: PROGRESS.md** -- This file

---

## Phase 2: WebSocket and Audio Pipeline

- [x] **Step 6: connection.py** -- WebSocket lifecycle handler (COMPLETE)
  - WebSocketCallHandler class with 5 pipeline queues and 6 concurrent coroutines
  - receive_loop: parses connected/start/media/stop Twilio events
  - _handle_start: extracts call_sid/stream_sid, loads session, fires _started_event
  - _handle_media: decodes base64 mulaw, enqueues raw bytes, drops during barge-in
  - audio_in_loop: delegates to AudioInputProcessor.process_stream
  - stt_loop: delegates to STTStream.start with on_partial/on_final_clear callbacks
  - llm_loop: consumes transcript_queue, runs LLMStream.run_turn per utterance
  - tts_loop: consumes tts_text_queue, runs TTSStream.synthesise_chunk (cancellable)
  - send_loop: reads audio_out_queue, sends Twilio JSON media events
  - _on_partial_transcript: barge-in (cancel TTS, drain queues, send Twilio clear)
  - _on_final_transcript_clear: resets _clearing on each FinalTranscript
  - _inject_greeting: direct TTS greeting on call start (no LLM round-trip, ~500ms saving)
  - _cleanup: saves session, mirror-saves to call: prefix for /twilio/status webhook

- [x] **Step 7: audio_in.py** -- Inbound audio pipeline (COMPLETE)
  - AudioInputProcessor.convert_chunk: ulaw2lin + ratecv (8kHz->16kHz, stateful)
  - process_stream: async loop, buffers PCM_FLUSH_FRAMES (3x20ms) before forwarding
  - v2 fallback path: ulaw2lin only (8kHz PCM16 direct, no upsampling)
  - detect_silence(chunk, threshold): RMS-based silence detection helper
  - reset(): clears ratecv state on barge-in to prevent stale state corruption

- [x] **Step 8: stt_stream.py** -- AssemblyAI streaming STT (COMPLETE)
  - STTStream.start: opens WebSocket, runs send + receive concurrently
  - Authorization header auth (NOT ?token= URL param -- confirmed in realtime.py)
  - _send_audio_loop: forwards PCM16 chunks; keep-alive silence on idle (100ms timeout)
  - _receive_results_loop: routes PartialTranscript/FinalTranscript/Turn/error events
  - _is_garbage_transcript: filters noise-only transcripts (mm/uh/hmm/etc) before LLM
  - Reconnect: up to ASSEMBLYAI_MAX_RECONNECTS (2) with 0.5s/1.0s exponential backoff
  - transcript_queue full guard: discard oldest entry to make room for new final

- [x] **Step 9: audio_out.py** -- Outbound audio pipeline (COMPLETE)
  - AudioOutputProcessor.convert_chunk: 4-byte alignment + tomono 2:1 + lin2ulaw + b64
  - 4-byte remainder buffer maintained across chunks (audioop.tomono alignment requirement)
  - flush(): converts trailing bytes after last ElevenLabs chunk (prevents audio cutoff)
  - process_stream: async queue-based integration path (alternative to direct calling)
  - play_audio_file: load WAV/raw mulaw, auto-resample/convert, enqueue for Twilio
  - reset(): clears alignment state between TTS chunks on barge-in

---

## Phase 3: LLM and TTS Streaming

- [x] **Step 10: chunker.py** -- Text chunk boundary detection (COMPLETE)
  - ResponseChunker.add_token: buffers LLM tokens, emits at sentence boundaries
  - Emit condition 1: word_count >= MIN_WORDS (15) AND ends with .!? AND not abbreviation
  - Emit condition 2: word_count >= MAX_WORDS (50) -- hard cutoff
  - flush(): returns remaining buffer when LLM stream ends (tail never dropped)
  - reset(): clears state on barge-in
  - NEVER_SPLIT_AFTER: titles (mr/mrs/dr/prof), days (mon-sun), months, abbreviations
  - _ends_with_abbreviation: regex on last word before .!? to prevent false splits
  - chunk_text_static(): splits pre-formed text using same rules (for greeting/filler injection)

- [x] **Step 11: fast_path.py** -- Fast-path resolver adapter (COMPLETE)
  - FastPathResult dataclass: turn_type, value, response_text, needs_llm_followup
  - All 7 original handlers preserved: phone_last_six, phone_first_five, full_name,
    clinic_selection, new_returning, yes_no_confirmation, slot_selection
  - needs_llm_followup=False: clinic_selection, full_name, phone_first_five, phone_last_six
  - needs_llm_followup=True: new_returning, yes_no, slot_selection (interim + LLM follows)
  - Interim acknowledgements: "Right, let me just note that for you." etc.
  - Uses F_ constants for session field access (F_LAST_BOT_PROMPT, F_COLLECTED, etc.)
  - try_fast_path(session, transcript) -> Optional[FastPathResult]

- [x] **Step 12: llm_stream.py** -- Claude streaming LLM integration (COMPLETE)
  - LLMStream.run_turn: fast_path check first, then model selection, then Claude stream
  - Model selection: SONNET for booking steps (slot_selection etc), HAIKU for info/greetings
  - Date prefix injection: "Today is {weekday} {date}. This week ends Sunday {X}. Next week starts Monday {Y}."
  - _one_streaming_call: async with client.messages.stream(), ResponseChunker integration
  - Filler guard: 5s timeout -> FILLER_PHRASE, rate-limited by LLM_FILLER_COOLDOWN_SEC=20
  - _streaming_tool_loop: buffers tool_uses via get_final_message(), executes, re-streams
  - _execute_tools: calls receptionist_tools, saves updated session
  - _gpt_fallback: full OpenAI tool-calling loop on Claude 429/500/529
  - on_transfer callback for call_transfer tool result

- [x] **Step 13: tts_stream.py** -- ElevenLabs streaming TTS (COMPLETE)
  - TTSStream.synthesise_chunk (MODE A): POST to /stream?output_format=pcm_16000
  - output_format MUST be URL query param -- body field silently ignored (confirmed in realtime.py)
  - Streams PCM16 640-byte chunks through AudioOutputProcessor -> audio_out_queue
  - CancelledError handler: resets alignment state (clean start for next chunk after barge-in)
  - flush() called after stream end (prevents audio cutoff on last chunk)
  - _get_http_client(): shared httpx.AsyncClient singleton (connection pool, avoids TLS re-handshake)
  - start_ws (MODE B): persistent WebSocket for ultra-low-latency on long responses
  - WS init: chunk_length_schedule=[50,100,150], voice_settings, xi_api_key
  - _ws_send_text_loop: None sentinel -> flush message {"text": "", "flush": true}
  - _ws_receive_audio_loop: {"audio": b64} -> decode -> convert; {"isFinal": true} -> flush remainder

---

## Phase 4: Integration and Testing

- [x] **Step 14: router.py** -- FastAPI WebSocket route (COMPLETE)
  - @router.websocket("/twilio/media-stream-v2")
  - Instantiate WebSocketCallHandler, call handler.handle()
  - MEDIA_STREAMS_ENABLED gate: closes with 1001 if flag is false
  - Registration: app.include_router(media_streams_router) in main.py when flag is true

- [x] **Step 15: router.py full implementation** -- TwiML + WebSocket + error handling (COMPLETE)
  - POST /ms/incoming: returns TwiML <Stream url="wss://DOMAIN/ms/stream"/>
  - RENDER_EXTERNAL_URL env var for domain; falls back to Host header
  - MEDIA_STREAMS_ENABLED=false kill switch: returns <Redirect>/twilio/voice</Redirect>
  - /ms/incoming wrapped in try/except: any exception returns legacy redirect (never dead air)
  - WS /ms/stream: WebSocketCallHandler per connection
  - Unstable call detection: logs ERROR if _call_stable=False on exception
  - Calls handler.play_pipeline_failure() on unstable exception before closing
  - Graceful WebSocket close (1011) on unhandled exception

- [x] **Step 16: connection.py error handling** -- Watchdog + re-ask + stability (COMPLETE)
  - WatchdogTimer (_watchdog_loop): every 0.5s, fires rotating bridge phrase if silence > 3s
  - WATCHDOG_PHRASES: ["Just bear with me...", "Let me just check...", "One moment please..."]
  - Watchdog conditions: _llm_busy AND _last_audio_at > 0 AND no TTS active AND queue empty
  - _watchdog_armed flag: only activates after greeting plays (prevents false fires at startup)
  - _silence_reask_loop: every 1s, re-asks last question if caller silent > 5s after question
  - MAX_REASK_ATTEMPTS=2 before offering TRANSFER_OFFER_PHRASE + triggering transfer
  - _record_question(): called after greeting and after each LLM turn (tracks last_bot_prompt)
  - _on_final_transcript_clear(): resets _reask_count and _last_question_at on each transcript
  - _call_stable flag: set True after first complete STT->LLM->TTS cycle
  - play_pipeline_failure(): plays PIPELINE_FAILURE_PHRASE then sets stop_event
  - LLM error handling: plays CLAUDE_ERROR_PHRASE on exception, logs full traceback
  - 8 concurrent tasks: receive, audio_in, stt, llm, tts, send, watchdog, reask

- [x] **Step 17: config.py additions** -- Watchdog + re-ask + domain constants (COMPLETE)
  - RENDER_EXTERNAL_URL: domain for building wss:// URL in /ms/incoming
  - LEGACY_VOICE_URL = "/twilio/voice": fallback redirect target
  - WATCHDOG_SILENCE_SEC = 3.0: dead air threshold
  - WATCHDOG_PHRASES: 3 rotating bridge phrases
  - QUESTION_SILENCE_SEC = 5.0: silence threshold before re-ask
  - MAX_REASK_ATTEMPTS = 2: max re-asks before transfer
  - REASK_PREFIX = "Sorry about that — "
  - TRANSFER_OFFER_PHRASE: played after max re-asks exceeded
  - CLAUDE_ERROR_PHRASE: played on recoverable LLM error
  - PIPELINE_FAILURE_PHRASE: played on total pipeline collapse

- [x] **Step 18: main.py registration** -- Single allowed external change (COMPLETE)
  - Added import: from app.media_streams.config import MEDIA_STREAMS_ENABLED
  - Added import: from app.media_streams.router import router as media_streams_router
  - Conditional registration: if MEDIA_STREAMS_ENABLED: app.include_router(media_streams_router)
  - Startup log: "Media Streams system: ENABLED/DISABLED" based on env var
  - All existing routes completely unaffected

- [x] **Step 19: TEST_PROTOCOL.md** -- 50-call test protocol (COMPLETE)
  - Phase 1 (5 calls): Basic connection, greeting, WebSocket lifecycle
  - Phase 2 (10 calls): All 7 fast-path turn types with pass/fail criteria
  - Phase 3 (15 calls): Full booking flow end-to-end with session validation
  - Phase 4 (10 calls): Edge cases (correction, next-week, silence, off-script, unclear name)
  - Phase 5 (10 calls): Stress test — back-to-back calls, session isolation check
  - Cut-over criteria: 50/50 pass, latency < 2s on 90%, zero unstable calls
  - Cut-over instructions: exact Twilio console steps to switch Mark's number
  - Rollback: revert webhook URL to /twilio/voice (legacy system always available)
  - Monitoring guide: log patterns to watch during testing

- [x] **Final check: all 15 files present in app/media_streams/**
  - __init__.py, config.py, session.py, connection.py
  - audio_in.py, audio_out.py, stt_stream.py, llm_stream.py, tts_stream.py
  - chunker.py, fast_path.py, router.py
  - AUDIT.md, PROGRESS.md, TEST_PROTOCOL.md

---

## Notes

- The existing realtime.py (/twilio/media-stream) is completely unchanged.
- This package runs on /twilio/media-stream-v2 (separate route).
- Switch by changing the Stream URL in twilio.py -- both pipelines can coexist.
- Both pipelines share app.fast_path, app.tools, app.storage.redis_store (different key prefix).
- CRITICAL: On disconnect, session is mirror-saved to call: prefix so /twilio/status works.
- Model IDs: SONNET = "claude-sonnet-4-20250514" (correct), not "claude-sonnet-4-6" (realtime.py).
