# Finishing slot presentation — plan of record, rev. 7 (2026-09-10, night)

**Goal (owner, 10 Sep):** slot presentation is *finished* by the end of this week.
**Time available:** Friday 11th, weekend buffer 12–13th.

> ## rev. 7 — read this before anything below it
>
> Rev. 6 said slot presentation was DONE. **It is not.** Reading the new
> `slot_offers` corpus back — the thing rev. 6 shipped and never read — turned
> up a defect in the readout itself, and it is systematic rather than a slip.
>
> **Three corrections to rev. 6, in descending order of how much they change:**
>
> 1. 🔴 **N1 is a real, measured, systematic slot-presentation defect (§1.8).**
>    When a caller asks about a day they were *just offered*, the re-readout
>    withholds **every time they were offered** and reads three different ones.
>    **6 of 8** such re-readouts in the corpus; the 2 exceptions are jv_v1 days
>    holding only two slots, where the rule had nothing else to reach for.
>    Nothing is invented — every time spoken is real and bookable — so the
>    judge's `wrong_info` tag on CA12036a45 is **wrong**, and reading it as a
>    hallucination sends the next reader to the wrong file. §1.8 has the
>    payloads and the one-line cause.
>
> 2. ⚠️ **"B-149" was already taken.** `test_b149_b150_a_refused_day_after_
>    narrowing.py` (6 Sep) is the promised-work defect — a head that promised a
>    Tuesday lookup before Susie talked about Monday. Rev. 6 gave the same
>    number to the dead-air defect. Two live defects under one id is how a fix
>    gets reported closed against the wrong exhibit. **The dead-air defect is
>    B-151 from here** (B-150 was the highest in use); §1.7 keeps its timeline.
>
> 3. ✅ **B-151 is FIXED, not call-verified.** `_rearm_no_input_watchdog` from
>    `on_llm_finished()`, gated on `session["tts_inhibit"]`. 45 lines, one file,
>    ten regression tests, all three harnesses clean against a same-corpus
>    baseline. §1.7 carries the reasoning and the two measurements that changed
>    the shape of it.
>
> **Also closed since rev. 6, neither call-verified:** **N2** `dfc8b914` (a
> caller asked for midday three times and was read the three times furthest
> from it) and **N3** `ba3374ff` ("say that again" was dropped as a meaningless
> fragment and the caller waited 19 s). Both pushed to `latency-eval`.
>
> ```
> origin/latency-eval  ba3374ff   N3       ← + B-151 local, unpushed
>                      dfc8b914   N2
>                      818793c3   rev. 6
> origin/production    2658f727   ← 3 behind, clean fast-forward, nothing ahead
> ```
>
> **Filed, not fixed:** `day_by_position` is `RAISED:TypeError` on all 2082
> turns in every tree — `replay_slot_decisions.py:303` calls
> `day_selected_by_position(slots, utterance)` against a signature of
> `(available_days, session, text)`, and `safe()` swallows it into a string, so
> the gate reads `CHANGED: 0` while one of its five dimensions has never
> produced a value. B-105's rung is scored by nothing.

**Author's note (rev. 6, kept):** rev. 6 was a HANDOVER. Read §1.7 first if you
are picking this up cold — a caller heard 13.7 seconds of silence and it is the
only thing on this page that a patient can hear. Rev. 7 adds §1.8, which is the
only thing on this page a patient can *mis*hear.

> ### STATUS — four calls, 10 Sep. Slot presentation is DONE. One new P1.
>
> **`latency-eval` and `production` are both at `2658f727`.**
>
> ```
> revert ladder   2658f727  <- HEAD, pacing
>                 ebc99d76     S-7 + Stage C instrumentation
>                 aa4c324c     S-14
>                 8ab39703     S-13
>                 337fbd9e     before any of it
> ```
>
> **`ELEVENLABS_SLOT_SPEED=0.92` is SET on the demo service** and the owner
> confirmed the pace is right. It is NOT set on the three clinic services; the
> code default is 1.0, so they are unchanged until someone sets it. §4.9.
>
> ~~🔴 **B-149 is open**~~ — **superseded by rev. 7: renumbered B-151, and FIXED
> (not call-verified).** Left below as rev. 6 wrote it. 13.7 s of
> dead air on tonight's 20:57 call; the caller said "hello" because they thought
> the line had dropped. **It is not new and not from today's work** — the
> mechanism is described verbatim in a code comment dated 20 Aug. §1.7 has the
> timeline, the anchor and why the obvious fix is the wrong one.
>
> | | |
> |---|---|
> | **VERIFIED ON A PHONE** | S-1a, S-2, S-9 (13:43) · **S-13** (14:42, the D8 line fired for the first time in its life) · **S-14, S-8** (19:36, four readouts → four obs rows, `presented` populated on single_day) · **script step 6 twice** |
> | **SHIPPED, NOT EXERCISED** | S-3 — two attempts, see §8. S-6 — no stand-down has occurred. |
> | **CLOSED** | S-13 (`8ab39703`), S-14 (`aa4c324c`) |
> | **NEW, not queued** | S-11 has its first live instance on this build: turn 7 of the 19:36 call, **ttfa 3144 ms, over the bar, headless**. It confirms §4.1's costing rather than contradicting it. |
>
> **The remaining gap is the diary shape, not the code.** Every call that
> verified any of this was northgate, a uniform 50-minute grid. **Vital Edge
> and JV have not been called since T1b** and both are non-grid. §8 step 1.
>
> **That call was being placed as this revision was written.** Its log went to a
> different session. If you are that session: score it against §5's script,
> then read §1.7 before filing anything about silence.
>
> **Confirm `[build_info] running build <sha>` before trusting any call** —
> `/health` returns a hardcoded 1.0.0 and always has.

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

### Scored against the bar, 10 Sep night

| | | |
|---|---|---|
| 1 | No known defect a caller can hear | ✅ **S-13 closed and verified on a phone.** S-2, S-3 and the T1/T1b family closed. S-1 and S-4 have owner decisions recorded. Nothing caller-audible is open. |
| 2 | The defect class cannot recur silently | ✅ for the trim contract (S-5) and now for the record (S-14: an AST census fails producer number six by name). ⚠️ **still not true of D8's DECLINE** — the pin logs when it fires, never when it stands down, and §7 has a call where that read as a failure and was not one. Same for S-2. |
| 3 | Measured, not asserted | ✅ three harnesses, all gates 0, on every commit. ✅ the offer corpus now records all five producers, not just Gate 5 — **but it starts on 10 Sep and cannot be back-filled.** |
| 4 | A defined call script sounds right, twice, on two diaries | ⚠️ **twice, on ONE diary.** Steps 1–6 passed on 14:42 and 19:36, both northgate. **The second diary is the whole of what is left** — §8 step 1. |

