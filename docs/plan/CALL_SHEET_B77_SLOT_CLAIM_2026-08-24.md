# Call sheet — B-77 slot over-claim · 2026-08-24

**Clinic / dial:** `jv_v1` · **`+447366263180`** — the demo line, service
`low-latency-joint-venture`, branch `latency-eval`.
🚫 **Never `+447367002651`** (Marcus, live) and **never `+447380841468`**
(Mark, live). Neither has this fix.

**Build must read:** `[build_info] running build 91a8db265882` in the Render log.
**Time:** ~12 minutes, 3 calls. C-2 is the important one.
**Diary writes:** only call **C-3**. C-1 and C-2 never reach a booking.

**What this closes:** `8de7e7d0` (the over-claim), `a74f60c8` (the multi_day
"that day" gate) and **`91a8db26` (B-78 — only two of a day's slots were ever
reachable)**.

---

## ⚠️ Read this before you dial — the obvious test proves nothing

The defect is **stochastic**. On CA98557584dc the formatter emitted "And I've
a few others that day if neither suits" over a two-slot day. It did **not** do
that on most calls. So:

> **Hearing no "few others" on a test call is NOT evidence the fix worked.**
> That was already the usual outcome.

The deterministic proof is already done — the live call's exact text replayed
through the real `_flush_slot_buf`, tail present before, stripped after, plus
26 regression tests. **What these calls are for is the opposite: proving the
fix did not break the slot readout, and exercising the direction that only
runs live.**

## ⚠️ And the direction you will naturally hit is the WRONG one

`jv_v1` does not use Acuity. Its availability goes through the generic tail of
`_exec_check_availability` → `_cap_presented_slots`, which trims to **2 days ×
1 time** and sets `more_times` whenever it trimmed. Bolton runs evenings
(16:30/17:00/17:30 open, last appointment 20:30), so a day holds ~9 slots and
**`more_times` is true on essentially every open-ended request.**

So a normal "have you got anything?" call exercises the *append* path, not the
strip path that was the actual bug. **C-2 exists to reach the strip path
deliberately.** Do not skip it and call this verified.

---

## Pre-flight (do not skip)

1. **Build.** Render → `low-latency-joint-venture` → logs. Find
   `[build_info] running build <sha>` at the end of any call. It must be
   **`91a8db265882`** (build_info prints 12 chars). `/health` returns a hardcoded `1.0.0` and proves nothing.
   If the sha is older, **stop** — nothing below means anything.
2. **Which calendar.** The demo line is expected to write to Quentin's demo
   calendar, not Marcus's. **Confirm before C-3** — if it resolves to a real
   JV calendar, skip C-3 entirely rather than write a fake patient into a live
   diary.
3. **No SMS on this line.** Corrected from the 11:11 call — the log shows
   `[sms] SMS_ENABLED is off — outbound SMS suppressed`, so C-3 sends nothing.
   (Sheets also fails here on a real config error: `GOOGLE_SERVICE_ACCOUNT_JSON
   is not valid JSON — Invalid \escape: line 5 column 46`. Known-accepted on
   the demo line, but it is a genuine bad escape in the key, not "not set".)
4. Sheets / `EVAL_STAFF_SMS_TO` warnings on this line are known-accepted —
   ignore them.

---

## C-1 — the append direction, and no double tail

**Goal:** the tail is spoken **once**, and the multi_day gate holds.

| You say | Expect |
|---|---|
| "Hi, I'd like to book an initial assessment please." | asks in-clinic at Bolton or remote |
| "In clinic please." | asks when suits you |
| "Whenever you've got — as soon as possible." | reads the numbered options |

**PASS:**
- Two numbered options read out cleanly, each ending in a full stop, with a
  clear pause before "Number 2".
- If it names **two different days** → **no "few others that day" tail at
  all.** ← this is `a74f60c8`. A dangling "that day" after two days is a FAIL.
- If it names **one day** → the tail may appear, **exactly once**, and the
  wording must match the option count: two options → "if neither suits";
  three → "if none of those suit". "Neither" over three options is a FAIL.
- No dead air after the closing question.

**FAIL and stop:** the tail spoken twice, or "that day" after two days named.

**Log check:** `[ms_gate5] slot buf: appended more-times tail` should appear
only on a single_day turn.

---

## C-2 — walking a day's slots  ⭐ the one that matters

**Goal:** B-78. Before `91a8db26` a caller could reach only **two** of a day's
times, no matter how often they asked. On CA7cd9bed5 Tuesday 1 Sept held five
(17:00, 17:45, 18:30, 19:15, 20:00) and three were never offered.

Ask for one specific day, then keep asking for more. Bolton evenings hold ~9
slots, so any weekday works.

| You say | Expect |
|---|---|
| "Can I book an initial assessment, in clinic?" | asks when |
| "Have you got anything on **Tuesday**?" | two times + "a few others that day" |
| "**Have you got anything else that day?**" | **two NEW times, never repeats** |
| same question again | two more NEW times |
| keep going until she stops offering new ones | — |
| once more | "I don't have any further times on that day — would you like me to look at a different day?" |

⚠️ **Use that exact wording.** "Anything **else** that day" routes to the
follow-up. "Another day" / "a different day" leave it entirely — those are
excluded by `utterance_requests_different_day`.

**PASS — all four:**
1. Every ask returns times you have **not already heard**. A repeat of an
   earlier pair is the B-78b loop and is a FAIL.
2. You can reach the **last** slot of the day (Bolton's is 20:30). Before the
   fix this was unreachable.
3. The "a few others that day" tail appears **while** more remain and **stops**
   on the final batch.
4. When the day is exhausted she says so in those words — she must never say
   "those are the two available slots".

**Log checks:**
```
[ms_llm] unspoken slot follow-up spoken call_sid=... text='On Tuesday ... I also have ...'
```
That line must appear **once per ask**. If it is absent, the deterministic path
did not run and the model answered — that is the original bug, still live.

You should also now see `[ms_llm] slot cache kept — awaiting slot selection`
where the 11:11 call logged `slot cache cleared on new turn`.

---

## C-3 — keypad and booking still land  ⚠️ writes to a diary

**Goal:** the slot map is now built from **reconciled** text. This is the one
thing the fix could plausibly have broken, and a green suite would not catch a
keypad that points at the wrong option.

| You do | Expect |
|---|---|
| "Initial assessment, in clinic, earliest you've got." | numbered options |
| **press `1` on the keypad** | takes option 1 — the time it read as Number 1 |
| give name "Test Patient" | asks for phone |
| confirm the calling number | reads the full booking back |
| "yes please" | "All booked…" |

**PASS:**
- Pressing `1` books the time actually read out as Number 1 — not Number 2, not
  a time never spoken.
- The read-back names the same day and time as the option you chose.
- Under ~3 s of silence between "locking that in" and the confirmation.

**Cleanup:** cancel it **by calling back and asking Susie to cancel** — not by
deleting it in the calendar. Deleting behind her back leaves the session and
the diary disagreeing.

---

## Recording the result

| Call | Pass/Fail | Call sid | Notes |
|---|---|---|---|
| C-1 | | | one day or two? tail present? |
| C-2 | | | did the REMOVED warning fire? |
| C-3 | | | keypad → correct slot? |

**If C-1 or C-2 fails, do not port to `theorem-onboarding` or
`vitaledge-onboarding`.** VE is the most exposed of the three — its
availability path never sets `more_times` at all, so any such sentence there is
false 100% of the time — but it is still a gated live line.
