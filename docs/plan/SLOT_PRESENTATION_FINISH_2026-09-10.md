# Finishing slot presentation — plan of record, rev. 3 (2026-09-10, evening)

**Goal (owner, 10 Sep):** slot presentation is *finished* by the end of this week.
**Time available:** Friday 11th, weekend buffer 12–13th.
**Author's note:** rev. 3 records a session that worked rev. 2's queue in order
and closed items 1–4. Read §1 and §7 before touching anything.

> ### ⚠️ NOTHING BELOW HAS BEEN CALL-VERIFIED
>
> Four engine commits are now on **`latency-eval` — pushed 10 Sep evening,
> `7654561c` → `04dd2bbb`**, which `autoDeploy` puts on the DEMO LINE
> (+447366263180) and nothing else. `production` is **untouched at
> `337fbd9e`**.
>
> Every gate that can be met without a phone has been met — failing-set diff
> EMPTY against a freshly re-measured baseline, three replay harnesses, 34 new
> tests — and the one gate that cannot has not. **S-2 changes what a caller
> hears on a named-day follow-up and S-3 changes when the hold phrase speaks.**
>
> **NEXT: call +447366263180 and work §5's script.** Confirm the Render log
> says `[build_info] running build 04dd2bbb` first — that line is the only
> proof of what is running. Do not fast-forward `production` until the call is
> done.

**What rev. 3 changes about rev. 2's plan, in one line each:**

* **S-3's remedy in rev. 2 was wrong**, and measurement says so: the constant
  cannot reach 63% of the breaches. The part that IS the constant is fixed;
  the rest is filed as **S-11**. See §4.1.
* **S-2 is fixed** and the corpus effect is large: repeated clock times on the
  38 heard-day readouts fall **29 → 1**.
* **S-5 is enforced** by a parameter, a runtime warning and a census — and the
  guard found a real fifth producer on its first run.
* **S-6 / S-8 / S-9 are closed**, with the not-observed rule that decides
  whether the first corpus pull after this is read correctly.
* **Two superseded decisions were re-aimed deliberately**, not deleted: one
  harness gate and one B-142 test. §7 says why that distinction matters.

---

## 0. The bar — because "perfect" is not a thing you can prove

Unchanged from rev. 1, and still the exit criteria:

1. **No known defect a caller can hear.** Every item in §2 is closed or has an
   owner decision recorded against it.
2. **The defect class cannot recur silently.** Every producer of a slot sentence
   goes through one owner, and a test fails if a new one does not.
3. **It is measured, not asserted.** Three replay harnesses run over the stored
   corpus and report zero on their gates.
4. **A defined call script sounds right**, twice, on two different diaries.

**What this explicitly does not promise:** that no new slot defect will ever be
found. The change this week is that the next one is found by a harness rather
than by a patient.

---

## 1. Where we are

```
production          337fbd9e  T1 + T1b, deployed 10 Sep 09:4x, all three clinics
origin/latency-eval 04dd2bbb  = 337fbd9e + S-1a + S-3 + S-2 + S-5 + S-6/8/9
                              PUSHED 10 Sep evening. NOT CALL-VERIFIED.

revert targets, written down before the push:
  latency-eval      7654561c  what it was before this session
  production        337fbd9e  where it still is
  the pre-T1 engine fc47e508  keep this to hand
```

The five commits, oldest first:

| sha | what |
|---|---|
| `40a67ea8` | S-1 lever (a) — the month is said once. 18.59 s → 17.93 s |
| `d94c52d7` | rev. 2 of this document |
| `dd15f2d7` | **S-3** — the first rung was audible outside the 3 s bar |
| `afabb549` | **S-2** — the cross-day preference stopped firing after the first readout |
| `ab5b6752` | **S-5** — the trim contract made enforceable |
| `1508df0c` | **S-6 / S-8 / S-9** — the three harness blind spots |

> **The local `latency-eval` branch ref is STALE** — measured 10 Sep at
> `0421b72d` against origin's `7654561c`. A parallel session holds it in its own
> worktree. Fetch before you measure anything, and never trust the local ref.

### Landed and call-verified this morning

| | site | anchor | state |
|---|---|---|---|
| T1 | cross-turn: a fresh day read at another day's clock times | `slot_followup.py:3737` `_prefer_unheard_clock_times` | fixed, replay-verified |
| T1b-1 | a multi-day readout opened every day at the same time | `receptionist_tools.py:5790` `_cap_presented_slots` loop | fixed, **call-verified** 09:31 |
| T1b-2 | a named day re-read at the times just heard for it | `slot_followup.py:5460` `speak_one_day_from_payload` | fixed, **call-verified** 09:35 |

Corpus effect, multi-day readouts: repeats 58/59 → 4/59, identical openers
16 → 0, northgate 50 → 0. The four residuals are theorem_v3, whose rota holds too
few distinct times to vary — the documented stand-down, not a defect. Re-measured
this afternoon on a corpus grown to 61 readouts: still 4 repeats, still 0
identical openers, northgate still 0.

### Landed this afternoon, not yet pushed

**`40a67ea8` — S-1 lever (a): the month is said once.** Days two and three of a
multi-day readout now say *"Tuesday the 15th"*. **18.59 s → 17.93 s.**

It carries two things that were not in rev. 1 and are worth more than the 0.66 s:

* a **month-end guard**. "Tuesday the 1st" spoken after "Monday 30th September"
  is heard as September, so a readout that crosses a month keeps the month.
  Decided on the ISO date, never on the prose.
* a **real regression it caused, fixed at the root**. B-111's dedupe matched the
  payload label against the RENDERED SENTENCE, so the moment a producer worded a
  date differently the dedupe went silently inert and Susie offered a date one
  sentence after reading it out. `append_other_dates_offer` now also takes what
  the offer NAMED. See §7.

