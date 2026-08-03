# Call suite — `B-46` verification, 3 Aug 2026

**Build `773e1f57cd57`.** Service `low-latency-joint-venture`, number
**`+447366263180`**, clinic `jv_v1`.

Verifies `80b545b` (`B-46` — the booking read-back fired before any slot was
offered) and, on the same dials, three fixes carrying test-only debt: `B-33`
(a name invented from Susie's own utterance), `B-38` (the write CTA truncated out
of `last_bot_prompt`), and the Bug B day-change escape.

---

## 0. Before you dial

**Confirm the build.** `/health` returns a hardcoded `"version": "1.0.0"` and
**cannot** tell you what is deployed. The only proof is the Render log line at
call cleanup:

```
[build_info] running build 773e1f57cd57
```

If it says anything else, stop — you are testing the old code and every result
below is worthless.

**Two housekeeping notes:**

- Calls 1 and 2 **create real calendar events**. Delete them afterwards.
- The test calendar already holds ~11–13 future appointments under
  `+447502211207`. That is the `B-42` test-data contamination and it is expected.

**Dial in the order written.** Call 2 is the regression check on the change
itself; if it fails, stop and do not spend calls 3 and 4.

---

## Call 1 — `B-46`: the read-back must wait for a slot

**The defect:** you give a first name, and Susie reads back a booking for an
appointment nobody has offered you — skipping the surname and the phone step.

**Say, in this order.** Give the name *early* and unprompted; that is what
triggered it.

| # | You say |
|---|---|
| 1 | *"Hi, my name's Quentin, I'd like to book an appointment please"* |
| 2 | *"It's for my shoulder — it's been aching a few weeks"* |
| 3 | *"Some time later this week if you've got it"* |
| 4 | *(pick whichever slot she offers)* — *"Yeah, the first one's good"* |
| 5 | *"Roch. R-O-C-H."* |
| 6 | *"Yes, use this number"* |
| 7 | *"Yes, go ahead"* |

### ✅ PASS

- Turns 1–3 produce **questions**, not a booking summary.
- Susie offers **actual slot times** before she ever says *"shall I go ahead and
  book that in?"*
- The surname is asked for (turn 5) and the phone step happens (turn 6) **before**
  the read-back.
- The read-back, when it comes, names **you and a real slot**.

### ❌ FAIL — this is the defect, unfixed

Any booking summary before turn 4. The exact shape to listen for:

> *"So that's Quentin — shall I go ahead and book that in?"*

…with **no day, no time**, or a slot you were never offered.

### Log lines

```bash
grep -nE "build_info|check_availability BLOCKED|forcing booking readback" render.log
```

- **Must NOT appear before slots are offered:**
  `check_availability BLOCKED — name collected + phone CONFIRMED`
- If that line appears at turn 2 or 3, **the fix is not deployed.**

---

## Call 2 — the direction the fix could have broken

**Why this call exists.** `B-46` made the guard arm *later*. The guard's original
job (BUG-14) is to stop the model re-running availability once your details are
settled and dead-ending — asking your name a second time, or re-offering slots
you already chose. If that returns, the fix traded one defect for another.

**This is the call that decides whether `80b545b` was safe.**

| # | You say |
|---|---|
| 1 | *"Hello, I'd like to book an appointment"* |
| 2 | *"My knee's been sore, just want it looked at"* |
| 3 | *"Whenever you've got, really"* |
| 4 | *"That first one works"* |
| 5 | *"Quentin Roch"* |
| 6 | *"Yes, use this number"* |
| 7 | **Stay quiet for ~4 seconds**, then: *"Sorry, could you say that again?"* |
| 8 | *"Yes, book it please"* |

Turn 7 is the pressure: a vague turn *after* the phone is confirmed, which is
exactly where the model used to re-run availability.

### ✅ PASS

- After turn 6 Susie goes **straight to the read-back**.
- Turn 7 gets the read-back **repeated**, not a fresh set of slots.
- **She never asks your name again.**
- One booking, on the slot you picked at turn 4.

### ❌ FAIL — BUG-14 has returned

- Slots offered again after turn 6.
- *"Can I take your name?"* a second time.
- *"I don't actually have a slot confirmed for you yet."*

### Log lines

