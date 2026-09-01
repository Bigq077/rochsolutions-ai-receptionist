# Open defects — hold speech & slot readouts, 2026-09-01

Handover. Everything below is evidenced from the obs corpus (`demo_obs`) and the
Render logs, with call SIDs. **Nothing here is fixed.** One related fix DID land
today and is described at the bottom so it is not re-done.

Branch state at handover: `origin/latency-eval` = `0bc6ca45` (demo line only),
`origin/production` = `1d85d13e` (the three patient lines). ADR-002 is live — see
`RELEASE_PROMOTION_DECISION.md`. Push engine work to `latency-eval`, call
**+447366263180**, promote by `git push origin latency-eval:production`.

---

## P1 — A phantom barge-in destroyed a 12-second slot readout

> **FIXED `d8af8932` on `latency-eval`, 2026-09-01. Not yet promoted, and
> not yet heard on a call.** The recovery changed, not the trigger, exactly as
> proposed below: a barge-in that tears down a readout mid-playback now
> re-reads the readout whole rather than re-asking the closing question.
> Gated on audio actually thrown away (`_tts_playout_end_mono` at teardown),
> on the map still being armed, and on the readout still being what the
> caller was hearing. Regression test:
> `tests/regression/test_b120_a_slot_readout_torn_down_at_playback_is_read_again.py`.
> Full-suite failing set byte-identical to `0a73a5d7`. **Still owed: a real
> call on +447366263180 before promotion.** The analysis below stands as
> written — keep it, it is why the three obvious fixes are wrong.

**Severity: HIGH.** Caller-audible, cost a real call, reproduced once and it is
the top item.

**Evidence:** `CAa2bdff2b702cea8869d29a0dca981e26`, 2026-09-01 15:14, build
`0bc6ca45`, demo line.

The caller asked for Friday. The readout was built correctly, all four sub-chunks
synthesised, every ElevenLabs call returned 200 inside 800ms, `lost_total=0`,
`media_frames=5204`. Nothing failed.

```
15:14:45.387  tts_finished in  4.6s: 'The available slots for Friday 11th September are — Number 1'
15:14:45.579  tts_finished in  6.3s: 'Number 2, ten to nine in the morning.'
15:14:45.985  tts_finished in 12.2s: "Number 3, ... And I've a few o"
15:14:47.213  barge-in: partial='hi'
15:14:47.213  barge-in start: synthesis_active=False playback_active=True
              interrupted_text="And I've a few others that day if none of those suit. Any of"
15:14:50.165  barge-in #0 carried no words (partial='hi' own_audio=False)
              — speaking 'Any of those work?' instead of an ack (1/2)
15:14:57.114  FINAL → queue: 'uh you got cut off say that again'
```

**12.2 seconds of audio, killed 1.9 seconds in, by a word the caller never said.**
They heard *"The available slots for Friday eleventh September are — Number one,
eight in the…"* and then silence. Numbers 2 and 3, the more-times line and the
closing question were all synthesised and sitting in Twilio's buffer when the
teardown flushed it.

### Why three separate guards all missed

1. **The noise filter cannot catch this.** `_BARGE_NOISE`
   (`connection.py:15991`) holds `uh, um, hmm, ah, er, oh, erm, ehm, hm, mm,
   mhm, ugh, huh`. `'hi'` is a real word and would pass anyway — but more
   importantly the filter runs on the **final**, while the teardown is at
   `connection.py:15864` on the **partial**. No final ever arrived here, so the
   filter never ran at all. This is the shape recorded in
   `barge-in-tears-before-the-noise-filter`.

2. **`synthesis_active=False` is read as "she has finished speaking".** It means
   "we have finished *sending*". On a slot readout those differ by up to twelve
   seconds. The engine already computes the gap — it logs `tts_finished in
   12.2s` — and then does not use it.

3. **The existing recovery does not cover the playback case.** There IS a
   "Heard-nothing slot recovery (Bug A)" at `connection.py:6470`
   (`_inhibited_slot_chunks`, `_slot_represented_once`) built for this exact
   complaint. Its trigger is chunks discarded by `tts_inhibit` **before
   synthesis**. Here they were synthesised fine and killed at **playback**, so
   it never armed.

The `carried no words` recovery that did fire is right in principle — it
deliberately avoids an ack that would claim the caller spoke — but after a
truncated readout, re-asking the closing question is useless: the caller never
heard the options it refers to.

