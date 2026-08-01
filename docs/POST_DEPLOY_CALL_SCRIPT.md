# Post-deploy verification — `6b706bb` (1 Aug 2026)

**Purpose:** confirm the three changes just deployed do what they claim, and that
none of them broke something adjacent. Four calls, about fifteen minutes.

**Scope:** this is NOT the U3.5 A/B (`docs/U35_AB_CALL_SCRIPT.md`) and not a
general soak test. Leave `ASSEMBLYAI_USE_U35=false` for all four calls — changing
two variables at once measures nothing.

**Dial:** the demo Render service's Twilio number (the one `latency-eval`
deploys). Script below assumes `MEDIA_STREAMS_CLINIC_ID=jv_v1` — Joint Venture
Physiotherapy, practitioner **Marcus**, Flexspace Bolton, **evenings only**
(Mon from 16:30, Tue from 17:00, Wed from 17:30, last appointment 20:30).
Confirm that env var before you dial or the place names and times below are wrong.

**What shipped:**

| Commit | Change | Which call tests it |
|---|---|---|
| `66e5d6d` | Phone numbers spelled as words for TTS | 1 (and 4 for false positives) |
| `6b706bb` | Phone-confirm accepts the adjective slot + filler runs | 1 |
| `49fde08` | Requested day full → widen and offer real alternatives | 2 |

**The one rule:** don't fix anything mid-run. Write down what you hear and batch
the fixes afterwards. If a call goes badly wrong, hang up, note where, dial again.

---

## Pre-flight — not a call

Confirm the deploy actually landed. A green Render page is not proof the new
commit is serving.

```bash
curl -s https://<demo-service>.onrender.com/health
```

Check the Render dashboard shows **`6b706bb`** as the live commit. If it shows
`aa0b3bd` or earlier, the deploy has not finished — wait, don't dial.

---

## Call 1 — The phone step (the headline)

This is the call that matters. Two shipped fixes meet here, and the second one is
a defect that has recurred four times.

1. Greeting → *"Hi, I'd like to book an appointment please."*
2. Give your reason when asked — *"my lower back's been sore for a couple of
   weeks."* Answer any screening questions normally, with a plain "no".
3. Take any slot she offers.
4. Give your name when asked: **"Quentin Rock."**
5. **She reads your number back.** This is fix `66e5d6d`. Listen, don't talk.

   ✅ **PASS:** three clearly separated groups with an audible pause between
   each — *"oh seven five oh two … two one one … two oh seven"*. You should be
   able to check it against your own phone without effort.

   ❌ **FAIL:** one rushed run of digits, or any group blurring into the next.
   That is the ElevenLabs normalization problem still winning — note whether it
   sounded like numerals ("seven thousand five hundred") or fast-but-separate
   digits, the two failures have different causes.

6. **Answer loosely, exactly like this — do not say "yes, that's the best
   number":**

   > **"Um, yeah, that's a good number."**

   ✅ **PASS:** she moves **straight** to the booking question — *"So that's
   Quentin, [day] at [time] — shall I go ahead and book that in?"*

   ❌ **FAIL:** she asks about the number again. That is A4 unfixed and it is
   the whole point of this call.

7. Confirm with **"yeah, go ahead."**

   ✅ **PASS:** *"All booked"* (or the booking readback) — **no repeat of either
   question.** The whole point of the fix is that steps 5–7 happen once each.

   ❌ **FAIL:** she re-asks the number question here. That is gate 2 in
   `llm_stream.py`, a different mechanism — note it separately, it was not fixed.

