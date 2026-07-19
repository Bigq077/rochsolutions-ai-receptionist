# Susie Latency Eval — Strategy, Baseline & Status

**The master doc.** Read this first, then `LATENCY_HARNESS.md` (the measurement system)
and `LATENCY_WS-C.md` (the active lever). This one holds the plan, the isolation rules,
the locked baseline, the current status ("you are here"), and the WS-A verdict.

*(Consolidates the former SIDE_BRANCH_EVAL_PLAN, BASELINE_LOCKED, WS-A_RESULT, and the
WS-A spec/pseudocode.)*

---

## 0. Prime directive — total isolation from live

- **The production number, `main`, and every live clinic branch stay exactly as they are.**
  No exceptions, ever, in this eval.
- All work is on the throwaway branch **`latency-eval`**, deployed to its **own Render
  service** behind its **own Twilio number** (setup + isolation invariants in
  `LATENCY_HARNESS.md`). This branch is a **lab, not a release candidate.**
- Even a winning lever is promoted to live only as a **separate, later PR under normal
  review** — never by merging `latency-eval` as-is.
- Every lever is behind its **own env flag, default OFF**, so the branch boots
  byte-identical to live and each lever toggles independently. No PII in commits/logs;
  Frankfurt/EU only.

*(Campaign note: for the battle-hardening pass, fixes now land on `latency-eval` itself —
see `SUSIE_HANDOFF_JULES.md`. The "nothing touches the live JV number" rule still holds.)*

---

## 1. The metric & the honest target

> **Voice-to-voice latency** = wall-clock from *caller stops speaking* → *first audio byte
> sent to Twilio*. You cannot tune what you can't measure — the whole eval hinges on this.

Aggregate **p50/p90/p95 across ≥30 `path=llm` turns** per config. **Single-call anecdotes
are not evidence** — the win is a distribution shift, and the failure modes (mid-number
cut-offs) live in the tail, so **p95 matters more than p50**.

**Honest expectation:** realistic combined win ≈ **300–500ms** (from ~2.6s toward
~1.4–1.7s voice-to-voice). Sub-1s is **not** on the table with a safety-first endpoint floor
for elderly callers — 600ms endpointing on capture turns is a *correct* choice, not a bug.

---

## 2. The turn anatomy + LOCKED baseline

Measured on the eval (flag OFF), caller stops speaking → first audio:

```
[caller stops] ─ ~600ms endpoint silence ─▶ [dispatch] ─ ~1225ms llm_ttft
                                                         ─ ~724ms  chunk_gate
                                                         ─ ~121ms  tts_first_byte ─▶ [first audio]
```
True voice-to-voice ≈ **2.65s** (≈600 endpoint + ≈2050 TTFA).

**Locked baseline** (flag OFF `flags=-`, `LATENCY_TIMING=on`, 4 calls, **n=28 completed**;
numpy-type7 percentiles via `lat_parse.py`). Raw data: `lat_baseline_29turns.txt`.

| metric (ms) | n | min | p50 | p90 | p95 | max |
|---|---|---|---|---|---|---|
| perceived TTFA (t4−t0) | 28 | 1072 | **2164** | 3037 | 3454 | 3701 |
| content TTFA (unmasked) | 28 | 1327 | 2182 | 3316 | 3663 | 4441 |
| llm_ttft | 28 | 1047 | 1210 | 1607 | 1636 | 2921 |
| **chunk_gate [WS-A target]** | 24 | 107 | **682** | 934 | 1048 | 1567 |
| tts_first_byte [WS-B] | 24 | 103 | 128 | 144 | 146 | 205 |

