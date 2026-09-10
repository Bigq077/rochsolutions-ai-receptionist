# Finishing slot presentation — plan of record, rev. 4 (2026-09-10, evening)

**Goal (owner, 10 Sep):** slot presentation is *finished* by the end of this week.
**Time available:** Friday 11th, weekend buffer 12–13th.
**Author's note:** rev. 4 adds the first call against these fixes (§1.5). Read
§1.5 and §7 before touching anything, and §8 for what to do next — it is
ordered by what blocks what, not by size.

> ### STATUS — called once, 10 Sep 13:43. Three fixes verified, one new P1.
>
> On **`latency-eval` (`9259595f`)**, demo line only. `production` is
> **untouched at `337fbd9e`**.
>
> | | |
> |---|---|
> | **VERIFIED ON A PHONE** | S-2 (script steps 2, 3, 4 — four log lines), S-1a (wording + 17.85 s), S-9 (tool marker on all six turns) |
> | **SHIPPED, NOT EXERCISED** | S-3 (both slow turns had a situational head), S-6, S-8 |
> | **NEW, P1, OPEN** | **S-13** — a caller who names a time on a day already on the table is answered without it. Asked twice for midday, offered neither time, hung up. §4.4 |
> | **NEW, structural** | **S-14** — 4 of 5 readouts on that call left no obs row. §2.2 |
>
> **DO NOT PROMOTE TO `production` YET.** S-13 is on the same code path S-2
> just changed, and no call has reached script step 6, so the readout and the
> diary have never been checked against each other on this build.
>
> **NEXT: §8 step 1.** Confirm the Render log says
> `[build_info] running build <sha>` before trusting any call — `/health`
> returns a hardcoded 1.0.0 and always has.

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
* **The call found S-13 and S-14**, and §4.5 records the uncomfortable part:
  S-2 did not cause S-13, but it removed the coincidence that had been hiding
  it. A call step that passes without its mechanism firing is not a pass.

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

### Scored against the bar, 10 Sep evening

| | | |
|---|---|---|
| 1 | No known defect a caller can hear | ❌ **S-13 is open and P1.** S-2, S-3 and the T1/T1b family are closed. S-1 and S-4 have owner decisions recorded. |
| 2 | The defect class cannot recur silently | ✅ for the trim contract (S-5: parameter + runtime warning + census, verified both directions). ⚠️ **not yet true of D8** — S-13 was invisible for days because no test and no log distinguished "the pin fired" from "B-116 left the time in the pool". |
| 3 | Measured, not asserted | ✅ three harnesses run, all gates 0. ⚠️ but S-14 means the offer corpus sees only the Gate 5 path — 4 of 5 readouts on the last call are absent from it. |
| 4 | A defined call script sounds right, twice, on two diaries | ❌ **one call, one diary, steps 1–4 of 6.** Step 6 — the only step that proves the readout and the diary agree — has never been reached. |

Two of four. The honest summary is that the readout is now *correct where it
has been measured*, and the measurement stops short of a booking.

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
| `2abea957` | rev. 3 of this document |
| `04dd2bbb` | Stage C's gate re-worded (S-6) |
| `9259595f` | the push record — **this is the SHA that was called** |

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

### Landed this afternoon, pushed 10 Sep evening

**`40a67ea8` — S-1 lever (a): the month is said once.** Days two and three of a
multi-day readout now say *"Tuesday the 15th"*. **18.59 s → 17.93 s predicted,
17.85 s measured live on the 13:43 call** — see §1.5.

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

## 1.5 The call — CAb8ac636017de7d35370fd7951c54d3cf, 10 Sep 13:43, northgate

**Build confirmed from the Render log: `[build_info] running build 9259595f50f5`.**
105 s, `outcome=abandoned`, judge score 1. Six turns, all six stored.

### Steps 1–4 PASS. S-2 is verified live, four times.

The rule fired and logged itself on every readout after the first:

