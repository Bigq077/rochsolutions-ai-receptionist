# Call suite — `A3` (surname read-back) verification, 3 Aug 2026

**Build `__________`** — fill in the A3 build SHA before dialling. Service
`low-latency-joint-venture`, number **`+447366263180`**, clinic `jv_v1`.

Verifies the A3 change: **the surname is collected before the booking read-back,
and the read-back says it aloud.** On the same dials it clears `B-52`, which the
3 Aug call did not exercise.

---

## 0. Before you dial

**The baseline already exists — do not spend a call on it.** Call
`CA451f165085a33431137630a188ed871a` (build `8ac5ecf069e5`, 18:22–18:24 on 3 Aug)
is the recorded pre-fix failure and is what every row below is measured against:

```
18:23:58  book_appointment BLOCKED — surname not captured (patient_name='Quentin')
18:24:00  synthesise_chunk: 'And your surname?'          <- surname asked only after the block
18:24:12  book_appointment ... "patient_name": "Quentin Rook"
18:24:13  synthesise_chunk len=83 "So that's Quentin, Saturday the 15th of Aug…"
```

Two facts to hold on to. The turn-8 read-back chunk is `len=83`, **byte-identical
to the turn-6 chunk generated before any surname existed** — so `Rook` was written
and never spoken. And the surname was requested only because the write guard
refused; nothing in the flow asked for it.

**Confirm the build.** `/health` returns a hardcoded `"version": "1.0.0"` and
cannot tell you what is deployed. The only proof is the Render line at cleanup:

```
[build_info] running build <A3 sha>
```

If it still says `8ac5ecf069e5`, stop — you are testing the code that produced
the baseline and every result below is worthless.

**Housekeeping.** Calls 1–3 create real calendar events; delete them afterwards.
Two bad events from before this suite also need deleting:
`ut7p0a17j71fabqpm9jlpspo24` (Quentin Way) and `94q9h39eo4n9qdm2o0eer81890`
(Quentin Rook, Sat 15 Aug 10:15).

**Dial in the order written.** Call 1 decides whether A3 works at all. If it
fails, stop — calls 2–4 test the edges of a fix that isn't there.

---

## Call 1 — A3: the surname must be spoken back

**The defect:** STT mishears your surname, it goes to the calendar, and you never
get a chance to hear it. Proven live twice — `Way` by the parser, `Rook` by STT.

> ### Arming the test — read this before dialling
>
> **Say "Roch" plainly. Do not spell it.** Spelling it is the *control*, not the
> test — that is Call 2. On 3 Aug, spoken plain, AssemblyAI returned `rook` in
> two consecutive partials and in the final. That mis-hear is the arming
> condition, and it is reliable.
>
> **Your signal that the test is armed is hearing a wrong surname come back.**
> If she reads back "Roch" correctly, STT got it right this time and the call
> proves nothing about A3 — it only proves the read-back exists. Note it, redial,
> and say it faster or less clearly. A pass on an unarmed call is the same false
> clean the 2 Aug suite produced twice.
>
> **Do not correct her before the read-back.** The whole point is whether the
> system surfaces the error unprompted.

| # | You say |
|---|---|
| 1 | *"Hi, I'd like to book an appointment"* |
| 2 | *"My left ankle's been a bit stiff"* |
| 3 | *"Anytime next Saturday if you've got it"* |
| 4 | *(pick a slot she offers)* — *"Yeah, the second one works"* |
| 5 | *"Quentin Roch"* — **both names, spoken plain, not spelled** |
| 6 | *"Yes, use this number"* |
| 7 | **Listen to the read-back. Do not speak over it.** Then: *"Yes, go ahead"* |

> **Turn 5 must carry BOTH names.** No first name is given at turn 1, so Step 7
> asks *"could I take your first name and surname?"* — and a bare *"Roch"*
> answers that with a single token, which is captured as the **first** name.
> `book_appointment` then blocks on `surname_required` and you end up testing
> the surname backstop instead of the read-back.
>
> If she asks for the surname on its own anyway, answer it plainly — *"Roch"* —
> and carry on; that path arms the test too.

### ✅ PASS

- The surname is **asked for before the read-back**, as part of the flow — not
  after you have already said "go ahead".
- The read-back contains **both names**: *"So that's Quentin Rook, Saturday the
  15th of August at quarter past ten…"*
- You can hear the error at turn 7 and could have stopped it.
- You never hear *"Just locking that in now…"* followed by a question.

### ❌ FAIL

Any of these:

- The read-back says only *"So that's Quentin"* — A3 did not ship.
- You are asked for the surname **after** confirming the booking.
- *"Just locking that in now…"* and then *"And your surname?"* — the write guard
  is still doing the collecting. This is the baseline behaviour, unchanged.
- The read-back names a surname you can't make out because it is rushed into the
  same breath as the date.

### Log lines

```bash
grep -nE "build_info|surname|patient_name|synthesise_chunk.*So that's" render.log
```

**The decisive check — the two must agree.** Take the surname in the last
`book_appointment` payload and the surname in the read-back chunk:

```bash
grep -nE "book_appointment.*patient_name" render.log | tail -1
grep -nE "synthesise_chunk.*So that's" render.log
```

If `patient_name` is `"Quentin Rook"` and the read-back chunk does not contain
`Rook`, **A3 has failed even if the call sounded fine.** That is exactly the
baseline shape.

> **Chunk length is a usable proxy.** On the baseline both read-back chunks were
> `len=83`. With the surname in it, the A3 read-back must be **longer than the
> first-name-only version** — a chunk of `len=83` starting `"So that's Quentin,"`
> is the old sentence.

**Must NOT appear:**

```
book_appointment BLOCKED — surname not captured
```

If it does, the surname is still being discovered by the write guard rather than
collected by the flow. The guard firing is not a pass — it is the symptom.

---

## Call 2 — the control: a correct surname must not be second-guessed

**Why this call exists.** The failure that matters more than a wrong name is
**over-challenging**: a read-back that makes the caller re-confirm a surname that
was already right, or loops on it. `B-15` recorded a caller sent round the
surname loop twice on a live call. A3 must not reintroduce that.

| # | You say |
|---|---|
| 1 | *"Hello, I'd like to book something in"* |
| 2 | *"Knee's been sore, want it looked at"* |
| 3 | *"Whenever you've got"* |
| 4 | *"That first one's good"* |
| 5 | *"Quentin Roch. R-O-C-H."* — both names, then spell the surname |
| 6 | *"Yes, use this number"* |
| 7 | *"Yes, book it please"* |

### ✅ PASS

- Read-back says **"Quentin Roch"**, spelled correctly.
- **One** read-back. She does not ask you to confirm the surname a second time.
- Booking lands first time — no second *"shall I go ahead?"* round.
- Calendar event reads `Quentin Roch`.

### ❌ FAIL

- *"Can I just confirm the surname?"* after you already spelled it.
- Two read-backs where one would do.
- A spelling loop: *"Was that R-O-C-H?"* → yes → *"So that's R-O-C-H…"*
- She asks for the surname again at turn 7.

### Log lines

```bash
grep -nE "surname|name persisted|Row built" render.log
```

`name persisted` should appear **once** with `'Quentin Roch'`. Two persists with
the same value is the loop starting.

---

## Call 3 — the correction path, and the trap in it

**This is the call most likely to fail, and it is the one that decides whether A3
is safe to keep.**

A3 only has value if you can act on what you hear. But correcting a surname
happens *after* the phone is confirmed — and that is precisely where the BUG-14
guard lives. On the baseline call, given a surname at that point, the model tried
to re-run availability and was blocked:

```
18:24:09  check_availability BLOCKED — name collected + phone CONFIRMED; forcing booking readback
```

The guard held. But a **correction** is a different shape from a first supply,
and if the model reaches for `check_availability` again the caller can lose the
slot they already chose.

| # | You say |
|---|---|
| 1 | *"Hi, can I book an appointment"* |
| 2 | *"It's my shoulder, aching a few weeks"* |
| 3 | *"Something later this week"* |
| 4 | *(pick a slot)* — *"That one's fine"* |
| 5 | *"Quentin Roch"* — both names, plain, expect the mis-hear |
| 6 | *"Yes, use this number"* |
| 7 | **At the read-back:** *"No — the surname's wrong. It's Roch, R-O-C-H."* |
| 8 | *"Yes, that's right — go ahead"* |

### ✅ PASS

- Turn 7 is **accepted as a correction**. She says the corrected name back.
- **The slot from turn 4 survives.** She does not re-offer times, does not ask
  the day again, does not ask which slot you wanted.
- One booking, on the original slot, under `Quentin Roch`.
- Turn 7 comes back in **under ~3 s**, or with a filler if longer.

### ❌ FAIL

- Slots offered again after turn 7 — the correction re-triggered availability.
- *"Sorry, which day were you after?"*
- She treats "Roch" as a new booking, or asks for the reason again.
- The booking is written as `Rook` anyway — the correction was heard but not
  applied to the payload.
