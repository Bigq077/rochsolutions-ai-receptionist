# Vital Edge Therapy — Pre-Handover Test Call Matrix

**Purpose:** 12 calls that between them exercise every behaviour Susie has on the Vital Edge line. If all 12 pass, the number can be handed to Jonathan.

**Line under test:** `+447426779875` → `vital_edge` → provisional booking model.
**Owner ping / transfer target:** `+447545862307` (Jonathan's mobile).
**Branch:** `vitaledge-jv-parity` (fast-forward `vitaledge-onboarding` only after these pass).

Every expectation below was read out of the live config and engine, not assumed.

---

## Pre-flight (do these once, before Call 1)

| # | Check | Why it matters |
|---|---|---|
| P1 | Twilio webhook for `+447426779875` → VE Render service `/ms/incoming` | Otherwise no call reaches Susie |
| P2 | `TWILIO_PHONE_NUMBER` on VE's Render service = `+447426779875` | It is the **SMS from-number**, read per-send |
| P3 | Twilio **"Call status change"** field on the number = `https://<ve-host>/twilio/status` (POST) | Number-level config, NOT code. If blank: **no Sheets row and no follow-up SMS ever fire** |
| P4 | Google Calendar OAuth'd as Vital Edge's own account; `GOOGLE_CALENDAR_ID` set (or `calendar_id` correct) | Availability is *read* from the published calendar |
| P5 | Publish ≥6 real slots to the availability calendar across the next 14 days — include a **Tue/Wed/Thu/Sat**, one **same-day**, and at least one **non-o'clock** time (e.g. 18:10, 18:45) | Several calls depend on these existing |
| P6 | Confirm at least one published slot is **>14 days out** | Needed for the horizon test (Call 8) |
| P7 | `DIGEST_EMAIL_TO` / SMTP set if you want the 21:30 digest | Optional for handover |

> **Slot hygiene:** availability skips any event whose title starts `PENDING`, `BOOKED`, or `CONFIRMED`. After each booking test, delete the `PENDING CONFIRMATION — …` event or that slot stays consumed.

---

## What "pass" means on every single call

These are global. A breach on *any* call is a fail, even if that call was testing something else.

- **Never** says "all booked", "confirmed", "you're booked in", or that a confirmation text was sent.
- **Never** offers or accepts remote / video / phone appointments, or a home visit.
- **Never** says "Marcus", "Bolton", "physiotherapy", "MSK", "assessment", "acupuncture (as something we offer)", "Carepatron".
- **Never** appends a robotic closer: "Is there anything else I can help with?", "Would you like to book an appointment?" after an FAQ answer.
- **Never** invents a price for anything except Deep Tissue (£125 / 60 min, £175 / 90 min).
- **Never** promises a diagnosis, a cure, a recovery timescale, or a number of sessions.
- **Never** reads out an internal note (e.g. anything containing "TBC").
- Speaks times naturally: "ten past six", "quarter past seven" — never "six 10".

---

## Call 1 — Happy path booking (the one that matters most)

**Covers:** greeting · booking consent · service selection · timing · slot offer · full-name capture · phone via caller-ID · warm readback · provisional wording · PENDING calendar event · owner SMS **with Notes** · no caller SMS · Sheets row

**Say:** "Hi, I'd like to book a deep tissue massage." → give a day → pick a slot → give **first name and surname when asked** → accept "use this number" → say **yes** at the readback.
Somewhere mid-call, volunteer context: *"my lower back's been tight for months, worse after running."*

**Must hear**
- Greeting: "Hi there, I'm Susie, Vital Edge Therapy's AI receptionist — how can I help you today?"
- Asks timing preference **before** offering slots.
- Readback exactly once, opening "So that's…" / "Right, so…", ending **"shall I put that request through to Jonathan to confirm?"**
- After yes: *"I've noted your preferred time and sent it to Jonathan. Your appointment is subject to his confirmation — it isn't finalised until you hear from him via WhatsApp or phone… He'll also arrange payment with you beforehand, so there's nothing to pay right now."*

**Must NOT hear:** "all booked" · "confirmed" · "I've sent you a confirmation text" · a duration or a description of the treatment in the readback · the town ("at Kingston") in the readback.

**System checks**
- Calendar: new event titled `PENDING CONFIRMATION — <Full Name> — Deep Tissue Massage (60 min)`.
- Calendar description contains a **`Notes:`** line carrying the back/running context.
- Jonathan's phone receives:
  ```
  📅 Vital Edge Therapy — booking request (please confirm with the client)
  Name: … / Phone: … / Service: Deep Tissue Massage (60 min)
  Requested: <Day DD Mon at HH:MM>
  Notes: <the caller's context>
  Not yet confirmed — Susie told the caller you'll confirm directly.
  ```
- **Caller receives NO SMS.**
- Google Sheet gains a row with outcome `BOOK_PROVISIONAL`.

> ⚠️ The `Notes:` line is the newest fix. If it is missing, the caller's reason for booking is being dropped and Jonathan is ringing back blind — **fail the call**.

---

## Call 2 — Name capture under stress

**Covers:** first name mid-sentence · surname straggler · slot-words rejected as names · mid-name correction · name reused naturally

**Say:** Book. When asked for your name, reply **"It's Quentin"** and stop. When she asks for the surname, say **"Roch"** *after a pause*. Later, correct it once: *"Actually it's Roche, with an e."*
Then, at the slot question, answer **"Tuesday"** — check she does **not** capture "Tuesday" as a name.

**Pass**
- Captures both first name and surname; does **not** re-ask for the full name later.
- Never treats a day/time word ("Tuesday", "Thursday", "six") as a name.
- Accepts the correction and uses the corrected spelling in the readback.
- Uses the first name naturally, not in every sentence.

**Fail signals:** asks "and your surname?" twice · drops the surname · confirms a name you never said ("Did you say Quentin?" → "Thanks Quentin") when you gave it plainly.

---

## Call 3 — Phone capture (the "captcha" element)

**Covers:** "use this number" recognition · keypad DTMF fallback · star-reset · booking blocked when the phone step is skipped

**Say:** Book a slot. At the phone step, **decline** the caller-ID number ("no, use a different number"). Then key a number on the keypad. Press `*` mid-entry and re-enter.

**Pass**
- Offers the calling number first ("shall I use the number you're calling from?").
- On decline, converges on the **keypad** line ("type the number on your keypad").
- Captures the DTMF digits; does **not** read them back digit-by-digit.
- `*` clears and restarts entry.
- Talking mid-entry (with no digits typed) escapes gracefully.

**Also check:** she never mentions a door/entry keypad — Vital Edge has no entry code (that's a Joint Venture thing).

**System check:** the phone in Jonathan's SMS is the **keyed** number, not the caller ID.

---

## Call 4 — Deep treatment knowledge (the "which massage?" brain)

**Covers:** `treatment_guidance` · confident single recommendation · all five services · `if_unsure` fallback · pressure/comfort reassurance

Ask each, on one call, and listen for a **specific, reasoned** answer:

| You say | Must recommend |
|---|---|
| "Neck and shoulders from desk work" | **60-min Deep Tissue** |
| "I've started running, sore after training" | **Sports Massage** |
| "I'm stressed and not sleeping" | **Stress Buster** |
| "My jaw is tight, I clench at night" | **Facial Release** |
| "I honestly don't know what I need" | **60-min Deep Tissue** as the versatile starting point |
| "Will it hurt?" | Firm but never unbearable; pressure adjustable; you stay in control |

**Pass:** each answer names **one** best-fit treatment and briefly says *why*. She may name a close second only if it's a genuine toss-up.

**Fail signals:** "they're all good" · "they're basically the same" · "Jonathan will decide which one" (choosing the treatment is exactly her job) · recommending a physiotherapy assessment.

---

## Call 5 — Non-massage refusal (Jonathan does NOT offer these)

**Covers:** `other_services_line` · decline without callback · all five massages remain bookable

**Say, in turn:** "Do you do acupuncture?" · "What about reiki?" · "Do you offer psychotherapy or energy healing?" · "Are you a physiotherapist?"
Then: "Fine — can I book a **sports massage** instead?"

**Pass**
- Politely says those are **not** offered — Jonathan is a massage therapist.
- Does **not** book them, and does **not** take a name/number callback for them.
- Immediately books the **sports massage** on the normal flow (no "I'll take your details and Jonathan will call you back").

> This is the exact bug that shipped once: a live call had Susie say *"Jonathan does offer acupuncture, yes."* The onboarding document is wrong here; the config is right.

---

## Call 6 — Pricing and payment

**Covers:** Deep Tissue pricing · no invented prices · payment-in-advance · payment methods · **price-enquiry SMS**

**Say:** "How much is a massage?" → "And the 90 minute one?" → "How much is the Stress Buster?" → "How do I pay?" → **hang up without booking.**

**Pass (spoken)**
- 60 min = **£125**, 90 min = **£175**.
- Stress Buster: **no figure invented** — Jonathan confirms the price.
- Payment is **arranged in advance** with Jonathan; nothing is taken on the call. Cash / debit / credit via Worldpay, receipts provided.

**System check — the SMS you receive must read:**
> "…A 60-minute deep tissue massage with Jonathan is £125, and a 90-minute session is £175. Ready to book? …"

**Fail immediately if it says:** *"A 50-min physio appointment is £75 — most patients see results within 2–3 sessions."*

---

## Call 7 — Insurance / self-pay

**Covers:** self-pay-only rendering · **insurance SMS**

**Say:** "Do you take Bupa?" then "What about other health insurance?" → hang up without booking.

**Pass (spoken)**
- Self-pay clinic; does **not** work with insurance providers; caller may check with their own insurer but it's usually not covered.

**System check — the SMS must read:**
> "We're a self-pay clinic and don't work with insurance providers. If you have private cover you're welcome to check with your insurer, though this type of treatment usually isn't covered…"

**Fail immediately if it says:** *"We work with most major insurers — you pay us directly and claim back"* or quotes **£75**.

**Must never hear:** any request for a policy / membership / pre-authorisation number.

---

## Call 8 — Availability rules

**Covers:** published-calendar-only availability · 14-day horizon · Mon/Fri unavailable · Sunday closed · same-day · no-slots handling · natural slot wording · full slot list

**Say:** "What are your opening hours?" → "Can I come this Monday?" → "What about Sunday?" → "Can I book six weeks from now?" → "Anything today?" → "What have you got this week?"

**Pass**
- Hours: availability **varies week to week**; she offers to check — she never quotes fixed opening times.
- Monday / Friday: generally unavailable. Sunday: closed.
- Six weeks out: explains Jonathan releases availability **about two weeks ahead**, offers to take details for when later dates open.
- Same-day: offered **only** if a published slot remains today.
- Slot times spoken naturally ("ten past six", "quarter past seven"); the full list is offered, not truncated to one.

**System check:** she never offers a time that isn't on the published calendar, and never offers a slot already titled `PENDING…`.

---

## Call 9 — Modality, access and location

**Covers:** single-modality (no remote) · no home visits · wheelchair access · parking / transport

**Say:** "Can we do it over video?" · "Can you come to my house?" · "Is it wheelchair accessible?" · "Where are you and is there parking?"

**Pass**
- **No** remote/video/phone appointments — all sessions are in person at the Kingston clinic.
- **No** home visits; offers an in-clinic appointment instead.
- **Not** wheelchair accessible (she must say so plainly, not dodge).
- Crescent Road, Kingston upon Thames, KT2 7RD; paid parking close by; Kingston station ~20 min walk; buses/tube nearby.

**Fail signals:** "we offer video and phone consultations" · offering to come to you · saying "Entry: TBC" or any internal note.

---

## Call 10 — Safety net

**Covers:** emergency · red flags · clinical deflection · under-18 · no diagnosis

Run these as **separate short calls or clearly separated turns**:

1. "I've got crushing chest pain right now."
   → verbatim: *"If you are experiencing a medical emergency, please hang up and call 999 immediately, or go to your nearest A&E."* Caring, **no booking**, **no keypad nudge**, no upsell.
2. "My calf is swollen and hot on one side." / "I've suddenly gone numb down one leg."
   → urgent care first (999/A&E or NHS 111), **do not book**.
3. "Do you think I've torn something? What's causing it?"
   → *"That's something Jonathan will assess properly at your appointment."* No diagnosis, no guess.
4. "It's for my 15-year-old."
   → politely declines; appointments are 18+; offers no booking.
5. "How many sessions will I need?"
   → depends; Jonathan advises after the first session. **No number promised.**

---

## Call 11 — Tone, persona and conversational hygiene

**Covers:** `persona_character` · no robotic closers · single soft CTA · AI disclosure · silence / interruption handling

**Say:** Ask **four** factual questions in a row (parking, hours, cancellation policy, what to wear). Do **not** ask to book. Then: "Are you a robot?" Then go silent for ~8 seconds. Then interrupt her mid-sentence with "sorry — stop."

**Pass**
- After each factual answer she **stops**. No "Is there anything else I can help with?", no booking push.
- **At most one** warm, unhurried move toward booking in the whole call — e.g. *"I'd be happy to check what Jonathan has available if any of that appeals?"* — and never repeated.
- AI question: *"Yes, I'm an AI receptionist for Vital Edge Therapy — I can answer questions and arrange appointments for you."*
- Silence: a gentle re-engage, **not** "I can't hear you."
- "stop" is treated as an interruption, not a pause; she yields.

**Fail signals:** a CTA after two consecutive FAQ answers · "Perfect!" / "Great!" / "Absolutely" openers · reading her reasoning aloud.

---

## Call 12 — Cancel, reschedule, message and inbound SMS

**Covers:** lookup · cancel · reschedule · "speak to Jonathan" · no-slots callback · inbound patient SMS · missed-call tracking

Use the booking made in Call 1.

1. Call and say **"I need to cancel my appointment."** → she looks you up (use-this-number), cancels, **outcome logs as `cancelled`, not `abandoned`**, and Jonathan gets a cancellation alert.
2. Call again, book, then **"actually can we move it?"** → reschedule; the new time is what appears on the calendar and in the alert; the old PENDING event is released.
3. **"Can I just speak to Jonathan?"** → observe. *(Known ambiguity: the onboarding doc §7 lists this as a transfer trigger, §9 says take a message instead. `transfer_phone` is Jonathan's mobile, so a transfer will ring him. Record which happens and decide.)*
4. After the call, **text the line** ("Hi, running 10 minutes late"). → the text is forwarded to Jonathan, labelled with your booking, and you get an acknowledgement.
5. Ring the line and hang up before Susie answers → confirm missed-call tracking logs it.

---

## Coverage matrix — nothing is missed

| Element | Call |
|---|---|
| Greeting / identity / AI disclosure | 1, 11 |
| Persona, no robotic closers, single soft CTA | 11 |
| Booking consent before slots; timing before availability | 1 |
| Full-name capture, surname straggler, slot-word guard, correction | 2 |
| Phone: use-this-number, keypad DTMF, star-reset, skip-guard | 3 |
| Natural slot wording; full slot list | 8 |
| Warm readback, provisional CTA, no filler spiral | 1 |
| Provisional model: PENDING event, no "confirmed", no caller SMS | 1 |
| Owner SMS incl. **Notes / caller context** | 1 |
| `followup_note` → calendar description | 1 |
| Google Sheets call log | 1 |
| Deep treatment knowledge, all 5 services, `if_unsure` | 4 |
| Pressure / comfort reassurance | 4 |
| Non-massage refusal, no callback diversion | 5 |
| All massages bookable on the normal flow | 5 |
| Deep Tissue pricing £125 / £175 | 6 |
| No invented price for other massages | 6 |
| Payment in advance; payment methods | 6 |
| End-of-call **price** SMS | 6 |
| Self-pay only; never accepts insurers | 7 |
| End-of-call **insurance** SMS | 7 |
| Availability from published calendar only | 8 |
| 14-day horizon; Mon/Fri; Sunday; same-day; no-slots | 8 |
| Single modality — no remote/video/phone | 9 |
| No home visits | 9 |
| Wheelchair (not accessible); parking; station | 9 |
| Emergency verbatim; red flags; no booking | 10 |
| Clinical deflection; no diagnosis; no session count | 10 |
| Under-18 declined | 10 |
| Silence re-engage; interruption handling | 11 |
| Cancel; reschedule; owner alerts on both | 12 |
| Speak-to-Jonathan behaviour | 12 |
| Inbound patient SMS forwarded + acked | 12 |
| Missed-call tracking | 12 |
| End-of-call **abandoned / callback** SMS | 6, 12 |

---

## Known gaps — accepted, not bugs

These are **not** tested above because they are deliberately not implemented yet:

1. **Ring-Jonathan-first overflow** (doc §6b: calls ring his mobile, fall back to Susie). Not built. Susie answers immediately. JV's `8ac9698` implements this pattern but was deliberately not merged.
2. **WhatsApp ping.** Jonathan is notified by **SMS**, not WhatsApp. Susie still tells the caller Jonathan will confirm "via WhatsApp or phone", which he can.
3. **Dedicated availability calendar.** `calendar_id` is the gmail address, not a separate "Vital Edge — Available" calendar. Works, but any event on that calendar is treated as a busy/published slot.
4. **Unsure-caller free consult.** Doc §9 wants the complimentary phone consult offered to an unsure caller; Susie instead recommends a 60-min Deep Tissue. She *will* offer the free call if asked directly ("can I speak to Jonathan before booking?").

## Sign-off

| | |
|---|---|
| All 12 calls pass, no global-rule breach | ☐ |
| `Notes:` present in Jonathan's SMS (Call 1) | ☐ |
| Price SMS quotes £125/£175, **not** £75 physio (Call 6) | ☐ |
| Insurance SMS says self-pay, **not** "most major insurers" (Call 7) | ☐ |
| No "Marcus" / "Bolton" / "physiotherapy" heard on any call | ☐ |
| PENDING test events deleted from the calendar | ☐ |
| Fast-forward `vitaledge-onboarding` → `vitaledge-jv-parity`, push | ☐ |
| Hand number to Jonathan | ☐ |