```
13:43:33  B-116 picked ['08:00','16:20']            for 2026-09-15 -> reading ['08:50','16:20']
13:43:33  B-116 picked ['08:00','17:10']            for 2026-09-16 -> reading ['09:40','15:30']
13:43:54  B-116 picked ['08:50','09:40','16:20']    for 2026-09-14 -> reading ['10:30','11:20','14:40']
13:44:07  B-116 picked ['08:00','09:40','15:30']    for 2026-09-15 -> reading ['12:10','13:00','13:50']
```

| step | asked | heard | verdict |
|---|---|---|---|
| 2 | "anything next week" | Mon 08:00/17:10 · Tue 08:50/16:20 · Wed 09:40/15:30 | ✅ three days, **no shared clock time** |
| 3 | "tell me about Monday" | 10:30 / 11:20 / 14:40 | ✅ nothing heard on Monday or anywhere |
| 4 | "and what about Tuesday" | 12:10 / 13:00 / 13:50 | ✅ nothing from Tuesday, **and nothing repeated from step 3** |

Step 4's second clause is the whole of S-2, and it is the first time in this
defect's life that it has held on a phone.

**S-1a verified live too:** *"Number 2, Tuesday **the 15th**"*, *"Number 3,
Wednesday **the 16th**"* — day one carries the month, the rest inherit it.

### The synthesis calibration is confirmed to within 0.5%

First chunk synthesised 13:43:33.046; terminal chunk `tts_finished` 13:43:50.897.
**Live readout = 17.85 s against the 17.93 s predicted in §4.0.** The
18.2 chars/sec calibration is sound, which means §4.4's numbers can be trusted
as a basis for the owner decision (§4.6) — including **12.76 s** for
`speak_part_of_day: false`.

It also confirms S-1's premise the hard way: the caller barged in on four
consecutive turns (13:43:51, 13:44:05, 13:44:18, 13:44:32), each time within a
second of the readout ending.

### Steps 5 and 6 FAIL — a new defect, S-13

| you said | she offered |
|---|---|
| *"do you have anything around midday on tuesday"* | 08:00, 09:40, 15:30 |
| *"what's the closest slot to midday do you have on tuesday **then**"* | 10:30, 11:20, 14:40 |

12:10 and 13:00 were bookable throughout — she had read them out sixteen
seconds earlier. The caller asked twice, explicitly, was answered with neither,
and hung up. **Step 6 was never reached, so the readout-vs-diary agreement is
still unproven.**

See **S-13** in §2.1 for the anchor. It is not an S-2 regression, but S-2 is
what stopped luck from hiding it — see §4.5.

### What this call did NOT exercise

* **S-3.** Neither slow turn reached the ladder's first rung. Both had a
  situational head, and `_hold_delay_s` is `HOLD_HEAD_DELAY_MS` (600 ms)
  whenever one exists — `LLM_FIRST_CHUNK_TIMEOUT_MS` governs only turns with
  **no** situational head. Turn 1 `llm_ttft=4833 ms` and turn 2 `3406 ms` both
  cleared 2750 ms and still never touched it. **S-3 remains uncalled.** §8 says
  how to force one.
* **S-8.** Only ONE offer row was written for the whole call, and it was
  `multi_day`. The four named-day readouts produced none — which is **S-14**,
  new, below.

### S-9 IS verified live — the first field to survive a call

```
seq=1 path=llm            tool_calls=0
seq=2 path=llm            tool_calls=1     <- check_availability
seq=3 path=slot_followup  tool_calls=0
seq=4 path=slot_followup  tool_calls=0
seq=5 path=slot_followup  tool_calls=0
seq=6 path=slot_followup  tool_calls=0
```

Present on every turn, 0 where no tool ran, 1 on the one that did. The
tool-vs-plain split is now real data rather than a proxy — with n=6.

### Two more readings worth keeping

* **S-4 fired on every single turn**: `last_bot_prompt truncated at 200 chars
  and lost its '?'`, four times. Unchanged, and it will stay so until the
  readout shortens (§4.6).
* **`endpoint_wait_ms` 182–1401 ms** on this call, so S-12's correction applies
  here too: turn 3's real silence was 1397 + 131 = 1.53 s, and turn 1's was
  1198 + 873 = 2.07 s.


---

## 2. The register — every open item, with anchors

