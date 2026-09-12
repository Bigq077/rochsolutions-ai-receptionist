# Scope — the stall ladder and the 7.9 s of dead air on `CA1ef288f1`

**Asked for:** scope `LATENCY_DISTRIBUTION_2026-09-10.md` §8 item 1, the
18.7 % of turns with over 3 s of silence, which DEC-1 named as N5's replacement.

**Measured on** `44e2d451` (= `origin/latency-eval`).

> 🔴 **This scope CORRECTS the finding it was opened on.** The 12 Sep evening
> audit and the handover both attribute the dead air to the no-input watchdog
> failing to arm after a filler head (`connection.py:5044`). **That attribution
> is wrong.** The watchdog's behaviour there is correct and deliberate; the
> silence comes from the stall ladder's second rung, which is a different
> mechanism with a different owner and a different fix. The corrected reading is
> below, and `DOC_AUDIT_2026-09-12_EVENING.md` §A1 should be read against it.

---

## 1. What actually happened on turn 2

```
13:28:27.151  iteration=1               LLM dispatched      (_filler_t0)
13:28:27.758  situational head (named_week)                 rung 1, ~600 ms
13:28:30.116  tts_finished — head ends
                                         ← 7.9 s of silence
13:28:35.459  first token                                   llm_ttft = 8,313 ms
13:28:38.007  first content audio                           content_ttfa = 10,990 ms
```

`[LAT] turn_seq=2 ttfa_ms=734 content_ttfa_ms=10990 llm_ttft_ms=8313`

**Rung 1 fired and worked.** The caller heard *"Let me see what next week looks
like —"* at 734 ms. The silence is between the head ending and the content
starting.

**Rung 2's deadline is 10,000 ms from dispatch**
([config.py:630](../../app/media_streams/config.py:630),
`LLM_FILLER_SECOND_STALL_MS`). The token arrived at **8,313 ms — 1.7 s before
rung 2 was due to wake.** So rung 2 never ran, and nothing else was going to
speak.

**There is a structural hole between the rungs.** Rung 1 lands at 600 ms
(situational) or 2,750 ms (contentless, `LLM_FIRST_CHUNK_TIMEOUT_MS`). Rung 2
lands at 10,000 ms. Any stall whose first token arrives between roughly 3 s and
10 s produces **exactly one head followed by unbounded silence** — up to ~9.4 s
of it. Today's turn sat in the middle of that hole.

The config comment already states the population: *"over 5.6 s is 13.9 % of
turns, over 10 s is 2.0 %"* — so **~12 % of turns can land in the hole**, which
is the right order of magnitude for the measured 18.7 % of turns carrying >3 s
of silence.

---

## 2. Why the watchdog attribution was wrong

`on_tts_finished` does return early while the LLM is in flight —
[connection.py:5044](../../app/media_streams/connection.py:5044):

```python
if self._llm_busy:
    return
```

and `on_llm_finished`'s recovery (`:4909`) only fires when `_watchdog_deferred`
was set, which requires `_restart_timer` to have been reached — it was not. B-151's
safety net (`:4961`) is **gated on `tts_inhibit` and nothing else**, and its own
comment explains why the gate must stay narrow: *"A wider gate would arm on
every ordinary turn whose audio is still playing and take the DEFERRED_CLEAR
hand-off away."*

So it is true that no watchdog was armed. **But the no-input watchdog is the
wrong instrument for this silence.** It exists to re-ask the caller when *they*
say nothing. Here the caller has spoken and is waiting on *us*. Arming it mid-LLM
is precisely the spurious-re-ask-during-a-tool-call defect its docstring says the
guard prevents:

> *"the delayed TTS-done callback for the previous question can fire after
> on_transcript_received() cancels the timer but before `_llm_busy` is set;
> without this guard the timer re-arms and can fire during the
> check_availability tool call, causing a spurious re-ask concatenated with the
> slot list."*

**Do not touch `connection.py:5044`, `on_llm_finished`, or B-151's gate for this
defect.** The instrument for "we are slow" is the ladder in
`llm_stream.py::_delayed_filler`, and that is where the fix belongs.

### Consequence for DEC-1's lead
DEC-1 says the real lead is *"the ladder NOT arming on 14–21 s silent turns with
a **fast** first token."* Today's turn had a **slow** first token (8.3 s) and
rung 1 **did** arm. **Today's call is therefore NOT a fourth instance of that
lead** — it is a different population. The audit's claim that it was is
withdrawn.