**Record:** the exact number of turns from readback to booked. Four is the bug
(that's what CA587b103b did). Two is the fix working.

---

## Call 2 — Requested day full

Fix `49fde08`. Needs a genuinely full day, so **check the Acuity calendar first**
and pick a weekday whose evening slots are all taken. If nothing is full, block
one out in Acuity for 30 minutes and use that — otherwise this call proves nothing.

8. Book normally until she asks about timing, then name that day:
   *"Could I do [full day] evening?"*

   ✅ **PASS:** she says that day is fully booked **and offers real alternatives
   in the same breath** — *"Tuesday the 4th is fully booked, I'm afraid — the
   available slot for Wednesday the 5th is seven in the evening."*

   ❌ **FAIL A:** she offers a day but you were never given slots for it → she is
   inventing an alternative. This is the exact behaviour the fix removes.
   ❌ **FAIL B:** two turns — "that day's full", silence, then you have to ask
   what else there is. The point is one turn.
   ❌ **FAIL C:** she says the day is unavailable and stops dead.

9. Take the alternative and book it. Confirm the booking lands in Acuity on the
   day she actually named.

**Record:** whether the day she offered had real slots behind it. That is the
correctness question; the phrasing is secondary.

---

## Call 3 — Decline the number (measuring, not verifying)

**Nothing was shipped for this.** It is the open question the U3.5 A/B exists to
answer, and this call gives you the "before" reading. Expect it to go badly — that
is the data.

10. Book as far as the number readback, then decline:
    *"No, it's a different number."*

    ✅ **PASS:** she says *"No problem — go ahead and type the number on your
    keypad. You can press the star key to reset at any time."*

    ❌ **FAIL:** she says *"just say use this number"*, or invites you to say the
    number aloud. Both phrasings still exist in two silence-retry paths in
    `connection.py` and contradict the main prompt.

11. Type a different 11-digit number on the keypad. **PASS:** accepted, read back.
12. Hang up, dial again, get to the readback, decline, and this time **read a
    number aloud** — *"oh seven seven one two, three four five, six seven eight"*.

    **Record verbatim what happens.** Expected failure: she doesn't capture it,
    because the extractor strips non-digits and the current STT returns number
    *words*. Note whether she (a) captured it correctly, (b) asked you to use the
    keypad, or (c) captured a wrong number. **(c) is the dangerous one** — write
    down exactly what she read back.

---

## Call 4 — Nothing else broke (the false-positive sweep)

The TTS change rewrites digit runs. Its real risk is touching something it
shouldn't. Everything here should sound **exactly as it did yesterday**.

13. Ask *"how much is the first appointment?"*
    ✅ **PASS:** *"fifty-two pounds"*. ❌ FAIL: *"five two pounds"*.
14. Ask *"how long is it?"*
    ✅ **PASS:** *"forty minutes"*. ❌ FAIL: *"four oh minutes"*.
15. Let her read a date and time back.
    ✅ **PASS:** *"Tuesday the fourth of August at half past six."*
    ❌ FAIL: any digit-wise reading of the date, the time, or the year.
16. Ask *"where are you based?"*
    ✅ **PASS:** the Bolton address, postcode **"B L three, five N Z"** spoken
    normally. ❌ FAIL: the postcode digits spelled out oddly, or the number in
    the street address read digit-wise.
17. Ask *"what's your phone number?"* if the clinic number comes up.
    ✅ **PASS:** grouped and paced, same as your own number in call 1.

**Record:** any place a number sounded different from yesterday. One false
positive here is worse than the bug being fixed — if you hear one, that's a
revert candidate, not a tweak.

---

## Reporting back

For each call, three lines is enough:

```
Call 1  PASS/FAIL  turns from readback to booked: ___
Call 2  PASS/FAIL  did the offered day have real slots? Y/N
Call 3  keypad line correct? Y/N   spoken digits: captured / keypad / WRONG
Call 4  anything that sounded different from yesterday: ___
```

Grab the `CA…` call SID for anything that fails — the transcript is in the
`demo_obs` Postgres, and note that stored transcripts are **post-Gate-5**, so a
wrong sentence there may be the gate rewriting a correct generation rather than a
bad generation. Audio is the arbiter for calls 1 and 4, not the transcript.

**Rollback if calls 1 or 4 fail badly:**

```bash
git revert --no-edit 6b706bb 66e5d6d 49fde08
```
