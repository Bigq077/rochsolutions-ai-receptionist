# WS-A — Chunk-Gate Implementation Spec

**Companion to** `LATENCY_SIDE_BRANCH_EVAL_PLAN.md` (§4 WS-A) and `LATENCY_MEASUREMENT_SPEC.md`.
**Branch/worktree:** `latency-eval` (isolated). **Live system untouched.**
**Status:** spec only — no code yet.
**Date:** 2026-07-13

The largest, simplest lever: the first-audio delay caused by the chunker waiting
for a fixed word count before it will emit the opening chunk. The intended fix
already exists as a *dead constant* — this spec turns it into a real, flag-gated,
measurable change.

---

## 1. The exact defect

Two constants, out of sync, one of them unused:

| Constant | Value | Reality |
|---|---|---|
| `MIN_CHUNK_WORDS` | `8` | Defined in `config.py:168` with a comment claiming a ~300–500ms saving. **Imported by nobody.** Dead. |
| `MIN_WORDS` | `15` | Hardcoded in `chunker.py:41`, read in the emit condition at `chunker.py:161`. **This is what actually runs.** |

So every turn's first chunk is gated at **15 words + a sentence boundary**. On a
~22-word reply the opener can wait for the *second* sentence before any audio
plays. The `8` was configured and forgotten.

**Emit logic today** (`ResponseChunker.add_token`, `chunker.py:~131–170`):
- buffer tokens; count words
- emit when `word_count >= MIN_WORDS` AND buffer ends with a hard split `.!?`
  AND the word before the punctuation is not a `NEVER_SPLIT_AFTER` abbreviation
- force-emit at `MAX_WORDS` (50)
- `flush()` returns the tail on stream end (emits even below the threshold)

---

## 2. Goal

Let the **first** chunk of each turn emit as soon as the first *complete sentence*
finishes, even if short — then keep the existing behaviour for the rest of the
turn (so mid-response prosody and forward-progress are unchanged).

Rationale for first-chunk-only: the latency the caller feels is **time to first
audio**. Only the opening chunk sits on the critical path; chunks 2..n are
synthesised while earlier audio is already playing, so lowering *their* threshold
buys nothing and only risks choppier delivery.

---

## 3. Design

### 3.1 Parameterise the threshold (retire the dead constant)

- `ResponseChunker.__init__` takes `min_words: int` and `min_words_first: int`, defaulting to the module constants so existing callers are unchanged.
- Introduce `MIN_WORDS_FIRST` (module constant) and wire `config.py` through to it so it is actually imported — killing the dead-constant class of bug. The live `MIN_WORDS = 15` stays the default for non-first chunks.
- `add_token` uses `min_words_first` while `self._chunks_emitted == 0`, then `min_words` thereafter.

### 3.2 First-chunk emit rule

While `_chunks_emitted == 0`:
- emit at the **first hard split `.!?`** once `word_count >= MIN_WORDS_FIRST`
  (candidate default **6**, tunable via flag), keeping the existing
  `NEVER_SPLIT_AFTER` abbreviation guard and the `FORBIDDEN_CHUNK_STARTERS`
  right-fragment guard **unchanged**.
- `MAX_WORDS` force-emit unchanged.

After the first emit, revert to the current `MIN_WORDS=15` path verbatim.

### 3.3 Per-turn reset

`_chunks_emitted` must reset to 0 at the **start of each caller turn**, or only
the very first reply of the call gets the fast opener. A fresh `ResponseChunker()`
is already constructed inside the streaming call (`llm_stream.py:1163`), so the
counter is naturally per-turn — **confirm** this holds for every path that streams
(normal, post-tool continuation) and that no chunker instance is reused across
turns. If any path reuses one, add an explicit `reset()` at turn start.

### 3.4 Flag gating (side-branch discipline)