DEC-1's population is S-11 (§4 below).

---

## 3. Option A — lower `LLM_FILLER_SECOND_STALL_MS`

The cheapest real improvement, and the one today's call is evidence for.

**Why it is safer than it looks.** `LLM_FILLER_SECOND_MIN_GAP_MS = 4000` is an
absolute floor on the gap between the two rungs, and its comment says it exists
for exactly this change:

> *"MIN_GAP is the structural guard that makes stacking unrepresentable whatever
> the other numbers become. In practice it never binds (a 600 ms head is 9.4 s
> clear of the 10 s deadline); it exists so that the next timing change cannot
> recreate this defect the way `dc6f521e` did."*

At a 6,000 ms rung-2 deadline with a 600 ms rung 1, MIN_GAP still does not bind
(4,600 ms < 6,000 ms). At 4,000 ms it begins to bind and becomes the real
deadline. **So 6,000 ms is the most aggressive value that changes nothing
structural.**

**What it would have done today:** rung 2 wakes at 6.0 s, token at 8.3 s → a
second phrase plays, cutting 7.9 s of silence to ~3.4 s and ~1.9 s.

**What it costs — and this is the number nobody has:** the share of turns whose
token arrives between 6 s and 10 s, each of which would newly hear a second hold
phrase. The corpus can answer this directly (`llm_ttft_ms` is stored per turn),
and it **must be answered before the change**, because this is the exact shape of
the `dc6f521e` regression: a deadline moved without anyone deciding to, taking
turns from 4.8 % to 13.9 %.

**Cost query:** over stored turns, `count(6000 <= llm_ttft_ms < 10000) / count(*)`,
split by clinic, and of those the share where `content_ttfa_ms - 6000 < 300`
(the "phrase lands just before the content" defect the config comment names).

Needs the obs corpus — see §6.

---

## 4. Option B — S-11, the 63 % no constant reaches. **Do not start.**

`SLOT_PRESENTATION_FINISH_2026-09-10.md` §4.1 already costed this and parked it
with reasons. Summarised so nobody re-derives it:

* The ladder cancels on the first **token**; dead air ends at the first
  **audio**. `chunk_gate` between them is p50 1.46 s / p95 2.95 s on breaching
  turns.
* **190 of 302 corpus breaches (62.9 %) have `llm_ttft < 3000`** — the timer had
  already been cancelled. No deadline reaches them.
* Watching the first chunk instead was costed at three deadlines and is a bad
  trade at all of them: 3,000 ms buys 76 % of the harm but makes **19.6 %** of
  all turns newly speak, and 175 of those hear content within 300 ms of the
  phrase.
* **The predicate that would separate them is time since the LAST token** — a
  streaming turn will release soon, a stalled one will not.

**That last point is the one thing that has changed, and it is the actionable
item.** The doc says *"the corpus does not store inter-token times, so it cannot
be measured, and shipping an unmeasurable predicate onto the hot path is the trap
this codebase keeps falling into."*

Verified today: `TurnTiming` stamped **`t1` = first token only**. There was no
inter-token stamp. So the blocker was exactly what the doc says it is — **an
instrumentation gap, not a design problem** — and it is now closed: `token_count`,
`last_token_ms` and `max_inter_token_ms` ship on the `[LAT]` line and the stored
row (§5 step 1, done).

### The two options address DISJOINT populations — do not mix the readings

Building the instrumentation made this sharper, and it matters for whoever reads
the data:

**"Time since the last token" is undefined before the first token.** So:

| population | shape on the `[LAT]` line | which option reaches it |
|---|---|---|
| **S-11's 63 %** — token arrives early, then the chunk/TTS gap | `llm_ttft_ms` small, `max_gap_ms` tells you whether the stream then went quiet | **Option B.** The predicate applies |
| **The rung hole** — no token at all for seconds | `llm_ttft_ms` large (8,313 ms today), `max_gap_ms` small or -1 | **Option A only.** No last-token predicate can reach it — there is no last token |

`CA1ef288f1` turn 2 is the second row: reconstructed synthetically it emits
`llm_ttft_ms=8250 max_gap_ms=250` — the token was slow to *start* and the stream
then flowed steadily. **A reader who saw `max_gap_ms=250` and concluded "the
stream was healthy, so no predicate would have helped" would be right about the
predicate and wrong about the caller**, who heard 7.9 s of nothing. The remedy
there is the rung-2 deadline, not the predicate.

