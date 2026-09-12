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

Verified today: `TurnTiming` stamps **`t1` = first token only**
([latency_timing.py:234](../../app/media_streams/latency_timing.py:234)). There is
no inter-token stamp. So the blocker is still exactly what the doc says it is —
**and it is an instrumentation gap, not a design problem.**

---

## 5. Recommended order

1. **Instrument the token stream.** Add a last-token stamp and a simple
   inter-token summary (count, max gap) to `TurnTiming`, persisted through
   `as_record()` like S-9's `tool_calls`. Off the hot path in cost — one
   `time.monotonic()` per token — and it is the only thing that converts S-11
   from "do not touch without a new idea" into a decidable question.
   **Verifiable offline; no call needed; no caller-facing change.**
2. **Run the §3 cost query** against the corpus, then pick a rung-2 value on
   evidence. Probably 6,000 ms; possibly 7,000 ms if the 6–10 s band is fatter
   than expected.
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

## 7. What this scope does NOT claim

* It does not claim a fix is ready. It claims the mechanism is now identified,
  the wrong mechanism is ruled out, and the next action is a measurement.
* It does not claim today's 7.9 s is representative. It is one turn, in a hole
  the config comment already described in prose.
* It does not reopen S-11. `SLOT_PRESENTATION_FINISH_2026-09-10.md` §1.6 is
  explicit: *"Do not touch without a new idea, not just a new instance."* The
  new idea here is the instrumentation, not a remedy.
