# Universal-3.5 Pro A/B — test-call script

**Purpose:** decide whether `ASSEMBLYAI_USE_U35=true` is better than the model we
run today. Nothing else. Resist the urge to also fix things you hear.

**Dial:** the demo Render service's Twilio number (the one `latency-eval` deploys).
**Confirm first:** `MEDIA_STREAMS_CLINIC_ID` on that service, so you know which
greeting and which place names are correct below.

---

## The one rule

**Run all six calls on the OLD model first, then flip the flag and run the same
six with the same words.** Not similar words — the same words. An A/B where the
two runs said different things measures nothing, and you will not get a second
quiet afternoon before the meeting.

Write the six utterances you'll use on a sheet before you dial. Read them.

Don't fix anything mid-run. Batch fixes after both passes.

---

## Pre-flight — not a call, do this first

```bash
curl -s https://<demo-service>.onrender.com/ms/test-stt | python -m json.tool
```

**PASS:** `"connected": true`, `"begin_received": true`, and
`"stt_variant": "u3.5-pro"` once the flag is on.

❌ **FAIL:** any close code. That is `universal-3-5-pro` rejecting a query
param — almost certainly `keyterms_prompt`. v3 closes instantly on an unknown
param, so this would kill every call from the first second. Stop here if it
fails; do not dial. This is the only U3.5 risk that cannot be checked offline.

---

## Call 1 — Barge-in (expect the friction here)

U3.5 emits stable, fully-transcribed partials instead of word-by-word ones. The
noise gate in `connection.py` was tuned against word-by-word partials, so this is
where I'd expect a regression first.

1. Let Susie start the greeting. **Cut across her after ~2 words** with
   *"Yeah, I want to book an appointment."*
   **PASS:** she stops mid-word and answers.
   ❌ FAIL: she finishes the whole greeting first, or stops noticeably later than
   she does today → stable partials arriving later than word-by-word ones.
2. Ask for slots. While she's reading the list, **cough once**, then stay quiet.
   **PASS:** she keeps going.
   ❌ FAIL: she stops for a cough → the gate got looser, not tighter.
3. While she's reading the list, cut in with *"the second one."*
   **PASS:** stops, takes it.

**Record:** does barge-in feel earlier, the same, or later than the old model?
That subjective answer is the main output of this call.

## Call 2 — Mid-sentence hesitation (the endpointer)

U3.5's turn detection is punctuation-based, not confidence-based. Ours is set to
`min_turn_silence=600` — hand-tuned against the *old* endpointer.

4. Say, with a deliberate ~1 second pause at the marked point:
   *"I'd like to book something for my shoulder … [pause] … it's been bad since
   Tuesday."*
   **PASS:** one reply, addressing the whole sentence.
   ❌ FAIL: two replies, or she answers the first half then talks over you →
   the sentence split into two FINALs. This is the 2026-06-12 stress case.
5. Repeat with a longer pause (~2 s). Note where it starts splitting.

**Record:** the pause length at which it splits, on each model. If U3.5 splits
sooner, sweep `U35_MIN_TURN_SILENCE` upward — that's env-only, no redeploy.

## Call 3 — Phone number and name (the de-format acceptance test)

This is the on-air check for the three raw-transcript consumers fixed in
`f0adf21`. If de-formatting is working, these behave exactly as they do today.

6. When asked for your number, say it as **one continuous run of digits**, no
   pauses: *"oh seven five zero two two one one two zero seven."*
   **PASS:** she reads the number back correctly.
   ❌ FAIL: she asks again, or acts as if you said nothing → the number was
   discarded. That is `_PHONE_NUMBER_RE` failing on a trailing full stop, i.e.
   de-formatting is not applying. Check the logs for `[ms_stt] deformat`.
7. When asked your name, answer with the label form: *"My name is Sarah Jenkins."*
   **PASS:** she reads back **only "Sarah"**.
   ❌ FAIL: she reads back *"my name is"*, or treats the label as the name →
   `_NAME_WRAPPER_PATTERNS` not matching a punctuated final.
8. Give a surname that needs spelling: *"It's O'Brien — O, apostrophe, B, R, I, E, N."*
   **PASS:** spelled correctly on the confirmation.

## Call 4 — Clinical safety vocabulary (highest stakes)

The keyterms boost is what stops `"calf"` degrading to `"coffee"` and defeating
the DVT screen. A new acoustic model can change which words it mishears, so this
gets tested explicitly rather than assumed.

9. Say: *"I've got pain and swelling in my calf, and it's hot to touch."*
   **PASS:** the red-flag screening path arms — she asks the follow-up screening
   questions rather than going straight to booking.
   ❌ FAIL: she books you in. Check the log for `[clinical_screening]` lines: if
   they are absent, the trigger word was misheard and Layer 1 never ran.
10. Say: *"I've had pins and needles in both legs and some numbness saddle area."*
    **PASS:** escalation, not a booking.

**This one decides the A/B on its own.** If U3.5 is better everywhere and worse
here, it does not ship.

## Call 5 — Hard tokens

11. Give a postcode: *"B49 6AD."* **PASS:** read back correctly.
12. Use the clinic's own place names and practitioner names (whichever tenant the
    service is running). **PASS:** correct, not a phonetic neighbour.
13. Say a date the awkward way: *"the twenty-second of next month."*

## Call 6 — Clean booking end-to-end (the control)

14. Straightforward booking, no tricks, no interruptions, all the way to
    *"All booked."*
    **PASS:** the appointment exists in the booking system. Check it — a call
    that sounds perfect and books nothing is this system's worst failure mode.

---

## After each pass

```bash
grep -E "ms_stt\] init|deformat|keyterms_prompt:" render-logs.txt
```

Every call must show `stt_variant=u3.5-pro` on the U3.5 pass and
`stt_variant=universal-streaming-english` on the control pass. A transcript you
cannot attribute to a model is not evidence — that is exactly what cost four
misaimed fixes on the C1 findings.

Also confirm `keyterms_prompt: N terms for clinic=...` shows a non-zero N on both
passes. Zero means the safety vocabulary never loaded and Call 4 proved nothing.

## Scoring

| | Old model | U3.5 |
|---|---|---|
| Barge-in feel (early / same / late) | | |
| Pause length that splits a sentence | | |
| Phone number captured first time | | |
| Name read back correctly | | |
| Clinical screening armed (Q9, Q10) | | |
| Hard tokens correct | | |
| Booking landed in Acuity/Calendar | | |
| Perceived reply latency | | |

**Ship U3.5 only if** clinical screening is at least as reliable, no booking is
lost, and barge-in is not worse. Latency and WER wins do not buy a safety
regression.

If barge-in is the only thing worse, that's the noise gate at
`connection.py:12084` — a fix, not a reason to abandon the model. If sentences
split, sweep `U35_MIN_TURN_SILENCE` before concluding anything; the knee is not
in the same place as the old endpointer's.
