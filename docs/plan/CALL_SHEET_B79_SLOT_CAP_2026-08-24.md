# Call sheet — B-79 slot readout cap · 2026-08-24 (evening)

**Clinic / dial:** `jv_v1` · **`+447366263180`** — the demo line, service
`low-latency-joint-venture`, branch `latency-eval`.
🚫 **Never `+447367002651`** (Marcus, live) and **never `+447380841468`**
(Mark, live). Neither has this fix.

**Build must read:** `[build_info] running build 58319e89bc65` in the Render log.
**Time:** ~10 minutes, 3 calls. C-2 is the one that matters.
**Diary writes:** only call **C-3**.

**Supersedes** `CALL_SHEET_B77_SLOT_CLAIM_2026-08-24.md`. That sheet's C-2 was
run on `91a8db26` and found B-79 — this sheet replaces it.

---

## ⚠️ What this closes, and what the last call proved

`91a8db26` fixed *reachability* — every slot on a day can now be reached. The
24 Aug 12:23 call (`CA6b90c3a2`) confirmed that and exposed three more:

| | Defect | Symptom on that call |
|---|---|---|
| B-79a | nothing capped the SPOKEN option count | five times read out in one breath |
| B-79b | nothing recorded what the MODEL read out | "the others" returned times already heard, twice |
| B-79c | "anything else that day" used the wrong day | a Wednesday caller offered Tuesday |

B-79c is the severe one and it is **live on the current build**. B-79b is why
that caller hung up.

---

## Pre-flight

1. **Build.** Render → `low-latency-joint-venture` → logs → `[build_info]
   running build <sha>` at the end of any call. `/health` returns a hardcoded
   `1.0.0` and proves nothing.
2. **Which calendar** the demo line writes to — confirm before C-3.
3. Sheets / `EVAL_STAFF_SMS_TO` warnings on this line are known-accepted.
   `GOOGLE_SERVICE_ACCOUNT_JSON` has a genuine bad escape; ignore it here.
4. SMS is off on this line (`SMS_ENABLED is off — outbound SMS suppressed`).

---

## C-1 — the offer is three, and the tail invites the follow-up

| You say | Expect |
|---|---|
| "Hi, can I book an initial assessment, in clinic?" | asks what it's for |
| "Just my left ankle, nothing serious." | asks when suits |
| "Have you got anything on Tuesday?" | reads the times |

**PASS:**
- **At most THREE numbered options.** Four or more is a FAIL — that is B-79a.
- The tail comes **before** the closing question: "…half past six in the
  evening. And I've a few others that day if none of those suit. Any of those
  work?" Tail *after* the question is the old order.
- Grammar matches the count — "none of those suit" over three, "neither suits"
  over two. "Neither" over three is a FAIL.
- No dead air after the question.

**Log:** `[ms_gate5] slot buf: TRIMMED readout N option(s) -> 3` fires only if
the model over-read. `[ms_gate5] slot buf: 3 spoken option(s) recorded as
offered` should fire on every readout — if it is **absent**, the record did
not take and B-79b is still live for that call.

---

## C-2 — walking the day  ⭐

Continue from C-1 without hanging up.

| You say | Expect |
|---|---|
| "Have you got anything **else that day**?" | **every** remaining time on that day, none repeated |
| "Anything else that day?" again | "I don't have any further times on that day — would you like me to look at a different day?" |

⚠️ Exact wording. "Anything **else** that day" routes to the follow-up;
"another day" / "a different day" leave it entirely.

**PASS — all four:**
1. Not one time you already heard is repeated. A repeat is B-79b, still live.
2. You reach the **last** slot of the day (Bolton's is 20:30).
3. The remainder arrives in **one** answer, not two at a time.
4. The day named in the answer is **the day you asked about** — if you asked
   about Tuesday and hear "On Wednesday…", that is B-79c and it is a stop.

---

## C-3 — RETIRED. It tested an affordance that does not exist.

> **Run 24 Aug on `CAd075ea9673`, build `1db23a26bb94`. The corrected script was
> right; the PASS criterion was wrong.** Pressing `1` after a numbered readout
> never selected a slot on this system and never has — slot DTMF is
> **fallback-only**, armed solely when Susie's previous prompt contains the word
> "keypad", which she says only after failing to understand a *spoken* choice.
> On a first readout the digit is discarded:
> `DTMF raw digit='1' v3_phone_dtmf_active=False` →
> `dtmf_digit_discarded`. Recorded as **B-85**.
>
> That criterion was mine and it was wrong twice — first the script (day map vs
> time map), then the premise. What the call DID prove is below.

**What C-3 actually established, and it is worth having:**

1. **The cap survives the whole booking path.** Three numbered options, and the
   diary event is `2026-09-01T17:00` — exactly the time read out as Number 1.
   The slot map is built from the trimmed text and resolved correctly:
   `_resolve_slot_iso: available_days match → 2026-09-01T17:00:00+01:00`.
2. **Voice selection works on the trimmed readout.** *"yeah i'll take the first
   slot you offered"* resolved to Number 1.
3. **B-81 did not recur.** `name persisted (normal path): 'Quentin Rock'` — from
   the caller's own words, on the build carrying the fix.
4. `outcome=booked`, `dur=131s`, `lost_total=1` — the single loss being the
   discarded keypress.

**Do not re-run C-3 as written.** The keypad question is now B-85 and is blocked
behind B-80; testing it before both are fixed can only reproduce a known result.

**Cleanup owed:** this call wrote a real event — Tuesday 1 September, 17:00,
"Quentin Rock", on calendar `63bc844e…@group.calendar.google.com`. Cancel it by
calling back and asking Susie, not in the calendar.

---

## Recording the result

| Call | Pass/Fail | Call sid | Notes |
|---|---|---|---|
| C-1 | **PASS** | `CAb6bd961f` 13:03, build `58319e89bc65` | 3 options; `slots_presented=True slots_count=3`; record took (`3 spoken option(s) recorded as offered`); tail before the question, chunk 3/3 — "…if none of those suit. Any of those work?" |
| C-2 | **PASS** | `CAb6bd961f` (same call) | whole remainder in one answer — "quarter past seven **or** eight in the evening"; no repeats; correct day; exhausted day answered honestly ("no further times on that day") |
| C-3 | **RETIRED — invalid criterion** | `CAd075ea9673` 13:58, build `1db23a26bb94` | keypad never arms on a first readout (B-85). The call still proved the cap end-to-end: diary event `2026-09-01T17:00` = Number 1, voice selection worked, B-81 did not recur |

**Two things C-1/C-2 could NOT prove, and are not claimed:**

- **The trim never fired.** No `TRIMMED readout` line — the model stayed within
  three by itself. Like B-77 the over-read is stochastic, so its absence is not
  evidence. What proves the trim is the replay: the live five-option text
  through the real `_flush_slot_buf`, five before, three after.
- **B-79c was not exercised.** JV asked with `day_window=1`, so only one day
  came back and there was no other day to wander into. It rests on the
  parent-vs-fix repro (Wednesday offered; "anything else that day" answered
  Tuesday on the parent, Wednesday on the fix) plus four tests. Live exposure
  comes on VE/Theorem, where sweeps return several days.

**If C-1 or C-2 fails, do not port.** VE and Theorem are gated live lines.