So: filter on `llm_ttft_ms` FIRST, then read `max_gap_ms`. The two questions are
separate and each has its own answer.

---

## 5. Recommended order

1. ~~**Instrument the token stream.**~~ ✅ **DONE 12 Sep.** `token_count`,
   `t_last_token` and `max_inter_token_gap` on `TurnTiming`, with
   `note_token()` (last-write-wins) and `note_stream_break()` so a tool round
   trip is not scored as the model going quiet. One `time.monotonic()` per
   token; no list allocation on the hot path.

   **Put on the printed `[LAT]` line as well as the stored row** —
   `tools= tok= last_tok_ms= max_gap_ms=` — which also closes the S-9 residual
   (`tool_calls` was stored but never printed). That is deliberate: a read-only
   `OBS_DATABASE_URL` is the standing blocker on five measurements, and
   printing these means **the Render log alone is enough to collect S-11
   evidence, with no provisioning step in front of it.** `lat_parse.py` coerces
   all seven to ints; its kv parser absorbed the new fields without change.

   Verified: 8 new tests in `tests/regression/test_s11_inter_token_gaps.py`;
   whole `tests/regression` run **8,903 passed with the same 5 standing
   failures** (`b84` ×3, the multi-day count, scarcity multiple-days) — zero new
   failures. No behaviour change: nothing reads these fields.
2. ~~**Run the §3 cost query** against the corpus, then pick a rung-2 value on
   evidence. Probably 6,000 ms; possibly 7,000 ms if the 6–10 s band is fatter
   than expected.~~ ✅ **DONE — see §7. The answer is 7,000 ms, and the guess of
   6,000 ms above was wrong**: at 6,000 ms `MIN_GAP` binds on the contentless
   rung-1 path and the constant stops meaning what it says, which is the
   `dc6f521e` failure shape rather than a rate problem. 3,203 turns measured.
3. **Ship the constant with a call.** One value, one commit, a regression test
   pinning the sum against MIN_GAP (`test_b19_filler_rearm.py` already asserts
   that sum — extend it rather than adding a new file).
4. **Only then** revisit S-11, with the inter-token data in hand.

**Do not do 2 before 1** if the corpus turns out to lack the rows; and do not do
3 before 2, because the cost is the whole question and the last person to move a
ladder deadline without measuring caused `dc6f521e`.

---

## 6. The blocker, again

Steps 1 and 2 both need the stored corpus: step 1 to write to it, step 2 to read
`llm_ttft_ms` out of it. This is the **fifth** item to land on the same
dependency — a **read-only `OBS_DATABASE_URL`**
(`DOC_AUDIT_2026-09-12_EVENING.md` §B, `OWNER_DECISIONS_2026-09-12.md` DEC-1).

Step 1 is worth doing regardless, because it is what makes the corpus able to
answer the question at all.

---

## 7. THE MEASUREMENT — step 2, done 12 Sep evening

Read from the obs corpus (`OBS_DATABASE_URL` is set in the slotspec `.env` and
reachable; one read-only `SELECT`). **3,203 `path=llm` turns with a usable
`llm_ttft_ms`**, across all four clinics — an order of magnitude more than the
294 turns the config comment was written on.

`IMPOSSIBLE_MS = 10_000_000` applied, per `latency_percentiles.py`. **I first ran
it without the filter and got a p90 of 11,873,267,705 ms** — the absolute-clock
defect of `LATENCY_DISTRIBUTION_2026-09-10.md` §7, exactly as that section warns.
The filter is not optional; 0 `llm_ttft` values were impossible, but
`content_ttfa` needed it.

### 7.1 Emission rate by candidate deadline

| D | fires on | rate | silence STILL LEFT after the phrase | wasted firings (content <300 ms later) |
|---|---:|---:|---|---:|
| 5,600 ms | 179 | 5.6 % | p50 3.6 s, p90 8.0 s | 0 |
| **6,000 ms** | 151 | **4.7 %** | p50 3.4 s, p90 7.7 s | 1 of 107 = 0.9 % |
| 6,500 ms | 130 | 4.1 % | p50 3.1 s | 0 |
| **7,000 ms** | 101 | **3.2 %** | p50 3.1 s, p90 7.5 s | **0 of 68** |
| 8,000 ms | 81 | 2.5 % | p50 3.0 s, p90 7.0 s | 0 of 53 |
| 10,000 ms (now) | 30 | 0.9 % | p50 4.4 s | 0 of 15 |

