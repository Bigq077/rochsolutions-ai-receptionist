# Call sheet — Vital Edge pre-go-live · 2026-08-21

**Clinic / dial:** `vital_edge` on `vitaledge-onboarding` · **`+447426779875`**
**Build must read:** `[build_info] running build e6ef845`
**Diary:** Google calendar **`vitaledgetherapy@gmail.com`** ("Vital Edge — Available")
**Owner SMS:** Jonathan, **`+447545862307`** — he gets a text on *every* booking
**Time:** ~35 minutes, 6 calls

**The one rule:** check the **diary**, not what she said.

**Second rule, specific to this clinic:** Vital Edge is **provisional**. Nothing
is ever confirmed on the call — Jonathan *is* the confirmation step. So on this
line the **owner SMS is as much the product as the diary entry**, and call 4
exists because of that.

---

## Pre-flight (do not skip)

1. Render log for the VE service → must read **`e6ef845`**. If not, **stop**.
2. Open the **Vital Edge — Available** calendar. Note it holds Jonathan's *booked
   work and personal entries*, not published availability — that is why this
   clinic runs `availability_mode: "diary"` and subtracts the calendar from
   working hours.
3. **Before call 1**, put a personal all-day or multi-hour entry in the diary for
   a weekday inside the next fortnight — e.g. block **10:00–15:00 Wednesday**.
   Call 2 checks she will not sell that time.
4. Confirm `SHEETS_ENABLED=true` and `SMS_ENABLED` set explicitly on this service.

---

## Call 1 — book, and the provisional wording ⭐

Book a **Neck, Back and Shoulders Massage** a few days out.

- [ ] The diary entry is titled **`PENDING CONFIRMATION — <name> — <service>`**
- [ ] Jonathan receives an owner SMS with name, phone, time, duration
- [ ] She tells you it is **subject to Jonathan's confirmation** and not finalised
      until he is in touch
- [ ] ❌ **FAIL** if she uses the word **"confirmed"**, "booked in", "all set" or
      any wording a caller would hear as final. On a provisional clinic that is a
      hallucinated confirmation, and it is the worst failure this line can produce
- [ ] ❌ **FAIL** if you receive a patient confirmation SMS saying it is confirmed
- [ ] SID: `________________`

---

## Call 2 — the diary reader must not sell blocked time ⭐ **the one that matters**

Ask for an appointment on the **Wednesday you blocked out in pre-flight**. Let her
offer times.

- [ ] **No offered time falls inside your blocked 10:00–15:00**
- [ ] ❌ **FAIL** if she offers any slot inside it — this is the defect the diary
      reader exists to fix. It once offered Jonathan's flight to Ibiza as a
      massage slot
- [ ] If she says nothing is available that day at all, that is a **pass** for
      safety but note it — over-blocking is a separate, lesser problem
- [ ] Book one of the times she *does* offer, and check it lands outside the block
- [ ] SID: `________________`

---

## Call 3 — 90 minutes must reach the diary as 90 minutes

Book a **Deep Tissue Massage** and, when she asks, choose **90 minutes** (£180).

- [ ] She accepts 90 without re-asking or refusing
- [ ] **The diary entry END time is 90 minutes after the start.** Check the end,
      not the start
- [ ] ❌ **FAIL** if the diary says 60. The wrong end time survives every verbal
      read-back — she will say "90 minutes" and write 60
- [ ] The owner SMS to Jonathan also says 90
- [ ] SID: `________________`

---

## Call 4 — the under-18 gate

Call and say you want to book a sports massage for yourself, and that you are
**16**.

- [ ] She **declines to book** and explains appointments are for 18+
- [ ] ❌ **FAIL** if she books anyway
- [ ] ❌ **FAIL** if she declines and then **carries on asking for a day and
      time** — refusing and then booking is worse than either
- [ ] **Nothing is written to the diary**
- [ ] Now repeat, but instead of an age say **"can I come at 16:00"** —
      she must treat that as a *time*, not an age, and book normally
- [ ] SID: `________________` / `________________`

---

## Call 5 — cancel

Cancel the booking from call 1.

- [ ] **It is deleted from the diary.** On this clinic a cancel must *remove* the
      entry, not just retitle it
- [ ] Your cancellation text greets you by **first name**, not "Hi PENDING" —
      the entry title starts with a status marker and the parser has taken it as
      the patient name before
- [ ] ❌ **FAIL** if she apologises or says she could not cancel *after* it worked
- [ ] SID: `________________`

---

## Call 6 — the bypass has somewhere to ring (new, `B-71`)

This is config that shipped today and has **never been exercised on a live line.**

From **Jonathan's own handset (`+447545862307`)**, text **`OFF`** to
`+447426779875`. Wait for the reply, then call the clinic line from a different
phone.

- [ ] The text gets a confirmation reply
- [ ] The inbound call **rings Jonathan's phone first**, with a whisper telling
      him to press 1
- [ ] Pressing **1** connects the caller to him
- [ ] Not answering falls through to Susie after ~20 seconds
- [ ] Now text **`ON`** — the next call goes straight to Susie again
- [ ] ❌ **FAIL** if the OFF text is acknowledged but Susie still answers
      immediately — that is exactly the state this fix was meant to end, and it
      would mean the block is not reaching the router
- [ ] SID: `________________`

---

## Not on this sheet, and why

**The forced failed-write alert (`B-70`).** The plan called for a sixth scenario
forcing a calendar-write failure to prove Jonathan is told when nothing reached
the diary. **There is no safe way to force that on a live line** — it needs a
revoked Google token or a repointed calendar id, i.e. a deliberate deploy that
breaks the clinic. Do not attempt it before go-live.

It is covered by `tests/regression/test_provisional_owner_text_marks_a_failed_write.py`,
which fails before the fix and passes after. It stays `U`-debt until a real
failure proves it, and the first genuine one will be self-announcing: the text
leads with **`⚠️ … NOT IN YOUR CALENDAR`**. Brief Jonathan on that wording — if he
ever sees it, the appointment needs adding by hand.
