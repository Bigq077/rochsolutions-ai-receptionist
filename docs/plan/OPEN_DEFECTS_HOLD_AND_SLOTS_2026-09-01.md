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

> **MOSTLY MISDIAGNOSED. The one real recording bug is FIXED as `d0454e41`
> on `latency-eval`, 2026-09-01. Not yet promoted.** Both headline claims
> were the transcript, not the call — this is P2 biting a third time in this
> same document. Taken in turn:
>
> **"Susie answered her own question" — NO.** `connection.py` resolves the
> location INLINE and speaks `_ack = f"{_loc_label}."` to confirm the clinic
> the caller just named. What is missing is the CALLER's "Alcester": that
> branch never reaches `run_turn`, and `llm_stream._append_history` was the
> ONLY caller of `record_user` in the codebase. Corpus: of 105 location
> questions, **68** are followed by Susie's own bare "Alcester."/"Redditch."
> with no caller turn between, and 28 by a real caller turn — the two paths
> are mutually exclusive. A defect reproducing on 65% of a deterministic
> path is a recording bug. **Fixed:** the inline branch now records the
> answer, and `record_user` is adjacent-duplicate safe. It is worth fixing
> rather than just explaining, because the judge's verdict on this
> transcript IS the operator CALL BACK SMS — a caller who appears silent is
> described to the operator as one.
>
> **"A phonetic spelling reached TTS" — NO, it is the design.**
> `app/clinics/theorem/canonical.py` documents `"Awlstuh"` as a TTS-only
> pronunciation hint for Alcester, which is said "AWL-stuh", and P2
> established that obs stores the PRE-substitution form. Pinned by a test so
> nobody "corrects" the spelling and breaks the pronunciation on every
> Theorem call.
>
> **The doubled reschedule question — REAL, STILL OPEN.** 3 occurrences in
> 806 calls once the designed no-input re-asks ("Sorry, I didn't catch
> that…", "I'm having trouble hearing you…") are excluded. On
> `CA17e0639e237340` the filler between the two asks is a TOOL-time one, so
> both questions are one turn: the model asked, ran a lookup with no answer
> to look up, then asked again. Root cause needs the Render log, not the
> corpus. Rare and caller-audible, not a booking failure.
>
> **Stacked fillers — REAL, STILL OPEN, and NOT Theorem-specific.** 89 in
> the corpus. Counted with a detector covering BOTH filler families (legacy
> ellipsis pool *and* arbiter heads — an ellipsis-only regex reports
> northgate as having zero fillers, which is wrong and nearly produced a
> false conclusion here). Per call: northgate 0.32, jv_v1 0.12, vital_edge
> 0.07, theorem_v3 0.04. **So the hold-speech arbiter does NOT fix stacking**
> — northgate is the only clinic with it ON and has the highest rate. Its
> 25 calls are test traffic and mostly predate `0bc6ca45`, so that number is
> confounded and the honest reading is "no evidence either way". Its own
> job; do not fold it into the hold_speech rollout.

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

## P6 — A caller who ACCEPTS a slot in words is read the list again

**Severity: HIGH. Open. Not caused by the 1 Sep work — it predates it by
weeks, on every clinic.** Found on the first Vital Edge call after the
hold-speech flip, `CA82b240ccad48ed219371c3f2fddfffb8`, 2026-09-01 21:46,
build `f875126e`. `outcome=abandoned`, judge `score=1`.

```
  assi  Number 3, Wednesday 9th September — nine in the morning, or six in
        the evening. Any of those work?
  user  um yeah the last day at 6 in the evening works
  assi  Let me see what I've got in the evening —            <- read as a NEW query
  assi  Here's what we've got coming up — Number 1, Monday 7th September —
        TEN in the morning, or FIVE in the evening.          <- and the 6pm he
  assi  Number 2 …                                              picked is GONE
```

**The model got it right and the engine threw the answer away.** From the
Render log, one second before that second readout:

```
21:48:19  [ms_gate5] deterministic offer in force — 3 chunk(s); the model's 1
          buffered chunk(s) are discarded
          ('PRE_SLOTWednesday 9th September at six in the evening
            works. Can I just get yo')
```

### Chain

1. The caller picks in WORDS, not by number. `utterance_is_slot_selection`
   (`connection.py:306`) is containment against `offered_slot_labels`, and the
   map was `day_selection` — `{1: Monday 7th, 2: Tuesday 8th, 3: Wednesday
   9th}`. "the last day" is an ordinal by POSITION and matches no label, so
   the pick is invisible to it.
2. The model therefore reads "6 in the evening" as a time-band request and
   calls `check_availability` again. The situational head agrees —
   `situational head (time_band)`.
3. That builds a fresh offer into `session["_slot_offer_prebuilt"]`, and the
   cache is invalidated (`date_hint changed from 'any' to 'next week'`), so
   the times come back DIFFERENT: 09:00/18:00 became 10:00/17:00. The slot
   the caller just accepted is no longer on the list.
4. In iteration 2 the model self-corrects and produces the right sentence.
   `_flush_slot_buf` (`llm_stream.py:3508`) discards it, because a prebuilt
   offer wins unconditionally.

Step 4 is the one that makes it unrecoverable. Its comment is right about the
case it was written for — never blend a half-model half-code sentence — but
it assumes the model's buffered text is an OFFER. Here it was an ACCEPTANCE
moving the booking forward, and there is no test of which.

### How common

Corpus, 806 calls: **106** read the numbered offer more than once (July,
August and September; jv_v1 73, theorem_v3 21, northgate 7, vital_edge 5).
Many of those are legitimate — a caller asking "what else have you got?"
SHOULD get a second list. Narrowing to "the caller's reply named a slot or a
time and another readout followed anyway" gives **33 calls, 24 of them
abandoned**. Hand-checked clear acceptances inside that set:
`CA903bd6ef1ce0c8` (VE, "uh the second one please", abandoned),
`CA5842023b88b926` (jv_v1, "The second one", abandoned),
`CA3c5b401045dac2` (jv_v1, "let's see that saturday slot please", abandoned),
`CA5e72ee56351475` (jv_v1, "wednesday at 5 in the evening suits me"),
`CA130c0a6b823817` (jv_v1, "the tuesday at 5 in the evening works"), and this
one. **"The second one" fails too** — so this is not only about "the last
day".

### Proposed fix — NOT written yet, and not to be rushed

The tempting fix is to widen `utterance_is_slot_selection` to understand
ordinals. Do that and step 4 still discards the acceptance the next time the
model calls the tool for any other reason. **Fix step 4:** a prebuilt offer
must not overwrite model text that is not itself an offer — if the buffered
text carries no "Number N", the payload offer should be dropped and the
model's sentence spoken, with `record_spoken_slots` NOT recording slots
nobody heard. Widening the ordinal matching is worth doing as well, second.

This is the slot path on live patient lines. Small diff, a regression test
built from this call's exact transcript, and a real call before promotion.

### Update, 1 Sep 23:40 — step 4 fixed; two corrections to the entry above

**Correction 1 — the two defects here have different ages, and the entry
above merges them.** The deterministic path did not exist "for weeks". It
landed **31 Aug 22:15 UTC** (`fa8c22ba` 22:46 BST, `7d6837cf` 23:15 BST) —
about 23 hours before this call. So:

* **Step 1, the RECOGNITION defect** — a caller picks in words, it is not read
  as a pick, and the model re-queries. Old, and the volume defect.
* **Step 4, the DISCARD** — a prebuilt offer overwrites the model's recovery.
  Can only have existed since 31 Aug.

Re-scanned the 807-call corpus with a filter that excludes legitimate "what
else have you got?" replies: **106** calls read the numbered offer more than
once, **28** of those after a reply that named a slot or a time, **14**
abandoned. (The 33/24 above used a looser filter; the shape agrees, the
numbers are filter-dependent — treat both as estimates.) Split at the
deterministic path:

| | picks | abandoned | clinics |
|---|---|---|---|
| before 31 Aug 22:15 | **27** | 13 | jv_v1 20, theorem_v3 5, vital_edge 1, northgate 1 |
| after | **1** | 1 | vital_edge — this call |

So the 24-abandoned figure above must **not** be attributed to step 4. On all
27 historical calls the model's own text was spoken, and it read the list
again anyway — they are step 1. Step 4 has exactly one observed instance: this
one. It remains worth fixing first because it is a 23-hour-old regression, it
is a 64-line change against step 1's semantic surgery, and it converts a
recoverable failure into an unrecoverable one — but the case for it rests on
**one** observation of the model self-correcting, not on 24.

**Correction 2 — the re-read does not merely return different times; it is
guaranteed to withdraw the accepted slot.** The entry above reads
`date_hint 'any' -> 'next week'` cache invalidation as the reason. It is not.
`choose_presented_indices` (`slot_followup.py:1865`) deliberately prefers
times this caller has **not heard** (B-116), so the one slot certain to be
missing from any second readout is the slot they just accepted. The stored
transcript shows it on all three days at once:

```
  offer:   Mon 7th / Tue 8th / Wed 9th — nine in the morning, or six in the evening
  re-read: Mon 7th / Tue 8th / Wed 9th — TEN in the morning, or FIVE in the evening
```

09:00/18:00 -> 10:00/17:00 on every day. That is B-116 working, not a fault to
be chased, and it is the reason the re-read must not happen at all.

### The fix, on `fix/p6-slot-acceptance` — NOT pushed

`llm_stream.py` section **1b-i**, +64 lines, nothing deleted. The deterministic
offer stands down when **both** hold:

* `extract_slot_options()` finds no numbered option in the model's buffered
  text — the same parser sections 3a-6 use, so with nothing to find every one
  of those repairs is already a no-op; and
* an offer is **already standing** (`last_offered_slots` non-empty), so the
  caller has heard options and the model is answering a pick.

The second condition is what makes it safe: on a first lookup there is no
standing offer, so a contentless model turn can never cost a caller the only
offer they were going to get — the payload sentence still wins, exactly as
before. Nothing is recorded when it stands down: `last_offered_slots`,
`slot_labels` and the keypad map still describe the offer the caller is
choosing *from*, and overwriting them would renumber the keypad mid-sentence.

Deliberately **not** matching model wording — [[write-gates-match-one-literal]]
has burned this codebase three times.

`tests/regression/test_p6_an_accepted_slot_is_not_read_back_as_a_list.py`,
5 tests built from this call's transcript. Two fail before the fix and pass
after; three pin the existing behaviour and pass in both trees. Full
`tests/regression` failing set is **byte-identical** before and after — 24
either side, same tests — with passes 6370 -> 6375.

**Still open, and now the bigger half: step 1.** Widening pick recognition to
understand "the last day", "the second one" and a bare time is the ~27-call
defect. Not attempted here.

**Before this promotes:** a real call on the demo line (+447366263180) that
reproduces the shape — read a multi-day offer, accept one in words — and
confirms Susie moves to the name instead of re-reading. `latency-eval` is a
live line; the revert is this one commit.

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
