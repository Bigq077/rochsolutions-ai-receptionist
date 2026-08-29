# Call sheet — hold speech, the gate before the port

**Purpose.** The owner held the hold-speech port to the three patient lines on
2026-08-29 pending "a full suite of test calls". This is that suite. Work it in
order; every call has a stated pass and a stated fail, so the outcome is a
verdict rather than an impression.

**Line:** `+447366263180` (northgate, the demo tenant). It is the only clinic
with `operational.hold_speech: true`, so it is the only line where any of this
is audible. Do **not** run these on `+447367002651` — the heads are gated off
there, which is the point.

**Before you dial**, in the Render log for the northgate service:

- `[build_info] running build <sha>` at call cleanup — the only proof of which
  build you tested. `/health` returns a hardcoded `1.0.0` and tells you nothing.
  You want `deb0dc76` or later.
- No `⚠️ CLINIC CONFIG` line. If a clinic key is sitting in the environment it
  is doing nothing, and the call will sound identical to the feature being
  broken.

**What this suite can and cannot settle.** By ear it can catch stacking, a
dangling clause, a head at a forbidden moment, and a robot repeating one
waveform. It cannot measure the dead-end rate — that is
`python -m scripts.replay_situational_heads` over the corpus, which is what
settled the design. A clean-sounding call is necessary evidence, not sufficient:
the model could always have produced the right answer by itself.

**Record for every call:** the call SID, and the exact opening words you heard.
"Sounded fine" is not a result.

---

## The five things being verified

| | what it is | gated? |
|---|---|---|
| **A. Situational heads** | 20 intents, the head read from *your* words at 600ms | behind `hold_speech` |
| **B. Clip / head exclusivity** | the recorded clip stands down when a head is coming | behind `hold_speech` |
| **C. The clip pool** | five recordings, not one waveform replayed forever | **NOT gated** — ports immediately |
| **D. `_ORPHAN_LEAD`** | no dangling clause left by the opener strip | **NOT gated** — ports immediately |
| **E. Nothing regressed** | a booking still lands, with heads on | — |

C and D reach a folded clinic whether or not anyone sets `hold_speech`, so they
are the two that most need to be right before the port.

---

## Call 1 — topic heads, on turns where no tool runs

This is the headline claim: every turn had ~2s of dead air, not just tool turns.
A price question used to get silence, because the old arbiter keyed on tool
names.

Ask these **in this order, in one call** — the rotation is per call:

1. *"How much is a sports massage?"*
2. *"And do you take Bupa?"*
3. *"What are your opening hours?"*
4. *"Is there parking there?"*

**Pass.** Each answer opens with a lead-in at roughly half a second, and the
answer **continues that clause** rather than restarting:

> "In terms of pricing — a sports massage is £X for an hour."

Expect the wording to alternate across the call: `In terms of pricing —` then
`As for insurance —` then `In terms of our opening hours —`. Two questions in
one call must not get the same construction twice.

**Fail, any of:**
- Silence for ~2 seconds before the answer, on any of the four.
- The lead-in is repeated by the model: *"In terms of pricing — In terms of
  pricing, a sports massage…"* — that is `strip_head_echo` not firing.
- A full stop after the dash, so it sounds like two separate utterances.
- *"On insurance —"*. That wording was rejected on 29 Aug for reading like an
  index entry; if you hear it the build is old.

---

## Call 2 — diary heads must say your own word back

1. *"Have you got anything on Saturday?"*
2. then, after the answer: *"What about the morning?"*
3. then: *"Do you do ninety-minute sessions?"*

**Pass.**
- *"Let me see what Saturday looks like —"* — **your** day, named back.
- *"Let me have a look at the mornings for you —"* or *"Let me see what I've got
  in the morning —"*.
- *"Let me see where a ninety-minute session fits —"*.

**Fail:** a bare *"Let me see —"* on question 1 or 2. The subject-free head is a
fallback for when you named nothing; hearing it when you said "Saturday" means
the rotation is picking across the whole pool again, which was a defect fixed on
29 Aug.

**Also fail:** a day you did not say. A head must never name a day the caller
did not — if you say "Saturday" and hear "Sunday", stop the suite and write it
down; that is a P1 and it is not a wording problem.

---

## Call 3 — the clip and the head must never stack

The recorded clip is CLOSED: it ends *"Let me just check that for you…"*, a
falling contour and a pause. It has to be the only hold speech in its turn.

Say: *"What's the earliest appointment you've got?"*

**Pass.** One of the two, never both:
- *"Let me find the soonest I've got —"* then the times, **or**
- the recorded clip, then the times.

**Fail.** Both, a third of a second apart:

> "Let me just check that for you… Let me find the soonest I've got —"

