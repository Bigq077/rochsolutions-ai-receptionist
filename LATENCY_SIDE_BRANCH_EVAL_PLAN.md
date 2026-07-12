# Susie Latency Eval — Side-Branch Plan

**Status:** planning only. Nothing here is implemented. No change to the live system.
**Date:** 2026-07-13

---

## 0. Prime directive — total isolation from live

- **The production number, `main`, and every live clinic branch stay exactly as they are.** No exceptions, at any point in this eval.
- All work happens on a throwaway branch (`latency-eval`) deployed to its **own Render service** behind its **own Twilio number**.
- This branch is a **lab**, not a release candidate. Even if a lever wins, promoting it to live is a *separate*, later decision under the normal ship/stability rules — not part of this eval.
- CLAUDE.md guardrails still apply on the side branch (feature-flag new subsystems, no PII in commits/logs, Frankfurt/EU only).

---

## 1. Test harness setup (one-time)

| Item | Detail |
|---|---|
| Branch | `latency-eval` off current `main` |
| Deploy | New Render service (Frankfurt/EU), `autoDeploy` off — deploy manually so nothing ships by surprise |
| Number | A **separate** Twilio number pointing its Media-Stream webhook at the side-branch service |
| Config | Every lever behind its own env flag, all default OFF, so the side branch boots byte-identical to live and each lever is toggled independently |
| Data | Test calls only. If real-call audio is used for endpointing calibration, it must be **redacted** before any of it lands in the repo (UK GDPR — health data) |

**Why a separate number, not a staging path on the same service:** guarantees zero shared state (Redis prefixes, sessions, env) with the live line. A misconfigured flag can never touch a real patient call.

---

## 2. Measurement methodology (do this BEFORE touching any lever)

You cannot tune what you can't measure. The whole eval hinges on one metric:

> **Voice-to-voice latency** = wall-clock from *caller stops speaking* → *first audio byte sent to Twilio*.

Instrument the side branch (side branch only) to log, per turn:
- `t0` — `end_of_turn=true` final received (`stt_stream.py:636`)
- `t1` — first TTS audio chunk enqueued to `audio_out_queue` (`_tts_loop`, `connection.py:~10574`)
- Derived sub-splits: `t0→first LLM token`, `first token→first chunk emitted` (the chunk-gate cost), `chunk emitted→first audio` (the TTS cost)

Log these as structured lines (no transcript content needed → no PII). Aggregate p50/p90/p95 across ≥30 turns per config. **Single-call anecdotes are not evidence** — the win is a distribution shift, and the failure modes (mid-number cut-offs) live in the tail, so p95 matters more than p50.

---

## 3. Baseline (measure first, with all flags OFF)

Expected live composition (voice-to-voice), from code inspection:

| Stage | Where | ~Time |
|---|---|---|
| Endpointing (`min_turn_silence=600`) | `config.py:102` | ~600ms |
| Final transcript delivered | — | ~100ms |
| Claude first token (cached prompt) | — | ~400–700ms |
| **Chunker waits to 15 words** | `chunker.py:41` (live) — NOT the dead `MIN_CHUNK_WORDS=8` in `config.py:168` | ~+300–500ms |
| ElevenLabs first audio (HTTP per-chunk) | `_tts_loop` → `synthesise_chunk` (MODE A) | ~300–800ms |
| **Total** | | **~1.8–2.3s** |

Confirm these numbers empirically before optimizing — if the real baseline differs, the lever priorities may change.

---

## 4. The three workstreams (levers)

Each is independently flagged and independently measurable. Ordered by **win ÷ risk**.

### WS-A — Dead-config chunk gate  *(largest, simplest, do first)*

- **Finding:** `config.py:168` sets `MIN_CHUNK_WORDS = 8` with a comment claiming a ~300–500ms saving — but **nothing imports it**. The live chunker uses its own hardcoded `MIN_WORDS = 15` (`chunker.py:41`, read at `:161`). The intended optimization was never wired.
- **Eval:** on the side branch, make the chunker's first-chunk threshold configurable and test **first-sentence emit** (emit at first `.!?`, ~5–8 words) vs the current 15.
- **Expected:** ~200–400ms off first audio, every turn.
- **Watch for:** clipped/awkward openers on very short first sentences. The existing `FORBIDDEN_CHUNK_STARTERS` guard already blocks the ugly time-phrase splits; confirm it still holds at the lower threshold.
- **Pass/fail:** p90 first-audio drops meaningfully AND no increase in choppy-opener incidents on the listen-back set.

### WS-B — WebSocket stream-input TTS (MODE B)  *(the "big rebuild")*

