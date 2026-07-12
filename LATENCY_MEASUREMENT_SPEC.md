# Latency Measurement Instrumentation Spec

**Companion to** `LATENCY_SIDE_BRANCH_EVAL_PLAN.md` (§2 Measurement).
**Status:** spec only — no code. Side-branch (`latency-eval`) only. Never on live.
**Date:** 2026-07-13

The eval is worthless without trustworthy per-turn timing. This spec defines the
timestamp points, how they're correlated across the concurrent loops, the log
schema, and how the failure modes are tagged. Implement this **first**, before
touching any lever, and validate the baseline with it.

---

## 1. Design constraints

- **Clock:** `time.monotonic()` only (already used across `connection.py`, e.g. `_last_final_at`). Immune to NTP/wall-clock jumps. All stamps in seconds; all reported deltas in **milliseconds**.
- **Zero PII:** log timings and enum tags only. **Never** the transcript text, name, phone digits, or audio. (UK GDPR — health data.)
- **Zero live-path risk:** everything gated behind one env flag `LATENCY_TIMING` (default OFF). When OFF, not a single extra statement executes in the hot path beyond one boolean check per capture point. Side branch sets it ON.
- **No new deps:** stdlib `time` + the existing logger. Aggregation is offline (parse logs).

---

## 2. The pipeline & the six capture points

One caller turn flows through four concurrent coroutines communicating by queue:

```
STT recv loop ──(transcript_queue)──▶ _llm_loop ──▶ run_turn (llm_stream)
     │                                                     │
     │ end_of_turn=true                                    │ tokens ▶ chunker ▶ (tts_text_queue)
     ▼                                                     ▼
    [t0]                                          [t1]  [t2]
                                                          │
                        _tts_loop ──(synthesise)──▶ (audio_out_queue) ──▶ _send_loop ──▶ Twilio
                                          [t3]                                  [t4]
```

| ID | Event | Anchor (function / statement) | File |
|----|-------|-------------------------------|------|
| **t0** | Caller turn finalized (endpoint fired) | The `time.monotonic()` stamped onto the transcript tuple `(ts, text)` when `end_of_turn=true`; canonical source is `_last_final_at = time.monotonic()` | `stt_stream.py` (Turn handler, ~:653) |
| **t_dispatch** | Transcript dequeued, turn begins | `(ts, text) = await transcript_queue.get()` at top of turn handling | `connection.py::_llm_loop` (~:5367) |
| **t1** | First LLM token received | First `token` yielded inside `async for event in stream` (the iteration that feeds `chunker.add_token`) | `llm_stream.py::_stream_claude` (~:1261/1310) |
| **t2** | First content chunk emitted to TTS | First **non-None** return of `chunker.add_token(...)` → `tts_text_queue.put(chunk)` | `llm_stream.py` (~:1310–1327) |
| **t3** | First audio frame enqueued | First `audio_out_queue.put(b64)` for this turn (from `synthesise_chunk` streaming, or `_put_audio` for filler) | `connection.py::_tts_loop` / `_put_audio` (~:10910) |
| **t4** | First audio frame sent to Twilio | First `media` payload `ws.send(...)` for this turn | `connection.py::_send_loop` (~:10992) |

**t4 is the number that matters** (first sound the caller hears). t1–t3 exist to attribute *which lever* moved it.

---

## 3. Derived metrics (all ms)

| Metric | Formula | Isolates |
|---|---|---|
| **TTFA** — time to first audio | `t4 − t0` | **The headline.** Voice-to-voice minus the caller's own trailing silence. |
| endpoint→dispatch | `t_dispatch − t0` | queue/scheduling overhead (should be ~0) |
| LLM TTFT | `t1 − t_dispatch` | Claude first-token latency |
| **chunk-gate cost** | `t2 − t1` | **WS-A lever.** Time spent accumulating to the word threshold. |
| TTS first-byte | `t3 − t2` | **WS-B lever.** ElevenLabs synth start latency. |
| audio→wire | `t4 − t3` | encode/queue/send overhead (should be ~0) |

Note `t4 − t0 = (t_dispatch−t0) + (t1−t_dispatch) + (t2−t1) + (t3−t2) + (t4−t3)` — the sub-splits must sum to TTFA. This is a built-in correctness check on the instrumentation itself.

---

## 4. Correlation across coroutines

There is no turn counter today, and the four loops run concurrently — so we need a single shared record that each loop stamps on **first occurrence**.

**Record** (one per turn), held as `self._turn_timing`:

```
turn_seq:      int          # monotonically incrementing, assigned at t_dispatch
t0:            float        # carried in from the transcript tuple
t_dispatch:    float
t1, t2, t3, t4: float|None  # first-write-wins, None until stamped
path:          enum         # llm | fast_path | filler | fallback  (see §5)
outcome:       enum         # completed | barged_in | abandoned | error
```

**Rules:**
- **Assign** at `t_dispatch`: `turn_seq += 1`; create a fresh record; copy `t0` from the transcript tuple.
- **Stamp** each of t1–t4 with a first-write-wins guard: `if rec.t1 is None: rec.t1 = time.monotonic()`. The guard is what makes concurrency safe — asyncio is single-threaded, so no lock is needed; a plain `is None` check is atomic enough.
- **Emit & close** the record when t4 is stamped (see §6), then leave it in place (stale) until the next `t_dispatch` replaces it.
- **Turns are strictly sequential** (one response at a time; barge-in cancels the current turn before a new one starts), so a single "current turn" record is sufficient — no map of concurrent turns needed. `turn_seq` is carried into the log purely for offline correlation/debugging.