**Three and a half of four, and the missing half is a diary shape rather than a
defect.** The readout is correct everywhere it has been measured, the
measurement now reaches the booking, and every measurement so far is on a
uniform 50-minute grid.

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

## 1.6 The two calls that closed it — 10 Sep, 14:42 and 19:36

### CA5003203cd7ee8d5e743a1d63932c37b7, 14:41, build `8ab39703cd26`

**162 s, `outcome=booked`, judge 3.** The first call in this defect's life to
reach script step 6.

```
14:42:39  [slot_followup] pinned the requested time back into the readout --
          the caller asked for 12:10 and B-116 had dropped it (D8). [3, 4, 8] -> [3, 4, 5]
```

That line had never appeared in any stored call. `[3, 4, 8] -> [3, 4, 5]` is
the pin **displacing, not filtering** — three times still spoken, 12:10 among
them.

**Step 6, the first readout-vs-diary check ever performed on this system:**

| stage | value |
|---|---|
| spoken | *"so that's Quentin Rock, Tuesday the 15th of September at ten past twelve"* |
| `book_appointment` | `slot_iso: 2026-09-15T12:10:00` |
| diary | `start 2026-09-15T12:10:00+01:00` |

**IT FIRED THROUGH B-145, NOT D-B.** §4.4 anchored the diagnosis on the D-B log
line, because that is the line the 13:43 exhibit produced. The wording *"do you
have anything around midday on tuesday"* resolved as a **day acceptance**, so
`day_accepted_by_caller` claimed it and the readout came from the B-145
producer. Both call sites were given `user_text` in `8ab39703`; had only the
one named in §4.4 been patched, this call would have failed identically and the
fix would have looked wrong. **Two producers share
`speak_one_day_from_payload`, and which one a sentence reaches is decided by
wording the caller chooses.**

### CA5f45b7aa0d8720f3fa22c9c58b81f0f4, 19:35, build `aa4c324c7462`

**172 s, `outcome=booked`, judge 3.** Step 6 again, on 11:20 this time, agreeing
at every link — readout, ordinal pick (*"the second slot works"*), readback,
tool arg, diary.

**S-14 verified, and S-8 with it.** The corpus row for this call:

```
seq=0  multi_day   payload_days=6  presented_days=3  [['08:00','17:10'], ['08:50','16:20'], ['09:40','15:30']]
seq=1  single_day  payload_days=6  presented_days=1  [['10:30','11:20','14:40']]
seq=2  single_day  payload_days=6  presented_days=1  [['08:00','09:40','15:30']]
seq=3  single_day  payload_days=6  presented_days=1  [['10:30','11:20','14:40']]
```

Four readouts, four rows. The 13:43 call wrote **one row for five readouts**.
And `presented` is populated on every `single_day` row — the field that was
empty on all 24 single_day rows in the corpus, which is S-8 confirmed on a
phone for the first time.

### What this call did NOT prove, and why that is not a defect

**Step 5 was untestable, and we caused it.** *"anything around midday on a
tuesday"* was answered 10:30 / 11:20 / 14:40 with no D8 line. The pin
**declined correctly**: `nearest_time_index` has a 20-minute tolerance, and
**12:10 had been booked by the 14:42 call**, leaving 11:20 (40 min out) and
13:00 (60 min out) as the nearest bookable times to noon.

The same booking made **S-2 stand down twice** in the same call, which is why
Tuesday read back Monday's three times at steps 4 and 5. See §7.

**One test booking disabled two of the three things the script exists to
test.** Cancel each test booking through Susie — never the calendar — before
the next run.

### S-3: the opener works, the model was 208 ms too fast

Turn 1 of the 19:36 call was genuinely headless — no `situational head`, no
`filler phrase triggered`. *"What should I know before I come in?"* defeats the
head arbiter reliably, which was the hard part. But `llm_ttft=2542 ms` against
a 2750 ms deadline, so the token cancelled the rung before it could fire.

**Two attempts, both spent. §8's rule applies: say so and stop.**

### Turn 7 is S-11, live on this build

```
turn_seq=7  ttfa_ms=3144  llm_ttft_ms=2348  chunk_gate_ms=667
```

Headless, no filler, and **3.144 s of dead air — over §0's bar.** The rung
could not help: the token arrived at 2348 ms and cancelled it 400 ms before its
deadline, then `chunk_gate` added 667 ms before any audio existed.

This is §4.1's warning reproduced exactly — *"the rung cancels on the first
TOKEN, and the wait from the token to the first CHUNK is unguarded."* **Do not
read this as S-3 underdelivering.** No value of `LLM_FIRST_CHUNK_TIMEOUT_MS`
reaches this turn, which is precisely why S-11 is filed and not queued.

---

## 1.7 B-151 — a turn that speaks nothing leaves no safety net

> **rev. 7: renumbered from B-149 (collision, see the header) and FIXED — not
> call-verified.** The diagnosis below stood up; two things measured while
> building it changed the shape of the repair, and both are recorded at the end
> of this section.

**CAc9a7976f516be8d816c367500a0fd130, 10 Sep 20:57, northgate, build
`2658f7272216`. 104 s, `outcome=abandoned`, judge 2. P1, caller-audible.**

Reported as *"about 8 seconds of silence, which is the watchdog firing"*. It was
**13.7 seconds**, and the watchdog was not firing — **it was never armed.**

```
20:58:15.024   +0.00s  'Apologies for that -' ENDS. LAST AUDIO.
20:58:15.270   +0.25s  barge-in partial "nothing's there"  -> tts_inhibit set
20:58:16.571   +1.55s  FINAL 'nothing serious though'      -> queued, not yet dequeued
20:58:19.769   +4.74s  tts_inhibit: DISCARDING 'ankles can be tricky...'
20:58:19.794   +4.77s  B-76: every chunk dropped before TTS
20:58:19.855   +4.83s  incomplete utterance HELD (mid-clause)
20:58:22.357   +7.33s  LAT turn_seq=2  outcome=no_content   content_ttfa=-1
20:58:24.569   +9.54s  barge-in 'have'                     -> tts_inhibit set AGAIN
20:58:25.569  +10.54s  FINAL 'hello'   <- the caller thinks the line has dropped
20:58:25.938  +10.91s  tts_inhibit: DISCARDING 'Ankles can be a bit unpredictable...'
20:58:25.979  +10.96s  LAT turn_seq=3  outcome=superseded
20:58:28.741  +13.72s  'Sorry, still with you -'  FIRST AUDIO SINCE 20:58:15
```