```bash
grep -nE "check_availability BLOCKED|forcing booking readback|already retrieved" render.log
```

> **Honest reading — absence is not failure.** The guard only logs when the model
> *attempts* `check_availability` at that moment. If the model simply doesn't try,
> there is no line, and the call still passes on behaviour. **Judge this call on
> what you hear, not on the presence of the line.** Same division of labour as
> Gate 5f: the steering layer resolves it first, the guard is the backstop.

---

## Call 3 — the Bug B escape must still work

`CAc6b971ad`: a caller asked for Wednesday seven times from behind this guard,
was re-read Tuesday every time, and hung up unbooked. The escape is
`latency-eval`-only — `main` does not have it — so a port could silently drop it.

| # | You say |
|---|---|
| 1 | *"Hi, I'd like to book an appointment"* |
| 2 | *"Lower back, it's been stiff"* |
| 3 | *(if a screening question comes, answer honestly — probably "no" to all)* |
| 4 | *"Whatever's soonest"* |
| 5 | *"That's fine"* → then **"Quentin Roch"** → **"Yes, use this number"** |
| 6 | **After the phone is confirmed:** *"Actually — have you got anything on a different day?"* |
| 7 | *(take whatever she offers)* — *"Yes that one"* |
| 8 | *"Yes, book that in"* |

### ✅ PASS

- Turn 6 makes her **genuinely check another day** and offer **different times**.
- The booking lands on the **new** day.

### ❌ FAIL

- She announces *"let me check Wednesday"* and then re-reads the **original** day.
- She repeats the old read-back verbatim and will not move.
- Any *"that's exactly what I've got"* loop.

### Log lines

```bash
grep -nE "check_availability BLOCKED|different_day_steer|check_availability" render.log
```

Turn 6 must produce a **real** `check_availability` call, not a BLOCKED line.

---

## Call 4 — `B-33`: a name must not be invented

`CAc3c4e661`: the caller said only *"I've hurt my ankle"* and **`Rehab`** was
persisted as the patient name, with keypad phone collection armed behind it.
Type digits at that moment and the appointment is written under "Rehab".

**Give no name at all until the very end.**

| # | You say |
|---|---|
| 1 | *"I've hurt my ankle"* |
| 2 | *(let her talk — say nothing for a full turn)* |
| 3 | *"What sort of thing do you do for that?"* |
| 4 | *"How much is it?"* |
| 5 | *"Okay, no thanks for now"* → hang up |

**Do not give a name and do not book.** The point is that nothing gets captured.

### ✅ PASS

No name is ever persisted. Nothing in the log names you as anything.

### ❌ FAIL

```
[ms_conn v3] name persisted (normal path): '<any word>'
v3_phone_dtmf_active = True (name confirmed — phone collection phase)
```

Any word from **Susie's own sentence** appearing as a name — `Rehab`, `Massage`,
`Marcus`, `Bolton`, `Alcester`.

### Log lines

```bash
grep -nE "name persisted|v3_phone_dtmf_active|Row built" render.log
```

The summary row must read `name=` **empty**, not a word Susie said.

---

## Watch across all four — `B-38`, free of charge

`B-38` truncates the write CTA out of `last_bot_prompt` when the read-back runs
long (251 chars measured on a reschedule, against a 200 cap). It re-opens two
defects at once. **You cannot force it reliably**, but it is free to watch for:

- you say *"yes"* / *"go ahead"* to a booking question and it is **ignored** —
  you get a re-steer or a repeated question instead;
- Susie asks *"which of those would you like?"* when the outstanding question was
  the booking CTA, not a slot.

```bash
grep -nE "slot fragment ignored|WATCHDOG_FIRE|write CTA outstanding|cta_asked" render.log
```

Report the call SID if you hear it; do not chase it.

---

## Reporting

For each call, capture: **SID**, `[build_info]` line, pass/fail, and any
surprising log line verbatim. A call that passes for the wrong reason is worth
more than one that fails — say what you actually heard, not what the row expected.

> **The standing caveat applies.** Stored obs transcripts are **post-Gate-5**, so
> a wrong sentence there may be the gate rewriting a correct generation. Judge
> these calls **by ear** and by the Render log, not by the obs transcript.
