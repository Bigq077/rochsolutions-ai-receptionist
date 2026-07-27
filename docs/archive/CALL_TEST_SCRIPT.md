# Susie v3 — Lethal One-Call Test Script

Single call covering every subsystem in order: greeting chunking, DTMF, watchdog ladder, STT mid-sentence safety net, name extraction, name correction, phone collection, and full booking flow.

Run with a phone call to the Twilio number. Keep server logs open. Each step has an expected outcome and a pass/fail criterion.

---

## PRE-CALL CHECKLIST
- [ ] Server logs streaming (`tail -f` or Railway live logs)
- [ ] No active session from a previous call on the same number
- [ ] Note your calling number (the one Twilio will see) — you'll use it in step 14

---

## PHASE 1 — GREETING INTEGRITY
**Tests: 2-chunk TTS, no orphan third chunk, correct text**

### Step 1 — Connect, say nothing
Dial the number. Let Susie speak without interrupting.

**Susie should say (exactly 2 TTS chunks):**
> Chunk 1: "Hi there, I'm Susie, Theorem Health's AI receptionist —"
> Chunk 2: "to speak to Mark directly press 1, otherwise how can I help you today?"

**Log to check:** `[tts] chunk 1/2` and `[tts] chunk 2/2` — **no chunk 3**

**FAIL if:** you hear "Otherwise, how can I help you today?" as a third mumbled piece, or there are 3 TTS log lines

---

## PHASE 2 — INTRO DTMF (Mark transfer)
**Tests: v3_intro_dtmf_active gate, press-1 transfer**

### Step 2 — Press 1 immediately during greeting audio
Press `1` on your keypad while Susie is still speaking the greeting.

**Susie should:** stop TTS, initiate transfer to Mark

**FAIL if:** Susie treats `1` as a regular DTMF digit mid-booking, or ignores it entirely

*If transfer fires correctly, hang up and call back for the main test. The rest of this script assumes you do NOT press 1.*

---

## PHASE 3 — LOCATION WATCHDOG LADDER
**Tests: silence detection → biased confirm → DTMF rung, Alcester pronunciation**

### Step 3 — State booking intent, give no location
After greeting:
> **You:** "I'd like to book an appointment"

Susie acknowledges. She will ask which clinic (Alcester or Redditch).

### Step 4 — Say nothing for 12+ seconds
After Susie asks the clinic question, go completely silent.

**Susie should (rung 1 — biased confirm after ~8s):**
> "Did you say the Alcester clinic?"

**Log to check:** `[watchdog] v3 rung 1 — biased confirm`

**FAIL if:** watchdog doesn't fire, or Susie repeats the original question verbatim

### Step 5 — Say nothing for another 12+ seconds
Still silent after the biased confirm.

**Susie should (rung 2 — DTMF after ~8s):**
> "For Alcester press 1, for Redditch press 2."

**Log to check:** `[watchdog] v3 rung 2 — DTMF`

**FAIL if:** watchdog retires or escalates past DTMF

### Step 6 — Press 2 (Redditch)
**Susie should:** acknowledge Redditch and move on

### Step 7 — Check Alcester pronunciation (separate note)
On a different call (or rewind your memory from rung 1): Susie said "Alcester" — did it sound like **"Awlstuh"** or did she say the full spelling?

**FAIL if:** she says "Al-chester" or "Al-sess-ter"

---

## PHASE 4 — STT MID-SENTENCE SAFETY NET
**Tests: safety net does NOT fire while you're still talking**

### Step 8 — Speak very slowly with a 4-second mid-sentence pause
When Susie asks your name:
> **You:** "My name is..." *(pause 4 seconds)* "...James."

**Susie should:** wait for you to finish, then respond with name confirmation

**FAIL if:** Susie interrupts you or fires a "are you still there?" during your pause (safety net firing on partial)

**Log to check:** `[partial]` transcript lines during your pause — these should reset the dead-air timer without triggering the net

---

## PHASE 5 — FALSE NAME EXTRACTION
**Tests: _NOT_NAMES blocklist, \b word-boundary regex**

### Step 9 — Use a phrase that embeds a blocked word
When Susie asks your name, say:
> **You:** "It suits me fine, call me James"

**Susie should:** extract "James" — NOT "Me" or "Suits"

**FAIL if:** Susie says "Thanks Me" or "Thanks Suits"

**Log to check:** `[name extractor]` line — should show `James`

---

## PHASE 6 — NAME CORRECTION (new rule)
**Tests: 'no that's wrong' → name re-ask, NOT phone collection**