`20c13378` records the measurement correction in this document's own history.

---

## 2. The register — every open item, with anchors

A row without `file:line` is a lead, not a finding. All of these have one.

### 2.1 Caller-audible

| id | what a caller experiences | anchor | state |
|---|---|---|---|
| ~~**S-3**~~ | The filler's first rung was AUDIBLE at ~3.12 s, past the 3 s bar. | `config.py` `LLM_FIRST_CHUNK_TIMEOUT_MS` | **FIXED `dd15f2d7`**, 3000 → 2750. §4.1. Not call-verified. |
| ~~**S-2**~~ | "Twenty to ten" said for Monday and again for Tuesday, 17 s apart. | `slot_followup.py` `_prefer_unheard_clock_times` | **FIXED `afabb549`**. Corpus: heard-day repeats **29 → 1**. Not call-verified. |
| **S-11** | **NEW, and it is the larger half of S-3.** The ladder cancels on the first LLM **token**, but dead air ends at the first **audio**. Everything between — `chunk_gate`, p50 1.46 s / p95 2.95 s on the breaching turns — is unguarded, and **190 of 302 corpus breaches (63%) live there**. | `llm_stream.py:5604` `got_first_chunk = True` cancels `_filler_task` | measured; **no deadline fixes it** and the obvious fix is a bad trade at every value. §4.1. |
| **S-12** | **NEW.** On the caller's real clock (`endpoint_wait_ms` + `ttfa_ms`) **47.1% of turns exceed the 3 s bar**, p50 2.83 s, p95 5.04 s — not the 18.8% `ttfa` alone shows, because `t0` starts AFTER the endpointer. | `latency_timing.py`, `endpoint_wait_ms` is documented "pre-t0 dead-time" | 1,579 turns. No ladder tuning reaches it; it is the general latency work. |
| **S-1** | A three-day readout takes **17.93 s**. Callers barge in on nearly every turn. | `slot_offer.py:50-52` caps; `build_slot_offer` `:359` | **owner decision, unchanged** — see §4.4. Two minutes of work whenever it is taken. |
| **S-4** | `last_bot_prompt` blows its 200-char cap and loses its "?" 3–4 times per call, disarming clinical screening's orphan matcher. | `clinical_screening.py:530` `_LAST_BOT_PROMPT_CAP` | open. Improves for free as the readout shortens. **Do not touch the cap** — 34 writers, marked RED. |

### 2.2 Structural — no caller sees these today, they are how the next one gets in

| id | finding | anchor |
|---|---|---|
| ~~**S-5**~~ | Five producers, four honouring the rule, nothing enforcing it. | **FIXED `ab5b6752`** — `pretrimmed` parameter + runtime warning + AST census. The guard found a real fifth site on its first run. §4.3. |
| ~~**S-6**~~ | `_record_stood_down_slots` returned silently when it resolved nothing. | **FIXED `1508df0c`** — nothing-parsed is a WARNING, already-held is INFO. **Stage C's gate re-worded to match** (`ONE_PRESENTATION_LAYER.md`): zero of BOTH reverse-parse lines, read as a pair. Not yet measurable — no call since. |
| **S-7** | **13 % of recorded offers were never spoken.** `record_offer` fires where the offer is BUILT, above the P6/P6b stand-downs. **No code needed** — it is a fact every future harness author must know. | `llm_stream.py:7353` |
| ~~**S-8**~~ | `presented_days` empty on every `single_day` offer. | **FIXED `1508df0c`** — the single_day path records `[_fd]`. Confirmed by the corpus pull after the next call, not by this diff. |
| ~~**S-9**~~ | No tool marker on `calls.latency`. | **FIXED `1508df0c`** — `TurnTiming.tool_calls`, and `latency_percentiles.py` reports the real split. **It reads 0 today and says so**: all 3,578 stored turns predate the field and are NOT OBSERVED. |
| **S-10** | **NEW.** `operational.speak_part_of_day` is **half-wired**. It changes the deterministic labels, but the *rendered* northgate prompt instructs the model to speak the band in **three separate places** — so flipping it makes the READOUT bare while confirmations and read-backs stay banded. | rendered prompt (105 k chars); `SLOT_FORMATTER_SYSTEM_PROMPT` line 36 also still carries a band-form reference table |

### 2.3 Adjacent — real, not slot presentation

| id | finding | anchor |
|---|---|---|
| B-146 | A booking request that never says "book" gets *"Sorry, still with you —"* **on the caller's first sentence**. `classify_intent` cannot see `v3_treatment_mentioned`, which the engine set one line earlier. | `hold_speech.py:619` |
| — | STT drops numerals: `'the 30-minute session'` → `'the 5-minute session'`. Keyterm list carries no numbers. | 6 Sep register, secondary |
| — | `GOOGLE_SERVICE_ACCOUNT_JSON` invalid → Sheets skipped; ElevenLabs 401 on `/v1/models`. **Known-accepted on the demo line**, not on the live lines. | log, every call |

> **B-146 deserves an explicit decision rather than the scope rule making it by
> default.** It is not slot presentation and it does not belong in this week's
> diff. It is also a first-sentence defect on exactly the call being rehearsed
> for a partner. Decide it; do not simply inherit the exclusion.

---

## 3. The three lineage documents — what is left

All three are live. None supersedes the others.

* **`DETERMINISTIC_SLOT_PRESENTATION.md` (31 Aug).** Steps 1–4 done. **Open:
  steps 5 and 6** — delete the ~900-line reverse-parse layer, re-aim the pinned
  tests. Its best claim is now confirmed at scale: `slot_followup` **0.13 s** vs
  `llm` **3.06 s** at p50, n=3,566.