---

## 5. Path tagging (why a turn may skip stages)

Not every turn passes through every point. Tag `path` so the aggregation can bucket correctly instead of polluting the LLM distribution:

- **`llm`** — normal: t1, t2 both present. The only bucket that measures the chunk-gate (WS-A) and LLM TTFT.
- **`fast_path`** — `run_turn` short-circuits to a canned response (`llm_stream.py:310`, `fp_result.response_text` → `tts_text_queue`). **No LLM tokens** → t1 == t2 (both stamped at the put). Exclude from LLM TTFT / chunk-gate stats.
- **`filler`** — an ACK/think filler played first (`ACK_FILLER_MARKER`, `llm_stream.py:1211`; or `_put_audio` filler clip). The filler is the true first audio the caller hears, so it legitimately owns t3/t4 — but tag it so we can report TTFA **with** and **without** filler masking (a filler hides LLM latency from the caller but doesn't remove it).
- **`fallback`** — `SAFE_FALLBACK_PHRASE` / error phrases put directly to the queue (`llm_stream.py:408/529/…`). Exclude from lever stats.

---

## 6. Log schema

One structured line per turn, at `t4` (or at close for non-completed turns). Machine-parseable, PII-free:

```
[LAT] turn_seq=<int> path=<enum> outcome=<enum> \
      ttfa_ms=<int> ep_dispatch_ms=<int> llm_ttft_ms=<int> \
      chunk_gate_ms=<int> tts_first_byte_ms=<int> audio_wire_ms=<int> \
      flags=<A|B|C csv of active levers> model=<claude model> \
      stt_model=universal-streaming-english
```

- Any stage not reached → that field = `-1` (not 0), so "missing" never contaminates a sum.
- `flags` records which lever(s) were ON for this turn → lets one log file hold an A/B run.
- Emit at `logging.INFO` under a dedicated logger `susie.latency` so it can be routed to its own file/sink and grepped with `[LAT]`.

---

## 7. Edge cases → how each is handled

| Case | Handling |
|---|---|
| **Barge-in before t4** | Caller interrupted before hearing audio. Close record with `outcome=barged_in`, emit with whatever stamps exist (rest `-1`). Exclude from TTFA stats; count separately (barge-in rate is its own health metric). |
| **Double `end_of_turn`** (split utterance) | Second final arrives while a turn is mid-flight → new `t_dispatch` supersedes. Tag the abandoned first record `outcome=abandoned`, emit, replace. (This also *counts* split-utterance events — directly relevant to WS-C's capture-turn safety gate.) |
| **Filler then real chunk** | t3/t4 owned by the filler frame (correct — caller hears it). Additionally record `content_t3/t4` for the first *non-filler* audio so both "perceived" and "true content" TTFA are available. |
| **Tool round-trips** (LLM calls a tool, no user-facing text yet) | Tool-only iterations produce no chunk; t1 = first token of any kind, t2 = first *speakable* chunk after tools resolve. The gap shows up honestly in `chunk_gate_ms` — do not zero it. |
| **TTS/EL error, dead air** | No t3/t4. Close at turn end with `outcome=error`, stamps `-1`. |
| **Fast-path** | t1==t2; `llm_ttft_ms` reported but tagged `path=fast_path` for exclusion. |

---

## 8. Aggregation (offline, no code committed)

Parse `[LAT]` lines → per-metric **p50 / p90 / p95** (and count) bucketed by `path` and `flags`, over **≥30 `path=llm` turns** per config. Report:

1. **TTFA p50/p90/p95** — the headline, `path=llm`, filler excluded and included.
2. **chunk_gate_ms** distribution — the WS-A proof.
3. **tts_first_byte_ms** distribution — the WS-B proof.
4. **Rates:** barge-in %, abandoned % (split-utterance), error % — the safety metrics, especially for WS-C.

Sanity gate before trusting any run: sub-splits must sum to TTFA within a few ms; `ep_dispatch_ms` and `audio_wire_ms` should be near-zero. If not, the instrumentation is wrong, not the pipeline.

---

## 9. Turn-detection sub-instrumentation (WS-C only)

For the endpointing lever, add two fields from the AssemblyAI Turn message (`stt_stream.py:636`) when present:

- `eot_confident=<bool>` — whether the fire was confidence-driven vs `max_turn_silence` fallback (derivable from the silence elapsed at fire vs the configured floor).
- `capture_phase=<conversation|phone|name>` — which flow phase the turn ended in.

This is what lets the capture-turn safety gate be measured objectively: **any `abandoned`/split or clipped turn where `capture_phase ∈ {phone,name}` is a hard fail**, independent of the conversational-turn TTFA win.

---

## 10. What to build, in order

1. `LATENCY_TIMING` env flag + `susie.latency` logger (off by default).
2. The `self._turn_timing` record + `turn_seq` at `t_dispatch`.
3. Six first-write-wins stamps at the §2 anchors, each guarded by the flag.
4. Emit-and-close at t4 / on close.
5. Path + outcome tagging (§5, §7).
6. Offline parser → p50/p90/p95 table.
7. **Record baseline (all levers OFF) — this is the number every lever is measured against.**

Steps 1–6 are the instrumentation; nothing about them changes call behaviour when the flag is OFF, so the side branch stays byte-behaviour-identical to live until a lever flag is flipped.
