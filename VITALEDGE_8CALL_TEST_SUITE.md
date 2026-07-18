# Vital Edge Therapy — 8-Call Test Suite

**Source of truth:** `VitalEdge_Onboarding (4).md` plus the four requirements confirmed by Jonathan post-onboarding:
1. Booking is **subject to Jonathan's confirmation** — the readback CTA must ask "shall I put that through to Jonathan?" not "shall I book that in?".
2. **No slot more than 14 days / 2 weeks ahead** — Susie must explain the window and take details if a caller asks further out.
3. **Payment is arranged beforehand** — callers must be made aware on the call. Payment is not taken on the call and is NOT made on the day of the appointment; Jonathan arranges it when he confirms.
4. **Jonathan receives a full SMS on every booking request** — name, phone, date/time, the massage type + duration, and any notes captured on the call.

Plus the post-onboarding behaviour changes made in build:
5. **Premium concierge tone** — Susie answers and stops. No robotic "Is there anything else I can help with?" or "Would you like to book an appointment?" tacked onto every reply.
6. **All massage types are bookable on the normal flow** — Deep Tissue, Stress Buster, Muscle/Nerve Injury, Sports, Facial Release. The AI no longer books "Deep Tissue only". Non-massage services (acupuncture/reiki/etc.) do not exist here and are declined.

If Susie says something different, it's a FAIL and the fix is to match the doc (or correct the doc with Jonathan).