A row without `file:line` is a lead, not a finding. All of these have one.

### 2.1 Caller-audible

| id | what a caller experiences | anchor | state |
|---|---|---|---|
| **S-13** | **NEW, P1, and the only open caller-audible defect.** A caller who names a TIME on a day already on the table is answered without it. "Anything around midday on Tuesday" → 08:00 / 09:40 / 15:30; asked again, 10:30 / 11:20 / 14:40. 12:10 and 13:00 were bookable and had been read out 16 s earlier. | `slot_followup.py:3687` reads `REQUESTED_TIMES_KEY`; its only three writers are `receptionist_tools.py:6658`, `:7319`, `:7642` — all inside `check_availability` | CAb8ac636017de7d35370fd7951c54d3cf, 13:44:22 and 13:44:36. **D8's pin is structurally dead on every payload-answered turn**, which is the path `speak_one_day_from_payload` takes (`no tool call needed (D-B)`). §4.4. |
| ~~**S-3**~~ | The filler's first rung was AUDIBLE at ~3.12 s, past the 3 s bar. | `config.py` `LLM_FIRST_CHUNK_TIMEOUT_MS` | **FIXED `dd15f2d7`**, 3000 → 2750. §4.1. Not call-verified. |
| ~~**S-2**~~ | "Twenty to ten" said for Monday and again for Tuesday, 17 s apart. | `slot_followup.py` `_prefer_unheard_clock_times` | **FIXED `afabb549`**. Corpus: heard-day repeats **29 → 1**. Not call-verified. |
| **S-11** | **NEW, and it is the larger half of S-3.** The ladder cancels on the first LLM **token**, but dead air ends at the first **audio**. Everything between — `chunk_gate`, p50 1.46 s / p95 2.95 s on the breaching turns — is unguarded, and **190 of 302 corpus breaches (63%) live there**. | `llm_stream.py:5604` `got_first_chunk = True` cancels `_filler_task` | measured; **no deadline fixes it** and the obvious fix is a bad trade at every value. §4.1. |
| **S-12** | **NEW.** On the caller's real clock (`endpoint_wait_ms` + `ttfa_ms`) **47.1% of turns exceed the 3 s bar**, p50 2.83 s, p95 5.04 s — not the 18.8% `ttfa` alone shows, because `t0` starts AFTER the endpointer. | `latency_timing.py`, `endpoint_wait_ms` is documented "pre-t0 dead-time" | 1,579 turns. No ladder tuning reaches it; it is the general latency work. |
| **S-1** | A three-day readout takes **17.93 s**. Callers barge in on nearly every turn. | `slot_offer.py:50-52` caps; `build_slot_offer` `:359` | **owner decision, unchanged** — see §4.6. Two minutes of work whenever it is taken. |
| **S-4** | `last_bot_prompt` blows its 200-char cap and loses its "?" 3–4 times per call, disarming clinical screening's orphan matcher. | `clinical_screening.py:530` `_LAST_BOT_PROMPT_CAP` | open. Improves for free as the readout shortens. **Do not touch the cap** — 34 writers, marked RED. |

### 2.2 Structural — no caller sees these today, they are how the next one gets in

| id | finding | anchor |
|---|---|---|
| ~~**S-5**~~ | Five producers, four honouring the rule, nothing enforcing it. | **FIXED `ab5b6752`** — `pretrimmed` parameter + runtime warning + AST census. The guard found a real fifth site on its first run. §4.3. |
| ~~**S-6**~~ | `_record_stood_down_slots` returned silently when it resolved nothing. | **FIXED `1508df0c`** — nothing-parsed is a WARNING, already-held is INFO. **Stage C's gate re-worded to match** (`ONE_PRESENTATION_LAYER.md`): zero of BOTH reverse-parse lines, read as a pair. Not yet measurable — no call since. |
| **S-7** | **13 % of recorded offers were never spoken.** `record_offer` fires where the offer is BUILT, above the P6/P6b stand-downs. **No code needed** — it is a fact every future harness author must know. | `llm_stream.py:7353` |
| **S-14** | **NEW.** `record_offer` has ONE call site (`llm_stream.py:7394`, Gate 5). The three `slot_followup` producers write session state via `apply_offer_to_session` but **no obs row**. On the 13:43 call, **4 of 5 readouts left no trace in `calls.slot_offers`** — and they were the four that exposed S-13. The offer corpus systematically under-represents the payload-answered path. | `llm_stream.py:7394` is the only writer; `slot_followup.py:5276`, `:5365`, and `speak_one_day_from_payload` record nothing |
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