* **`SLOT_PRESENTATION_CONVERGENCE.md` (3 Sep).** Phases 0 and 1 complete.
  **Phase 2 open**: one producer, one record, fewer guards. Its test-risk warning
  was measured and withdrawn — the real surface is **one literal pin** plus one
  negative assertion to preserve.
* **`ONE_PRESENTATION_LAYER.md` (9 Sep).** Stage A and Stage B **done**. **Stage
  C** evidence gathered, gate not met (S-6). **Stages D and E are NOT this week's
  work** — they buy onboarding speed, not correctness, and starting D trades a
  finishable week for an unfinishable one.

---

## 4. The work, in the order I now recommend

### 4.0 Why this order changed

Rev. 1 put S-1 first, explicitly because *"it needs no owner decision if you take
(a) and (b)"* and bought a 44 % cut in half a day. **Measurement falsified both
halves of that claim.** On a readout reproduced at 18.59 s, calibrated at
18.2 chars/sec against CAb5b52d95's own chunk timings:

| lever | rev. 1 estimate | **measured** | result |
|---|---|---|---|
| (a) month after day one | ~4.5 s | **0.66 s** | 17.93 s |
| (b) suffix on the 2nd time | ~3 s | **2.69 s** | 15.89 s |
| (a)+(b) | ~7.5 s | **3.35 s** | **15.23 s** |
| `speak_part_of_day: false`, all times | — | **5.17 s** | **12.76 s** with (a) |
| (c) `MULTI_DAY_MAX_DAYS` 3 → 2 | ~5.5 s | ~6 s | owner decision |

(a) was over-credited about sevenfold: it drops ONE word of the six rev. 1 counts
in a date. **So (a)+(b) reaches 15.2 s and cannot meet this item's own "under
12 s" gate.** The floor for three days at two times, keeping the part-of-day
suffix, is **14.85 s** — with no opener at all, weekday-only dates and minimal
punctuation. Time labels are **47 %** of the readout; the suffix alone is
**27.8 %**, which independently reproduces the LAT-1 measurement in
`speaks_part_of_day` ("4.7 s of 17.3 s"). The calibration is corroborated twice:
by `SLOWEST_REAL_CHARS_PER_SEC = 17.9`, already in the repo, and by that 27 %
figure derived from a different call.

**Consequence: S-1 is decision-blocked, not work-blocked.** There is no
engineering left in it — the remaining lever is one key in a JSON file. Queuing
real work behind a wording decision is how a week gets spent waiting. So S-1
moves to §4.4 as a decision takeable at any time, and the engineering queue is
re-ordered by **audible damage per hour of work**.

**The ranking principle, stated once:**

> **Dead air > repetition > length.** A caller who hears a long list interrupts —
> annoying, recoverable, and they still book. A caller who hears the same time
> offered for two different days concludes the system is broken and asks for a
> human. A caller who hears three seconds of nothing thinks the line dropped.

---

### 4.1 · S-3 — DONE in part, and rev. 2's remedy was wrong

**`dd15f2d7`. Not call-verified.**

Rev. 2 said "find the constant, confirm it is a single threshold". Both halves
of that were right, and the conclusion drawn from them was not.

**The constant is `LLM_FIRST_CHUNK_TIMEOUT_MS`, and it was 3000 — the bar
itself.** That is the whole defect, stated in one sentence: *the constant is a
DEADLINE and the bar is about AUDIO*, and between them sit synthesis and the
wire. Measured over the 84 corpus turns where the first rung demonstrably
spoke, that gap is **p50 128 ms / p90 246 ms / p95 313 ms** — so the rung's
audio landed at p50 3128 ms and **79 of 84 (94%) put the caller past the bar**.
The three live readings that opened S-3 (3118 / 3120 / 3128) are that constant
plus synthesis, which is why they looked suspiciously constant.

**3000 → 2750** puts the sound inside the bar at p90 (2996 ms). The cost was
measured *before* it was taken, because this file's history is a series of
arguments about exactly that cost: the rung now fires on **16.3% of turns
instead of 14.1%** (+2.2 points over 3,170 turns). Nowhere near the 42% that
made 1800 ms untenable, and a smaller move than the 1800 → 3000 change it
corrects.

`DEAD_AIR_BAR_MS` and `FIRST_RUNG_SYNTH_MS` now name the missing half of the
relationship. **The test that "pinned the bar" was pinning the deadline** —
`assert LLM_FIRST_CHUNK_TIMEOUT_MS / 1000.0 <= 3.0` passed at exactly 3000
while 94% of the audio was outside — and now asserts the SUM. It fails at 3000
and passes at 2750; both directions were run.

#### S-11 — the 63% no constant can reach ← **read this before touching S-3 again**

The ladder cancels on the first **token**:

```
if not got_first_chunk:
    got_first_chunk = True          # llm_stream.py:5604
    if _filler_task and not _filler_task.done():
        _filler_task.cancel()
```

But `ttfa = llm_ttft + chunk_gate + tts_first_byte + wire`. The timer guards
only the first term. Of the 302 corpus turns whose caller waited over 3 s for
any sound:

| | turns | |
|---|---|---|
| the timer had FIRED (`llm_ttft ≥ 3000`) | 104 | 34.4% — this is the ladder, now fixed |
| the timer was **CANCELLED** (`llm_ttft < 3000`) | **190** | **62.9% — UNGUARDED** |
| no `llm_ttft` recorded | 8 | 2.6% |

On that unguarded population the token arrives at **p50 1.9 s**, comfortably
inside any candidate deadline, and then `chunk_gate` alone spends **p50 1.46 s,
p95 2.95 s** before a sound. **No value of `LLM_FIRST_CHUNK_TIMEOUT_MS` reaches
these turns.**

