# Susie Latency Eval — Measurement Harness

**What this is:** the `[LAT]`/`[LAT-EP]` per-turn timing system — the schema, the six
timestamps, how it's wired, how to read it with `lat_parse.py`, and how the isolated eval
service is set up. Read `LATENCY.md` first (strategy/status); this is the "how the numbers
are made". *(Consolidates the former MEASUREMENT_SPEC, INSTRUMENTATION_WIRING, HARNESS_SETUP.)*

**Status: BUILT and shipped.** `app/media_streams/latency_timing.py` is live behind
`LATENCY_TIMING` (default OFF; the eval sets it ON). WS-C Phase-1 endpoint fields shipped at
`e7f64ff`.

---

## 1. Design constraints (kept)

- **Clock:** `time.monotonic()` only (immune to NTP jumps). Deltas reported in **ms**.
- **Zero PII:** timings + enum tags only — never transcript, name, phone digits, or audio.
- **Zero live-path risk:** everything behind `LATENCY_TIMING` (default OFF). When OFF,
  `new_turn()` returns `None` and every stamp site short-circuits on one falsy check — no
  allocation, no logging, no attribute walk. The branch stays byte-behaviour-identical to
  live until the flag (or a lever flag) is set.
- **No new deps:** stdlib `time` + the existing logger. Aggregation is offline.

---

## 2. The pipeline & the six capture points

One caller turn flows through four concurrent coroutines, communicating by queue:

```
STT recv loop ──(transcript_queue)──▶ _llm_loop ──▶ run_turn (llm_stream)
     │ end_of_turn=true                    │              │ tokens ▶ chunker ▶ (tts_text_queue)
     ▼                                     ▼              ▼
    [t0]                            [t_dispatch]   [t1]  [t2]
                        _tts_loop ──(synthesise)──▶ (audio_out_queue) ──▶ _send_loop ──▶ Twilio
                                        [t3]                                   [t4]
```

| ID | Event | Where (grep the symbol; lines drift) |
|----|-------|------|
| **t0** | Caller turn finalized (endpoint fired) | `stt_stream.py` Turn handler, `_last_final_at = time.monotonic()`; carried on the transcript tuple |
| **t_dispatch** | Transcript dequeued, turn begins | `connection.py::_llm_loop`, right after `transcript_queue.get()` |
| **t1** | First LLM token | `llm_stream.py::_one_streaming_call`, the `got_first_chunk` moment |
| **t2** | First content chunk → TTS | first non-None `chunker.add_token(...)` → `tts_text_queue.put` |
| **t3** | First audio frame enqueued | `connection.py::_tts_loop` / `_put_audio` (filler counts) |
| **t4** | First audio frame sent to Twilio | `connection.py::_send_loop`, first `media` send — **also the emit site** |

**t4 is the number that matters** (first sound the caller hears). t1–t3 attribute *which
lever* moved it. Plus **content_t3/content_t4** = first *non-filler* audio, so a filler
("just a second…") doesn't make the splits meaningless (see §5 filler).

---

## 3. Derived metrics (all ms)

| Metric | Formula | Isolates |
|---|---|---|
| **perceived TTFA** | `t4 − t0` | headline — first sound (filler or content) |
| **content TTFA** | `content_t4 − t0` | real content arrival (what levers reduce) |
| ep_dispatch | `t_dispatch − t0` | queue/scheduling (≈0) |
| llm_ttft | `t1 − t_dispatch` | Claude first-token |
| **chunk_gate** | `t2 − t1` | **WS-A lever** (chunk accumulation) |
| tts_first_byte | `content_t3 − t2` | **WS-B lever** (ElevenLabs start) |
| audio_wire | `content_t4 − content_t3` | encode/queue/send (≈0) |
| **endpoint_wait** | `t_end_of_turn − t_last_partial` | **WS-C lever** — pre-t0 silence the endpointer imposed |

`content_ttfa = ep_dispatch + llm_ttft + chunk_gate + tts_first_byte + audio_wire` — the
sub-splits must sum. That's the built-in correctness check.

---

## 4. Log schema (as built)