### 4.4 · S-13 — the requested-time pin is dead on the payload path **← do this first**

**Found by the 10 Sep 13:43 call. P1, caller-audible, and it ended the call.**

D8 exists to force a time the caller ASKED FOR back into a readout B-116 has
dropped. It reads one session key:

```python
wanted = session.get(REQUESTED_TIMES_KEY)      # slot_followup.py:3687
```

That key has **exactly three writers, and all three are inside
`check_availability`** — `receptionist_tools.py:6658`, `:7319`, `:7642`. Its own
docstring says so:

> *"Set once per availability lookup by the three `check_availability` entry
> points, from that lookup's own `date_hint`."*

A named-day follow-up does not run a tool. The live log says it in as many
words:

```
[slot_followup] 'Tuesday 15th September' answered from the payload
                -- 3 of 11 bookable times spoken, ... no tool call needed (D-B)
```

So on that path the key is never written from the caller's own words, and D8
pins nothing. **Two failure modes, not one:**

* **Dead** — the last lookup named no time, so the key is empty and the pin is
  a no-op. This is what happened: the lookup was `date_hint="next week"`.
* **Stale** — if the last lookup DID name a time, the pin fires with a time
  from a question the caller has moved on from. The docstring's "written on
  EVERY lookup, empty included" defends against staleness *between lookups* and
  cannot defend against a turn that performs none.

**The parser is not the problem, and do not go near it.**
`requested_clock_times("around midday")` correctly returns `12:00`
(`slot_followup.py:3498`, `\b(?:midday|noon)\b`). It is simply never called on
this path.

**The fix, smallest form:** write `REQUESTED_TIMES_KEY` from the caller's
utterance on the payload-answered path, immediately before
`choose_presented_indices` is called, using the same `requested_clock_times`
the tool path uses. One writer added, no new rule, and D8's own decline logic
(two readings decline, `nearest_time_index` tolerance) is unchanged.

**Watch three things:**

1. **Write it on EVERY payload turn, empty included.** Partial writing
   re-creates the staleness the docstring guards against, one layer down.
2. **`_pin_requested_time_index` runs INSIDE `_pin_accepted_index`** — an
   accepted slot outranks a time merely asked about. Do not reorder.
3. **B-116's pool has already removed the heard time.** On the exhibit, 12:10
   and 13:00 had been spoken one turn earlier, so they are *correctly* outside
   the pool — the pin's whole job is to reach past that. Verify the pin
   displaces rather than filters.

**Gate:** the exhibit reproduced offline as a failing test first; then
replay by direction; then failing-set diff; then **the call script all the way
to step 6**, which no call has yet reached.

---

### 4.5 · S-2 did not cause S-13, but it removed the luck

Stated plainly because the next reader will suspect it, and they should.

On the exhibit, S-2 changed Tuesday's step-4 readout from
`['08:00','09:40','15:30']` to `['12:10','13:00','13:50']`. That spent the
midday slots one turn before the caller asked for midday. Without S-2, step 4
would have read the earlier times, midday would still have been in B-116's
unheard pool at step 5, and `_spread` over
`10:30 11:20 12:10 13:00 13:50 14:40` would probably have surfaced one of them.

**The caller would have got midday by accident.** D8 would still have been dead;
nothing would have been logged; and the register would still say step 5 passes.

Two conclusions, and the second matters more:

* **This is not a reason to revert S-2.** Steps 2, 3 and 4 pass for the first
  time and the corpus effect is 29 → 1. A rule that works by coincidence on one
  wording is not a rule.