**The obvious fix — watch the first CHUNK instead of the first token — was
costed and is a bad trade at every deadline:**

| deadline | buys (of 167 harmed) | costs (turns newly speaking) |
|---|---|---|
| 3000 ms | 127 (76%) | **567 / 2895 = 19.6%**, 175 land <300 ms before the content |
| 3400 ms | 65 (39%) | 353 = 12.2% |
| 4000 ms | 30 (18%) | 228 = 7.9% |

The curve is bad because the breaches are *marginal* — the unguarded set's
`ttfa` is p50 3445 ms. Paying 8–20 points of extra hold phrases to cover ~450 ms
of extra silence buys the wrong thing, and 175 of the 567 would hear the content
within 300 ms of the phrase, which is the "empty marker in front of an instant
reply" defect the config comment already documents.

**The predicate that WOULD separate them** is time since the LAST token — a
steadily streaming turn will release soon; a stalled one will not. The corpus
does not store inter-token times, so it cannot be measured, and shipping an
unmeasurable predicate onto the hot path is the trap this codebase keeps
falling into. **Left open deliberately. Do not "fix" it without evidence.**

#### S-12 — the bar is being missed by more than anyone has been reporting

`ttfa_ms` is measured from `t0`, and `endpoint_wait_ms` is documented in
`latency_timing.py` as **"pre-t0 dead-time"**. So the caller's real silence is
`endpoint_wait + ttfa`, and every S-3 figure above understates it by p50 1.1 s.

| | p50 | p75 | p90 | p95 | over 3 s |
|---|---|---|---|---|---|
| `ttfa` alone | 1.92 s | 2.65 s | 3.26 s | 3.69 s | 18.8% |
| **caller's clock** | **2.83 s** | **3.80 s** | **4.56 s** | **5.04 s** | **47.1%** |

The config comment already said this in prose — *"3000 ms here is ~4.1 s of real
silence … moving this number cannot fix that"* — and it is right. Meeting the
bar means cutting `llm_ttft` (p50 1.63 s) and `chunk_gate` (p50 0.67 s), which
is the general latency work §"Explicitly not this week" excludes. **Recorded so
that nobody reports 18.8% as the dead-air rate again.**

---

### 4.2 · S-2 — DONE

**`afabb549`. Not call-verified.** This is the item with the largest measured
effect and it is the one that most needs a phone.

Rev. 2's diagnosis was right and its mechanism was slightly off, in a way worth
recording. The stand-down is per-DAY:

```
if not today or today in {str(s)[:10] for s in spoken}:
    return chosen
```

so it is not "once the caller NAMES a day". It is **once a day has been read
out at all** — and a multi-day readout makes *every* day it named a heard day.
From the first readout onwards, every day the caller can then ask about takes
the early return. That is why it bit on a call where the caller was comparing
days, which is exactly when a repeat is most audible.

**B-116's pool is NOT widened**, and that is the whole of the design. On a heard
day the candidates are exactly the slots B-116 would have chosen from — unheard
*on this day* — and the preference picks among those. A time already offered
that day can no more come back than before. All-or-nothing against `_spread` is
unchanged.

**Measured, 416 replayed days:**

