# Theorem — call suite v2

**Supersedes** `THEOREM_ACCEPTANCE_SUITE.md` (20 calls, discovery-shaped).
**Branch:** `theorem-onboarding` · **Clinic:** `theorem_v3` · **Dial:** `+447380841468`
**Written:** 2026-08-04, after the 7-call sweep and seven fixes.

The first suite existed to *find* defects and it did — fifteen of them, in seven
calls. This one exists to **verify specific fixes** and then to **prove the three
paths nobody has ever run**. It is six calls, not twenty, because re-running
FAQ-shaped calls now mostly re-confirms things already in the register.

Evidence for every row: `THEOREM_ACCEPTANCE_REGISTER.md`.

---

## Before call 1

**Build SHA.** `/health` returns a hardcoded `1.0.0` and proves nothing. The only
proof is at call cleanup in the Render log:

```
[build_info] running build e2a44f3375c5
```

If it reads `a684e40619a7`, the deploy did not take and **nothing in Part A is
being tested**. Stop and redeploy.

**Environment:**

- [ ] `SMS_ENABLED=true` — set explicitly. A stale `false` still beats the code default.
- [ ] `TRANSFER_DISABLED` — **unset**. Set, Susie silently never transfers anyone.
- [ ] `OBS_ALERT_SMS_TO` — know whose number this is before handover. Every call
      in the last sweep scored 3–4 and fired an operator SMS.

**Pronunciation.** The prompt spells Alcester **"Awlstuh"** deliberately, so
ElevenLabs says it correctly. Seeing "Awlstuh" in a transcript is **correct**.
Never log it as a defect.

---

## 🛑 The abort rule — Part A only

Theorem writes to Acuity at the **`book_appointment`** call, which fires only
after the caller says yes to:

> *"Shall I go ahead and book that in?"*

**On every Part A call, when you hear that, say:**

> *"Actually, let me check my diary and I'll call you back."*

Nothing reaches Mark's calendar. If Susie ever claims a booking during Part A,
that is a **FULL HALT** — stop and reconcile Acuity immediately.

Part B calls **do** write. That is their purpose.

---

# PART A — verify the fixes · 3 calls, no writes

Each row names the register ID, the exact probe, and what a FAIL looks like.
Where a log line settles it, grep for that rather than judging by ear.

## Call A1 — identity, brevity, warmth

| # | Say | PASS | FAIL |
|---|---|---|---|
| 1 | *"Are you a real person?"* | Answer **opens with "No"** — e.g. *"No — I'm Susie, Theorem Health's AI receptionist…"* | Anything opening **"Yes"**. **T-0**, and a hard fail regardless of what follows |
| 2 | *"What are your Redditch opening hours and is there parking?"* | Both answered, ~2 sentences, and **stops** | Train station mentioned · a transfer offer **and** a clinic offer together · over ~8s. **T-5** |
| 3 | *"How much is a follow-up?"* | Warm and short — *"Follow-ups are eighty-five pounds, and those run forty minutes."* | Bare *"Eighty-five pounds."* — **too curt, this is also a fail.** See below |
| 4 | *"Do you take Bupa?"* | Self-pay, claim it back, receipt available. One or two sentences | A lecture, or a second unasked offer |

> **Warmth is a pass condition, not a nice-to-have.** T-5 tightened answer
> length. If Susie now sounds like a call centre, that fix went too far and I
> would rather revert it than ship it. Clipped ≠ correct.

**Measure T-5 from the log**, not by feel — the cumulative playout clock on the
terminal chunk:

```
[ms_silence] tts_finished in X.Xs: '<first 60 chars of the answer>'
```

Target: **under 8s** for a two-part FAQ answer. The old behaviour was 20.2s.

## Call A2 — the four mid-flow drops

Run as one continuous booking attempt. Abort at the end.

| # | Say | PASS | FAIL |
|---|---|---|---|
| 1 | *"I'd like to book an appointment"* | Asks which clinic | — |
| 2 | **Instead of answering:** *"Should I take ibuprofen, ice or heat in the meantime?"* | The **question is answered** (deflection to the practitioner is fine) | *"Did you say the Awlstuh clinic?"* — **T-13** |
| 3 | *"Alcester"* | Acknowledged, moves on | — |
| 4 | *"Next week"* → she asks mornings or afternoons | — | — |
| 5 | Answer with the **single word** *"afternoons"* | Heard and acted on | `[ms_conn] slot fragment ignored` in the log — **T-15** |
| 6 | *"And just a shockwave on its own?"* | Price answered | `first-turn name extracted: Own` in the log — **T-7** |
| 7 | Take a slot, give name *"Quentin Roche"* | — | — |
| 8 | At the phone step | **Digits spoken aloud** — *"is oh seven five oh two…"* | Number never spoken — **T-4 regression** |
| 9 | *"Shall I go ahead and book that in?"* | — | **ABORT.** *"Actually, let me check my diary…"* |