- A dead-end: she acknowledges the correction and then asks nothing, and the
  watchdog has to rescue the turn.

### Log lines

```bash
grep -nE "check_availability|BLOCKED|surname|patient_name|slot_iso|WATCHDOG_FIRE" render.log
```

- `slot_iso` in the final `book_appointment` **must equal** the slot from turn 4.
- A **real** `check_availability` (not BLOCKED) after turn 7 is a fail — the
  correction escaped the guard.
- A `BLOCKED` line at turn 7 is **acceptable but worth reporting**: it means the
  guard is the only thing preventing it, and the steering layer isn't handling
  corrections yet.

> **Honest reading — absence is not a pass.** If the model simply doesn't attempt
> `check_availability`, there is no line either way. Judge this call on whether
> the slot survived, not on the presence of a guard line.

---

## Call 4 — `B-52`, which the 3 Aug call did not test

**Read this: the 3 Aug call did not exercise `B-52`.** The transcript was
`'by the way my name is quentin'` — the phrase **leading**. The defect needs it
**trailing**. With "by the way" in front, the extractor anchors on *"my name is
quentin"* and there is nothing after the name to mis-capture, so
`_walk_particles_back` was never reached. `dc974f6` is still test-and-replay only.

**The utterance must end with the phrase.** Say the name, pause slightly, then
the tail — as one sentence, not two.

| # | You say |
|---|---|
| 1 | *"Hi, I'd like to book an appointment"* |
| 2 | *"Sorry — my name is Quentin, by the way"* ← **the test. Nothing after it.** |
| 3 | *"It's for my lower back"* |
| 4 | *"Whatever's soonest"* |
| 5 | *(pick a slot)* — *"That one"* |
| 6 | *"Roch. R-O-C-H."* |
| 7 | *"Yes, use this number"* → then *"Yes, go ahead"* |

Also worth one dial each if you have the calls to spare — same defect family,
different word, all four reproduced offline:

- *"my name is Quentin at the moment"* → captured `Moment`
- *"I'm Quentin in a rush"* → captured `In`

### ✅ PASS

- **No surname is taken at turn 2.** She thanks you by first name only.
- She **asks you for the surname later** — you are not silently booked without one.
- The calendar event reads `Quentin Roch`.

### ❌ FAIL

Any of:

```
[ms_conn v3] name persisted (normal path): 'Quentin Way'
[ms_conn v3] name persisted (normal path): 'Way'
[ms_conn v3] name persisted (normal path): 'By'
```

Or a read-back naming you *"Quentin Way"*. Or — quieter and worse — **no
surname request later in the call**, because the system believes it already has
one.

### Log lines

```bash
grep -nE "name persisted|first-turn name extracted|patient_name|surname" render.log
```

---

## Watch across all four — latency

The baseline burned three turns on guard-driven recovery:

| turn | baseline `content_ttfa_ms` |
|---|---|
| 7 | 4794 |
| 8 | **7585** |
| 9 | 5381 |

Every one of those was a blocked tool call forcing another LLM iteration. If A3
collects the surname in the flow, those iterations should not happen.

```bash
grep -nE "\[LAT\]" render.log | grep -oE "turn_seq=[0-9]+ .*content_ttfa_ms=[0-9]+"
```

**Expected after A3:** no turn above ~3000 ms `content_ttfa_ms`. If turn 8 is
still ~7500 ms, the guards are still doing the collecting and A3 changed the
wording without changing the order.

---

## Two log defects to confirm while you are here

Neither is on the call path; both were in the 3 Aug log and both mislead anyone
reading it after you.

```bash
grep -nE "SMS_ENABLED is off|Booking confirmation SMS sent|GOOGLE_SHEETS_ID" render.log
```

- `booking_sms` logs **"Booking confirmation SMS sent to ***1207"** two lines
  after **"SMS_ENABLED is off — outbound SMS suppressed"**. The second line is
  false.
- `GOOGLE_SHEETS_ID MISSING` on this service — `CallSummaries` rows are silently
  dropped, so pillar 4 (visibility) is off here.

---

## Reporting

For each call: **SID**, the `[build_info]` line, pass/fail, the final
`book_appointment patient_name`, and the read-back chunk verbatim. Those last two
together are the whole of A3 — everything else is context.

> **The standing caveat applies.** Stored obs transcripts are **post-Gate-5**, so
> a wrong sentence there may be the gate rewriting a correct generation. Judge
> these calls **by ear** and by the Render log, not by the obs transcript. That
> distinction is exactly what `B-52` cost us last time.