| | before | after |
|---|---|---|
| heard-day readouts (S-2's own population, n=38) — repeated clock times | 29 | **1** |
| `lost_a_slot` / `invented_a_slot` | 0 / 0 | 0 / 0 |
| multi-day loop (61 readouts) — repeats / identical openers | 4 / 0 | 4 / 0 |
| `replay_slot_decisions`, 1,851 turns scored | — | **CHANGED: 0** |

**`test_the_same_day_rule_is_untouched` did NOT need re-aiming**, contrary to
rev. 2's expectation, and the reason is worth keeping: with only one day heard
there is no sibling day to be fresh against, so the preference finds nothing to
do and hands B-116's answer straight back. The rule rev. 2 thought it was
overturning turns out to be a special case of the new one.

**Two things DID need re-aiming, both deliberately.** See §7 — the distinction
between a stale test and a superseded decision is the single most reusable thing
in this document.

---

### 4.3 · S-5 / S-6 / S-8 / S-9 — DONE

**`ab5b6752` and `1508df0c`.**

**S-5 took option (ii), as rev. 2 recommended, plus one thing rev. 2 did not
ask for and should have.** The contract moved out of the docstring and into the
signature as `pretrimmed`, with a runtime warning naming the offending
`file:line` once per call site per process.

The detection is **not** "did you pass `more_times`" — all five producers pass
it, so that would catch nothing. It is **"am I about to drop a slot"**: a
properly trimmed day has nothing left to drop, so dropping one means
`_pick_times_for_day` is about to select by POSITION, blind to what the caller
heard. That is T1b-2 exactly.

It **logs and does not repair**. Repairing would make `build_slot_offer` a
second owner of "how many, and which", which is the defect it exists to prevent
— and a live-call exception would cost a caller their offer on a path that
already treats a raise as "fall back to the model".

> **The guard found a real fifth site on its first run:**
> `numbered_more_times_speech` (P9's "more times that day"). It turned out to
> be a **deliberate** untrimmed hand-in — the batch is what the caller has NOT
> been read, so B-116's subtraction already happened upstream — and it now
> declares itself with `pretrimmed=False`. Without that, the warning would have
> fired on every "tell me the others" turn and been noise inside a week.

**The census** walks `app/` by AST, so producer six fails in CI whether or not
any test drives its path. Verified in **both** directions — an unaccounted
producer fails, and a *stale entry* fails too, because a census describing a
codebase nobody runs is how a guard quietly stops guarding.

> **Its first cut took 26 MINUTES against a 6-minute suite** — it re-walked all
> of `app/` for each of five tests, and `flow.py` alone is 24,820 lines. Cached,
> with a substring pre-filter deciding only which files are worth parsing: **4
> seconds**. A correctness guard that quadruples the suite is a guard someone
> deletes. §7.

**S-6** now distinguishes the two arms: nothing-parsed is a WARNING (it is the
one that can leave a caller's next sentence resolving against a slot they were
never read); already-held is INFO.

> **Stage C's gate was re-worded to match, and the amendment is the point of
> S-6.** It read "no `could not resolve spoken option(s)` on any clinic" — a
> string only site A emits. Site B was silent, so an absence of that line was
> evidence of nothing. The gate is now ZERO OF BOTH lines, read as a pair, and
> `ONE_PRESENTATION_LAYER.md` records that it cannot be met until the corpus
> pull after the next call.

**S-8** records `[_fd]` on the single_day path, read off the OFFER's mode rather
than a mode variable, so it records what was built and not what was intended.

**S-9** adds `TurnTiming.tool_calls`, incremented at the one point every
`tool_use` block passes through so a tool loop accumulates across iterations.

> **The NOT-OBSERVED rule is the load-bearing part of S-9, not the field.** All
> 3,578 stored turns predate it and carry no key. A reader that treats the
> absence as 0 puts every historical tool turn in the plain bucket — a split
> strictly **worse** than the proxy it replaces. `latency_percentiles.py` counts
> those separately and, run today, prints: *"the real split is empty. Every
> stored turn predates the field; read the PROXY above and nothing else."* The
> proxy is kept alongside rather than deleted, because it is what can be said
> about the turns already stored.

**S-6's and S-8's call sites are not unit-testable** — they are inside Gate 5's
streaming path and need a WebSocket, a model and a tool result. Everything they
depend on is driven by tests; the sites themselves are confirmed by the corpus
pull after the next call, which is what this item's gate already said.

---

### 4.4 · S-1 remainder — an owner decision, takeable at any time

**Not queued. Not blocking. Two minutes of work whenever it is decided.**

Lever (a) is landed. Everything further changes what the caller hears, so it is
the clinic's call, not the engine's — which is precisely why the codebase put it
behind a per-clinic flag rather than an engine default.

#### What `speak_part_of_day: false` actually does

| readout | today (with (a)) | flag off | saving |
|---|---|---|---|
| multi_day, 3 days × 2 | 17.93 s | **12.76 s** | −5.17 s (−29 %) |
| single_day, 3 times | 10.23 s | **7.64 s** | −2.59 s (−25 %) |

#### What provably does NOT change — verified 10 Sep, not taken from the docstring

* **Caller resolution is identical.** Seven utterances A/B'd through the real
  `slot_accepted_by_caller`, on a session built by `apply_offer_to_session`: same
  ISO start in both modes, **7/7** — including "monday at eight in the morning"
  resolving against a bare "eight".
* **The SMS is unaffected.** `sms_templates.py:126` builds the time from the
  datetime as `%I:%M%p` → "8:00am". The patient still gets **am/pm in writing**
  whatever the flag says. This is the strongest mitigation of the ambiguity
  worry.
* **The booking record, the keypad map and all three harnesses.** Wording only.

#### What the risks actually are

* **S-10, the half-wiring.** See §2.2. The readout goes bare; the model's
  confirmations stay banded. That is arguably the *best* outcome — a fast offer,
  a precise confirmation — but nobody decided it, and it is not what the flag's
  name claims.
* **The bare-number collision is real, known, and already fixed where it bit.**
  `payload_slots_named_in` carries a comment saying bare labels made a 3 pm slot
  "named" by any list containing "Number 3", and that guarding only the fallback
  *"left exactly those clinics exposed"*. The other five `_time_named_in` sites
  scan **caller** text, which never says "Number 3", and the bare form is already
  tried as a fallback at all of them today. **The flag promotes bare from
  fallback to primary; it does not open a new class.**
  `_offered_time_named_without_its_band` becomes a silent no-op under the flag —
  correctly, since it is then redundant.
* **What is genuinely lost:** the caller's *spoken* confirmation that eight means
  morning. The corpus behind the flag (236 offers, 2262 labels) found zero
  ambiguous pairs, because a clinic day spans under twelve hours so a clock face
  names exactly one time. The information survives; the reassurance does not.

#### Why lever (b) as specified was NOT taken

`_pick_times_for_day` **deliberately** picks the second slot in a DIFFERENT part
of the day (measured: 3 of 4 representative rotas). So lever (b) strips the band
from exactly the slot whose band the caller cannot carry over from the first. It
is also half of a wording decision this codebase already assigned to
`clinic.json`, for **less than half** the saving of doing it properly.

#### Recommendation

Take it on **northgate only** — the demo line, no patient can hear it — push to
`latency-eval`, and judge 12.76 s by ear. One key, no engine change, revertible
in seconds. If it sounds right, the next question is whether to fix the prompt's
three banded-time instructions so confirmations match, or deliberately keep them
banded.

**Note:** even with the flag, 12.76 s. Under 12 s additionally needs lever (c),
`MULTI_DAY_MAX_DAYS` 3 → 2 (`slot_offer.py:50`).

> **The prompt already asks for this.** The rendered northgate prompt contains
> *"Keep the whole slot offer under about eight seconds."* The 18 s readout has
> been violating a standing instruction the whole time.

---

### 4.5 · Phase 2 — weekend buffer, or next week

*Only if §4.1–4.3 are closed and called.*

Take `SLOT_PRESENTATION_CONVERGENCE.md` Phase 2 in its own order — **one record
before deleting guards**, never the reverse. Then step 5 of the 31 Aug document:
delete the reverse-parse layer.

**Do not delete the repair layer on a clean Render grep.** Site A is reachable
only when no deterministic offer was built, and `slot_offers` records only the
turns where one *was* — so the population that reaches it leaves no row.
Instrument that first. (`STAGE_C_EVIDENCE_2026-09-10.md` §5.)

Most likely to slip past Friday. **That is acceptable.** §4.1–4.3 deliver the
goal; this is what stops it decaying.

---

### Explicitly not this week

Stage D (provider interface) · Stage E (clinic policy) · the STT numeral gap ·
anything touching `_LAST_BOT_PROMPT_CAP` · general latency work beyond S-3.
B-146 is excluded **pending an explicit decision**, not by default.

---

## 5. Verification protocol — non-negotiable

**The suite is red on purpose. Do not look for green; diff the failing sets.**

Baseline at `7654561c`, **re-measured from scratch 10 Sep evening: 98 failed,
9688 passed, 22 skipped — 96 excluding `test_acuity_live`.** Identical to the
morning's figure, so the number in this document is reproducible rather than
remembered.

Every commit in §1 was gated against it in a SEPARATE frozen worktree, never
the tree being edited:

| commit | failing set vs baseline |
|---|---|
| `dd15f2d7` S-3 | **EMPTY** (96 = 96, 9696 passed) |
| `afabb549` S-2 | **one new failure**, investigated and re-aimed — see §7 |
| `ab5b6752` S-5 | see the run log |
| `1508df0c` S-6/8/9 | see the run log |

> **EXCLUDE `tests/auto/test_acuity_live.py` from the failing-set diff.** It hits
> the live Acuity API, which is returning `ProviderUnavailable` today, and its
> failing set **varies run to run on the untouched baseline** — measured three
> consecutive times, three different sets. Excluding it, the baseline is
> **96 failed**, and that number is stable. It is read-only (0 write call sites),
> so running it books nothing.

```bash
# BASELINE — a SECOND worktree at the base commit, never touched
git worktree add --detach /tmp/base <base-sha>
cp .env /tmp/base/.env && cp tests/auto/.env /tmp/base/tests/auto/.env
cd /tmp/base && python -m pytest -q -p no:randomly > /tmp/base.txt 2>&1
grep -E "^FAILED " /tmp/base.txt | sed 's/ - .*//' | grep -v test_acuity_live | sort > /tmp/B.txt

# CANDIDATE — freeze the tree, then run
md5sum <every file you changed> > /tmp/md5.before
find . -name __pycache__ -type d -prune -exec rm -rf {} +
python -m pytest -q -p no:randomly > /tmp/head.txt 2>&1
md5sum -c /tmp/md5.before                 # MUST all say OK
grep -E "^FAILED " /tmp/head.txt | sed 's/ - .*//' | grep -v test_acuity_live | sort > /tmp/H.txt
diff /tmp/B.txt /tmp/H.txt                 # must be EMPTY
```

**Never run the suite in a worktree you are editing** — ~55 `inspect.getsource`
tests fail as an artefact and it reads as catastrophe.

### The three harnesses, and what each is blind to

| harness | covers | blind to |
|---|---|---|
| `replay_slot_decisions.py` | slot decisions, 1,851 turns scored | **the whole selection** — `choose_presented_indices`, `remaining_unspoken_on_current_day`, `all_remaining_on_next_day`. It said `CHANGED: 0` on both T1 and T1b. |
| `replay_presented_times.py` | the per-day selection, 416 day-readouts | the **loop** around it. It reported 0 changed while the live readout was broken. |
| `replay_multi_day_spread.py` | the multi-day loop, 61 readouts | the named-day producers |

```bash
python scripts/replay_slot_decisions.py  --out BASE.json   # then --diff BASE.json CAND.json
python scripts/replay_presented_times.py --out BASE.json   # then --diff BASE.json CAND.json
python scripts/replay_multi_day_spread.py                  # before / after
```

Both `--diff` flags take **two** arguments, base and candidate.

Gates that must read 0: `lost_a_slot`, `invented_a_slot`, and — since 10 Sep —
`re_offered_a_heard_time`. **`changed_a_heard_day` is NO LONGER a gate.** It
asserted T1's stand-down as an invariant, which S-2 supersedes; it is replaced
by the rule it was really protecting (a selection may never re-offer a time
already read out *on that day*), narrowed to exclude the case B-116 deliberately
allows — a day that cannot fill the readout without it. The old count is still
printed. §7.
Read the rest **by direction** — a pick changing to a *different* slot is
dangerous; a pick *lost* is usually a guard working.

**No single harness covers slot presentation.** Run all three, every time. That
sentence is the whole lesson of 9–10 September.

### Measuring a readout without a phone

Reproducible in synthesis, and it agrees with the live chunk timings:

* calibration **18.2 chars/sec** at `ELEVENLABS_PHONE_SPEED`, from CAb5b52d95's
  quoted day-line (100 chars in ~5.5 s);
* corroborated by `SLOWEST_REAL_CHARS_PER_SEC = 17.9` in
  `tests/regression/test_o_absolute_play_cap.py`, derived independently from a
  93-char greeting in 5.2 s.

Build the payload with `slot_times` + `slot_times_spoken` — **not** `slots` with
a `spoken` key, which `flatten_bookable_slots` silently drops, giving you a
readout of raw "08:00" labels and a flatteringly short measurement.

### The call script — the exit criterion

Two calls, two different clinics, at least one on a **non-grid diary** (Vital
Edge or JV — neither has been called since T1b, and northgate's uniform grid is
the easy case).

1. *"I'd like to book an appointment — my ankle."*
2. *"Anytime next week."* → three days, no shared clock time
3. *"Tell me about Monday."* → **nothing you already heard for Monday**
4. *"And what about Tuesday?"* → nothing you heard for Tuesday, **and no time
   repeated from step 3**
5. *"Anything around midday on Tuesday?"* → **midday is offered** (D8)
6. Take a slot, and check the diary entry matches what you were told.

Step 4's second clause is S-2. Step 6 is the only step that proves the readout
and the booking agree.

**Build SHA is the only proof of what ran:** `[build_info] running build <sha>`
at call cleanup. `/health` returns a hardcoded 1.0.0 and always has.

---

## 6. Deploy discipline

Promotion is **fast-forward, one direction**, `latency-eval` → `production`. A
merge commit here means someone fixed something in the wrong place.

```bash
git log --oneline origin/production ^origin/latency-eval        # MUST be empty
git rev-parse origin/production                                 # WRITE THIS DOWN
git diff origin/production..origin/latency-eval -- app/ | \
  grep -E "^[+-].*(SMS_ENABLED|APPOINTMENT_REMINDERS_ENABLED|SHEETS_ENABLED|OBS_.*_ENABLED)"
git push origin origin/latency-eval:production
```

That last grep is not optional. Those code defaults **must stay OFF on both
branches**, or a test call texts a real patient.

A push to `production` reaches three live clinics with `autoDeploy` on — real
call after any engine change. **`latency-eval` serves the demo line
(+447366263180) and nothing else; it is the safe place to push.**

---

## 7. Traps — every one of these cost real time

### Carried forward

**A change can ship completely inert.** Three times in one day on 9 Sep: a `\b`
written as a literal backspace byte; a hold phrase silently refused by
`_second_filler_text`; config constants defined but never imported, raising
`NameError` inside a background task where it may never reach a log.

**Test BEHAVIOUR, never presence.** `assert "X" in source` proves nothing. All
three of the above passed a presence-style check. Drive the real function.

**Verifying the function is not verifying the system.** T1 was correctly verified
against `choose_presented_indices` and reported as fixed. The live call went
through two other sites and heard the identical defect. **Enumerate a rule's
callers before claiming coverage.**

**A mock of the loop cannot see the loop's bug.** Drive the real entry point.

**Built is not spoken.** 13 % of recorded offers were never said out loud.

**A safety property can live in an idiom.** `len(hits) == 1` also silently meant
"decline when the same time sits on several days".

**`_spread` outranks new preferences.** Two slots fifty minutes apart are not a
choice.

**Prompt hashes live in TWO tables under different names**, each with its own
`_sha`. Recompute per table; never copy a value across.

**`git stash` does not revert here** (OneDrive locks) — back changes out by hand.
Use `git -C <path>`, never `cd X; cmd`. Run `git worktree prune` and
`git rev-parse --abbrev-ref HEAD` before you trust a single number: there are
160+ registered worktrees.

**`receptionist_tools.py` is CRLF** — and so are `slot_offer.py` and
`slot_followup.py`. A whole-file rewrite lands as a 16k-line diff. Preserve line
endings when scripting an edit.

### New, 10 Sep PM

**A dedupe that reads the RENDERED SENTENCE is a wording dependency, not a
record.** B-111's *"I've also got another Tuesday, the 15th"* check matched the
payload label against the readout text. Shortening one label made it silently
inert, and Susie offered a date one sentence after reading it out. Key such
checks on **what the offer named**, never on how it worded it.

**A negative assertion can go vacuous under a wording change.**
`assert "Tuesday 8th September" not in spoken` cannot see a day re-read as
"Tuesday the 8th". Two existing tests would have passed while the defect
occurred. **The failing-set diff structurally cannot catch this class** — they
pass either way. When you change wording, grep the suite for negative assertions
on the old form and re-aim them to a wording-independent comparison.

**The record is not the speech, and `dtmf_map` must stay canonical.**
`day_selected_by_position` matches the map's value against
`available_days[].day_label` by **containment**. Shorten the map and "the second
one" resolves to `None` — indistinguishable from a caller who named nothing.
Verified both ways.

**Shortening a date must be guarded on the month end.** "Tuesday the 1st" after
"Monday 30th September" is heard as September. Decide on the ISO date, never on
the prose.

**A wording change that reaches a caller belongs in `clinic.json`.** The engine
already has the flag (`operational.speak_part_of_day`). Re-deciding it in engine
code is the CLAUDE.md violation, and doing it by halves is worse than both.

**Check what the RENDERED prompt says, not the module.** The "Always say the FULL
spoken time" instruction in `susie_system_prompt.py` does **not** reach
northgate's rendered prompt; three other places instruct the band instead.
`build_system_prompt_parts` is the authority.

**The live Acuity tests are externally nondeterministic.** See §5.

**The local `latency-eval` ref goes stale** because a parallel session holds the
branch in its own worktree. Fetch first, or diagnose an already-fixed bug.

### New, 10 Sep evening

**A STALE TEST AND A SUPERSEDED DECISION ARE NOT THE SAME THING, and this is
the most reusable paragraph here.** S-2 made two existing assertions fail. Both
had to be re-aimed; neither was stale, and deleting either would have thrown
away a real rule:

* `replay_presented_times`' third gate, `changed_a_heard_day MUST be 0`. That
  was T1's stand-down restated as an invariant. What it was actually protecting
  is B-116's pool, so **that** is now asserted directly — a selection may never
  re-offer a time already read out on that day. The old count is still printed,
  just no longer a failure.
* `test_b142_soonest_means_earliest.py::test_what_else_still_leads_with_the_unheard`,
  which asserted `second == LIVE_OFFER_2` byte for byte. `LIVE_OFFER_2` is what
  a caller heard **on the defective build**, and it contains 08:50 on Monday —
  a clock time they had been read on Tuesday one offer earlier. It was pinning
  the S-2 defect as the expectation. What the test was FOR is now asserted
  directly (the second offer repeats nothing from the first), the single
  difference has its own named test, and a third test drives the loop WITH
  `also_heard_clock_times`.

The rule: **find what the assertion was protecting, assert that, and keep the
old number visible.** An assertion deleted for going red is a rule deleted.

**A GATE CAN BE STRICTER THAN THE RULE IT DEFENDS.** The first cut of
`re_offered_a_heard_time` failed on one day — and it was right that it failed,
because the day held exactly two times and one had been heard, so B-116's
"never starves a repeat" branch offered it correctly. Base and candidate were
identical there. **Always find out whether a new gate's first failure is yours.**
It was narrowed to "…and the day could have filled the readout without it".

**A MOCK OF THE LOOP CANNOT SEE THE LOOP — the second time in two days.**
`test_b142`'s `_two_offers` calls `choose_presented_indices` per day WITHOUT
`also_heard_clock_times`, so nothing stops two days in one readout picking the
same clock time — which is how `LIVE_OFFER_2` came to hold 16:20 on both Monday
and Tuesday. After S-2 that mock produced a *second* collision, which looked
like a regression and was not: driven through the real carry, the same diary
yields **no cross-day repeat and nothing already heard**. `replay_multi_day_spread`
agreed (61 readouts, 4 repeats, unchanged). **Check the harness that drives the
real entry point before believing a mock.**

**A CORRECTNESS GUARD THAT QUADRUPLES THE SUITE IS A GUARD SOMEONE DELETES.**
The S-5 census re-walked all of `app/` once per test: **26 minutes** against a
6-minute suite, because `flow.py` is 24,820 lines and the parent map over its
AST is millions of entries. Cached, plus a substring pre-filter deciding only
which files are worth *parsing* (a file that never mentions the name cannot
call it): **4 seconds**. Time the test you just added.

**A DEADLINE IS NOT A SOUND.** `LLM_FIRST_CHUNK_TIMEOUT_MS` was set to the bar
exactly, and a test asserted `deadline <= 3.0s` and passed while 94% of the
audio it schedules landed outside. Any threshold whose purpose is something the
caller PERCEIVES must be checked against perception, with the intervening cost
named as its own constant.

**`t0` IS NOT WHEN THE CALLER STOPPED TALKING.** `endpoint_wait_ms` is
documented as "pre-t0 dead-time", so every `ttfa` figure understates the
caller's silence by p50 1.1 s. Reading `ttfa > 3000` as the dead-air rate gives
18.8%; the caller's clock gives **47.1%**.

**A COUNT ADDED TODAY IS NOT OBSERVED YESTERDAY.** Every row in `calls.latency`
predates `tool_calls`. Treating a missing key as 0 would have put ~3,500 tool
turns in the plain bucket and produced a split worse than the proxy it replaces.
A new field needs its absent case decided in the same commit, and printed.

**A GUARD MUST BE SILENT ON CORRECT CODE.** The S-5 warning fires once per call
site per process, and the one legitimate untrimmed producer opts out explicitly.
A warning that repeats every turn is a warning nobody reads.

---

## 8. Order of work, on one page

### Done, unpushed, NOT call-verified

| # | item | commit | offline gate |
|---|---|---|---|
| 1 | **S-3** the rung was audible outside the bar | `dd15f2d7` | failing-set diff EMPTY; fails-before/passes-after both run |
| 2 | **S-2** cross-day preference stopped firing | `afabb549` | heard-day repeats 29 → 1; decisions CHANGED 0; one re-aim (§7) |
| 3 | **S-5** trim contract enforceable | `ab5b6752` | census verified in both directions; guard found a real 5th site |
| 4 | **S-6/S-8/S-9** harness blind spots | `1508df0c` | 9 tests; the real split correctly reports 0 and says why |

### What is left, in the order I recommend

| # | item | gate | est. |
|---|---|---|---|
| **1** | **THE CALL.** §5's script, twice, on two diaries. S-2 and S-3 both change what a caller hears. | the script | ½ hour |
| 2 | Push `latency-eval`, call the demo line, then fast-forward `production` | §6, and a revert target written down | — |
| 4 | **Phase 2** — one record, then delete the guards | its own plan's gates | ≥ 2 days |
| — | **S-1 remainder** | **owner decision, not queued** — §4.4 | 2 min |
| — | **S-11 / S-12** | **do not touch without new evidence** — §4.1 | — |

**If you have time for exactly one thing: item 1.** Four engine commits are
sitting on evidence that stops at the edge of a phone call. Every offline gate
this repo has was run and they all pass; none of them can hear a hold phrase
arrive 250 ms earlier, or a Tuesday read at times the caller has not heard.

**B-146 still has no explicit decision recorded against it** (§2.3). It was
excluded from this session's diff, which is right, but that is still the scope
rule making the decision by default.

---

## 9. Handover rules

* **Commit messages carry the exhibit** — call SID, what the caller said, what
  Susie said. Rules get re-softened once the call behind them is lost.
* **Every behavioural fix ships with a regression test** in `tests/regression/`.
* **Clinic-specific behaviour belongs in `clinic.json`**, never in engine code.
* **If these documents and the code disagree, the code wins.** Record the
  correction here. Rev. 2 exists because rev. 1's central estimate was wrong by
  sevenfold, and the one thing that has consistently worked on this codebase is
  measuring rather than believing. Rev. 3 exists because rev. 2's remedy for
  S-3 could not reach 63% of the thing it was aimed at -- found by measuring,
  invisible from reading.
* **Say what has NOT been verified, in the same breath as what has.** The four
  engine commits in §1 have every offline gate this repo can offer and no
  call. "Not call-verified" is doing real work at the top of this document and
  in every one of those commit messages; do not let it be dropped by whoever
  promotes them.