### Step 10 — Give a garbled name
> **You:** "My name is Moch"

Susie should echo it back:
> "Thanks Moch — if you'd like me to use the number you're calling from, just say use this number."

### Step 11 — Correct the name immediately
> **You:** "No that's wrong"

**Susie should (name correction rule):**
> "Sorry about that — what's your first name?"

**FAIL if:** Susie moves to phone collection ("Could you type the number on your keypad?") or says "I'll take a different number then"

### Step 12 — Give the correct name
> **You:** "It's actually Tom"

**Susie should:** "Thanks Tom — if you'd like me to use the number you're calling from, just say use this number."

---

## PHASE 7 — PHONE NUMBER COLLECTION
**Tests: calling-number offer, keypad entry, digit readback**

### Step 13 — Reject the calling number
> **You:** "No, use a different number"

**Susie should:** "Could you type the number on your keypad? You can press the star key to reset at any time."

### Step 14 — Type a number on your keypad
Type any valid UK mobile slowly: e.g. `07700900123 #`

**Susie should:** read every digit back individually then confirm:
> "I've got 0-7-7-0-0-9-0-0-1-2-3 — is that right?"

**FAIL if:** she reads it as a lump, skips the readback, or asks you to say the number aloud

### Step 15 — Confirm the number
> **You:** "Yes"

---

## PHASE 8 — BOOKING TIMING EXTRACTION
**Tests: timing preference detection, no double-ask**

### Step 16 — Give a vague but valid timing preference
> **You:** "Something next week if possible"

**Susie should:** say a filler ("Just a moment while I check what's available") then call `check_availability` — she should NOT ask for a day/time again

**FAIL if:** Susie asks "Is there a particular day or time that works best?" after you already said "next week"

---

## PHASE 9 — SLOT PRESENTATION
**Tests: one slot at a time, numbered list, rejection → next slot**

### Step 17 — Susie presents the first slot
She should offer the soonest available single day:
> "I've got Tuesday the 13th — does that work for you?"

### Step 18 — Reject it
> **You:** "No, I can't do Tuesday"

**Susie should:** immediately offer the next day — no open question like "When would you prefer?"

### Step 19 — Accept the next day
> **You:** "Yes that works"

**Susie should:** present up to 3 times as a numbered list:
> "I've got three options. Number 1, 9am. Number 2, 11am. Number 3, 2pm. Any of those suit you?"

### Step 20 — Give an ambiguous time response
> **You:** "The second one" *(or say something garbled like "errm the... second")*

**Susie should:** confirm "11am" — not re-ask or pick randomly

**FAIL if:** she re-asks the whole list or picks option 1

---

## PHASE 10 — WATCHDOG SILENCE ON NAME (non-location)
**Tests: non-location watchdog re-ask is just the bare question**

*To hit this: start a fresh call, state booking intent, give location, then go silent when Susie asks for your name.*

### Step 21 — Go silent after "what's your first name?"
Wait 8+ seconds.

**Susie should re-ask:**
> "What's your first name?"

**FAIL if:** the re-ask is something like "Tuesday the 13th — what's your first name?" (em-dash prefix with stale slot info leaked in)

---

## PHASE 11 — GRACEFUL EXIT LADDER
**Tests: 3-attempt graceful exit, no premature hang-up**

*Start a fresh call and stay completely silent throughout (no speech, no DTMF).*

- Attempt 1: Greeting
- Silence → watchdog rung 1
- Silence → watchdog rung 2 (DTMF)
- Silence → graceful exit message + hang-up

**Susie should hang up after the DTMF rung goes unanswered — not before**

**FAIL if:** she hangs up after rung 1, or loops indefinitely without hanging up

---

## SCORING

| Phase | What it tests | Pass | Fail |
|-------|---------------|------|------|
| 1 | 2-chunk greeting, no orphan | | |
| 2 | DTMF → Mark transfer | | |
| 3 | Watchdog ladder (silence → confirm → DTMF) | | |
| 4 | Alcester = "Awlstuh" | | |
| 5 | Safety net doesn't fire mid-sentence | | |
| 6 | False name blocklist | | |
| 7 | Name correction → re-ask (not phone) | | |
| 8 | Calling-number offer + keypad entry + readback | | |
| 9 | Timing preference — no double-ask | | |
| 10 | Slot: one at a time, numbered list, reject→next | | |
| 11 | Non-location watchdog: bare re-ask (no prefix) | | |
| 12 | Graceful exit at correct rung | | |

**12/12 = ship it**