**Two objections die here.**

*"It will pad turns that were about to answer."* It will not: at every candidate
deadline there is a **median 3 s or more of silence still to cover** after the
phrase, and the wasted-firing rate is 0–0.9 %. If the token took over 6 s, the
content takes substantially longer again.

*"It recreates `dc6f521e`'s 13.9 %."* Not on this corpus. `dc6f521e`'s rejected
setting (5.6 s) measures **5.6 %** today, and the rate the owner accepted before
it (4.8 % at 8.0 s) is matched by **6,000 ms at 4.7 %**. The engine is roughly
twice as fast as it was on 1 Sep, so the comment's percentages overstate the cost
of every value.

### 7.2 By clinic — and `northgate` is the outlier

| clinic | turns | ≥6.0 s | ≥7.0 s | ≥8.0 s | ≥10.0 s (today) |
|---|---:|---:|---:|---:|---:|
| jv_v1 | 2,077 | 3.0 % | 2.1 % | 1.7 % | 0.7 % |
| **northgate** | 597 | **9.7 %** | **6.2 %** | 4.9 % | 1.2 % |
| theorem_v3 | 400 | 6.0 % | 4.2 % | 3.5 % | 1.8 % |
| vital_edge | 129 | 5.4 % | 2.3 % | 2.3 % | 1.6 % |
| **ALL** | 3,203 | 4.7 % | 3.2 % | 2.5 % | 0.9 % |

The aggregate is dominated by `jv_v1` (65 % of turns), which is the fastest.
**`northgate` — the demo line, and the line the 294-turn sample was almost
certainly taken on — runs at double the aggregate rate.** At 6,000 ms it would
emit on nearly one turn in ten, which is the neighbourhood of the rate that was
rejected. Judge a candidate on the `northgate` column, not the ALL row.

### 7.3 The structural argument settles it: **7,000 ms**

`wake_at = max(_filler_t0 + STALL_MS, now + MIN_GAP_MS)`, computed:

| rung 1 | STALL 6,000 | STALL 7,000 | STALL 8,000 |
|---|---|---|---|
| 600 ms (situational) | 6,000 — ok | 7,000 — ok | 8,000 — ok |
| 2,750 ms (contentless) | **6,750 — MIN_GAP BINDS** | 7,000 — ok | 8,000 — ok |

**At 6,000 ms the constant stops meaning what it says** on any turn that took the
contentless rung 1: MIN_GAP silently moves the deadline to 6,750 ms. That is the
same *class* of defect as `dc6f521e` — a number whose effective value is set by
something else — and MIN_GAP exists to make that unrepresentable, not to absorb it.

**7,000 ms is the lowest value at which MIN_GAP never binds on either rung-1
path.** It also carries zero wasted firings (0 of 68), leaves a median 3.1 s
still to cover, and holds `northgate` at 6.2 % — below the rejected band.

**Recommended: `LLM_FILLER_SECOND_STALL_MS = 7000`.** Conservative alternative
**8,000 ms**, which reproduces the historically accepted rate almost exactly on
the line it was measured on (`northgate` 4.9 % against the recorded 4.8 %).

On `CA1ef288f1` turn 2 (token 8,313 ms, content 10,990 ms), a 7,000 ms rung 2
fires at 7.0 s and covers ~4.0 s of the 7.9 s hole.

### 7.4 What is still NOT measured

**`max_gap_ms` — zero turns carry it.** The field shipped in `7cc67f31`, which
deployed to the three clinic services and the demo line on 12 Sep evening; every
stored turn predates it. So **§4's Option B remains unanswerable until calls
accumulate on `7cc67f31` or later**, and this measurement settles Option A only.

Re-run 7.1–7.2 once the corpus has post-`7cc67f31` turns; the same script filters
on `llm_ttft_ms` first, then reads `max_gap_ms`, per §4's warning.

---

## 8. What this scope does NOT claim

* It does not claim a fix is ready. It claims the mechanism is now identified,
  the wrong mechanism is ruled out, and the next action is a measurement.
* It does not claim today's 7.9 s is representative. It is one turn, in a hole
  the config comment already described in prose.
* It does not reopen S-11. `SLOT_PRESENTATION_FINISH_2026-09-10.md` §1.6 is
  explicit: *"Do not touch without a new idea, not just a new instance."* The
  new idea here is the instrumentation, not a remedy.