### Proposed fix

Extend the heard-nothing recovery to the **playback** teardown: when a barge-in
tears down a slot readout and then carries no words, re-present the readout
instead of the bare closing question. The chunks are already saved; the
play-duration estimate already tells us how much was actually heard.

**Do NOT "filter more partials".** Making teardown reluctant makes Susie
un-interruptible, and this repo's history says the obvious version of that fix is
wrong. Change the recovery, not the trigger.

**Risk:** barge-in is the highest-risk surface in the engine. Small diff,
regression test built from this call's exact timings, real call before promotion.

---

## NOT A DEFECT — the multi-day readout stays silent on purpose

**Owner decision, 2026-09-01. Do not re-open this.** It was raised in that
session and withdrawn on the owner's clarification, and it is recorded here so
the next reader does not re-derive it as a bug.

A multi-day readout — *"Monday: two times. Tuesday: two times. Wednesday: two
times."* — deliberately does **not** add "and I've more on those days". That is
intended behaviour, not an omission. `slot_offer.py:306` gates the tail on
`mode == "single_day"` (B-99: "that day" has no referent after three days named),
and `llm_stream.py:6284` only ever measures `first_day`, so there is no data
behind such a claim for days two and three anyway.

**What the owner actually wants happens already, and it works.** When a caller
names a specific day and gets that day's slots, the tail fires from
`more_times_tail()` whenever the day holds more than was read out.

Verified over the corpus, 2026-08-20 onward:

```
single-day readouts        72
carried the more-times tail 19
did not                     53
of those 53, cases where more times were later shown to exist:  0
```