That is the exact double-phrase defect the arbiter exists to remove. If you hear
it, the port stops here.

---

## Call 4 — register heads, and their duplicates

1. *"I've hurt my lower back."*
2. *"Actually — can I cancel my appointment instead?"*
3. *"Sorry, I didn't catch that."*

**Pass.** *"Sorry to hear that —"*, then *"No problem at all —"*, then *"Sorry
about that —"*, each running straight into the reply.

These are the model's own words, taken verbatim from stored payloads, which is
what lets the duplicate be stripped cleanly. So the specific fail here is
**hearing it twice**: *"Sorry to hear that — Sorry to hear that, that sounds
uncomfortable."*

---

## Call 5 — where a head must NOT fire (the safety half)

Three moments, deny-by-default. Any head at any of them is a fail.

**5a — a bare answer.** Let Susie ask you something, and answer *"Yes."* or
*"No."* Nothing to stand in front of; expect no lead-in at all.

**5b — a clinical screen.** Say: *"My lower back's been bad for weeks and it
goes numb down my leg sometimes."* Susie should screen. **Answer the screening
question.** Expect **no head** in front of your red-flag answer — it is the
worst moment in the call to guess, and a diary head there would promise a lookup
that is not happening.

**5c — a goodbye.** Close with *"Great, thanks. Bye."* Expect no hold phrase.
This one is live evidence from the adaptive-caller suite: on the red-flag call a
caller said *"Alright. I'll ring 111 then. Thanks."* and heard *"Sorry, still
with you —"* in front of it.

---

## Call 6 — the dangling clause (ungated, ports regardless)

`_strip_interim_opener` runs on all four branches; only canonical has the
`_ORPHAN_LEAD` guard. So the three patient lines can speak a half-sentence
today, and this is what the port fixes.

Ask for availability twice in one call, so the opener strip runs more than once:

1. *"What have you got on Friday?"*
2. *"And what's available the week after?"*

**Pass.** Every sentence Susie speaks starts as a sentence.

**Fail.** Any utterance that begins mid-clause:
- *"While I look that up."*
- *"What's available for Saturday."*
- *"As I check that."*

Write the exact words down if you hear one — the guard is a word list, and a
miss tells us which word is missing.

---

## Call 7 — the clip pool, listened to across the whole suite

Not a separate call: a thing to notice during all of them. `audio_clips/` held
one recording for weeks despite the rotation machinery shipping, so every hold
moment on every clinic was the identical waveform, to the byte. That is the
2026-08-08 report — *"latency is great but it sounds quite robotic"*.

**Pass.** Where you do hear the recorded clip, it is not obviously the same take
every time.

**Note honestly:** variant 1 is byte-identical to the original and was kept
deliberately, so the phrase you hear most often is unchanged. You are listening
for *some* variation across many calls, not for a different clip every time.

---

## Call 8 — a booking, end to end, with heads on

The risk in this port is not the phrase. It is the phrase breaking a booking.

Book a real appointment on the demo line: give a name, a number, pick a slot,
let it complete. Then check:

- the diary entry exists, at the time you were told, for the duration you asked;
- the confirmation SMS arrived and reads correctly (no `Hi PENDING`);
- the read-back said your name and the right time.

**Fail:** any difference from a booking made with `hold_speech` off. If in
doubt, make the same booking on `+447367002651` and compare.

---

## The verdict that licenses the port

Port when all of these hold:

1. **Zero** stacked pairs (Call 3).
2. **Zero** heads at the three forbidden moments (Call 5).
3. **Zero** sentences beginning mid-clause (Call 6).
4. Every diary head named the day or band **you** said (Call 2).
5. The booking in Call 8 is indistinguishable from one made with the flag off.
6. `python -m scripts.detect_defects --check` still exits 0 afterwards, and
   `python -m scripts.replay_situational_heads` still reports a dead-end rate at
   or below 2.4%.

If 1–5 hold, port in this order — the two ungated items first, because they are
fixes that reach the clinics whether or not anyone touches the flag:

1. `_ORPHAN_LEAD` — the **widened** word list, not the original. The original
   stopped one word short of the commonest opener in the corpus.
2. The clip pool — pure audio, no behaviour change.
3. FillerGuard's second clip deletion.
4. The situational-head taxonomy, arriving gated OFF on every patient line, and
   turned on per clinic afterwards.

If any of 1–5 fails, write down the exact words and the call SID. Every defect
in this subsystem so far has been reproducible from the wording alone.

---

## What to do with what you hear

The transcript in `obs` is **post-Gate-5**, so a wrong sentence there may be the
gate rewriting a correct generation — four fixes were aimed at the wrong place
because of that. The wording you heard on the phone is the primary evidence.
Write it verbatim.
