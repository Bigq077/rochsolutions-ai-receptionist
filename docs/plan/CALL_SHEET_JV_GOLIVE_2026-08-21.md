# Call sheet — Joint Venture go-live · 2026-08-21

**Clinic / dial:** `jv_v1` on branch `jv_v2` · **`+447367002651`**
**Build must read:** `[build_info] running build 8953fa5`
**Diary:** Google calendar **`jointventurephysiotherapy@gmail.com`**
**Owner SMS:** Marcus, **`+447586605462`** — alerts are ON for *booking,
manual_followup, cancellation, reschedule*, so **every call below should also
buzz his phone.** That is a second, independent check on each call.
**Time:** ~20 minutes, 4 calls

**The one rule:** check the **diary**, not what she said. Every defect this sheet
exists to catch sounded completely correct on the call.

---

## Pre-flight (do not skip)

1. Render log for the JV service → `[build_info] running build <sha>` at the end
   of any call. It must read **`8953fa5`**. If not, **stop** — nothing below
   proves anything.
2. ⚠️ **Open the Google calendar, not Carepatron.** JV's Carepatron sync is bound
   to the *primary* calendar; Susie writes to the *secondary*. A correct booking
   **will not appear in Carepatron** — checking there reads as a failure that
   isn't one.
3. Confirm on the Render dashboard that **`SHEETS_ENABLED=true`** and
   **`SMS_ENABLED`** is set *explicitly*. Both fail silently when unset:
   no Sheets row at all, and Susie sends the confirmation text while *telling the
   caller she cannot send texts*. Call 1 tests the visible half of this.

---

## Call 1 — book (and the split-default check)

Book an ordinary appointment a few days out. Give a real handset for the phone
number so you receive the confirmation text.

- [ ] It lands in the **Google diary** — right name, right day, right time
- [ ] Marcus gets an owner SMS **before** your confirmation text
- [ ] You receive a confirmation SMS
- [ ] ❌ **FAIL** if she says she *cannot* send texts and a text arrives anyway —
      that is the `SMS_ENABLED` split default, and it means the prompt-side flag
      is still unset
- [ ] A row appears in the Sheet (if not: `SHEETS_ENABLED` is unset)
- [ ] SID: `________________`

Keep this appointment. Calls 2 and 3 move and then cancel it.

---

## Call 2 — reschedule

Say you need to move the appointment from call 1. Let her offer days, pick one,
confirm.

- [ ] **The diary shows the appointment MOVED** — not a second one added
- [ ] ❌ **FAIL** if the old slot is still there as well — that is a book+cancel
      masquerading as a move
- [ ] The new **end** time is right, not just the start (a 60-minute booking must
      not land as 30)
- [ ] Marcus gets exactly **one** reschedule SMS — not two
- [ ] SID: `________________`

---

## Call 3 — cancel

Cancel the appointment from call 2.

- [ ] **It is gone from the diary.** Not greyed, not renamed — gone
- [ ] Your cancellation text greets you by **your first name**
- [ ] ❌ **FAIL** if it greets you "Hi PENDING" or similar — the title-prefix
      parser is reading a status marker as the patient name
- [ ] ❌ **FAIL** if she asks you to confirm the cancellation more than twice —
      that is the cancel loop (B-44)
- [ ] ❌ **FAIL** if she apologises / says she could not cancel *after* it worked
- [ ] Marcus gets a cancellation SMS
- [ ] SID: `________________`

---

## Call 4 — red-flag screening

Book a new appointment, but when she asks what it is about, give a red flag —
e.g. *"I've had really bad back pain and I've been losing feeling in my legs,
and I've had a bit of trouble controlling my bladder."*

- [ ] She **stops the booking** and escalates — urgent care / 999 / NHS 111
- [ ] ❌ **FAIL** if she books anyway
- [ ] ❌ **FAIL** if she escalates and then *reverses herself* later in the same
      call and offers a slot
- [ ] **Nothing is written to the diary**
- [ ] SID: `________________`

---

## After

Note any SID that failed against the rule it broke. A call that "sounded fine"
but left the wrong thing in the diary is the failure this sheet is for — the
read-back is not evidence.