Every readout that had more times said so. No missed disclosure. `CA320e6b1cb782`
shows it working end to end — the caller heard it and used it (*"oh yeah what
else have you got"* → got the remaining times).

**One caveat, which is really P1's problem.** The tail is appended to the LAST
chunk, after every option and just before the closing question, so on a 12.2s
readout it lands 10–12 seconds in. That makes it the first thing lost to any
interruption. On `CAa2bdff2b702cea88` it was generated correctly and never heard,
which is what made it look absent and produced this false lead. If P1's re-read
is built, make sure the tail survives it.

---

## P2 — The obs transcript records speech the caller never heard

> **FIXED `4acc5a35` on `latency-eval`, 2026-09-01. Not yet promoted.** The
> false claim went first, in both places that made it — connection.py's
> record site and `app/obs/turns.py`'s docstring. Both now say what the list
> actually is: **intent to speak**. That is the half that cost the two wrong
> diagnoses, and it is why the wording is pinned by a test.
>
> Then the two downstream losses that are cheaply knowable at that seam:
>
> * **The P3 leading-marker strip** rewrites what is synthesised, so the
>   record now stores the rewritten form. Recording the un-stripped text
>   would have been this defect a THIRD time, added by the fix for P3.
>   `_obs_chunk_text` itself is untouched — it is the string
>   `_unrecord_spoken` matches against llm_stream's record and the one
>   `_slot_readout_chunks` compares by equality — so the strip is applied to
>   the pre-substitution form separately, keeping phone numbers readable.
> * **A barge-in cancelling synthesis part-way** now annotates the fragment
>   already written (`turns.note_cut`, sub-chunks spoken of total), and the
>   judge renders it. A truncated line read as complete is the same class of
>   invention that once had it text the operator that a caller had hung up
>   when they had not. An uncut line renders byte-identical, so this cannot
>   move scoring on calls that had no barge-in.
>
> **STILL OPEN, deliberately: the playback case.** Synthesis finished, the
> audio was already in Twilio's buffer, and the teardown flushed it — P1,
> `CAa2bdff2b8`, 12.2s stored and ~1.9s heard. Closing it means threading
> chunk identity through `_send_loop`'s cumulative playout clock, i.e. the
> audio path, for a LOW-severity defect. **So the absence of `cut` is still
> not evidence a fragment was heard**, both docstrings say so, and
> `test_absence_of_the_marker_is_not_a_claim` exists to stop that caveat
> being dropped once the marker makes the record look trustworthy. Keep
> cross-checking `[ms_tts] tts_finished` and `barge-in start` in the Render
> log. Regression test:
> `tests/regression/test_p2_the_obs_record_does_not_claim_speech_was_heard.py`.

**Severity: LOW as a defect, HIGH as a trap.** It caused two wrong diagnoses in
one session, including reporting a working feature as broken — see the
NOT A DEFECT section above.

`connection.py:14808` records the obs transcript, and its comment claims:

> *"it is past every suppression check above, so nothing the caller did not hear
> is stored"*

**That is false.** It is past every *suppression* check, but it sits **before**
`split_tts_text`, before synthesis and before playback. Anything killed
downstream is still recorded as spoken.

**Proof:** `CAa2bdff2b702cea8869d29a0dca981e26` has four assistant entries for
the Friday turn. The caller heard about 1.9 seconds of the first one.

**Fix:** correct the comment at minimum. Better: record play-completion
separately so "was this heard?" is answerable from the record. Until then,
**treat the obs transcript as intent-to-speak, not audio** — cross-check
`[ms_tts] tts_finished` and any `barge-in start` in the Render log.

---

## P3 — A bare "Right —" is reaching callers

> **MISDIAGNOSED HERE, AND FIXED AS `bf01f9fc` on `latency-eval`,
> 2026-09-01. Not yet promoted, and not yet heard on a call.** The reading
> below — "it is the model echoing the head's register" — is wrong, and it
> points at a prompt fix that would not have worked. **The prompt MANDATES
> the bare marker:** `clinic_template_prompt.py:2266` says *"acknowledge
> simply: 'Right —' and NOTHING ELSE"*, because `connection.py` then injects
> the next question itself (`_next_question_after_booking_ack`). The stub is
> deliberate, and it reads correctly when it is the ONLY acknowledgement the
> caller hears — which it is on every turn where the model beats the 600ms
> head delay and no head plays.
>
> **The defect is the RACE, not the register.** When the head wins instead,
> the head and the stub perform the same contentless speech act ~1s apart.
> Measured over all 798 stored calls (8,639 assistant chunks): 188 bare
> markers, of which **32 follow a contentless hold phrase** — the defect —
> and 156 do not, which is the intended two-fragment design and is left
> alone.
>
> **The obvious fix is the one that must not be taken.** `join_after_head`
> has a `suppress_pure_duplicate` branch for exactly this shape and both
> call sites already pass it `True`; it is unreachable only because
> `_strip_interim_opener` does not recognise a bare marker. Making it
> reachable empties the turn — `_display_reply` becomes `""`, so
> `conversation_history` stores `""`, so the booking-ack injector (which
> gates on `_last_bot` containing `"right —"`) never fires; Gate 5's
> empty-turn fallback then arms behind it, DEFERRED on freeform clinics to
> the post-turn path, and answers *"I'd like to book an appointment"* with
> *"Sorry, I didn't quite catch that — could you say that again?"* That is
> the dead end `strip_head_echo` documents and refuses to walk into.
>
> So the drop is at the AUDIO, in `_tts_loop`, above the dedup guard, gated
> on `_hold_head_spoken`. **No `_unrecord_spoken` — that omission is
> load-bearing**, and is pinned by a test, exactly as the dedup guard below
> it already keeps the record for a chunk it drops. Vocabulary is a closed
> set matched whole-chunk only, deliberately NOT `ACK_OPENER_RE` (which
> would drop 67 chunks where the evidence is 32). Regression test:
> `tests/regression/test_p3_a_bare_marker_is_not_said_on_top_of_a_head.py`.
> Full-suite failing set byte-identical to `3cd337cc` (118 = 118, nothing
> newly failing). **Still owed: a real call on +447366263180.**

**Severity: MEDIUM.** 10 occurrences in 57 calls since 2026-08-28. (Measured
over the full corpus above: 32.)

```
assi  Let's get you booked in —
assi  Right —
assi  What's the appointment for?
```

Three fragments before a question. `hold_speech.py:216` documents bare discourse
markers (`"Right —"`, `"So —"`, `"Okay —"`) as **removed** for failing live in
three separate ways. They are back — as the *model's own* opening chunk after a
head, not as a filler. So it is not a filler-pool bug; it is the model echoing
the head's register, and it needs a different fix from P1/P4.

Calls: `CA6a59e59f0a67fe`, `CA320e6b1cb78217`, `CA9fda59b3a01981`, and others.

---

## P4 — Vital Edge played a hold phrase AFTER the sign-off

> **MISDIAGNOSED HERE, AND FIXED AS `f876230a` on `latency-eval`, 2026-09-01.
> Not yet promoted, and not yet heard on a call.**
> It is NOT a filler outliving its turn. `request_callback` fired FOUR times
> on this call — turns 7, 11, 15 and 19 of the obs transcript — three of them
> triggered by a plain acknowledgement or a goodbye. The hold phrase follows
> the sign-off because the model streamed farewell text and a tool call in the
> same turn, so the filler was correctly announcing a genuine fresh write.
> Reading the transcript as audio is exactly the P2 trap below.

> **And it is NOT Vital Edge only.** `build_tool_schemas` is clinic-aware, so
> the flat master list proves nothing on its own — checked directly, it hands
> `request_callback` and `add_to_waitlist` to all four live clinics, and the
> executor has no clinic gate. The corpus shows it only on VE because VE is
> the only clinic with any callback traffic (5 calls; the other three have
> zero). Silence, not a negative.

> Root cause: the farewell-turn re-fire `_WRITE_TOOL_FAMILIES` already guards
> for booking/reschedule/cancel. These two tools were never members. Gated
> now in the refusal chain above the executor, so the filler is suppressed
> with the re-write. Test:
> `tests/regression/test_b121_a_finished_callback_keeps_restarting.py`.

> **Not harmful, which lowers the urgency:** the owner SMS dedups on
> `_waitlist_pinged` and the record uses `setdefault`, so Jonathan was texted
> once and no data was damaged. Only the speech repeated.

> **Adjacent defect this exposed, NOT fixed:** because that SMS dedup is
> per-CALL rather than per-LEAD, a caller who asks for a *second, different*
> person to be rung back has that lead silently dropped — the tool still
> returns "Clinic notified". Pre-existing and untouched by this fix.

**Severity: MEDIUM. This is a patient line.**

`CA8522b3e23fc64293`, 2026-09-01 13:44:

```
assi  That's all from my end — the message is with Jonathan and he'll be in touch. Take care!
assi  Let me get that message over for you…
assi  Jonathan's been notified and will be in touch on that number. Take care, Alice!
```

A hold phrase after the closing means a filler task outlived its turn. Lifecycle
bug, not a wording one. The same call also duplicates the "Jonathan has been
notified" claim.

---

## P5 — Theorem: Susie answered her own question, and a phonetic spelling reached TTS

**Severity: MEDIUM.** `CA17e0639e237340`, 2026-09-01 11:34.

```
assi  Was your original appointment at our Awlstuh or Redditch clinic?
assi  Alcester.
```

Two problems in three lines: the assistant answers its own question, and
`"Awlstuh"` is a phonetic spelling being sent to TTS as text. The same call also
asks *"Do you have a preference for when you'd like to reschedule to?"* twice
with a filler between, and stacks two ellipsis fillers back to back.

---

## Also open, unrelated to hold speech

* **`GOOGLE_SERVICE_ACCOUNT_JSON` is malformed** — `Invalid \escape: line 5
  column 46`. Sheets is dead on the demo service. Known-accepted there, but it is
  a broken paste, so **confirm the three patient services have a valid one**.
* **`DEPLOYMENT_INVENTORY.md`** has `[owner]` cells for the JV and Theorem
  service IDs that only the Render dashboard can fill.

---

## Already fixed today — do not re-do

* **`0bc6ca45`** — the double hold phrase (*"Sorry to hear that —"* …
  *"Still with you —"*). Root cause was a **relative** timing constant:
  `dc6f521e` moved the head to 600ms and the re-arm silently slid from 8.0s to
  5.6s, tripling the rate. Now an absolute 10s deadline from dispatch, plus a
  min-gap that makes stacking unrepresentable. On `latency-eval` only —
  **not yet promoted, and not yet heard on a call.**
* **`f876230a`** — P4 below, the callback that kept re-firing. On
  `latency-eval` only — **not yet promoted, and not yet heard on a call.**
* **`d8af8932`** — P1 above, the readout killed at playback. On
  `latency-eval` only — **not yet promoted, and not yet heard on a call.**
* **`f2637315`** — the chunker severing a sentence one word early
  (*"…like to come"* / *"In?"*). Promoted, live on all lines.
