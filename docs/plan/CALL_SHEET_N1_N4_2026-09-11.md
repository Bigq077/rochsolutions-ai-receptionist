# Call sheet — N1, N4, N3, B-151 in one call (2026-09-11)

**Line:** +447366263180 (demo, `northgate`). **Not** +447367002651.
**Build:** confirm `[build_info] running build 88f801f5` (or later) at call
cleanup before reading anything. `/health` is hardcoded 1.0.0.

**Don't complete a booking.** A booked slot shrinks the pool and makes the next
test call's stand-downs look like regressions (§7, "a test booking degrades the
next test call"). If one slips through, cancel it **through Susie**.

The times below are written RELATIVE to what Susie actually says, because
northgate's diary changes and a script with guessed clock times fails for the
wrong reason. Write down what she offers at step 2 — every later step is
scored against it.

---

## 1. Open
> *"Hi, I'd like to book an appointment — it's my ankle."*

## 2. Get a spread
> *"Anytime next week."*

**Expect** three days, two times each, no clock time on two days.
**Write down** Day 1's two times (call them **A** and **B**) and Day 2's two
(**C** and **D**).

## 3. N1 — ask about a day you were just offered
> *"Um, what about [Day 1]?"*

**PASS:** she reads [Day 1] with **both A and B**, plus **one time you have not
heard on any day**, and "a few others that day".
**FAIL (the old defect):** three times, none of them A or B.

**Log — the mechanism, not just the words:**
```
[slot_followup] the caller asked about 2026-..., which they were already offered at ['A', 'B'] ... (N1)
'<Day 1 label>' answered from the payload -- 3 of N bookable times spoken ... (D-B)
```
No `(N1)` line and the step "passed" → it did not pass; find out why.

## 4. N1 again, second day
> *"And what about [Day 2]?"*

**PASS:** **C and D** both there; the third time is **not** one you heard in
step 3.

## 5. N4 — ask for a time on the day under discussion
Straight after step 4, while [Day 2] is on the table:
> *"As close as possible to 12, please."*

**PASS:** an answer about **[Day 2]**, with the time nearest noon on it (on
northgate's grid, 12:10 if it is free) — and **no other day named**.
**FAIL (the old defect):** "Number 1, Thursday…" — a day you did not ask about.

**Log:** this step only exercises N4 if the model's lookup is REFUSED. Look for
```
check_availability ... day_window=1 after_date=<Day 2>
check_availability BLOCKED — slots already retrieved this turn
the refused lookup named ONE day (<Day 2>, day_window=1) ... (N4)
```
If there is no `BLOCKED` line, the lookup ran for real and **N4 was not
tested** — score the step as NOT EXERCISED, not as a pass (§4.5, and the 19:36
call's lesson with the sign flipped).

## 6. N3 — three words
After any readout:
> *"Say that again."*

**PASS:** the times are re-read; `lost_total=0` in the call summary.

## 7. B-151 — talk over her, then go quiet
On her next reply, **talk over her with a real sentence**, then **count to
fifteen without speaking**.

**PASS:** `WATCHDOG_REARM_SILENT_TURN reason=tts_inhibit` in the log, and she
speaks again before fifteen. **FAIL:** silence to the end of the count.

## 8. Hang up.

---

## Not in this call, deliberately

* **N6** ("what else have you got on Monday" → other days). Pre-existing,
  known, strict xfail. Saying it will reproduce it; that is not news.
* **The 9 Sep N1** (an acceptance-shaped "what about Monday" routed to the
  model, CA2ac47ad588). Different defect, same id — see rev. 8 note 4. It
  needs a day holding ONE slot, which northgate's grid will not give you.
* A non-grid diary (Vital Edge / JV). Still the honest gap, still §8 item 3.

## After the call

Pull the obs row and check, in this order: `build_sha`; the `slot_offers`
rows for steps 3–4 carry `"producer": "D-B"`; the transcript matches the
record. Then score each step PASS / FAIL / **NOT EXERCISED**.
