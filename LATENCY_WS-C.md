# WS-C — Endpointing: Measurement Spec + Phase-Aware Plan

**Date:** 2026-07-13. **Branch:** `latency-eval` (isolated). **Live untouched.**
**Status:** Phase 1 (measurement) shipped. **Phase 2 (phase-aware endpointing) now
SHIPPED behind `WS_C_SEMANTIC_ENDPOINT` (default OFF)** — see §3, revised below. It is
built but **not yet measured on calls**: the Phase-1 endpoint baseline and the Phase-2
A/B (with the capture-cutoff hard gate) are still required before any promotion.

> **API revision (verified 2026-07-19, `universal-streaming-english`):** the plan below
> originally keyed capture-vs-conversation on `end_of_turn_confidence_threshold`. That
> param is now **DEPRECATED on Universal Streaming** — AssemblyAI directs you to
> `min_turn_silence` / `max_turn_silence` instead. The shipped lever is therefore
> **silence-based** (raise both in capture phases), not confidence-based. The other
> open question — *does v3 support mid-session config update?* — is **resolved YES**:
> the `UpdateConfiguration` message applies new `min/max_turn_silence` without a
> reconnect, which is what makes per-phase profiles (old "Approach B") feasible and how
> the shipped lever works.
Companion to `LATENCY.md` (strategy/status/baseline/WS-A verdict) and `LATENCY_HARNESS.md`
(the `[LAT]`/`[LAT-EP]` measurement system this builds on).

---

## 0. Why WS-C is the remaining lever (from the WS-A result)

Anatomy of one turn, measured (WS-A A/B data):

```
[caller stops speaking]
   ↓  ~600ms   endpoint silence floor (min_turn_silence=600)   ← NOT in TTFA, pure dead-time
[dispatch t0]
   ↓  ~1225ms  llm_ttft   (Sonnet TTFT, already prompt-cached — near floor)   60% of TTFA
   ↓  ~724ms   chunk_gate (first-sentence generation floor — WS-A null)        35% of TTFA
   ↓  ~121ms   tts_first_byte (WS-B ceiling — not worth it)                     6% of TTFA
[first audio]
```

- **llm_ttft** and **chunk_gate** are structurally near their floors — not cheap to cut without quality/architecture risk.
- **tts_first_byte** is already 121ms — WS-B has almost no ceiling.
- The **600ms endpoint silence** is dead-time on *every* turn, upstream of everything, and it is the **only** remaining slice that is both large and attackable.

**The real reason to do WS-C:** the 600ms timer is the *shared root* of BOTH problems seen on the calls:
- On **short, confident answers** ("Thursday the 23rd", "yes", "use this number") 600ms is wasted latency → should fire faster.
- On **capture phases** (name/phone/hesitant multi-word) 600ms is *too short* and clips the front of the answer ("please" / "session please"; "Quentin"+"Rook" split) → should wait longer.

So WS-C, done as **phase-aware endpointing**, improves **latency AND comprehension at once** — which is exactly where the data points. Blanket "fire faster" would worsen the clipping we already measured; that is explicitly not the plan.

---

## 1. The defect

`config.py` `ASSEMBLYAI_WS_URL` sets only `min_turn_silence=600`. The v3 **semantic endpointer is dormant**: `end_of_turn_confidence_threshold` and `max_turn_silence` are absent → at defaults → v3 runs as a plain 600ms silence timer. It never fires early on a confident complete thought, and never waits longer on an obviously-incomplete one. It treats "yes" and a half-spoken surname identically.

History (config.py comments): min_turn_silence was 200 → 800 → 600, hand-tuned against the split-vs-sluggish tradeoff. That tuning is exactly what a semantic/phase-aware endpointer replaces with signal instead of a single global guess.

---

## 2. PHASE 1 — Measurement (build this first; it is the whole first deliverable)

The existing harness starts the clock at **t0 = end_of_turn** (post-endpoint), so it is **blind to the 600ms**. We cannot judge WS-C without seeing it. Add endpoint-latency instrumentation, flag-gated, zero hot-path cost when off.

### 2.1 New timestamps (in `stt_stream.py` `_receive_results_loop`)

The v3 `Turn` handler (~636) already distinguishes partial (`end_of_turn=false`) from final (`end_of_turn=true`). Stamp:

- `t_last_partial` — monotonic time of the **last** `end_of_turn=false` Turn whose transcript is non-empty (updated on each partial).
- `t_end_of_turn` — monotonic time the `end_of_turn=true` Turn is received (co-located with the existing `_put_transcript` enqueue stamp).

Derived, per turn:
```
endpoint_wait_ms = t_end_of_turn − t_last_partial
```
This is the silence the endpointer imposed after the caller's last word — ~600ms+ when timer-bound, less when a (future) semantic fire beats the timer. First-write-wins per turn; reset on each new turn's first partial. None when the flag is off.

Pass the pair alongside the transcript (extend `_put_transcript`'s tuple, or stash on the STT instance keyed by turn) so `connection.py` can attach `capture_phase` at dispatch (it already computes phase via `latency_timing.capture_phase(session)`).

### 2.2 Cutoff detector (the hard-gate metric)

A "cutoff" = the endpointer ended the turn while the caller was still mid-thought. Emit a per-turn heuristic flag `ep_cutoff` from any of:

1. **Fragment-continuation:** another partial/final for the same logical turn arrives within `EP_CUTOFF_WINDOW_MS` (candidate 1500ms) after a final — i.e. the caller kept talking. (Strongest signal; matches the "please" → "session please" pattern.)
2. **Correction-lead next turn:** the *next* caller final starts with a correction phrase ("i said", "i told you", "no ", "that's not"). (Matches "I told you, anytime next week".)
3. **Watchdog re-ask fired** between this final and the next caller transcript (`WATCHDOG_FIRE` / safety-net re-ask). Already logged; correlate by q_gen.

`ep_cutoff` is advisory (heuristic, may over/under-count) — its job is a **relative** rate baseline-vs-WS-C, not ground truth. Confirm with listen-back on flagged turns.

### 2.3 Emit

Add fields to the existing `[LAT]` line (preferred — one line per turn, one parser) or a sibling `[LAT-EP]` line on the same `susie.latency` logger:
```
endpoint_wait_ms=<int>  ep_cutoff=<0|1>  ep_fired=<timer|semantic|max>  capture_phase=<...>
```
`ep_fired` = why the turn ended (timer floor / semantic confidence / max_turn_silence) — trivial today (always `timer`), meaningful once the semantic net is on. Gate behind the existing `LATENCY_TIMING` (or a new `LATENCY_ENDPOINT`) env; `None`/no-op when off, so the hot path is one falsy check. Update `lat_parse.py` to summarise `endpoint_wait_ms` p50/p90 per capture_phase and the `ep_cutoff` rate.

### 2.4 Phase-1 success criteria (what we're actually buying)

Run the same eval call set (baseline endpointing, semantic net OFF) and answer:
- **How much dead-time is actually recoverable?** `endpoint_wait_ms` p50 per phase. If conversation/confirm turns sit at ~600ms and capture turns also ~600ms, the win ceiling is ~600ms on confident turns.
- **What is the baseline cutoff rate, per phase?** This is the number WS-C must **not** worsen. Expect capture (name/phone) > conversation.
- Only then decide WS-C is worth building — same discipline that killed WS-A honestly.

---

## 3. PHASE 2 — Phase-aware endpointing (only after Phase 1)

### 3.1 Turn on the dormant semantic net (config, not a rebuild)

Add `end_of_turn_confidence_threshold` and `max_turn_silence` to the v3 URL. Two-stage behaviour: after `min_turn_silence`, if end-of-turn confidence > threshold → fire (fast); else wait until `max_turn_silence`. This alone gives **implicit phase-awareness**, because confidence already encodes "is this a complete thought":
- "Thursday the 23rd." → high confidence → fires early.
- A half-said surname / trailing "and my number is…" → low confidence → waits to max.

**Approach A (simplest — try first):** a single well-tuned semantic profile. Let confidence do the phase separation. Lowest complexity; may be enough.

**Approach B (explicit phase profiles — if A doesn't cleanly separate):** switch endpoint params by `capture_phase`:

| Phase | min_turn_silence | confidence threshold | max_turn_silence | intent |
|---|---|---|---|---|
| conversation / confirm | lower (e.g. 400) | aggressive (fire readily) | modest | reclaim dead-time on crisp answers |
| name / phone capture | higher (e.g. 700–800) | conservative (rarely fire early) | high (e.g. 1500) | never clip a spelled name / read-out number |

**OPEN QUESTION (gates B): RESOLVED — yes.** v3 streaming supports mid-session config
update via `{"type":"UpdateConfiguration","min_turn_silence":<ms>,"max_turn_silence":<ms>}`
(applied without a reconnect). The shipped lever uses this to switch per-phase profiles
in-stream, so we build the phase-profile approach directly rather than the single-profile
fallback. Confidence-threshold separation is moot — that param is deprecated on our model.

### 3.2 Flag gating

`WS_C_PHASE_ENDPOINT` (env, default OFF) — OFF = today's `min_turn_silence=600`, byte-identical. ON = semantic net + (A or B). Individual params env-sweepable (`WS_C_CONF_THRESHOLD`, `WS_C_MAX_TURN_SILENCE`, per-phase min-silence) so the knee is found without redeploys, same as WS-A.

### 3.3 HARD GATE (non-negotiable)

**Zero increase in mid-capture cutoffs vs the Phase-1 baseline, in name/phone phases.** Judge on TWO metrics, not one:
- `endpoint_wait_ms` p50 down (latency recovered), AND
- `ep_cutoff` rate flat-or-down in capture phases (comprehension preserved).

If capture cutoffs rise, that arm reverts — it's config. An elderly caller reading a phone number must never be cut off to save 300ms. Latency is secondary to not-clipping in capture.

---

## 4. Risks / knowns

- **Model deprecation (CONFIRMED):** `end_of_turn_confidence_threshold` is **deprecated
  on `universal-streaming-english`** (and absent on Universal-3 Pro) — AssemblyAI directs
  you to `min_turn_silence` / `max_turn_silence`. The shipped lever is silence-based, so
  this no longer blocks anything. Model stays pinned to `universal-streaming-english`.
- **We already have a clipping problem at 600ms** (WS-A findings, bucket 2). Any aggressive-fire arm risks making it worse — hence the hard gate and the conservative capture profile. This is the main reason to measure cutoffs first.
- **The 600ms floor is a deliberate elderly-friendly choice.** WS-C makes it *conditional* (tight only when confident), it does not blanket-lower it.
- **Heuristic cutoff detector** will mis-count; use it for relative comparison + listen-back, not as an absolute.

---

## 5. Test procedure (eval number, semantic net measured then toggled)

1. Phase 1: `LATENCY_TIMING/ENDPOINT=on`, `WS_C_PHASE_ENDPOINT=off`. Record `endpoint_wait_ms` + `ep_cutoff` per phase over ≥30 turns.
2. Phase 2: flip `WS_C_PHASE_ENDPOINT=on` (Approach A first). Same call set. Compare dead-time recovered vs cutoff rate.
3. **Call set must stress both directions:** (a) crisp short answers ("yes", "Thursday the 23rd", "use this number") to prove faster fire; (b) hesitant/spelled name + read-out phone number to prove no new cutoffs; (c) a run-on rambler with mid-sentence pauses (the original 800→600 regression case) to prove it isn't split.
4. Sweep threshold / max_turn_silence. Record the winner. **Do not promote** — live is a separate PR under normal review.

---

## 6. Files in scope

| File | Phase | Change |
|---|---|---|
| `app/media_streams/stt_stream.py` | 1 | stamp `t_last_partial` / `t_end_of_turn`; carry through with the final. |
| `app/media_streams/latency_timing.py` | 1 | `endpoint_wait_ms` + `ep_cutoff` + `ep_fired` fields; flag-gated. |
| `app/media_streams/connection.py` | 1 | attach `capture_phase`; cutoff-detector correlation (fragment window / correction-lead / watchdog). |
| `lat_parse.py` | 1 | summarise endpoint_wait p50/p90 per phase + cutoff rate. |
| `app/media_streams/config.py` | 2 | `WS_C_*` env; add v3 confidence/max_turn_silence params. |
| `stt_stream.py` (config send) | 2 | apply profile; mid-stream update IF v3 supports it (else Approach A). |

## 7. Out of scope

- Any change to the LLM, chunker (WS-A), or TTS transport (WS-B).
- Blanket lowering of `min_turn_silence` (that's the old hand-tuning WS-C replaces).
- Promotion to `main`/live.
- Universal-3 Pro migration.