**The loop is self-sustaining, and the caller's own rescue attempt is what kills
the next reply.** Barge-in sets `tts_inhibit`; the reply arrives and is
discarded; the silence makes the caller speak; that speech is a new barge-in;
the next reply is discarded. `'hello'` at +10.54s is what killed turn 2's answer.

**Why nothing broke the loop.** `WATCHDOG_DEFERRED_CLEAR reason=tts_still_playing`
(`connection.py:4855`) hands arming to `on_tts_finished()`. When every chunk is
inhibited that callback never runs, `_restart_timer()` is never called, and no
watchdog is armed. **Measured on this call: unarmed for 24.4 s**, from the
`WATCHDOG_CANCEL` at 20:58:12.370 to the next `WATCHDOG_START` at 20:58:36.773.
The safety net is armed BY SPEECH, so it is missing exactly on the turns that
produced none.

**The codebase already knows.** `connection.py:17206`, in B-67's own comment:

> *"arming was handed to `on_tts_finished()` by `WATCHDOG_DEFERRED_CLEAR` —
> which never fires, because every chunk of that turn was inhibited."*

and the exhibit beside it is CAa0f76e2c (Vital Edge, 20 Aug) where the caller
also said **"hello"** into the same silence.

**So this is B-67 recurring through a different door.** B-67 closed the door
where the final is GARBAGE and gets dropped at the socket boundary, and its
repair hangs on `_is_garbage_fc`. Tonight's final was real — `'nothing serious
though'` — and WAS enqueued. It simply arrived while the loop was mid-turn, so
`_resolve_barge_in()` did not run for another 3.3 s and the in-flight reply died
in that window. The garbage gate cannot see this case.

**NOT CAUSED BY TODAY'S WORK.** Nothing shipped on 10 Sep touches barge-in,
`tts_inhibit` or the watchdog; the pacing commit adds one `speed` kwarg. The
mechanism is documented from 20 Aug. Do not bisect today's commits for it.

**The fix direction, and why it was NOT taken tonight.** A turn that ends having
spoken nothing must arm the watchdog itself; `_rearm_no_input_watchdog`
(`connection.py:6102`) already exists for the "cancelled speculatively, put it
back on its ORIGINAL deadline" case and is the right tool. It was deliberately
not attempted at 21:00 on a night with live clinic calls pending:

* it is the barge-in/watchdog seam in a 12,000-line file, where the record says
  the obvious fix is the wrong one — teardown is on the PARTIAL, resolution is
  on the FINAL, and a change on the wrong side of that seam makes silence worse;
* it needs a fails-before test built from the timeline above, which is an hour
  of careful work rather than fifteen minutes; and
* the clinic calls test the DIARY SHAPE, a different axis entirely, and are
  unaffected by it.

**If it bites during a call:** speak again. The turn after next lands.

### rev. 7 — the fix, and the two measurements that changed its shape

`on_llm_finished()`, after the two existing deferred branches. 45 lines, one
file, ten regression tests in
`tests/regression/test_b151_a_turn_that_speaks_nothing_still_arms_the_watchdog.py`.

```python
if not self._cancelled and self._watchdog_armed_at is not None:
    _sess_b151 = self._get_session() if self._get_session else None
    if (_sess_b151 or {}).get("tts_inhibit"):
        self._rearm_no_input_watchdog(self._watchdog_armed_at, self._watchdog_q_gen)
```

**Reproduced before it was fixed**, against the real `SilenceHandler`, not by
inspection: arm a question, `on_speech_started(stt_source=True)` during TTS,
then an LLM turn that never calls `on_tts_started`. Three tests fail, seven
guards already pass. Rev. 6 was right about the seam and right about the tool.

**Two things measured while building it, both of which would have made a
plausible fix wrong:**

* **`_watchdog_q_gen` initialises to `-1`, which is truthy; `_watchdog_armed_at`
  initialises to `None`.** The obvious guard — "we armed one once, so re-arm it"
  — reads naturally as `if self._watchdog_q_gen:` and passes `None` into
  `max(armed_at, …)` on the first turn of every call. Gate on `_watchdog_armed_at`.

* **`on_llm_started()` calls `_cancel_timer()`, which kills the watchdog on
  EVERY turn**, not only barged-in ones. So at `on_llm_finished()` there is
  never a live watchdog to preserve, and an added `and not <watchdog live>`
  clause would look careful and be a no-op. The whole weight of the gate falls
  on `tts_inhibit`, which is why that flag — and nothing wider — is the
  discriminator. `test_the_llm_turn_itself_is_what_leaves_the_call_unguarded`
  pins this down so the next reader does not re-derive it.

**The partial-inhibit case is safe by construction, and this is worth knowing
before anyone "hardens" it.** A turn can play some chunks and have the rest
discarded; `tts_inhibit` is still set at `on_llm_finished`, so this arms — and
`on_tts_finished` for the chunks that DID play sets `_watchdog_grace_until`.
`_no_input_watchdog` recomputes `max(armed_at, last_engagement_at,
_watchdog_grace_until)` **on every loop iteration**, not once at start, so the
later anchor simply wins and the window is never shorter than the audio
warrants. Arming early cannot make Susie speak early.

**Why not `_restart_timer()`.** It also arms on this state — measured, both
candidates were run against the reproduction. It was not chosen because it
cancels and recreates the W1/W2/W3 task, resets `currently_reasking` and re-runs
every Spec Z gate; `connection.py` is 18k lines and frozen. The cancel being
undone here is *speculative* (the teardown is on the PARTIAL), which is verbatim
the case `_rearm_no_input_watchdog`'s docstring was written for, and it is the
third defect of that family after B-67 and the DTMF one.