- `WS_A_FAST_FIRST_CHUNK` (env, default OFF) selects `MIN_WORDS_FIRST = <n>` when ON, and **`= MIN_WORDS` (15) when OFF** — i.e. OFF is byte-behaviour-identical to live today. This is what lets the side branch A/B the two configs and lets any future promotion be a one-variable rollback.
- `MIN_WORDS_FIRST` value itself also read from env (e.g. `WS_A_MIN_WORDS_FIRST`, default 6) so the threshold can be swept (4/5/6/8) without redeploying code.

---

## 4. Interaction with existing safeguards (must all still hold)

| Safeguard | Where | Effect on WS-A |
|---|---|---|
| `NEVER_SPLIT_AFTER` abbreviations | chunker | Unchanged — still blocks "Dr." / "Mon." false boundaries even at 6 words. |
| `FORBIDDEN_CHUNK_STARTERS` | chunker (`split_tts_text` + streaming path) | Unchanged — still rejects a first chunk that would orphan a time-phrase ("in the morning…"). Critical at low thresholds; verify it fires on the shorter opener. |
| Markers (`PRE_SLOT_MARKER`, `ACK_FILLER_MARKER`, watchdog re-ask) | `llm_stream.py` / `_tts_loop` | Unchanged — WS-A only changes *when* the first content chunk is emitted, not how it's tagged or de-duplicated. |
| `flush()` tail emit | chunker | Unchanged. |

WS-A touches **only** the first-chunk emit threshold. No change to tool handling,
barge-in, dedup, or the TTS transport.

---

## 5. Measurement (from `LATENCY_MEASUREMENT_SPEC.md`)

The proof metric is **`chunk_gate_ms = t2 − t1`** (first-token → first-content-chunk),
`path=llm` only. Expected: a clear leftward shift in p50/p90 with the flag ON.
Also watch **TTFA (`t4 − t0`)** for the downstream effect.

Guardrail metrics (must not regress):
- **Choppy-opener rate** — qualitative listen-back on ≥30 openers; the opener must not sound clipped or land mid-clause. This is the only real risk of the lever.
- `FORBIDDEN_CHUNK_STARTERS` rejections at the low threshold — confirm none slip through as an ugly first fragment.
- Barge-in %, error % — should be flat (WS-A shouldn't touch them; if they move, something leaked).

---

## 6. Test procedure (side branch, separate number)

1. Bring up `latency-eval` deploy with `LATENCY_TIMING=ON`, `WS_A_FAST_FIRST_CHUNK=OFF`. Record baseline `chunk_gate_ms` + TTFA over ≥30 `llm` turns.
2. Flip `WS_A_FAST_FIRST_CHUNK=ON`, `WS_A_MIN_WORDS_FIRST=6`. Repeat the same call set.
3. Compare p50/p90/p95. Listen back to all openers for clipping.
4. Sweep the threshold (4, 5, 8) to find the knee where latency stops improving or openers start sounding clipped.
5. Record the winning value. **Do not promote** — promotion to live is a separate PR under normal review.

**Call set** must include the opener shapes that stress the lever: a short-first-sentence reply ("Right, let me check. …"), a reply whose first sentence starts with a time phrase (to exercise `FORBIDDEN_CHUNK_STARTERS`), and a reply that opens with an abbreviation.

---

## 7. Files in scope

| File | Change |
|---|---|
| `app/media_streams/chunker.py` | Add `min_words_first` param + `MIN_WORDS_FIRST`; first-chunk branch in `add_token`; `_chunks_emitted` counter. |
| `app/media_streams/config.py` | Wire `MIN_WORDS_FIRST` / flag env; make the (currently dead) config value actually imported. |
| `app/media_streams/llm_stream.py` | Only if §3.3 finds a reused chunker instance — add per-turn `reset()`. Expected: no change. |

Instrumentation (t1/t2 stamps) comes from the measurement spec and is a
prerequisite — build that first.

---

## 8. Explicitly out of scope for WS-A

- TTS transport (that's WS-B).
- Endpointing / turn detection (WS-C).
- Any change to non-first-chunk emission, prosody constants, or `MAX_WORDS`.
- Any promotion to `main`/live.
