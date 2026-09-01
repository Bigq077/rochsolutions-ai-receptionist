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

## P2 — A multi-day readout never signals that more times exist

**Severity: MEDIUM.** Not a wording bug — a missing measurement.

**Evidence:** same call, 15:14:16. `check_availability(date_hint="next week",
day_window=7)` returned Monday 7th with **twelve** `slot_times`
(`08:00 … 17:10`). Two were read out. Nothing told the caller the other ten
existed. Same for Tuesday and Wednesday.

**This is currently deliberate**, and the reason is recorded in two places:

* `slot_offer.py:306` — *"The tail is a claim about the clinic's diary, so it is
  made only where it has a referent — ONE day. 'A few others that day' after a
  three-day readout names no day, which is the B-99 rule."*
* `llm_stream.py:6280` — *"presentation_mode gates the APPEND only… Stripping is
  unconditional."*

**The deeper blocker, and the thing that makes a naive fix unsafe:**
`llm_stream.py:6284` sets `_slot_more_times` from **`first_day` only**.

```python
_fd = result.get("first_day")
session["_slot_more_times"] = bool(_fd.get("more_times"))
```

So on a multi-day readout that flag describes **Monday**. Appending the existing
tail after *"Number 3, Wednesday 9th September…"* would assert something about
Wednesday that the payload never measured. That is the exact failure the whole
family exists to prevent — on 2026-08-24, `CA98557584dc`, the model said *"I've a
few others that day"* about a Tuesday holding precisely the two slots the caller
had just been offered.

**Note for whoever picks this up:** the model *was* saying it on multi-day
readouts before the deterministic builder landed — `CA0453bd85037ece`,
2026-08-26: *"Number 2, Tuesday 8th September — nine in the morning. And I've a
few others that day if neither suits."* So "we have always had this" is true, and
it was unpoliced and wrong.

### Proposed fix

No new caller-facing literal. Reuse `more_times_tail()`
(`slot_followup.py:1204`) verbatim — its three variants all say "that day", which
**does** have a referent when the tail sits inside a chunk that just named its
day (*"Number 1, Monday 7th September — eight in the morning, or ten past five in
the evening. And I've a few others that day…"*).

Per-day `more_times` is computable from data already in the payload:
`available_days[i].slot_times` against what `presented_days` actually offered. No
new tool call.

**Open decision:** say it on the last day that has more (one utterance), or on
every day that has more (up to three per readout — probably worse than silence).
Owner leaned toward reuse-what-exists; the count is unresolved.

---

## P3 — The more-times line is the most droppable sentence in the call

**Severity: LOW–MEDIUM.** Same family as P1, different cause.

`build_slot_offer` appends the tail and then the closing question to the **last**
chunk, so on a single-day readout it lands **10–12 seconds into a 12.2-second
utterance**, after every option.

That makes it the first casualty of *any* interruption — not just the phantom in
P1, but a caller who hears three times and speaks up, which is the normal and
expected thing to do. It works when nobody interrupts (`CA320e6b1cb782`: caller
heard it and used it — *"oh yeah what else have you got"* → got the remaining
times), but it is structurally fragile.

**Proposed fix:** move the disclosure next to the day it describes rather than
parking it at the very end. Naturally folds into P2, and into P1's re-read.

---

## P4 — The obs transcript records speech the caller never heard

**Severity: LOW as a defect, HIGH as a trap.** It caused two wrong diagnoses in
one session.

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

## P5 — A bare "Right —" is reaching callers

**Severity: MEDIUM.** 10 occurrences in 57 calls since 2026-08-28.

```
assi  Let's get you booked in —
assi  Right —
assi  What's the appointment for?
```

Three fragments before a question. `hold_speech.py:216` documents bare discourse
markers (`"Right —"`, `"So —"`, `"Okay —"`) as **removed** for failing live in
three separate ways. They are back — as the *model's own* opening chunk after a
head, not as a filler. So it is not a filler-pool bug; it is the model echoing
the head's register, and it needs a different fix from P1/P6.

Calls: `CA6a59e59f0a67fe`, `CA320e6b1cb78217`, `CA9fda59b3a01981`, and others.

---

## P6 — Vital Edge played a hold phrase AFTER the sign-off

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

## P7 — Theorem: Susie answered her own question, and a phonetic spelling reached TTS

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
* **`f2637315`** — the chunker severing a sentence one word early
  (*"…like to come"* / *"In?"*). Promoted, live on all lines.