Per capture_phase (perceived TTFA p50 / p90 / chunk_gate p50): conversation (n=19)
2147/3276/662 · name (n=4) 2212/2353/**905** · phone (n=5) 2014/2389/680.
**This is the number every lever is judged against. Don't overwrite it — measure new arms
beside it.** (Baseline is n=28, 2 short of the ≥30 target, but the numbers converged
23→28, so it's locked.)

---

## 3. The three levers (ordered by win ÷ risk) + status

| Lever | What | Verdict | Flag |
|---|---|---|---|
| **WS-A** — chunk gate | Emit the first chunk sooner (retire the dead 15-word gate) | ✅ **NULL — shelved** (§5) | `WS_A_FAST_FIRST_CHUNK` OFF |
| **WS-B** — streaming TTS | WebSocket stream-input TTS (MODE B) | ⏭️ **skip** (§6) | — |
| **WS-C** — endpointing | Turn on the dormant semantic endpointer, phase-aware | 🔨 **the live lever** — see `LATENCY_WS-C.md` | `WS_C_SEMANTIC_ENDPOINT` |

### YOU ARE HERE
| Item | State |
|---|---|
| Baseline locked (§2) | ✅ done |
| WS-A (chunk gate) | ✅ tried → **null → shelved**, flag OFF. Don't reopen. |
| WS-B (streaming TTS) | ✅ decided **skip** (ceiling too low) |
| WS-C **Phase 1** — endpoint + cutoff instrumentation | ✅ **shipped** (`e7f64ff`) — but **not yet measured on calls** (deployed ≠ baselined). |
| WS-C **Phase 2** — the actual semantic endpointing | ✅ **SHIPPED, gated OFF** (`WS_C_SEMANTIC_ENDPOINT`). Silence-based phase profiles via mid-session `UpdateConfiguration` (confidence threshold is deprecated on our model — see `LATENCY_WS-C.md`). Capture-phase **hard gate enforced in code** (`ws_c_profile_for_phase` floors capture ≥ conversation). **Not yet measured on calls** — needs the Phase-1 baseline + A/B before promotion. |
| Response length (biggest real lever) | ⬜ deferred — prompt change, needs Quentin's sign-off. |

**Next three moves:** (1) capture the Phase-1 endpoint baseline (nobody has — ~30 turns,
read the WS-C ENDPOINT block from `lat_parse.py`); (2) build WS-C Phase 2 (Approach A,
gated behind `WS_C_SEMANTIC_ENDPOINT`); (3) A/B it with the **zero-new-mid-capture-cutoffs**
hard gate. Full plan in `LATENCY_WS-C.md`.

> **Note on `llm_ttft` (the biggest single slice, ~1225ms):** already prompt-cached
> (`llm_stream.py:443` two-block caching) — near its floor for Sonnet+tools, not a cheap win.
> The **biggest real lever is response length** (Susie's turns run 10–17s on slot lists/FAQ,
> which wastes time *and* causes echo-clipping) — but that's a prompt/behaviour change,
> out of scope until signed off.

---

## 4. Measurement discipline (why we can trust the verdicts)

Do the whole eval measurement-first: record the baseline, change **one** lever, measure
p50/p90/p95 beside the baseline, decide. **Stacking un-measured changes hides which lever
paid.** Sanity gate before trusting any run: the sub-splits must sum to TTFA within a few
ms, and `ep_dispatch_ms`/`audio_wire_ms` should be ~0 — if not, the instrumentation is
wrong, not the pipeline. Everything about the harness is in `LATENCY_HARNESS.md`.

---

## 5. WS-A — chunk gate: what was tried, and why it was NULL

**The idea.** The chunker delayed first audio two ways: (1) a hardcoded 15-word gate
(`chunker.py` `MIN_WORDS=15`) — while `config.py:168` had a **dead** `MIN_CHUNK_WORDS=8`
imported by nobody; and (2) **hold-one-behind** in `_handle_candidate` (the first valid
candidate is held until a *second* boundary confirms it). WS-A relaxed **both, for the
first chunk only**: lower the gate to `WS_A_MIN_WORDS_FIRST` (=6) and fast-release chunk 0
(skip the hold) when its opening isn't a `FORBIDDEN_CHUNK_STARTER`. Later chunks keep
`MIN_WORDS=15` and full protection. Implemented flag-gated (`WS_A_FAST_FIRST_CHUNK`,
default OFF = byte-identical to live); the code is still in `chunker.py`/`config.py`/
`llm_stream.py`, inert.

**The result — NULL.** A/B (3 calls, 25 completed, all `flags=A`) vs baseline:

| metric (ms) | baseline (n=28) | WS-A ON (n=25) | Δ |
|---|---|---|---|
| perceived TTFA p50 | 2164 | 2055 | −109 (noise) |
| **chunk_gate p50** | **682** | **724** | **+42 (no change)** |
| chunk_gate p90 | 934 | 1577 | +643 (worse) |

**chunk_gate — WS-A's direct target — did not move.** Why: chunk_gate is floored by the
time for the model to *stream the first full sentence to a hard `.`/`!`/`?`*. The word-gate
almost never bites (a first sentence rarely has a boundary between word 6 and word 15), and
the hold-removal doesn't show in aggregate. **The dead `MIN_CHUNK_WORDS=8` was a red
herring.** (Confound: the WS-A call set was heavier on long FAQ openers where WS-A
structurally can't help; short openers do show low gates 266–464ms, but too few matched
pairs to prove it.) **Verdict: don't promote, keep the flag OFF, don't reopen.** Raw data:
`lat_wsA_ON_27turns.txt`. Residual guardrail if ever revisited: listen-back on ≥30 openers
for clipped/orphaned first audio (the one real risk of a low gate).

---

## 6. WS-B — streaming TTS: skip (and the gate if ever revisited)

`tts_first_byte` is already **~121ms** (§2) — the entire slice WS-B could touch. Best case
~50ms saved for a substantial socket/barge-in rebuild. **Skip.** The original plan
estimated 150–300ms, written before we had the measurement; the data overrules it.

If ever revisited: MODE B WS stream-input TTS (`tts_stream.py:503 start_ws`) exists but is
unwired. **The blocker to prove FIRST is barge-in atomicity over a persistent socket** —
in MODE A an interrupt cancels the in-flight HTTP + drains the queue cleanly; over a WS,
buffered audio may play *after* the caller interrupts. **If clean barge-in can't be
demonstrated, WS-B stops there** — the latency win isn't worth a broken interrupt. Also
lower `chunk_length_schedule` from `[50,100,150]`.

---

## 7. Open decisions / promotion policy

- **WS-C calibration:** eval on synthetic test calls, or run **redacted** real-call audio
  through AssemblyAI's turn-detection calibration? (Better, but touches health data → EU
  redaction overhead.) See `LATENCY_WS-C.md`.
- **Promotion:** any live promotion of a winning lever is a **separate PR under normal
  review**. This branch never merges to `main`/live as-is.
