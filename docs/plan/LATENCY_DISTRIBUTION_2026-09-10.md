# Latency distribution — 2026-09-10 (T2)

**Corpus:** `calls.latency`, 587 calls / 3,566 turns, all four clinics.
**Method:** `scripts/latency_percentiles.py` (added with this note). Measurement
only — no engine code was touched for this.
**All figures in seconds.**

---

## 0. The one-line answer

> **`llm_ttft` is the latency. Everything else is rounding.**

At p95 the caller waits **8.28 s** for the answer. Of that, **5.62 s** is the
model's first token and **3.58 s** is the chunk gate. TTS contributes
**0.19 s** — it is not worth optimising and should be taken off the list.

The bar in `CLAUDE.md` §6.2 is *p95 caller-perceived turn latency under 1.5 s*.
The corpus says **p95 = 8.28 s**, and **86.2 % of all turns** are over 1.5 s.
This is not a tuning gap; it is a different order of magnitude.

---

## 1. Components, all turns

|                     |     n | p50 | p75 | p90 | p95 |  p99 |   max |
|---------------------|------:|----:|----:|----:|----:|-----:|------:|
| `endpoint_wait_ms`  | 1,601 |1.10 |1.50 |1.80 |1.90 | 2.10 |  3.60 |
| `llm_ttft_ms`       | 3,158 |1.63 |2.21 |3.94 |**5.62**| 9.74 | 28.57 |
| `chunk_gate_ms`     | 2,886 |0.67 |1.12 |1.85 |**3.58**| 4.33 | 21.66 |
| `tts_first_byte_ms` | 2,846 |0.12 |0.13 |0.14 |0.19 | 0.92 |  3.47 |
| `ttfa_ms` (any sound)| 1,598 |1.92 |2.65 |3.26 |3.70 | 5.22 | 21.43 |
| `content_ttfa_ms` (the answer)| 1,574 |**2.93**|4.48 |6.29 |**8.28**|13.63| 24.74 |

`ttfa_ms` and `content_ttfa_ms` must be read as a pair. `ttfa` is how long until
Susie makes any sound at all; `content_ttfa` is how long until she says the
thing the caller asked for. A low `ttfa` against a high `content_ttfa` is the
filler ladder working, not latency being acceptable.

**Against the two published bars:**

| bar | measured |
|---|---|
| p95 caller-perceived under 1.5 s | **8.28 s**; 1,357 of 1,574 turns (86.2 %) over 1.5 s |
| no dead air over 3 s without a filler | 299 of 1,598 turns (18.7 %) had **no sound at all** for over 3 s |

That second row is the one to act on first. Nearly one turn in five is more
than three seconds of pure silence — the ladder is not covering them.

---

## 2. By path

| path | n turns | `llm_ttft` p95 | `content_ttfa` p50 | p95 |
|---|---:|---:|---:|---:|
| `llm` | 3,344 | 5.66 | 3.06 | 8.47 |
| `scripted` | 155 | — | 0.12 | 0.14 |
| `slot_followup` | 67 | 0.01 | 0.13 | 0.16 |

The deterministic paths answer in **130 milliseconds**, sixty-five times faster
than a generated turn at p50. Every utterance moved off the `llm` path is worth
roughly three seconds to the caller. That is the strongest argument in this
document for the fast path and for `slot_followup` — and it is an argument for
*widening* them, which is engine work with a call gate, not tonight's.

---

## 3. The tool-result split the runbook asked for — and why it is not here

**It cannot be made from the stored corpus.** Nothing in a `calls.latency` turn
records whether the turn ran a tool:

* `path` separates `llm` / `scripted` / `slot_followup`, not tool from plain;
* `flags` are the A/B lever letters (`WS_A_FAST_FIRST_CHUNK` etc., see
  `app/media_streams/latency_timing.py:57`), nothing to do with tools;
* `capture_phase` is which question was on the table (conversation / name /
  phone).

So the table below is a **proxy, named as one**: turns where a filler played
before the content (`content_ttfa - ttfa > 1 s`). Every tool turn slow enough to
matter is in that set — and so is every slow plain generation, so these are an
**upper bound on the tool-turn cost, not the tool figure**.

| | n | `llm_ttft` p50 | p95 | `chunk_gate` p50 | p75 | p95 | `content_ttfa` p50 | p95 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| filler covered the wait | 894 | 2.60 | 8.72 | 0.90 | **2.98** | 4.07 | 4.84 | 10.56 |
| no filler needed | 2,264 | 1.51 | 2.65 | 0.60 | **0.98** | 1.69 | 2.32 | 4.12 |

Two things fall out:

1. **The gap is the model, not the plumbing.** `llm_ttft` p50 goes 1.51 → 2.60
   and p95 2.65 → 8.72 between the two sets.
2. **`chunk_gate` triples at p75** (0.98 → 2.98) on covered turns. That is not
   the model; it is our own gate holding text back. On a slow turn we add ~2 s
   *on top of* an already slow first token. This is the single most tractable
   number in this document, because it is entirely ours.

**To get the real split, one line is needed:** carry a tool-call count on
`TurnTiming` and emit it in the record. That is a code change on a live path and
therefore has a call gate, so it is written down here rather than made tonight.

---

## 4. By clinic — `content_ttfa_ms`

| clinic | n | p50 | p75 | p90 | p95 | p99 | max |
|---|---:|---:|---:|---:|---:|---:|---:|
| northgate | 555 | 3.23 | 4.87 | 6.52 | 8.41 | 13.03 | 22.97 |
| theorem_v3 | 479 | 2.90 | 4.41 | 6.25 | 7.93 | 13.13 | 20.48 |
| jv_v1 | 418 | 2.75 | 3.93 | 5.90 | 7.80 | 11.42 | 20.42 |
| vital_edge | 122 | 2.95 | 4.04 | 6.51 | 10.06 | 17.19 | 24.74 |