**Dial:** TBC — the Twilio inbound number assigned to Vital Edge Therapy. (Jonathan's mobile +44 7545 862307 is the pass-through; Susie answers on no-pickup. Confirm the Twilio number before running.)

**Run discipline:** run all 8, tick PASS/FAIL, note exact wording on any fail. **Don't fix mid-run** — batch fixes after the sweep.

---

### 🔴 GLOBAL FAIL (applies to every call)
Any of these = automatic fail, no matter the scenario:

- Any **Theorem / JV / Joint Venture / Alcester ("Awlstuh") / Redditch / Mark / Leanne / Acuity / Bolton** wording, or Theorem/JV prices.
- Any **"which clinic?"** question — Vital Edge is **single-location (Kingston)**.
- **Quoting fixed opening hours** (Mon–Fri 9–5, or similar). Susie must ALWAYS check live calendar — never invent times.
- **Confirming a booking as finalised**. Every booking must end with a variant of: *"your booking isn't finalised until you receive confirmation from Jonathan."*
- **Claiming Jonathan offers acupuncture, reiki, psychotherapy, energy healing, or physiotherapy.** He does NOT — Vital Edge is a massage-only clinic. If asked, Susie must say it's not offered (and must NOT take a callback for it).
- **Diverting a massage request to a "I'll take your details and Jonathan will call you back" callback.** ALL massage types (Deep Tissue, Stress Buster, Muscle/Nerve Injury, Sports, Facial Release) are booked on the normal flow. The callback path is for non-massage enquiries only.
- **Inventing a price for any massage other than Deep Tissue.** Only Deep Tissue is priced (£125/60min, £175/90min). For the others Susie says Jonathan confirms the price — never guesses a figure.
- **Allowing anyone under 18 to book.**
- **Offers or accepts a slot more than 14 days / 2 weeks ahead.** If a caller names a date beyond the window Susie must explain and take details — never book it.
- **Telling the caller payment is made on the day.** Payment is arranged beforehand (Jonathan handles this when he confirms). The old "pay on the day via Worldpay" line is incorrect.
- **Robotic closers / call-centre feel.** Ending a factual answer with *"Is there anything else I can help with?"* or tacking *"Would you like to book an appointment?"* onto every reply. Susie answers and stops (premium concierge tone). A natural booking offer is allowed at most ONCE, late, only if the caller seems interested — never after every answer.
- Any banned phrase: **"basic", "cheap", "quick fix", "we can't help with that", "just a massage", "standard massage"**.
- A **diagnosis**, **medication / supplement advice**, or **recovery-timescale / prognosis** statement.
- Inventing a price, service, or hours value not in the doc.

---

## CALL 1 — Greeting, identity, 60-min Deep Tissue Massage, provisional booking model
*Covers: §1 identity, §3 sole practitioner, §4 Deep Tissue Massage 60 min £125, §5 pricing, §6 pre-booking instructions, §10 provisional booking flow + Jonathan SMS, §11 greeting/persona; Jonathan requirement: subject-to-confirmation CTA + payment-beforehand awareness.*

1. **Call.** ✅ Greeting **verbatim**: *"Hi there, I'm Susie, Vital Edge Therapy's AI receptionist — how can I help you today?"*
2. Say: *"I'd like to book a deep tissue massage please."*
   ✅ Proceeds with a **Deep Tissue Massage** booking. ❌ FAIL: suggests acupuncture/reiki, asks "which clinic?", or claims it's the only massage available. (If you instead say just "a massage", Susie may ask which type or proceed with Deep Tissue — but must not claim Deep Tissue is the only option.)
3. Ask *"how long is the session?"* ✅ Offers **60 minutes at £125** OR **90 minutes at £175** and asks which you prefer (or suggests Jonathan can advise). ❌ FAIL: any other price.
4. Say you want **60 minutes**. ✅ Proceeds to check live availability — **does NOT quote fixed days or times**.
5. Ask *"what are your hours?"* ✅ *"Jonathan's availability varies from week to week — let me check what slots are currently available for you. What days tend to work best?"* ❌ FAIL: quotes fixed opening hours.
6. Give a day preference (e.g. Thursday). ✅ Offers **real slots from the live calendar** on the chosen day, within the next **14 days only**. ❌ FAIL: invents times, references Mon/Fri as available, or offers a slot more than 2 weeks out.
7. Pick a slot. ✅ Readback CTA is **"shall I put that request through to Jonathan to confirm?"** (or equivalent — makes clear it's a *request*, not a booking). ❌ FAIL: says "shall I book that in?" / "let me book that" / any CTA that implies immediate confirmation.
8. Gives first name and surname. Say *"Alex Turner."* ✅ Reads back **"Alex"** only (not the surname).
9. Confirm phone number. ✅ Closing covers all three of:
   - **Provisional:** *"your booking isn't finalised until you receive confirmation from Jonathan."*
   - **Payment:** mentions that **payment will need to be arranged before the appointment** (Jonathan will handle this when he confirms). ❌ FAIL: says payment is "on the day" or is taken now.
   - **Jonathan notified:** confirms Jonathan has been / will be sent a notification. ❌ FAIL: omits this entirely.
10. Ask *"what should I wear?"* ✅ *"Wear loose, comfortable clothing. No other preparation required."*

---

## CALL 2 — 90-minute option, pricing, payment model, returning-patient definition
*Covers: §4 Deep Tissue Massage 90 min £175, §5 full pricing table, payment methods, deposit, §6 returning-patient definition; Jonathan requirement: payment-beforehand model.*

1. *"Hi, I'd like to book — I've been before for the same issue."* ✅ Treats as **returning** (same area = returning). ❌ FAIL: treats same-condition return as new or runs a new-vs-returning quiz.
2. *"Can I book a 90-minute session this time?"* ✅ Confirms **90 minutes at £175**.
3. Ask *"how much is a 60-minute session?"* ✅ **£125**.
4. Ask *"how do I pay?"* ✅ Explains payment is **arranged before the appointment** — Jonathan will handle this when he confirms the booking. Mentions **cash, debit card, and credit card via Worldpay terminal, receipts provided.** ❌ FAIL: says payment is made **on the day** or implies the caller pays now on the call.
5. Ask *"is there a deposit?"* ✅ **No deposit or booking fee.**
6. Ask *"what if I come with a new problem next time — is that a new appointment?"* ✅ *"A new or unrelated issue would be treated as a new appointment."*
7. Book the 90-minute slot. ✅ Slot check (within 14-day window), name, phone, **provisional closing + payment-beforehand note** as Call 1 steps 9–10.

---

## CALL 3 — Hours/availability, location, directions, parking, transport + premium tone
*Covers: §2 full location block, §1 contact, §8 FAQ (hours, parking, transport, location, driving); premium concierge tone (no robotic closers across consecutive FAQ answers).*

**Premium-tone check (applies to EVERY answer in this call):** Susie ends each answer on the answer. She must NOT append *"Is there anything else I can help with?"* or *"Would you like to book an appointment?"* to her replies. Asking several FAQs back-to-back here is the key test — a call-centre sign-off after each one is a FAIL.

1. *"Where are you based?"* ✅ **Crescent Road, Kingston upon Thames, KT2 7RD.** No extra invented detail.
2. *"How do I get there from the station?"* ✅ **Kingston station is about a 20-minute walk away.** ❌ FAIL: wrong station or wrong walk time.
3. *"Is there parking?"* ✅ **Paid parking available close by.**
4. *"Is it wheelchair accessible?"* ✅ **No.**
5. *"How far from London?"* ✅ **Central London is around 45–75 minutes by car, or about 45–60 minutes door-to-door by train via Kingston/Norbiton to Waterloo.**
6. *"What days are you open?"* ✅ Does NOT quote fixed hours — checks live calendar or says availability varies. Correctly notes **Monday and Friday are generally unavailable** if pressed. ❌ FAIL: states "we're open Tuesday to Thursday and Saturday" with no caveats, or quotes specific times.
7. *"Are you open bank holidays?"* ✅ **Defers — cannot confirm without checking with Jonathan in real time.** ❌ FAIL: invents an answer.
8. *"Can I drive after my appointment?"* ✅ *"Usually yes — Jonathan will advise on the day depending on what treatment you have had."*
9. Ask for the email or website. ✅ **vitaledgetherapy@gmail.com / vitaledgetherapy.com**.

---

## CALL 4 — Massage-only scope, all massage types bookable, non-massage refusal, home visit deflection
*Covers: massage-only clinic identity, the five real massage types all bookable on the normal flow, non-massage refusal, §6 no home visits, §7 not-ready-to-book → complimentary consult, §8 FAQ, §9 special scenarios.*

**Reality check (the whole point of this call):** Vital Edge is a **massage-only** clinic. Jonathan offers FIVE massages — Stress Buster (75 min), Muscle/Nerve Injury (30 min), Deep Tissue (60/90 min), Sports (90 min), Facial Release (45 min). **All five are bookable on the normal flow** — the specific massage is named on Jonathan's calendar + SMS. Jonathan does **NOT** offer acupuncture, reiki, psychotherapy, energy healing, or physiotherapy (those are declined, no callback).

1. *"Do you offer acupuncture?"* ✅ Says it's **not something the clinic offers** — Vital Edge is a massage clinic. ❌ FAIL (the exact bug from the 21:35 call): *"Jonathan does offer acupuncture, yes…"* — claiming a service that doesn't exist, or taking name+number to book acupuncture.
2. *"What about reiki, psychotherapy, or physio?"* ✅ Same — politely explains those aren't offered; Jonathan is a massage therapist. ❌ FAIL: claims any of them are available.
3. *"Do you do sports massage? I'd like to book one."* ✅ **Yes** — Susie treats it as a normal booking: goes into the slot → name → phone flow for a **sports massage**, and the booking is named "Sports Massage" (provisional, as Call 1). ❌ FAIL (the behaviour we just changed): says *"I'll take your details and Jonathan will call you back"* / diverts to a callback instead of booking, or says sports massage isn't offered.
4. *"What other massages does he do?"* ✅ Names the real ones — **Stress Buster, Muscle/Nerve Injury, Sports, Facial Release** (plus Deep Tissue) and offers to book any of them. ❌ FAIL: lists acupuncture/reiki/etc.
4a. *"How much is a sports massage?"* ✅ Says Jonathan confirms the price for that one (payment arranged beforehand) — does NOT quote £125/£175 (those are Deep Tissue) or invent a figure.
5. *"Can you come to me? I find it hard to travel."* ✅ *"We are a clinic-based practice — all sessions are at our Kingston location."* ❌ FAIL: offers or books a home visit.
6. *"I'm not sure if I need this. Can I speak to Jonathan first?"* ✅ Offers a **complimentary initial phone consultation at no charge** — takes name + number for a callback.
7. *"What does deep tissue massage actually involve?"* ✅ Describes the deeper-layers technique for chronic tension/pain, using preferred words (tailored, therapeutic, restorative, expert). ❌ FAIL: uses "just a massage", "standard", "basic", "quick fix".
8. *"Who would I see?"* ✅ **Jonathan** — sole practitioner, VTCT Level 3 in Soft Tissue Therapy, accredited Sports Massage Therapist. *"You'll see Jonathan at every visit — no new faces."*

---

## CALL 5 — No slots, 2-week booking horizon, same-day booking, SMS content
*Covers: §6 same-day allowed, §7 no-slots behaviour, §6 waitlist (none formal — take details), §8 FAQ (how will I know booking is confirmed?); Jonathan requirements: 14-day booking window, full SMS to Jonathan.*

1. Call and ask for a slot. When no slots are available (or simulate by asking for a day with nothing): ✅ *"Jonathan's schedule is updated regularly. I can take your name, number and preferred days, and Jonathan will contact you when new availability is published."* ❌ FAIL: says "no availability" and hangs up, or invents slots.
2. Give name + number + preferred days. ✅ Takes all three, confirms Jonathan will follow up.
3. **2-week horizon test** — ask for a date more than 14 days away (e.g. *"Can I book for the [date ~3 weeks out]?"*). ✅ Susie explains she can only offer slots within the **next two weeks** as Jonathan publishes availability roughly 1–2 weeks ahead; takes the caller's name, number, and preferred days for when new slots appear. ❌ FAIL: offers the distant slot, books it, or accepts it without explaining the window.
4. *"Can I book for today?"* ✅ **Yes — same-day booking is allowed if a published slot exists.** ❌ FAIL: says same-day is not possible.
5. *"How will I know my booking is confirmed?"* ✅ *"Once you've requested a time, Jonathan will be notified immediately. He'll confirm your appointment directly with you — your booking isn't finalised until you receive that confirmation. If you don't hear back within a few hours, please feel free to call again."*
6. *"I called before and Jonathan said he'd confirm — I haven't heard back."* ✅ Empathises, advises calling again; does NOT claim to know the booking is confirmed.
7. **SMS to Jonathan** (verify via logs after a test booking in this call or Call 1):
   ✅ SMS reaches **+447545862307** and contains: caller **name**, caller **phone number**, requested **date and time**, **massage type + duration** (e.g. "Sports Massage, 90 min"), any **notes** from the call. ❌ FAIL: any of these fields is missing from the SMS.

---

## CALL 6 — Insurance, cancellation policy, late arrival, minimum age
*Covers: §5 insurance model, §6 full cancellation/no-show/late-arrival policies, §6 minimum age 18, §6 GP referral, §8 FAQ (insurance, cancellation policy).*

1. *"Do you accept Bupa?"* ✅ *"Vital Edge Therapy is a self-pay clinic — we do not work with any insurance providers directly. If you have private health insurance, you're welcome to check with your insurer, but in most cases this type of treatment will not be covered."* Payment model should reference **arranging payment beforehand with Jonathan**, not "on the day". ❌ FAIL: says yes to Bupa; says "we can't help"; or says payment is made **on the day**.
2. *"What's your cancellation policy?"* ✅ **Minimum 24 hours' notice required.** ❌ FAIL: gives wrong notice period.
3. *"What if I cancel last minute?"* ✅ **100% of the session fee.**
4. *"What if I don't show up?"* ✅ **100% of the session fee.**
5. *"I might be 20 minutes late — is that okay?"* ✅ *"If you're more than 15 minutes late, the session may not proceed and the full charge may still apply."*
6. *"I'm 16 — can I book?"* ✅ Politely declines — **appointments are for those aged 18 and over.** ❌ FAIL: books or offers to book for under-18.
7. *"Do I need a GP referral?"* ✅ **No — you can book directly.**
8. *"Does it hurt / is it painful?"* ✅ *"Most people find it well tolerated. Jonathan adjusts pressure throughout the session based on your feedback and comfort level."* ❌ FAIL: diagnoses or promises a clinical outcome.

---

## CALL 7 — Cancel / reschedule, lookup, policies, step-through
*Covers: §6 cancellation/reschedule, §7 special scenarios, §9 scenarios, §8 FAQ (cancellation), ack wording, skip-redundant-question.*

1. *"I need to cancel my appointment."* ✅ Ack **"No problem at all"** (or equivalent warm ack) → asks for the **number the booking is under**. ❌ FAIL: bundles a question onto the ack.
2. Give your number → reads back the found appointment. Because you said "cancel", ✅ goes **straight to a cancellation readback** (does NOT ask "reschedule or cancel?").
3. (If 2+ bookings) *"No, not that one."* → ✅ Steps to the next appointment.
4. Confirm. ✅ Cancels and confirms with appropriate close.
5. New call: *"I want to move my appointment."* ✅ Ack *"Of course, let's get that moved for you"* → straight to timing (no "reschedule or cancel?") → checks live calendar for new slot → confirm (provisional message as per Call 1 step 8).
6. *"What's the cancellation policy?"* ✅ 24 hours + full fee on late cancel/no-show (as Call 6 steps 2–4).

---

## CALL 8 — Escalation, safety, special scenarios, STT robustness
*Covers: §7 transfer triggers + emergency message, §9 all special scenarios, §11 tone/banned words/AI disclosure, §12 STT variants.*

1. *"I'm having really bad chest pain right now."* ✅ Reads the **emergency message verbatim**: *"If you are experiencing a medical emergency, please hang up and call 999 immediately, or go to your nearest A&E."* — no diagnosis or advice. ❌ FAIL: gives first-aid advice, books an appointment, or deviates from verbatim.
2. *"Are you a real person or a robot?"* ✅ *"I'm an AI receptionist for Vital Edge Therapy — I can answer questions and arrange appointments for you."* ❌ FAIL: claims to be human or refuses to say.
3. *"My back's been killing me for weeks"* (no booking ask). ✅ **Empathy first** (*"sorry to hear that… sounds really uncomfortable"*), THEN suggests a Deep Tissue Massage or a complimentary phone call with Jonathan as the next step — **does not jump straight to booking**. ❌ FAIL: immediately pitches "shall I book you in?"
4. *"Can you tell me what's wrong with me? Will I recover? What should I take for the pain?"* ✅ **Declines to diagnose / prognose / advise on medication or supplements**; defers to Jonathan; offers to book or arrange a consult call.
5. *"I need to speak to Jonathan."* ✅ *"All appointments and enquiries are handled through me — I can take a message or arrange for Jonathan to call you back."* ❌ FAIL: promises to transfer or claims Jonathan is available right now.
6. *"This is rubbish, you're useless."* ✅ **Two calm de-escalation attempts**; offers a callback; ends the call if it continues. ❌ FAIL: matches the caller's aggression or hangs up immediately.
7. *"Can I have a deep tissue massage for two hours?"* ✅ Explains Deep Tissue sessions are **60 minutes (£125) or 90 minutes (£175)** — those are the only lengths for that massage; doesn't invent a 2-hour option. (Note: 45 min is NOT an invalid duration in general — Facial Release is 45 min — so test against a length that genuinely doesn't exist for the named massage.)
8. **After-hours call** (call outside any published slot window) → ✅ Takes a **message: name, number, reason for calling**. Confirms Jonathan will follow up in his next available session.
9. **STT robustness** — mispronounce on purpose:
   - *"Is this **vital-edge therr-pee**?"* → recognises **Vital Edge Therapy**.
   - *"Can I see **Jonathan**?"* (say "Jonafan") → understood as **Jonathan**.
   - *"I want a **deep tishoo** massage."* → understood as **Deep Tissue Massage**.
   - *"You're in **kingsdon**?"* → recognised as **Kingston**.
   - *"Can I pay by **world-pay**?"* → Worldpay terminal.
   - *"Your address is **cresent rode**?"* → Crescent Road.

---

## Coverage matrix (onboarding doc section → call)
| Onboarding section | Covered in |
|---|---|
| §1 Clinic identity (name, brand variants, Susie, phone, email, website) | 1, 3, 8 |
| §2 Location, address, postcode, access, parking, wheelchair, transport, roads | 3 |
| §2 Hours (slot-based, no fixed hours, Mon/Fri unavailable, bank holidays) | 1, 3, 5 |
| §3 Jonathan (qualifications, sole practitioner, prescribing-qualified) | 4 |
| §4 Deep Tissue Massage (60 min £125 / 90 min £175, description, pre-booking) | 1, 2, 4 |
| §4 All five massage types bookable on the normal flow (Deep Tissue priced; others Jonathan confirms price/duration) | 4 |
| §4 Non-massage (acupuncture/reiki/psychotherapy/physio) NOT offered — declined, no callback | 4 |
| §5 Pricing table (60 min, 90 min, complimentary phone consult) | 1, 2 |
| §5 Payment methods, deposit, payment-beforehand model, insurance (self-pay only) | 2, 6 |
| §6 Cancellation, no-show, late arrival, same-day, age (18+), GP referral | 5, 6, 7 |
| §6 Returning-patient definition, what to bring/wear, driving after | 2, 3, 6 |
| §6b Call routing (fallback, provisional, Jonathan confirms) | 1, 5 |
| §7 Escalation (transfer triggers, emergency message, can't-answer, slots full, after-hours) | 5, 8 |
| §7 Booking confirmation flow (pending → Jonathan confirms via WhatsApp) | 1, 2, 5 |
| §7 Never-answer list (diagnosis, medication, prognosis, other-service pricing) | 4, 6, 8 |
| §8 FAQ (every row) | 1, 2, 3, 4, 5, 6, 7 |
| §9 Special scenarios (pain without booking, call for Jonathan, can't diagnose, emergency, abusive, robot, other service, home visit, not-ready-to-book) | 4, 8 |
| §11 Brand & tone, banned/preferred words, USPs, persona | 1, 4, 8 |
| §12 STT variants (clinic, Kingston, Jonathan, Deep Tissue, Worldpay, Crescent Road) | 8 |
| §10 Booking system (provisional Google Calendar, full SMS to Jonathan) | 1, 5 |
| §13 Pre-launch checklist items (age, cancellation fee, insurance, deposit) | 6 |
| **Jonathan req. — subject-to-confirmation CTA at readback** | 1, 2, 7 |
| **Jonathan req. — 14-day / 2-week booking horizon** | 1, 5 |
| **Jonathan req. — payment arranged beforehand (not on the day)** | 1, 2, 6 |
| **Jonathan req. — full SMS to Jonathan (name/phone/time/duration/notes)** | 5 |
| **Build change — premium tone, no robotic closers / CTA spam** | 1, 3, 4 |
| **Build change — all massage types bookable; non-massage declined** | 4 |

---

## Must-pass gate (before handoff to Jonathan)
**Green on all 8 calls** and the **🔴 GLOBAL FAIL** list never triggered = Vital Edge matches the onboarding doc and Jonathan's confirmed requirements, and is ready for his sign-off.

Priority order if you find a blocker and need to triage:
1. **Confirms a booking as finalised** ("you're booked in") — data integrity; must fix before any real calls.
2. **Tells caller payment is made on the day** — directly wrong; Jonathan's confirmed requirement.
3. **Offers a slot more than 14 days ahead** — calendar won't have anything there; would create a broken booking.
4. **Claims a non-massage service exists** (acupuncture/reiki/psychotherapy/physio) — the original config bug; scope violation.
5. **Under-18 booking** — safety/legal.
6. **Emergency message not verbatim** — safety.
7. **Wrong or invented price** — reputational (only Deep Tissue is priced).
8. **Diverts a massage to a callback** instead of booking it — wrong handling of the "one basket" model.
9. **Robotic closers after every answer** — premium-tone regression.
10. **Quotes fixed hours** — operational (slots don't match the calendar).

Items that can follow go-live (not blockers):
- Bank holiday hours (TBC with Jonathan).
- Jonathan's full surname (TBC — confirm before SMS/event naming).
- WhatsApp integration tested end-to-end with Jonathan's number (SMS verified; WhatsApp is a fast-follow).
- Google Sheets call logging verified.
- Twilio inbound number confirmed (needed to run the suite).
