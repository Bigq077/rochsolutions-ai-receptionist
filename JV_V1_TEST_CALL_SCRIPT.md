# JV_v1 Test-Call Script — pre-commit validation

**Dial:** `+447367002651` (the official JV inbound line → routes to `jv_v1`).
**Deployment must have:** `MEDIA_STREAMS_CLINIC_ID=jv_v1` and `google_tokens` connected
(for booking calls). Greeting/parity calls work without Google; booking writes need it.

**Why this script:** the template prompt and the free-form-loop routing (Stages 1–2)
are offline-verified only. These calls confirm JV behaves like theorem_v3 **minus**
the two-clinic location step. Listen for the ❌ FAIL markers — they're the specific
ways the routing could break.

Run each scenario, tick PASS/FAIL, note exact wording on any fail. Don't fix mid-run —
batch fixes after the sweep (per the established discipline).

---

## 1. Greeting + single-location (the routing linchpin)
1. Call. **PASS:** greeting is *"Hi there, I'm Susie, Joint Venture Physiotherapy's
   AI receptionist — how can I help you today?"*
   ❌ FAIL: any "press 1 to speak to Mark", any Theorem/Alcester/Redditch wording, or
   a generic demo greeting → routing/greeting branch wrong.
2. Say *"I'd like to book an appointment."*
   **PASS:** Susie asks about **modality** (come into the Bolton clinic / remote /
   home visit) — NOT "which clinic" and NOT "Awlstuh or Redditch".
   ❌ FAIL: any "which clinic / Alcester or Redditch?" → single-location suppression
   (Stage 2) not working — the #1 thing this whole change had to get right.

## 2. Booking end-to-end + full-name capture (#2)
3. Continue: pick in-clinic, give a day/time preference.
   **PASS:** Susie offers real Bolton slots within JV hours (Mon–Fri evenings, Sat
   morning), one question per turn, no spoken slot-selection reasoning.
4. Pick a slot. Susie asks **"Could I take your first name and surname?"**
   Give e.g. *"Sarah Jenkins."*
   **PASS:** she reads back **only "Sarah"** (never the surname) for confirmation.
   ❌ FAIL: asks first name only, or reads back the surname.
5. Give/confirm phone, confirm the readback, let it book.
   **PASS:** *"All booked — you're in for [day] the [date] at [time]. I've just sent
   you a confirmation text. We'll see you then — take care."* (no "reply with your
   full name" request). Check the **Google Calendar event** = full name "Sarah
   Jenkins", phone, Bolton.
   ❌ FAIL: event shows first name only, or closing asks for the name by text.

## 3. Reschedule / cancel — ack, skip-redundant-Q (#1), stepping (#5)
6. New call: *"I need to cancel my appointment."*
   **PASS:** Susie says exactly **"No problem at all."** then asks for the number
   (*"Could I take the number you booked under? Or just say use this number…"*).
   ❌ FAIL: bundles a question onto the ack, or asks "which clinic".
7. Give the number. Susie reads back the found appointment.
   - Because you already said "cancel" up front, **PASS:** she goes **straight to the
     cancel readback** — she does **NOT** ask "would you like to reschedule or cancel?"
     (that's port #1). ❌ FAIL: asks reschedule-or-cancel anyway.
8. (If the test number has 2+ upcoming bookings) say *"no, that's not the one"* to the
   first read-back. **PASS:** Susie steps to the next: *"I also have one on [date] —
   is that the one?"*; if none match, *"That's the only upcoming appointment I can see
   under that number — let me put you through to the team."* (port #5).
9. Confirm the right one → *"shall I go ahead and cancel that?"* → yes → cancellation
   confirmed + the Google event is removed.

## 4. Reschedule path
10. New call: *"Can I move my appointment?"* → ack **"Of course, let's get that moved
    for you."** → number → found → because intent ("move") was stated, goes straight to
    asking timing preference (no reschedule-or-cancel question) → new slot → *"shall I
    go ahead and move that?"* → confirmed + event moved.

## 5. Clinic-fact accuracy (JV data, not Theorem)
11. Ask each, expect JV answers (not Theorem's):
    - *"How much is an appointment?"* → JV in-clinic price.
    - *"Where are you?"* → Flexspace Bolton, BL3 2NZ, access-code keypad entry.
    - *"Do you do remote?"* → yes, video/phone £40.
    - *"What are your hours?"* → Mon–Fri evenings, Sat 09:30–13:30.
    ❌ FAIL: any Alcester/Redditch/Mark/£75/Acuity Theorem facts leak in.

## 6. Transfer target
12. *"Can I speak to a person?"* → Susie offers transfer. Confirm it dials the intended
    human line (currently `+447586605462` — **verify this is correct before go-live**).

## 7. (Optional) End-of-day digest
13. After a booking, confirm (with SMTP + `digest.email_to` set) the digest email lands
    after the configured time (default 21:30 London) listing the day's bookings.

---

### Quick pass/fail gate for commit
The **must-pass** set before committing the routing work: **1, 2, 4, 5, 6, 7, 11.**
If those are green, JV is behaving as theorem_v3-minus-location and the batch is safe to
commit. Items 8/13 depend on test-data / SMTP being set up and can follow.