* **A passing call step is not a working mechanism.** Step 5 has been in the
  script for days and had never distinguished "D8 fired" from "B-116 happened
  to leave midday in the pool". **When a step passes, check that the thing it
  names actually ran** — the D8 log line, `pinned the requested time back into
  the readout`, has never appeared in any stored call.

### 4.6 · S-1 remainder — an owner decision, takeable at any time

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

### 4.7 · Phase 2 — weekend buffer, or next week

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

### New, from the 10 Sep 13:43 call

**A CALL STEP THAT PASSES WITHOUT ITS MECHANISM FIRING IS NOT A PASS.** Step 5
of §5 has been in the script for days and had never once distinguished "D8
pinned the requested time" from "B-116 happened to leave midday in the pool".
The log line D8 emits when it fires — `pinned the requested time back into the
readout` — **has never appeared in any stored call.** When a step passes, grep
for the thing it is supposed to be testing. §4.5.

**A SESSION KEY IS ONLY AS LIVE AS ITS WRITERS.** `REQUESTED_TIMES_KEY` has
three writers and all three are inside `check_availability`. Every path that
answers WITHOUT a tool call — and `speak_one_day_from_payload` is the commonest
one on a booking call — reads a key nothing on that turn wrote. Before trusting
any `session[...]` guard, enumerate its writers and ask which turn types reach
none of them. This is the fourth defect in this family
([[config-keys-that-never-reach-the-model]] is the same shape one layer up).

**THE OBS CORPUS IS NOT THE SYSTEM.** `record_offer` has ONE call site, in
Gate 5. Four of the five readouts on this call went through `slot_followup`
producers and left no row at all. Any statement of the form "N of M offers in
the corpus..." is a statement about the Gate 5 path. S-14.

**A FIX CAN REMOVE A COINCIDENCE THAT WAS DOING REAL WORK.** S-2 is correct and
its corpus effect is 29 → 1, and it also spent the midday slots one turn before
the caller asked for midday — which is what turned a dormant D8 into a hung-up
call. Not a reason to revert it. It IS a reason to expect the first call after
any selection change to surface something, and to read that call for what it
uncovered rather than for what it broke.

---

## 8. What happens next, in dependency order

Ordered by what BLOCKS what, not by size. Each step names the thing that must
be true before the next one starts.

### Shipped, on `latency-eval` (`9259595f`), demo line only

| | item | commit | offline gate | on a phone |
|---|---|---|---|---|
| ✅ | **S-1a** month said once | `40a67ea8` | 18.59 → 17.93 s in synthesis | **VERIFIED** — 17.85 s live, and the wording is right |
| ✅ | **S-3** rung audible outside the bar | `dd15f2d7` | failing-set EMPTY; fails-before/passes-after | **NOT EXERCISED** — see step 3 |
| ✅ | **S-2** cross-day preference | `afabb549` | heard-day repeats 29 → 1; decisions CHANGED 0 | **VERIFIED** — script steps 2, 3, 4 |
| ✅ | **S-5** trim contract | `ab5b6752` | census both directions; found a real 5th producer | n/a — structural |
| ✅ | **S-6** stand-down logging | `1508df0c` | 9 tests | not exercised — no stand-down occurred |
| ✅ | **S-8** presented on single_day | `1508df0c` | 9 tests | **NOT EXERCISED** — blocked by S-14, step 5 |
| ✅ | **S-9** tool marker | `1508df0c` | 9 tests | **VERIFIED** — 0/1/0/0/0/0 across six turns |

---

### 1. Fix S-13 — the requested-time pin. **BLOCKS EVERYTHING BELOW.**

The only open caller-audible defect, it is P1, and it is the reason the last
call ended. §4.4 has the anchor, the two failure modes, the smallest fix and
the three things to watch.

**Why it blocks:** it is on the same code path S-2 just changed, so it must be
fixed and the whole script re-run *together* — not layered on a promotion that
is already half-verified.

**Exit:** the exhibit reproduced as a failing test first, then replay by
direction, then failing-set diff EMPTY.

### 2. Re-run the call script, all the way to step 6

No call has yet reached step 6, so **the readout and the diary have never been
checked against each other on this build.** That is the only step that proves a
booking matches what the caller was told, and it is CLAUDE.md §6's first bar.