**Verification.** 10/10 on the new file. The seven pre-existing failures in
`test_silence_handler.py` / `test_dead_air_safety_net.py` are **identical on the
baseline tree** — checked, not assumed, including
`test_no_fire_while_tts_playing`, which is the one that would have looked like
this change. `replay_slot_decisions` `CHANGED: 0` at **1889 = 1889 turns** (same
corpus — the header, not the rows, per N3's note); `replay_presented_times`
0 changed / 0 lost / 0 invented / 0 re-offered over 526 = 526 days;
`replay_multi_day_spread` byte-identical.

**On the call, this is what to listen for:** talk over Susie mid-reply. The log
must show `WATCHDOG_REARM_SILENT_TURN reason=tts_inhibit` followed by a
`WATCHDOG_START`, and there must be no hole. The fix cannot make her speak the
*wrong* thing — it only decides whether anything is armed — so the risk on the
call is a re-ask arriving too soon, which
`test_the_caller_is_not_re_asked_the_instant_they_stop_talking` is the offline
guard against.

---

## 1.8 N1 — asking about a day withdraws the times you were offered for it

**P1, caller-audible, systematic, and it is slot presentation.** Found by
reading the `slot_offers` corpus that rev. 6 shipped; it is the first thing that
corpus has been asked and the first answer it gave was a defect.

**CA12036a4529eaf8e46919432a9ebc1a6a, 10 Sep 21:57, northgate, build
`dfc8b91454b8`.** 100 s, `outcome=abandoned`, judge 2.

```
Susie : I've got a few days — Number 1, Monday 14th September — eight in the
        morning, or ten past five in the evening.
        Number 2, Tuesday the 15th — ten to nine, or twenty past four.
        Number 3, Wednesday the 16th — twenty to ten, or half past three.
caller: um what about monday
Susie : Monday 14th September — Number 1, half past ten in the morning.
        Number 2, twenty past eleven. Number 3, twenty to three.
caller: uh and what about tuesday
Susie : Tuesday 15th September — Number 1, ten past twelve. Number 2, one.
        Number 3, ten to two.
caller: uh yeah as close as possible as 12 please
Susie : Ten past twelve on Tuesday the 15th — could I take your first name…
        [caller drops]
```

**Nothing was invented — this is the first thing to get right.** The stored
payload for every one of those days is the same uniform grid:

```
08:00 08:50 09:40 10:30 11:20 12:10 13:00 13:50 14:40 15:30 16:20 17:10
       spread PRESENTED  Mon 08:00 17:10 · Tue 08:50 16:20 · Wed 09:40 15:30
       single-day        Mon 10:30 11:20 14:40   ← zero overlap with the above
```

Every time spoken is real and bookable. **The judge tagged this call
`wrong_info` + `booking_error`, and the `wrong_info` half is wrong** — read as a
hallucination it sends the next reader to the LLM and the prompt, which is the
one place the defect is not. Same misread as the sweep's "one safety FAIL".

**What the caller experiences.** They are offered Monday *at eight or ten past
five*, they say "what about Monday" — and both of those times are gone,
replaced by three they have never heard, with no explanation. The two times
that made them ask about the day in the first place are the two the readout
guarantees to withhold.

### Measured, with the denominator

Scanned every call carrying `slot_offers` (134 calls). The instrumentation that
makes this measurable — `presented` on `single_day` rows, **S-14 `aa4c324c`** —
starts on 10 Sep and cannot be back-filled, so **5 calls** can be scored at all.

| | |
|---|---|
| re-readouts of a day the caller had already been offered | **8** |
| kept at least one of the offered times | **2** |
| kept **none** of them | **6** |

The 2 that kept them are jv_v1 days holding only `19:15` and `20:00` — the
whole day, so there was nothing else to reach for. **On every day with slots to
spare, the offered times were withheld: 6 of 6.** This is deterministic, not a
model slip, and it will reproduce on the next call that asks about a day.

### The cause — corrected 11 Sep by a live log line, and this changes where the fix goes

**The first reading of this was wrong and is worth keeping, because it is the
expensive kind of wrong: it named a real rule that really does fire here, and
would have sent the fix to the wrong function.**

The call of 23:10 on 11 Sep (CA91d1f12332f6230ed51ad1a427f91f5c, build
`6e556ad0`) reproduced N1 on demand and printed the whole decision:

```
[slot_followup] B-116 had picked ['08:50', '09:40', '16:20'] for 2026-09-14
  -- clock times this caller already heard on another day (T1/S-2).
  Reading ['10:30', '11:20', '14:40'] instead, chosen from 10 candidates;
  heard clocks ['08:00','08:50','09:40','15:30','16:20','17:10']
```

**Read B-116's own pick: `08:50 / 09:40 / 16:20`. `08:00` and `17:10` are
already gone before T1/S-2 is consulted at all.**

* **B-116 (`_choose_presented_indices_b116`, `:4089`) is the owner.** It
  subtracts by **dated ISO start**, so the two times spoken *for Monday* in the
  spread are removed as "heard on this day" — which is exactly the rule it was
  built to enforce, for "what else have you got".
* **T1 / S-2 (`_prefer_unheard_clock_times`, `:3798`) only picks the
  replacements**, swapping `08:50/09:40/16:20` for `10:30/11:20/14:40` because
  the first three were heard on Tuesday and Wednesday.

**So reverting S-2 would not fix N1** — it would hand the caller
`08:50 / 09:40 / 16:20`, still zero overlap with what they were offered. Do not
revert it for this, and do not revert it at all: it fixed cross-day repeats
29 → 1 and the two rules compose.

**The fix belongs on B-116's within-day subtraction, not on the cross-day
wrapper.** The distinction the code cannot currently draw is between a time
withheld because *the caller has heard it and wants something else*, and a time
withheld because *the caller heard it and is asking to hear it again*.

Rev. 6 marked S-2 **VERIFIED** on the 13:43 call; that call never asked about a
day it had been offered, so the step passed without the case existing. §4.5's
rule again — a step that passes without its mechanism firing is not a pass.
**The same rule caught this correction:** the defect was measured from the
corpus and mis-attributed from the source, and one live log line settled it.

### The fix shape already exists in this file

B-142 is the precedent, and its comment states the principle outright: *"'sooner'
and 'what else' are opposite questions"*, so `caller_wants_soonest` makes the
unheard filter **stand down** rather than widening anyone's pool. N1 is the
third question-shape the filter gets wrong:

* **"what else have you got?"** → withhold what they heard. B-116. Correct.
* **"anything sooner?"** → repeat the earliest. B-142. Correct, already built.
* **"what about Monday?"** → **hear the day, including the times that made me
  ask.** Not built. This is N1.

So the change is one more stand-down arm on an existing wrapper, not a new
selection rule — and it must be an arm on `_prefer_unheard_clock_times`, not a
widening of B-116's pool, for the reason that file gives at length.

**The one thing to prove before building it:** the signal. A day named right
after a spread that offered it is not the same as a day named cold, and
`_prefer_unheard_clock_times` currently cannot tell them apart. Establish which
session key carries "this day came from the spread I just read" before writing
anything — `[[anchor-defect-rows-before-scheduling]]`, and the fourth wrapper on
this function is where a wrong guess is expensive.

### 🔴 The harness gate forbids the fix — re-aim it deliberately, do not delete it

`replay_presented_times.py:304` counts a **gate** failure, `MUST be 0`:

```python
own   = set(c.get("heard_clocks_this_day") or [])
spare = set(c.get("day_times") or []) - own
if own & set(cc) and len(spare) >= (c.get("limit") or 0):
    re_offered += 1
```

That is *verbatim* the thing N1's fix has to start doing: put a time the caller
already heard **on that day** back into the readout, on a day with plenty spare.
So the N1 fix cannot pass this gate as written, and the exemption already there
(a day too short to fill the readout) is the wrong exemption — it is the one
that produced the two `KEPT` rows above, which were never the defect.

**This is the third measurement in this project to encode a rule that a later
defect proved too broad**, and rev. 6 §7 already records the right treatment:
re-aim, never delete, and say in the commit which case moved it. The honest
re-aim is that "re-offered" means *re-offered in answer to "what else"* — the
same distinction B-142 drew in the code. Whoever builds N1 must move the gate in
the same commit as the fix, with the exhibit named, or the next reader reads a
red gate as a regression and reverts a correct change.

**Exit:** the 21:57 payload replayed offline as a failing test, `presented` for
Monday containing `08:00` or `17:10`; `replay_presented_times` re-aimed in the
same commit and green on its re-aimed terms; `lost` / `invented` still 0 — those
two are untouched by this and stay the real safety line; then a call that asks
about an offered day and hears at least one of its offered times back.

---

## 1.9 N4 — asking for a time on Monday is answered with Thursday

**P1, caller-audible, and it is the sharper half of the 11 Sep call.** New;
found by the call sheet's step 3, which was aimed at N2 and hit something else.

**CA91d1f12332f6230ed51ad1a427f91f5c, 11 Sep 23:11, northgate, build
`6e556ad0`.** The caller is mid-conversation about **Monday** — she has just
read Monday's times — and asks for a time:

```
23:11:58  Susie : Monday 14th September — half past ten, twenty past eleven,
                  twenty to three. And I've a few others that day.
23:12:14  caller: "as close as possible to 12 please"
23:12:18  tool  : check_availability day_window=1 after_date=2026-09-14
                  date_hint="around 12 noon"          <- N2's parse WORKED
23:12:18  [ms_llm] check_availability BLOCKED — slots already retrieved this
                   turn (last_offered_slots present); returning cached result
23:12:18  [slot_followup] 3 of 6 days already offered
                   -- leading with the 3 the caller has not heard
23:12:20  Susie : Number 1, THURSDAY 17th — ten past twelve, or ten to seven.
                  Number 2, Friday the 18th…  Number 3, Saturday the 19th…
```

**He asked for midday on Monday and was given Thursday, Friday and Saturday.**
He hung up. `12:10` was bookable on Monday the entire time and was never spoken.

### Three correct mechanisms, composing into a wrong answer

Nothing here is a model failure and nothing is a hallucination.

1. **N2's fix worked.** `"as close as possible to 12"` parsed, and the model
   asked for `day_window=1` from Monday with `date_hint="around 12 noon"` — the
   right question. Before `dfc8b914` it would have parsed to nothing.
2. **The re-entrancy guard fired,** correctly: `check_availability` had already
   run this turn, so the cached 6-day payload was returned instead.
3. **The "lead with days they have not heard" rule then applied to that cached
   payload** — and Monday, Tuesday and Wednesday were all heard, so it led with
   Thursday, Friday and Saturday.

**The day the caller was talking about is discarded between steps 2 and 3.**
`day_window=1 after_date=2026-09-14` says Monday and only Monday; the cached
result carries no memory that this turn was about one day, so the presenter
treats it as a fresh open-ended offer.

**The 12:00 pin proves it was still trying to answer him:** Thursday's readout
is `12:10 / 18:50`, and `12:10` is there *because* of the pin. It found his
time. It just put it on the wrong day — the one thing worse than not finding it,
because the answer sounds responsive.

**Same family as N1, one level up.** N1 forgets that the caller asked about a
day they had already heard; N4 forgets *which day they asked about at all*.
Both are the presenter treating "already heard" as a reason to move on, when
the caller is asking to stay.

**Anchor before scheduling this.** The seam is where `day_window=1` /
`after_date` survive into the cached branch — the block at
`llm_stream.py` "check_availability BLOCKED", and whatever reads
`last_offered_slots` after it. **Do not weaken the re-entrancy guard**; it
exists because a second live lookup mid-turn is its own defect. The narrow
question is whether a cached result should be re-presented as a multi-day
spread when the request that hit the cache named one day.

---

## 2. The register — every open item, with anchors

A row without `file:line` is a lead, not a finding. All of these have one.

### 2.1 Caller-audible

| id | what a caller experiences | anchor | state |
|---|---|---|---|
| **N1** | 🔴 **NEW, rev. 7, and it is the top of §8.** "What about Monday?" — and both times she offered for Monday are gone, replaced by three the caller has never heard. Nothing invented; every time is real and bookable. | `slot_followup.py:3798` `_prefer_unheard_clock_times`; the multi-day spread makes every day it named a **heard** day | **open, unbuilt.** 6 of 8 corpus re-readouts, and the 2 exceptions are two-slot days with no alternative. CA12036a4529 21:57. **The `replay_presented_times` gate forbids the fix** and must move with it. §1.8. |
| ~~**B-151**~~ | 13.7 s of dead air; the caller said "hello" because they thought the line had dropped, and that "hello" discarded the reply they were waiting for. **Not slot presentation.** | `connection.py` `on_llm_finished`; `_rearm_no_input_watchdog` | **FIXED (rev. 7), not call-verified.** Renumbered from B-149 — that id was taken on 6 Sep. §1.7. |
| ~~**N2**~~ | Asked for midday three times; read the three times FURTHEST from noon out of the five the day held. | `requested_clock_times` — "as" is not a preposition; "12 am" → `00:00` blanks the text | **FIXED `dfc8b914`**, not call-verified. CA2ac47ad588. |
| ~~**N3**~~ | "Say that again" dropped as a meaningless fragment; the caller waited 19 s and had to add a fourth word to be heard. | `_COMMUNICATIVE_WORDS`; fourth eaten answer at that site, third of this shape | **FIXED `ba3374ff`**, not call-verified. CAb2fc0c23f1. |
| ~~**S-13**~~ | **FIXED `8ab39703`, verified on a phone 14:42 — the D8 line fired for the first time in its life, through B-145 not D-B (§1.6, §4.4's correction box).** Was: A caller who names a TIME on a day already on the table is answered without it. "Anything around midday on Tuesday" → 08:00 / 09:40 / 15:30; asked again, 10:30 / 11:20 / 14:40. 12:10 and 13:00 were bookable and had been read out 16 s earlier. | `slot_followup.py:3687` reads `REQUESTED_TIMES_KEY`; its only three writers are `receptionist_tools.py:6658`, `:7319`, `:7642` — all inside `check_availability` | CAb8ac636017de7d35370fd7951c54d3cf, 13:44:22 and 13:44:36. **D8's pin is structurally dead on every payload-answered turn**, which is the path `speak_one_day_from_payload` takes (`no tool call needed (D-B)`). §4.4. |
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
| ~~**S-14**~~ | **FIXED `aa4c324c`, verified in the corpus 19:36 — four readouts, four rows, `presented` populated on every single_day row (which is S-8 on a phone).** Was: `record_offer` had ONE call site (`llm_stream.py:7394`, Gate 5). The three `slot_followup` producers write session state via `apply_offer_to_session` but **no obs row**. On the 13:43 call, **4 of 5 readouts left no trace in `calls.slot_offers`** — and they were the four that exposed S-13. The offer corpus systematically under-represents the payload-answered path. | `llm_stream.py:7394` is the only writer; `slot_followup.py:5276`, `:5365`, and `speak_one_day_from_payload` record nothing |
| ~~**S-8**~~ | `presented_days` empty on every `single_day` offer. | **FIXED `1508df0c`, CONFIRMED ON A PHONE 10 Sep 19:36** — `presented` populated on all three single_day rows of `CA5f45b7aa0d8720f3fa22c9c58b81f0f4`. It could not be confirmed until S-14 gave those producers a row to write. |
| ~~**S-9**~~ | No tool marker on `calls.latency`. | **FIXED `1508df0c`** — `TurnTiming.tool_calls`, and `latency_percentiles.py` reports the real split. **It reads 0 today and says so**: all 3,578 stored turns predate the field and are NOT OBSERVED. |
| **S-10** | **NEW.** `operational.speak_part_of_day` is **half-wired**. It changes the deterministic labels, but the *rendered* northgate prompt instructs the model to speak the band in **three separate places** — so flipping it makes the READOUT bare while confirmations and read-backs stay banded. | rendered prompt (105 k chars); `SLOT_FORMATTER_SYSTEM_PROMPT` line 36 also still carries a band-form reference table |

### 2.3 Adjacent — real, not slot presentation

| id | finding | anchor |
|---|---|---|
| ~~B-146~~ | **ALREADY FIXED — this row was stale through three revisions.** `hold_speech.py:832` carries the `service_named` gate with the B-146 exhibit in its own comment, and `llm_stream.py:5195` passes it. Verified 10 Sep: `classify_intent('yeah can i have a good sports massage please', service_named=True)` → `Intent.BOOK_NEW`, so a head fires. The row's diagnosis was also wrong in a way worth keeping: the fix deliberately reads the UTTERANCE, never `v3_treatment_mentioned` — that key is a call-scoped latch and a head keyed on it fires on every later turn of the call (B-138). | `hold_speech.py:832`, `llm_stream.py:5195` |
| — | STT drops numerals: `'the 30-minute session'` → `'the 5-minute session'`. Keyterm list carries no numbers. | 6 Sep register, secondary |
| — | `GOOGLE_SERVICE_ACCOUNT_JSON` invalid → Sheets skipped; ElevenLabs 401 on `/v1/models`. **Known-accepted on the demo line**, not on the live lines. | log, every call |

> **B-146 needed a decision for three revisions and had already been fixed.**
> Rev. 5 went looking for it as the last caller-audible item before the live
> clinic calls and found the gate, the exhibit and the wiring all present. The
> lesson is the register's own rule turned on itself: *a row without file:line
> is a lead, not a finding* — and a row WITH a file:line still decays, because
> `hold_speech.py:619` is no longer where that code lives. **Re-verify an
> anchor before scheduling work against it**, exactly as §7 says to re-verify
> a mechanism before believing a call step.
>
> **Live-line item that is NOT closed:** `GOOGLE_SERVICE_ACCOUNT_JSON` is
> invalid on the demo service (`JSONDecodeError: Invalid \escape: line 5
> column 46`), so every call-summary row is dropped. That is known-accepted on
> the demo line. **Nobody has checked whether the three clinic services carry
> the same broken value** — and if they do, every live call produces no
> `CallSummaries` row, which is §0's bar 4 (visibility) failing silently on the
> lines where it matters. It is a Render dashboard check, not a code change,
> and it is worth doing BEFORE the live calls rather than after.

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

### 4.4 · S-13 — the requested-time pin was dead on the payload path **[CLOSED `8ab39703`]**

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

> ### CORRECTION, rev. 5 — THIS SECTION'S ANCHOR IS HALF THE PATH
>
> **Shipped as `8ab39703`, verified on a phone at 14:42 — but not through the
> producer named above.** The log excerpt in this section is D-B's, because
> that is what the 13:43 exhibit produced, and a reader following it would
> patch one of two call sites.
>
> `speak_one_day_from_payload` has **TWO** callers, and which one a sentence
> reaches is decided by the caller's wording, not by anything in the code
> path: `named_day_speech` (**D-B**, a request) and the day-acceptance
> producer (**B-145**). *"Do you have anything around midday on tuesday"*
> resolved as an ACCEPTANCE, so B-145 answered it and D-B never ran.
>
> Both were given `user_text` in the shipped fix. Had only D-B been patched,
> the verifying call would have failed identically and the fix would have
> looked wrong. §1.6 has the exhibit.

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

> **Rev. 5: S-14 may have moved this, and nobody has checked.** The three
> `slot_followup` producers now record, and those are payload-answered turns —
> some of which are exactly the "no deterministic offer" population above.
> **Do not assume it is fixed and do not assume it is not.** Re-derive which
> turns leave a row before reading any Phase 2 gate off this corpus. §4.8.

Most likely to slip past Friday. **That is acceptable.** §4.1–4.3 deliver the
goal; this is what stops it decaying.

---

### 4.8 · S-14 — CLOSED, `aa4c324c`, and verified in the corpus

`record_offer` had ONE call site, Gate 5. Three producers spoke offers and
recorded none: `more_days_speech`, `numbered_more_times_speech`, and
`speak_one_day_from_payload` (both D-B and B-145). On the 13:43 call **4 of 5
readouts left no row**, and they were the four that exposed S-13 — so every
harness reading `calls.slot_offers` was measuring the Gate 5 path and calling
it the system.

The corpus is **forward-only**. A day the producers do not record is a day of
evidence that never exists, which is why this did not wait for Phase 2.

**Recorded beside the `apply_offer_to_session` that already owns "anything that
speaks an offer calls this"** — the same rule, one layer out. `presented_days`
is passed BY each producer rather than derived, because only the producer knows
which days it trimmed and that gap IS B-95's split; a row that guessed it would
be worse than no row.

**NOT put inside `apply_offer_to_session`**, which is the true convergence
point for all five producers. It would double-record Gate 5 unless that call
site were deleted too, and `presented_days` would then be derived rather than
passed — silently undoing S-8, which had been verified the day before.
**Enforcement comes from a test instead**:
`test_s14_every_readout_is_visible_in_the_corpus.py` walks `app/` by AST and
fails producer number six by name. A substring check for `record_offer` would
pass on a module that records in one producer and not the other two, which is
the exact shape of this defect.

Gates: 5 failed before / 7 pass after · `replay_slot_decisions` CHANGED 0 ·
`replay_presented_times` 0 changed, lost, invented, re-offered · failing-set
diff EMPTY (96 = 96). One test asserts the thing that would actually hurt: with
obs made to raise, the caller still hears the offer.

---

### 4.9 · The single-day readout pace — DONE, `2658f727`, and the guess was wrong

Owner report: *"the slot readout is too quick... it's fine when there are
multiple days"*, with the guess that week and day presentation are configured
differently.

**MEASURED FIRST, and there was no difference to correct.** On
CA5f45b7aa0d8720f3fa22c9c58b81f0f4 both readouts run at the same rate and both
synthesise at `speed=default`:

```
multi_day   318 chars   17.63s   18.0 chars/sec   2.94s per time offered
single_day  201 chars   11.20s   17.9 chars/sec   3.73s per time offered
```

Single-day is if anything the more generous per slot. **What differs is
decisions per second, not words per second**: in a multi-day readout each number
is a DAY carrying its own landmark ("Number 2, Tuesday the 15th —") which resets
attention; in a single-day readout each number is a TIME with only "Number 2,"
between them.

`ELEVENLABS_SLOT_SPEED`, single-day only, **default 1.0** so an untuned
deployment is byte-identical. **Set to 0.92 on the demo service and confirmed
right by ear.** Not set on the three clinic services.

**THE TRAP, and the whole change turns on it.** `_slot_presentation_mode` has
ONE writer, inside tool execution. A named-day follow-up runs no tool, so on
exactly the turn this feature is for it still holds the mode of the last LOOKUP
and says `multi_day` — steering off it would have slowed the readout the owner
said was FINE and left the reported one untouched. `apply_offer_to_session`
records `_slot_readout_mode` beside the chunks instead, and pops it with them.
The test suite caught the missing pop in the first cut.

**Pacing never goes in punctuation.** `" — "` and `"…"` both split a chunk;
that is how a phone number once straddled two synthesis calls.

**Still open, and it is an owner call:** this pulls against S-1, which says the
readout is too LONG. `speak_part_of_day: false` cuts 5.17 s and would more than
pay for 0.92. **Fewer words, spoken more slowly** is probably better than
either alone, and both are env vars — it can be heard on one call. §4.6.

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

### New, 10 Sep night

**A TEST BOOKING DEGRADES THE NEXT TEST CALL, SILENTLY AND IN TWO PLACES.**
The 14:42 call booked `2026-09-15T12:10`. The 19:36 call, running the identical
script, then:

* read Tuesday as 08:00 / 09:40 / 15:30 — three clock times already heard on
  other days, **with no S-2 log line at all**; and
* answered *"anything around midday"* with nothing near noon, **with no D8 log
  line at all**.

Both look exactly like regressions of the two fixes just shipped. Neither is.

`_prefer_unheard_clock_times` is **all-or-nothing** by owner decision (the
`_spread` OUTRANKS THIS paragraph): `if len(fresh) < limit: return chosen`.
With 12:10 gone, Tuesday's unheard-anywhere pool fell to `{13:00, 13:50}` = 2 <
limit 3, so it stood down. And `nearest_time_index`'s 20-minute tolerance left
nothing within reach of noon, so D8 declined. **Both stand-downs are silent by
construction** — the rules log when they FIRE, not when they decline.

**The tell is the `N of M bookable times spoken` count** in the D-B / B-145 log
line: `3 of 11` on the 14:42 call, `3 of 10` on the 19:36 one. One slot, the one
we booked.

**Cancel every test booking through Susie before the next call** — never
through the calendar, which leaves the reminder queued against a deleted event.

**A CALL STEP THAT CANNOT RUN IS NOT A CALL STEP THAT FAILED.** This is §4.5's
lesson with the sign flipped, and it is the more dangerous half: rev. 4 warned
that a passing step may hide a dead mechanism, and the 19:36 call shows a
FAILING-LOOKING step whose mechanism was correct. Before filing either, check
whether the mechanism could have fired at all.

---

## 8. What happens next, in dependency order

> **rev. 7 re-orders this list.** Rev. 6's text is kept below it unchanged.
>
> | | | |
> |---|---|---|
> | **0** | **N1 — §1.8** | 🔴 unbuilt. Systematic (6 of 6 on days with slots to spare), caller-audible, and it IS slot presentation. Comes with a harness gate that has to move in the same commit. |
> | **1** | **One call** | Proves **B-151**, **N2** and **N3** in one go — three unverified fixes on two different axes, distinguishable in the log. See below for exactly what to say. |
> | **2** | Promote | `latency-eval` → `production`, fast-forward, out of hours. Revert target `2658f727`. **Not before the call.** |
> | **3** | Non-grid diary | Rev. 6's item 2, unchanged and still the honest gap. |
>
> **The one call, three things, in order:**
>
> 1. **N2** — ask for "around 12" on a day that has no noon slot, then
>    *"as close as possible as 12"*. Expect times either side of midday, not the
>    three furthest from it.
> 2. **N3** — after any slot readout, say **"say that again"**. Expect the times
>    re-read, and `lost_total=0` in the call summary. That number is the tell;
>    it was `1`.
> 3. **B-151** — talk over Susie mid-reply. Expect
>    `WATCHDOG_REARM_SILENT_TURN reason=tts_inhibit` and no hole.
>
> And while you are there, **N1 reproduces for free**: ask for a few days, then
> ask about one of the days she just offered. Today she reads three times you
> have never heard and withholds both of the ones that made you ask.
>
> **Confirm `[build_info] running build <sha>` before trusting any of it.**

**Slot presentation is finished.** Nine items verified on a phone, script step 6
passed twice, both branches at `2658f727`. What follows is one open P1 that is
not slot presentation, one call, one owner decision, and Phase 2.

> ⚠️ **rev. 7: the sentence above is wrong.** It was true of everything rev. 6
> could see, and §1.8 is the thing it could not — the corpus that proves it
> landed the same evening. Left in place because deleting it hides how the
> claim was reached: every item was verified on a call, and no call happened to
> ask about a day it had just been offered.

### Shipped and promoted — `latency-eval` AND `production` at `2658f727`

| | item | commit | on a phone |
|---|---|---|---|
| ✅ | **S-1a** month said once | `40a67ea8` | **VERIFIED** — 17.85 s live vs 17.93 predicted |
| ✅ | **S-3** rung inside the bar | `dd15f2d7` | **NOT EXERCISED** — two attempts, closed §8.3 |
| ✅ | **S-2** cross-day preference | `afabb549` | **VERIFIED** — steps 2, 3, 4 |
| ✅ | **S-5** trim contract | `ab5b6752` | n/a — structural |
| ✅ | **S-6** stand-down logging | `1508df0c` | no stand-down has yet occurred |
| ✅ | **S-8** presented on single_day | `1508df0c` | **VERIFIED** 19:36, via S-14 |
| ✅ | **S-9** tool marker | `1508df0c` | **VERIFIED** — 0/1/0/0/0/0 |
| ✅ | **S-13** requested-time pin | `8ab39703` | **VERIFIED** 14:42 — through B-145, not D-B |
| ✅ | **S-14** payload readouts recorded | `aa4c324c` | **VERIFIED** — 4 readouts, 4 rows |
| ✅ | **S-7** spoken vs built | `e57f2d6c` | not yet read back |
| ✅ | **Stage C** model-readout counter | `ebc99d76` | not yet read back |
| ✅ | **single-day pace** | `2658f727` | **CONFIRMED BY EAR** at `ELEVENLABS_SLOT_SPEED=0.92` |

---

### 1. B-151 — FIXED, not call-verified. §1.7.   (was "B-149, the only one open")

13.7 s of dead air, `outcome=abandoned`, and the safety net absent for 24.4 s
because it is armed by speech and the turn produced none.

**Do this with a fails-before test built from §1.7's timeline, not by
inspection.** The seam is barge-in/watchdog inside a 12,000-line file and the
record says the obvious fix is wrong: teardown is on the PARTIAL, resolution is
on the FINAL. `_rearm_no_input_watchdog` (`connection.py:6102`) is the right
tool; the hole is `connection.py:4855`.

**Exit:** the timeline reproduced offline as a failing test; failing-set diff
EMPTY; then a call where a barge-in lands on an in-flight reply and the watchdog
is observed arming anyway.

> **rev. 7: the first two are done** — reproduced offline (3 of 10 tests failed
> before, 10/10 after), failing-set diff EMPTY, all three harnesses clean
> against a same-corpus baseline. **The call is the only thing left**, and it is
> the same call as N2's and N3's. §1.7's rev. 7 block has the fix, the two
> measurements that changed its shape, and what to listen for.

### 2. Call a non-grid diary

Every call verifying anything above was **northgate, a uniform 50-minute grid**.
Vital Edge and JV have not been called since T1b. **A call was placed the night
of 10 Sep and its log went to a different session** — find it before repeating
the work.

Two hazards northgate does not have: `SMS_ENABLED` and
`APPOINTMENT_REMINDERS_ENABLED` are ON for clinic services, so a real text goes
out and a real reminder is queued; and `nearest_time_index`'s 20-minute
tolerance may legitimately decline on an irregular diary — **pick a time you can
see in the diary** rather than "midday", or step 5 proves nothing.

**Cancel every test booking through Susie, never the calendar.** §7: one booking
disabled two of the three things the script exists to test.

### 3. Read the new corpus fields back

First time these can be answered in SQL rather than guessed:

```
what did the caller actually HEAR?        source / spoken     (S-7)
how often is the repair layer reachable?  source = 'model'    (Stage C)
does the reverse parse succeed there?     labels_read vs labels_resolved
```

**The corpus is forward-only and starts 10 Sep.** A small clean sample is not a
clean gate — that is S-6's shape. Give it traffic before deciding anything.

**Stage C's amended gate**, still unmet because it needs all clinics: ZERO of
BOTH `could not resolve spoken option(s)` (site A) and `resolved to NO payload
slots` (site B), read as a pair.

### 4. S-1 remainder — the owner decision. §4.6, and now §4.9 too.

`speak_part_of_day: false` on northgate: **17.93 s → 12.76 s**, resolution
identical on 7/7, calibration confirmed live to 0.5%. It pulls the opposite way
from the pace change just shipped, and **fewer words spoken more slowly is
probably better than either alone.** Both are env vars; it can be heard on one
call without shipping anything. Decide it WITH S-10 (half-wired, §2.2).

### 5. Phase 2 — one producer, one record, fewer guards

`SLOT_PRESENTATION_CONVERGENCE.md` in its own order, then step 5 of the 31 Aug
document. ≥ 2 days. **Now genuinely unblocked**: the model-readout counter is
the measurement `STAGE_C_EVIDENCE` §5 said the ~900-line deletion was waiting
on. Test surface is one literal pin plus one negative assertion to preserve.

### 6. Stop needing a call per clinic — the thing that survives 15 of them

Not queued, and the largest lever on this page. `tests/harness/` already drives
the live turn loop in-process with no phone and no calendar writes, and
`fake_clinic.py:231` runs the REAL `_exec_check_availability`, **faking only the
reader**. `FakeDiary(slots={date: [times]})` is the seam.

**Snapshot each clinic's real availability read-only and feed it to
`FakeDiary`**, and clinic 15 costs what clinic 3 costs. What it will still NOT
catch, and must be said out loud whenever it is relied on: per-service env vars,
`theorem_v3`'s hardcoded prompt, Theorem's Acuity short-circuit, and everything
the phone owns. Calls do not go to zero — they go from every clinic on every
change to one representative clinic per release.

---

### Open, deliberately not queued

| id | why |
|---|---|
| **S-11** | The ladder watches the token, not the audio — 63% of breaches. Has a live instance on this build (§1.6, turn 7: ttfa 3144 ms, headless, over the bar). Evidence of the shape, not new evidence about the remedy. **Do not touch without a new idea, not just a new instance.** |
| **S-12** | 47.1% of turns breach the 3 s bar on the caller's clock. General latency work. |
| **S-4** | `last_bot_prompt` truncation, fires on every call. Improves free when the readout shortens. **Do not touch the cap** — 34 writers, RED. |
| **S-10** | `speak_part_of_day` half-wired: flag changes labels, prompt still instructs the band in three places. Decide WITH item 4. |
| **Sheets** | `GOOGLE_SERVICE_ACCOUNT_JSON` invalid on the demo service, so every call-summary row is dropped. Known-accepted there. **Nobody has checked the three clinic services** — if they carry the same value, every live call writes no `CallSummaries` row. Dashboard check, not code. |
| ~~B-146~~ | **Already fixed, and the row was stale for three revisions.** §2.3. |

---

**If you have time for exactly one thing: N1 (§1.8).** B-151 is fixed and needs
only a call; N1 is unbuilt, systematic, and a caller hears it every time they
ask about a day. Everything else on this page
is verified, decided, or measurable.
and its safety net is the one that is supposed to catch everything else.

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
