# Theorem — go-live run sheet

**Branch:** `theorem-onboarding` @ `6901ffb` · **Clinic:** `theorem_v3` · **Dial:** `+447380841468`
**Three calls.** Call 1 verifies everything fixed tonight. Calls 2–3 prove the
write path, which has never run.

---

## ⚠️ Deploy first

The service is on `e2a44f3`. The two-rung keypad ladder is in `6901ffb` and is
**not running yet** — Call 1 will fail step 4 against the old build.

Redeploy, then confirm at call cleanup:

```
[build_info] running build 6901ffb…
```

**Environment, confirm don't assume:**

- [ ] `SMS_ENABLED=true` — explicit. A stale `false` beats the code default.
- [ ] `TRANSFER_DISABLED` — **unset**, or Susie silently never transfers.

---

# CALL 1 — everything fixed tonight · no write

One continuous call. Covers six fixes; abort before the booking.

| # | Say | PASS | FAIL |
|---|---|---|---|
| 1 | *"I'd like to book an appointment"* | Asks which clinic | — |
| 2 | **Don't answer.** *"Should I take ibuprofen, ice or heat in the meantime?"* | The question is **answered** — deflecting to the practitioner is fine | *"Did you say the Awlstuh clinic?"* → **T-13 regressed** |
| 3 | *"Are you a real person?"* | Opens with **"No"** | Opens with "Yes" → **T-0 regressed**, hard fail |
| 4 | She re-asks the clinic. **Mumble something unintelligible** | **Keypad, immediately** — *"…press 1 for Awlstuh, or 2 for Redditch."* | Any spoken re-ask, especially *"did you say the Awlstuh clinic?"* → **the three-rung ladder is back** |
| 5 | Press **5** | Re-prompts, keypad **stays armed** | Silence, or a clinic gets picked |
| 6 | Press **1** | Alcester confirmed | — |
| 7 | *"Next week"* → she asks mornings or afternoons | — | — |
| 8 | Answer **one word**: *"afternoons"* | Heard, slots offered | `slot fragment ignored` in log → **T-15 regressed** |
| 9 | *"And just a shockwave on its own?"* | Price answered, ~1–2 sentences | `first-turn name extracted: Own` → **T-7 regressed** · over ~8s → **T-5 regressed** |
| 10 | Pick a slot. Name: *"Quentin Roche"* | — | — |
| 11 | Phone step | **Digits spoken** — *"is oh seven five oh two…"* | Number never spoken → **T-4 regressed** |
| 12 | *"Shall I go ahead and book that in?"* | — | **ABORT** — *"Actually, let me check my diary."* |
| 13 | *"Actually, put me through to Mark"* | Transfer initiated | — |

**Then grep the log. All three must be ABSENT:**

```
slot fragment ignored
first-turn name extracted
Haiku unknown non-question
```

**And this must be PRESENT, with a sid:**

```
[ms_conn] staff notify SMS sent → +447870166861 (sid=SM…)
```

`⚠️ staff notify SMS NOT SENT` means Mark was never told about a caller who
asked for him — **T-6**, and a real failure, not a log nit.

**Warmth check.** If Susie now sounds clipped — bare *"Eighty-five pounds."* —
that is a fail. T-5 was meant to stop the lecture, not the manners.

---

# CALL 2 — the first real booking · **this writes**

The one that matters. Book an **Alcester** assessment and say **yes** at *"shall
I go ahead and book that in?"*.

Book **by clinic, not by practitioner** — T-9 means Mark and Leanne have no
Acuity calendar ID, so naming them fails for a reason unrelated to what you are
testing.

- [ ] Susie confirms the booking in words
- [ ] Log shows the **`book_appointment` tool call** — not just availability GETs
- [ ] **The appointment exists in Acuity.** Open the calendar. Do not trust the transcript
- [ ] Surname spelled as you gave it
- [ ] Phone matches the number she read back
- [ ] **Confirmation SMS arrives**

> **If the appointment is not in Acuity, stop. Do not go live.** A call that
> sounds perfect over a booking that never happened is this system's worst
> failure mode, and it is the only one a caller cannot detect.

**Keep this appointment** — call 3 acts on it.

---

# CALL 3 — reschedule, then cancel · **this writes**

Both in one call, which also leaves Mark's calendar clean.

**Reschedule:**
- [ ] Lookup finds the call-2 appointment
- [ ] Alternative slots offered
- [ ] Confirmed in words
- [ ] **Acuity shows the new time and only ONE appointment** — the old slot released

**Then, same call, cancel it:**
- [ ] Cancels, confirms in words
- [ ] **Acuity shows it cancelled**
- [ ] Cancellation SMS arrives

**Reconcile Acuity afterwards.** Nothing from tonight should remain on the
calendar.

---

## Known-open — expect these, don't stop for them

| | |
|---|---|
| **T-16** | Naming a clinic in the question still draws *"which clinic?"* — costs one turn |
| **T-2** | Two summary rows per call. Inert while `SHEETS_ENABLED` is off |
| **T-12** | Every abandoned call texts the caller — **owner decision, still unanswered** |
| **T-1** | A factual question asked with a timing preference can still be swallowed |
| **T-3** | No watchdog after a bare FAQ answer |
| **T-8** | A TTS chunk can split a word (*"well. Being"*) |
| **T-9** | No calendar ID for named practitioners |
| **T-14** | *"yeah but"* can read as booking assent |

None of these blocks going live. **T-12 is the one to decide before real
patients call** — right now anyone who rings, asks a price and hangs up gets an
unsolicited text from the clinic.

---

## Go/no-go

**Go** if: Call 1 clean, Call 2's appointment is **in Acuity**, Call 3 leaves
the calendar clean.

**No-go** if: Call 2's booking is not in Acuity · Susie claims a booking that
did not happen · the disclosure answers "Yes" · the keypad never appears.

**After go-live, day one:**

1. Decide **T-12**.
2. Find out whose number `OBS_ALERT_SMS_TO` is — every call scoring under 4
   pages them.
3. Watch for `⚠️ staff notify SMS NOT SENT` — it means a patient asked for a
   human and nobody was told.
