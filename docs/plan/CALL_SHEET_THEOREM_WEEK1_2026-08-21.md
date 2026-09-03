# Theorem week-1 review + call sheet · 2026-08-21

**Clinic / dial:** `theorem_v3` on `theorem-onboarding` · **`+447380841468`**
**Build must read:** `[build_info] running build 6647acf`
**Source:** read from the obs corpus (`demo_obs`), not from memory.

Per the plan, this sheet is written **from what the corpus actually shows**, not
blind. What it shows changes the sheet substantially — read section 1 first.

---

## 1. What the week-1 corpus actually says

`theorem_v3` has **40 captured calls, 8 Aug – 19 Aug**. But the go-live seam
matters: the calls from 8–12 Aug carry build SHAs belonging to *worktree* branches
(`ca2b3c1`, `62d6bbb`, `952131a`, `6759ad5`…) — those are our own test calls
before Mark went live. Only calls from **14 Aug** onward run
`theorem-onboarding` builds.

**Real production traffic since go-live is 5 calls, not a week of them:**

| When | Dur | What happened | Outcome |
|---|---|---|---|
| 14 Aug 08:16 | 10s | pressed **1** → transferred to Mark | ok |
| 14 Aug 13:03 | 339s | **real reschedule, booked** — Acuity `1754340504` | ✅ |
| 14 Aug 13:08 | 7s | pressed **1** → transferred to Mark | ok |
| 17 Aug 07:09 | 0s | hung up during greeting, `+234…` — spam | n/a |
| 19 Aug 11:03 | 12s | pressed **1** → transferred to Mark | ok |

Three findings, in order of importance:

**(a) The good news is real.** Across all 40 calls, `booking_confirmed` and
`acuity_booking_id` **agree 100%** — 10 confirmed bookings, 10 Acuity ids, zero
disagreements. The single worst failure mode in this system (*the call sounded
perfect and the booking silently never happened*) has **not occurred once** on
Theorem. The one live AI-handled booking was a 22-turn reschedule that completed
cleanly.

**(b) Press-1 is the dominant caller path — 3 of 5 live calls.** Most people
ringing Mark want Mark. That is working as designed, but it means the AI is
carrying far less of this line than the "one week live" framing suggests, and
**the press-1 path deserves more test weight than the booking path.**

**(c) Volume is low and the line has been silent since 19 Aug.** Two days with no
captured call. Most likely genuinely quiet, but see call 1 — it is worth ten
minutes to prove capture is still running rather than assume it.

> ⚠️ **A measurement defect found while doing this review — see `B-72`.**
> `transfer_attempted` is **`False` on all 40 calls**, including the three where
> Susie demonstrably said *"Transferring you to Mark now."* The only writer is
> `app/routes/twilio.py:1047`, which sits on the **failed**-transfer branch —
> a *successful* transfer never sets it, and the WS press-1 path
> (`connection.py:6541`) never sets it at all. `transfer_requested_by_caller` is
> set in the session but never persisted.
>
> So in the store **a successful transfer is indistinguishable from a hang-up**,
> and the LLM judge is told no transfer was attempted on calls that were
> transferred. On Theorem specifically this zeroes out the most important
> operational number there is: how many patients reach Mark vs Susie. The `20
> abandoned` in the corpus cannot be trusted until this is fixed.

---

## 2. Pre-flight

1. Render log for the Theorem service → must read **`6647acf`** (today's bypass
   deploy). If it reads `319733a`, the deploy has not landed.
2. Confirm `SHEETS_ENABLED=true` and `SMS_ENABLED` explicit on this service.
3. ⚠️ Theorem's config is a **hardcoded dict in `clinic_config.py`**, not
   `clinic.json`. If you go looking for a Theorem fact, look there.

---

## Call 1 — press 1, the path most callers actually take ⭐

Ring the line and, at the greeting, **press 1**.

- [ ] She says *"Transferring you to Mark now — one moment"*
- [ ] **Mark's phone actually rings** and connecting works
- [ ] ❌ **FAIL** if she says it and nothing rings — three live callers took this
      path and we have no record confirming any of them reached him
- [ ] Afterwards, confirm the call **appears in the obs store at all** — that
      settles finding (c)
- [ ] SID: `________________`

---

## Call 2 — book, end to end

Book an ordinary appointment.

- [ ] It lands in **Acuity** with the right name, day and time
- [ ] ❌ **FAIL** if the surname is wrong. The surname is collected *after* the
      booking tool blocks, so it is **never read back** — two live calls in a row
      wrote a wrong surname to the calendar. Check the spelling in Acuity
- [ ] She does **not** ask what the appointment is about — the reason question is
      deliberately suppressed on Theorem only
- [ ] SID: `________________`

---

## Call 3 — the location ladder

Ask for an appointment **in Redditch**.

- [ ] She handles the location cleanly and does not leave dead air after the
      acknowledgement
- [ ] ❌ **FAIL** if she acknowledges the location and then says nothing —
      the clinic answer is resolved at four separate sites and a bare ack with no
      turn behind it is the known failure shape here
- [ ] SID: `________________`

---

## Call 4 — cancel, and the word that broke it

Book a throwaway appointment, then ring back and say **"I'd like to cancel it"**.

- [ ] She understands "cancel" — ❌ **FAIL** if she hears anything like
      *"can't see"* or steers toward the rotator cuff. "cancel it" was once
      transcribed as *"can't see the rotator cuff"*
- [ ] **It is gone from Acuity**
- [ ] ❌ **FAIL** if she asks you to confirm more than twice
- [ ] ❌ **FAIL** if she apologises for failing *after* it succeeded
- [ ] SID: `________________`

---

## Call 5 — the bypass has somewhere to ring (new, `B-71`)

Shipped today, **never exercised on a live line.** Mark has been live since
14 Aug with no way to divert his own number without a deploy.

From **Mark's handset (`+447870166861`)**, text **`OFF`** to `+447380841468`,
then ring the clinic line from another phone.

- [ ] The text gets a confirmation reply
- [ ] The call **rings Mark first** with a press-1 whisper
- [ ] Not answering falls through to Susie after ~20 seconds
- [ ] Text **`ON`** — next call goes straight to Susie
- [ ] ❌ **FAIL** if OFF is acknowledged and Susie still answers immediately
- [ ] SID: `________________`

---

## 3. What to do about `B-72`

Not urgent, not a caller-facing defect — but it is the reason this review is
thinner than it should be, and it gets worse the longer the corpus grows around
it. The fix is small: persist `transfer_requested_by_caller` into the record and
set `transfer_attempted` on the WS transfer path, not only on the failure branch
in `twilio.py`. Canonical-first, as ever.

Do **not** detect transfers by matching the sentence *"Transferring you to Mark"* —
matching one literal of model speech has broken three times in this repo, and
Gate 5 can rewrite or delete that very line.