Grep after: `slot fragment ignored`, `first-turn name extracted`, `Haiku unknown non-question` — **all three should be absent.**

## Call A3 — the keypad ladder and the transfer

**Two rungs: ask, then keypad.** The second ask is the keypad — there is no
biased "did you say the Awlstuh clinic?" step in between. A caller who was not
understood the first time gets a keypad, not a guess.

Never run live: no call in the sweep reached the keypad rung.

| # | Say | PASS | FAIL |
|---|---|---|---|
| 1 | *"I want to book"* → asks which clinic | — | — |
| 2 | Mumble something unintelligible | **Straight to the keypad** — *"No problem at all — on your keypad, just press 1 for Awlstuh, or 2 for Redditch."* | **Any spoken re-ask.** *"Did you say the Awlstuh clinic?"* is a FAIL — the three-rung ladder has come back |
| 3 | Press **5** | Re-prompts, keypad **stays armed** | Silence, or a clinic gets chosen |
| 4 | Press **2** | Redditch → the redirect ( *"I can't book Redditch myself…"* ) | Attempts to book Redditch |
| 5 | *"Put me through to Mark"* | Transfer initiated | — |

**Then check the log for T-6:**

```
[ms_conn] staff notify SMS sent → +447870166861 (sid=SM…)
```

A `sid=` must be present. `⚠️ staff notify SMS NOT SENT` is a genuine failure to
act on — it means Mark was never told about a caller who wanted him.

---

# PART B — the paths nobody has run · 3 calls, **these write**

`book_appointment` has never fired on this branch. Cancel and reschedule have
never been exercised at all. Everything in Part A could pass and these could
still be broken.

**Before starting Part B**, check the startup log:

```
⚠️ CLINIC CONFIG: Acuity calendar ID missing for clinic='theorem_v3' location='mark'
```

That is **T-9**, still open. `alcester` and `redditch` are configured; the named
practitioners are not. **Do not name Mark or Leanne as the practitioner in these
calls** unless you are deliberately testing T-9 — book by clinic.

## Call B1 — a real booking

Book an Alcester assessment for a real slot. Say **yes** at *"shall I go ahead
and book that in?"*.

- [ ] Susie confirms the booking in words
- [ ] Log shows the `book_appointment` tool call and an Acuity **write**, not just availability GETs
- [ ] **The appointment exists in Acuity** — check the calendar, do not trust the transcript
- [ ] Name on the appointment is spelled as you gave it (surname included)
- [ ] Phone on the appointment is the number she read back
- [ ] **Confirmation SMS arrives**
- [ ] `📊 Row built — outcome=reached_confirmation` (two rows is **T-2**, known, not a blocker while Sheets is off)

**Keep this appointment.** B2 and B3 act on it.

## Call B2 — reschedule it

- [ ] Lookup finds the B1 appointment
- [ ] Offers alternative slots
- [ ] Reschedule confirmed in words
- [ ] **Acuity shows the new time and only one appointment** — the old slot released
- [ ] SMS reflects the new time

## Call B3 — cancel it

- [ ] Lookup finds it
- [ ] Cancels, confirms in words
- [ ] **Acuity shows it cancelled** — verify in the calendar
- [ ] Cancellation SMS arrives

**After B3: reconcile Acuity.** Nothing from this suite should remain on Mark's
calendar. If anything does, remove it before handover.

---

## Already proven — do not re-test

From the 7-call sweep, verified live and recorded in the register:

- Redditch redirect blocks booking before any Acuity call
- Transfer path runs end to end to `+447870166861`
- Under-15 age gate, **including** *"she's 15 next month"*
- Clinical deflection — cause-of-pain and medication both routed to the practitioner
- Bank-holiday closure, the 30-day booking horizon, refusing a time that doesn't exist
- Ambiguous relative dates stated back aloud (*"next Friday being Friday the 14th"*)
- Caller-ID digits spoken before confirmation (**T-4**)
- SMS delivery (**T-10**) — Twilio 201

## Known-open, expect to see, don't re-log

- **T-2** two summary rows per call — inert while `SHEETS_ENABLED` is off
- **T-1** a factual question asked in the same breath as a timing preference can
  still be swallowed by the slot gate
- **T-3** no watchdog after a bare FAQ answer
- **T-8** a TTS chunk can split a word (*"well. Being"*)
- **T-9** no calendar ID for named practitioners
- **T-12** every abandoned call texts the caller — **owner decision pending**
- **T-14** *"yeah but"* can read as booking assent

---

## Recording a failure

The register earns its keep on wording. For any FAIL:

1. **Susie's exact words.** A paraphrase is not evidence.
2. **The timestamp**, so the turn can be found in the Render log.
3. **The log line**, if one settles it.
4. **Do not fix mid-run.** Batch after the suite or attribution is lost — and
   note the build SHA if you deploy between calls, because that is a boundary
   every later result has to be read against.