**No clinic is meaningfully faster than another.** The spread at p50 is half a
second across four different prompts, two booking providers and four diaries.
Latency here is a property of the engine, not of any clinic's configuration —
so there is no per-clinic tuning to be done, and a fix helps all four equally.

`vital_edge`'s heavier tail (p95 10.06 against 7.80–8.41) is 122 turns and
should not be read as a finding without more data.

Only one STT model appears in the corpus (`u3.5-pro`), so the U3.5 A/B cannot be
scored from this column — see [[u35-stt-lever-already-built]].

---

## 5. The worst ten turns

| call | seq | content | ttfa | ttft | gate | tts | clinic |
|---|---:|---:|---:|---:|---:|---:|---|
| CA8522b3e23fc6 | 5 | 24.7 | 21.4 | 1.2 | 2.1 | 0.11 | vital_edge |
| CAffc720c24d4d | 5 | 23.0 | 0.7 | **22.4** | 0.4 | 0.12 | northgate |
| CA56b262493525 | 2 | 20.5 | 2.7 | **19.3** | 1.1 | 0.12 | theorem_v3 |
| CAe84b871bcbd8 | 8 | 20.4 | 1.9 | **20.0** | 0.3 | 0.12 | jv_v1 |
| CA8522b3e23fc6 | 6 | 18.0 | 17.7 | — | — | — | vital_edge |
| CA7888178d5913 | 5 | 15.5 | 0.7 | **15.0** | 0.4 | 0.13 | northgate |
| CAa4231548cafb | 14 | 15.1 | 3.1 | **14.3** | 0.7 | 0.12 | northgate |
| CAec05ee5ba236 | 1 | 15.0 | 2.3 | **14.8** | 0.1 | 0.12 | theorem_v3 |
| CA7d6aa7714225 | 5 | 14.6 | 14.6 | 1.8 | 1.6 | 0.13 | jv_v1 |
| CA1736b441372d | 5 | 14.6 | 0.8 | 8.3 | **6.2** | 0.13 | theorem_v3 |

Eight of the ten are a single slow first token — 14 to 22 seconds from one
provider call. **`tts_first_byte` never exceeds 0.13 s in this table.**

Two are a different shape and worth separating: `CA8522b3e23fc6` seq 5–6 and
`CA7d6aa7714225` seq 5 have `ttfa` ≈ `content_ttfa` with a *fast* `ttft`, which
means nothing was said for 14–21 s while the model answered promptly. That is
the ladder failing to fire, not the model being slow, and it is a different
defect from the other eight.

---

## 6. What this does and does not say about N5

The runbook parks `feat/n5-third-rung` and cites CA5e14516b as evidence against
it. **This corpus agrees, and adds the reason.** On that call the first token
arrived at 9.5 s, inside the 10 s deadline, so the ladder correctly stood down
and a third rung would not have fired.

But §5 shows a second population where the ladder did not fire and should have
— `ttfa` of 14–21 s with a fast first token. **Those are not a third-rung
problem either.** A third stall phrase helps only when the ladder is already
running and runs out of things to say; these turns had the ladder never start.
So the corpus argues against N5 twice, and points at the arming condition
instead.

**This is a lead, not a finding** — three calls, no file:line, and the arming
path has not been read. It needs its own scoping pass before anyone schedules
it. ([[anchor-defect-rows-before-scheduling]].)

---

## 7. A corpus defect found while doing this

`ttfa_ms` and `content_ttfa_ms` were written as an **absolute clock reading
rather than a delta from t0** on:

* **clinic:** `jv_v1` only — northgate, theorem_v3 and vital_edge never did it
* **window:** 22–23 Aug 2026
* **builds:** `c4b5b0c54dbd`, `562715d35021`, `e4e5f936b821`, `8221248ff018`,
  `d9670eaeb18b`, `2048582db65d`, `625344fb4d1f`
* **volume:** 1,934 turns, values around 11,941,606,930 ms ≈ 138 days

The component fields on those same turns (`llm_ttft_ms`, `chunk_gate_ms`,
`tts_first_byte_ms`) are sound; only the two totals are not.

**Historical, not live.** Nothing has produced one since 23 Aug. No fix is
proposed and none is possible — `t0` was never stored, so the rows cannot be
repaired.

**But it must never be aggregated.** The first run of this analysis, before the
filter, reported a p50 `content_ttfa` of **11,849,310 s** and a jv_v1 row that
was pure nonsense — and the shape of the table looked entirely normal. The
filter is `IMPOSSIBLE_MS` in `scripts/latency_percentiles.py`, it prints what it
dropped every run, and it is deliberately not silent.

---

## 8. Recommended order of work — none of it tonight

Ranked by seconds returned to the caller per unit of risk.

1. **The 18.7 % of turns with over 3 s of total silence.** A published bar,
   already breached, and §5 says at least some are an arming failure rather than
   a slow model. Scope the ladder's arming condition first.
2. **`chunk_gate` on slow turns** — p75 0.98 → 2.98. Entirely our own code, and
   it compounds precisely on the turns that are already worst.
3. **Move utterances off the `llm` path.** `slot_followup` answers in 0.13 s
   against 3.06 s. The largest available win, and the largest blast radius.
4. **`llm_ttft` itself** — model, prompt size, caching. The biggest number and
   the least under our control.
5. **Not `tts_first_byte`.** 0.19 s at p95. Take it off the list.

Every one of these is engine work on a live path and needs a real call to
verify, so none of it is a tonight job.