**Exit:** steps 1–6 pass, AND the log shows `pinned the requested time back
into the readout` — a step that passes without its mechanism firing is what
§4.5 is about.

### 3. Force ONE turn with no situational head, to exercise S-3

`_hold_delay_s` is `HOLD_HEAD_DELAY_MS` (600 ms) whenever a situational head
exists, so `LLM_FIRST_CHUNK_TIMEOUT_MS` governs only the turns that get none.
Turn 1 (`llm_ttft=4833 ms`) and turn 2 (`3406 ms`) both cleared 2750 ms and
still never touched it.

Ask something the head arbiter has no subject for — a general question with no
day, no service and no body part ("what should I know before I come in?").
Then read the `[LAT]` line: `ttfa_ms` should be ~2870 ms, not ~3120 ms.

**Exit:** one `[LAT]` line with a filler-covered `ttfa` under 3000 ms.
**If it cannot be forced in two attempts, say so and promote anyway** — the
change is a constant with a unit test either side of it, and blocking three
verified fixes on it is the wrong trade.

### 4. Promote to `production`

Only after 1–3. §6 has the discipline; the grep for `SMS_ENABLED` /
`APPOINTMENT_REMINDERS_ENABLED` is not optional.

```
revert target   337fbd9e     write it down before you push
```

**Then call a live line.** Vital Edge or JV — **neither has been called since
T1b**, and both are non-grid diaries where northgate's uniform 50-minute ladder
cannot flatter the result.

### 5. S-14 — make the payload-answered readouts visible in the corpus

`record_offer` has ONE call site. On the 13:43 call **4 of 5 readouts left no
obs row**, and they were the four that exposed S-13. Every harness reading
`calls.slot_offers` is therefore measuring the Gate 5 path and calling it the
system.

Not caller-audible, so it sits below the promotion — but it is above Phase 2,
because Phase 2's gates are read from this corpus. **Fixing S-14 also unblocks
the live confirmation of S-8**, which cannot be observed until the single_day
producers record anything.

### 6. S-1 remainder — the owner decision (§4.6)

Two minutes of work, takeable at any time, blocked on nobody but the owner.
`speak_part_of_day: false` on northgate only: **17.93 s → 12.76 s**, verified
resolution-identical on 7/7 utterances, and the patient still gets am/pm in the
SMS. §4.6 has what is genuinely lost and the S-10 half-wiring caveat.

Worth noting now that the calibration is confirmed live to 0.5%: **the rendered
northgate prompt already asks for "under about eight seconds".**

### 7. Phase 2 — one record, then delete the guards

`SLOT_PRESENTATION_CONVERGENCE.md`, in its own order. Then step 5 of the 31 Aug
document. ≥ 2 days, and it needs S-14 first.

---

### Open, deliberately not queued

| id | why it is not in the list |
|---|---|
| **S-11** | The ladder watches the token, not the audio — 63% of breaches. Costed at every deadline and a bad trade at all of them; the predicate that would work cannot be measured from the stored corpus. **Do not touch without new evidence.** §4.1 |
| **S-12** | 47.1% of turns breach the 3 s bar on the caller's clock. Real, and it is the general latency work — `llm_ttft` p50 1.63 s and `chunk_gate` p50 0.67 s — not a slot-presentation item. §4.1 |
| **S-4** | `last_bot_prompt` truncation, fired on all four turns of the last call. Improves for free when the readout shortens. **Do not touch the cap** — 34 writers, RED. |
| **S-10** | `speak_part_of_day` is half-wired: the flag changes the labels, the prompt still instructs the band in three places. Decide it WITH item 6, not separately. |
| **B-146** | *"Sorry, still with you —"* on a caller's first sentence. **Still has no explicit decision recorded against it.** It was excluded from this session's diff, which was right, but that is the scope rule deciding by default — and it is a first-sentence defect on exactly the call being rehearsed for a partner. |

---

**If you have time for exactly one thing: step 1, then step 2.** Three fixes are
verified on a phone and one defect is open on the same code path. Promoting
without S-13 ships a call that ends the way the last one did.

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