- **Finding:** MODE B (`start_ws`, `tts_stream.py:503`) is substantially built (connect, init, send/recv loops, reconnect) but **unwired** and **not integrated** with the live `_tts_loop` orchestration.
- **The blocker that stalled it — prove this FIRST (spike):** barge-in atomicity over a long-lived socket. In MODE A an interrupt cancels the in-flight HTTP task + drains the queue (clean). Over a persistent WS, audio for a whole utterance may already be buffered in ElevenLabs; cancelling means tearing down / resetting the socket and discarding in-flight audio, or the caller hears a fragment *after* interrupting. **If clean barge-in can't be demonstrated, WS-B stops here** — the latency win isn't worth a broken interrupt experience.
- **Then port** the `_tts_loop` responsibilities onto the WS path: dedup (`_last_tts_chunk`), `DEDUP_RESET` sentinel, watchdog re-ask markers, `_tts_playing` state, silence-timer arming (`on_tts_finished`).
- **Tune:** lower `chunk_length_schedule` from `[50, 100, 150]` (chars) toward `[20]` — the current value buffers 50 chars before first audio and would eat the win.
- **Expected:** further ~150–300ms off TTS first-byte.
- **Pass/fail:** barge-in feels as clean as MODE A (the gate) AND TTS first-byte p90 improves.

### WS-C — Semantic endpointing (the sub-400ms lever + its safety net)

- **Finding:** you run AssemblyAI v3 (`universal-streaming-english`) and consume its `end_of_turn` boolean (`stt_stream.py:636`), but you send **only** `min_turn_silence=600`. `end_of_turn_confidence_threshold` (default 0.4) and `max_turn_silence` (default 1280ms) are **unset** — so v3's semantic endpointer is effectively running as a plain silence timer. The best feature is switched off.
- **How the safety net works:** two-stage. After `min_turn_silence` of silence, if the model's *semantic* confidence that the thought is complete exceeds `end_of_turn_confidence_threshold` → fire immediately (AssemblyAI's aggressive preset fires at **160ms**); else keep listening up to `max_turn_silence`, then force. Fast when confident, patient when not.
- **Reliability reality — this is the crux of the eval:** it's probabilistic and fails *predictably* on the hardest turns — **phone-number readback, surname spelling, hesitant/elderly mid-sentence pauses** — where semantic completeness is genuinely ambiguous. There, low confidence means either no speed gain (it waits) or, if you force the floor down globally, **mid-number cut-offs**. A single aggressive number across the whole call is the wrong design.
- **The design to validate:**
  1. **Dynamic, per-phase tuning** (params are updatable mid-stream): tight floor (~160–250ms) during free conversation; loosen (raise `max_turn_silence`, lean on confidence, don't force) the moment the call enters **phone or name capture**. The capture phases are already explicit in the flow — gate the thresholds on them.
  2. **Calibrate from real calls, not guesses:** use AssemblyAI's [historical-audio turn-detection analysis](https://www.assemblyai.com/docs/guides/turn_detection_improvement_using_async) on **redacted** real-call audio to pick thresholds.
- **Expected:** ~300–400ms off *conversational* turns; **deliberately little-to-none** on capture turns (that's correct, not a miss).
- **Pass/fail gate (hard):** **zero tolerance for mid-capture cut-offs.** The explicit gate is a set of phone-number and surname-spelling test turns (including a deliberately slow/pausing speaker) — if any get chopped, WS-C fails regardless of the conversational-turn win.
- **Model caveat:** Universal-3 **Pro** uses a different, punctuation-based endpointer and **deprecates** `end_of_turn_confidence_threshold`. Pin the model version in the eval; this lever changes shape if the model changes.

---

## 5. Sequence

1. Stand up harness (§1) + measurement (§2). Record baseline (§3).
2. **WS-A** — biggest, simplest, immediate signal. Measure.
3. **WS-C spike** — calibrate + prove the capture-turn safety gate. Measure conversational vs capture separately.
4. **WS-B spike** — prove clean barge-in FIRST; only then port `_tts_loop` orchestration. Measure.
5. Stack the winners; measure combined (they overlap — expect **~300–500ms total**, not the sum).

Do them one at a time with clean measurement between. Stacking un-measured changes hides which lever paid.

---

## 6. Honest expectation

- Realistic combined win: **~300–500ms** voice-to-voice → from ~1.8–2.3s down to ~1.4–1.7s.
- **Sub-1s / "800ms" is not on the table** here: those figures usually exclude the endpointing wait or assume ~300ms semantic endpointing + speculative execution. With a safety-first floor for elderly clinic callers, ~1.4–1.7s is the honest target. 600ms endpointing on capture turns is a *correct* choice, not a bug.
- The single largest, cleanest, already-half-done win is **WS-A** — it's the ~300–500ms you thought was already live.

---

## 7. Open decisions (need your call before/around each WS)

- WS-C: are you willing to run real (redacted) call audio through AssemblyAI's calibration tool, or eval on synthetic test calls only? (Calibration is materially better but touches real audio → redaction overhead + EU handling.)
- WS-B: acceptable to gate behind `TTS_TRANSPORT=ws|http` env so it's a one-variable rollback if promoted later?
- Promotion policy: confirm that *any* live promotion of a winning lever is a separate PR under normal review — this branch never merges to `main` as-is.