One PII-free line per turn on the dedicated `susie.latency` logger (route to its own sink;
grep `[LAT`):

```
[LAT] turn_seq=<int> path=<enum> outcome=<enum> ttfa_ms=<int> content_ttfa_ms=<int>
      ep_dispatch_ms=<int> llm_ttft_ms=<int> chunk_gate_ms=<int> tts_first_byte_ms=<int>
      audio_wire_ms=<int> flags=<A|C csv | -> model=<claude id> stt_model=<...>
      eot_confident=<bool|None> capture_phase=<conversation|phone|name> endpoint_wait_ms=<int>
```

Plus an advisory **cutoff** line when a turn opens with a correction lead ("I said…",
"I told you…") — meaning the *prior* turn's capture was likely clipped:

```
[LAT-EP] ep_cutoff turn_seq=<prior> reason=correction capture_phase=<...>
```

- Any stage not reached → field = **`-1`** (never 0), so "missing" can't contaminate a sum.
- **`flags`** records which lever(s) were ON → one log file can hold an A/B (`flags=-`
  baseline, `flags=C` WS-C on). Driven by env in `latency_timing.py` `_LEVER_ENV`
  (`A→WS_A_FAST_FIRST_CHUNK`, `C→WS_C_SEMANTIC_ENDPOINT`).

---

## 5. Path & outcome tagging + edge cases

**`path`** buckets turns so a filler/tool turn doesn't pollute the LLM distribution:
- **`llm`** — normal; the only bucket that measures chunk_gate + llm_ttft.
- **`fast_path`** — canned response, no tokens (t1==t2). Excluded from lever stats.
- **`filler`** — an ACK/think filler is the true first audio → it owns t3/t4; `content_t3/4`
  hold the real content so TTFA can be reported with *and* without filler masking.
- **`fallback`** — safe-fallback/error phrase; excluded.

**`outcome`** + edge handling:
| Case | Handling |
|---|---|
| Normal | `completed`, emit at first send frame |
| Barge-in before t4 | `barged_in`, emit with partial stamps (rest `-1`); excluded from TTFA, counted separately |
| Split utterance (double `end_of_turn`) | next dispatch supersedes → prior record `abandoned`, emit, replace. **Also counts split events** — directly relevant to WS-C's capture-turn gate |
| Tool round-trips | t1 = first token of any kind, t2 = first speakable chunk after tools; the gap shows honestly in chunk_gate |
| TTS/LLM error, dead air | `error`, stamps `-1` |

`emit()` is idempotent (`_emitted` guard) — a record closed by barge-in then reached by a
late frame won't double-log.

---

## 6. WS-C sub-instrumentation (Phase 1 — shipped)

For the endpointing lever, three fields make the capture-turn safety gate measurable:
- **`endpoint_wait_ms`** = `t_end_of_turn − t_last_partial` — the silence the endpointer
  imposed after the caller's last word (~600ms+ when timer-bound; less when a future
  semantic fire beats it). Stamped in `stt_stream.py` (last-partial on each non-empty
  partial; the delta on the final), carried to `connection.py` at dispatch.
- **`ep_cutoff`** (the `[LAT-EP]` line) — advisory: a correction-lead opener implies the
  prior turn was clipped. Heuristic → **confirm flagged turns by listen-back**; use it for a
  relative rate, not truth.
- **`capture_phase`** ∈ `conversation|phone|name` — from the flow state (enum only).

> **The hard gate this enables:** any `abandoned`/clipped turn where `capture_phase ∈
> {phone,name}` is a **hard fail**, independent of the conversational-turn TTFA win. An
> elderly caller reading a number must never be chopped to save 300ms.

(`eot_confident` — confidence-driven vs `max_turn_silence` fallback — is a Phase-2 field,
populated when the semantic net is on; defaults `None` today.)

---

## 7. How it's wired (cost ≈ nil when OFF)

`app/media_streams/latency_timing.py` owns `TurnTiming` (dataclass, first-write-wins
`stamp()`), `new_turn()`, `capture_phase()`, `emit_cutoff()`, `is_correction_lead()`.
`connection.py` owns `self._turn_timing`, created at t_dispatch, stashed on the per-call
`LLMStream._timing` so `llm_stream.py` can stamp t1/t2 without threading a param through
every call site; `_tts_loop`/`_send_loop` stamp t3/t4 and emit. When OFF: `new_turn()`
returns `None` → every site is `if self._turn_timing:` / `if _LAT_ON:` — one falsy check,
no work. That's what keeps the branch byte-identical to live.

EOL footgun when editing these: `latency_timing.py`/`stt_stream.py` are **LF** blobs
(default `git add`); `connection.py` is a **CRLF** blob (stage `-c core.autocrlf=false`).
Verify a minimal diff. (Details in `SUSIE_HANDOFF_JULES.md` §5.)

---

## 8. Reading the numbers — `lat_parse.py`

Pure-stdlib, PII-free, numpy-type7 percentiles. Reads raw Render logs, a pre-grepped file,
or stdin:

```
grep -E "\[LAT" render.log | python lat_parse.py        # or: python lat_parse.py call*.log
```
Prints: perceived + content TTFA and the splits (p50/p90/p95), a per-`capture_phase`
breakdown, the chunk_gate histogram, and — once endpoint data is present — a **WS-C
ENDPOINT** block (endpoint_wait p50/p90 + cutoff rate per phase). `--json <file>` also dumps
raw stats. It prints `THIN (<30)` until ≥30 completed turns. Reproduce the locked baseline
with `python lat_parse.py lat_baseline_29turns.txt`.

**Aggregate ≥30 `path=llm` turns per config**; report TTFA (filler in/excluded), chunk_gate,
tts_first_byte, and the rates (barge-in %, abandoned %, error %) — the safety metrics,
especially for WS-C.

---

## 9. The eval service — infra & isolation invariants

The eval is **a new Twilio number → a new Render service (branch `latency-eval`) → clinic
`jv_v1`**, with its own Redis, touching nothing live. It's already provisioned; this section
is the isolation contract to keep true (and the recipe to reproduce one for another clinic).

**Isolation invariants — verify before any real call:**
1. **Separate Redis** — dedicated URL; never the live `REDIS_URL` (session collisions).
2. **No live calendar writes** — throwaway calendar, or don't complete bookings on timing runs.
3. **No owner SMS to Marcus** — owner-alert recipient redirected; `SMS_ENABLED` off on the eval.
4. **Sheets off** — `SHEETS_ENABLED` off (code-gated).
5. **Test callers only**; redact any transcript before it becomes a committed fixture (UK
   GDPR — health data). `[LAT]` logs are timings+enums, no PII by design.
6. **Frankfurt/EU region.** autoDeploy **OFF** — deploy manually so a push never surprises a
   running eval.

**Standing up one (recipe):** new UK Twilio number → map it to the clinic via
`TWILIO_TO_CLINIC` in `app/clinic_config.py` **on `latency-eval` only** (unmapped numbers
fall through to `demo`) → new Render Web Service (branch `latency-eval`, Frankfurt,
autoDeploy OFF, start `uvicorn app.main:app --host 0.0.0.0 --port $PORT --timeout-keep-alive
75`) → separate Redis → env: shared API keys can copy live, but `REDIS_URL` / calendar /
owner-alert recipient / `RENDER_EXTERNAL_URL` **must differ**; set `MEDIA_STREAMS_ENABLED=true`,
`MEDIA_STREAMS_CLINIC_ID=<clinic>`, `LATENCY_TIMING=true`, lever flags OFF → point the number's
Voice webhook at `/ms/incoming` → smoke-test (logs show the right `clinic_id`, correct greeting,
no live side-effects) → capture the baseline.

**Baseline capture:** `LATENCY_TIMING=true`, ≥30 turns across a few calls (mix of question,
booking, name/phone capture), grep `[LAT]` → `lat_parse.py` → record p50/p90/p95. **This is
the number every lever is judged against — don't touch a lever until it's recorded.**

**Teardown:** nothing here can affect live (separate number/service/Redis/calendar). To
pause: suspend the Render service. To tear down: delete the eval service + Redis, release the
number, delete `origin/latency-eval`.
